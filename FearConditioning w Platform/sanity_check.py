# -*- coding: utf-8 -*-
"""
sanity_check.py
---------------
Produces two sanity-check Excel workbooks per BehaviorData folder, written to:
    <BehaviorData>/Analysis/sanity_checks/

  sanity_check_tracking.xlsx
      Sheet "per_trial_IQR"   — per-trial tracking coverage and IQR outlier
                                flags on freeze_pct across animals.
      Sheet "per_session_IQR" — per-session (mean across trials) IQR outlier
                                flags on freeze_pct across animals.

  sanity_check_trial_windows.xlsx
      Sheet "CS+"      — trial window timing for every CS+ window.
      Sheet "CS-"      — trial window timing for every CS- window.
      Sheet "ITI"      — trial window timing for every ITI window.
      Sheet "Outliers" — rows whose window start/end/duration deviates from
                         the within-cohort mode for that trial type × day ×
                         trial index combination.

Both workbooks are produced from the raw AnyMaze CSVs so that tracking
coverage (NaN rate in the freezing signal) can be assessed directly.
"""

from pathlib import Path

import numpy as np
import pandas as pd

import utils


TRACKING_THRESHOLD = 0.9   # minimum fraction of non-NaN samples to be "ok"


# ── Raw data collection ─────────────────────────────────────────────────────

def _collect_raw(behaviordata: Path, meta: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Read every raw AnyMaze CSV in every session day folder for one
    BehaviorData directory. Returns a long DataFrame with one row per
    trial window per animal.
    """
    suffix   = cfg["csv_suffix"]
    all_rows = []

    for day_dir in utils.find_session_dirs(behaviordata):
        csvs = sorted(
            day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv")
        )
        for csv_path in csvs:
            test_date, behavior_id, row, exclusion_reason = utils.metadata_for_csv(csv_path, meta)
            if exclusion_reason is not None:
                continue

            animal_id = row.get("animal_id", behavior_id)
            treatment = utils.normalize_treatment(
                row.get("treatment_group", "Unknown"), cfg["treatment_lookup"]
            )
            sex       = utils.normalize_sex(row.get("sex", None))
            cohort_id = row.get("cohort_id", None)
            litter_id = row.get("litter_id", None)

            try:
                df_header = utils.load_csv_header(csv_path)
                time_col   = utils.find_time_col(df_header, cfg)
                freeze_col = utils.find_freeze_col(df_header, cfg)
                source_cols = utils.unique_existing_columns(
                    df_header,
                    [time_col, freeze_col]
                    + utils.trial_detection_source_columns(df_header, cfg),
                )
            except ValueError:
                continue

            # Sanity checks only need freezing coverage and trial windows, so
            # avoid parsing unrelated AnyMaze columns during optional QA runs.
            df = utils.load_csv(csv_path, usecols=source_cols)
            df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)

            try:
                trials, itis, _ = utils.detect_trials(df, time_col, cfg)
            except Exception:
                continue

            windows = trials + itis
            if not windows:
                continue

            day, context, session = utils.parse_folder_bits(day_dir.name)
            t = df[time_col].astype(float).to_numpy()
            x = df[freeze_col].to_numpy()   # keep NaNs for tracking coverage

            for w in windows:
                mask    = (t >= w["start"]) & (t < w["end"])
                n_total = int(mask.sum())
                n_valid = int(np.sum(~np.isnan(x[mask].astype(float))))
                coverage = n_valid / n_total if n_total > 0 else 0.0
                freeze_pct = float(np.nanmean(x[mask].astype(float))) * 100 \
                    if n_valid > 0 else np.nan
                dur = max(0.0, w["end"] - w["start"])

                all_rows.append({
                    "animal_id":        animal_id,
                    "behavior_id":      behavior_id,
                    "treatment_group":  treatment,
                    "sex":              sex,
                    "cohort_id":        cohort_id,
                    "litter_id":        litter_id,
                    "test_date":        test_date,
                    "day":              day,
                    "context":          context,
                    "session_label":    session,
                    "_day_folder":      day_dir.name,
                    "_source_csv":      csv_path.name,
                    "trial_type":       w["type"],
                    "trial_index":      w["trial_index"],
                    "window_start_s":   round(w["start"]),
                    "window_end_s":     round(w["end"]),
                    "window_len_s":     round(dur),
                    "freeze_pct":       freeze_pct,
                    "tracking_coverage": coverage,
                    "tracking_ok":      coverage >= TRACKING_THRESHOLD,
                })

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


# ── IQR helpers ─────────────────────────────────────────────────────────────

def _iqr_flag(series: pd.Series) -> pd.Series:
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr    = q3 - q1
    return (series < q1 - 1.5 * iqr) | (series > q3 + 1.5 * iqr)


# ── Sanity check 1 - tracking coverage + freeze_pct IQR ─────────────────────

def _make_tracking_workbook(df: pd.DataFrame, out_path: Path):
    """
    Sheet 1 — per_trial_IQR:
        One row per animal × day × trial type × trial index.
        Flags animals whose freeze_pct is an IQR outlier within that group.

    Sheet 2 — per_session_IQR:
        One row per animal × day × trial type (mean across trials).
        Flags animals whose session-mean freeze_pct is an IQR outlier.
    """
    # --- Per-trial IQR ---
    per_trial = df.copy()
    per_trial["freeze_outlier_trial"] = (
        per_trial.groupby(["_day_folder", "trial_type", "trial_index"])["freeze_pct"]
        .transform(_iqr_flag)
    )

    # --- Per-session IQR ---
    per_session = (
        df.groupby(["_day_folder", "trial_type", "animal_id",
                    "behavior_id", "treatment_group", "cohort_id"], as_index=False)
        ["freeze_pct"].mean()
        .rename(columns={"freeze_pct": "mean_freeze_pct"})
    )
    per_session["freeze_outlier_session"] = (
        per_session.groupby(["_day_folder", "trial_type"])["mean_freeze_pct"]
        .transform(_iqr_flag)
    )

    n_trial_out   = int(per_trial["freeze_outlier_trial"].sum())
    n_session_out = int(per_session["freeze_outlier_session"].sum())
    print(f"    Per-trial outliers:   {n_trial_out}")
    print(f"    Per-session outliers: {n_session_out}")

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        per_trial.to_excel(writer,   sheet_name="per_trial_IQR",   index=False)
        per_session.to_excel(writer, sheet_name="per_session_IQR", index=False)

    print(f"    [ok] Saved: {out_path}")


# ── Sanity check 2 - trial window timing consistency ────────────────────────

def _make_windows_workbook(df: pd.DataFrame, out_path: Path):
    """
    For each trial type × day × trial index, checks whether window
    start / end / duration are consistent across animals.
    Rows that deviate from the within-group mode are flagged as outliers.
    """
    timing_cols = ["window_start_s", "window_end_s", "window_len_s"]
    group_cols  = ["_day_folder", "trial_type", "trial_index"]

    outliers = []
    for _, g in df.groupby(group_cols, dropna=False):
        if len(g) < 2:
            continue
        consensus = {}
        for col in timing_cols:
            if col not in g.columns:
                continue
            vals = g[col].dropna()
            mode = vals.mode()
            consensus[col] = mode.iloc[0] if not mode.empty else (
                vals.iloc[0] if not vals.empty else pd.NA
            )

        mismatch = pd.Series(False, index=g.index)
        for col, val in consensus.items():
            mismatch |= g[col].ne(val)

        bad = g[mismatch].copy()
        if not bad.empty:
            for col, val in consensus.items():
                bad[f"mode_{col}"] = val
            bad["mismatch_fields"] = bad.apply(
                lambda row: ", ".join(
                    col for col, val in consensus.items()
                    if pd.notna(val) and row[col] != val
                ), axis=1
            )
            outliers.append(bad)

    outliers_df = (
        pd.concat(outliers, ignore_index=True) if outliers
        else pd.DataFrame()
    )

    keep = [c for c in [
        "animal_id", "behavior_id", "cohort_id", "litter_id",
        "treatment_group", "sex", "test_date", "day", "context",
        "session_label", "_day_folder", "_source_csv",
        "trial_type", "trial_index",
        "window_start_s", "window_end_s", "window_len_s",
    ] if c in df.columns]

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for trial, sheet in [("CS+", "CSplus"), ("CS-", "CSminus"), ("ITI", "ITI")]:
            df[df["trial_type"] == trial][keep].to_excel(
                writer, sheet_name=sheet, index=False
            )
        if not outliers_df.empty:
            outliers_df.to_excel(writer, sheet_name="Outliers", index=False)
        else:
            pd.DataFrame({"note": ["No timing outliers detected."]}).to_excel(
                writer, sheet_name="Outliers", index=False
            )

    print(f"    [ok] Saved: {out_path}")


# ── Entry point ─────────────────────────────────────────────────────────────

def run(cfg: dict):
    for bd in cfg["behaviordata_dirs"]:
        print(f"\n  Sanity checks for: {bd.name}")
        meta    = utils.load_metadata(bd)
        df      = _collect_raw(bd, meta, cfg)

        if df.empty:
            print(f"  [warn] No raw data found in {bd.name}; skipping.")
            continue

        out_dir = bd / "Analysis" / "sanity_checks"
        out_dir.mkdir(parents=True, exist_ok=True)

        print("    Running tracking + IQR checks...")
        _make_tracking_workbook(df, out_dir / "sanity_check_tracking.xlsx")

        print("    Running trial window consistency checks...")
        _make_windows_workbook(df, out_dir / "sanity_check_trial_windows.xlsx")

    print("  Sanity checks complete.")
