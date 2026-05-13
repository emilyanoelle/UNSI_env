# -*- coding: utf-8 -*-
"""
statistics_utils.py
-------------------
Python wrapper for R-backed statistics.

The behavioral analysis modules produce long-format concatenated CSV files.
This module reads those files, prepares one tidy table per requested scope, and
delegates the model fitting to statistics_2way_anova.R.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import tempfile
from typing import Iterable

import numpy as np
import pandas as pd

import utils
import run_report as rr


MEASURES = (
    {
        "measure": "freezing",
        "label": "% time freezing",
        "subfolder_key": "freezing_subfolder",
        "input_name": "freezing_all_days_concat.csv",
        "value_col": "freeze_pct",
    },
    {
        "measure": "platform",
        "label": "% time on platform",
        "subfolder_key": "platform_subfolder",
        "input_name": "platform_all_days_concat.csv",
        "value_col": "platform_pct",
    },
)

STATISTICS_SUBFOLDER_DEFAULT = "statistics"


def _as_bool(value, default=False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _safe_name(value) -> str:
    text = str(value if value is not None else "unknown").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("._") or "unknown"


def _series_or_default(df: pd.DataFrame, column: str, default="") -> pd.Series:
    if column in df.columns:
        return df[column]
    return pd.Series([default] * len(df), index=df.index)


def _first_subject_id(df: pd.DataFrame) -> pd.Series:
    if "behavior_id" in df.columns:
        subject = df["behavior_id"].copy()
    elif "animal_id" in df.columns:
        subject = df["animal_id"].copy()
    else:
        subject = pd.Series(np.arange(len(df)), index=df.index)

    if "animal_id" in df.columns:
        subject = subject.where(subject.notna() & (subject.astype(str).str.strip() != ""),
                                df["animal_id"])
    return subject.fillna("unknown_subject").astype(str)


def _prepare_measure_frame(raw_df: pd.DataFrame, measure_def: dict,
                           cfg: dict) -> pd.DataFrame:
    value_col = measure_def["value_col"]
    required = {"treatment_group", "trial_index", value_col}
    if "trial_type" not in raw_df.columns and "_trial_type" not in raw_df.columns:
        required.add("trial_type")
    missing = sorted(c for c in required if c not in raw_df.columns)
    if missing:
        print(f"  [warn] {measure_def['measure']} statistics skipped; "
              f"missing columns: {', '.join(missing)}")
        return pd.DataFrame()

    df = raw_df.copy()
    if "_trial_type" not in df.columns:
        df["_trial_type"] = df["trial_type"].apply(utils.normalize_trial_type)
    else:
        df["_trial_type"] = df["_trial_type"].apply(utils.normalize_trial_type)

    df = utils.filter_trials(df, cfg.get("cs_trial_cap", 10),
                             cfg.get("iti_trial_cap", 20))
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df["trial_index"] = pd.to_numeric(df["trial_index"], errors="coerce")
    df = df.dropna(subset=[value_col, "trial_index", "treatment_group"])
    if df.empty:
        return pd.DataFrame()

    cohort = _series_or_default(df, "cohort_id", "unknown_cohort")
    cohort = cohort.fillna("unknown_cohort").astype(str)
    subject = _first_subject_id(df)
    stats_subject_id = cohort + "|" + subject

    session = _series_or_default(df, "_day_folder", "")
    session = session.where(session.astype(str).str.strip() != "",
                            _series_or_default(df, "session_label", "unknown_session"))

    prepared = pd.DataFrame({
        "measure": measure_def["measure"],
        "subject_id": stats_subject_id,
        "animal_id": _series_or_default(df, "animal_id", "").fillna("").astype(str),
        "behavior_id": _series_or_default(df, "behavior_id", "").fillna("").astype(str),
        "treatment_group": df["treatment_group"].fillna("Unknown").astype(str),
        "cohort_id": cohort,
        "session": session.fillna("unknown_session").astype(str),
        "day": _series_or_default(df, "day", "").fillna("").astype(str),
        "context": _series_or_default(df, "context", "").fillna("").astype(str),
        "session_label": _series_or_default(df, "session_label", "").fillna("").astype(str),
        "trial_type": df["_trial_type"].astype(str),
        "time": df["trial_index"].astype(int).astype(str),
        "time_numeric": df["trial_index"].astype(float),
        "value": df[value_col].astype(float),
    })

    prepared = prepared[prepared["trial_type"].isin(["CS+", "CS-", "ITI"])]
    include_treatments = cfg.get("stats_include_treatments")
    if include_treatments is not None:
        if isinstance(include_treatments, str):
            include_treatments = [include_treatments]
        include_set = {str(t) for t in include_treatments}
        excluded = sorted(set(prepared["treatment_group"].unique()) - include_set)
        if excluded:
            print(f"  [info] {measure_def['measure']} statistics excluding "
                  f"treatments not in STATS_INCLUDE_TREATMENTS: {', '.join(excluded)}")
        prepared = prepared[prepared["treatment_group"].isin(include_set)]
    return prepared.reset_index(drop=True)


def _read_measure_frame(cfg: dict, measure_def: dict) -> pd.DataFrame:
    in_path = (
        Path(cfg["analysis_out"])
        / cfg[measure_def["subfolder_key"]]
        / measure_def["input_name"]
    )
    if not in_path.exists():
        print(f"  [warn] Statistics input not found for {measure_def['measure']}: {in_path}")
        return pd.DataFrame()

    print(f"  Loading {measure_def['measure']} input: {in_path}")
    raw_df = pd.read_csv(in_path)
    return _prepare_measure_frame(raw_df, measure_def, cfg)


def _cohort_to_behaviordata(cfg: dict) -> dict:
    mapping = {}
    for bd in cfg.get("behaviordata_dirs", []):
        bd = Path(bd)
        try:
            meta = utils.load_metadata(bd)
        except Exception as exc:
            print(f"  [warn] Could not load metadata for statistics cohort map: {bd} ({exc})")
            continue
        if meta is None or "cohort_id" not in meta.columns:
            continue
        for cohort_id in meta["cohort_id"].dropna().astype(str).unique():
            mapping.setdefault(cohort_id, bd)
    return mapping


def _write_failure_log(log_path: Path, measure: str, analysis_scope: str,
                       analysis_cohort_id: str, message: str):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "analysis_scope": analysis_scope,
        "analysis_cohort_id": analysis_cohort_id,
        "measure": measure,
        "session": "",
        "trial_type": "",
        "method": "not_run",
        "status": "error",
        "model_formula": "",
        "n_subjects": 0,
        "n_rows": 0,
        "n_timepoints": 0,
        "n_treatments": 0,
        "missing_cells": "",
        "message": message,
    }]).to_csv(log_path, index=False)


def _rscript_command(cfg: dict) -> list[str]:
    command = cfg.get("stats_rscript_command")
    if command:
        if isinstance(command, (list, tuple)):
            return [str(part) for part in command]
        return [str(command)]
    return [str(cfg.get("stats_rscript_path", "Rscript"))]


def _run_r_backend(input_df: pd.DataFrame, out_dir: Path, output_prefix: str,
                   cfg: dict, analysis_scope: str, analysis_cohort_id: str,
                   measure: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / f"{output_prefix}_2way_anova.csv"
    log_path = out_dir / f"{output_prefix}_model_log.csv"

    rscript_cmd = _rscript_command(cfg)
    rscript_display = " ".join(rscript_cmd)
    backend = Path(cfg.get(
        "stats_r_backend_script",
        Path(__file__).with_name("statistics_2way_anova.R"),
    ))
    use_gg = "true" if _as_bool(cfg.get("stats_use_greenhouse_geisser"), True) else "false"

    if input_df.empty:
        _write_failure_log(
            log_path, measure, analysis_scope, analysis_cohort_id,
            "No rows available after filtering.",
        )
        pd.DataFrame().to_csv(results_path, index=False)
        return results_path, log_path

    input_df = input_df.copy()
    input_df["analysis_scope"] = analysis_scope
    input_df["analysis_cohort_id"] = analysis_cohort_id

    with tempfile.TemporaryDirectory(prefix="fc_stats_") as tmp:
        input_path = Path(tmp) / f"{output_prefix}_input.csv"
        input_df.to_csv(input_path, index=False)
        cmd = rscript_cmd + [
            str(backend),
            "--input", str(input_path),
            "--results", str(results_path),
            "--log", str(log_path),
            "--use_gg", use_gg,
        ]
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            message = (
                f"Rscript command not found: {rscript_display}. Install R, "
                "set STATS_RSCRIPT_PATH, or set STATS_RSCRIPT_COMMAND in runner.py."
            )
            print(f"  [warn] {message}")
            _write_failure_log(log_path, measure, analysis_scope,
                               analysis_cohort_id, message)
            pd.DataFrame().to_csv(results_path, index=False)
            return results_path, log_path

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        message = stderr or stdout or f"R backend exited with code {completed.returncode}"
        print(f"  [warn] R statistics backend failed for {output_prefix}: {message}")
        _write_failure_log(log_path, measure, analysis_scope,
                           analysis_cohort_id, message)
        pd.DataFrame().to_csv(results_path, index=False)
    elif completed.stderr.strip():
        print(f"  [info] R statistics backend messages for {output_prefix}: "
              f"{completed.stderr.strip()}")

    return results_path, log_path


def _run_scope(prepared_df: pd.DataFrame, measure: str, out_dir: Path,
               output_prefix: str, cfg: dict, analysis_scope: str,
               analysis_cohort_id: str) -> tuple[Path, Path]:
    n_subjects = prepared_df["subject_id"].nunique() if "subject_id" in prepared_df else 0
    n_sessions = prepared_df["session"].nunique() if "session" in prepared_df else 0
    treatments = (
        ", ".join(sorted(prepared_df["treatment_group"].dropna().astype(str).unique()))
        if "treatment_group" in prepared_df else "none"
    )
    print(f"  Running {measure} statistics ({analysis_scope}: {analysis_cohort_id}; "
          f"{n_subjects} subjects, {n_sessions} sessions, treatments: {treatments})...")
    return _run_r_backend(
        prepared_df, out_dir, output_prefix, cfg, analysis_scope,
        analysis_cohort_id, measure,
    )


def _record_outputs(report: dict | None, outputs: Iterable[tuple[Path, Path]],
                    cfg: dict):
    if report is None:
        return
    rr.record_overview(report, "statistics", "Backend", "R via Python wrapper")
    rr.record_overview(
        report, "statistics", "Greenhouse-Geisser correction",
        cfg.get("stats_use_greenhouse_geisser"),
    )
    rr.record_overview(
        report, "statistics", "Pass 2 by cohort",
        cfg.get("stats_by_cohort"),
    )
    rr.record_overview(
        report, "statistics", "Pass 3 combined",
        cfg.get("stats_combined"),
    )
    rr.record_overview(
        report, "statistics", "Included treatments",
        cfg.get("stats_include_treatments") or "all",
    )
    paths = []
    for results_path, log_path in outputs:
        paths.extend([str(results_path), str(log_path)])
    rr.record_overview(report, "statistics", "Output files", "\n".join(paths) or "none")


def run(cfg: dict, report=None):
    stats_by_cohort = _as_bool(cfg.get("stats_by_cohort"), True)
    stats_combined = _as_bool(cfg.get("stats_combined"), True)
    stats_subfolder = cfg.get("statistics_subfolder", STATISTICS_SUBFOLDER_DEFAULT)

    if not stats_by_cohort and not stats_combined:
        print("  [warn] Statistics enabled, but both STATS_BY_COHORT and "
              "STATS_COMBINED are False. Nothing to run.")
        return

    print("  Pass 1: loading freezing/platform concat tables...")
    measure_frames = []
    for measure_def in MEASURES:
        prepared = _read_measure_frame(cfg, measure_def)
        if prepared.empty:
            continue
        measure_frames.append((measure_def["measure"], prepared))

    if not measure_frames:
        print("  [warn] No statistics inputs found. Skipping statistics.")
        return

    outputs = []

    if stats_by_cohort:
        print("  Pass 2: running cohort-level statistics...")
        cohort_map = _cohort_to_behaviordata(cfg)
        for measure, prepared in measure_frames:
            for cohort_id, cohort_df in prepared.groupby("cohort_id", dropna=False):
                cohort_key = str(cohort_id)
                bd = cohort_map.get(cohort_key)
                if bd is None:
                    print(f"  [warn] Cannot locate BehaviorData for cohort "
                          f"'{cohort_key}'; skipping cohort statistics.")
                    continue
                out_dir = Path(bd) / "Analysis" / stats_subfolder
                prefix = f"{measure}_{_safe_name(cohort_key)}_statistics"
                outputs.append(_run_scope(
                    cohort_df, measure, out_dir, prefix, cfg,
                    "cohort", cohort_key,
                ))

    if stats_combined:
        print("  Pass 3: running combined across-cohort statistics...")
        out_dir = Path(cfg["analysis_out"]) / stats_subfolder
        for measure, prepared in measure_frames:
            prefix = f"{measure}_statistics"
            outputs.append(_run_scope(
                prepared, measure, out_dir, prefix, cfg,
                "combined", "collapsed",
            ))

    _record_outputs(report, outputs, cfg)
    print("  Statistics complete.")
