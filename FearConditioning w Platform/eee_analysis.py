# -*- coding: utf-8 -*-
"""
eee.py
------
Classifies CS+ shock trials as Evade, Escape, or Endure for all session days.
Produces per-animal CSVs, a concatenated CSV, and tiled stacked bar figures.
All configuration comes from runner.py.

Definitions:
  Evade  — on platform for the ENTIRE US (shock) window
  Escape — off platform at US onset, reaches platform BEFORE US ends
  Endure — off platform for the ENTIRE US window
"""

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import utils


US_DEFAULT_LEN_S  = 2.0      # assumed US duration if US-OFF TTL is absent
PLATFORM_FULL_TOL = 1e-6     # tolerance for "entire window" checks

OUTCOME_ORDER  = ["evade", "escape", "endure"]
OUTCOME_COLORS = {
    "evade":  "#2ca02c",
    "escape": "#1f77b4",
    "endure": "#ff7f0e",
}


# =============================================================================
# Outcome classification helpers
# =============================================================================

def _first_rising_after(df: pd.DataFrame, time_col: str,
                         ttl_col: str, t0: float):
    """Index of first row >= t0 where ttl_col > 0; else None."""
    if ttl_col is None or ttl_col not in df.columns:
        return None
    sub = df[df[time_col] >= t0]
    idx = sub[ttl_col].fillna(0).astype(float).to_numpy().nonzero()[0]
    return int(sub.index[idx[0]]) if len(idx) > 0 else None


def _find_us_cols(df: pd.DataFrame) -> tuple:
    cols = df.columns.tolist()

    def like(words):
        import re
        pat = re.compile(r".*".join(re.compile(w).pattern for w in words))
        matches = [c for c in cols if pat.search(c)]
        return matches[0] if matches else None

    us_on  = (like(["us", "on", "activat"]) or like(["shock", "on", "activat"])
               or like(["us", "on"]) or like(["shock", "on"]))
    us_off = (like(["us", "off", "activat"]) or like(["shock", "off", "activat"])
               or like(["us", "off"]) or like(["shock", "off"]))
    return us_on, us_off


def _classify_outcome(t: np.ndarray, platform: np.ndarray,
                       us_start: float, us_end: float) -> str:
    """
    Classify a single CS+ trial as evade / escape / endure.
    """
    frac_on = utils.interval_fraction_on(t, platform, us_start, us_end)
    full_on  = (1.0 - frac_on) <= PLATFORM_FULL_TOL
    full_off = frac_on <= PLATFORM_FULL_TOL

    if full_on:
        return "evade"
    if full_off:
        return "endure"

    # Check for a platform-entry transition during the US window
    plat_bin = (platform > 0).astype(int)
    re_ix    = np.where((np.append(0, plat_bin[:-1]) == 0) & (plat_bin == 1))[0]
    if re_ix.size > 0:
        re_times = t[re_ix]
        if np.any((re_times > us_start) & (re_times < us_end)):
            return "escape"

    return "escape"   # partial on-platform still counts as escape


# =============================================================================
# Per-file processing
# =============================================================================

def _process_file(csv_path: Path, meta: pd.DataFrame,
                   day_dir: Path, cfg: dict) -> list:
    """
    Classify every CS+ trial in one animal's raw CSV.
    Returns a list of row dicts (one per CS+ trial).
    """
    test_date, behavior_id = utils.parse_filename_bits(csv_path)
    if behavior_id is None:
        print(f"  [warn] Could not parse behavior_id from {csv_path.name}; skipping.")
        return []

    row_meta = utils.find_metadata_for_behavior(meta, behavior_id) if meta is not None else pd.DataFrame()
    if row_meta.empty:
        print(f"  [warn] behavior_id '{behavior_id}' not in metadata; skipping {csv_path.name}.")
        return []

    row         = row_meta.iloc[0]
    animal_id   = row.get("animal_id", behavior_id)
    treatment   = utils.normalize_treatment(row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    sex         = utils.normalize_sex(row.get("sex", None))

    df = utils.load_csv(csv_path)
    try:
        time_col = utils.find_time_col(df)
    except ValueError as e:
        print(f"  [warn] {csv_path.name}: {e}; skipping.")
        return []

    if "in_platform" not in df.columns:
        print(f"  [warn] {csv_path.name}: no 'in_platform' column; skipping.")
        return []

    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)

    trials, _, cs_source = utils.detect_trials(df, time_col, cfg)
    us_on, us_off = _find_us_cols(df)

    day, context, session = utils.parse_folder_bits(day_dir.name)

    t    = df[time_col].astype(float).to_numpy()
    plat = df["in_platform"].fillna(0).astype(float).to_numpy()

    rows = []
    for tr in [tr for tr in trials if tr["type"] == "CS+"]:
        if tr["trial_index"] > cfg["eee_trial_cap"]:
            continue

        us_on_ix = _first_rising_after(df, time_col, us_on, tr["start"])
        if us_on_ix is None:
            outcome = "no_us"
            us_start = us_end = np.nan
        else:
            us_start = float(df.loc[us_on_ix, time_col])
            if us_start >= tr["end"]:
                outcome = "no_us"
                us_end  = np.nan
            else:
                us_end = utils.get_off_time(df, time_col, us_off, us_start, US_DEFAULT_LEN_S)
                us_s, us_e = utils.clip_interval(us_start, us_end, tr["start"], tr["end"])
                outcome = _classify_outcome(t, plat, us_s, us_e)

        rows.append(dict(
            animal_id=animal_id, behavior_id=behavior_id,
            treatment_group=treatment, sex=sex,
            test_date=test_date, day=day, context=context,
            session_label=session, _day_folder=day_dir.name,
            _source_csv=csv_path.name,
            trial_type="CS+", trial_index=tr["trial_index"],
            trial_start_s=tr["start"], trial_end_s=tr["end"],
            us_start_s=us_start, us_end_s=us_end,
            outcome=outcome,
        ))

    return rows


# =============================================================================
# Data collection
# =============================================================================

def _collect_all(cfg: dict, meta: pd.DataFrame) -> pd.DataFrame:
    behaviordata = cfg["behaviordata"]
    suffix       = cfg["csv_suffix"]

    # EEE works on raw session CSVs directly (not a subfolder output).
    # Each day folder contains the raw AnyMaze CSVs at its top level.
    all_rows = []
    for day_dir in utils.find_session_dirs(behaviordata):
        csvs = sorted(
            day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv")
        )
        if not csvs:
            continue
        for csv_path in csvs:
            rows = _process_file(csv_path, meta, day_dir, cfg)
            all_rows.extend(rows)

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


# =============================================================================
# Per-animal proportions
# =============================================================================

def _per_animal_props(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each animal × day, compute % of CS+ trials that are evade/escape/endure.
    Only uses trials with a valid US window (excludes 'no_us').
    """
    valid = df[df["outcome"].isin(OUTCOME_ORDER)].copy()
    if valid.empty:
        return pd.DataFrame()

    def props(g):
        n = len(g)
        return pd.Series({
            "evade_pct":  100.0 * g["outcome"].eq("evade").sum()  / n,
            "escape_pct": 100.0 * g["outcome"].eq("escape").sum() / n,
            "endure_pct": 100.0 * g["outcome"].eq("endure").sum() / n,
            "n_trials":   n,
        })

    return (
        valid.groupby(
            ["_day_folder", "day", "context", "session_label",
             "treatment_group", "sex", "animal_id"],
            as_index=False,
        )
        .apply(props)
        .reset_index(drop=True)
    )


def _group_means(per_animal: pd.DataFrame, by_sex: bool = False) -> pd.DataFrame:
    """Average per-animal proportions within each group."""
    if per_animal.empty:
        return pd.DataFrame()

    group_cols = ["_day_folder", "day", "context", "session_label", "treatment_group"]
    if by_sex:
        group_cols.append("sex")

    return (
        per_animal.groupby(group_cols, as_index=False)
        .agg(
            evade_pct=("evade_pct",  "mean"),
            escape_pct=("escape_pct", "mean"),
            endure_pct=("endure_pct", "mean"),
            n_animals=("animal_id",   "nunique"),
        )
    )


# =============================================================================
# Stacked bar figures
# =============================================================================

def _stacked_bar_panel(ax: plt.Axes, group_means: pd.DataFrame,
                        x_keys: list, x_labels: list):
    """Draw stacked evade/escape/endure bars on a single axes panel."""
    evade  = np.array([group_means.get(k, {}).get("evade_pct",  0.0) for k in x_keys])
    escape = np.array([group_means.get(k, {}).get("escape_pct", 0.0) for k in x_keys])
    endure = np.array([group_means.get(k, {}).get("endure_pct", 0.0) for k in x_keys])
    ns     = [group_means.get(k, {}).get("n_animals", 0) for k in x_keys]

    x    = np.arange(len(x_keys))
    bar_w = 0.7
    ax.bar(x, evade,  width=bar_w, color=OUTCOME_COLORS["evade"])
    ax.bar(x, escape, width=bar_w, bottom=evade, color=OUTCOME_COLORS["escape"])
    ax.bar(x, endure, width=bar_w, bottom=evade + escape, color=OUTCOME_COLORS["endure"])

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, fontsize=9, linespacing=1.2)
    ax.set_ylim(0, 100)
    ax.grid(True, axis="y", alpha=0.3)
    ax.margins(x=0.15)

    for xi, n in enumerate(ns):
        if n > 0:
            ax.text(xi, 101.5, f"n={n}", ha="center", va="bottom", fontsize=8)


def _make_stacked_bars(group_df: pd.DataFrame, out_dir: Path,
                        cfg: dict, by_sex: bool, fname_tag: str):
    """
    Tiled stacked bar figure: one panel per day folder.
    x-groups = treatment (× sex if by_sex=True).
    """
    if group_df.empty:
        return

    day_order  = sorted(group_df["_day_folder"].unique(), key=utils.day_sort_key)
    groups     = cfg["canonical_groups"]
    SEX_ORDER  = ["M", "F", "Unknown"]

    n_cols = len(day_order)
    panel_w = 3.8
    fig_w   = min(22, panel_w * max(1, n_cols))
    fig, axes = plt.subplots(1, n_cols, figsize=(fig_w, 4.5), squeeze=False)
    axes = axes[0]

    title_suffix = "by Sex × Treatment" if by_sex else "by Treatment"
    fig.suptitle(f"CS+ Outcomes — Evade / Escape / Endure ({title_suffix})",
                 fontsize=12, y=0.92)
    fig.subplots_adjust(left=0.06, right=0.99, bottom=0.18, top=0.78, wspace=0.55)

    legend_patches = [
        plt.Line2D([0], [0], color=OUTCOME_COLORS[o], lw=8, label=o.capitalize())
        for o in OUTCOME_ORDER
    ]
    fig.legend(handles=legend_patches, loc="upper center", ncol=3,
               frameon=False, fontsize=10, bbox_to_anchor=(0.5, 0.985))

    for ci, day in enumerate(day_order):
        ax = axes[ci]
        g  = group_df[group_df["_day_folder"] == day]
        if g.empty:
            ax.set_axis_off(); continue

        if by_sex:
            # Build x-keys as (treatment, sex) tuples
            x_keys, x_labels = [], []
            for trt in groups:
                for sex in SEX_ORDER:
                    row = g[(g["treatment_group"] == trt) & (g["sex"] == sex)]
                    if not row.empty:
                        x_keys.append((trt, sex))
                        x_labels.append(f"{trt}\n{sex}")

            key_to_data = {}
            for _, row in g.iterrows():
                key_to_data[(row["treatment_group"], row["sex"])] = row.to_dict()
        else:
            x_keys, x_labels = [], []
            for trt in groups:
                if (g["treatment_group"] == trt).any():
                    x_keys.append(trt)
                    x_labels.append(trt)
            key_to_data = {row["treatment_group"]: row.to_dict() for _, row in g.iterrows()}

        _stacked_bar_panel(ax, key_to_data, x_keys, x_labels)
        ax.set_title(day, fontsize=11, pad=8)
        if ci == 0:
            xlabel = "Sex × Treatment" if by_sex else "Treatment"
            ax.set_xlabel(xlabel, fontsize=10, labelpad=8)
            ax.set_ylabel("% of CS+ trials", fontsize=10)

    utils.save_fig(fig, out_dir / f"{fname_tag}.svg")


# =============================================================================
# Prism export
# =============================================================================

def _prism_export(per_animal: pd.DataFrame, out_dir: Path):
    """
    Wide-format Excel: one sheet per outcome × day.
    Rows = animals, columns = treatment group.
    """
    out_path = out_dir / "eee_prism_ready.xlsx"
    per_animal = per_animal.sort_values(["_day_folder", "treatment_group", "animal_id"])

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for outcome in ("evade_pct", "escape_pct", "endure_pct"):
            for day in sorted(per_animal["_day_folder"].unique(), key=utils.day_sort_key):
                sub = per_animal[per_animal["_day_folder"] == day][
                    ["animal_id", "treatment_group", outcome]
                ]
                wide = sub.pivot_table(
                    index="animal_id", columns="treatment_group",
                    values=outcome, aggfunc="mean",
                )
                sheet = f"{outcome[:5]}_{day}"[:31]
                wide.to_excel(writer, sheet_name=sheet)

    print(f"[ok] Prism table saved: {out_path}")


# =============================================================================
# Entry point
# =============================================================================

def run(cfg: dict):
    behaviordata = cfg["behaviordata"]
    out_dir      = behaviordata / "Shock outcomes (evade-escape-endure)"
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = utils.load_metadata(behaviordata)

    print("  Classifying CS+ outcomes across all session days...")
    df = _collect_all(cfg, meta)

    if df.empty:
        print("  [warn] No EEE data found. Skipping EEE analysis.")
        return

    df.to_csv(out_dir / "shock_outcomes_all_days_concat.csv", index=False)
    print("  [ok] Concatenated CSV saved.")

    per_animal = _per_animal_props(df)
    if per_animal.empty:
        print("  [warn] No valid CS+ trials with US detected.")
        return

    # --- Treatment-only stacked bars ---
    gm_no_sex = _group_means(per_animal, by_sex=False)
    _make_stacked_bars(gm_no_sex, out_dir, cfg, by_sex=False,
                        fname_tag="stacked_eee_by_treatment_tiled")

    # --- Sex × Treatment stacked bars ---
    if cfg["eee_by_sex"]:
        gm_sex = _group_means(per_animal, by_sex=True)
        _make_stacked_bars(gm_sex, out_dir, cfg, by_sex=True,
                            fname_tag="stacked_eee_by_sex_treatment_tiled")

    # --- Prism export ---
    if cfg["prism_export"]:
        print("  Generating Prism-ready tables...")
        _prism_export(per_animal, out_dir)

    print("  EEE analysis complete.")
