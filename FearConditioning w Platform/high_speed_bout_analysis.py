# -*- coding: utf-8 -*-
"""
high_speed_bout_analysis.py
---------------------------
Detects darting/high-speed movement bouts from the HMM movement parquet.

The HMM parquet provides one continuous 100 ms movement trace per subject and
session. Raw AnyMaze CSVs are still used only to recover CS+/CS-/ITI window
timing, so the output can mirror freezing bout summaries.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd

import utils
import run_report as rr
from freezing_analysis import (
    _tiled_individual,
    _tiled_group_means,
    _tiled_by_sex,
    _prism_export,
)


MOTION_COL = "displacement_per_100ms"
DELTA_COL = "abs_delta_displacement_per_100ms"
OLD_MOTION_COL = "speed"
OLD_DELTA_COL = "acceleration"


def _as_text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _norm(value) -> str:
    return _as_text(value).lower()


def _norm_behavior(value) -> str:
    normed = utils.normalize_behavior_id_for_lookup(_as_text(value))
    return normed or _norm(value)


def _session_key(cohort_id, behavior_id, day, context, session) -> tuple:
    return (
        _norm(cohort_id),
        _norm_behavior(behavior_id),
        _norm(day),
        _norm(context),
        _norm(session),
    )


def _fallback_key(cohort_id, behavior_id, day, context, session) -> tuple:
    return _session_key(cohort_id, behavior_id, day, context, session)[1:]


def _resolve_motion_columns(df: pd.DataFrame) -> tuple:
    warnings = []
    if MOTION_COL in df.columns:
        motion_col = MOTION_COL
    elif OLD_MOTION_COL in df.columns:
        motion_col = OLD_MOTION_COL
        warnings.append(
            "Using old HMM parquet column 'speed'. Rerun HMM_analysis.py to "
            "write 'displacement_per_100ms'."
        )
    else:
        raise ValueError(
            f"HMM parquet must contain '{MOTION_COL}' "
            f"(or legacy '{OLD_MOTION_COL}'). Columns present: {df.columns.tolist()}"
        )

    if DELTA_COL in df.columns:
        delta_col = DELTA_COL
    elif OLD_DELTA_COL in df.columns:
        delta_col = OLD_DELTA_COL
        warnings.append(
            "Using old HMM parquet column 'acceleration'. Rerun HMM_analysis.py "
            "to write 'abs_delta_displacement_per_100ms'."
        )
    else:
        delta_col = None

    return motion_col, delta_col, warnings


def _load_motion_parquet(cfg: dict) -> tuple:
    parquet_setting = cfg.get("high_speed_motion_parquet")
    if not parquet_setting:
        raise FileNotFoundError(
            "HMM_INPUT_PARQUET is not set in runner.py. Run standalone "
            "HMM_analysis.py first, then copy its OUTPUT_CSV path into "
            "HMM_INPUT_PARQUET and change the extension from .csv to .parquet."
        )

    parquet_path = Path(parquet_setting)
    if not parquet_path.exists():
        raise FileNotFoundError(
            "High-speed bout analysis needs the HMM movement parquet. "
            f"Not found: {parquet_path}. Run HMM_analysis.py first or update "
            "HMM_INPUT_PARQUET in runner.py."
        )

    try:
        df = pd.read_parquet(parquet_path)
    except ImportError as e:
        raise RuntimeError(
            "Reading the HMM parquet requires pyarrow or fastparquet. "
            "Install one of them, then rerun the pipeline."
        ) from e

    required = ["behavior_id", "cohort_id", "day", "context", "session", "time_s"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(
            f"HMM parquet is missing required columns: {missing}. "
            "Rerun HMM_analysis.py with the current version."
        )

    motion_col, delta_col, warnings = _resolve_motion_columns(df)
    df = df.dropna(subset=["time_s"]).copy()
    df["time_s"] = pd.to_numeric(df["time_s"], errors="coerce")
    df[motion_col] = pd.to_numeric(df[motion_col], errors="coerce")
    if delta_col is not None:
        df[delta_col] = pd.to_numeric(df[delta_col], errors="coerce")
    df = df.dropna(subset=["time_s"])

    return df, motion_col, delta_col, warnings, parquet_path


def _build_motion_session_maps(df: pd.DataFrame):
    work = df.copy()
    work["_key_cohort"] = work["cohort_id"].map(_norm)
    work["_key_behavior"] = work["behavior_id"].map(_norm_behavior)
    work["_key_day"] = work["day"].map(_norm)
    work["_key_context"] = work["context"].map(_norm)
    work["_key_session"] = work["session"].map(_norm)

    key_cols = [
        "_key_cohort", "_key_behavior", "_key_day", "_key_context",
        "_key_session",
    ]
    session_map = {}
    fallback_map = {}
    duplicate_fallbacks = set()

    for key, group in work.groupby(key_cols, dropna=False, sort=False):
        g = group.sort_values("time_s").reset_index(drop=True)
        session_map[key] = g
        fb = key[1:]
        if fb in fallback_map:
            fallback_map[fb] = None
            duplicate_fallbacks.add(fb)
        elif fb not in duplicate_fallbacks:
            fallback_map[fb] = g

    return session_map, fallback_map


def _get_motion_session(session_map, fallback_map, cohort_id, behavior_id,
                        day, context, session):
    key = _session_key(cohort_id, behavior_id, day, context, session)
    if key in session_map:
        return session_map[key]
    return fallback_map.get(key[1:])


def _trial_signal_columns(df_header: pd.DataFrame, cfg: dict) -> list:
    mode = str(cfg.get("cs_detection_mode", "auto")).strip().lower()

    def _tone_status_columns():
        return list(utils.find_tone_status_cols(
            df_header,
            cfg.get("tone_status_col_csplus", "cs plus tone status"),
            cfg.get("tone_status_col_csminus", "cs minus tone status"),
            cfg,
        ))

    def _ttl_columns():
        return [col for col in utils.find_ttl_cols(df_header, cfg) if col is not None]

    if mode == "tone_status":
        return _tone_status_columns()
    if mode == "ttl":
        return _ttl_columns()

    try:
        return _tone_status_columns()
    except ValueError:
        return _ttl_columns()


def _load_trial_frame(csv_path: Path, cfg: dict) -> tuple:
    df_header = utils.load_csv_header(csv_path)
    time_col = utils.find_time_col(df_header, cfg)
    trial_cols = _trial_signal_columns(df_header, cfg)
    usecols = utils.unique_existing_columns(df_header, [time_col] + trial_cols)
    df = utils.load_csv(csv_path, usecols=usecols)
    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    if df.empty:
        raise ValueError("no valid time samples")
    df[time_col] = df[time_col].astype(float) - float(df[time_col].astype(float).iloc[0])
    trials, itis, source = utils.detect_trials(df, time_col, cfg)
    return df, time_col, source, trials, itis


def _detect_bouts(t, state, start, end):
    t = np.asarray(t, float)
    x = (np.asarray(state, float) > 0).astype(int)
    if len(t) == 0 or start >= end:
        return []

    bouts, bout_start = [], None
    idx0 = max(0, np.searchsorted(t, start, side="right") - 1)
    prev = x[idx0]
    if prev == 1:
        bout_start = start

    for i in range(len(t)):
        seg_s = t[i]
        seg_e = t[i + 1] if i + 1 < len(t) else t[i] + (
            t[i] - t[i - 1] if i > 0 else 0.1)
        if seg_e <= start:
            continue
        if seg_s >= end:
            break
        a, b = max(seg_s, start), min(seg_e, end)
        if b <= a:
            continue
        current = x[i]
        if prev == 0 and current == 1:
            bout_start = a
        elif prev == 1 and current == 0 and bout_start is not None:
            if a > bout_start:
                bouts.append({"start": bout_start, "end": a})
            bout_start = None
        prev = current

    if prev == 1 and bout_start is not None and end > bout_start:
        bouts.append({"start": bout_start, "end": end})
    return bouts


def _merge_short_gaps(bouts, max_gap_s: float):
    if not bouts or max_gap_s <= 0:
        return bouts
    merged = [dict(bouts[0])]
    for bout in bouts[1:]:
        gap = bout["start"] - merged[-1]["end"]
        if gap <= max_gap_s:
            merged[-1]["end"] = bout["end"]
        else:
            merged.append(dict(bout))
    return merged


def _filter_short_bouts(bouts, min_dur_s: float):
    if min_dur_s <= 0:
        return bouts
    return [b for b in bouts if b["end"] - b["start"] >= min_dur_s]


def _series_stats(t, values, start, end):
    mask = (t >= start) & (t < end)
    vals = np.asarray(values, float)[mask]
    vals = vals[~np.isnan(vals)]
    if len(vals) == 0:
        return np.nan, np.nan, 0
    return float(np.mean(vals)), float(np.max(vals)), int(len(vals))


def _summarize_file(csv_path, meta, day_dir, cfg, session_map, fallback_map,
                    motion_col, delta_col, motion_warnings, parquet_path):
    report_data = {"subjects": {}, "exclusions": {}}
    subject_key = csv_path.name
    test_date, behavior_id, row, exclusion_reason = utils.metadata_for_csv(csv_path, meta)
    if exclusion_reason is not None:
        report_data["exclusions"][subject_key] = exclusion_reason
        return [], [], report_data

    animal_id = row.get("animal_id", behavior_id)
    treatment = utils.normalize_treatment(
        row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    sex = utils.normalize_sex(row.get("sex", None))
    litter_id = row.get("litter_id", None)
    cohort_id = row.get("cohort_id", None)
    day, context, session = utils.parse_folder_bits(day_dir.name)

    motion = _get_motion_session(
        session_map, fallback_map, cohort_id, behavior_id, day, context, session)
    if motion is None or motion.empty:
        report_data["exclusions"][subject_key] = (
            "no matching session in HMM movement parquet"
        )
        return [], [], report_data

    try:
        _, time_col, cs_source, trials, itis = _load_trial_frame(csv_path, cfg)
    except ValueError as e:
        report_data["exclusions"][subject_key] = str(e)
        return [], [], report_data

    windows = trials + itis
    if not windows:
        report_data["exclusions"][subject_key] = "no trials detected"
        return [], [], report_data

    threshold = float(cfg["high_speed_min_displacement_per_100ms"])
    min_dur_s = float(cfg.get("high_speed_min_bout_s", 0.0))
    max_gap_s = float(cfg.get("high_speed_max_gap_s", 0.0))

    t = motion["time_s"].astype(float).to_numpy()
    movement = motion[motion_col].astype(float).to_numpy()
    delta = (motion[delta_col].astype(float).to_numpy()
             if delta_col is not None else np.full(len(motion), np.nan))
    high_state = np.where(np.isfinite(movement) & (movement >= threshold), 1, 0)

    warnings = list(motion_warnings)
    report_data["subjects"][subject_key] = {
        "columns_used": {
            "time": time_col,
            "CS_detection": cs_source,
            "motion_parquet": str(parquet_path),
            "movement": motion_col,
            "movement_change": delta_col if delta_col else "not available",
            "threshold": threshold,
        },
        "warnings": warnings,
        "skipped": [],
    }

    shared_base = dict(
        animal_id=animal_id,
        behavior_id=behavior_id,
        treatment_group=treatment,
        sex=sex,
        litter_id=litter_id,
        cohort_id=cohort_id,
        test_date=test_date,
        day=day,
        context=context,
        session_label=session,
        source_behaviordata=str(day_dir.parent),
        source_behaviordata_name=day_dir.parent.name,
        _day_folder=day_dir.name,
        _source_csv=csv_path.name,
        file_name=csv_path.name,
        movement_source_col=motion_col,
        movement_change_source_col=delta_col if delta_col else "",
        high_speed_threshold=threshold,
        min_bout_s=min_dur_s,
        max_gap_s=max_gap_s,
    )

    summary_rows = []
    bout_rows = []

    for w in windows:
        start = float(w["start"])
        end = float(w["end"])
        dur = max(0.0, end - start)
        high_s = utils.integrate_binary(t, high_state, start, end) if dur > 0 else 0.0
        mean_move, peak_move, n_bins = _series_stats(t, movement, start, end)
        mean_delta, peak_delta, _ = _series_stats(t, delta, start, end)

        raw_bouts = _detect_bouts(t, high_state, start, end)
        bouts = _filter_short_bouts(
            _merge_short_gaps(raw_bouts, max_gap_s),
            min_dur_s,
        )

        shared = dict(
            **shared_base,
            trial_type=w["type"],
            trial_index=w["trial_index"],
            window_start_s=start,
            window_end_s=end,
            window_len_s=dur,
        )

        summary_rows.append({
            **shared,
            "high_speed_time_s": high_s,
            "high_speed_pct": 100.0 * high_s / dur if dur > 0 else 0.0,
            "high_speed_bout_count": len(bouts),
            "mean_displacement_per_100ms": mean_move,
            "peak_displacement_per_100ms": peak_move,
            "mean_abs_delta_displacement_per_100ms": mean_delta,
            "peak_abs_delta_displacement_per_100ms": peak_delta,
            "n_motion_bins": n_bins,
        })

        if not bouts:
            bout_rows.append({
                **shared,
                "bout_id_in_trial": 0,
                "bout_start_s": np.nan,
                "bout_end_s": np.nan,
                "bout_dur_s": np.nan,
                "bout_mean_displacement_per_100ms": np.nan,
                "bout_peak_displacement_per_100ms": np.nan,
                "bout_mean_abs_delta_displacement_per_100ms": np.nan,
                "bout_peak_abs_delta_displacement_per_100ms": np.nan,
            })
            continue

        for j, bout in enumerate(bouts, 1):
            b_start = float(bout["start"])
            b_end = float(bout["end"])
            b_mean, b_peak, _ = _series_stats(t, movement, b_start, b_end)
            bd_mean, bd_peak, _ = _series_stats(t, delta, b_start, b_end)
            bout_rows.append({
                **shared,
                "bout_id_in_trial": j,
                "bout_start_s": b_start,
                "bout_end_s": b_end,
                "bout_dur_s": b_end - b_start,
                "bout_mean_displacement_per_100ms": b_mean,
                "bout_peak_displacement_per_100ms": b_peak,
                "bout_mean_abs_delta_displacement_per_100ms": bd_mean,
                "bout_peak_abs_delta_displacement_per_100ms": bd_peak,
            })

    return summary_rows, bout_rows, report_data


def _collect_all(cfg, report=None):
    motion_df, motion_col, delta_col, motion_warnings, parquet_path = _load_motion_parquet(cfg)
    session_map, fallback_map = _build_motion_session_maps(motion_df)

    all_summary_rows = []
    all_bout_rows = []

    for bd in cfg["behaviordata_dirs"]:
        meta = utils.load_metadata(bd)
        suffix = cfg["csv_suffix"]
        for day_dir in utils.find_session_dirs(bd):
            csvs = sorted(
                day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
            for csv_path in csvs:
                summary_rows, bout_rows, report_data = _summarize_file(
                    csv_path, meta, day_dir, cfg, session_map, fallback_map,
                    motion_col, delta_col, motion_warnings, parquet_path)
                all_summary_rows.extend(summary_rows)
                all_bout_rows.extend(bout_rows)

                if report is not None:
                    for key, info in report_data["subjects"].items():
                        rr.record_subject(
                            report, "high_speed_bouts", key,
                            columns_used=info["columns_used"],
                            warnings=info["warnings"],
                            skipped=info["skipped"],
                        )
                    for key, reason in report_data["exclusions"].items():
                        rr.record_exclusion(report, "high_speed_bouts", key, reason)

    summary_df = pd.DataFrame(all_summary_rows) if all_summary_rows else pd.DataFrame()
    bout_df = pd.DataFrame(all_bout_rows) if all_bout_rows else pd.DataFrame()
    return summary_df, bout_df


def _write_outputs(summary_df, bout_df, out_dir, cfg, fname_tag):
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_df["_trial_type"] = summary_df["trial_type"].apply(utils.normalize_trial_type)
    summary_df = utils.filter_trials(
        summary_df, cfg["cs_trial_cap"], cfg["iti_trial_cap"])
    summary_df.to_csv(out_dir / f"{fname_tag}_all_days_concat.csv", index=False)

    _tiled_individual(
        summary_df, "high_speed_pct", "% high speed",
        "High-Speed Movement", out_dir, cfg, fname_tag, ylim=(0, 100))
    _tiled_group_means(
        summary_df, "high_speed_pct", "% high speed",
        "High-Speed Movement", out_dir, cfg, fname_tag, ylim=(0, 100))
    if cfg.get("high_speed_by_sex", False):
        _tiled_by_sex(
            summary_df, "high_speed_pct", "% high speed",
            "High-Speed Movement", out_dir, cfg, fname_tag, ylim=(0, 100))

    bout_dir = out_dir / "high_speed_bouts"
    bout_dir.mkdir(exist_ok=True)
    if not bout_df.empty:
        bout_df["_trial_type"] = bout_df["trial_type"].apply(utils.normalize_trial_type)
        bout_df.to_csv(
            bout_dir / f"{fname_tag}_bouts_all_days_concat.csv", index=False)

    count_cols = [
        "animal_id", "behavior_id", "treatment_group", "sex", "litter_id",
        "cohort_id", "_day_folder", "_source_csv", "_trial_type", "trial_index",
        "high_speed_bout_count",
    ]
    counts_df = summary_df[[c for c in count_cols if c in summary_df.columns]].copy()
    counts_df = counts_df.rename(columns={"high_speed_bout_count": "bout_count"})
    counts_df = utils.filter_trials(counts_df, cfg["cs_trial_cap"], cfg["iti_trial_cap"])
    _tiled_individual(
        counts_df, "bout_count", "Bout count",
        "High-Speed Bouts", bout_dir, cfg, f"{fname_tag}_bouts", ylim=None)
    _tiled_group_means(
        counts_df, "bout_count", "Bout count",
        "High-Speed Bouts", bout_dir, cfg, f"{fname_tag}_bouts", ylim=None)
    if cfg.get("high_speed_by_sex", False):
        _tiled_by_sex(
            counts_df, "bout_count", "Bout count",
            "High-Speed Bouts", bout_dir, cfg, f"{fname_tag}_bouts", ylim=None)

    if cfg["prism_export"]:
        _prism_export(summary_df, "high_speed_pct", out_dir, fname_tag)
        _prism_export(counts_df, "bout_count", bout_dir, f"{fname_tag}_bouts")


def _find_behaviordata_for_cohort(cohort_id, behaviordata_dirs):
    for bd in behaviordata_dirs:
        meta = utils.load_metadata(bd)
        if meta is not None and "cohort_id" in meta.columns:
            if str(cohort_id) in meta["cohort_id"].astype(str).values:
                return bd
    return None


def run(cfg, report=None):
    threshold = cfg.get("high_speed_min_displacement_per_100ms", None)
    threshold = float(threshold) if threshold is not None else np.nan
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError(
            "Set HIGH_SPEED_MIN_DISPLACEMENT_PER_100MS in runner.py to a "
            "positive threshold before running high-speed bout analysis."
        )

    print("  Loading HMM movement parquet and detecting high-speed bouts...")
    try:
        summary_df, bout_df = _collect_all(cfg, report=report)
    except FileNotFoundError as e:
        warning = str(e)
        print(f"  [warn] {warning}")
        print("  Skipping high-speed bout analysis.")
        if report is not None:
            rr.record_overview(report, "high_speed_bouts", "Skipped", warning)
        return

    if summary_df.empty:
        print("  [warn] No high-speed bout data found. Skipping figures.")
        return

    print("  Writing cohort high-speed bout outputs...")
    if "cohort_id" in summary_df.columns:
        for cohort_id, cohort_summary in summary_df.groupby("cohort_id", dropna=False):
            if pd.isna(cohort_id):
                continue
            cohort_bouts = (bout_df[bout_df["cohort_id"] == cohort_id]
                            if not bout_df.empty and "cohort_id" in bout_df.columns
                            else pd.DataFrame())
            cohort_bd = _find_behaviordata_for_cohort(
                cohort_id, cfg["behaviordata_dirs"])
            if cohort_bd is None:
                print(
                    f"  [warn] Cannot locate BehaviorData folder for cohort "
                    f"'{cohort_id}'; skipping."
                )
                continue
            cohort_out = cohort_bd / "Analysis" / cfg["high_speed_subfolder"]
            _write_outputs(
                cohort_summary.copy(), cohort_bouts.copy(), cohort_out, cfg,
                f"high_speed_{cohort_id}")

    print("  Writing combined high-speed bout outputs...")
    combined_out = cfg["analysis_out"] / cfg["high_speed_subfolder"]
    _write_outputs(summary_df.copy(), bout_df.copy(), combined_out, cfg, "high_speed")

    print("  High-speed bout analysis complete.")
