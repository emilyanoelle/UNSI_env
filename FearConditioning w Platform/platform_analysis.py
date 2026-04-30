# -*- coding: utf-8 -*-
"""
platform_analysis.py
--------------------
Two-pass pipeline for % time on platform and (optionally) latency to platform.

Pass 1 — Per-day:
    Reads raw _fixed.csv AnyMaze files from the top level of each day folder.
    Writes one per-day summary CSV per animal into:
        <day_folder>/% time on platform/platform_summary.csv

Pass 2 — Cumulative:
    Reads all per-day summary CSVs, concatenates across days, and produces
    tiled multi-day figures and Prism-ready tables in:
        BehaviorData/% time on platform/
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import utils


# =============================================================================
# Latency helpers
# =============================================================================

def _compute_latency(t: np.ndarray, p: np.ndarray,
                     start: float, end: float) -> float:
    """
    Compute latency to first platform entry within [start, end).
    Returns 0.0 if already on platform at window start, NaN if never entered.
    """
    idx = np.searchsorted(t, start, side="right") - 1
    if idx >= 0 and p[idx] > 0:
        return 0.0
    for i in range(len(t)):
        if t[i] < start:
            continue
        if t[i] >= end:
            break
        prev = p[i-1] if i > 0 else 0
        if prev == 0 and p[i] > 0:
            return float(t[i] - start)
    return np.nan


def _find_latency_col(df: pd.DataFrame):
    """Return an explicit latency column if present, else None."""
    candidates = [
        "latency_to_platform_s", "latency_to_platform",
        "latency_s", "latency", "latency_to_platform__s",
    ]
    for c in candidates:
        if c in df.columns:
            return c
    for c in df.columns:
        if "latency" in c.lower() and "platform" in c.lower():
            return c
    return None


# =============================================================================
# Pass 1 helpers — per-file and per-day processing
# =============================================================================

def _process_file(csv_path: Path, meta: pd.DataFrame,
                  day_dir: Path, cfg: dict) -> pd.DataFrame:
    """
    Process one animal's raw AnyMaze CSV for one session day.
    Returns a DataFrame of per-trial platform metrics, or empty DataFrame on failure.
    """
    test_date, behavior_id = utils.parse_filename_bits(csv_path)
    if behavior_id is None:
        print(f"  [warn] Could not parse behavior_id from {csv_path.name}; skipping.")
        return pd.DataFrame()

    row_meta = utils.find_metadata_for_behavior(meta, behavior_id) \
        if meta is not None else pd.DataFrame()
    if row_meta.empty:
        print(f"  [warn] behavior_id '{behavior_id}' not in metadata; skipping {csv_path.name}.")
        return pd.DataFrame()

    row       = row_meta.iloc[0]
    animal_id = row.get("animal_id", behavior_id)
    treatment = utils.normalize_treatment(
        row.get("treatment_group", "Unknown"), cfg["treatment_lookup"]
    )
    sex       = utils.normalize_sex(row.get("sex", None))
    litter_id = row.get("litter_id", None)

    df = utils.load_csv(csv_path)
    try:
        time_col = utils.find_time_col(df)
    except ValueError as e:
        print(f"  [warn] {csv_path.name}: {e}; skipping.")
        return pd.DataFrame()

    if "in_platform" not in df.columns:
        print(f"  [warn] {csv_path.name}: no 'in_platform' column; skipping.")
        return pd.DataFrame()

    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)

    trials, itis, _ = utils.detect_trials(df, time_col, cfg)
    windows = trials + itis
    if not windows:
        print(f"  [warn] No trials detected in {csv_path.name}.")
        return pd.DataFrame()

    day, context, session = utils.parse_folder_bits(day_dir.name)
    t   = df[time_col].astype(float).to_numpy()
    p   = df["in_platform"].fillna(0).astype(float).to_numpy()
    lat_col = _find_latency_col(df)

    rows = []
    for w in windows:
        dur    = max(0.0, w["end"] - w["start"])
        plat_s = utils.integrate_binary(t, p, w["start"], w["end"]) if dur > 0 else 0.0
        plat_pct = 100.0 * plat_s / dur if dur > 0 else 0.0

        # Latency: prefer explicit column; compute from signal if absent
        if w["type"] in ("CS+", "CS-") and cfg["platform_latency"]:
            if lat_col is not None:
                sub = df[(df[time_col] >= w["start"]) & (df[time_col] < w["end"])]
                latency = float(sub[lat_col].dropna().iloc[0]) \
                    if not sub[lat_col].dropna().empty else np.nan
            else:
                latency = _compute_latency(t, p, w["start"], w["end"])
        else:
            latency = np.nan

        rows.append({
            "animal_id":              animal_id,
            "behavior_id":            behavior_id,
            "treatment_group":        treatment,
            "sex":                    sex,
            "litter_id":              litter_id,
            "test_date":              test_date,
            "day":                    day,
            "context":                context,
            "session_label":          session,
            "_day_folder":            day_dir.name,
            "_source_csv":            csv_path.name,
            "trial_type":             w["type"],
            "trial_index":            w["trial_index"],
            "window_start_s":         w["start"],
            "window_end_s":           w["end"],
            "window_len_s":           dur,
            "platform_time_s":        plat_s,
            "platform_pct":           plat_pct,
            "latency_to_platform_s":  latency,
        })

    return pd.DataFrame(rows)


def _process_day(day_dir: Path, meta: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Pass 1: process all raw CSVs in one day folder.
    Writes per-day platform_summary.csv into the % time on platform subfolder.
    Returns the combined DataFrame for that day.
    """
    suffix    = cfg["csv_suffix"]
    subfolder = cfg["platform_subfolder"]

    csvs = sorted(
        day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv")
    )
    if not csvs:
        print(f"  [warn] No matching CSVs in {day_dir}")
        return pd.DataFrame()

    day_frames = []
    for csv_path in csvs:
        df = _process_file(csv_path, meta, day_dir, cfg)
        if not df.empty:
            day_frames.append(df)

    if not day_frames:
        return pd.DataFrame()

    day_df = pd.concat(day_frames, ignore_index=True)

    out_dir = day_dir / subfolder
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "platform_summary.csv"
    day_df.to_csv(out_path, index=False)
    print(f"  [ok] Per-day summary written: {out_path}")

    return day_df


# =============================================================================
# Pass 2 — cumulative collection
# =============================================================================

def _collect_cumulative(cfg: dict) -> pd.DataFrame:
    """
    Pass 2: read all per-day platform_summary.csv files and concatenate.
    """
    behaviordata = cfg["behaviordata"]
    subfolder    = cfg["platform_subfolder"]

    frames = []
    for day_dir in utils.find_session_dirs(behaviordata):
        summary = day_dir / subfolder / "platform_summary.csv"
        if summary.exists():
            frames.append(pd.read_csv(summary))
        else:
            print(f"  [warn] Missing per-day summary: {summary}")

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# =============================================================================
# Figures
# =============================================================================

def _tiled_individual(df: pd.DataFrame, value_col: str, ylabel: str,
                       title_prefix: str, out_dir: Path, cfg: dict,
                       fname_tag: str, trial_types=("CS+", "CS-", "ITI"),
                       ylim=None):
    groups    = cfg["canonical_groups"]
    colors    = cfg["treatment_colors"]
    day_order = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    for trial in trial_types:
        sub = df[df["_trial_type"] == trial]
        if sub.empty:
            continue

        n_rows, n_cols = len(groups), len(day_order)
        fig, axes = plt.subplots(
            n_rows, n_cols,
            figsize=(min(18, 3.0*n_cols), min(12, 2.6*n_rows)),
            squeeze=False,
        )
        fig.suptitle(f"{title_prefix} — {trial}", fontsize=12, y=0.98)

        for r, trt in enumerate(groups):
            df_tr = sub[sub["treatment_group"] == trt]
            for c, day in enumerate(day_order):
                ax  = axes[r][c]
                dfd = df_tr[df_tr["_day_folder"] == day]
                if dfd.empty:
                    ax.set_axis_off(); continue

                for _, g in dfd.groupby("behavior_id"):
                    ax.plot(
                        g.sort_values("trial_index")["trial_index"],
                        g.sort_values("trial_index")[value_col],
                        linewidth=0.9, alpha=0.35, color=colors.get(trt),
                    )

                agg = dfd.groupby("trial_index")[value_col].agg(
                    ["mean", "count", "std"]
                ).reset_index()
                utils.plot_mean_sem(ax, agg, color=colors.get(trt))

                if r == 0: ax.set_title(day, fontsize=9)
                if c == 0: ax.set_ylabel(f"{trt}\n{ylabel}", fontsize=9)
                ax.set_xlabel("Trial #", fontsize=8)
                ax.set_ylim(*ylim) if ylim else ax.set_ylim(bottom=0)
                ax.grid(True, axis="y", alpha=0.25)
                utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        fig.tight_layout(rect=[0, 0, 1, 0.95])
        tag = trial.replace("+", "plus").replace("-", "minus").lower()
        utils.save_fig(fig, out_dir / f"{fname_tag}_{tag}_individual_tiled.svg")


def _tiled_group_means(df: pd.DataFrame, value_col: str, ylabel: str,
                        title_prefix: str, out_dir: Path, cfg: dict,
                        fname_tag: str, trial_types=("CS+", "CS-", "ITI"),
                        ylim=None):
    groups    = cfg["canonical_groups"]
    colors    = cfg["treatment_colors"]
    day_order = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    for trial in trial_types:
        sub = df[df["_trial_type"] == trial]
        if sub.empty:
            continue

        n_cols = len(day_order)
        fig, axes = plt.subplots(
            1, n_cols, figsize=(min(18, 3.2*n_cols), 3.4), squeeze=False
        )
        axes = axes[0]
        fig.suptitle(f"{title_prefix} — {trial} (group means)", fontsize=12, y=0.98)

        legend_handles, legend_labels = [], []
        for c, day in enumerate(day_order):
            ax  = axes[c]
            dfd = sub[sub["_day_folder"] == day]
            if dfd.empty:
                ax.set_axis_off(); continue

            for trt in groups:
                agg = (
                    dfd[dfd["treatment_group"] == trt]
                    .groupby("trial_index")[value_col]
                    .agg(["mean", "count", "std"])
                    .reset_index()
                )
                if agg.empty:
                    continue
                utils.plot_mean_sem(ax, agg, color=colors.get(trt), label=trt)
                if trt not in legend_labels:
                    legend_labels.append(trt)
                    legend_handles.append(ax.lines[-1])

            ax.set_title(day, fontsize=9)
            ax.set_xlabel("Trial #", fontsize=8)
            ax.set_ylim(*ylim) if ylim else ax.set_ylim(bottom=0)
            ax.grid(True, axis="y", alpha=0.25)
            if c == 0: ax.set_ylabel(ylabel, fontsize=9)
            utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        if legend_handles:
            fig.legend(
                legend_handles, legend_labels,
                loc="upper center", ncol=len(legend_labels),
                frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.93),
            )

        fig.tight_layout(rect=[0, 0, 1, 0.88])
        tag = trial.replace("+", "plus").replace("-", "minus").lower()
        utils.save_fig(fig, out_dir / f"{fname_tag}_{tag}_groupmeans_tiled.svg")


def _tiled_by_sex(df: pd.DataFrame, value_col: str, ylabel: str,
                   title_prefix: str, out_dir: Path, cfg: dict,
                   fname_tag: str, trial_types=("CS+", "CS-", "ITI"),
                   ylim=None):
    sex_color_map = utils.build_sex_color_map(cfg["treatment_colors"])
    SEX_ORDER     = ["M", "F", "Unknown"]
    day_order     = sorted(df["_day_folder"].unique(), key=utils.day_sort_key)

    for trial in trial_types:
        sub = df[df["_trial_type"] == trial]
        if sub.empty:
            continue

        n_cols = len(day_order)
        fig, axes = plt.subplots(
            1, n_cols, figsize=(min(18, 3.2*n_cols), 3.4), squeeze=False
        )
        axes = axes[0]
        fig.suptitle(
            f"{title_prefix} — {trial} (by sex × treatment)", fontsize=12, y=0.98
        )

        legend_entries = {}
        for c, day in enumerate(day_order):
            ax  = axes[c]
            dfd = sub[sub["_day_folder"] == day]
            if dfd.empty:
                ax.set_axis_off(); continue

            for trt in cfg["canonical_groups"]:
                for sex in SEX_ORDER:
                    grp = dfd[(dfd["treatment_group"] == trt) & (dfd["sex"] == sex)]
                    if grp.empty:
                        continue
                    agg = grp.groupby("trial_index")[value_col].agg(
                        ["mean", "count", "std"]
                    ).reset_index()
                    if agg.empty:
                        continue
                    color  = sex_color_map.get((trt, sex))
                    marker = utils.SEX_MARKERS.get(sex)
                    lbl    = f"{trt}-{sex}"
                    utils.plot_mean_sem(
                        ax, agg, color=color, label=lbl,
                        marker=marker,
                        linestyle=(":" if sex == "Unknown" else "-"),
                    )
                    if lbl not in legend_entries:
                        legend_entries[lbl] = ax.lines[-1]

            ax.set_title(day, fontsize=9)
            ax.set_xlabel("Trial #", fontsize=8)
            ax.set_ylim(*ylim) if ylim else ax.set_ylim(bottom=0)
            ax.grid(True, axis="y", alpha=0.25)
            if c == 0: ax.set_ylabel(ylabel, fontsize=9)
            utils.sparse_xticks(ax, int(dfd["trial_index"].max()))

        if legend_entries:
            lbls = sorted(legend_entries)
            fig.legend(
                [legend_entries[l] for l in lbls], lbls,
                loc="upper center", ncol=min(6, len(lbls)),
                frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.93),
            )

        fig.tight_layout(rect=[0, 0, 1, 0.88])
        tag = trial.replace("+", "plus").replace("-", "minus").lower()
        utils.save_fig(fig, out_dir / f"{fname_tag}_{tag}_by_sex_tiled.svg")


# =============================================================================
# Prism export
# =============================================================================

def _prism_export(df: pd.DataFrame, value_col: str, out_dir: Path, tag: str):
    df     = df.sort_values(
        ["treatment_group", "_day_folder", "_trial_type", "trial_index", "animal_id"]
    )
    groups   = df[["treatment_group", "_day_folder", "_trial_type"]].drop_duplicates()
    out_path = out_dir / f"{tag}_prism_ready.xlsx"

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for _, row in groups.iterrows():
            subset = df[
                (df["treatment_group"] == row["treatment_group"]) &
                (df["_day_folder"]     == row["_day_folder"])     &
                (df["_trial_type"]     == row["_trial_type"])
            ]
            wide = subset.pivot_table(
                index="trial_index", columns="animal_id",
                values=value_col, aggfunc="mean",
            ).sort_index()
            wide.columns.name = "animal_id"
            sheet = (
                f"{row['treatment_group']}_{row['_day_folder']}_{row['_trial_type']}"
            )[:31]
            wide.to_excel(writer, sheet_name=sheet)

    print(f"[ok] Prism table saved: {out_path}")


# =============================================================================
# Entry point
# =============================================================================

def run(cfg: dict):
    behaviordata = cfg["behaviordata"]
    out_dir      = behaviordata / cfg["platform_subfolder"]
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = utils.load_metadata(behaviordata)

    # ------------------------------------------------------------------
    # Pass 1: process raw CSVs day by day, write per-day summary CSVs
    # ------------------------------------------------------------------
    print("  Pass 1: processing raw CSVs day by day...")
    for day_dir in utils.find_session_dirs(behaviordata):
        _process_day(day_dir, meta, cfg)

    # ------------------------------------------------------------------
    # Pass 2: read all per-day summaries, concatenate, produce figures
    # ------------------------------------------------------------------
    print("  Pass 2: concatenating and producing cumulative figures...")
    plat_df = _collect_cumulative(cfg)

    if plat_df.empty:
        print("  [warn] No platform data found after Pass 1. Skipping figures.")
        return

    plat_df["_trial_type"] = plat_df["trial_type"].apply(utils.normalize_trial_type)
    plat_df = utils.filter_trials(plat_df, cfg["cs_trial_cap"], cfg["iti_trial_cap"])
    plat_df.to_csv(out_dir / "platform_all_days_concat.csv", index=False)
    print("  [ok] Concatenated CSV saved.")

    print("  Generating % time on platform figures...")
    _tiled_individual(plat_df, "platform_pct", "% on platform",
                       "% Time on Platform", out_dir, cfg, "platform",
                       ylim=(0, 100))
    _tiled_group_means(plat_df, "platform_pct", "% on platform",
                        "% Time on Platform", out_dir, cfg, "platform",
                        ylim=(0, 100))
    if cfg["platform_by_sex"]:
        _tiled_by_sex(plat_df, "platform_pct", "% on platform",
                       "% Time on Platform", out_dir, cfg, "platform",
                       ylim=(0, 100))

    if cfg["platform_latency"]:
        lat_df = plat_df.dropna(subset=["latency_to_platform_s"])
        if not lat_df.empty:
            print("  Generating latency to platform figures...")
            lat_dir = out_dir / "latency_to_platform"
            lat_dir.mkdir(exist_ok=True)
            lat_df.to_csv(lat_dir / "latency_all_days_concat.csv", index=False)

            ymax = float(lat_df["latency_to_platform_s"].max())
            ymax = ymax + 0.05 * ymax if ymax > 0 else 1.0

            _tiled_individual(lat_df, "latency_to_platform_s", "Latency (s)",
                               "Latency to Platform", lat_dir, cfg, "latency",
                               trial_types=("CS+", "CS-"), ylim=(0, ymax))
            _tiled_group_means(lat_df, "latency_to_platform_s", "Latency (s)",
                                "Latency to Platform", lat_dir, cfg, "latency",
                                trial_types=("CS+", "CS-"), ylim=(0, ymax))
            if cfg["platform_by_sex"]:
                _tiled_by_sex(lat_df, "latency_to_platform_s", "Latency (s)",
                               "Latency to Platform", lat_dir, cfg, "latency",
                               trial_types=("CS+", "CS-"), ylim=(0, ymax))
            if cfg["prism_export"]:
                _prism_export(lat_df, "latency_to_platform_s", lat_dir, "latency")

    if cfg["prism_export"]:
        print("  Generating Prism-ready tables...")
        _prism_export(plat_df, "platform_pct", out_dir, "platform")

    print("  Platform analysis complete.")
