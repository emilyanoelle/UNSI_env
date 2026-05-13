# -*- coding: utf-8 -*-
"""
us_locked_analysis.py
---------------------
Computes % time on platform during each US (shock) window, locked to the
moment of shock delivery rather than the full CS+ trial window.
"""

from pathlib import Path
import re
from concurrent.futures import ProcessPoolExecutor, as_completed

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

def _process_file(csv_path, meta, day_dir, cfg):
    report_data = {"subjects": {}, "exclusions": {}, "subject_logs": {}}
    subject_key = csv_path.name

    test_date, behavior_id, row, exclusion_reason = utils.metadata_for_csv(csv_path, meta)
    if exclusion_reason is not None:
        report_data["exclusions"][subject_key] = exclusion_reason
        return [], report_data

    animal_id = row.get("animal_id", behavior_id)
    cohort_id = row.get("cohort_id", None)
    treatment = utils.normalize_treatment(row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    sex       = utils.normalize_sex(row.get("sex", None))

    canonical = cfg["canonical_groups"]
    include   = cfg.get("include_treatments")

    if treatment not in canonical:
        reason = f"treatment '{treatment}' not in canonical groups {canonical}"
        report_data["exclusions"][subject_key] = reason
        return [], report_data

    if include is not None and treatment not in include:
        reason = f"treatment '{treatment}' not in INCLUDE_TREATMENTS {include}"
        report_data["exclusions"][subject_key] = reason
        return [], report_data

    if behavior_id in cfg.get("exclude_behavior_ids", []):
        reason = "explicitly excluded via EXCLUDE_BEHAVIOR_IDS"
        report_data["exclusions"][subject_key] = reason
        return [], report_data

    df_header = utils.load_csv_header(csv_path)
    subject_log = {"columns_used": {}, "skipped_analyses": [], "warnings": []}

    try:
        time_col = utils.find_time_col(df_header, cfg)
        platform_col = utils.find_platform_col(df_header, cfg)
        source_cols = [time_col, platform_col] + utils.trial_detection_source_columns(df_header, cfg)
        if cfg["use_shocker_column"]:
            source_cols.append(utils.find_shocker_col(df_header, cfg))
        source_cols = utils.unique_existing_columns(df_header, source_cols)
        subject_log["columns_used"]["time"] = time_col
        subject_log["columns_used"]["in_platform"] = platform_col
    except ValueError as e:
        subject_log["skipped_analyses"].append(f"column match error: {e}")
        report_data["subject_logs"][subject_key] = subject_log
        report_data["exclusions"][subject_key] = str(e)
        return [], report_data

    # US-locked output only uses platform occupancy, CS timing, and optionally
    # the shocker state column; skipping other columns keeps worker reads small.
    df = utils.load_csv(csv_path, usecols=source_cols)
    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    trials, _, cs_source = utils.detect_trials(df, time_col, cfg)
    subject_log["columns_used"]["CS+_detection"] = cs_source

    cs_trials = [tr for tr in trials if tr["type"] == "CS+"]
    if not cs_trials:
        subject_log["skipped_analyses"].append("no CS+ trials detected")
        report_data["subject_logs"][subject_key] = subject_log
        report_data["exclusions"][subject_key] = "no CS+ trials detected"
        return [], report_data

    if cfg["use_shocker_column"]:
        try:
            us_windows, us_source = _get_us_windows_mode_a(df, time_col, cs_trials, cfg)
        except ValueError as e:
            subject_log["skipped_analyses"].append(f"column match error: {e}")
            report_data["subject_logs"][subject_key] = subject_log
            report_data["exclusions"][subject_key] = str(e)
            return [], report_data
        if us_windows is None:
            subject_log["skipped_analyses"].append(f"Mode A selected but: {us_source}")
            report_data["subject_logs"][subject_key] = subject_log
            report_data["exclusions"][subject_key] = f"Mode A: {us_source}"
            return [], report_data
    else:
        us_windows, us_source = _get_us_windows_mode_b(cs_trials, cfg["us_duration_s"])

    subject_log["columns_used"]["US_detection"] = us_source

    if not us_windows:
        subject_log["skipped_analyses"].append("no US windows detected")
        report_data["subject_logs"][subject_key] = subject_log
        report_data["exclusions"][subject_key] = "no US windows detected"
        return [], report_data

    # Success — record to run_report
    report_data["subjects"][subject_key] = {
        "columns_used": subject_log["columns_used"],
        "warnings": subject_log["warnings"],
        "skipped": subject_log["skipped_analyses"],
    }

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
            "_day_dir":                str(day_dir),
            "_source_csv":             csv_path.name,
            "us_number":               w["trial_index"],
            "us_start_s":              w["start"],
            "us_end_s":                w["end"],
            "platform_time_s":         plat_s,
            "platform_pct":            plat_pct,
            "platform_pct_above_chance": plat_pct - cfg["us_chance_baseline_pct"],
        })

    report_data["subject_logs"][subject_key] = subject_log
    return rows, report_data


def _process_file_star(args):
    return _process_file(*args)


# Worker processes return report payloads instead of mutating the shared Excel
# report object directly; the parent process records them safely here.
def _merge_report_data(report, report_data):
    if report is None:
        return
    for key, info in report_data["subjects"].items():
        rr.record_subject(report, "us_locked", key,
                          columns_used=info["columns_used"],
                          warnings=info["warnings"],
                          skipped=info["skipped"])
    for key, reason in report_data["exclusions"].items():
        rr.record_exclusion(report, "us_locked", key, reason)


def _collect_all_parallel(cfg, report=None):
    tasks = []
    task_bd = []

    for bd in cfg["behaviordata_dirs"]:
        bd_cfg = {**cfg, "behaviordata": bd}
        meta   = utils.load_metadata(bd)
        suffix = cfg["csv_suffix"]
        for day_dir in utils.find_session_dirs(bd):
            csvs = sorted(
                day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
            for csv_path in csvs:
                tasks.append((csv_path, meta, day_dir, bd_cfg))
                task_bd.append(bd)

    if not tasks:
        print("  [warn] No CSV files found.")
        return {}

    print(f"  Found {len(tasks)} CSV files. Processing with {cfg['n_workers']} workers...")

    day_results = {}
    with ProcessPoolExecutor(max_workers=cfg["n_workers"]) as pool:
        # Results complete out of order; keep each task's day/cohort context so
        # session CSVs and heatmaps are written to the right place.
        future_to_idx = {
            pool.submit(_process_file_star, t): i
            for i, t in enumerate(tasks)
        }
        for future in as_completed(future_to_idx):
            idx      = future_to_idx[future]
            csv_path = tasks[idx][0]
            day_dir  = tasks[idx][2]
            bd       = task_bd[idx]
            day_key  = str(day_dir)
            info = day_results.setdefault(day_key, {
                "bd":      bd,
                "day_dir": day_dir,
                "rows":    [],
                "cfg":     {**cfg, "behaviordata": bd},
            })

            try:
                rows, report_data = future.result()
            except Exception as exc:
                print(f"  [error] {csv_path.name} raised an exception: {exc}")
                continue

            info["rows"].extend(rows)
            _merge_report_data(report, report_data)

    return day_results


# ── Data collection ─────────────────────────────────────────────────────────

def _collect_day(day_dir, meta, cfg, report=None):
    suffix   = cfg["csv_suffix"]
    all_rows = []
    csvs = sorted(day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
    for csv_path in csvs:
        rows, report_data = _process_file(csv_path, meta, day_dir, cfg)
        _merge_report_data(report, report_data)
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

    out_dir.mkdir(parents=True, exist_ok=True)
    utils.save_fig(fig, out_dir / f"us_locked_heatmap_{treatment}_tiled.svg")


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

def _shock_avoidance(df, out_dir):
    group_cols = ["treatment_group", "cohort_id", "animal_id", "_day_folder"]
    # Start from every subject in this treatment/day, then merge avoided-shock
    # counts so animals with zero avoided shocks remain explicit rows.
    subjects = df[group_cols].drop_duplicates()
    avoided = df[df["platform_pct"] >= 100.0].copy()
    avoided_counts = (
        avoided.groupby(group_cols, dropna=False, as_index=False, sort=False)
        .agg(shocks_avoided=("us_number", "count"))
    )
    summary = subjects.merge(avoided_counts, on=group_cols, how="left", sort=False)
    summary["shocks_avoided"] = summary["shocks_avoided"].fillna(0).astype(int)
    out_path = out_dir / "us_locked_shock_avoidance.csv"
    summary.to_csv(out_path, index=False)
    print(f"[ok] Shock avoidance summary saved: {out_path}")


# ── Entry point ─────────────────────────────────────────────────────────────

def run(cfg, report=None):
    combined_root = Path(cfg["analysis_out"]) / "US_lock_plttime"
    combined_root.mkdir(parents=True, exist_ok=True)
    combined_rows = []

    print("  Collecting US-locked data in parallel...")
    day_results = _collect_all_parallel(cfg, report=report)

    ordered_days = sorted(
        day_results.values(),
        key=lambda info: (str(info["bd"]), utils.day_sort_key(info["day_dir"].name)),
    )

    for info in ordered_days:
        bd      = info["bd"]
        bd_cfg  = info["cfg"]
        day_dir = info["day_dir"]
        df_day  = pd.DataFrame(info["rows"]) if info["rows"] else pd.DataFrame()

        print(f"  Writing US-locked outputs for: {day_dir.name}")
        day_out = day_dir / "Analysis" / "US_lock_plttime"
        day_out.mkdir(parents=True, exist_ok=True)

        if df_day.empty:
            print(f"  [warn] No US-locked data found in {day_dir.name}. Skipping.")
            continue

        df_day.to_csv(day_out / "us_locked_all_days_concat.csv", index=False)
        print(f"  [ok] Session CSV saved: {day_out / 'us_locked_all_days_concat.csv'}")

        _shock_avoidance(df_day, day_out)

        for trt in cfg["canonical_groups"]:
            include = cfg.get("include_treatments")
            if include is not None and trt not in include:
                continue
            df_trt = df_day[df_day["treatment_group"] == trt]
            if df_trt.empty:
                continue
            df_trt_plot = _apply_plot_trial_caps(df_trt, bd_cfg)
            fig, _, _ = _make_heatmap(df_trt_plot, trt, day_out, bd_cfg,
                                      label_suffix=day_dir.name)
            if fig is not None:
                utils.save_fig(fig, day_out / f"us_locked_heatmap_{trt}.svg")
            if cfg.get("prism_export"):
                _prism_export(df_trt_plot, day_out, trt)

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
