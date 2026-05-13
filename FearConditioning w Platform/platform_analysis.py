# -*- coding: utf-8 -*-
"""
platform_analysis.py
--------------------
Two-pass pipeline for % time on platform and (optionally) latency to platform.
"""

from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import utils
import run_report as rr


# ── Latency helpers ─────────────────────────────────────────────────────────

def _compute_latency(t, p, start, end):
    idx = np.searchsorted(t, start, side="right") - 1
    if idx >= 0 and p[idx] > 0:
        return 0.0
    for i in range(len(t)):
        if t[i] < start:   continue
        if t[i] >= end:    break
        prev = p[i-1] if i > 0 else 0
        if prev == 0 and p[i] > 0:
            return float(t[i] - start)
    return np.nan


# ── Pass 1 helpers ──────────────────────────────────────────────────────────

def _process_file(csv_path, meta, day_dir, cfg):
    report_data = {"subjects": {}, "exclusions": {}}
    subject_key = csv_path.name

    test_date, behavior_id, row, exclusion_reason = utils.metadata_for_csv(csv_path, meta)
    if exclusion_reason is not None:
        print(f"  [warn] {csv_path.name}: {exclusion_reason}; skipping.")
        report_data["exclusions"][subject_key] = exclusion_reason
        return [], report_data

    animal_id = row.get("animal_id", behavior_id)
    treatment = utils.normalize_treatment(row.get("treatment_group","Unknown"), cfg["treatment_lookup"])
    sex       = utils.normalize_sex(row.get("sex", None))
    litter_id = row.get("litter_id", None)
    cohort_id = row.get("cohort_id", None)

    df_header = utils.load_csv_header(csv_path)
    try:
        time_col = utils.find_time_col(df_header, cfg)
        platform_col = utils.find_platform_col(df_header, cfg)
        lat_col = utils.find_latency_col(df_header, cfg)
        source_cols = utils.unique_existing_columns(
            df_header,
            [time_col, platform_col, lat_col]
            + utils.trial_detection_source_columns(df_header, cfg),
        )
    except ValueError as e:
        print(f"  [warn] {csv_path.name}: {e}; skipping.")
        report_data["exclusions"][subject_key] = str(e)
        return [], report_data

    # Read only time/platform/latency/trial-detection columns; platform metrics
    # never use the other exported AnyMaze signals.
    df = utils.load_csv(csv_path, usecols=source_cols)
    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    trials, itis, cs_source = utils.detect_trials(df, time_col, cfg)
    windows = trials + itis
    if not windows:
        print(f"  [warn] No trials detected in {csv_path.name}.")
        report_data["exclusions"][subject_key] = "no trials detected"
        return [], report_data

    report_data["subjects"][subject_key] = {
        "columns_used": {
            "time":          time_col,
            "in_platform":   platform_col,
            "CS+_detection": cs_source,
            "latency_col":   lat_col or "computed from in_platform",
        },
        "warnings": [],
        "skipped":  [],
    }

    day, context, session = utils.parse_folder_bits(day_dir.name)
    t = df[time_col].astype(float).to_numpy()
    p = df[platform_col].fillna(0).astype(float).to_numpy()

    rows = []
    for w in windows:
        dur      = max(0.0, w["end"] - w["start"])
        plat_s   = utils.integrate_binary(t, p, w["start"], w["end"]) if dur > 0 else 0.0
        plat_pct = 100.0 * plat_s / dur if dur > 0 else 0.0

        if w["type"] in ("CS+","CS-") and cfg["platform_latency"]:
            if lat_col is not None:
                sub = df[(df[time_col] >= w["start"]) & (df[time_col] < w["end"])]
                latency = float(sub[lat_col].dropna().iloc[0]) \
                    if not sub[lat_col].dropna().empty else np.nan
            else:
                latency = _compute_latency(t, p, w["start"], w["end"])
        else:
            latency = np.nan

        rows.append({
            "animal_id":             animal_id,
            "behavior_id":           behavior_id,
            "treatment_group":       treatment,
            "sex":                   sex,
            "litter_id":             litter_id,
            "cohort_id":             cohort_id,
            "test_date":             test_date,
            "day":                   day,
            "context":               context,
            "session_label":         session,
            "_day_folder":           day_dir.name,
            "_day_dir":              str(day_dir),
            "_behaviordata_root":    str(day_dir.parent),
            "_source_csv":           csv_path.name,
            "trial_type":            w["type"],
            "trial_index":           w["trial_index"],
            "window_start_s":        w["start"],
            "window_end_s":          w["end"],
            "window_len_s":          dur,
            "platform_time_s":       plat_s,
            "platform_pct":          plat_pct,
            "latency_to_platform_s": latency,
        })

    return rows, report_data


def _process_file_star(args):
    return _process_file(*args)


# Worker processes cannot safely mutate the shared run report, so each worker
# returns small report payloads and the parent process records them here.
def _merge_report_data(report, report_data, cfg):
    if report is None:
        return
    for key, info in report_data["subjects"].items():
        rr.record_subject(report, "platform", key,
                          columns_used=info["columns_used"],
                          warnings=info["warnings"],
                          skipped=info["skipped"])
        if cfg.get("platform_latency"):
            rr.record_subject(report, "platform_latency", key,
                              columns_used={
                                  "latency_col": info["columns_used"].get("latency_col"),
                                  "time":        info["columns_used"].get("time"),
                                  "in_platform": info["columns_used"].get("in_platform"),
                              },
                              warnings=info["warnings"],
                              skipped=info["skipped"])
    for key, reason in report_data["exclusions"].items():
        rr.record_exclusion(report, "platform", key, reason)


def _collect_all_parallel(cfg, report=None):
    tasks = []
    for bd in cfg["behaviordata_dirs"]:
        meta   = utils.load_metadata(bd)
        suffix = cfg["csv_suffix"]
        for day_dir in utils.find_session_dirs(bd):
            csvs = sorted(
                day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
            for csv_path in csvs:
                tasks.append((csv_path, meta, day_dir, cfg))

    if not tasks:
        print("  [warn] No CSV files found.")
        return pd.DataFrame()

    print(f"  Found {len(tasks)} CSV files. Processing with {cfg['n_workers']} workers...")

    all_rows = []

    with ProcessPoolExecutor(max_workers=cfg["n_workers"]) as pool:
        # Track each future's original task so out-of-order completions still
        # report errors with the right source file.
        future_to_idx = {
            pool.submit(_process_file_star, t): i
            for i, t in enumerate(tasks)
        }
        for future in as_completed(future_to_idx):
            idx      = future_to_idx[future]
            csv_path = tasks[idx][0]
            try:
                rows, report_data = future.result()
            except Exception as exc:
                print(f"  [error] {csv_path.name} raised an exception: {exc}")
                continue

            if rows:
                all_rows.extend(rows)

            _merge_report_data(report, report_data, cfg)

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


# ── Figures ─────────────────────────────────────────────────────────────────

def _tiled_individual(df, value_col, ylabel, title_prefix, out_dir, cfg,
                       fname_tag, trial_types=("CS+","CS-","ITI"), ylim=None):
    groups    = cfg["canonical_groups"]
    colors    = cfg["treatment_colors"]
    day_order = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    for trial in trial_types:
        sub = df[df["_trial_type"] == trial]
        if sub.empty: continue

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
                ax.set_ylim(*(ylim if ylim else (0, 100)))
                ax.grid(True, axis="y", alpha=0.25)
                utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        fig.tight_layout(rect=[0, 0, 1, 0.95])
        tag = trial.replace("+","plus").replace("-","minus").lower()
        utils.save_fig(fig, out_dir / f"{fname_tag}_{tag}_individual_tiled.svg")


def _tiled_group_means(df, value_col, ylabel, title_prefix, out_dir, cfg,
                        fname_tag, trial_types=("CS+","CS-","ITI"), ylim=None):
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
    # Put trial type on rows and session day on columns so the group means can
    # be reviewed from one SVG instead of one file per trial type.
    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(min(20, 3.2 * n_cols), 2.9 * n_rows),
        squeeze=False,
    )
    fig.suptitle(f"{title_prefix} (group means)", fontsize=12, y=0.985)

    legend_handles, legend_labels = [], []
    for r, trial in enumerate(trial_types):
        sub = df[df["_trial_type"] == trial]
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
            ax.set_ylim(*(ylim if ylim else (0, 100)))
            ax.grid(True, axis="y", alpha=0.25)
            if c == 0:
                ax.set_ylabel(f"{trial}\n{ylabel}", fontsize=9)
            utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

    if legend_handles:
        fig.legend(legend_handles, legend_labels, loc="upper center",
                   ncol=len(legend_labels), frameon=False, fontsize=9,
                   bbox_to_anchor=(0.5, 0.955))
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    utils.save_fig(fig, out_dir / f"{fname_tag}_groupmeans_tiled.svg")


def _tiled_by_sex(df, value_col, ylabel, title_prefix, out_dir, cfg,
                   fname_tag, trial_types=("CS+","CS-","ITI"), ylim=None):
    sex_color_map = utils.build_sex_color_map(cfg["treatment_colors"])
    SEX_ORDER     = ["M","F","Unknown"]
    day_order     = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    for trial in trial_types:
        sub = df[df["_trial_type"] == trial]
        if sub.empty: continue

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
            ax.set_ylim(*(ylim if ylim else (0, 100)))
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


# ── Output helper ───────────────────────────────────────────────────────────

def _write_outputs(plat_df, out_dir, cfg, fname_tag):
    out_dir.mkdir(parents=True, exist_ok=True)

    plat_df["_trial_type"] = plat_df["trial_type"].apply(utils.normalize_trial_type)
    plat_df = utils.filter_trials(plat_df, cfg["cs_trial_cap"], cfg["iti_trial_cap"])
    plat_df.to_csv(out_dir / f"{fname_tag}_all_days_concat.csv", index=False)

    _tiled_individual(plat_df, "platform_pct", "% on platform",
                       "% Time on Platform", out_dir, cfg, fname_tag, ylim=(0,100))
    _tiled_group_means(plat_df, "platform_pct", "% on platform",
                        "% Time on Platform", out_dir, cfg, fname_tag, ylim=(0,100))
    if cfg["platform_by_sex"]:
        _tiled_by_sex(plat_df, "platform_pct", "% on platform",
                       "% Time on Platform", out_dir, cfg, fname_tag, ylim=(0,100))

    if cfg["platform_latency"]:
        lat_df = plat_df.dropna(subset=["latency_to_platform_s"])
        if not lat_df.empty:
            lat_dir = out_dir / "latency_to_platform"
            lat_dir.mkdir(exist_ok=True)
            lat_df.to_csv(lat_dir / f"{fname_tag}_latency_all_days_concat.csv", index=False)
            ymax = float(lat_df["latency_to_platform_s"].max())
            ymax = ymax + 0.05 * ymax if ymax > 0 else 1.0
            _tiled_individual(lat_df, "latency_to_platform_s", "Latency (s)",
                               "Latency to Platform", lat_dir, cfg, f"{fname_tag}_latency",
                               trial_types=("CS+","CS-"), ylim=(0, ymax))
            _tiled_group_means(lat_df, "latency_to_platform_s", "Latency (s)",
                                "Latency to Platform", lat_dir, cfg, f"{fname_tag}_latency",
                                trial_types=("CS+","CS-"), ylim=(0, ymax))
            if cfg["platform_by_sex"]:
                _tiled_by_sex(lat_df, "latency_to_platform_s", "Latency (s)",
                               "Latency to Platform", lat_dir, cfg, f"{fname_tag}_latency",
                               trial_types=("CS+","CS-"), ylim=(0, ymax))
            if cfg["prism_export"]:
                _prism_export(lat_df, "latency_to_platform_s", lat_dir, f"{fname_tag}_latency")

    if cfg["prism_export"]:
        _prism_export(plat_df, "platform_pct", out_dir, fname_tag)


def _find_behaviordata_for_cohort(cohort_id, behaviordata_dirs):
    for bd in behaviordata_dirs:
        meta = utils.load_metadata(bd)
        if meta is not None and "cohort_id" in meta.columns:
            if str(cohort_id) in meta["cohort_id"].astype(str).values:
                return bd
    return None


# ── Entry point ─────────────────────────────────────────────────────────────

def run(cfg, report=None):
    subfolder = cfg["platform_subfolder"]

    print("  Pass 1: collecting raw CSVs in parallel...")
    plat_df = _collect_all_parallel(cfg, report=report)
    if plat_df.empty:
        print("  [warn] No platform data found. Skipping figures.")
        return

    print("  Pass 2: writing combined outputs...")
    _write_outputs(plat_df.copy(), cfg["analysis_out"] / subfolder, cfg, "platform")

    if "cohort_id" in plat_df.columns:
        for cohort_id, cohort_df in plat_df.groupby("cohort_id", dropna=False):
            if pd.isna(cohort_id):
                continue
            if "_behaviordata_root" in cohort_df.columns and cohort_df["_behaviordata_root"].notna().any():
                cohort_bd = Path(cohort_df["_behaviordata_root"].dropna().iloc[0])
            else:
                cohort_bd = _find_behaviordata_for_cohort(cohort_id, cfg["behaviordata_dirs"])
            if cohort_bd is None:
                print(f"  [warn] Cannot locate BehaviorData for cohort '{cohort_id}'; skipping.")
                continue
            cohort_out = cohort_bd / "Analysis" / subfolder
            print(f"  Writing cohort '{cohort_id}' outputs to {cohort_out}")
            _write_outputs(cohort_df.copy(), cohort_out, cfg, f"platform_{cohort_id}")

    print("  Platform analysis complete.")
