# -*- coding: utf-8 -*-
"""
us_locked_analysis.py
---------------------
Computes % time on platform during each US (shock) window, locked to the
moment of shock delivery rather than the full CS+ trial window.
"""

from pathlib import Path
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable

import utils
import run_report as rr


# ── US window detection ─────────────────────────────────────────────────────

def _get_us_windows_mode_a(df, time_col, cs_trials, cfg):
    shocker_col = utils.find_shocker_col(df, cfg)
    if shocker_col is None:
        return None, "missing_shocker_column"

    t = df[time_col].astype(float).to_numpy()
    s = df[shocker_col].fillna(0).astype(float).to_numpy()
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
            for cs_start, cs_end in cs_intervals:
                clipped_start = max(block_start, cs_start)
                clipped_end   = min(block_end, cs_end)
                if clipped_end > clipped_start:
                    us_counter += 1
                    us_windows.append({"type": "US", "start": clipped_start,
                                       "end": clipped_end, "trial_index": us_counter,
                                       "source": shocker_col})
                    break

    if in_block:
        block_start = float(t[block_start_idx])
        block_end   = float(t[-1])
        for cs_start, cs_end in cs_intervals:
            clipped_start = max(block_start, cs_start)
            clipped_end   = min(block_end, cs_end)
            if clipped_end > clipped_start:
                us_counter += 1
                us_windows.append({"type": "US", "start": clipped_start,
                                   "end": clipped_end, "trial_index": us_counter,
                                   "source": shocker_col})
                break

    return us_windows, shocker_col


def _get_us_windows_mode_b(cs_trials, us_duration_s):
    us_windows = []
    for tr in cs_trials:
        if tr["type"] != "CS+":
            continue
        us_end   = tr["end"]
        us_start = max(tr["start"], us_end - us_duration_s)
        us_windows.append({"type": "US", "start": us_start, "end": us_end,
                            "trial_index": tr["trial_index"], "source": "last_2s_of_CS+"})
    return us_windows, "last_2s_of_CS+"


# ── Per-file processing ─────────────────────────────────────────────────────

def _process_file(csv_path, meta, day_dir, cfg, run_log, report=None):
    test_date, behavior_id = utils.parse_filename_bits(csv_path, meta)
    subject_key = csv_path.name

    if behavior_id is None:
        run_log["excluded_subjects"][subject_key] = "could_not_parse_behavior_id"
        if report is not None:
            rr.record_exclusion(report, "us_locked", subject_key,
                                "could not parse behavior_id from filename")
        return []

    row_meta = utils.find_metadata_for_behavior(meta, behavior_id) if meta is not None else pd.DataFrame()
    if row_meta.empty:
        run_log["excluded_subjects"][subject_key] = f"behavior_id '{behavior_id}' not found in metadata"
        if report is not None:
            rr.record_exclusion(report, "us_locked", subject_key,
                                f"behavior_id '{behavior_id}' not found in metadata")
        return []

    row       = row_meta.iloc[0]
    animal_id = row.get("animal_id", behavior_id)
    cohort_id = row.get("cohort_id", None)
    treatment = utils.normalize_treatment(row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    sex       = utils.normalize_sex(row.get("sex", None))

    canonical = cfg["canonical_groups"]
    include   = cfg.get("include_treatments")

    if treatment not in canonical:
        reason = f"treatment '{treatment}' not in canonical groups {canonical}"
        run_log["excluded_subjects"][subject_key] = reason
        if report is not None:
            rr.record_exclusion(report, "us_locked", subject_key, reason)
        return []

    if include is not None and treatment not in include:
        reason = f"treatment '{treatment}' not in INCLUDE_TREATMENTS {include}"
        run_log["excluded_subjects"][subject_key] = reason
        if report is not None:
            rr.record_exclusion(report, "us_locked", subject_key, reason)
        return []

    if behavior_id in cfg.get("exclude_behavior_ids", []):
        reason = "explicitly excluded via EXCLUDE_BEHAVIOR_IDS"
        run_log["excluded_subjects"][subject_key] = reason
        if report is not None:
            rr.record_exclusion(report, "us_locked", subject_key, reason)
        return []

    df = utils.load_csv(csv_path)
    subject_log = {"columns_used": {}, "skipped_analyses": [], "warnings": []}

    try:
        time_col = utils.find_time_col(df, cfg)
        platform_col = utils.find_platform_col(df, cfg)
        subject_log["columns_used"]["time"] = time_col
        subject_log["columns_used"]["in_platform"] = platform_col
    except ValueError as e:
        subject_log["skipped_analyses"].append(f"column match error: {e}")
        run_log["subject_logs"][subject_key] = subject_log
        run_log["excluded_subjects"][subject_key] = str(e)
        if report is not None:
            rr.record_exclusion(report, "us_locked", subject_key, str(e))
        return []

    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    trials, _, cs_source = utils.detect_trials(df, time_col, cfg)
    subject_log["columns_used"]["CS+_detection"] = cs_source

    cs_trials = [tr for tr in trials if tr["type"] == "CS+"]
    if not cs_trials:
        subject_log["skipped_analyses"].append("no CS+ trials detected")
        run_log["subject_logs"][subject_key] = subject_log
        if report is not None:
            rr.record_exclusion(report, "us_locked", subject_key, "no CS+ trials detected")
        return []

    if cfg["use_shocker_column"]:
        try:
            us_windows, us_source = _get_us_windows_mode_a(df, time_col, cs_trials, cfg)
        except ValueError as e:
            subject_log["skipped_analyses"].append(f"column match error: {e}")
            run_log["subject_logs"][subject_key] = subject_log
            run_log["excluded_subjects"][subject_key] = str(e)
            if report is not None:
                rr.record_exclusion(report, "us_locked", subject_key, str(e))
            return []
        if us_windows is None:
            subject_log["skipped_analyses"].append(f"Mode A selected but: {us_source}")
            run_log["subject_logs"][subject_key] = subject_log
            run_log["excluded_subjects"][subject_key] = us_source
            if report is not None:
                rr.record_exclusion(report, "us_locked", subject_key,
                                    f"Mode A: {us_source}")
            return []
    else:
        us_windows, us_source = _get_us_windows_mode_b(cs_trials, cfg["us_duration_s"])

    subject_log["columns_used"]["US_detection"] = us_source

    if not us_windows:
        subject_log["skipped_analyses"].append("no US windows detected")
        run_log["subject_logs"][subject_key] = subject_log
        if report is not None:
            rr.record_exclusion(report, "us_locked", subject_key, "no US windows detected")
        return []

    # Success — record to run_report
    if report is not None:
        rr.record_subject(report, "us_locked", subject_key,
                          columns_used=subject_log["columns_used"],
                          warnings=subject_log["warnings"],
                          skipped=subject_log["skipped_analyses"])

    t = df[time_col].astype(float).to_numpy()
    p = df[platform_col].fillna(0).astype(float).to_numpy()
    day, context, session = utils.parse_folder_bits(day_dir.name)

    rows = []
    for w in us_windows:
        dur      = max(0.0, w["end"] - w["start"])
        plat_s   = utils.integrate_binary(t, p, w["start"], w["end"]) if dur > 0 else 0.0
        plat_pct = 100.0 * plat_s / dur if dur > 0 else 0.0
        rows.append({
            "animal_id":               animal_id,
            "behavior_id":             behavior_id,
            "cohort_id":               cohort_id,
            "treatment_group":         treatment,
            "sex":                     sex,
            "test_date":               test_date,
            "day":                     day,
            "context":                 context,
            "session_label":           session,
            "_day_folder":             day_dir.name,
            "_source_csv":             csv_path.name,
            "us_number":               w["trial_index"],
            "us_start_s":              w["start"],
            "us_end_s":                w["end"],
            "platform_time_s":         plat_s,
            "platform_pct":            plat_pct,
            "platform_pct_above_chance": plat_pct - cfg["us_chance_baseline_pct"],
        })

    run_log["subject_logs"][subject_key] = subject_log
    return rows


# ── Data collection ─────────────────────────────────────────────────────────

def _collect_day(day_dir, meta, cfg, run_log, report=None):
    suffix   = cfg["csv_suffix"]
    all_rows = []
    csvs = sorted(day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
    for csv_path in csvs:
        rows = _process_file(csv_path, meta, day_dir, cfg, run_log, report=report)
        all_rows.extend(rows)
    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


# ── Duplicate-ID warning helper ─────────────────────────────────────────────

def _duplicate_animal_id_warning(df):
    if df.empty or "cohort_id" not in df.columns or "animal_id" not in df.columns:
        return None
    tmp  = df[["cohort_id", "animal_id"]].drop_duplicates().copy()
    tmp["_animal_id_label"] = tmp["animal_id"].map(_animal_id_label)
    dupes = tmp.groupby("_animal_id_label", dropna=False, sort=False)["cohort_id"].nunique(dropna=False)
    bad   = dupes[dupes > 1]
    if bad.empty:
        return None
    ids = ", ".join(map(str, bad.index.tolist()))
    return (f"Duplicate animal_id values appear across cohorts: {ids}. "
            "Rows may be averaged during pivoting. Recommended fix: make animal IDs globally unique.")


def _animal_id_label(value):
    return "" if pd.isna(value) else str(value)


def _animal_sort_key(value):
    return _animal_id_label(value).casefold()


def _apply_plot_trial_caps(df, cfg):
    if df.empty:
        return df
    cap = cfg.get("cs_trial_cap")
    if not cap:
        return df
    group_cols = [c for c in ("_day_folder", "cohort_id", "animal_id") if c in df.columns]
    if not group_cols:
        return df
    work = df.copy()
    if "animal_id" in work.columns:
        work["_animal_id_sort"] = work["animal_id"].map(_animal_sort_key)
    sort_cols = [
        "_animal_id_sort" if c == "animal_id" and "_animal_id_sort" in work.columns else c
        for c in ("_day_folder", "cohort_id", "animal_id", "us_number", "_source_csv")
        if c in work.columns
    ]
    return (work.sort_values(sort_cols)
              .groupby(group_cols, dropna=False, group_keys=False, sort=False)
              .head(int(cap))
              .drop(columns=["_animal_id_sort"], errors="ignore")
              .copy())


# ── Heatmap ─────────────────────────────────────────────────────────────────

def _make_heatmap(df, treatment, out_dir, cfg, label_suffix="",
                  row_order=None, fig=None, ax=None, show_cbar=True):
    if df.empty:
        return None, None, None

    df = df.copy()
    df["_animal_id_label"] = df["animal_id"].map(_animal_id_label)

    baseline  = cfg["us_chance_baseline_pct"]
    sort_mode = cfg.get("heatmap_sort", "response")
    row_col   = "_animal_id_label"

    pivot = df.pivot_table(index=row_col, columns="us_number",
                           values="platform_pct_above_chance", aggfunc="mean")

    if row_order is None:
        order = (pivot.mean(axis=1).sort_values(ascending=False).index.tolist()
                 if sort_mode == "response"
                 else sorted(pivot.index.tolist(), key=_animal_sort_key))
    else:
        order = [_animal_id_label(v) for v in row_order]
    pivot = pivot.reindex(order)

    max_us = int(pivot.columns.max()) if len(pivot.columns) else 1
    vmax   = 100.0 - baseline
    vmin   = -baseline

    created_fig = False
    if fig is None or ax is None:
        fig_w = max(6, max_us * 0.5)
        fig_h = max(3, len(pivot) * 0.5)
        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
        created_fig = True

    hm = sns.heatmap(pivot, cmap="coolwarm_r", vmin=vmin, vmax=vmax, center=0,
                     square=True, ax=ax, cbar=show_cbar,
                     cbar_kws={"label": "% platform time during US\n(above chance)"} if show_cbar else None)

    ax.set_xticks(np.arange(max_us) + 0.5)
    ax.set_xticklabels([f"US{i}" for i in range(1, max_us + 1)], rotation=45)
    ax.set_xlabel("US number")
    ax.set_ylabel("Subject (sorted by response)" if sort_mode == "response" else "Subject (alphabetical)")
    title = f"{treatment}" + (f" — {label_suffix}" if label_suffix else "")
    ax.set_title(title)
    return fig, ax, hm


# ── Combined tiled figure ───────────────────────────────────────────────────

def _make_tiled_treatment_figure(df_trt, treatment, out_dir, cfg):
    if df_trt.empty:
        return
    df_plot = _apply_plot_trial_caps(df_trt, cfg)
    if df_plot.empty:
        return
    df_plot = df_plot.copy()
    df_plot["_animal_id_label"] = df_plot["animal_id"].map(_animal_id_label)

    days = sorted(df_plot["_day_folder"].dropna().unique().tolist(), key=utils.day_sort_key)
    if not days:
        return

    sort_mode = cfg.get("heatmap_sort", "response")
    if sort_mode == "response":
        row_order = (df_plot.groupby("_animal_id_label", dropna=False, sort=False)["platform_pct_above_chance"]
                     .mean().sort_values(ascending=False).index.tolist())
    else:
        row_order = sorted(df_plot["_animal_id_label"].dropna().unique().tolist(), key=_animal_sort_key)

    max_us  = int(df_plot["us_number"].max()) if not df_plot["us_number"].empty else 1
    n_days  = len(days)
    fig_w   = max(4.5 * n_days, 6)
    fig_h   = max(4, len(row_order) * 0.35)
    fig, axes = plt.subplots(1, n_days, figsize=(fig_w, fig_h), squeeze=False,
                              gridspec_kw={"wspace": 0.05})
    axes = axes[0]

    vmax = 100.0 - cfg["us_chance_baseline_pct"]
    vmin = -cfg["us_chance_baseline_pct"]
    cmap = plt.get_cmap("coolwarm_r")
    norm = Normalize(vmin=vmin, vmax=vmax)

    last_mappable = None
    for i, day in enumerate(days):
        ax     = axes[i]
        df_day = df_plot[df_plot["_day_folder"] == day].copy()
        warn   = _duplicate_animal_id_warning(df_day)
        if warn:
            print(f"[warn] {treatment} / {day}: {warn}")

        pivot = df_day.pivot_table(index="_animal_id_label", columns="us_number",
                                   values="platform_pct_above_chance",
                                   aggfunc="mean").reindex(row_order)
        sns.heatmap(pivot, cmap=cmap, vmin=vmin, vmax=vmax, center=0,
                    square=False, ax=ax, cbar=False)

        ax.set_title(day)
        ax.set_xlabel("US number")
        if i == 0:
            ax.set_ylabel("Subject (sorted by response)" if sort_mode == "response"
                          else "Subject (alphabetical)")
        else:
            ax.set_ylabel("")
            ax.tick_params(axis="y", labelleft=False)

        ax.set_xticks(np.arange(max_us) + 0.5)
        ax.set_xticklabels([f"US{i}" for i in range(1, max_us + 1)], rotation=45)
        last_mappable = ScalarMappable(norm=norm, cmap=cmap)
        last_mappable.set_array([])

    fig.suptitle(f"{treatment} — US-locked platform time", y=1.02)
    fig.tight_layout()
    fig.subplots_adjust(wspace=0.05, top=0.88, right=0.94)
    cbar = fig.colorbar(last_mappable, ax=axes.tolist(), shrink=0.85, pad=0.015)
    cbar.set_label("% platform time during US\n(above chance)")

    trt_dir = out_dir / treatment
    trt_dir.mkdir(parents=True, exist_ok=True)
    utils.save_fig(fig, trt_dir / f"us_locked_heatmap_{treatment}_tiled.svg")


# ── Prism export ────────────────────────────────────────────────────────────

def _prism_export(df, out_dir, tag, subject_col="animal_id"):
    df = df.copy()
    df["_subject_label"] = df[subject_col].map(_animal_id_label)
    df = df.sort_values(["treatment_group", "_day_folder", "us_number", "_subject_label"])
    groups   = df[["treatment_group", "_day_folder"]].drop_duplicates()
    out_path = out_dir / f"{tag}_prism_ready.xlsx"
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for _, row in groups.iterrows():
            subset = df[(df["treatment_group"] == row["treatment_group"]) &
                        (df["_day_folder"]     == row["_day_folder"])]
            wide = subset.pivot_table(index="us_number", columns="_subject_label",
                                      values="platform_pct_above_chance",
                                      aggfunc="mean").sort_index()
            wide.columns.name = subject_col
            sheet = f"{row['treatment_group']}_{row['_day_folder']}"[:31]
            wide.to_excel(writer, sheet_name=sheet)
    print(f"[ok] Prism table saved: {out_path}")


# ── Shock avoidance summary ─────────────────────────────────────────────────

def _shock_avoidance(df, out_dir, tag):
    avoided = df[df["platform_pct"] >= 100.0].copy()
    summary = (avoided.groupby(["treatment_group", "cohort_id", "animal_id", "_day_folder"],
                               dropna=False, as_index=False, sort=False)
               .agg(shocks_avoided=("us_number", "count")))
    out_path = out_dir / f"{tag}_shock_avoidance.csv"
    summary.to_csv(out_path, index=False)
    print(f"[ok] Shock avoidance summary saved: {out_path}")


# ── Legacy txt run report ───────────────────────────────────────────────────

def _write_run_report(out_dir, cfg, run_log):
    report_path = out_dir / "us_locked_run_report.txt"
    lines = []
    lines.append("=" * 70)
    lines.append("US-LOCKED PLATFORM ANALYSIS — RUN REPORT")
    lines.append("=" * 70)
    lines.append(f"Timestamp : {run_log['timestamp']}")
    lines.append(f"Python    : {run_log['python_version']}")
    lines.append("")
    lines.append("--- Runner settings ---")
    for k in ["use_shocker_column","us_duration_s","us_chance_baseline_pct",
              "include_treatments","exclude_behavior_ids","heatmap_sort",
              "column_match_mode","prism_export","cs_trial_cap"]:
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
            for k, v in slog.get("columns_used", {}).items():
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


# ── Entry point ─────────────────────────────────────────────────────────────

def run(cfg, report=None):
    combined_root = Path(cfg["analysis_out"]) / "US_lock_plttime"
    combined_root.mkdir(parents=True, exist_ok=True)
    combined_rows = []

    for bd in cfg["behaviordata_dirs"]:
        bd_cfg = {**cfg, "behaviordata": bd}
        meta   = utils.load_metadata(bd)

        for day_dir in utils.find_session_dirs(bd):
            run_log = {
                "timestamp":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "python_version":     sys.version,
                "excluded_subjects":  {},
                "subject_logs":       {},
                "included_treatments": [],
            }

            print(f"  Collecting US-locked data from: {day_dir.name}")
            df_day = _collect_day(day_dir, meta, bd_cfg, run_log, report=report)

            day_out = day_dir / "Analysis" / "US_lock_plttime"
            day_out.mkdir(parents=True, exist_ok=True)

            if df_day.empty:
                print(f"  [warn] No US-locked data found in {day_dir.name}. Skipping.")
                continue

            df_day.to_csv(day_out / "us_locked_all_days_concat.csv", index=False)
            print(f"  [ok] Session CSV saved: {day_out / 'us_locked_all_days_concat.csv'}")

            run_log["included_treatments"] = df_day["treatment_group"].dropna().unique().tolist()
            _write_run_report(day_out, bd_cfg, run_log)

            for trt in cfg["canonical_groups"]:
                include = cfg.get("include_treatments")
                if include is not None and trt not in include:
                    continue
                df_trt = df_day[df_day["treatment_group"] == trt]
                if df_trt.empty:
                    continue
                trt_dir = day_out / trt
                trt_dir.mkdir(parents=True, exist_ok=True)
                df_trt_plot = _apply_plot_trial_caps(df_trt, bd_cfg)
                _shock_avoidance(df_trt, trt_dir, trt)
                fig, _, _ = _make_heatmap(df_trt_plot, trt, trt_dir, bd_cfg,
                                          label_suffix=day_dir.name)
                if fig is not None:
                    utils.save_fig(fig, trt_dir / f"us_locked_heatmap_{trt}.svg")
                if cfg.get("prism_export"):
                    _prism_export(df_trt_plot, trt_dir, trt)

            combined_rows.append(df_day.assign(_behaviordata=bd.name))

    if not combined_rows:
        print("  [warn] No US-locked data found across any BehaviorData directory.")
        return

    df_all = pd.concat(combined_rows, ignore_index=True)
    df_all.to_csv(combined_root / "us_locked_all_days_concat.csv", index=False)
    print(f"  [ok] Combined CSV saved: {combined_root / 'us_locked_all_days_concat.csv'}")

    for trt in cfg["canonical_groups"]:
        include = cfg.get("include_treatments")
        if include is not None and trt not in include:
            continue
        df_trt = df_all[df_all["treatment_group"] == trt]
        if df_trt.empty:
            continue
        _make_tiled_treatment_figure(df_trt, trt, combined_root, cfg)

    print("  US-locked analysis complete.")


if __name__ == "__main__":
    raise SystemExit("Run this module through runner.py")
