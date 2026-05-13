# -*- coding: utf-8 -*-
"""
freezing_analysis.py
--------------------
Two-pass pipeline for % time freezing and (optionally) freezing bout counts.

Pass 1 — Parallel, fully in-memory:
    Each CSV file is processed independently on a worker process.
    No per-day CSVs are written. Results are returned as row lists and
    accumulated in the main process.

Pass 2 — Cumulative:
    The accumulated DataFrame is written once per cohort and once combined.
    Combined outputs  → cfg["analysis_out"] / "<subfolder>"/
    Cohort outputs    → <BehaviorData>/Analysis/<subfolder>/
"""

from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import utils
import run_report as rr


# ── Pass 1: per-file processing ─────────────────────────────────────────────
#
# _process_file now returns a tuple of (rows, report_data) instead of
# mutating a shared report object. This is necessary for parallelism:
# each worker process has its own copy of memory, so they can't share
# a single report dict. We collect report_data from all workers and
# merge it in the main process afterward.
#
# report_data looks like:
#   {
#     "subjects":   { csv_name: {columns_used, warnings, skipped} },
#     "exclusions": { csv_name: reason_string },
#   }
# ---------------------------------------------------------------------------

def _process_file(csv_path, meta, day_dir, cfg):
    """
    Process one CSV. Returns (freeze_rows, bout_rows, report_data).
    freeze_rows and bout_rows are lists of dicts (one dict per trial window).
    report_data is a small dict describing what happened, for the run report.
    """
    report_data = {"subjects": {}, "exclusions": {}}
    subject_key = csv_path.name

    test_date, behavior_id, row, exclusion_reason = utils.metadata_for_csv(csv_path, meta)
    if exclusion_reason is not None:
        print(f"  [warn] {csv_path.name}: {exclusion_reason}; skipping.")
        report_data["exclusions"][subject_key] = exclusion_reason
        return [], [], report_data

    animal_id = row.get("animal_id", behavior_id)
    treatment = utils.normalize_treatment(
        row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    sex       = utils.normalize_sex(row.get("sex", None))
    litter_id = row.get("litter_id", None)
    cohort_id = row.get("cohort_id", None)

    df_header = utils.load_csv_header(csv_path)
    try:
        time_col   = utils.find_time_col(df_header, cfg)
        freeze_col = utils.find_freeze_col(df_header, cfg)
        source_cols = utils.unique_existing_columns(
            df_header,
            [time_col, freeze_col] + utils.trial_detection_source_columns(df_header, cfg),
        )
    except ValueError as e:
        print(f"  [warn] {csv_path.name}: {e}; skipping.")
        report_data["exclusions"][subject_key] = str(e)
        return [], [], report_data

    # Parsing only the columns used below avoids paying for unrelated AnyMaze
    # signals in every worker process.
    df = utils.load_csv(csv_path, usecols=source_cols)
    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    trials, itis, cs_source = utils.detect_trials(df, time_col, cfg)
    windows = trials + itis
    if not windows:
        print(f"  [warn] No trials detected in {csv_path.name}.")
        report_data["exclusions"][subject_key] = "no trials detected"
        return [], [], report_data

    # Record which columns were used for the run report
    cols_used = {"time": time_col, "freeze": freeze_col, "CS+_detection": cs_source}
    report_data["subjects"][subject_key] = {
        "columns_used": cols_used,
        "warnings":     [],
        "skipped":      [],
    }

    day, context, session = utils.parse_folder_bits(day_dir.name)
    t = df[time_col].astype(float).to_numpy()
    x = df[freeze_col].fillna(0).astype(float).to_numpy()

    # ── Freezing rows ────────────────────────────────────────────────────────
    pretrial_windows = []
    first_trial_start = min(float(w["start"]) for w in trials)
    session_start = float(np.nanmin(t))
    if first_trial_start > session_start:
        pretrial_windows.append({
            "type": "Pre-trial",
            "trial_index": 1,
            "start": session_start,
            "end": first_trial_start,
        })

    freeze_rows = []
    for w in pretrial_windows + windows:
        dur      = max(0.0, w["end"] - w["start"])
        freeze_s = utils.integrate_binary(t, x, w["start"], w["end"]) if dur > 0 else 0.0
        freeze_rows.append({
            "animal_id":       animal_id,
            "behavior_id":     behavior_id,
            "treatment_group": treatment,
            "sex":             sex,
            "litter_id":       litter_id,
            "cohort_id":       cohort_id,
            "test_date":       test_date,
            "day":             day,
            "context":         context,
            "session_label":   session,
            "_day_folder":     day_dir.name,
            "_source_csv":     csv_path.name,
            "trial_type":      w["type"],
            "trial_index":     w["trial_index"],
            "window_start_s":  w["start"],
            "window_end_s":    w["end"],
            "window_len_s":    dur,
            "freeze_time_s":   freeze_s,
            "freeze_pct":      100.0 * freeze_s / dur if dur > 0 else 0.0,
        })

    # ── Bout rows (optional) ─────────────────────────────────────────────────
    bout_rows = []
    if cfg.get("freezing_bouts"):
        for w in windows:
            bouts  = _detect_bouts(t, x, w["start"], w["end"])
            shared = dict(
                animal_id=animal_id, behavior_id=behavior_id,
                treatment_group=treatment, sex=sex,
                litter_id=litter_id, cohort_id=cohort_id,
                test_date=test_date, day=day, context=context,
                session_label=session, _day_folder=day_dir.name,
                _source_csv=csv_path.name,
                trial_type=w["type"], trial_index=w["trial_index"],
                window_start_s=w["start"], window_end_s=w["end"],
            )
            if not bouts:
                bout_rows.append({**shared,
                                  "bout_id_in_trial": 0,
                                  "bout_start_s": np.nan,
                                  "bout_end_s":   np.nan,
                                  "bout_dur_s":   np.nan})
            else:
                for j, b in enumerate(bouts, 1):
                    bout_rows.append({**shared,
                                      "bout_id_in_trial": j,
                                      "bout_start_s": b["start"],
                                      "bout_end_s":   b["end"],
                                      "bout_dur_s":   b["end"] - b["start"]})

    return freeze_rows, bout_rows, report_data


def _process_file_star(args):
    """
    Thin wrapper so ProcessPoolExecutor can call _process_file with a single
    argument. (Pool workers can only receive one argument per task.)
    """
    return _process_file(*args)


# ── Bout detection (unchanged from original) ─────────────────────────────────

def _detect_bouts(t, x, start, end):
    t = np.asarray(t, float)
    x = (np.asarray(x, float) > 0).astype(int)
    if len(t) == 0 or start >= end:
        return []

    bouts, bout_start = [], None
    idx0 = max(0, np.searchsorted(t, start, side="right") - 1)
    prev = x[idx0]
    if prev == 1:
        bout_start = start

    for i in range(len(t)):
        seg_s = t[i]
        seg_e = t[i+1] if i+1 < len(t) else t[i] + (t[i]-t[i-1] if i > 0 else 0.02)
        if seg_e <= start: continue
        if seg_s >= end:   break
        a, b  = max(seg_s, start), min(seg_e, end)
        if b <= a:         continue
        state = x[i]
        if prev == 0 and state == 1:
            bout_start = a
        elif prev == 1 and state == 0 and bout_start is not None:
            if a > bout_start:
                bouts.append({"start": bout_start, "end": a})
            bout_start = None
        prev = state

    if prev == 1 and bout_start is not None and end > bout_start:
        bouts.append({"start": bout_start, "end": end})
    return bouts


# ── Pass 1 driver: collect all rows in parallel ───────────────────────────
#
# This replaces both _process_day() and _collect_cumulative() from the
# original. Instead of writing per-day CSVs and reading them back, we:
#   1. Build a flat list of (csv_path, meta, day_dir, cfg) tasks — one per file
#   2. Hand them to a process pool — each worker calls _process_file_star
#   3. Collect results as they finish (as_completed gives us results in
#      whichever order they finish, not submission order — that's fine here)
#   4. Merge the per-worker report_data back into the shared report object
# ---------------------------------------------------------------------------

def _collect_all_parallel(cfg, report=None):
    """
    Walk all BehaviorData dirs and session day dirs, build task list, run
    in parallel, return (freeze_df, bout_df).
    """
    # Build the flat task list first so we know what we're dealing with
    tasks = []
    meta_by_bd = {}   # cache metadata so we don't reload it per-file
    for bd in cfg["behaviordata_dirs"]:
        meta = utils.load_metadata(bd)
        meta_by_bd[bd] = meta
        suffix = cfg["csv_suffix"]
        for day_dir in utils.find_session_dirs(bd):
            csvs = sorted(
                day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
            for csv_path in csvs:
                tasks.append((csv_path, meta, day_dir, cfg))

    if not tasks:
        print("  [warn] No CSV files found.")
        return pd.DataFrame(), pd.DataFrame()

    print(f"  Found {len(tasks)} CSV files. Processing with {cfg['n_workers']} workers...")

    all_freeze_rows = []
    all_bout_rows   = []

    # Submit all tasks to the process pool.
    # as_completed() yields futures as each worker finishes — we don't have
    # to wait for all of them before starting to collect results.
    with ProcessPoolExecutor(max_workers=cfg["n_workers"]) as pool:
        future_to_csv = {pool.submit(_process_file_star, t): t[0] for t in tasks}

        for future in as_completed(future_to_csv):
            csv_path = future_to_csv[future]
            try:
                freeze_rows, bout_rows, report_data = future.result()
            except Exception as exc:
                print(f"  [error] {csv_path.name} raised an exception: {exc}")
                continue

            all_freeze_rows.extend(freeze_rows)
            all_bout_rows.extend(bout_rows)

            # Merge this worker's report data back into the main report.
            # This happens in the main process (safe — no race conditions).
            if report is not None:
                for key, info in report_data["subjects"].items():
                    rr.record_subject(report, "freezing", key,
                                      columns_used=info["columns_used"],
                                      warnings=info["warnings"],
                                      skipped=info["skipped"])
                    if cfg.get("freezing_bouts"):
                        rr.record_subject(report, "freezing_bouts", key,
                                          columns_used=info["columns_used"],
                                          warnings=info["warnings"],
                                          skipped=info["skipped"])
                for key, reason in report_data["exclusions"].items():
                    rr.record_exclusion(report, "freezing", key, reason)

    freeze_df = pd.DataFrame(all_freeze_rows) if all_freeze_rows else pd.DataFrame()
    bout_df   = pd.DataFrame(all_bout_rows)   if all_bout_rows   else pd.DataFrame()
    return freeze_df, bout_df


# ── Figures (unchanged from original) ────────────────────────────────────────

def _auto_mean_sem_ylim(df, value_col, group_cols, pad=1.1, minimum=1.0):
    agg = (
        df.groupby(list(group_cols), dropna=False)[value_col]
        .agg(["mean", "count", "std"])
        .reset_index()
    )
    if agg.empty:
        return (0, minimum)
    sem = agg["std"].fillna(0) / agg["count"].pow(0.5)
    upper = (agg["mean"] + sem).replace([np.inf, -np.inf], np.nan)
    y_max = upper.max()
    if pd.isna(y_max):
        return (0, minimum)
    return (0, max(float(y_max) * pad, minimum))


def _tiled_individual(df, value_col, ylabel, title_prefix, out_dir, cfg, fname_tag,
                      ylim=None):
    groups    = cfg["canonical_groups"]
    colors    = cfg["treatment_colors"]
    day_order = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    for trial in ("CS+", "CS-", "ITI"):
        sub = df[df["_trial_type"] == trial]
        if sub.empty:
            continue
        trial_ylim = ylim
        if trial_ylim is None:
            trial_ylim = _auto_mean_sem_ylim(
                sub, value_col, ["_day_folder", "treatment_group", "trial_index"])

        n_rows, n_cols = len(groups), len(day_order)
        fig, axes = plt.subplots(n_rows, n_cols,
                                  figsize=(min(18, 3.0*n_cols), min(12, 2.6*n_rows)),
                                  squeeze=False)
        fig.suptitle(f"{title_prefix} — {trial}", fontsize=12, y=0.98)

        for r, trt in enumerate(groups):
            df_tr = sub[sub["treatment_group"] == trt]
            for c, day in enumerate(day_order):
                ax  = axes[r][c]
                dfd = df_tr[df_tr["_day_folder"] == day]
                if dfd.empty:
                    ax.set_axis_off(); continue
                for _, g in dfd.groupby("behavior_id"):
                    ax.plot(g.sort_values("trial_index")["trial_index"],
                            g.sort_values("trial_index")[value_col],
                            linewidth=0.9, alpha=0.35, color=colors.get(trt))
                agg = dfd.groupby("trial_index")[value_col].agg(["mean","count","std"]).reset_index()
                utils.plot_mean_sem(ax, agg, color=colors.get(trt))
                if r == 0: ax.set_title(day, fontsize=9)
                if c == 0: ax.set_ylabel(f"{trt}\n{ylabel}", fontsize=9)
                ax.set_xlabel("Trial #", fontsize=8)
                ax.set_ylim(*trial_ylim)
                ax.grid(True, axis="y", alpha=0.25)
                utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        fig.tight_layout(rect=(0, 0, 1, 0.95))
        tag = trial.replace("+","plus").replace("-","minus").lower()
        utils.save_fig(fig, out_dir / f"{fname_tag}_{tag}_individual_tiled.svg")


def _tiled_group_means(df, value_col, ylabel, title_prefix, out_dir, cfg, fname_tag,
                        trial_types=("CS+", "CS-", "ITI"), ylim=None):
    groups    = cfg["canonical_groups"]
    colors    = cfg["treatment_colors"]
    day_order = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)
    if not day_order:
        return

    trial_types = tuple(trial_types)
    has_any = df["_trial_type"].isin(trial_types).any()
    if not has_any:
        return

    n_rows = len(trial_types)
    n_cols = len(day_order)
    # Put trial type on rows and session day on columns so all group means can
    # be inspected in one SVG instead of opening one file per trial type.
    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(min(20, 3.2 * n_cols), 2.9 * n_rows),
        squeeze=False,
    )
    fig.suptitle(f"{title_prefix} (group means)", fontsize=12, y=0.985)

    legend_handles, legend_labels = [], []
    for r, trial in enumerate(trial_types):
        sub = df[df["_trial_type"] == trial]
        trial_ylim = ylim
        if trial_ylim is None and not sub.empty:
            trial_ylim = _auto_mean_sem_ylim(
                sub, value_col, ["_day_folder", "treatment_group", "trial_index"])
        if trial_ylim is None:
            trial_ylim = (0, 1)

        for c, day in enumerate(day_order):
            ax  = axes[r][c]
            dfd = sub[sub["_day_folder"] == day]
            if dfd.empty:
                ax.set_axis_off(); continue
            for trt in groups:
                agg = (dfd[dfd["treatment_group"] == trt]
                       .groupby("trial_index")[value_col]
                       .agg(["mean","count","std"]).reset_index())
                if agg.empty: continue
                utils.plot_mean_sem(ax, agg, color=colors.get(trt), label=trt)
                if trt not in legend_labels:
                    legend_labels.append(trt)
                    legend_handles.append(ax.lines[-1])
            if r == 0:
                ax.set_title(day, fontsize=9)
            ax.set_xlabel("Trial #", fontsize=8)
            ax.set_ylim(*trial_ylim)
            ax.grid(True, axis="y", alpha=0.25)
            if c == 0:
                ax.set_ylabel(f"{trial}\n{ylabel}", fontsize=9)
            utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

    if legend_handles:
        fig.legend(legend_handles, legend_labels, loc="upper center",
                   ncol=len(legend_labels), frameon=False, fontsize=9,
                   bbox_to_anchor=(0.5, 0.955))
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    utils.save_fig(fig, out_dir / f"{fname_tag}_groupmeans_tiled.svg")


def _tiled_by_sex(df, value_col, ylabel, title_prefix, out_dir, cfg, fname_tag,
                  ylim=None):
    sex_color_map = utils.build_sex_color_map(cfg["treatment_colors"])
    SEX_ORDER     = ["M", "F", "Unknown"]
    day_order     = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    for trial in ("CS+", "CS-", "ITI"):
        sub = df[df["_trial_type"] == trial]
        if sub.empty:
            continue
        trial_ylim = ylim
        if trial_ylim is None:
            trial_ylim = _auto_mean_sem_ylim(
                sub, value_col,
                ["_day_folder", "treatment_group", "sex", "trial_index"])

        n_cols = len(day_order)
        fig, axes = plt.subplots(1, n_cols, figsize=(min(18, 3.2*n_cols), 3.4), squeeze=False)
        axes = axes[0]
        fig.suptitle(f"{title_prefix} — {trial} (by sex × treatment)", fontsize=12, y=0.98)

        legend_entries = {}
        for c, day in enumerate(day_order):
            ax  = axes[c]
            dfd = sub[sub["_day_folder"] == day]
            if dfd.empty:
                ax.set_axis_off(); continue
            for trt in cfg["canonical_groups"]:
                for sex in SEX_ORDER:
                    grp = dfd[(dfd["treatment_group"] == trt) & (dfd["sex"] == sex)]
                    if grp.empty: continue
                    agg = grp.groupby("trial_index")[value_col].agg(["mean","count","std"]).reset_index()
                    if agg.empty: continue
                    color  = sex_color_map.get((trt, sex))
                    marker = utils.SEX_MARKERS.get(sex)
                    lbl    = f"{trt}-{sex}"
                    utils.plot_mean_sem(ax, agg, color=color, label=lbl, marker=marker,
                                        linestyle=(":" if sex == "Unknown" else "-"))
                    if lbl not in legend_entries:
                        legend_entries[lbl] = ax.lines[-1]
            ax.set_title(day, fontsize=9)
            ax.set_xlabel("Trial #", fontsize=8)
            ax.set_ylim(*trial_ylim)
            ax.grid(True, axis="y", alpha=0.25)
            if c == 0: ax.set_ylabel(ylabel, fontsize=9)
            utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        if legend_entries:
            lbls = sorted(legend_entries)
            fig.legend([legend_entries[l] for l in lbls], lbls,
                       loc="upper center", ncol=min(6, len(lbls)),
                       frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.93))
        fig.tight_layout(rect=(0, 0, 1, 0.88))
        tag = trial.replace("+","plus").replace("-","minus").lower()
        utils.save_fig(fig, out_dir / f"{fname_tag}_{tag}_by_sex_tiled.svg")


def _tiled_by_litter(df, value_col, ylabel, title_prefix, out_dir, cfg, fname_tag):
    if "litter_id" not in df.columns or df["litter_id"].isna().all():
        print("  [info] No litter_id data; skipping litter figures.")
        return

    day_order   = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)
    all_litters = sorted(df["litter_id"].dropna().unique())
    cmap        = plt.cm.get_cmap("rainbow", max(len(all_litters), 1))
    lcolors     = {l: cmap(i) for i, l in enumerate(all_litters)}
    litter_dir  = out_dir / "litter_breakdown"
    litter_dir.mkdir(exist_ok=True)
    groups      = cfg["canonical_groups"]

    for trial in ("CS+", "CS-", "ITI"):
        sub = df[df["_trial_type"] == trial]
        if sub.empty:
            continue

        n_rows, n_cols = len(groups), len(day_order)
        fig, axes = plt.subplots(n_rows, n_cols,
                                  figsize=(min(18, 3.2*n_cols), 3.2*n_rows),
                                  squeeze=False)
        fig.suptitle(f"{title_prefix} — {trial} (by litter)", fontsize=12, y=0.98)

        for r, trt in enumerate(groups):
            df_tr = sub[sub["treatment_group"] == trt]
            for c, day in enumerate(day_order):
                ax  = axes[r][c]
                dfd = df_tr[df_tr["_day_folder"] == day].dropna(subset=["litter_id"])
                if dfd.empty:
                    ax.set_axis_off(); continue
                for litter, lsub in dfd.groupby("litter_id"):
                    agg = lsub.groupby("trial_index")[value_col].agg(["mean","count","std"]).reset_index()
                    utils.plot_mean_sem(ax, agg, color=lcolors.get(litter), label=str(litter))
                if r == 0: ax.set_title(day, fontsize=9)
                if c == 0: ax.set_ylabel(f"{trt}\n{ylabel}", fontsize=9)
                ax.set_xlabel("Trial #", fontsize=8)
                ax.set_ylim(0, 100)
                ax.grid(True, axis="y", alpha=0.25)
                utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        handles, labels = {}, {}
        for row in axes:
            for ax in row:
                for h, l in zip(*ax.get_legend_handles_labels()):
                    if l not in labels:
                        labels[l] = h
        if labels:
            fig.legend(labels.values(), labels.keys(), loc="upper center",
                       ncol=min(10, len(labels)), frameon=False, fontsize=7,
                       bbox_to_anchor=(0.5, 0.93))
        fig.tight_layout(rect=(0, 0, 1, 0.9))
        tag = trial.replace("+","plus").replace("-","minus").lower()
        utils.save_fig(fig, litter_dir / f"{fname_tag}_{tag}_by_litter_tiled.svg")


# -- Total time freezing bar plots -------------------------------------------

TOTAL_FREEZING_CONDITIONS = ("Pre-trial", "CS+", "CS-")


def _subject_total_freezing(df):
    """
    Return one row per subject/session/condition for the total freezing bar plots.
    CS+ and CS- values are averaged across trials within subject first.
    """
    if df.empty or "_trial_type" not in df.columns:
        return pd.DataFrame()

    id_cols = [
        "_day_folder", "day", "context", "session_label",
        "animal_id", "behavior_id", "treatment_group",
        "sex", "litter_id", "cohort_id",
    ]
    id_cols = [c for c in id_cols if c in df.columns]

    rows = []
    for condition in TOTAL_FREEZING_CONDITIONS:
        sub = df[df["_trial_type"] == condition]
        if sub.empty:
            continue
        summary = (
            sub.groupby(id_cols, dropna=False, as_index=False)["freeze_pct"]
               .mean()
        )
        summary["condition"] = condition
        rows.append(summary)

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _sem(values):
    values = pd.to_numeric(values, errors="coerce").dropna()
    if len(values) <= 1:
        return 0.0
    return float(values.std(ddof=1) / np.sqrt(len(values)))


def _treatment_color_map(cfg, groups):
    configured = cfg.get("treatment_colors", {}) or {}
    lookup = cfg.get("treatment_lookup", {}) or {}
    by_label = {}
    for label, color in configured.items():
        raw = str(label).strip()
        canonical = utils.normalize_treatment(raw, lookup)
        # Accept either canonical treatment names or aliases in TREATMENT_COLORS;
        # the analysis rows store canonical labels after metadata normalization.
        for key in {raw, raw.lower(), canonical, canonical.lower()}:
            by_label[key] = color

    color_map = {}
    for idx, group in enumerate(groups):
        raw = str(group).strip()
        canonical = utils.normalize_treatment(raw, lookup)
        color_map[group] = (
            by_label.get(raw)
            or by_label.get(canonical)
            or by_label.get(raw.lower())
            or by_label.get(canonical.lower())
            or f"C{idx % 10}"
        )
    return color_map


def _total_freezing_bar_plots(df, out_dir, cfg, fname_tag):
    subject_df = _subject_total_freezing(df)
    if subject_df.empty:
        print("  [info] No total freezing summary data; skipping bar plots.")
        return

    plot_dir = out_dir / "total_time_freezing_bar_plots"
    plot_dir.mkdir(exist_ok=True)

    day_order = sorted(subject_df["_day_folder"].dropna().unique(), key=utils.day_sort_key)
    if not day_order:
        return

    all_treatments = set(subject_df["treatment_group"].dropna())
    groups = [g for g in cfg["canonical_groups"] if g in all_treatments]
    groups.extend(sorted(all_treatments.difference(groups)))
    if not groups:
        return

    colors = _treatment_color_map(cfg, groups)

    n_days = len(day_order)
    n_cols = min(3, n_days)
    n_rows = int(np.ceil(n_days / n_cols))
    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(4.7 * n_cols, 3.7 * n_rows),
        squeeze=False,
        sharey=True,
    )

    x_base = np.arange(len(TOTAL_FREEZING_CONDITIONS), dtype=float)
    total_width = 0.78
    bar_width = total_width / max(len(groups), 1)
    offsets = (np.arange(len(groups)) - (len(groups) - 1) / 2.0) * bar_width
    legend_handles = {}

    for idx, day in enumerate(day_order):
        ax = axes[idx // n_cols][idx % n_cols]
        dfd = subject_df[subject_df["_day_folder"] == day]
        if dfd.empty:
            ax.set_axis_off()
            continue

        present_conditions = [
            c for c in TOTAL_FREEZING_CONDITIONS
            if c in set(dfd["condition"].dropna())
        ]
        if not present_conditions:
            ax.set_axis_off()
            continue

        for i, trt in enumerate(groups):
            color = colors.get(trt, "#666666")
            for j, condition in enumerate(TOTAL_FREEZING_CONDITIONS):
                vals_df = (
                    dfd[(dfd["treatment_group"] == trt) &
                        (dfd["condition"] == condition)]
                )
                vals = pd.to_numeric(vals_df["freeze_pct"], errors="coerce").dropna()
                if vals.empty:
                    continue

                x = x_base[j] + offsets[i]
                mean = float(vals.mean())
                sem = _sem(vals)
                ax.bar(
                    x, mean, width=bar_width * 0.86,
                    color=color, alpha=0.82,
                    edgecolor="black", linewidth=0.55,
                    zorder=2,
                )
                if trt not in legend_handles:
                    legend_handles[trt] = ax.patches[-1]
                ax.errorbar(
                    x, mean, yerr=sem, fmt="none",
                    ecolor="black", elinewidth=0.8,
                    capsize=2.5, capthick=0.8,
                    zorder=4,
                )

                scatter_span = min(bar_width * 0.24, 0.05)
                jitter = (
                    np.zeros(len(vals))
                    if len(vals) == 1
                    else np.linspace(-scatter_span, scatter_span, len(vals))
                )
                ax.scatter(
                    np.full(len(vals), x) + jitter,
                    vals.to_numpy(dtype=float),
                    s=28, marker="o",
                    facecolors="none", edgecolors="black",
                    linewidths=0.9, alpha=0.95,
                    zorder=5,
                )

        ax.set_title(str(day), fontsize=10)
        ax.set_xticks(x_base)
        ax.set_xticklabels(TOTAL_FREEZING_CONDITIONS, fontsize=9)
        ax.set_ylim(0, 100)
        ax.grid(True, axis="y", alpha=0.25, zorder=1)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        if idx % n_cols == 0:
            ax.set_ylabel("% freezing", fontsize=9)

    for idx in range(n_days, n_rows * n_cols):
        axes[idx // n_cols][idx % n_cols].set_axis_off()

    if legend_handles:
        labels = [g for g in groups if g in legend_handles]
        fig.legend(
            [legend_handles[g] for g in labels], labels,
            loc="upper center", ncol=len(labels), frameon=False, fontsize=9,
            bbox_to_anchor=(0.5, 0.965),
        )

    fig.suptitle("Total Time Freezing", fontsize=12, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    utils.save_fig(
        fig,
        plot_dir / f"{fname_tag}_total_time_freezing_bars_tiled.svg",
    )


# -- Prism export (unchanged) -------------------------------------------------

def _prism_export(df, value_col, out_dir, tag):
    df     = df.sort_values(["treatment_group","_day_folder","_trial_type","trial_index","animal_id"])
    groups = df[["treatment_group","_day_folder","_trial_type"]].drop_duplicates()
    out_path = out_dir / f"{tag}_prism_ready.xlsx"
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for _, row in groups.iterrows():
            subset = df[(df["treatment_group"] == row["treatment_group"]) &
                        (df["_day_folder"]     == row["_day_folder"])     &
                        (df["_trial_type"]     == row["_trial_type"])]
            wide = subset.pivot_table(index="trial_index", columns="animal_id",
                                      values=value_col, aggfunc="mean").sort_index()
            wide.columns.name = "animal_id"
            sheet = f"{row['treatment_group']}_{row['_day_folder']}_{row['_trial_type']}"[:31]
            wide.to_excel(writer, sheet_name=sheet)
    print(f"[ok] Prism table saved: {out_path}")


# ── Output helper (unchanged except no per-day bout CSV write) ────────────────

def _write_outputs(freeze_df, bout_df, out_dir, cfg, fname_tag):
    out_dir.mkdir(parents=True, exist_ok=True)

    freeze_df["_trial_type"] = freeze_df["trial_type"].apply(utils.normalize_trial_type)
    freeze_df = utils.filter_trials(freeze_df, cfg["cs_trial_cap"], cfg["iti_trial_cap"])
    freeze_df.to_csv(out_dir / f"{fname_tag}_all_days_concat.csv", index=False)

    _total_freezing_bar_plots(freeze_df, out_dir, cfg, fname_tag)

    _tiled_individual(freeze_df, "freeze_pct", "% freezing",
                     "% Time Freezing", out_dir, cfg, fname_tag, ylim=(0, 100))
    _tiled_group_means(freeze_df, "freeze_pct", "% freezing",
                        "% Time Freezing", out_dir, cfg, fname_tag, ylim=(0, 100))
    if cfg["freezing_by_sex"]:
        _tiled_by_sex(freeze_df, "freeze_pct", "% freezing",
                       "% Time Freezing", out_dir, cfg, fname_tag, ylim=(0, 100))
    if cfg["freezing_by_litter"]:
        _tiled_by_litter(freeze_df, "freeze_pct", "% freezing",
                          "% Time Freezing", out_dir, cfg, fname_tag)

    if cfg["freezing_bouts"] and not bout_df.empty:
        bout_dir = out_dir / "freezing_bouts"
        bout_dir.mkdir(exist_ok=True)
        bout_df["_trial_type"] = bout_df["trial_type"].apply(utils.normalize_trial_type)
        bout_df.to_csv(bout_dir / f"{fname_tag}_bouts_all_days_concat.csv", index=False)

        counts_df = (
            bout_df
            .groupby(
                ["animal_id", "behavior_id", "treatment_group", "sex",
                 "litter_id", "cohort_id", "_day_folder", "_trial_type", "trial_index"],
                dropna=False, as_index=False,
            )
            .apply(lambda g: pd.Series({"bout_count": int(g["bout_start_s"].notna().sum())}))
            .reset_index(drop=True)
        )
        counts_df = utils.filter_trials(counts_df, cfg["cs_trial_cap"], cfg["iti_trial_cap"])
        _tiled_individual(counts_df, "bout_count", "Bout count",
                     "Freezing Bouts", bout_dir, cfg, f"{fname_tag}_bouts", ylim=None)
        _tiled_group_means(counts_df, "bout_count", "Bout count",
                            "Freezing Bouts", bout_dir, cfg, f"{fname_tag}_bouts", ylim=None)
        if cfg["freezing_by_sex"]:
            _tiled_by_sex(counts_df, "bout_count", "Bout count",
                           "Freezing Bouts", bout_dir, cfg, f"{fname_tag}_bouts", ylim=None)
        if cfg["prism_export"]:
            _prism_export(counts_df, "bout_count", bout_dir, f"{fname_tag}_bouts")

    if cfg["prism_export"]:
        _prism_export(freeze_df, "freeze_pct", out_dir, fname_tag)


# ── Entry point ─────────────────────────────────────────────────────────────

def _find_behaviordata_for_cohort(cohort_id, behaviordata_dirs):
    for bd in behaviordata_dirs:
        meta = utils.load_metadata(bd)
        if meta is not None and "cohort_id" in meta.columns:
            if str(cohort_id) in meta["cohort_id"].astype(str).values:
                return bd
    return None


def run(cfg, report=None):
    # Single pass: process all CSVs in parallel, results stay in memory.
    # No per-day CSVs are written.
    print("  Collecting and processing all CSVs in parallel...")
    freeze_df, bout_df = _collect_all_parallel(cfg, report=report)

    if freeze_df.empty:
        print("  [warn] No freezing data found. Skipping figures.")
        return

    print("  Pass 2: writing cohort outputs...")
    if "cohort_id" in freeze_df.columns:
        for cohort_id, cohort_freeze in freeze_df.groupby("cohort_id", dropna=False):
            if pd.isna(cohort_id):
                continue
            cohort_bout = (bout_df[bout_df["cohort_id"] == cohort_id]
                           if not bout_df.empty and "cohort_id" in bout_df.columns
                           else pd.DataFrame())
            cohort_bd = _find_behaviordata_for_cohort(cohort_id, cfg["behaviordata_dirs"])
            if cohort_bd is None:
                print(f"  [warn] Cannot locate BehaviorData folder for cohort '{cohort_id}'; skipping.")
                continue
            cohort_out = cohort_bd / "Analysis" / cfg["freezing_subfolder"]
            print(f"  Writing cohort '{cohort_id}' outputs to {cohort_out}")
            _write_outputs(cohort_freeze.copy(), cohort_bout.copy(),
                           cohort_out, cfg, f"freezing_{cohort_id}")

    print("  Pass 3: writing combined outputs...")
    combined_out = cfg["analysis_out"] / cfg["freezing_subfolder"]
    _write_outputs(freeze_df.copy(), bout_df.copy(), combined_out, cfg, "freezing")

    print("  Freezing analysis complete.")
