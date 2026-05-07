# -*- coding: utf-8 -*-
"""
speed_analysis.py
-----------------
CS-locked speed analysis.

Pass 1 — Per-file, per-day:
    Loads raw AnyMaze CSVs, downsamples to 100ms bins, detects CS+ and CS-
    trial onsets, extracts a configurable pre/post window of speed values
    around each onset, and writes:
      - Per-animal CS-locked speed CSVs into:
            <day_dir>/Analysis/<subfolder>/speed_data/
      - Per-animal line plot PDFs (combined, CS+, CS-) into:
            <day_dir>/Analysis/<subfolder>/speed_graphs/

Pass 2 — Per-cohort aggregation:
    Reads all per-animal CSVs across all days in a BehaviorData folder,
    groups by treatment group, and writes per-group Excel workbooks into:
            <BehaviorData>/Analysis/<subfolder>/

Pass 3 — Cross-cohort aggregation:
    Combines per-group Excel workbooks across all BehaviorData folders and
    writes combined outputs into:
            <ANALYSIS_OUTPUT_DIR>/<subfolder>/
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import utils
import run_report as rr


# =============================================================================
# Helpers
# =============================================================================

def _bin_columns(pre_bins: int, post_bins: int) -> list:
    return [f"Bin{t}" for t in range(-pre_bins, post_bins + 1)]


def _extract_cs_number(cs_type: str) -> float:
    import re
    m = re.search(r"(\d+)", str(cs_type))
    return int(m.group(1)) if m else float("inf")


def _sanitize_sheet(name: str) -> str:
    import re
    return re.sub(r"[\\/*?:\[\]]", "_", str(name))[:31]


# =============================================================================
# Pass 1 — per-file processing
# =============================================================================

def _downsample(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    """Resample all columns to 100ms bins by mean, using the time column as index."""
    df = df.copy()
    df[time_col] = pd.to_timedelta(df[time_col].astype(float), unit="s")
    df = df.set_index(time_col)
    df_mean = df.resample("100ms").mean()
    df_mean = df_mean.reset_index()
    # Convert timedelta index back to float seconds for downstream use
    df_mean[time_col] = df_mean[time_col].dt.total_seconds()
    return df_mean


def _find_trial_onsets(df_ds: pd.DataFrame, time_col: str, cfg: dict) -> tuple:
    """
    Detect CS+ and CS- onset row-indices in the downsampled DataFrame.

    Uses detect_trials() on the downsampled data, then maps each detected
    trial start time to the nearest row index. Returns two lists of integer
    positional indices (not DataFrame index labels).
    """
    trials, _, _ = utils.detect_trials(df_ds, time_col, cfg)
    t_arr = df_ds[time_col].astype(float).to_numpy()

    cs_plus_idx, cs_minus_idx = [], []
    for tr in trials:
        pos = int(np.argmin(np.abs(t_arr - tr["start"])))
        if tr["type"] == "CS+":
            cs_plus_idx.append(pos)
        elif tr["type"] == "CS-":
            cs_minus_idx.append(pos)
    return cs_plus_idx, cs_minus_idx


def _extract_window(df_ds: pd.DataFrame, speed_col: str,
                    onset_pos: int, pre_bins: int, post_bins: int) -> list:
    """
    Extract speed values for the window [onset - pre_bins, onset + post_bins].
    Pads with NaN where the window extends beyond the recording.
    """
    n = len(df_ds)
    values = []
    for offset in range(-pre_bins, post_bins + 1):
        idx = onset_pos + offset
        if 0 <= idx < n:
            v = df_ds.iloc[idx][speed_col]
            values.append(float(v) if pd.notna(v) else np.nan)
        else:
            values.append(np.nan)
    return values


def _process_file(csv_path: Path, meta, day_dir: Path, cfg: dict,
                  report=None) -> list:
    """
    Process one raw CSV: downsample, detect onsets, extract windows.
    Returns a list of row dicts (one per trial) to be written to CSV.
    """
    test_date, behavior_id = utils.parse_filename_bits(csv_path, meta)
    if behavior_id is None:
        print(f"  [warn] Could not parse behavior_id from {csv_path.name}; skipping.")
        if report is not None:
            rr.record_exclusion(report, "speed", csv_path.name,
                                "could not parse behavior_id from filename")
        return []

    row_meta = utils.find_metadata_for_behavior(meta, behavior_id) \
        if meta is not None else pd.DataFrame()
    if row_meta.empty:
        print(f"  [warn] behavior_id '{behavior_id}' not in metadata; skipping {csv_path.name}.")
        if report is not None:
            rr.record_exclusion(report, "speed", csv_path.name,
                                f"behavior_id '{behavior_id}' not found in metadata")
        return []

    row       = row_meta.iloc[0]
    animal_id = row.get("animal_id", behavior_id)
    treatment = utils.normalize_treatment(
        row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    cohort_id = row.get("cohort_id", None)

    df_raw = utils.load_csv(csv_path)

    try:
        time_col = utils.find_time_col(df_raw, cfg)
    except ValueError as e:
        print(f"  [warn] {csv_path.name}: {e}; skipping.")
        if report is not None:
            rr.record_exclusion(report, "speed", csv_path.name, str(e))
        return []

    try:
        speed_col = utils.find_speed_col(df_raw, cfg)
    except ValueError as e:
        print(f"  [warn] {csv_path.name}: {e}; skipping.")
        if report is not None:
            rr.record_exclusion(report, "speed", csv_path.name, str(e))
        return []

    df_raw = df_raw.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)

    # Downsample to 100ms bins
    df_ds = _downsample(df_raw, time_col)

    # Detect onsets in downsampled data
    cs_plus_idx, cs_minus_idx = _find_trial_onsets(df_ds, time_col, cfg)

    if not cs_plus_idx and not cs_minus_idx:
        print(f"  [warn] No CS trials detected in {csv_path.name}; skipping.")
        if report is not None:
            rr.record_exclusion(report, "speed", csv_path.name, "no CS trials detected")
        return []

    if report is not None:
        rr.record_subject(report, "speed", csv_path.name,
                          columns_used={"time":  time_col,
                                        "speed": speed_col})

    pre_bins  = cfg["speed_pre_bins"]
    post_bins = cfg["speed_post_bins"]
    bin_cols  = _bin_columns(pre_bins, post_bins)
    day, context, session = utils.parse_folder_bits(day_dir.name)

    rows = []

    for count, onset_pos in enumerate(cs_plus_idx, start=1):
        vals = _extract_window(df_ds, speed_col, onset_pos, pre_bins, post_bins)
        start_time = float(df_ds.iloc[onset_pos][time_col])
        row_d = dict(
            animal_id=animal_id, behavior_id=behavior_id,
            treatment_group=treatment, cohort_id=cohort_id,
            test_date=test_date, day=day, context=context,
            session_label=session, _day_folder=day_dir.name,
            _source_csv=csv_path.name,
            file_name=csv_path.name,
            start_time_s=start_time,
            cs_type=f"CS+_{count}",
        )
        for col, v in zip(bin_cols, vals):
            row_d[col] = v
        rows.append(row_d)

    for count, onset_pos in enumerate(cs_minus_idx, start=1):
        vals = _extract_window(df_ds, speed_col, onset_pos, pre_bins, post_bins)
        start_time = float(df_ds.iloc[onset_pos][time_col])
        row_d = dict(
            animal_id=animal_id, behavior_id=behavior_id,
            treatment_group=treatment, cohort_id=cohort_id,
            test_date=test_date, day=day, context=context,
            session_label=session, _day_folder=day_dir.name,
            _source_csv=csv_path.name,
            file_name=csv_path.name,
            start_time_s=start_time,
            cs_type=f"CS-_{count}",
        )
        for col, v in zip(bin_cols, vals):
            row_d[col] = v
        rows.append(row_d)

    return rows


# =============================================================================
# Pass 1 — figures
# =============================================================================

def _make_animal_figures(animal_rows: list, original_stem: str,
                          graphs_dir: Path, cfg: dict):
    """
    Produce three line-plot PDFs for one animal:
      combined (CS+ and CS-), CS+ only, CS- only.
    Mirrors the original notebook's plot style exactly.
    """
    pre_bins  = cfg["speed_pre_bins"]
    post_bins = cfg["speed_post_bins"]
    bin_cols  = _bin_columns(pre_bins, post_bins)
    time_points = list(range(-pre_bins, post_bins + 1))

    # x-axis tick positions and labels (every 100 bins = every 10 s)
    xtick_pos    = time_points[::100]
    xtick_labels = [f"{t * 0.1:.1f}" for t in xtick_pos]

    combined_dir = graphs_dir / "combined"
    csplus_dir   = graphs_dir / "CS+"
    csminus_dir  = graphs_dir / "CS-"
    for d in (combined_dir, csplus_dir, csminus_dir):
        d.mkdir(parents=True, exist_ok=True)

    cmap = plt.get_cmap("tab20")

    cs_plus_rows  = [r for r in animal_rows if r["cs_type"].startswith("CS+")]
    cs_minus_rows = [r for r in animal_rows if r["cs_type"].startswith("CS-")]

    def _vals(row):
        return [row.get(c, np.nan) for c in bin_cols]

    def _draw(rows_subset, title_suffix, label_key, out_path):
        if not rows_subset:
            return
        fig, ax = plt.subplots(figsize=(10, 6))
        for i, r in enumerate(rows_subset):
            ax.plot(time_points, _vals(r),
                    color=cmap(i % 20), label=r[label_key], linewidth=1)
        ax.axvline(x=0,   color="grey",   linestyle="--", linewidth=1)
        ax.axvline(x=280, color="yellow", linestyle="--", linewidth=1)
        ax.axvline(x=300, color="grey",   linestyle="--", linewidth=1)
        ax.set_title(f"{title_suffix}: {original_stem}")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Speed (m/s)")
        ax.set_xticks(xtick_pos)
        ax.set_xticklabels(xtick_labels)
        handles, labels = ax.get_legend_handles_labels()
        ax.legend(handles[:20], labels[:20],
                  bbox_to_anchor=(1.05, 1), loc="upper left",
                  title="CS Trials")
        fig.tight_layout()
        fig.savefig(out_path, format="pdf", bbox_inches="tight")
        plt.close(fig)
        print(f"  [ok] Saved: {out_path}")

    _draw(animal_rows,   "combined CS+/- speed (Line Plot)",
          "cs_type", combined_dir / f"{original_stem}_combined_CS_speed_lineplot.pdf")
    _draw(cs_plus_rows,  "CS+ speed (Line Plot)",
          "cs_type", csplus_dir  / f"{original_stem}_CSplus_speed_lineplot.pdf")
    _draw(cs_minus_rows, "CS- speed (Line Plot)",
          "cs_type", csminus_dir / f"{original_stem}_CSminus_speed_lineplot.pdf")


# =============================================================================
# Pass 1 — day-level driver
# =============================================================================

def _process_day(day_dir: Path, meta, cfg: dict, report=None) -> list:
    """
    Process all CSVs in one session day folder.
    Writes per-animal CSVs and graphs.
    Returns all row dicts for this day (used by Pass 2).
    """
    suffix    = cfg["csv_suffix"]
    subfolder = cfg["speed_subfolder"]
    csvs      = sorted(
        day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
    if not csvs:
        return []

    pre_bins = cfg["speed_pre_bins"]
    post_bins = cfg["speed_post_bins"]
    bin_cols  = _bin_columns(pre_bins, post_bins)

    data_dir   = day_dir / "Analysis" / subfolder / "speed_data"
    graphs_dir = day_dir / "Analysis" / subfolder / "speed_graphs"
    data_dir.mkdir(parents=True, exist_ok=True)

    all_day_rows = []

    for csv_path in csvs:
        rows = _process_file(csv_path, meta, day_dir, cfg, report=report)
        if not rows:
            continue

        # Write per-animal CSV
        stem     = csv_path.stem
        out_csv  = data_dir / f"{stem}_CS_locked_speed.csv"
        meta_cols = ["animal_id", "behavior_id", "treatment_group", "cohort_id",
                     "test_date", "day", "context", "session_label",
                     "_day_folder", "_source_csv",
                     "file_name", "start_time_s", "cs_type"]
        out_cols = meta_cols + bin_cols
        pd.DataFrame(rows)[out_cols].to_csv(out_csv, index=False)
        print(f"  [ok] Per-animal CSV: {out_csv}")

        # Write per-animal figures
        _make_animal_figures(rows, stem, graphs_dir, cfg)

        all_day_rows.extend(rows)

    return all_day_rows


# =============================================================================
# Pass 2 — per-cohort Excel (all days within one BehaviorData folder)
# =============================================================================

def _collect_cohort_rows(bd: Path, cfg: dict) -> pd.DataFrame:
    """Read all per-animal CSVs produced by Pass 1 for one BehaviorData dir."""
    subfolder = cfg["speed_subfolder"]
    frames = []
    for day_dir in utils.find_session_dirs(bd):
        data_dir = day_dir / "Analysis" / subfolder / "speed_data"
        if not data_dir.exists():
            continue
        for csv_path in sorted(data_dir.glob("*_CS_locked_speed.csv")):
            frames.append(pd.read_csv(csv_path))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _write_group_excel(df_all: pd.DataFrame, out_path: Path, bin_cols: list):
    """
    Write one Excel workbook organised as:
      CS_Plus  — all CS+ trials
      CS_Minus — all CS- trials
      One sheet per unique CS type label (CS+_1, CS+_2, CS-_1, …)
    Mirrors the original notebook's Excel format exactly.
    """
    if df_all.empty:
        return

    output_cols = ["file_name", "animal_id", "treatment_group",
                   "start_time_s", "cs_type"] + bin_cols

    # Keep only columns that actually exist
    output_cols = [c for c in output_cols if c in df_all.columns]

    df_cs_plus  = df_all[df_all["cs_type"].str.contains(r"\+", na=False)].copy()
    df_cs_minus = df_all[df_all["cs_type"].str.contains(r"\-", na=False)].copy()

    df_cs_plus["_csn"]  = df_cs_plus["cs_type"].apply(_extract_cs_number)
    df_cs_minus["_csn"] = df_cs_minus["cs_type"].apply(_extract_cs_number)
    df_cs_plus  = df_cs_plus.sort_values("_csn").drop(columns="_csn")
    df_cs_minus = df_cs_minus.sort_values("_csn").drop(columns="_csn")

    unique_cs = sorted(df_all["cs_type"].dropna().unique(), key=_extract_cs_number)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_cs_plus[output_cols].to_excel(writer,  sheet_name="CS_Plus",  index=False)
        df_cs_minus[output_cols].to_excel(writer, sheet_name="CS_Minus", index=False)
        for cs_type in unique_cs:
            sub = df_all[df_all["cs_type"] == cs_type][output_cols]
            sheet = _sanitize_sheet(cs_type)
            sub.to_excel(writer, sheet_name=sheet, index=False)

    print(f"  [ok] Excel saved: {out_path}")


def _write_cohort_outputs(bd: Path, cfg: dict):
    """Pass 2: aggregate all days in one BehaviorData dir → per-group Excel."""
    subfolder = cfg["speed_subfolder"]
    bin_cols  = _bin_columns(cfg["speed_pre_bins"], cfg["speed_post_bins"])
    df_all    = _collect_cohort_rows(bd, cfg)
    if df_all.empty:
        print(f"  [warn] No speed data found for cohort in {bd.name}; skipping.")
        return

    out_dir = bd / "Analysis" / subfolder
    out_dir.mkdir(parents=True, exist_ok=True)

    for group in cfg["canonical_groups"]:
        df_grp = df_all[df_all["treatment_group"] == group]
        if df_grp.empty:
            continue
        out_path = out_dir / f"{group}_combined_output.xlsx"
        _write_group_excel(df_grp, out_path, bin_cols)


# =============================================================================
# Pass 3 — cross-cohort aggregation → ANALYSIS_OUTPUT_DIR
# =============================================================================

def _write_combined_outputs(cfg: dict):
    """
    Pass 3: combine per-group Excels across all BehaviorData dirs and write
    to cfg["analysis_out"] / subfolder.
    Mirrors the original combine_excel_across_fldr_Speed notebook exactly.
    """
    subfolder = cfg["speed_subfolder"]
    bin_cols  = _bin_columns(cfg["speed_pre_bins"], cfg["speed_post_bins"])
    out_dir   = cfg["analysis_out"] / subfolder
    out_dir.mkdir(parents=True, exist_ok=True)

    for group in cfg["canonical_groups"]:
        sheet_data = {}   # sheet_name → list of DataFrames

        for bd in cfg["behaviordata_dirs"]:
            excel_path = bd / "Analysis" / subfolder / f"{group}_combined_output.xlsx"
            if not excel_path.exists():
                print(f"  [warn] Missing cohort Excel: {excel_path}; skipping.")
                continue
            print(f"  Reading: {excel_path}")
            xls = pd.ExcelFile(excel_path)
            for sheet_name in xls.sheet_names:
                df = xls.parse(sheet_name)
                df["_source_cohort"] = bd.name
                sheet_data.setdefault(sheet_name, []).append(df)

        if not sheet_data:
            continue

        out_path = out_dir / f"{group}_combined_output.xlsx"
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            for sheet_name, dfs in sheet_data.items():
                combined = pd.concat(dfs, ignore_index=True)
                combined.to_excel(writer,
                                  sheet_name=_sanitize_sheet(sheet_name),
                                  index=False)
        print(f"  [ok] Cross-cohort Excel saved: {out_path}")


# =============================================================================
# Entry point
# =============================================================================

def run(cfg: dict, report=None):
    subfolder = cfg["speed_subfolder"]

    # Pass 1 — per file, per day
    print("  Pass 1: downsampling and extracting CS-locked speed windows...")
    for bd in cfg["behaviordata_dirs"]:
        meta = utils.load_metadata(bd)
        for day_dir in utils.find_session_dirs(bd):
            print(f"  Processing day: {day_dir.name}")
            _process_day(day_dir, meta, cfg, report=report)

    # Pass 2 — per-cohort Excel
    print("  Pass 2: aggregating within each cohort...")
    for bd in cfg["behaviordata_dirs"]:
        _write_cohort_outputs(bd, cfg)

    # Pass 3 — cross-cohort combined outputs
    print("  Pass 3: combining across cohorts...")
    _write_combined_outputs(cfg)

    print("  Speed analysis complete.")
