# -*- coding: utf-8 -*-
"""
HMM_analysis.py
---------------
Walks BehaviorData directories, downsamples each session to 100ms bins,
engineers motion features from x,y positions, and writes one clean CSV
ready for HMM fitting.

Output columns
--------------
subject, behavior_id, treatment, cohort_id, day, context, session,
time_s, x, y, freezing, in_platform,
displacement_per_100ms, abs_delta_displacement_per_100ms,
turning_angle, dist_from_centre
"""

import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

import utils

warnings.filterwarnings("ignore")


# =============================================================================
# ── CONFIGURATION ─────────────────────────────────────────────────────────────
# =============================================================================

BEHAVIORDATA_DIRS = [
    #r"Z:\...\BehaviorData",
    r"Z:\NIMH DIRP NSI\Projects\PFC Ketamine\Behavior\Fear Conditioning\Early Life Stress Cohort 1\test"
]

OUTPUT_CSV = r"Z:\NIMH DIRP NSI\Projects\PFC Ketamine\Behavior\Fear Conditioning\Analysis\HMM movement\hmm_input.csv"
WRITE_PARQUET = True # additional parquet file for better data handling

TREATMENT_ALIASES = {
    "ctrl": ["Control", "ctrl"],
    "ELS":  ["ELS", "LBN"],
}

# Add every column name variant that appears in your AnyMaze exports.
COLUMN_ALIASES = {
    "time":        ["Time (s)", "Time"],
    "freezing":    ["Freezing", "Freezing state"],
    "in_platform": ["In platform", "Inside platform"],
    "x_pos":       ["X centre", "X center", "X", "Centre position X"],
    "y_pos":       ["Y centre", "Y center", "Y", "Centre position Y"],
}
COLUMN_MATCH_MODE = "strict"

CSV_SUFFIX = ""       # e.g. "_fixed" to restrict; "" processes all .csv files

# Arena centre for dist_from_centre (same units as your x,y columns).
# Leave as None to auto-compute from the midpoint of the x/y range in your data.
# Set explicitly if you know the pixel coordinates, e.g. 320.0 / 240.0.
ARENA_CENTER_X = None
ARENA_CENTER_Y = None

N_WORKERS = 5         # parallel file-loading workers; set to 1 to disable

# ── DO NOT EDIT BELOW THIS LINE ───────────────────────────────────────────────


# =============================================================================
# ── HELPERS ───────────────────────────────────────────────────────────────────
# =============================================================================

BIN_S = 0.1   # 0.1 == 100ms per bin


def _build_cfg() -> dict:
    return dict(
        behaviordata_dirs = [Path(p) for p in BEHAVIORDATA_DIRS],
        output_csv        = Path(OUTPUT_CSV),
        treatment_lookup  = utils.build_treatment_normalizer(TREATMENT_ALIASES),
        column_aliases    = COLUMN_ALIASES,
        column_match_mode = COLUMN_MATCH_MODE,
        csv_suffix        = CSV_SUFFIX,
        arena_center_x    = ARENA_CENTER_X,
        arena_center_y    = ARENA_CENTER_Y,
    )


def _downsample(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    """Resample all columns to 100ms bins by mean. Identical to speed_analysis._downsample."""
    df = df.copy()
    df[time_col] = pd.to_timedelta(df[time_col].astype(float), unit="s")
    df = df.set_index(time_col)
    resampler = df.resample("100ms")
    ds = resampler.mean(numeric_only=True)
    nonnumeric_cols = [c for c in df.columns if c not in ds.columns]
    if nonnumeric_cols:
        ds = ds.join(resampler[nonnumeric_cols].first())
    ds = ds.reset_index()
    ds[time_col] = ds[time_col].dt.total_seconds()
    return ds


def _find_pos_col(df: pd.DataFrame, axis: str, cfg: dict) -> str:
    """Resolve x or y position column using the same alias system as utils."""
    candidates = {
        axis,
        f"{axis}_centre", f"{axis}_center",
        f"{axis}_pos",    f"pos_{axis}",
        f"{axis}_nose",   f"{axis}_body",
    }
    return utils.resolve_column(
        df, f"{axis}_pos", cfg,
        fallback=lambda cols: [c for c in cols if c in candidates],
        required=True,
    )


# =============================================================================
# ── PER-FILE PROCESSING ───────────────────────────────────────────────────────
# =============================================================================

def _process_file(csv_path: Path, meta, day_dir: Path, cfg: dict):
    """
    Load one raw CSV, downsample to 100ms, return a tidy DataFrame.
    Returns None if the file should be skipped.
    Called from ProcessPoolExecutor workers — all args must be picklable.
    """
    # ── Filename → behavior_id ───────────────────────────────────────────────
    _, behavior_id = utils.parse_filename_bits(csv_path, meta)
    if behavior_id is None:
        print(f"  [skip] Cannot parse behavior_id: {csv_path.name}")
        return None

    # ── Metadata lookup ──────────────────────────────────────────────────────
    row_meta = (
        utils.find_metadata_for_behavior(meta, behavior_id)
        if meta is not None else pd.DataFrame()
    )
    if row_meta.empty:
        print(f"  [skip] '{behavior_id}' not in metadata: {csv_path.name}")
        return None

    m         = row_meta.iloc[0]
    animal_id = str(m.get("animal_id", behavior_id))
    treatment = utils.normalize_treatment(
        m.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    cohort_id = str(m.get("cohort_id", ""))

    day_str, context, session_label = utils.parse_folder_bits(day_dir.name)

    # ── Load and sort ────────────────────────────────────────────────────────
    df_raw = utils.load_csv(csv_path)

    try:
        time_col = utils.find_time_col(df_raw, cfg)
        x_col    = _find_pos_col(df_raw, "x", cfg)
        y_col    = _find_pos_col(df_raw, "y", cfg)
    except ValueError as e:
        print(f"  [skip] {csv_path.name}: {e}")
        return None

    # Behavioral annotations are optional — not all sessions have them
    try:
        freeze_col = utils.find_freeze_col(df_raw, cfg)
    except ValueError:
        freeze_col = None

    try:
        platform_col = utils.find_platform_col(df_raw, cfg)
    except ValueError:
        platform_col = None

    df_raw = (df_raw
              .dropna(subset=[time_col])
              .sort_values(time_col)
              .reset_index(drop=True))

    # ── Select only needed columns before downsampling ───────────────────────
    keep = [time_col, x_col, y_col]
    if freeze_col:
        keep.append(freeze_col)
    if platform_col:
        keep.append(platform_col)
    df_raw = df_raw[keep].copy()

    # Normalise time to start at 0 within this session
    df_raw[time_col] = df_raw[time_col].astype(float) - df_raw[time_col].astype(float).iloc[0]

    # ── Downsample to 100ms ──────────────────────────────────────────────────
    ds = _downsample(df_raw, time_col)
    ds = ds.dropna(subset=[x_col, y_col])

    if ds.empty:
        print(f"  [skip] No valid position data after downsampling: {csv_path.name}")
        return None

    # Round booleans back to 0/1 (majority-vote within each 100ms bin)
    if freeze_col and freeze_col in ds.columns:
        ds[freeze_col] = ds[freeze_col].round().fillna(0).astype(int)
    if platform_col and platform_col in ds.columns:
        ds[platform_col] = ds[platform_col].round().fillna(0).astype(int)

    # Rebuild time_s as a clean 0.1s-step index — no floating-point drift
    ds["time_s"] = np.arange(len(ds)) * BIN_S

    # ── Assemble output ──────────────────────────────────────────────────────
    out = pd.DataFrame({
        "subject":     animal_id,
        "behavior_id": behavior_id,
        "treatment":   treatment,
        "cohort_id":   cohort_id,
        "day":         day_str,
        "context":     context,
        "session":     session_label,
        "time_s":      ds["time_s"].values,
        "x":           ds[x_col].values,
        "y":           ds[y_col].values,
        "freezing":    ds[freeze_col].values   if freeze_col   in ds.columns else np.nan,
        "in_platform": ds[platform_col].values if platform_col in ds.columns else np.nan,
    })

    return out


# =============================================================================
# ── COLLECT ALL SESSIONS ──────────────────────────────────────────────────────
# =============================================================================

def collect_all_sessions(cfg: dict) -> pd.DataFrame:
    tasks = []
    for bd in cfg["behaviordata_dirs"]:
        meta   = utils.load_metadata(bd)
        suffix = cfg["csv_suffix"]
        for day_dir in utils.find_session_dirs(bd):
            csvs = sorted(
                day_dir.glob(f"*{suffix}.csv") if suffix
                else day_dir.glob("*.csv")
            )
            for csv_path in csvs:
                tasks.append((csv_path, meta, day_dir))

    print(f"[collect] {len(tasks)} CSV files found.")

    frames = []
    if N_WORKERS > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=N_WORKERS) as pool:
            futures = {
                pool.submit(_process_file, csv, meta, dd, cfg): csv
                for csv, meta, dd in tasks
            }
            for fut in as_completed(futures):
                try:
                    result = fut.result()
                except Exception as exc:
                    print(f"  [error] {futures[fut].name}: {exc}")
                    result = None
                if result is not None:
                    frames.append(result)
    else:
        for csv, meta, dd in tasks:
            result = _process_file(csv, meta, dd, cfg)
            if result is not None:
                frames.append(result)

    if not frames:
        raise RuntimeError(
            "No data collected. Check BEHAVIORDATA_DIRS, CSV_SUFFIX, "
            "and that animals_metadata.xlsx is present."
        )

    df = (pd.concat(frames, ignore_index=True)
            .sort_values(["subject", "day", "session", "time_s"], kind="mergesort")
            .reset_index(drop=True))

    # Forward-fill brief NaN gaps in x,y within each session (dropped tracking frames)
    # groupby().ffill/bfill is vectorized -- faster than transform(lambda)
    df["x"] = df.groupby(["subject", "day", "session"])["x"].ffill().bfill()
    df["y"] = df.groupby(["subject", "day", "session"])["y"].ffill().bfill()

    n_subjects = df["subject"].nunique()
    n_sessions = df.groupby(["subject", "day", "session"]).ngroups
    print(f"[collect] {len(df):,} rows | {n_subjects} subjects | {n_sessions} sessions")

    return df


# =============================================================================
# ── FEATURE ENGINEERING ───────────────────────────────────────────────────────
# =============================================================================

def engineer_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Compute motion features from x,y within each session.
    No values are ever computed across session boundaries.

    Fully vectorized -- no Python loop over sessions. All diff-based features
    are computed on the full array at once; a boolean boundary mask zeros out
    values that would otherwise bleed across session edges.

    displacement_per_100ms           sqrt(dx^2 + dy^2) per 100ms bin --
                                      Pythagorean magnitude of the displacement
                                      vector. This is not velocity per second.
    abs_delta_displacement_per_100ms |delta displacement_per_100ms| from the
                                      previous bin. This is not acceleration
                                      per second squared.
    turning_angle                    |delta heading| in radians, wrapped to
                                      [0, pi].
    dist_from_centre                 Euclidean distance from arena centre.
    """
    # df must be sorted by (subject, day, session, time_s) -- guaranteed by collect_all_sessions
    df = df.copy()

    # -- Boundary mask: True at the first row of every new session ------------
    boundary = (
        (df["subject"] != df["subject"].shift()) |
        (df["day"]     != df["day"].shift())     |
        (df["session"] != df["session"].shift())
    ).to_numpy()

    x = df["x"].to_numpy(dtype=float)
    y = df["y"].to_numpy(dtype=float)

    # -- Displacement per bin (zeroed at boundaries) --------------------------
    dx = np.empty_like(x); dx[0] = 0.0; dx[1:] = np.diff(x)
    dy = np.empty_like(y); dy[0] = 0.0; dy[1:] = np.diff(y)
    dx[boundary] = 0.0
    dy[boundary] = 0.0

    # -- Displacement per 100ms bin: Pythagorean magnitude of (dx, dy) --------
    displacement_per_100ms = np.sqrt(dx**2 + dy**2)

    # -- Absolute change in displacement from the previous 100ms bin ----------
    abs_delta_displacement = np.empty_like(displacement_per_100ms)
    abs_delta_displacement[0] = 0.0
    abs_delta_displacement[1:] = np.abs(np.diff(displacement_per_100ms))
    abs_delta_displacement[boundary] = 0.0

    # -- Turning angle: |delta heading|, wrapped to [0, pi] ------------------
    heading = np.arctan2(dy, dx)
    delta   = np.empty_like(heading); delta[0] = 0.0; delta[1:] = np.abs(np.diff(heading))
    delta[boundary] = 0.0
    turning_angle = np.where(delta > np.pi, np.pi - (delta % np.pi), delta)

    df["displacement_per_100ms"]           = displacement_per_100ms
    df["abs_delta_displacement_per_100ms"] = abs_delta_displacement
    df["turning_angle"]                    = turning_angle

    # -- Distance from arena centre (per-session when auto-computed) ----------
    fixed_cx = cfg["arena_center_x"]
    fixed_cy = cfg["arena_center_y"]

    if fixed_cx is not None:
        df["dist_from_centre"] = np.sqrt((x - fixed_cx)**2 + (y - fixed_cy)**2)
    else:
        # Single aggregation + merge -- faster than transform(lambda) for large DataFrames
        centres = (
            df.groupby(["subject", "day", "session"])[["x", "y"]]
            .agg(x_min=("x", "min"), x_max=("x", "max"),
                 y_min=("y", "min"), y_max=("y", "max"))
            .assign(cx=lambda t: (t["x_min"] + t["x_max"]) / 2,
                    cy=lambda t: (t["y_min"] + t["y_max"]) / 2)
            [["cx", "cy"]]
        )
        df = df.join(centres, on=["subject", "day", "session"])
        df["dist_from_centre"] = np.sqrt(
            (df["x"] - df["cx"])**2 + (df["y"] - df["cy"])**2
        )
        df = df.drop(columns=["cx", "cy"])

    return df

# =============================================================================
# ── MAIN ──────────────────────────────────────────────────────────────────────
# =============================================================================

def main():
    cfg = _build_cfg()

    df = collect_all_sessions(cfg)
    df = engineer_features(df, cfg)

    out_path = cfg["output_csv"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"[done] {len(df):,} rows written to: {out_path}")

    if WRITE_PARQUET:
        parquet_path = out_path.with_suffix(".parquet")
        df.to_parquet(parquet_path, index=False)
        print(f"[done] Parquet also written: {parquet_path}")

    # Quick sanity print
    print(f"\nColumns:  {df.columns.tolist()}")
    print(f"\nSession row counts:\n{df.groupby(['subject', 'day', 'session']).size().to_string()}")


if __name__ == "__main__":
    main()
