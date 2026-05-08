# -*- coding: utf-8 -*-
"""
speed_analysis.py
-----------------
CS-locked speed and distance analysis.

Pass 1 — Per-file, per-day:
    Loads raw AnyMaze CSVs, downsamples to 100ms bins, detects CS+, CS-, and
    ITI trial onsets, extracts a configurable pre/post window of speed values
    around each onset, computes distance travelled per trial window, keeps those
    rows in memory for later passes, and writes:
      - Per-animal line plot SVGs (combined, CS+, CS-) into:
            <day_dir>/Analysis/<subfolder>/speed_graphs/

Pass 2 — Per-cohort aggregation:
    Groups the Pass 1 rows by treatment group and writes per-group Excel
    workbooks and a tiled 2x2 distance summary SVG into:
            <BehaviorData>/Analysis/<subfolder>/

Pass 3 — Cross-cohort aggregation:
    Combines per-group Excel workbooks across all BehaviorData folders and
    writes combined outputs and a cross-cohort tiled 2x2 distance SVG into:
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

BIN_DURATION_S = 0.1   # 100 ms bins throughout


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
    """Convert MM-DD-YY string (from parse_filename_bits) to Timestamp for sorting."""
    try:
        return pd.to_datetime(str(date_str), format="%m-%d-%y")
    except Exception:
        return pd.Timestamp.max   # push unparseable dates to the end


def _compute_distance(bin_values: list, bin_duration_s: float = BIN_DURATION_S) -> float:
    """
    Total distance (metres) for one CS-locked window.

    Uses speed x Δt rather than raw X/Y to preserve AnyMaze's adaptive
    smoothing. NaN bins are excluded (not filled with 0), consistent with
    AnyMaze's own distance calculation for untracked frames.

    Parameters
    ----------
    bin_values : list of float | NaN
        Speed values (m/s) for one trial window, one value per 100 ms bin.
    bin_duration_s : float
        Duration of each bin in seconds. Default 0.1 (100 ms).

    Returns
    -------
    float
        Total distance in metres, or NaN if all bins are NaN.
    """
    arr = np.array(bin_values, dtype=float)
    valid = arr[~np.isnan(arr)]
    if len(valid) == 0:
        return np.nan
    return float((valid * bin_duration_s).sum())


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
    df_mean[time_col] = df_mean[time_col].dt.total_seconds()
    return df_mean


def _find_trial_onsets(df_ds: pd.DataFrame, time_col: str, cfg: dict) -> tuple:
    """
    Detect CS+, CS-, and ITI onset row-indices in the downsampled DataFrame.

    Uses detect_trials() on the downsampled data, then maps each detected
    trial start time to the nearest row index. Returns three lists of integer
    positional indices (not DataFrame index labels).
    """
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
    Returns a list of row dicts (one per trial) for downstream outputs.
    Includes CS+, CS-, and ITI trials.
    """
    test_date, behavior_id = utils.parse_filename_bits(csv_path)
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

    # Detect onsets in downsampled data (CS+, CS-, ITI)
    cs_plus_idx, cs_minus_idx, iti_idx = _find_trial_onsets(df_ds, time_col, cfg)

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

    # --- CS+ trials ---
    for count, onset_pos in enumerate(cs_plus_idx, start=1):
        vals = _extract_window(df_ds, speed_col, onset_pos, pre_bins, post_bins)
        start_time   = float(df_ds.iloc[onset_pos][time_col])
        distance_m   = _compute_distance(vals)
        n_valid_bins = int(np.sum(~np.isnan(vals)))
        row_d = dict(
            animal_id=animal_id, behavior_id=behavior_id,
            treatment_group=treatment, cohort_id=cohort_id,
            test_date=test_date, day=day, context=context,
            session_label=session, _day_folder=day_dir.name,
            _source_csv=csv_path.name,
            file_name=csv_path.name,
            start_time_s=start_time,
            cs_type=f"CS+_{count}",
            trial_kind="CS+",
            distance_m=distance_m,
            n_valid_bins=n_valid_bins,
        )
        for col, v in zip(bin_cols, vals):
            row_d[col] = v
        rows.append(row_d)

    # --- CS- trials ---
    for count, onset_pos in enumerate(cs_minus_idx, start=1):
        vals = _extract_window(df_ds, speed_col, onset_pos, pre_bins, post_bins)
        start_time   = float(df_ds.iloc[onset_pos][time_col])
        distance_m   = _compute_distance(vals)
        n_valid_bins = int(np.sum(~np.isnan(vals)))
        row_d = dict(
            animal_id=animal_id, behavior_id=behavior_id,
            treatment_group=treatment, cohort_id=cohort_id,
            test_date=test_date, day=day, context=context,
            session_label=session, _day_folder=day_dir.name,
            _source_csv=csv_path.name,
            file_name=csv_path.name,
            start_time_s=start_time,
            cs_type=f"CS-_{count}",
            trial_kind="CS-",
            distance_m=distance_m,
            n_valid_bins=n_valid_bins,
        )
        for col, v in zip(bin_cols, vals):
            row_d[col] = v
        rows.append(row_d)

    # --- ITI windows ---
    for count, onset_pos in enumerate(iti_idx, start=1):
        vals = _extract_window(df_ds, speed_col, onset_pos, pre_bins, post_bins)
        start_time   = float(df_ds.iloc[onset_pos][time_col])
        distance_m   = _compute_distance(vals)
        n_valid_bins = int(np.sum(~np.isnan(vals)))
        row_d = dict(
            animal_id=animal_id, behavior_id=behavior_id,
            treatment_group=treatment, cohort_id=cohort_id,
            test_date=test_date, day=day, context=context,
            session_label=session, _day_folder=day_dir.name,
            _source_csv=csv_path.name,
            file_name=csv_path.name,
            start_time_s=start_time,
            cs_type=f"ITI_{count}",
            trial_kind="ITI",
            distance_m=distance_m,
            n_valid_bins=n_valid_bins,
        )
        for col, v in zip(bin_cols, vals):
            row_d[col] = v
        rows.append(row_d)

    return rows


# =============================================================================
# Pass 1 — figures
# =============================================================================

def _draw_single_animal_ax(ax: plt.Axes, rows: list, bin_cols: list,
                            time_points: list, xtick_pos: list,
                            xtick_labels: list, title: str):
    """Draw one animal's CS trial lines onto an existing Axes."""
    cmap = plt.get_cmap("tab20")
    for i, r in enumerate(rows):
        vals = [r.get(c, np.nan) for c in bin_cols]
        ax.plot(time_points, vals,
                color=cmap(i % 20), label=r["cs_type"], linewidth=1)
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
        ax.legend(handles[:20], labels[:20],
                  fontsize=5, loc="upper right", title="Trial", title_fontsize=5)


def make_trial_distance_figure(df_day: pd.DataFrame, cfg: dict, out_path: Path,
                                figure_title: str = ""):
    """
    2x2 tiled figure: distance (cm) by trial index for one day/session.

    Panels
    ------
    TL — CS+ trials:   x = CS+ trial index (1, 2, …), one line per treatment
    TR — CS- trials:   x = CS- trial index (1, 2, …), one line per treatment
    BL — ITI windows:  x = ITI trial index (1, 2, …), one line per treatment
    BR — All trials:   x = trial label ordered by start_time_s (CS+_1, ITI_1,
                       CS-_1, …), one line per treatment
    """
    if df_day.empty:
        return

    colors = cfg.get("treatment_colors", {})
    groups = cfg.get("canonical_groups",
                     df_day["treatment_group"].dropna().unique().tolist())

    df = df_day.copy()
    df["distance_cm"] = df["distance_m"] * 100.0

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    (ax_tl, ax_tr), (ax_bl, ax_br) = axes

    # ── Panels TL / TR / BL: per-trial-type, x = index within that type ─────
    for ax, kind, title in [
        (ax_tl, "CS+", "CS+ Trials"),
        (ax_tr, "CS-", "CS- Trials"),
        (ax_bl, "ITI", "ITI Windows"),
    ]:
        sub = df[df["trial_kind"] == kind].copy()
        if sub.empty:
            ax.set_visible(False)
            continue

        # Within-day trial index from cs_type number (CS+_1 → 1, etc.)
        sub["trial_index"] = sub["cs_type"].apply(_extract_cs_number)

        # Per-animal mean at each trial index (handles multiple animals)
        per_animal = (
            sub.groupby(["animal_id", "treatment_group", "trial_index"])["distance_cm"]
            .mean().reset_index()
        )
        agg = (
            per_animal.groupby(["treatment_group", "trial_index"])["distance_cm"]
            .agg(["mean", "std", "count"]).reset_index()
        )
        agg["sem"] = agg["std"] / np.sqrt(agg["count"])

        for grp in groups:
            g = agg[agg["treatment_group"] == grp].sort_values("trial_index")
            if g.empty:
                continue
            color = colors.get(grp)
            ax.plot(g["trial_index"], g["mean"], marker="o", linewidth=2,
                    markersize=4, color=color, label=grp)
            ax.fill_between(g["trial_index"],
                            g["mean"] - g["sem"],
                            g["mean"] + g["sem"],
                            alpha=0.18, linewidth=0, color=color)

        ax.set_title(title, fontsize=11, fontweight="bold")
        ax.set_xlabel("Trial #", fontsize=10)
        ax.set_ylabel("Distance (cm)", fontsize=10)
        ticks = sorted(agg["trial_index"].dropna().unique())
        ax.set_xticks(ticks)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # ── Panel BR: all trials ordered by start_time_s ─────────────────────────
    # Build a canonical trial order from the group-level median start times
    # so the x-axis labels are consistent across animals.
    trial_order = (
        df.groupby("cs_type")["start_time_s"]
        .median()
        .sort_values()
        .index.tolist()
    )
    x_positions = {label: i + 1 for i, label in enumerate(trial_order)}

    df["x_pos"] = df["cs_type"].map(x_positions)

    per_animal_all = (
        df.groupby(["animal_id", "treatment_group", "x_pos", "cs_type"])["distance_cm"]
        .mean().reset_index()
    )
    agg_all = (
        per_animal_all.groupby(["treatment_group", "x_pos", "cs_type"])["distance_cm"]
        .agg(["mean", "std", "count"]).reset_index()
    )
    agg_all["sem"] = agg_all["std"] / np.sqrt(agg_all["count"])
    agg_all = agg_all.sort_values("x_pos")

    for grp in groups:
        g = agg_all[agg_all["treatment_group"] == grp].sort_values("x_pos")
        if g.empty:
            continue
        color = colors.get(grp)
        ax_br.plot(g["x_pos"], g["mean"], marker="o", linewidth=2,
                   markersize=4, color=color, label=grp)
        ax_br.fill_between(g["x_pos"],
                           g["mean"] - g["sem"],
                           g["mean"] + g["sem"],
                           alpha=0.18, linewidth=0, color=color)

    ax_br.set_title("All Trials (ordered by onset time)", fontsize=11, fontweight="bold")
    ax_br.set_xlabel("Trial", fontsize=10)
    ax_br.set_ylabel("Distance (cm)", fontsize=10)
    ax_br.set_xticks(list(x_positions.values()))
    ax_br.set_xticklabels(trial_order, rotation=45, ha="right", fontsize=7)
    ax_br.spines["top"].set_visible(False)
    ax_br.spines["right"].set_visible(False)

    # Shared legend
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


def _make_day_tiled_figures(all_day_rows: list, graphs_dir: Path, cfg: dict):
    """
    Produce three tiled SVGs for an entire day — one per plot type:
      combined_CS_speed_tiled.svg  — CS+ and CS- together, one panel per animal
      CSplus_speed_tiled.svg       — CS+ only, one panel per animal
      CSminus_speed_tiled.svg      — CS- only, one panel per animal

    Panels are arranged 3 per row, as many rows as needed.
    Each panel is labelled with the animal_id.
    """
    if not all_day_rows:
        return

    pre_bins    = cfg["speed_pre_bins"]
    post_bins   = cfg["speed_post_bins"]
    bin_cols    = _bin_columns(pre_bins, post_bins)
    time_points = list(range(-pre_bins, post_bins + 1))
    xtick_pos   = time_points[::100]
    xtick_labels = [f"{t * 0.1:.1f}" for t in xtick_pos]

    graphs_dir.mkdir(parents=True, exist_ok=True)

    # Group rows by animal_id, preserving first-seen order
    animals_seen = []
    rows_by_animal = {}
    for r in all_day_rows:
        aid = r["animal_id"]
        if aid not in rows_by_animal:
            animals_seen.append(aid)
            rows_by_animal[aid] = []
        rows_by_animal[aid].append(r)

    COLS = 3

    def _make_tiled(subset_fn, filename, suptitle):
        # Build per-animal subset; skip animals with no data for this type
        panels = []
        for aid in animals_seen:
            rows = subset_fn(rows_by_animal[aid])
            if rows:
                panels.append((aid, rows))
        if not panels:
            return

        n     = len(panels)
        ncols = min(n, COLS)
        nrows = int(np.ceil(n / ncols))

        fig, axes = plt.subplots(
            nrows, ncols,
            figsize=(5 * ncols, 4 * nrows),
            squeeze=False,
        )

        for idx, (aid, rows) in enumerate(panels):
            r, c = divmod(idx, ncols)
            _draw_single_animal_ax(
                axes[r][c], rows, bin_cols, time_points,
                xtick_pos, xtick_labels, title=aid,
            )

        # Hide any unused axes in the last row
        for idx in range(len(panels), nrows * ncols):
            r, c = divmod(idx, ncols)
            axes[r][c].set_visible(False)

        fig.suptitle(suptitle, fontsize=10, fontweight="bold")
        fig.tight_layout()
        utils.save_fig(fig, graphs_dir / filename)

    _make_tiled(
        lambda rows: rows,
        "combined_CS_speed_tiled.svg",
        "Combined CS+/CS- Speed — All Animals",
    )
    _make_tiled(
        lambda rows: [r for r in rows if r["trial_kind"] == "CS+"],
        "CSplus_speed_tiled.svg",
        "CS+ Speed — All Animals",
    )
    _make_tiled(
        lambda rows: [r for r in rows if r["trial_kind"] == "CS-"],
        "CSminus_speed_tiled.svg",
        "CS- Speed — All Animals",
    )


# =============================================================================
# Pass 1 — day-level driver
# =============================================================================

def _process_day(day_dir: Path, meta, cfg: dict, report=None) -> list:
    """
    Process all CSVs in one session day folder.
    Writes per-animal graphs.
    Returns all row dicts for this day (used by Pass 2).
    """
    suffix    = cfg["csv_suffix"]
    subfolder = cfg["speed_subfolder"]
    csvs      = sorted(
        day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
    if not csvs:
        return []

    graphs_dir = day_dir / "Analysis" / subfolder / "speed_graphs"

    all_day_rows = []

    for csv_path in csvs:
        rows = _process_file(csv_path, meta, day_dir, cfg, report=report)
        if rows:
            all_day_rows.extend(rows)

    if all_day_rows:
        # Speed line plot tile (CS+ and CS- only, no ITI)
        cs_rows = [r for r in all_day_rows if r["trial_kind"] in ("CS+", "CS-")]
        if cs_rows:
            _make_day_tiled_figures(cs_rows, graphs_dir, cfg)

        # Distance by trial tile (all trial types)
        df_day = pd.DataFrame(all_day_rows)
        day_label = day_dir.name
        make_trial_distance_figure(
            df_day, cfg,
            out_path=graphs_dir / "distance_by_trial.svg",
            figure_title=f"Distance by Trial — {day_label}",
        )

    return all_day_rows


# =============================================================================
# Tiled 2x2 distance figure  (shared by Pass 2 and Pass 3)
# =============================================================================

def _assign_session_numbers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a numeric `session_num` column (1, 2, 3, …) by ranking unique
    test_date values in chronological order. Dates are parsed from the
    MM-DD-YY strings produced by parse_filename_bits().
    """
    df = df.copy()
    unique_dates = df["test_date"].dropna().unique()
    date_order = sorted(unique_dates, key=_parse_date)
    date_to_num = {d: i + 1 for i, d in enumerate(date_order)}
    df["session_num"] = df["test_date"].map(date_to_num)
    return df


def _group_mean_sem(df: pd.DataFrame, value_col: str = "distance_cm") -> pd.DataFrame:
    """
    Aggregate value_col by (treatment_group, session_num).
    Returns a DataFrame with columns: treatment_group, session_num,
    mean, sem, n.
    """
    agg = (
        df.groupby(["treatment_group", "session_num"])[value_col]
        .agg(["mean", "std", "count"])
        .reset_index()
        .rename(columns={"count": "n"})
    )
    agg["sem"] = agg["std"] / np.sqrt(agg["n"])
    return agg


def _draw_panel(ax: plt.Axes, agg: pd.DataFrame, title: str,
                groups: list, colors: dict):
    """Draw one mean±SEM line per treatment group on ax."""
    for grp in groups:
        sub = agg[agg["treatment_group"] == grp].sort_values("session_num")
        if sub.empty:
            continue
        color = colors.get(grp, None)
        ax.plot(sub["session_num"], sub["mean"],
                marker="o", linewidth=2, markersize=5,
                color=color, label=grp)
        ax.fill_between(
            sub["session_num"],
            sub["mean"] - sub["sem"],
            sub["mean"] + sub["sem"],
            alpha=0.18, linewidth=0, color=color,
        )

    # Integer x-ticks only
    all_sessions = sorted(agg["session_num"].dropna().unique())
    ax.set_xticks(all_sessions)
    ax.set_xlabel("Session", fontsize=10)
    ax.set_ylabel("Distance (cm)", fontsize=10)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def make_tiled_distance_figure(df_all: pd.DataFrame, cfg: dict,
                                out_path: Path, figure_title: str = ""):
    """
    Build and save the 2x2 tiled distance summary figure.

    Panel layout
    ------------
    [TL] Total session distance (all trial types summed) — one line per group
    [TR] Mean per-trial distance — CS+ trials
    [BL] Mean per-trial distance — CS- trials
    [BR] Mean per-trial distance — ITI windows

    Parameters
    ----------
    df_all   : DataFrame containing all rows (CS+, CS-, ITI) with distance_m
               and test_date columns.
    cfg      : pipeline config dict (for treatment_colors, canonical_groups).
    out_path : SVG file path to write.
    figure_title : optional suptitle string.
    """
    if df_all.empty:
        print(f"  [warn] No data for tiled distance figure; skipping {out_path.name}.")
        return

    colors = cfg.get("treatment_colors", {})
    groups = cfg.get("canonical_groups", df_all["treatment_group"].dropna().unique().tolist())

    # Convert distance to cm for all downstream work
    df = _assign_session_numbers(df_all.copy())
    df["distance_cm"] = df["distance_m"] * 100.0

    # ── Panel TL: total session distance per animal then group mean ──────────
    total = (
        df.groupby(["animal_id", "treatment_group", "session_num"])["distance_cm"]
        .sum()
        .reset_index()
    )
    agg_total = _group_mean_sem(total, "distance_cm")

    # ── Panels TR / BL / BR: mean per-trial distance by trial_kind ──────────
    agg_by_kind = {}
    for kind in ("CS+", "CS-", "ITI"):
        sub = df[df["trial_kind"] == kind]
        if sub.empty:
            agg_by_kind[kind] = pd.DataFrame()
            continue
        # Per-animal mean across trials within each session
        per_animal = (
            sub.groupby(["animal_id", "treatment_group", "session_num"])["distance_cm"]
            .mean()
            .reset_index()
        )
        agg_by_kind[kind] = _group_mean_sem(per_animal, "distance_cm")

    # ── Build figure ─────────────────────────────────────────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(12, 9), constrained_layout=True)
    (ax_tl, ax_tr), (ax_bl, ax_br) = axes

    _draw_panel(ax_tl, agg_total,           "Total Session Distance",  groups, colors)
    _draw_panel(ax_tr, agg_by_kind["CS+"],  "Mean CS+ Trial Distance", groups, colors)
    _draw_panel(ax_bl, agg_by_kind["CS-"],  "Mean CS- Trial Distance", groups, colors)
    _draw_panel(ax_br, agg_by_kind["ITI"],  "Mean ITI Distance",       groups, colors)

    # Shared legend on the right of the figure
    handles, labels = ax_tl.get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels,
                   loc="upper right", bbox_to_anchor=(1.0, 1.0),
                   frameon=False, fontsize=9, title="Treatment")

    if figure_title:
        fig.suptitle(figure_title, fontsize=13, fontweight="bold")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    utils.save_fig(fig, out_path)


# =============================================================================
# Pass 2 — per-cohort Excel (all days within one BehaviorData folder)
# =============================================================================

def _collect_cohort_rows(cohort_rows: list) -> pd.DataFrame:
    """Build one BehaviorData dataframe from Pass 1 rows produced in this run."""
    return pd.DataFrame(cohort_rows) if cohort_rows else pd.DataFrame()


def _write_group_excel(df_all: pd.DataFrame, out_path: Path, bin_cols: list):
    """
    Write one Excel workbook organised as:
      CS_Plus  — all CS+ trials
      CS_Minus — all CS- trials
      One sheet per unique CS type label (CS+_1, CS+_2, CS-_1, …)
    ITI rows are excluded from the speed bin workbook (they go in distance only).
    """
    if df_all.empty:
        return

    # Exclude ITI from the speed-bin workbook
    df_cs = df_all[df_all["trial_kind"].isin(["CS+", "CS-"])].copy()

    output_cols = ["file_name", "animal_id", "treatment_group",
                   "start_time_s", "cs_type"] + bin_cols
    output_cols = [c for c in output_cols if c in df_cs.columns]

    df_cs_plus  = df_cs[df_cs["cs_type"].str.contains(r"\+", na=False)].copy()
    df_cs_minus = df_cs[df_cs["cs_type"].str.contains(r"\-", na=False)].copy()

    df_cs_plus["_csn"]  = df_cs_plus["cs_type"].apply(_extract_cs_number)
    df_cs_minus["_csn"] = df_cs_minus["cs_type"].apply(_extract_cs_number)
    df_cs_plus  = df_cs_plus.sort_values("_csn").drop(columns="_csn")
    df_cs_minus = df_cs_minus.sort_values("_csn").drop(columns="_csn")

    unique_cs = sorted(df_cs["cs_type"].dropna().unique(), key=_extract_cs_number)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_cs_plus[output_cols].to_excel(writer,  sheet_name="CS_Plus",  index=False)
        df_cs_minus[output_cols].to_excel(writer, sheet_name="CS_Minus", index=False)
        for cs_type in unique_cs:
            sub = df_cs[df_cs["cs_type"] == cs_type][output_cols]
            sheet = _sanitize_sheet(cs_type)
            sub.to_excel(writer, sheet_name=sheet, index=False)

    print(f"  [ok] Excel saved: {out_path}")


def _write_distance_excel(df_all: pd.DataFrame, out_path: Path):
    """
    Write a compact distance-summary Excel workbook.

    Sheets
    ------
    CS_Plus_distance  — one row per CS+ trial
    CS_Minus_distance — one row per CS- trial
    ITI_distance      — one row per ITI window
    Summary           — per-animal mean ± SD distance, split by trial_kind
    """
    if df_all.empty:
        return

    dist_cols = ["file_name", "animal_id", "treatment_group",
                 "start_time_s", "cs_type", "trial_kind",
                 "distance_m", "n_valid_bins"]
    dist_cols = [c for c in dist_cols if c in df_all.columns]

    df_plus  = df_all[df_all["trial_kind"] == "CS+"][dist_cols].copy()
    df_minus = df_all[df_all["trial_kind"] == "CS-"][dist_cols].copy()
    df_iti   = df_all[df_all["trial_kind"] == "ITI"][dist_cols].copy()

    summary_rows = []
    for (animal, kind), grp in df_all.groupby(["animal_id", "trial_kind"], sort=False):
        d = grp["distance_m"].dropna()
        summary_rows.append(dict(
            animal_id=animal,
            treatment_group=grp["treatment_group"].iloc[0],
            trial_kind=kind,
            n_trials=len(d),
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


def _write_cohort_outputs(bd: Path, cfg: dict, cohort_rows: list):
    """Pass 2: aggregate all days in one BehaviorData dir → per-group Excel + tiled SVG."""
    subfolder = cfg["speed_subfolder"]
    bin_cols  = _bin_columns(cfg["speed_pre_bins"], cfg["speed_post_bins"])
    df_all    = _collect_cohort_rows(cohort_rows)
    if df_all.empty:
        print(f"  [warn] No speed data found for cohort in {bd.name}; skipping.")
        return

    out_dir = bd / "Analysis" / subfolder
    out_dir.mkdir(parents=True, exist_ok=True)

    # Per-group Excel outputs
    for group in cfg["canonical_groups"]:
        df_grp = df_all[df_all["treatment_group"] == group]
        if df_grp.empty:
            continue
        _write_group_excel(df_grp, out_dir / f"{group}_combined_output.xlsx", bin_cols)
        _write_distance_excel(df_grp, out_dir / f"{group}_distance_output.xlsx")

    # Tiled 2x2 distance figure — all groups together
    cohort_label = bd.name
    make_tiled_distance_figure(
        df_all, cfg,
        out_path=out_dir / "distance_across_sessions.svg",
        figure_title=f"Distance Summary — {cohort_label}",
    )


# =============================================================================
# Pass 3 — cross-cohort aggregation → ANALYSIS_OUTPUT_DIR
# =============================================================================

def _write_combined_outputs(cfg: dict):
    """
    Pass 3: combine per-group Excels across all BehaviorData dirs and write
    to cfg["analysis_out"] / subfolder.
    """
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


def _write_combined_distance_outputs(cfg: dict):
    """
    Pass 3 extension: combine per-group distance Excels across all
    BehaviorData dirs → cfg["analysis_out"] / subfolder.
    """
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
            print(f"  Reading distance: {excel_path}")
            xls = pd.ExcelFile(excel_path)
            for sheet_name in xls.sheet_names:
                df = xls.parse(sheet_name)
                df["_source_cohort"] = bd.name
                sheet_data.setdefault(sheet_name, []).append(df)

        if not sheet_data:
            continue

        out_path = out_dir / f"{group}_distance_output.xlsx"
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            for sheet_name, dfs in sheet_data.items():
                combined = pd.concat(dfs, ignore_index=True)
                combined.to_excel(writer,
                                  sheet_name=_sanitize_sheet(sheet_name),
                                  index=False)
        print(f"  [ok] Cross-cohort distance Excel saved: {out_path}")


def _write_combined_tiled_figure(cfg: dict, all_cohort_rows: list):
    """
    Pass 3 extension: build the cross-cohort tiled distance figure from
    the combined rows of all BehaviorData dirs.
    """
    subfolder = cfg["speed_subfolder"]
    out_dir   = cfg["analysis_out"] / subfolder
    df_all    = pd.DataFrame(all_cohort_rows) if all_cohort_rows else pd.DataFrame()

    make_tiled_distance_figure(
        df_all, cfg,
        out_path=out_dir / "distance_across_sessions_combined.svg",
        figure_title="Distance Summary — All Cohorts Combined",
    )


# =============================================================================
# Entry point
# =============================================================================

def run(cfg: dict, report=None):
    cohort_rows_by_bd = {}

    # Pass 1 — per file, per day
    print("  Pass 1: downsampling and extracting CS-locked speed windows...")
    for bd in cfg["behaviordata_dirs"]:
        cohort_rows = []
        meta = utils.load_metadata(bd)
        for day_dir in utils.find_session_dirs(bd):
            print(f"  Processing day: {day_dir.name}")
            cohort_rows.extend(_process_day(day_dir, meta, cfg, report=report))
        cohort_rows_by_bd[bd] = cohort_rows

    # Pass 2 — per-cohort Excel + tiled figure
    print("  Pass 2: aggregating within each cohort...")
    for bd in cfg["behaviordata_dirs"]:
        _write_cohort_outputs(bd, cfg, cohort_rows_by_bd.get(bd, []))

    # Pass 3 — cross-cohort combined outputs + tiled figure
    print("  Pass 3: combining across cohorts...")
    _write_combined_outputs(cfg)
    _write_combined_distance_outputs(cfg)

    all_rows = [r for rows in cohort_rows_by_bd.values() for r in rows]
    _write_combined_tiled_figure(cfg, all_rows)

    print("  Speed analysis complete.")