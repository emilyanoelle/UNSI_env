# -*- coding: utf-8 -*-
"""
event_raster_analysis.py
------------------------
Pass-1-only event and behavioral raster plots.

For each session/day folder, this module writes one tiled SVG with one subject
per row. Each row contains aligned tracks for:
    Freezing, platform occupancy, CS+ tone, CS- tone, and US/shock.

The extraction layer returns tidy interval rows so a later cache such as
event_intervals.parquet can be added without changing the plotting code.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

import utils
import run_report as rr


TRACKS = [
    ("freezing", "Freezing", "#4D4D4D"),
    ("platform", "Platform", "#1B9E77"),
    ("cs_plus", "CS+", "#D95F02"),
    ("cs_minus", "CS-", "#377EB8"),
    ("us", "US", "#E41A1C"),
]


def _binary_intervals(t, x, start=None, end=None):
    """
    Convert a sampled binary step-function signal into active [start, end)
    intervals. The last sample is extended by the median positive frame delta.
    """
    t = np.asarray(t, dtype=float)
    x = np.asarray(x, dtype=float)
    valid = np.isfinite(t)
    t = t[valid]
    x = x[valid]
    if len(t) == 0:
        return []

    order = np.argsort(t)
    t = t[order]
    active = (x[order] > 0)
    if not active.any():
        return []

    dt = np.diff(t)
    positive_dt = dt[dt > 0]
    last_dt = float(np.median(positive_dt)) if len(positive_dt) else 0.1
    segment_end = np.append(t[1:], t[-1] + last_dt)

    starts = np.where(active & ~np.append(False, active[:-1]))[0]
    stops = np.where(active & ~np.append(active[1:], False))[0]

    intervals = []
    for si, ei in zip(starts, stops):
        s = float(t[si])
        e = float(segment_end[ei])
        if start is not None:
            s = max(s, float(start))
        if end is not None:
            e = min(e, float(end))
        if e > s:
            intervals.append((s, e))
    return intervals


def _append_interval(rows, subject, event_type, event_label, start_s, end_s,
                     source_col=None, source_method=None):
    if pd.isna(start_s) or pd.isna(end_s) or float(end_s) <= float(start_s):
        return
    rows.append({
        **subject,
        "event_type": event_type,
        "event_label": event_label,
        "event_index": None,
        "start_s": float(start_s),
        "end_s": float(end_s),
        "duration_s": float(end_s) - float(start_s),
        "source_col": source_col,
        "source_method": source_method,
    })


def _number_events(rows):
    counts = {}
    for row in rows:
        key = (row["_source_csv"], row["event_type"])
        counts[key] = counts.get(key, 0) + 1
        row["event_index"] = counts[key]
    return rows


def _subject_label(subject):
    bits = [str(subject.get("animal_id", subject.get("behavior_id", "")))]
    treatment = subject.get("treatment_group")
    sex = subject.get("sex")
    if treatment and treatment != "Unknown":
        bits.append(str(treatment))
    if sex and sex != "Unknown":
        bits.append(str(sex))
    return " | ".join(bits)


def _metadata_for_file(csv_path, meta, cfg):
    test_date, behavior_id, row, exclusion_reason = utils.metadata_for_csv(csv_path, meta)
    if exclusion_reason is not None:
        return None, exclusion_reason

    animal_id = row.get("animal_id", behavior_id)
    treatment = utils.normalize_treatment(
        row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    sex = utils.normalize_sex(row.get("sex", None))
    litter_id = row.get("litter_id", None)
    cohort_id = row.get("cohort_id", None)

    return (test_date, behavior_id, animal_id, treatment, sex, litter_id, cohort_id), None


def _optional_signal_column(df, finder, label, warnings):
    try:
        return finder(df)
    except ValueError as exc:
        warnings.append(f"{label} unavailable: {exc}")
        return None


def _trial_intervals(df, time_col, cfg, warnings):
    try:
        trials, _, cs_source = utils.detect_trials(df, time_col, cfg)
        return trials, cs_source
    except ValueError as exc:
        warnings.append(f"CS tone tracks unavailable: {exc}")
        return [], None


def _ttl_us_intervals(df, time_col, cfg, warnings):
    try:
        us_on, us_off = utils.find_us_cols(df, cfg)
    except ValueError as exc:
        warnings.append(f"US TTL tracks unavailable: {exc}")
        return [], None, None

    if us_on is None:
        return [], None, None

    rows = []
    for ix in utils.rising_edges(df[us_on]):
        start = float(df.iloc[ix][time_col])
        end = utils.get_off_time(
            df, time_col, us_off, start, cfg.get("us_duration_s", 2.0))
        if end > start:
            rows.append((start, end))
    source = f"TTL ({us_on}" + (f", {us_off})" if us_off else ")")
    return rows, us_on, source


def _shocker_us_intervals(df, time_col, cfg, warnings):
    try:
        shocker_col = utils.find_shocker_col(df, cfg)
    except ValueError as exc:
        warnings.append(f"US shocker track unavailable: {exc}")
        return [], None, None

    if shocker_col is None:
        return [], None, None

    t = df[time_col].astype(float).to_numpy()
    x = df[shocker_col].fillna(0).astype(float).to_numpy()
    return _binary_intervals(t, x), shocker_col, f"shocker_active ({shocker_col})"


def _derived_us_intervals(trials, cfg):
    us_duration = float(cfg.get("us_duration_s", 2.0))
    rows = []
    for tr in trials:
        if tr.get("type") != "CS+":
            continue
        end = float(tr["end"])
        start = max(float(tr["start"]), end - us_duration)
        if end > start:
            rows.append((start, end))
    return rows


def _us_intervals(df, time_col, trials, cfg, warnings):
    """
    Return US intervals using the most useful available source.
    Respect USE_SHOCKER_COLUMN preference, but fall back gracefully.
    """
    source_order = (
        ("shocker", "ttl") if cfg.get("use_shocker_column") else ("ttl", "shocker")
    )

    empty_raw_sources = []
    empty_raw_col = None
    for source in source_order:
        if source == "ttl":
            intervals, source_col, source_method = _ttl_us_intervals(
                df, time_col, cfg, warnings)
        else:
            intervals, source_col, source_method = _shocker_us_intervals(
                df, time_col, cfg, warnings)
        if intervals:
            return intervals, source_col, source_method
        if source_method is not None:
            empty_raw_sources.append(source_method)
            empty_raw_col = empty_raw_col or source_col

    if empty_raw_sources:
        source_method = "no active intervals (" + "; ".join(empty_raw_sources) + ")"
        warnings.append("US raw source found but no active intervals")
        return [], empty_raw_col, source_method

    intervals = _derived_us_intervals(trials, cfg)
    if intervals:
        warnings.append(
            "US track derived from the last "
            f"{float(cfg.get('us_duration_s', 2.0)):.1f}s of each CS+ trial")
        return intervals, None, "derived_last_csplus"

    warnings.append("US track unavailable: no shocker, US TTL, or CS+ trials found")
    return [], None, None


def _process_file(csv_path: Path, meta, day_dir: Path, cfg: dict):
    report_data = {"subjects": {}, "exclusions": {}}
    warnings = []
    subject_key = csv_path.name

    metadata, exclusion_reason = _metadata_for_file(csv_path, meta, cfg)
    if exclusion_reason is not None:
        report_data["exclusions"][subject_key] = exclusion_reason
        return None, [], report_data
    test_date, behavior_id, animal_id, treatment, sex, litter_id, cohort_id = metadata

    try:
        df_header = utils.load_csv_header(csv_path)
    except Exception as exc:
        report_data["exclusions"][subject_key] = f"could not read CSV: {exc}"
        return None, [], report_data

    try:
        time_col = utils.find_time_col(df_header, cfg)
    except ValueError as exc:
        report_data["exclusions"][subject_key] = str(exc)
        return None, [], report_data

    source_cols = [time_col]
    for label, finder in (
        ("freezing", lambda d: utils.find_freeze_col(d, cfg)),
        ("platform", lambda d: utils.find_platform_col(d, cfg)),
        ("US shocker track", lambda d: utils.find_shocker_col(d, cfg)),
    ):
        try:
            source_cols.append(finder(df_header))
        except ValueError as exc:
            warnings.append(f"{label} unavailable: {exc}")
    try:
        source_cols.extend(utils.trial_detection_source_columns(df_header, cfg))
    except ValueError as exc:
        warnings.append(f"CS tone tracks unavailable: {exc}")
    try:
        source_cols.extend(utils.find_us_cols(df_header, cfg))
    except ValueError as exc:
        warnings.append(f"US TTL tracks unavailable: {exc}")
    source_cols = utils.unique_existing_columns(df_header, source_cols)

    # Raster generation only needs state/event tracks, not every exported
    # measurement column, so keep this sequential pass lightweight.
    df = utils.load_csv(csv_path, usecols=source_cols)
    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    if df.empty:
        report_data["exclusions"][subject_key] = "no valid time rows"
        return None, [], report_data

    day, context, session = utils.parse_folder_bits(day_dir.name)
    t = df[time_col].astype(float).to_numpy()
    subject = {
        "animal_id": animal_id,
        "behavior_id": behavior_id,
        "treatment_group": treatment,
        "sex": sex,
        "litter_id": litter_id,
        "cohort_id": cohort_id,
        "test_date": test_date,
        "day": day,
        "context": context,
        "session_label": session,
        "_day_folder": day_dir.name,
        "_day_dir": str(day_dir),
        "_source_csv": csv_path.name,
        "file_name": csv_path.name,
        "recording_start_s": float(np.nanmin(t)),
        "recording_end_s": float(np.nanmax(t)),
    }

    interval_rows = []
    columns_used = {"time": time_col}

    freeze_col = _optional_signal_column(
        df, lambda d: utils.find_freeze_col(d, cfg), "freezing", warnings)
    if freeze_col is not None:
        columns_used["freezing"] = freeze_col
        for start_s, end_s in _binary_intervals(t, df[freeze_col].fillna(0)):
            _append_interval(interval_rows, subject, "freezing", "Freezing",
                             start_s, end_s, freeze_col, "binary_state")
    else:
        columns_used["freezing"] = "not found"

    platform_col = _optional_signal_column(
        df, lambda d: utils.find_platform_col(d, cfg), "platform", warnings)
    if platform_col is not None:
        columns_used["in_platform"] = platform_col
        for start_s, end_s in _binary_intervals(t, df[platform_col].fillna(0)):
            _append_interval(interval_rows, subject, "platform", "Platform",
                             start_s, end_s, platform_col, "binary_state")
    else:
        columns_used["in_platform"] = "not found"

    trials, cs_source = _trial_intervals(df, time_col, cfg, warnings)
    columns_used["CS_detection"] = cs_source or "not found"
    for tr in trials:
        if tr["type"] == "CS+":
            _append_interval(interval_rows, subject, "cs_plus", "CS+",
                             tr["start"], tr["end"], None, cs_source)
        elif tr["type"] == "CS-":
            _append_interval(interval_rows, subject, "cs_minus", "CS-",
                             tr["start"], tr["end"], None, cs_source)

    us_rows, us_col, us_source = _us_intervals(df, time_col, trials, cfg, warnings)
    columns_used["US_detection"] = us_source or "not found"
    if us_col is not None:
        columns_used["US_col"] = us_col
    for start_s, end_s in us_rows:
        _append_interval(interval_rows, subject, "us", "US",
                         start_s, end_s, us_col, us_source)

    report_data["subjects"][subject_key] = {
        "columns_used": columns_used,
        "warnings": warnings,
        "skipped": [],
    }
    return subject, _number_events(interval_rows), report_data


def _event_spans(df_subject, event_type):
    if df_subject.empty or "event_type" not in df_subject.columns:
        return []
    sub = df_subject[df_subject["event_type"] == event_type]
    if sub.empty:
        return []
    return [
        (float(row.start_s), max(0.0, float(row.end_s) - float(row.start_s)))
        for row in sub.itertuples()
        if pd.notna(row.start_s) and pd.notna(row.end_s) and row.end_s > row.start_s
    ]


def _draw_subject_axis(ax, subject, df_intervals):
    y_positions = {event: len(TRACKS) - i - 1 for i, (event, _, _) in enumerate(TRACKS)}

    for event_type, _, color in TRACKS:
        spans = _event_spans(df_intervals, event_type)
        if spans:
            y = y_positions[event_type]
            ax.broken_barh(spans, (y - 0.34, 0.68),
                           facecolors=color, edgecolors="none", alpha=0.92)

    ax.set_ylim(-0.6, len(TRACKS) - 0.4)
    ax.set_yticks([y_positions[event] for event, _, _ in TRACKS])
    ax.set_yticklabels([label for _, label, _ in TRACKS], fontsize=7)
    ax.tick_params(axis="x", labelsize=7)
    ax.grid(axis="x", color="#D0D0D0", linewidth=0.5, alpha=0.7)
    ax.set_title(_subject_label(subject), loc="left", fontsize=8, pad=2)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)


def _make_day_raster(subjects, interval_rows, day_dir: Path, cfg: dict):
    if not subjects:
        return

    df_intervals = pd.DataFrame(interval_rows)
    n_subjects = len(subjects)
    fig_h = max(2.2, 1.25 * n_subjects)
    fig, axes = plt.subplots(
        n_subjects, 1, figsize=(14, fig_h), sharex=True, squeeze=False)
    axes = axes[:, 0]

    if df_intervals.empty:
        xmin = min(s["recording_start_s"] for s in subjects)
        xmax = max(s["recording_end_s"] for s in subjects)
    else:
        xmin = min(min(s["recording_start_s"] for s in subjects),
                   float(df_intervals["start_s"].min()))
        xmax = max(max(s["recording_end_s"] for s in subjects),
                   float(df_intervals["end_s"].max()))
    xmin = min(0.0, xmin)
    if xmax <= xmin:
        xmax = xmin + 1.0

    for ax, subject in zip(axes, subjects):
        if df_intervals.empty:
            df_subject = df_intervals
        else:
            df_subject = df_intervals[
                df_intervals["_source_csv"] == subject["_source_csv"]]
        _draw_subject_axis(ax, subject, df_subject)
        ax.set_xlim(xmin, xmax)

    axes[-1].set_xlabel("Time (s)", fontsize=9)
    title = f"Event and Behavioral Raster - {day_dir.name}"
    fig.suptitle(title, fontsize=12, fontweight="bold", y=0.995)

    handles = [Patch(facecolor=color, edgecolor="none", label=label)
               for _, label, color in TRACKS]
    fig.legend(handles=handles, loc="upper right", frameon=False,
               fontsize=8, ncol=len(TRACKS), bbox_to_anchor=(0.995, 0.998))

    fig.tight_layout(rect=[0.04, 0.02, 0.99, 0.965])

    out_dir = day_dir / "Analysis" / cfg["event_raster_subfolder"]
    out_dir.mkdir(parents=True, exist_ok=True)
    utils.save_fig(fig, out_dir / f"event_behavior_raster_{day_dir.name}.svg")


def _process_day(day_dir: Path, meta, cfg: dict, report=None):
    suffix = cfg["csv_suffix"]
    csvs = sorted(day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
    if not csvs:
        print(f"  [warn] No CSVs in {day_dir}")
        return

    subjects = []
    interval_rows = []
    for csv_path in csvs:
        subject, rows, report_data = _process_file(csv_path, meta, day_dir, cfg)
        if subject is not None:
            subjects.append(subject)
            interval_rows.extend(rows)

        if report is not None:
            for key, info in report_data["subjects"].items():
                rr.record_subject(report, "event_raster", key,
                                  columns_used=info["columns_used"],
                                  warnings=info["warnings"],
                                  skipped=info["skipped"])
            for key, reason in report_data["exclusions"].items():
                rr.record_exclusion(report, "event_raster", key, reason)

    if not subjects:
        print(f"  [warn] No valid subjects for event raster in {day_dir.name}.")
        return

    subjects = sorted(subjects, key=lambda s: str(s.get("animal_id", "")).casefold())
    _make_day_raster(subjects, interval_rows, day_dir, cfg)


def run(cfg: dict, report=None):
    subfolder = cfg["event_raster_subfolder"]
    for bd in cfg["behaviordata_dirs"]:
        meta = utils.load_metadata(bd)
        day_dirs = utils.find_session_dirs(bd)
        if not day_dirs:
            print(f"  [warn] No session directories found in {bd}")
            continue
        print(f"  Writing pass-1 event rasters for {bd.name} -> Analysis/{subfolder}")
        for day_dir in day_dirs:
            _process_day(day_dir, meta, cfg, report=report)
    print("  Event raster analysis complete.")
