# -*- coding: utf-8 -*-
"""
us_locked_analysis.py
---------------------
Computes % time on platform during each US (shock) window, locked to the
moment of shock delivery rather than the full CS+ trial window.

Two modes for identifying US windows (set USE_SHOCKER_COLUMN in runner.py):

  Mode A (USE_SHOCKER_COLUMN = True):
      Reads the 'Shocker active' column directly. Detects contiguous blocks
      where the value == 1. US windows are restricted to within detected CS+
      trial boundaries as a safeguard against stray pulses from timing
      imprecision in the electronics.

  Mode B (USE_SHOCKER_COLUMN = False):
      Derives the US window as the last US_DURATION_S seconds of each
      detected CS+ trial. Uses the same TTL/tone-status trial detection
      logic as platform_analysis.py. Valid for all sessions including yoked.

Outputs (per treatment group, and optionally per cohort):
  - us_locked_all_days_concat.csv      Long-format raw data
  - us_locked_prism_ready.xlsx         Wide Prism-ready table (if enabled)
  - us_locked_shock_avoidance.csv      Count of 100%-avoided US events
  - us_locked_heatmap_<treatment>.svg  One heatmap per treatment group
"""

from pathlib import Path
import sys
import re
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

import utils


# =============================================================================
# US window detection
# =============================================================================

def _get_us_windows_mode_a(df: pd.DataFrame, time_col: str,
                            cs_trials: list) -> list:
    """
    Mode A: detect US windows from 'Shocker active' column.
    Windows are intersected with CS+ trial boundaries to exclude any stray
    shocker pulses that fall outside tone periods.
    """
    shocker_col = next(
        (c for c in df.columns if "shocker" in c.lower() and "active" in c.lower()),
        None
    )
    if shocker_col is None:
        return None, "missing_shocker_column"

    t = df[time_col].astype(float).to_numpy()
    s = df[shocker_col].fillna(0).astype(float).to_numpy()

    # Build CS+ trial intervals for boundary enforcement
    cs_intervals = [(tr["start"], tr["end"]) for tr in cs_trials if tr["type"] == "CS+"]

    us_windows = []
    us_counter = 0
    in_block = False
    block_start_idx = None

    for i in range(len(s)):
        if s[i] == 1 and not in_block:
            in_block = True
            block_start_idx = i
        elif s[i] != 1 and in_block:
            in_block = False
            block_start = float(t[block_start_idx])
            block_end   = float(t[i])

            # Restrict to within a CS+ window
            for cs_start, cs_end in cs_intervals:
                clipped_start = max(block_start, cs_start)
                clipped_end   = min(block_end,   cs_end)
                if clipped_end > clipped_start:
                    us_counter += 1
                    us_windows.append({
                        "type":        "US",
                        "start":       clipped_start,
                        "end":         clipped_end,
                        "trial_index": us_counter,
                        "source":      shocker_col,
                    })
                    break  # each shocker block maps to at most one CS+ trial

    # Handle file ending mid-block
    if in_block:
        block_start = float(t[block_start_idx])
        block_end   = float(t[-1])
        for cs_start, cs_end in cs_intervals:
            clipped_start = max(block_start, cs_start)
            clipped_end   = min(block_end,   cs_end)
            if clipped_end > clipped_start:
                us_counter += 1
                us_windows.append({
                    "type":        "US",
                    "start":       clipped_start,
                    "end":         clipped_end,
                    "trial_index": us_counter,
                    "source":      shocker_col,
                })
                break

    return us_windows, shocker_col


def _get_us_windows_mode_b(cs_trials: list, us_duration_s: float) -> tuple:
    """
    Mode B: derive US window as the last US_DURATION_S seconds of each CS+
    trial. Uses the same trial boundaries produced by utils.detect_trials_*,
    so this is consistent with platform_analysis.py's trial detection.
    """
    us_windows = []
    for tr in cs_trials:
        if tr["type"] != "CS+":
            continue
        us_end   = tr["end"]
        us_start = max(tr["start"], us_end - us_duration_s)
        us_windows.append({
            "type":        "US",
            "start":       us_start,
            "end":         us_end,
            "trial_index": tr["trial_index"],
            "source":      "last_2s_of_CS+",
        })
    return us_windows, "last_2s_of_CS+"


# =============================================================================
# Per-file processing
# =============================================================================

def _process_file(csv_path: Path, meta: pd.DataFrame, day_dir: Path,
                  cfg: dict, run_log: dict) -> list:
    """
    Process one animal's CSV for one session.
    Returns a list of row dicts (one per US event).
    Populates run_log with per-subject validation notes.
    """
    test_date, behavior_id = utils.parse_filename_bits(csv_path)
    subject_key = csv_path.name

    if behavior_id is None:
        run_log["excluded_subjects"][subject_key] = "could_not_parse_behavior_id"
        return []

    row_meta = utils.find_metadata_for_behavior(meta, behavior_id) \
        if meta is not None else pd.DataFrame()

    if row_meta.empty:
        run_log["excluded_subjects"][subject_key] = \
            f"behavior_id '{behavior_id}' not found in metadata"
        return []

    row         = row_meta.iloc[0]
    animal_id   = row.get("animal_id", behavior_id)
    cohort_id   = row.get("cohort_id", None)
    treatment   = utils.normalize_treatment(
        row.get("treatment_group", "Unknown"), cfg["treatment_lookup"]
    )
    sex         = utils.normalize_sex(row.get("sex", None))

    # --- Treatment filtering ---
    # Only treatments defined in TREATMENT_ALIASES (runner.py) are valid.
    # Subjects whose treatment maps to something outside the canonical list
    # are automatically excluded here.
    canonical = cfg["canonical_groups"]
    include   = cfg.get("include_treatments")  # None = all canonical groups

    if treatment not in canonical:
        run_log["excluded_subjects"][subject_key] = \
            f"treatment '{treatment}' not in canonical groups {canonical}"
        return []

    if include is not None and treatment not in include:
        run_log["excluded_subjects"][subject_key] = \
            f"treatment '{treatment}' not in INCLUDE_TREATMENTS {include}"
        return []

    if behavior_id in cfg.get("exclude_behavior_ids", []):
        run_log["excluded_subjects"][subject_key] = \
            "explicitly excluded via EXCLUDE_BEHAVIOR_IDS"
        return []

    # --- Load CSV ---
    df = utils.load_csv(csv_path)
    subject_log = {"columns_used": {}, "skipped_analyses": [], "warnings": []}

    try:
        time_col = utils.find_time_col(df)
        subject_log["columns_used"]["time"] = time_col
    except ValueError as e:
        subject_log["skipped_analyses"].append(f"no time column: {e}")
        run_log["subject_logs"][subject_key] = subject_log
        run_log["excluded_subjects"][subject_key] = "missing time column"
        return []

    if "in_platform" not in df.columns:
        subject_log["skipped_analyses"].append("missing 'in_platform' column")
        run_log["subject_logs"][subject_key] = subject_log
        run_log["excluded_subjects"][subject_key] = "missing in_platform column"
        return []

    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)

    # --- Detect CS+ trials (shared logic with platform_analysis.py,
    #     controlled by CS_DETECTION_MODE in runner.py) ---
    trials, _, cs_source = utils.detect_trials(df, time_col, cfg)
    subject_log["columns_used"]["CS+_detection"] = cs_source

    cs_trials = [tr for tr in trials if tr["type"] == "CS+"]
    if not cs_trials:
        subject_log["skipped_analyses"].append("no CS+ trials detected")
        run_log["subject_logs"][subject_key] = subject_log
        return []

    # --- Detect US windows (Mode A or Mode B) ---
    if cfg["use_shocker_column"]:
        # Mode A: use Shocker active column, clipped to CS+ windows
        us_windows, us_source = _get_us_windows_mode_a(df, time_col, cs_trials)
        if us_windows is None:
            subject_log["skipped_analyses"].append(
                f"Mode A selected but: {us_source}"
            )
            run_log["subject_logs"][subject_key] = subject_log
            run_log["excluded_subjects"][subject_key] = us_source
            return []
    else:
        # Mode B: last US_DURATION_S seconds of each CS+ trial
        us_windows, us_source = _get_us_windows_mode_b(
            cs_trials, cfg["us_duration_s"]
        )

    subject_log["columns_used"]["US_detection"] = us_source

    if not us_windows:
        subject_log["skipped_analyses"].append("no US windows detected")
        run_log["subject_logs"][subject_key] = subject_log
        return []

    # --- Compute platform % during each US window ---
    t    = df[time_col].astype(float).to_numpy()
    p    = df["in_platform"].fillna(0).astype(float).to_numpy()
    day, context, session = utils.parse_folder_bits(day_dir.name)

    rows = []
    for w in us_windows:
        dur    = max(0.0, w["end"] - w["start"])
        plat_s = utils.integrate_binary(t, p, w["start"], w["end"]) if dur > 0 else 0.0
        plat_pct = 100.0 * plat_s / dur if dur > 0 else 0.0
        rows.append({
            "animal_id":              animal_id,
            "behavior_id":            behavior_id,
            "cohort_id":              cohort_id,
            "treatment_group":        treatment,
            "sex":                    sex,
            "test_date":              test_date,
            "day":                    day,
            "context":                context,
            "session_label":          session,
            "_day_folder":            day_dir.name,
            "_source_csv":            csv_path.name,
            "us_number":              w["trial_index"],
            "us_start_s":             w["start"],
            "us_end_s":               w["end"],
            "platform_time_s":        plat_s,
            "platform_pct":           plat_pct,
            "platform_pct_above_chance": plat_pct - cfg["us_chance_baseline_pct"],
        })

    run_log["subject_logs"][subject_key] = subject_log
    return rows


# =============================================================================
# Data collection
# =============================================================================

def _collect_all(cfg: dict, meta: pd.DataFrame, run_log: dict) -> pd.DataFrame:
    behaviordata = cfg["behaviordata"]
    suffix       = cfg["csv_suffix"]

    # US-locked analysis reads raw session CSVs from top-level day folders,
    # the same as eee_analysis.py (raw signal needed for both modes).
    all_rows = []
    for day_dir in utils.find_session_dirs(behaviordata):
        csvs = sorted(
            day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv")
        )
        for csv_path in csvs:
            rows = _process_file(csv_path, meta, day_dir, cfg, run_log)
            all_rows.extend(rows)

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


# =============================================================================
# Heatmap
# =============================================================================

def _make_heatmap(df: pd.DataFrame, treatment: str, out_dir: Path,
                  cfg: dict, cohort_label: str = "all"):
    """
    One heatmap SVG per treatment group (and optionally per cohort).
    X-axis = US number (= CS+ trial index; labeled 'US#' because we report
    the unconditioned stimulus window).
    Y-axis = one row per animal.
    Sort order controlled by HEATMAP_SORT in runner.py.
    """
    if df.empty:
        return

    baseline = cfg["us_chance_baseline_pct"]
    sort_mode = cfg.get("heatmap_sort", "response") # Change how heatmap sorts subjects

    # Pivot: rows=animal, cols=US number
    pivot = df.pivot_table(
        index="animal_id",
        columns="us_number",
        values="platform_pct_above_chance",
        aggfunc="mean",
    )

    if sort_mode == "response":
        order = pivot.mean(axis=1).sort_values(ascending=False).index
    else:
        # Alphanumeric sort keeps animals in the same row across sessions
        order = sorted(pivot.index)

    pivot = pivot.loc[order]

    max_us   = int(pivot.columns.max()) if len(pivot.columns) else 1
    fig_w    = max(6, max_us * 0.5)
    fig_h    = max(3, len(pivot) * 0.5)
    fig, ax  = plt.subplots(figsize=(fig_w, fig_h))

    vmax = 100.0 - baseline
    vmin = -baseline

    sns.heatmap(
        pivot,
        cmap="coolwarm_r",
        vmin=vmin,
        vmax=vmax,
        center=0,
        square=True,
        ax=ax,
        cbar_kws={"label": "% platform time during US (above chance)"},
    )

    ax.set_xticks(np.arange(max_us) + 0.5)
    ax.set_xticklabels([f"US{i}" for i in range(1, max_us + 1)], rotation=45)
    ax.set_xlabel("US number")
    ax.set_ylabel("Animal (sorted by response)" if sort_mode == "response"
                  else "Animal (alphabetical)")
    title = f"{treatment}"
    if cohort_label != "all":
        title += f" — cohort {cohort_label}"
    ax.set_title(title)

    plt.tight_layout()
    tag = f"us_locked_heatmap_{treatment}"
    if cohort_label != "all":
        tag += f"_cohort{cohort_label}"
    utils.save_fig(fig, out_dir / f"{tag}.svg")


# =============================================================================
# Prism export
# =============================================================================

def _prism_export(df: pd.DataFrame, out_dir: Path, tag: str):
    """
    Wide-format Excel: one sheet per treatment × day.
    Rows = US number, columns = animal_id.
    """
    df  = df.sort_values(["treatment_group", "_day_folder", "us_number", "animal_id"])
    groups = df[["treatment_group", "_day_folder"]].drop_duplicates()
    out_path = out_dir / f"{tag}_prism_ready.xlsx"

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for _, row in groups.iterrows():
            subset = df[
                (df["treatment_group"] == row["treatment_group"]) &
                (df["_day_folder"]     == row["_day_folder"])
            ]
            wide = subset.pivot_table(
                index="us_number",
                columns="animal_id",
                values="platform_pct_above_chance",
                aggfunc="mean",
            ).sort_index()
            wide.columns.name = "animal_id"
            sheet = f"{row['treatment_group']}_{row['_day_folder']}"[:31]
            wide.to_excel(writer, sheet_name=sheet)

    print(f"[ok] Prism table saved: {out_path}")


# =============================================================================
# Shock avoidance summary
# =============================================================================

def _shock_avoidance(df: pd.DataFrame, out_dir: Path, tag: str):
    """
    Count of US events where platform_pct == 100 per animal per day.
    A value of 100% means the animal was on the platform for the entire
    shock window — i.e., the shock was fully avoided.
    """
    avoided = df[df["platform_pct"] >= 100.0].copy()
    summary = (
        avoided.groupby(
            ["treatment_group", "cohort_id", "animal_id", "_day_folder"],
            dropna=False, as_index=False
        )
        .agg(shocks_avoided=("us_number", "count"))
    )
    out_path = out_dir / f"{tag}_shock_avoidance.csv"
    summary.to_csv(out_path, index=False)
    print(f"[ok] Shock avoidance summary saved: {out_path}")


# =============================================================================
# Run report
# =============================================================================

def _write_run_report(out_dir: Path, cfg: dict, run_log: dict):
    """
    Writes a human-readable run report with:
      - timestamp and Python version
      - all runner settings relevant to this analysis
      - included treatments
      - excluded subjects and reasons
      - per-subject data validation notes
    """
    report_path = out_dir / "us_locked_run_report.txt"
    lines = []

    lines.append("=" * 70)
    lines.append("US-LOCKED PLATFORM ANALYSIS — RUN REPORT")
    lines.append("=" * 70)
    lines.append(f"Timestamp : {run_log['timestamp']}")
    lines.append(f"Python    : {run_log['python_version']}")
    lines.append("")

    lines.append("--- Runner settings ---")
    settings_keys = [
        "use_shocker_column", "us_duration_s", "us_chance_baseline_pct",
        "include_treatments", "exclude_behavior_ids", "separate_by_cohort",
        "heatmap_sort", "prism_export", "cs_trial_cap",
    ]
    for k in settings_keys:
        lines.append(f"  {k:<30} {cfg.get(k)}")
    lines.append("")

    lines.append("--- Included treatments ---")
    for t in run_log.get("included_treatments", []):
        lines.append(f"  {t}")
    lines.append("")

    lines.append("--- Excluded subjects ---")
    excl = run_log.get("excluded_subjects", {})
    if excl:
        for subj, reason in excl.items():
            lines.append(f"  {subj:<50} reason: {reason}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("--- Per-subject validation log ---")
    slogs = run_log.get("subject_logs", {})
    if slogs:
        for subj, slog in slogs.items():
            lines.append(f"  {subj}")
            cols = slog.get("columns_used", {})
            for k, v in cols.items():
                lines.append(f"    columns_used.{k:<25} {v}")
            for w in slog.get("warnings", []):
                lines.append(f"    WARNING: {w}")
            for s in slog.get("skipped_analyses", []):
                lines.append(f"    SKIPPED: {s}")
    else:
        lines.append("  (no subjects processed)")

    lines.append("=" * 70)

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[ok] Run report saved: {report_path}")


# =============================================================================
# Entry point
# =============================================================================

def run(cfg: dict):
    behaviordata = cfg["behaviordata"]
    out_dir      = behaviordata / "US locked platform time"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Initialise run log (populated throughout processing)
    run_log = {
        "timestamp":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "python_version":     sys.version,
        "excluded_subjects":  {},
        "subject_logs":       {},
        "included_treatments": [],
    }

    meta = utils.load_metadata(behaviordata)

    print("  Collecting US-locked platform data across all session days...")
    df = _collect_all(cfg, meta, run_log)

    if df.empty:
        print("  [warn] No US-locked data found. Skipping US-locked analysis.")
        _write_run_report(out_dir, cfg, run_log)
        return

    df.to_csv(out_dir / "us_locked_all_days_concat.csv", index=False)
    print("  [ok] Concatenated CSV saved.")

    treatments = df["treatment_group"].dropna().unique().tolist()
    run_log["included_treatments"] = treatments

    # --- Per-treatment outputs ---
    for trt in cfg["canonical_groups"]:
        include = cfg.get("include_treatments")
        if include is not None and trt not in include:
            continue

        df_trt = df[df["treatment_group"] == trt]
        if df_trt.empty:
            continue

        trt_dir = out_dir / trt
        trt_dir.mkdir(exist_ok=True)

        # Shock avoidance
        _shock_avoidance(df_trt, trt_dir, trt)

        # Heatmap (all cohorts combined)
        _make_heatmap(df_trt, trt, trt_dir, cfg, cohort_label="all")

        # Per-cohort heatmaps and outputs
        if cfg.get("separate_by_cohort") and "cohort_id" in df_trt.columns:
            for cohort in sorted(df_trt["cohort_id"].dropna().unique()):
                df_cohort = df_trt[df_trt["cohort_id"] == cohort]
                cohort_dir = trt_dir / f"cohort_{cohort}"
                cohort_dir.mkdir(exist_ok=True)
                _make_heatmap(df_cohort, trt, cohort_dir, cfg,
                              cohort_label=str(cohort))
                _shock_avoidance(df_cohort, cohort_dir, f"{trt}_cohort{cohort}")
                if cfg.get("prism_export"):
                    _prism_export(df_cohort, cohort_dir,
                                  f"{trt}_cohort{cohort}")

        # Prism export (combined cohorts)
        if cfg.get("prism_export"):
            _prism_export(df_trt, trt_dir, trt)

    _write_run_report(out_dir, cfg, run_log)
    print("  US-locked analysis complete.")
