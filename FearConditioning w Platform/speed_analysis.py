# -*- coding: utf-8 -*-
"""
speed_analysis.py
-----------------
CS-locked speed and distance analysis.

Pass 1 — Parallel, fully in-memory:
    Each CSV file is downsampled, CS-locked windows extracted, and distance
    computed independently on a worker process. No per-day CSVs are written.
    Per-animal line plot SVGs are still written per day (they are figures,
    not intermediate data).

Pass 2 — Per-cohort aggregation:
    Groups Pass 1 rows by treatment group and writes per-group Excel
    workbooks, a tiled 2x2 distance summary SVG, and a tiled dual-axis
    total-movement SVG into:
            <BehaviorData>/Analysis/<subfolder>/

Pass 3 — Cross-cohort aggregation:
    Combines per-group Excel workbooks across all BehaviorData folders and
    writes combined outputs, a cross-cohort tiled 2x2 distance SVG, and a
    tiled dual-axis total-movement SVG into:
            <ANALYSIS_OUTPUT_DIR>/<subfolder>/
"""

from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import utils
import run_report as rr


BIN_DURATION_S = 0.1


def _bin_columns(pre_bins: int, post_bins: int) -> list:
    return [f"Bin{t}" for t in range(-pre_bins, post_bins + 1)]


def _extract_cs_number(cs_type: str) -> float:
    import re
    m = re.search(r"(\d+)", str(cs_type))
    return int(m.group(1)) if m else float("inf")


def _sanitize_sheet(name: str) -> str:
    import re
    return re.sub(r"[\\/*?:\[\]]", "_", str(name))[:31]


def _parse_date(date_str) -> pd.Timestamp:
    try:
        return pd.to_datetime(str(date_str), format="%m-%d-%y")
    except Exception:
        return pd.Timestamp.max


def _compute_distance(bin_values: list, bin_duration_s: float = BIN_DURATION_S) -> float:
    arr   = np.array(bin_values, dtype=float)
    valid = arr[~np.isnan(arr)]
    if len(valid) == 0:
        return np.nan
    return float((valid * bin_duration_s).sum())


def _compute_interval_distance(df_ds: pd.DataFrame, time_col: str, speed_col: str,
                               start_s: float, end_s: float,
                               bin_duration_s: float = BIN_DURATION_S) -> float:
    if end_s <= start_s:
        return np.nan
    sub = df_ds[(df_ds[time_col] >= start_s) & (df_ds[time_col] < end_s)]
    if sub.empty:
        return np.nan
    return _compute_distance(sub[speed_col].tolist(), bin_duration_s)


# ── Downsampling / onset detection (unchanged) ────────────────────────────────

def _downsample(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    df = df.copy()
    df[time_col] = pd.to_timedelta(df[time_col].astype(float), unit="s")
    df = df.set_index(time_col)
    df_mean = df.resample("100ms").mean()
    df_mean = df_mean.reset_index()
    df_mean[time_col] = df_mean[time_col].dt.total_seconds()
    return df_mean


def _find_trial_onsets(df_ds: pd.DataFrame, time_col: str, cfg: dict) -> tuple:
    trials, itis, _ = utils.detect_trials(df_ds, time_col, cfg)
    t_arr = df_ds[time_col].astype(float).to_numpy()
    cs_plus_idx, cs_minus_idx, iti_idx = [], [], []
    for tr in trials:
        pos = int(np.argmin(np.abs(t_arr - tr["start"])))
        if tr["type"] == "CS+":
            cs_plus_idx.append(pos)
        elif tr["type"] == "CS-":
            cs_minus_idx.append(pos)
    for tr in itis:
        pos = int(np.argmin(np.abs(t_arr - tr["start"])))
        iti_idx.append(pos)
    return cs_plus_idx, cs_minus_idx, iti_idx


def _find_trial_windows(df_ds: pd.DataFrame, time_col: str, cfg: dict) -> tuple:
    trials, itis, _ = utils.detect_trials(df_ds, time_col, cfg)
    t_arr = df_ds[time_col].astype(float).to_numpy()
    cs_plus, cs_minus, iti_windows = [], [], []
    for tr in trials:
        pos = int(np.argmin(np.abs(t_arr - tr["start"])))
        if tr["type"] == "CS+":
            cs_plus.append((pos, tr))
        elif tr["type"] == "CS-":
            cs_minus.append((pos, tr))
    for tr in itis:
        pos = int(np.argmin(np.abs(t_arr - tr["start"])))
        iti_windows.append((pos, tr))
    return cs_plus, cs_minus, iti_windows


def _extract_window(df_ds: pd.DataFrame, speed_col: str,
                    onset_pos: int, pre_bins: int, post_bins: int) -> list:
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


# ── Pass 1: per-file processing ───────────────────────────────────────────────

def _process_file(csv_path: Path, meta, day_dir: Path, cfg: dict) -> tuple:
    report_data = {"subjects": {}, "exclusions": {}}
    subject_key = csv_path.name

    test_date, behavior_id = utils.parse_filename_bits(csv_path)
    if behavior_id is None:
        print(f"  [warn] Could not parse behavior_id from {csv_path.name}; skipping.")
        report_data["exclusions"][subject_key] = "could not parse behavior_id from filename"
        return [], report_data

    row_meta = utils.find_metadata_for_behavior(meta, behavior_id) \
        if meta is not None else pd.DataFrame()
    if row_meta.empty:
        print(f"  [warn] behavior_id '{behavior_id}' not in metadata; skipping {csv_path.name}.")
        report_data["exclusions"][subject_key] = \
            f"behavior_id '{behavior_id}' not found in metadata"
        return [], report_data

    row       = row_meta.iloc[0]
    animal_id = row.get("animal_id", behavior_id)
    treatment = utils.normalize_treatment(
        row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    cohort_id = row.get("cohort_id", None)

    df_raw = utils.load_csv(csv_path)

    try:
        time_col  = utils.find_time_col(df_raw, cfg)
        speed_col = utils.find_speed_col(df_raw, cfg)
    except ValueError as e:
        print(f"  [warn] {csv_path.name}: {e}; skipping.")
        report_data["exclusions"][subject_key] = str(e)
        return [], report_data

    df_raw = df_raw.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    df_ds  = _downsample(df_raw, time_col)

    cs_plus_windows, cs_minus_windows, iti_windows = _find_trial_windows(df_ds, time_col, cfg)

    if not cs_plus_windows and not cs_minus_windows:
        print(f"  [warn] No CS trials detected in {csv_path.name}; skipping.")
        report_data["exclusions"][subject_key] = "no CS trials detected"
        return [], report_data

    report_data["subjects"][subject_key] = {
        "columns_used": {"time": time_col, "speed": speed_col},
        "warnings":     [],
        "skipped":      [],
    }

    pre_bins  = cfg["speed_pre_bins"]
    post_bins = cfg["speed_post_bins"]
    bin_cols  = _bin_columns(pre_bins, post_bins)
    day, context, session = utils.parse_folder_bits(day_dir.name)
    session_distance_m = _compute_distance(df_ds[speed_col].tolist())

    rows = []

    def _make_row(count, onset_pos, trial_kind, cs_type_prefix, interval=None):
        vals         = _extract_window(df_ds, speed_col, onset_pos, pre_bins, post_bins)
        start_time   = float(df_ds.iloc[onset_pos][time_col])
        distance_m   = _compute_distance(vals)
        n_valid_bins = int(np.sum(~np.isnan(vals)))
        interval_start = float(interval["start"]) if interval else np.nan
        interval_end   = float(interval["end"])   if interval else np.nan
        interval_distance_m = _compute_interval_distance(
            df_ds, time_col, speed_col, interval_start, interval_end) if interval else np.nan
        r = dict(
            animal_id=animal_id, behavior_id=behavior_id,
            treatment_group=treatment, cohort_id=cohort_id,
            test_date=test_date, day=day, context=context,
            session_label=session, _day_folder=day_dir.name,
            _source_csv=csv_path.name, file_name=csv_path.name,
            start_time_s=start_time,
            cs_type=f"{cs_type_prefix}_{count}",
            trial_kind=trial_kind,
            distance_m=distance_m,
            interval_start_s=interval_start,
            interval_end_s=interval_end,
            interval_distance_m=interval_distance_m,
            session_distance_m=session_distance_m,
            n_valid_bins=n_valid_bins,
        )
        for col, v in zip(bin_cols, vals):
            r[col] = v
        return r

    for count, (pos, interval) in enumerate(cs_plus_windows,  start=1):
        rows.append(_make_row(count, pos, "CS+", "CS+", interval))
    for count, (pos, interval) in enumerate(cs_minus_windows, start=1):
        rows.append(_make_row(count, pos, "CS-", "CS-", interval))
    for count, (pos, interval) in enumerate(iti_windows,      start=1):
        rows.append(_make_row(count, pos, "ITI", "ITI", interval))

    return rows, report_data


def _process_file_star(args):
    return _process_file(*args)


# ── Pass 1 driver ─────────────────────────────────────────────────────────────

def _collect_all_parallel(cfg, report=None):
    """
    Walk all dirs, build tasks, run in parallel.
    Returns a dict: {bd_path: [rows]} so Pass 2 can still group by cohort.
    """
    tasks        = []
    task_bd_map  = []   # parallel list tracking which bd each task belongs to
    meta_cache   = {}

    for bd in cfg["behaviordata_dirs"]:
        meta           = utils.load_metadata(bd)
        meta_cache[bd] = meta
        suffix         = cfg["csv_suffix"]
        for day_dir in utils.find_session_dirs(bd):
            csvs = sorted(
                day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
            for csv_path in csvs:
                tasks.append((csv_path, meta, day_dir, cfg))
                task_bd_map.append(bd)

    if not tasks:
        print("  [warn] No CSV files found.")
        return {}

    print(f"  Found {len(tasks)} CSV files. Processing with {cfg['n_workers']} workers...")

    rows_by_bd = {bd: [] for bd in cfg["behaviordata_dirs"]}

    with ProcessPoolExecutor(max_workers=cfg["n_workers"]) as pool:
        future_to_idx = {
            pool.submit(_process_file_star, t): i
            for i, t in enumerate(tasks)
        }
        for future in as_completed(future_to_idx):
            idx      = future_to_idx[future]
            csv_path = tasks[idx][0]
            bd       = task_bd_map[idx]
            try:
                rows, report_data = future.result()
            except Exception as exc:
                print(f"  [error] {csv_path.name} raised an exception: {exc}")
                continue

            rows_by_bd[bd].extend(rows)

            if report is not None:
                for key, info in report_data["subjects"].items():
                    rr.record_subject(report, "speed", key,
                                      columns_used=info["columns_used"],
                                      warnings=info["warnings"],
                                      skipped=info["skipped"])
                for key, reason in report_data["exclusions"].items():
                    rr.record_exclusion(report, "speed", key, reason)

    # Still make per-day tiled figures (these are outputs, not intermediate data)
    _make_all_day_figures(tasks, rows_by_bd, cfg)

    return rows_by_bd


def _make_all_day_figures(tasks, rows_by_bd, cfg):
    """Group collected rows back by day_dir and draw figures."""
    # Build day_dir → rows mapping
    rows_by_day = {}
    for bd, bd_rows in rows_by_bd.items():
        for row in bd_rows:
            key = row["_day_folder"]
            rows_by_day.setdefault(key, []).append(row)

    # Map day_folder name → day_dir Path (from tasks)
    day_dir_map = {}
    for task in tasks:
        csv_path, _, day_dir, _ = task
        day_dir_map[day_dir.name] = day_dir

    subfolder = cfg["speed_subfolder"]
    for day_folder_name, day_rows in rows_by_day.items():
        day_dir    = day_dir_map.get(day_folder_name)
        if day_dir is None:
            continue
        graphs_dir = day_dir / "Analysis" / subfolder / "speed_graphs"
        cs_rows    = [r for r in day_rows if r["trial_kind"] in ("CS+", "CS-")]
        if cs_rows:
            _make_day_tiled_figures(cs_rows, graphs_dir, cfg)
        df_day = pd.DataFrame(day_rows)
        make_trial_distance_figure(
            df_day, cfg,
            out_path=graphs_dir / "distance_by_trial.svg",
            figure_title=f"Distance by Trial — {day_folder_name}",
        )


# ── Figures (unchanged) ───────────────────────────────────────────────────────

def _draw_single_animal_ax(ax, rows, bin_cols, time_points,
                            xtick_pos, xtick_labels, title):
    cmap = plt.get_cmap("tab20")
    for i, r in enumerate(rows):
        vals = [r.get(c, np.nan) for c in bin_cols]
        ax.plot(time_points, vals, color=cmap(i % 20),
                label=r["cs_type"], linewidth=1)
    ax.axvline(x=0,   color="grey",   linestyle="--", linewidth=1)
    ax.axvline(x=280, color="yellow", linestyle="--", linewidth=1)
    ax.axvline(x=300, color="grey",   linestyle="--", linewidth=1)
    ax.set_title(title, fontsize=8, pad=3)
    ax.set_xlabel("Time (s)", fontsize=7)
    ax.set_ylabel("Speed (m/s)", fontsize=7)
    ax.set_xticks(xtick_pos)
    ax.set_xticklabels(xtick_labels, fontsize=6)
    ax.tick_params(axis="y", labelsize=6)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles[:20], labels[:20], fontsize=5, loc="upper right",
                  title="Trial", title_fontsize=5)


def make_trial_distance_figure(df_day, cfg, out_path, figure_title=""):
    if df_day.empty:
        return
    colors = cfg.get("treatment_colors", {})
    groups = cfg.get("canonical_groups",
                     df_day["treatment_group"].dropna().unique().tolist())
    df = df_day.copy()
    df["distance_cm"] = df["distance_m"] * 100.0

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    (ax_tl, ax_tr), (ax_bl, ax_br) = axes

    for ax, kind, title in [
        (ax_tl, "CS+", "CS+ Trials"),
        (ax_tr, "CS-", "CS- Trials"),
        (ax_bl, "ITI", "ITI Windows"),
    ]:
        sub = df[df["trial_kind"] == kind].copy()
        if sub.empty:
            ax.set_visible(False); continue
        sub["trial_index"] = sub["cs_type"].apply(_extract_cs_number)
        per_animal = (sub.groupby(["animal_id","treatment_group","trial_index"])["distance_cm"]
                      .mean().reset_index())
        agg = (per_animal.groupby(["treatment_group","trial_index"])["distance_cm"]
               .agg(["mean","std","count"]).reset_index())
        agg["sem"] = agg["std"] / np.sqrt(agg["count"])
        for grp in groups:
            g = agg[agg["treatment_group"] == grp].sort_values("trial_index")
            if g.empty: continue
            color = colors.get(grp)
            ax.plot(g["trial_index"], g["mean"], marker="o", linewidth=2,
                    markersize=4, color=color, label=grp)
            ax.fill_between(g["trial_index"],
                            g["mean"] - g["sem"], g["mean"] + g["sem"],
                            alpha=0.18, linewidth=0, color=color)
        ax.set_title(title, fontsize=11, fontweight="bold")
        ax.set_xlabel("Trial #", fontsize=10)
        ax.set_ylabel("Distance (cm)", fontsize=10)
        ax.set_xticks(sorted(agg["trial_index"].dropna().unique()))
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    trial_order = (df.groupby("cs_type")["start_time_s"].median()
                   .sort_values().index.tolist())
    x_positions = {label: i + 1 for i, label in enumerate(trial_order)}
    df["x_pos"] = df["cs_type"].map(x_positions)
    per_animal_all = (df.groupby(["animal_id","treatment_group","x_pos","cs_type"])["distance_cm"]
                      .mean().reset_index())
    agg_all = (per_animal_all.groupby(["treatment_group","x_pos","cs_type"])["distance_cm"]
               .agg(["mean","std","count"]).reset_index())
    agg_all["sem"] = agg_all["std"] / np.sqrt(agg_all["count"])
    for grp in groups:
        g = agg_all[agg_all["treatment_group"] == grp].sort_values("x_pos")
        if g.empty: continue
        color = colors.get(grp)
        ax_br.plot(g["x_pos"], g["mean"], marker="o", linewidth=2,
                   markersize=4, color=color, label=grp)
        ax_br.fill_between(g["x_pos"],
                           g["mean"] - g["sem"], g["mean"] + g["sem"],
                           alpha=0.18, linewidth=0, color=color)
    ax_br.set_title("All Trials (ordered by onset time)", fontsize=11, fontweight="bold")
    ax_br.set_xlabel("Trial", fontsize=10)
    ax_br.set_ylabel("Distance (cm)", fontsize=10)
    ax_br.set_xticks(list(x_positions.values()))
    ax_br.set_xticklabels(trial_order, rotation=45, ha="right", fontsize=7)
    ax_br.spines["top"].set_visible(False)
    ax_br.spines["right"].set_visible(False)

    handles, labels = ax_tl.get_legend_handles_labels()
    if not handles:
        handles, labels = ax_br.get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper right", bbox_to_anchor=(1.0, 1.0),
                   frameon=False, fontsize=9, title="Treatment")
    if figure_title:
        fig.suptitle(figure_title, fontsize=13, fontweight="bold")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    utils.save_fig(fig, out_path)


def _make_day_tiled_figures(all_day_rows, graphs_dir, cfg):
    if not all_day_rows:
        return
    pre_bins    = cfg["speed_pre_bins"]
    post_bins   = cfg["speed_post_bins"]
    bin_cols    = _bin_columns(pre_bins, post_bins)
    time_points = list(range(-pre_bins, post_bins + 1))
    xtick_pos   = time_points[::100]
    xtick_labels = [f"{t * 0.1:.1f}" for t in xtick_pos]
    graphs_dir.mkdir(parents=True, exist_ok=True)

    animals_seen   = []
    rows_by_animal = {}
    for r in all_day_rows:
        aid = r["animal_id"]
        if aid not in rows_by_animal:
            animals_seen.append(aid)
            rows_by_animal[aid] = []
        rows_by_animal[aid].append(r)

    COLS = 3

    def _make_tiled(subset_fn, filename, suptitle):
        panels = [(aid, subset_fn(rows_by_animal[aid]))
                  for aid in animals_seen
                  if subset_fn(rows_by_animal[aid])]
        if not panels:
            return
        n = len(panels)
        ncols = min(n, COLS)
        nrows = int(np.ceil(n / ncols))
        fig, axes = plt.subplots(nrows, ncols,
                                  figsize=(5*ncols, 4*nrows), squeeze=False)
        for idx, (aid, rows) in enumerate(panels):
            r, c = divmod(idx, ncols)
            _draw_single_animal_ax(axes[r][c], rows, bin_cols, time_points,
                                   xtick_pos, xtick_labels, title=aid)
        for idx in range(len(panels), nrows * ncols):
            r, c = divmod(idx, ncols)
            axes[r][c].set_visible(False)
        fig.suptitle(suptitle, fontsize=10, fontweight="bold")
        fig.tight_layout()
        utils.save_fig(fig, graphs_dir / filename)

    _make_tiled(lambda rows: rows,
                "combined_CS_speed_tiled.svg", "Combined CS+/CS- Speed — All Animals")
    _make_tiled(lambda rows: [r for r in rows if r["trial_kind"] == "CS+"],
                "CSplus_speed_tiled.svg", "CS+ Speed — All Animals")
    _make_tiled(lambda rows: [r for r in rows if r["trial_kind"] == "CS-"],
                "CSminus_speed_tiled.svg", "CS- Speed — All Animals")


# ── Tiled 2x2 distance figure (unchanged) ────────────────────────────────────

def _assign_session_numbers(df):
    df = df.copy()
    session_num = pd.Series(np.nan, index=df.index, dtype=float)

    if "day" in df.columns:
        session_num = df["day"].apply(_extract_session_num)
    if "session_label" in df.columns:
        session_num = session_num.fillna(df["session_label"].apply(_extract_session_num))
    if session_num.isna().any() and "test_date" in df.columns:
        missing = session_num.isna()
        unique_dates = df.loc[missing, "test_date"].dropna().unique()
        date_order = sorted(unique_dates, key=_parse_date)
        date_to_num = {d: i+1 for i, d in enumerate(date_order)}
        session_num.loc[missing] = df.loc[missing, "test_date"].map(date_to_num)

    df["session_num"] = pd.to_numeric(session_num, errors="coerce")
    return df


def _group_mean_sem(df, value_col="distance_cm"):
    agg = (df.groupby(["treatment_group","session_num"])[value_col]
           .agg(["mean","std","count"]).reset_index().rename(columns={"count":"n"}))
    agg["sem"] = agg["std"].fillna(0.0) / np.sqrt(agg["n"])
    return agg


def _draw_panel(ax, agg, title, groups, colors):
    for grp in groups:
        sub = agg[agg["treatment_group"] == grp].sort_values("session_num")
        if sub.empty: continue
        color = colors.get(grp, None)
        ax.plot(sub["session_num"], sub["mean"], marker="o", linewidth=2,
                markersize=5, color=color, label=grp)
        ax.fill_between(sub["session_num"],
                        sub["mean"] - sub["sem"], sub["mean"] + sub["sem"],
                        alpha=0.18, linewidth=0, color=color)
    ax.set_xticks(range(1, 10))
    ax.set_xlim(0.75, 9.25)
    ax.set_xlabel("Session", fontsize=10)
    ax.set_ylabel("Distance (cm)", fontsize=10)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _extract_session_num(value):
    import re
    m = re.search(r"(\d+)", str(value))
    return float(m.group(1)) if m else np.nan


def _assign_movement_session_numbers(df):
    return _assign_session_numbers(df)


def _ordered_groups_with_data(df, cfg):
    present = df["treatment_group"].dropna().unique().tolist()
    configured = cfg.get("canonical_groups", present)
    ordered = [grp for grp in configured if grp in present]
    ordered.extend([grp for grp in present if grp not in ordered])
    return ordered


def _movement_mean_sem(df, value_col):
    agg = (df.groupby(["treatment_group","session_num"])[value_col]
           .agg(["mean","std","count"]).reset_index().rename(columns={"count":"n"}))
    agg["sem"] = agg["std"].fillna(0.0) / np.sqrt(agg["n"])
    return agg


def _movement_subject_session_cols(df):
    cols = []
    if "cohort_id" in df.columns and df["cohort_id"].notna().any():
        df["_movement_cohort_id"] = df["cohort_id"].fillna("__missing_cohort__")
        cols.append("_movement_cohort_id")
    cols.extend(["animal_id", "treatment_group", "session_num"])
    return cols


def make_total_movement_dual_axis_figure(df_all, cfg, out_path, figure_title=""):
    if df_all.empty:
        print(f"  [warn] No data for total movement dual-axis figure; skipping {out_path.name}.")
        return

    kinds = ["CS+", "CS-", "ITI"]
    kind_colors = {"CS+": "#1f77b4", "CS-": "#d62728", "ITI": "#2ca02c"}
    total_color = "#111111"

    df = _assign_movement_session_numbers(df_all.copy())
    df = df[df["trial_kind"].isin(kinds)].copy()
    df = df[df["session_num"].between(1, 9, inclusive="both")]

    use_actual_intervals = (
        "interval_distance_m" in df.columns
        and "session_distance_m" in df.columns
        and df["interval_distance_m"].notna().any()
        and df["session_distance_m"].notna().any()
    )
    if use_actual_intervals:
        df["movement_distance_cm"] = df["interval_distance_m"] * 100.0
        df["session_distance_cm"] = df["session_distance_m"] * 100.0
        pct_ylabel = "Percent of whole-session movement (%)"
        total_ylabel = "Whole-session distance (cm)"
    else:
        df["movement_distance_cm"] = df["distance_m"] * 100.0
        df["session_distance_cm"] = np.nan
        pct_ylabel = "Percent of window-summed movement (%)"
        total_ylabel = "Window-summed distance (cm)"

    df = df.dropna(subset=[
        "animal_id", "treatment_group", "session_num", "movement_distance_cm"])

    if df.empty:
        print(f"  [warn] No session 1-9 movement data; skipping {out_path.name}.")
        return

    group_cols = _movement_subject_session_cols(df)
    per_kind = (df.groupby(group_cols + ["trial_kind"], dropna=False)
                ["movement_distance_cm"].sum(min_count=1).reset_index())
    per_animal = (per_kind.pivot_table(
        index=group_cols,
        columns="trial_kind",
        values="movement_distance_cm",
        aggfunc="sum",
        fill_value=0)
        .reset_index())

    for kind in kinds:
        if kind not in per_animal.columns:
            per_animal[kind] = 0.0

    if use_actual_intervals:
        session_totals = (
            df.dropna(subset=["session_distance_cm"])
              .groupby(group_cols, dropna=False)["session_distance_cm"]
              .max()
              .reset_index()
        )
        per_animal = per_animal.merge(session_totals, on=group_cols, how="left")
        per_animal["total_distance_cm"] = per_animal["session_distance_cm"]
    else:
        per_animal["total_distance_cm"] = per_animal[kinds].sum(axis=1)

    per_animal = per_animal[per_animal["total_distance_cm"] > 0].copy()
    if per_animal.empty:
        print(f"  [warn] Total movement is zero or missing; skipping {out_path.name}.")
        return

    for kind in kinds:
        per_animal[f"{kind}_pct"] = per_animal[kind] / per_animal["total_distance_cm"] * 100.0

    total_agg = _movement_mean_sem(per_animal, "total_distance_cm")
    pct_long = per_animal.melt(
        id_vars=["animal_id","treatment_group","session_num"],
        value_vars=[f"{kind}_pct" for kind in kinds],
        var_name="trial_kind",
        value_name="percent_movement",
    )
    pct_long["trial_kind"] = pct_long["trial_kind"].str.replace("_pct", "", regex=False)
    pct_agg = (pct_long.groupby(["treatment_group","session_num","trial_kind"])
               ["percent_movement"].agg(["mean","std","count"]).reset_index())
    pct_agg["sem"] = pct_agg["std"].fillna(0.0) / np.sqrt(pct_agg["count"])

    groups = _ordered_groups_with_data(per_animal, cfg)
    if not groups:
        print(f"  [warn] No treatment groups found; skipping {out_path.name}.")
        return

    ncols = len(groups)
    fig, axes = plt.subplots(1, ncols, figsize=(4.8 * ncols, 4.2),
                             squeeze=False, constrained_layout=True)
    legend_handles = None
    legend_labels = None

    for idx, grp in enumerate(groups):
        ax_pct = axes[0][idx]
        ax_total = ax_pct.twinx()

        for kind in kinds:
            sub = pct_agg[(pct_agg["treatment_group"] == grp) &
                          (pct_agg["trial_kind"] == kind)].sort_values("session_num")
            if sub.empty:
                continue
            x = sub["session_num"].to_numpy(dtype=float)
            mean = sub["mean"].to_numpy(dtype=float)
            sem = sub["sem"].to_numpy(dtype=float)
            ax_pct.fill_between(x, mean - sem, mean + sem,
                                alpha=0.18, linewidth=0,
                                color=kind_colors[kind], zorder=1)
            ax_pct.plot(x, mean,
                        marker="o", linewidth=2, markersize=4,
                        color=kind_colors[kind], label=kind, zorder=2)

        total = total_agg[total_agg["treatment_group"] == grp].sort_values("session_num")
        if not total.empty:
            x = total["session_num"].to_numpy(dtype=float)
            mean = total["mean"].to_numpy(dtype=float)
            sem = total["sem"].to_numpy(dtype=float)
            ax_total.fill_between(x, mean - sem, mean + sem,
                                  alpha=0.16, linewidth=0,
                                  color=total_color, zorder=1)
            ax_total.plot(x, mean, marker="s", linewidth=2.2, markersize=4,
                          color=total_color, label="Total distance", zorder=2)

        ax_pct.set_title(grp, fontsize=11, fontweight="bold")
        ax_pct.set_xlabel("Session", fontsize=10)
        ax_pct.set_xticks(range(1, 10))
        ax_pct.set_xlim(0.75, 9.25)
        ax_pct.set_ylim(0, 100)
        ax_pct.set_ylabel(pct_ylabel, fontsize=10)
        ax_total.set_ylabel(total_ylabel, fontsize=10)
        ax_pct.spines["top"].set_visible(False)
        ax_total.spines["top"].set_visible(False)

        if legend_handles is None:
            handles_pct, labels_pct = ax_pct.get_legend_handles_labels()
            handles_total, labels_total = ax_total.get_legend_handles_labels()
            legend_handles = handles_pct + handles_total
            legend_labels = labels_pct + labels_total

    if legend_handles:
        fig.legend(legend_handles, legend_labels, loc="upper center",
                   bbox_to_anchor=(0.5, 1.06), ncol=min(4, len(legend_handles)),
                   frameon=False, fontsize=9)
    if figure_title:
        fig.suptitle(figure_title, fontsize=13, fontweight="bold")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    utils.save_fig(fig, out_path)


def make_tiled_distance_figure(df_all, cfg, out_path, figure_title=""):
    if df_all.empty:
        print(f"  [warn] No data for tiled distance figure; skipping {out_path.name}.")
        return
    colors = cfg.get("treatment_colors", {})
    groups = cfg.get("canonical_groups", df_all["treatment_group"].dropna().unique().tolist())
    kinds = ["CS+", "CS-", "ITI"]
    df     = _assign_session_numbers(df_all.copy())
    df["distance_cm"] = df["distance_m"] * 100.0
    df = df[df["trial_kind"].isin(kinds)].copy()
    df = df[df["session_num"].between(1, 9, inclusive="both")]
    df = df.dropna(subset=["animal_id", "treatment_group", "session_num", "distance_cm"])

    if df.empty:
        print(f"  [warn] No session 1-9 distance data; skipping {out_path.name}.")
        return

    per_kind = (df.groupby(["animal_id","treatment_group","session_num","trial_kind"])
                ["distance_cm"].mean().reset_index())
    per_animal = (per_kind.pivot_table(
        index=["animal_id","treatment_group","session_num"],
        columns="trial_kind",
        values="distance_cm",
        aggfunc="mean",
        fill_value=0)
        .reset_index())
    for kind in kinds:
        if kind not in per_animal.columns:
            per_animal[kind] = 0.0
    per_animal["total_mean_distance_cm"] = per_animal[kinds].sum(axis=1)
    agg_total = _group_mean_sem(per_animal, "total_mean_distance_cm")

    agg_by_kind = {}
    for kind in kinds:
        kind_df = per_animal[["animal_id","treatment_group","session_num", kind]].copy()
        kind_df = kind_df.rename(columns={kind: "distance_cm"})
        agg_by_kind[kind] = _group_mean_sem(kind_df, "distance_cm")

    fig, axes = plt.subplots(2, 2, figsize=(12, 9), constrained_layout=True)
    (ax_tl, ax_tr), (ax_bl, ax_br) = axes
    _draw_panel(ax_tl, agg_total,           "CS+/CS-/ITI Mean Distance Sum", groups, colors)
    _draw_panel(ax_tr, agg_by_kind["CS+"],  "Mean CS+ Window Distance",     groups, colors)
    _draw_panel(ax_bl, agg_by_kind["CS-"],  "Mean CS- Window Distance",     groups, colors)
    _draw_panel(ax_br, agg_by_kind["ITI"],  "Mean ITI Window Distance",     groups, colors)

    handles, labels = ax_tl.get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper right", bbox_to_anchor=(1.0, 1.0),
                   frameon=False, fontsize=9, title="Treatment")
    if figure_title:
        fig.suptitle(figure_title, fontsize=13, fontweight="bold")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    utils.save_fig(fig, out_path)


# ── Pass 2 — per-cohort Excel + tiled figure (unchanged logic) ────────────────

def _write_group_excel(df_all, out_path, bin_cols):
    if df_all.empty:
        return
    df_cs      = df_all[df_all["trial_kind"].isin(["CS+","CS-"])].copy()
    output_cols = [c for c in
                   ["file_name","animal_id","treatment_group","start_time_s","cs_type"] + bin_cols
                   if c in df_cs.columns]
    df_cs_plus  = df_cs[df_cs["cs_type"].str.contains(r"\+", na=False)].copy()
    df_cs_minus = df_cs[df_cs["cs_type"].str.contains(r"\-", na=False)].copy()
    df_cs_plus["_csn"]  = df_cs_plus["cs_type"].apply(_extract_cs_number)
    df_cs_minus["_csn"] = df_cs_minus["cs_type"].apply(_extract_cs_number)
    df_cs_plus  = df_cs_plus.sort_values("_csn").drop(columns="_csn")
    df_cs_minus = df_cs_minus.sort_values("_csn").drop(columns="_csn")
    unique_cs   = sorted(df_cs["cs_type"].dropna().unique(), key=_extract_cs_number)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_cs_plus[output_cols].to_excel(writer,  sheet_name="CS_Plus",  index=False)
        df_cs_minus[output_cols].to_excel(writer, sheet_name="CS_Minus", index=False)
        for cs_type in unique_cs:
            sub   = df_cs[df_cs["cs_type"] == cs_type][output_cols]
            sheet = _sanitize_sheet(cs_type)
            sub.to_excel(writer, sheet_name=sheet, index=False)
    print(f"  [ok] Excel saved: {out_path}")


def _write_distance_excel(df_all, out_path):
    if df_all.empty:
        return
    dist_cols = [c for c in
                 ["file_name","animal_id","treatment_group","start_time_s",
                  "cs_type","trial_kind","distance_m","n_valid_bins"]
                 if c in df_all.columns]
    df_plus   = df_all[df_all["trial_kind"] == "CS+"][dist_cols].copy()
    df_minus  = df_all[df_all["trial_kind"] == "CS-"][dist_cols].copy()
    df_iti    = df_all[df_all["trial_kind"] == "ITI"][dist_cols].copy()
    summary_rows = []
    for (animal, kind), grp in df_all.groupby(["animal_id","trial_kind"], sort=False):
        d = grp["distance_m"].dropna()
        summary_rows.append(dict(
            animal_id=animal, treatment_group=grp["treatment_group"].iloc[0],
            trial_kind=kind, n_trials=len(d),
            mean_distance_m=round(d.mean(), 6) if len(d) else np.nan,
            sd_distance_m=round(d.std(ddof=1), 6) if len(d) > 1 else np.nan,
        ))
    df_summary = pd.DataFrame(summary_rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_plus.to_excel(writer,    sheet_name="CS_Plus_distance",  index=False)
        df_minus.to_excel(writer,   sheet_name="CS_Minus_distance", index=False)
        df_iti.to_excel(writer,     sheet_name="ITI_distance",      index=False)
        df_summary.to_excel(writer, sheet_name="Summary",           index=False)
    print(f"  [ok] Distance Excel saved: {out_path}")


def _write_cohort_outputs(bd, cfg, cohort_rows):
    subfolder = cfg["speed_subfolder"]
    bin_cols  = _bin_columns(cfg["speed_pre_bins"], cfg["speed_post_bins"])
    df_all    = pd.DataFrame(cohort_rows) if cohort_rows else pd.DataFrame()
    if df_all.empty:
        print(f"  [warn] No speed data found for cohort in {bd.name}; skipping.")
        return
    out_dir = bd / "Analysis" / subfolder
    out_dir.mkdir(parents=True, exist_ok=True)
    for group in cfg["canonical_groups"]:
        df_grp = df_all[df_all["treatment_group"] == group]
        if df_grp.empty: continue
        _write_group_excel(df_grp, out_dir / f"{group}_combined_output.xlsx", bin_cols)
        _write_distance_excel(df_grp, out_dir / f"{group}_distance_output.xlsx")
    make_tiled_distance_figure(df_all, cfg,
                                out_path=out_dir / "distance_across_sessions.svg",
                                figure_title=f"Distance Summary — {bd.name}")
    make_total_movement_dual_axis_figure(
        df_all, cfg,
        out_path=out_dir / "percent_total_movement_dual_axis.svg",
        figure_title=f"Total Movement Summary — {bd.name}",
    )


# ── Pass 3 — cross-cohort (unchanged logic) ───────────────────────────────────

def _write_combined_outputs(cfg):
    subfolder = cfg["speed_subfolder"]
    out_dir   = cfg["analysis_out"] / subfolder
    out_dir.mkdir(parents=True, exist_ok=True)
    for group in cfg["canonical_groups"]:
        sheet_data = {}
        for bd in cfg["behaviordata_dirs"]:
            excel_path = bd / "Analysis" / subfolder / f"{group}_combined_output.xlsx"
            if not excel_path.exists():
                print(f"  [warn] Missing cohort Excel: {excel_path}; skipping.")
                continue
            xls = pd.ExcelFile(excel_path)
            for sheet_name in xls.sheet_names:
                df = xls.parse(sheet_name)
                df["_source_cohort"] = bd.name
                sheet_data.setdefault(sheet_name, []).append(df)
        if not sheet_data: continue
        out_path = out_dir / f"{group}_combined_output.xlsx"
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            for sheet_name, dfs in sheet_data.items():
                pd.concat(dfs, ignore_index=True).to_excel(
                    writer, sheet_name=_sanitize_sheet(sheet_name), index=False)
        print(f"  [ok] Cross-cohort Excel saved: {out_path}")


def _write_combined_distance_outputs(cfg):
    subfolder = cfg["speed_subfolder"]
    out_dir   = cfg["analysis_out"] / subfolder
    out_dir.mkdir(parents=True, exist_ok=True)
    for group in cfg["canonical_groups"]:
        sheet_data = {}
        for bd in cfg["behaviordata_dirs"]:
            excel_path = bd / "Analysis" / subfolder / f"{group}_distance_output.xlsx"
            if not excel_path.exists():
                print(f"  [warn] Missing distance Excel: {excel_path}; skipping.")
                continue
            xls = pd.ExcelFile(excel_path)
            for sheet_name in xls.sheet_names:
                df = xls.parse(sheet_name)
                df["_source_cohort"] = bd.name
                sheet_data.setdefault(sheet_name, []).append(df)
        if not sheet_data: continue
        out_path = out_dir / f"{group}_distance_output.xlsx"
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            for sheet_name, dfs in sheet_data.items():
                pd.concat(dfs, ignore_index=True).to_excel(
                    writer, sheet_name=_sanitize_sheet(sheet_name), index=False)
        print(f"  [ok] Cross-cohort distance Excel saved: {out_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def run(cfg: dict, report=None):
    print("  Pass 1: collecting all CSVs in parallel...")
    rows_by_bd = _collect_all_parallel(cfg, report=report)

    print("  Pass 2: aggregating within each cohort...")
    for bd in cfg["behaviordata_dirs"]:
        _write_cohort_outputs(bd, cfg, rows_by_bd.get(bd, []))

    print("  Pass 3: combining across cohorts...")
    _write_combined_outputs(cfg)
    _write_combined_distance_outputs(cfg)

    all_rows = [r for rows in rows_by_bd.values() for r in rows]
    subfolder = cfg["speed_subfolder"]
    out_dir   = cfg["analysis_out"] / subfolder
    make_tiled_distance_figure(
        pd.DataFrame(all_rows) if all_rows else pd.DataFrame(),
        cfg,
        out_path=out_dir / "distance_across_sessions_combined.svg",
        figure_title="Distance Summary — All Cohorts Combined",
    )
    make_total_movement_dual_axis_figure(
        pd.DataFrame(all_rows) if all_rows else pd.DataFrame(),
        cfg,
        out_path=out_dir / "percent_total_movement_dual_axis_combined.svg",
        figure_title="Total Movement Summary — All Cohorts Combined",
    )

    print("  Speed analysis complete.")
