# -*- coding: utf-8 -*-
"""
freezing_analysis.py
--------------------
Two-pass pipeline for % time freezing and (optionally) freezing bout counts.

Pass 1 — Per-day (per BehaviorData folder):
    Reads raw AnyMaze CSVs from the top level of each day folder.
    Writes per-day freezing_summary.csv into:
        <day_folder>/Analysis/<subfolder>/freezing_summary.csv

Pass 2 — Cumulative:
    Reads all per-day summaries across ALL BehaviorData folders.
    Combined outputs  → cfg["analysis_out"] / "<subfolder>"/
    Cohort outputs    → <BehaviorData>/Analysis/<subfolder>/
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import utils
import run_report as rr


# ── Pass 1 helpers ──────────────────────────────────────────────────────────

def _process_file(csv_path, meta, day_dir, cfg, report=None):
    test_date, behavior_id = utils.parse_filename_bits(csv_path, meta)
    if behavior_id is None:
        print(f"  [warn] Could not parse behavior_id from {csv_path.name}; skipping.")
        if report is not None:
            rr.record_exclusion(report, "freezing", csv_path.name,
                                "could not parse behavior_id from filename")
        return pd.DataFrame()

    row_meta = utils.find_metadata_for_behavior(meta, behavior_id) \
        if meta is not None else pd.DataFrame()
    if row_meta.empty:
        print(f"  [warn] behavior_id '{behavior_id}' not in metadata; skipping {csv_path.name}.")
        if report is not None:
            rr.record_exclusion(report, "freezing", csv_path.name,
                                f"behavior_id '{behavior_id}' not found in metadata")
        return pd.DataFrame()

    row       = row_meta.iloc[0]
    animal_id = row.get("animal_id", behavior_id)
    treatment = utils.normalize_treatment(row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    sex       = utils.normalize_sex(row.get("sex", None))
    litter_id = row.get("litter_id", None)
    cohort_id = row.get("cohort_id", None)

    df = utils.load_csv(csv_path)
    try:
        time_col   = utils.find_time_col(df, cfg)
        freeze_col = utils.find_freeze_col(df, cfg)
    except ValueError as e:
        print(f"  [warn] {csv_path.name}: {e}; skipping.")
        if report is not None:
            rr.record_exclusion(report, "freezing", csv_path.name, str(e))
        return pd.DataFrame()

    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    trials, itis, cs_source = utils.detect_trials(df, time_col, cfg)
    windows = trials + itis
    if not windows:
        print(f"  [warn] No trials detected in {csv_path.name}.")
        if report is not None:
            rr.record_exclusion(report, "freezing", csv_path.name, "no trials detected")
        return pd.DataFrame()

    # Record columns used for this subject
    if report is not None:
        rr.record_subject(report, "freezing", csv_path.name,
                          columns_used={"time":          time_col,
                                        "freeze":        freeze_col,
                                        "CS+_detection": cs_source})
        if cfg.get("freezing_bouts"):
            rr.record_subject(report, "freezing_bouts", csv_path.name,
                              columns_used={"time":          time_col,
                                            "freeze":        freeze_col,
                                            "CS+_detection": cs_source})

    day, context, session = utils.parse_folder_bits(day_dir.name)
    t = df[time_col].astype(float).to_numpy()
    x = df[freeze_col].fillna(0).astype(float).to_numpy()

    rows = []
    for w in windows:
        dur      = max(0.0, w["end"] - w["start"])
        freeze_s = utils.integrate_binary(t, x, w["start"], w["end"]) if dur > 0 else 0.0
        rows.append({
            "animal_id":      animal_id,
            "behavior_id":    behavior_id,
            "treatment_group": treatment,
            "sex":            sex,
            "litter_id":      litter_id,
            "cohort_id":      cohort_id,
            "test_date":      test_date,
            "day":            day,
            "context":        context,
            "session_label":  session,
            "_day_folder":    day_dir.name,
            "_source_csv":    csv_path.name,
            "trial_type":     w["type"],
            "trial_index":    w["trial_index"],
            "window_start_s": w["start"],
            "window_end_s":   w["end"],
            "window_len_s":   dur,
            "freeze_time_s":  freeze_s,
            "freeze_pct":     100.0 * freeze_s / dur if dur > 0 else 0.0,
        })

    return pd.DataFrame(rows)


def _process_day(day_dir, meta, cfg, report=None):
    suffix    = cfg["csv_suffix"]
    subfolder = cfg["freezing_subfolder"]
    csvs      = sorted(day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
    if not csvs:
        print(f"  [warn] No matching CSVs in {day_dir}")
        return pd.DataFrame()

    frames = []
    for csv_path in csvs:
        df = _process_file(csv_path, meta, day_dir, cfg, report=report)
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    day_df  = pd.concat(frames, ignore_index=True)
    out_dir = day_dir / "Analysis" / subfolder
    out_dir.mkdir(parents=True, exist_ok=True)
    day_df.to_csv(out_dir / "freezing_summary.csv", index=False)
    print(f"  [ok] Per-day summary written: {out_dir / 'freezing_summary.csv'}")
    return day_df


# ── Bout detection ──────────────────────────────────────────────────────────

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


def _compute_bouts(day_dir, meta, cfg, report=None):
    suffix  = cfg["csv_suffix"]
    out_dir = day_dir / "Analysis" / cfg["freezing_subfolder"] / "freezing_bouts"
    out_dir.mkdir(parents=True, exist_ok=True)
    csvs    = sorted(day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))

    all_rows = []
    for csv_path in csvs:
        test_date, behavior_id = utils.parse_filename_bits(csv_path, meta)
        if behavior_id is None:
            continue
        row_meta = utils.find_metadata_for_behavior(meta, behavior_id) \
            if meta is not None else pd.DataFrame()
        if row_meta.empty:
            continue

        row       = row_meta.iloc[0]
        animal_id = row.get("animal_id", behavior_id)
        treatment = utils.normalize_treatment(row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
        sex       = utils.normalize_sex(row.get("sex", None))
        litter_id = row.get("litter_id", None)
        cohort_id = row.get("cohort_id", None)

        df = utils.load_csv(csv_path)
        try:
            time_col   = utils.find_time_col(df, cfg)
            freeze_col = utils.find_freeze_col(df, cfg)
        except ValueError:
            continue

        df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
        trials, itis, _ = utils.detect_trials(df, time_col, cfg)
        windows = trials + itis
        if not windows:
            continue

        day, context, session = utils.parse_folder_bits(day_dir.name)
        t = df[time_col].astype(float).to_numpy()
        x = df[freeze_col].fillna(0).astype(float).to_numpy()

        for w in windows:
            for j, b in enumerate(_detect_bouts(t, x, w["start"], w["end"]), start=1):
                all_rows.append({
                    "animal_id":        animal_id,
                    "behavior_id":      behavior_id,
                    "treatment_group":  treatment,
                    "sex":              sex,
                    "litter_id":        litter_id,
                    "cohort_id":        cohort_id,
                    "test_date":        test_date,
                    "day":              day,
                    "context":          context,
                    "session_label":    session,
                    "_day_folder":      day_dir.name,
                    "_source_csv":      csv_path.name,
                    "trial_type":       w["type"],
                    "trial_index":      w["trial_index"],
                    "window_start_s":   w["start"],
                    "window_end_s":     w["end"],
                    "bout_id_in_trial": j,
                    "bout_start_s":     b["start"],
                    "bout_end_s":       b["end"],
                    "bout_dur_s":       b["end"] - b["start"],
                })

    bout_df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame()
    if not bout_df.empty:
        bout_df.to_csv(out_dir / "Freezing_Bouts_Long.csv", index=False)
    return bout_df


# ── Pass 2 - cumulative collection across all BehaviorData dirs ─────────────

def _collect_cumulative(cfg):
    subfolder     = cfg["freezing_subfolder"]
    freeze_frames = []
    bout_frames   = []

    for bd in cfg["behaviordata_dirs"]:
        for day_dir in utils.find_session_dirs(bd):
            summary = day_dir / "Analysis" / subfolder / "freezing_summary.csv"
            if summary.exists():
                freeze_frames.append(pd.read_csv(summary))
            else:
                print(f"  [warn] Missing per-day summary: {summary}")

            if cfg["freezing_bouts"]:
                bouts_csv = day_dir / "Analysis" / subfolder / "freezing_bouts" / "Freezing_Bouts_Long.csv"
                if bouts_csv.exists():
                    bout_frames.append(pd.read_csv(bouts_csv))

    freeze_df = pd.concat(freeze_frames, ignore_index=True) if freeze_frames else pd.DataFrame()
    bout_df   = pd.concat(bout_frames,   ignore_index=True) if bout_frames   else pd.DataFrame()
    return freeze_df, bout_df


# ── Figures ─────────────────────────────────────────────────────────────────

def _tiled_individual(df, value_col, ylabel, title_prefix, out_dir, cfg, fname_tag,
                      ylim=None):
    groups    = cfg["canonical_groups"]
    colors    = cfg["treatment_colors"]
    day_order = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    if ylim is None:
        agg_all = (df.groupby(["_day_folder", "trial_index"])[value_col]
                     .agg(["mean", "count", "std"]).reset_index())
        agg_all["sem"] = agg_all["std"] / agg_all["count"].pow(0.5)
        y_max = (agg_all["mean"] + agg_all["sem"]).max()
        y_max = y_max * 1.1 if not np.isnan(y_max) else 10
        ylim = (0, max(y_max, 1))

    for trial in ("CS+", "CS-", "ITI"):
        sub = df[df["_trial_type"] == trial]
        if sub.empty:
            continue

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
                ax.set_ylim(*ylim)
                ax.grid(True, axis="y", alpha=0.25)
                utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        fig.tight_layout(rect=[0, 0, 1, 0.95])
        tag = trial.replace("+","plus").replace("-","minus").lower()
        utils.save_fig(fig, out_dir / f"{fname_tag}_{tag}_individual_tiled.svg")


def _tiled_group_means(df, value_col, ylabel, title_prefix, out_dir, cfg, fname_tag,
                        trial_types=("CS+", "CS-", "ITI"), ylim=None):
    groups    = cfg["canonical_groups"]
    colors    = cfg["treatment_colors"]
    day_order = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    for trial in trial_types:
        sub = df[df["_trial_type"] == trial]
        if sub.empty:
            continue

        n_cols = len(day_order)
        fig, axes = plt.subplots(1, n_cols, figsize=(min(18, 3.2*n_cols), 3.4), squeeze=False)
        axes = axes[0]
        fig.suptitle(f"{title_prefix} — {trial} (group means)", fontsize=12, y=0.98)

        legend_handles, legend_labels = [], []
        for c, day in enumerate(day_order):
            ax  = axes[c]
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
            ax.set_title(day, fontsize=9)
            ax.set_xlabel("Trial #", fontsize=8)
            ax.set_ylim(*(ylim if ylim else (0, 100)))
            ax.grid(True, axis="y", alpha=0.25)
            if c == 0: ax.set_ylabel(ylabel, fontsize=9)
            utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        if legend_handles:
            fig.legend(legend_handles, legend_labels, loc="upper center",
                       ncol=len(legend_labels), frameon=False, fontsize=9,
                       bbox_to_anchor=(0.5, 0.93))
        fig.tight_layout(rect=[0, 0, 1, 0.88])
        tag = trial.replace("+","plus").replace("-","minus").lower()
        utils.save_fig(fig, out_dir / f"{fname_tag}_{tag}_groupmeans_tiled.svg")


def _tiled_by_sex(df, value_col, ylabel, title_prefix, out_dir, cfg, fname_tag):
    sex_color_map = utils.build_sex_color_map(cfg["treatment_colors"])
    SEX_ORDER     = ["M", "F", "Unknown"]
    day_order     = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    for trial in ("CS+", "CS-", "ITI"):
        sub = df[df["_trial_type"] == trial]
        if sub.empty:
            continue

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
            ax.set_ylim(0, 100)
            ax.grid(True, axis="y", alpha=0.25)
            if c == 0: ax.set_ylabel(ylabel, fontsize=9)
            utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        if legend_entries:
            lbls = sorted(legend_entries)
            fig.legend([legend_entries[l] for l in lbls], lbls,
                       loc="upper center", ncol=min(6, len(lbls)),
                       frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.93))
        fig.tight_layout(rect=[0, 0, 1, 0.88])
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
        fig.tight_layout(rect=[0, 0, 1, 0.9])
        tag = trial.replace("+","plus").replace("-","minus").lower()
        utils.save_fig(fig, litter_dir / f"{fname_tag}_{tag}_by_litter_tiled.svg")


# ── Prism export ────────────────────────────────────────────────────────────

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


# ── Output helpers ──────────────────────────────────────────────────────────

def _write_outputs(freeze_df, bout_df, out_dir, cfg, fname_tag):
    out_dir.mkdir(parents=True, exist_ok=True)

    freeze_df["_trial_type"] = freeze_df["trial_type"].apply(utils.normalize_trial_type)
    freeze_df = utils.filter_trials(freeze_df, cfg["cs_trial_cap"], cfg["iti_trial_cap"])
    freeze_df.to_csv(out_dir / f"{fname_tag}_all_days_concat.csv", index=False)

    _tiled_individual(freeze_df, "freeze_pct", "% freezing",
                     "% Time Freezing", out_dir, cfg, fname_tag,
                     ylim=(0, 100))
    _tiled_group_means(freeze_df, "freeze_pct", "% freezing",
                        "% Time Freezing", out_dir, cfg, fname_tag,
                        ylim=(0, 100))
    if cfg["freezing_by_sex"]:
        _tiled_by_sex(freeze_df, "freeze_pct", "% freezing",
                       "% Time Freezing", out_dir, cfg, fname_tag)
    if cfg["freezing_by_litter"]:
        _tiled_by_litter(freeze_df, "freeze_pct", "% freezing",
                          "% Time Freezing", out_dir, cfg, fname_tag)

    if cfg["freezing_bouts"] and not bout_df.empty:
        bout_dir = out_dir / "freezing_bouts"
        bout_dir.mkdir(exist_ok=True)
        bout_df["_trial_type"] = bout_df["trial_type"].apply(utils.normalize_trial_type)
        bout_df.to_csv(bout_dir / f"{fname_tag}_bouts_all_days_concat.csv", index=False)
        counts_df = (
            bout_df.groupby(
                ["animal_id","behavior_id","treatment_group","sex",
                 "litter_id","cohort_id","_day_folder","_trial_type","trial_index"],
                dropna=False, as_index=False,
            ).size().rename(columns={"size": "bout_count"})
        )
        counts_df = utils.filter_trials(counts_df, cfg["cs_trial_cap"], cfg["iti_trial_cap"])
        _tiled_individual(counts_df, "bout_count", "Bout count",
                     "Freezing Bouts", bout_dir, cfg, f"{fname_tag}_bouts",
                     ylim=None)
        _tiled_group_means(counts_df, "bout_count", "Bout count",
                            "Freezing Bouts", bout_dir, cfg, f"{fname_tag}_bouts",
                            ylim=None)
        if cfg["freezing_by_sex"]:
            _tiled_by_sex(counts_df, "bout_count", "Bout count",
                           "Freezing Bouts", bout_dir, cfg, f"{fname_tag}_bouts",
                           ylim=None)
        if cfg["prism_export"]:
            _prism_export(counts_df, "bout_count", bout_dir, f"{fname_tag}_bouts")

    if cfg["prism_export"]:
        _prism_export(freeze_df, "freeze_pct", out_dir, fname_tag)


# ── Entry point ─────────────────────────────────────────────────────────────

def run(cfg, report=None):
    subfolder = cfg["freezing_subfolder"]

    # Pass 1
    print("  Pass 1: processing raw CSVs day by day...")
    for bd in cfg["behaviordata_dirs"]:
        meta = utils.load_metadata(bd)
        for day_dir in utils.find_session_dirs(bd):
            _process_day(day_dir, meta, cfg, report=report)
            if cfg["freezing_bouts"]:
                _compute_bouts(day_dir, meta, cfg, report=report)

    # Pass 2
    print("  Pass 2: concatenating across all cohorts...")
    freeze_df, bout_df = _collect_cumulative(cfg)

    if freeze_df.empty:
        print("  [warn] No freezing data found. Skipping figures.")
        return

    print("  Writing combined outputs...")
    combined_out = cfg["analysis_out"] / subfolder
    _write_outputs(freeze_df.copy(), bout_df.copy(), combined_out, cfg, "freezing")

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
            cohort_out = cohort_bd / "Analysis" / subfolder
            print(f"  Writing cohort '{cohort_id}' outputs to {cohort_out}")
            _write_outputs(cohort_freeze.copy(), cohort_bout.copy(),
                           cohort_out, cfg, f"freezing_{cohort_id}")

    print("  Freezing analysis complete.")


def _find_behaviordata_for_cohort(cohort_id, behaviordata_dirs):
    for bd in behaviordata_dirs:
        meta = utils.load_metadata(bd)
        if meta is not None and "cohort_id" in meta.columns:
            if str(cohort_id) in meta["cohort_id"].astype(str).values:
                return bd
    return None
