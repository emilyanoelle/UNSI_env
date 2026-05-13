# -*- coding: utf-8 -*-
"""
eee_analysis.py
---------------
Classifies CS+ shock trials as Evade, Escape, or Endure for all session days.

Pass 1 — Parallel, fully in-memory:
    Each CSV file is processed independently on a worker process.
    No per-day CSVs are written.

Pass 2 — Cumulative:
    Results written once per cohort and once combined.
"""

from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import utils
import run_report as rr


US_DEFAULT_LEN_S  = 2.0
PLATFORM_FULL_TOL = 1e-6

OUTCOME_ORDER  = ["evade", "escape", "endure"]
OUTCOME_COLORS = {"evade": "#2ca02c", "escape": "#1f77b4", "endure": "#ff7f0e"}


# ── Outcome classification helpers (unchanged) ───────────────────────────────

def _first_rising_after(df, time_col, ttl_col, t0):
    if ttl_col is None or ttl_col not in df.columns:
        return None
    sub = df[df[time_col] >= t0]
    idx = sub[ttl_col].fillna(0).astype(float).to_numpy().nonzero()[0]
    return int(sub.index[idx[0]]) if len(idx) > 0 else None


def _first_shocker_window(df, time_col, shocker_col, trial_start, trial_end):
    if shocker_col is None or shocker_col not in df.columns:
        return None, None
    sub = df[(df[time_col] >= trial_start) & (df[time_col] <= trial_end)]
    if sub.empty:
        return None, None
    active = sub[shocker_col].fillna(0).astype(float).to_numpy() > 0
    if not active.any():
        return None, None
    times    = sub[time_col].astype(float).to_numpy()
    start_ix = int(np.where(active)[0][0])
    end_ix   = start_ix + 1
    while end_ix < len(active) and active[end_ix]:
        end_ix += 1
    us_start = max(float(times[start_ix]), trial_start)
    us_end   = float(times[end_ix]) if end_ix < len(times) else trial_end
    us_end   = min(us_end, trial_end)
    if us_end <= us_start:
        return None, None
    return us_start, us_end


def _classify_outcome(t, platform, us_start, us_end):
    frac_on  = utils.interval_fraction_on(t, platform, us_start, us_end)
    full_on  = (1.0 - frac_on) <= PLATFORM_FULL_TOL
    full_off = frac_on <= PLATFORM_FULL_TOL
    if full_on:  return "evade"
    if full_off: return "endure"
    plat_bin = (platform > 0).astype(int)
    re_ix    = np.where((np.append(0, plat_bin[:-1]) == 0) & (plat_bin == 1))[0]
    if re_ix.size > 0:
        re_times = t[re_ix]
        if np.any((re_times > us_start) & (re_times < us_end)):
            return "escape"
    return "escape"


# ── Pass 1: per-file processing ──────────────────────────────────────────────

def _process_file(csv_path, meta, day_dir, behaviordata_root, cfg):
    report_data = {"subjects": {}, "exclusions": {}}
    subject_key = csv_path.name

    test_date, behavior_id, row, exclusion_reason = utils.metadata_for_csv(csv_path, meta)
    if exclusion_reason is not None:
        print(f"  [warn] {csv_path.name}: {exclusion_reason}; skipping.")
        report_data["exclusions"][subject_key] = exclusion_reason
        return [], report_data

    animal_id = row.get("animal_id", behavior_id)
    treatment = utils.normalize_treatment(
        row.get("treatment_group", "Unknown"), cfg["treatment_lookup"])
    sex       = utils.normalize_sex(row.get("sex", None))
    cohort_id = row.get("cohort_id", None)

    df_header = utils.load_csv_header(csv_path)
    shocker_col = None
    us_on = us_off = None
    try:
        time_col     = utils.find_time_col(df_header, cfg)
        platform_col = utils.find_platform_col(df_header, cfg)
        source_cols = [time_col, platform_col] + utils.trial_detection_source_columns(df_header, cfg)
        if cfg.get("use_shocker_column"):
            shocker_col = utils.find_shocker_col(df_header, cfg)
            if shocker_col is None:
                msg = "USE_SHOCKER_COLUMN is True, but no Shocker active column was found"
                print(f"  [warn] {csv_path.name}: {msg}; skipping.")
                report_data["exclusions"][subject_key] = msg
                return [], report_data
            source_cols.append(shocker_col)
        else:
            us_on, us_off = utils.find_us_cols(df_header, cfg)
            source_cols.extend([us_on, us_off])
        source_cols = utils.unique_existing_columns(df_header, source_cols)
    except ValueError as e:
        print(f"  [warn] {csv_path.name}: {e}; skipping.")
        report_data["exclusions"][subject_key] = str(e)
        return [], report_data

    # EEE only needs platform occupancy plus CS/US timing columns, so avoid
    # parsing the rest of the AnyMaze export for this file.
    df = utils.load_csv(csv_path, usecols=source_cols)
    df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    trials, _, cs_source = utils.detect_trials(df, time_col, cfg)

    cs_trials = [t for t in trials if t["type"] == "CS+"]
    if not cs_trials:
        report_data["exclusions"][subject_key] = "no CS+ trials detected"
        return [], report_data

    cols_used = {
        "time":          time_col,
        "in_platform":   platform_col,
        "CS+_detection": cs_source,
        "US_detection":  shocker_col or "TTL",
        "US_on_col":     us_on  or "not used",
        "US_off_col":    us_off or "not used",
    }
    report_data["subjects"][subject_key] = {
        "columns_used": cols_used,
        "warnings":     [],
        "skipped":      [],
    }

    day, context, session = utils.parse_folder_bits(day_dir.name)
    t    = df[time_col].astype(float).to_numpy()
    plat = df[platform_col].fillna(0).astype(float).to_numpy()

    rows = []
    for tr in cs_trials:
        if tr["trial_index"] > cfg["eee_trial_cap"]:
            continue
        if shocker_col is not None:
            us_start, us_end = _first_shocker_window(
                df, time_col, shocker_col, tr["start"], tr["end"])
            if us_start is None:
                outcome = "no_us"; us_start = us_end = np.nan
            else:
                outcome = _classify_outcome(t, plat, us_start, us_end)
        else:
            us_on_ix = _first_rising_after(df, time_col, us_on, tr["start"])
            if us_on_ix is None:
                outcome = "no_us"; us_start = us_end = np.nan
            else:
                us_start = float(df.loc[us_on_ix, time_col])
                if us_start >= tr["end"]:
                    outcome = "no_us"; us_end = np.nan
                else:
                    us_end  = utils.get_off_time(df, time_col, us_off, us_start, US_DEFAULT_LEN_S)
                    us_s, us_e = utils.clip_interval(us_start, us_end, tr["start"], tr["end"])
                    outcome = _classify_outcome(t, plat, us_s, us_e)

        rows.append(dict(
            animal_id=animal_id, behavior_id=behavior_id,
            treatment_group=treatment, sex=sex, cohort_id=cohort_id,
            test_date=test_date, day=day, context=context,
            session_label=session, _day_folder=day_dir.name,
            _day_dir=str(day_dir), _behaviordata_root=str(behaviordata_root),
            _source_csv=csv_path.name,
            trial_type="CS+", trial_index=tr["trial_index"],
            trial_start_s=tr["start"], trial_end_s=tr["end"],
            us_start_s=us_start, us_end_s=us_end, outcome=outcome,
        ))

    return rows, report_data


def _process_file_star(args):
    return _process_file(*args)


# ── Pass 1 driver ─────────────────────────────────────────────────────────────

def _collect_all_parallel(cfg, report=None):
    tasks = []
    for bd in cfg["behaviordata_dirs"]:
        meta   = utils.load_metadata(bd)
        suffix = cfg["csv_suffix"]
        for day_dir in utils.find_session_dirs(bd):
            csvs = sorted(
                day_dir.glob(f"*{suffix}.csv") if suffix else day_dir.glob("*.csv"))
            for csv_path in csvs:
                tasks.append((csv_path, meta, day_dir, bd, cfg))

    if not tasks:
        print("  [warn] No CSV files found.")
        return pd.DataFrame()

    print(f"  Found {len(tasks)} CSV files. Processing with {cfg['n_workers']} workers...")

    all_rows = []

    with ProcessPoolExecutor(max_workers=cfg["n_workers"]) as pool:
        future_to_csv = {pool.submit(_process_file_star, t): t[0] for t in tasks}
        for future in as_completed(future_to_csv):
            csv_path = future_to_csv[future]
            try:
                rows, report_data = future.result()
            except Exception as exc:
                print(f"  [error] {csv_path.name} raised an exception: {exc}")
                continue

            all_rows.extend(rows)

            if report is not None:
                for key, info in report_data["subjects"].items():
                    rr.record_subject(report, "eee", key,
                                      columns_used=info["columns_used"],
                                      warnings=info["warnings"],
                                      skipped=info["skipped"])
                for key, reason in report_data["exclusions"].items():
                    rr.record_exclusion(report, "eee", key, reason)

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


# ── Per-animal proportions and group means (unchanged) ───────────────────────

def _per_animal_props(df):
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
    return (valid.groupby(
                ["_day_folder","day","context","session_label",
                 "treatment_group","sex","cohort_id","animal_id"], as_index=False)
            .apply(props).reset_index(drop=True))


def _group_means(per_animal, by_sex=False):
    if per_animal.empty:
        return pd.DataFrame()
    group_cols = ["_day_folder","day","context","session_label","treatment_group"]
    if by_sex:
        group_cols.append("sex")
    return (per_animal.groupby(group_cols, as_index=False)
            .agg(evade_pct=("evade_pct","mean"),
                 escape_pct=("escape_pct","mean"),
                 endure_pct=("endure_pct","mean"),
                 n_animals=("animal_id","nunique")))


# ── Figures (unchanged) ───────────────────────────────────────────────────────

def _strip_n_count_label(label):
    lines = []
    for part in str(label).splitlines():
        compact = part.strip().lower().replace(" ", "")
        if compact.startswith("n="):
            continue
        lines.append(part)
    return "\n".join(lines)


def _stacked_bar_panel(ax, group_means, x_keys, x_labels):
    evade  = np.array([group_means.get(k, {}).get("evade_pct",  0.0) for k in x_keys])
    escape = np.array([group_means.get(k, {}).get("escape_pct", 0.0) for k in x_keys])
    endure = np.array([group_means.get(k, {}).get("endure_pct", 0.0) for k in x_keys])
    x = np.arange(len(x_keys)); bar_w = 0.7
    ax.bar(x, evade,  width=bar_w, color=OUTCOME_COLORS["evade"])
    ax.bar(x, escape, width=bar_w, bottom=evade, color=OUTCOME_COLORS["escape"])
    ax.bar(x, endure, width=bar_w, bottom=evade+escape, color=OUTCOME_COLORS["endure"])
    ax.set_xticks(x)
    ax.set_xticklabels([_strip_n_count_label(lbl) for lbl in x_labels],
                       fontsize=9, linespacing=1.2)
    ax.set_ylim(0, 100)
    ax.grid(True, axis="y", alpha=0.3)
    ax.margins(x=0.15)


def _make_stacked_bars(group_df, out_dir, cfg, by_sex, fname_tag):
    if group_df.empty:
        return
    day_order  = sorted(group_df["_day_folder"].unique(), key=utils.day_sort_key)
    groups     = cfg["canonical_groups"]
    SEX_ORDER  = ["M","F","Unknown"]
    n_cols     = len(day_order)
    panel_w    = 3.8
    fig_w      = min(22, panel_w * max(1, n_cols))
    fig, axes  = plt.subplots(1, n_cols, figsize=(fig_w, 4.5), squeeze=False)
    axes       = axes[0]
    title_sfx  = "by Sex × Treatment" if by_sex else "by Treatment"
    fig.suptitle(f"CS+ Outcomes — Evade / Escape / Endure ({title_sfx})",
                 fontsize=12, y=0.985)
    fig.subplots_adjust(left=0.06, right=0.99, bottom=0.18, top=0.80, wspace=0.55)
    legend_patches = [plt.Line2D([0],[0], color=OUTCOME_COLORS[o], lw=8, label=o.capitalize())
                      for o in OUTCOME_ORDER]
    fig.legend(handles=legend_patches, loc="upper center", ncol=3,
               frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.945),
               borderaxespad=0.0)

    for ci, day in enumerate(day_order):
        ax = axes[ci]
        g  = group_df[group_df["_day_folder"] == day]
        if g.empty:
            ax.set_axis_off(); continue
        if by_sex:
            x_keys, x_labels, key_to_data = [], [], {}
            for trt in groups:
                for sex in SEX_ORDER:
                    row = g[(g["treatment_group"] == trt) & (g["sex"] == sex)]
                    if not row.empty:
                        x_keys.append((trt, sex))
                        x_labels.append(f"{trt}\n{sex}")
                        key_to_data[(trt, sex)] = row.iloc[0].to_dict()
        else:
            x_keys   = [trt for trt in groups if (g["treatment_group"] == trt).any()]
            x_labels = x_keys[:]
            key_to_data = {row["treatment_group"]: row.to_dict() for _, row in g.iterrows()}
        _stacked_bar_panel(ax, key_to_data, x_keys, x_labels)
        ax.set_title(day, fontsize=11, pad=8)
        if ci == 0:
            ax.set_xlabel("Sex × Treatment" if by_sex else "Treatment", fontsize=10, labelpad=8)
            ax.set_ylabel("% of CS+ trials", fontsize=10)
    utils.save_fig(fig, out_dir / f"{fname_tag}.svg")


# ── Prism export (unchanged) ──────────────────────────────────────────────────

def _prism_export(per_animal, out_dir):
    out_path = out_dir / "eee_prism_ready.xlsx"
    per_animal = per_animal.sort_values(["_day_folder","treatment_group","animal_id"])
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for outcome in ("evade_pct","escape_pct","endure_pct"):
            for day in sorted(per_animal["_day_folder"].unique(), key=utils.day_sort_key):
                sub  = per_animal[per_animal["_day_folder"] == day][
                    ["animal_id","treatment_group", outcome]]
                wide = sub.pivot_table(index="animal_id", columns="treatment_group",
                                       values=outcome, aggfunc="mean")
                sheet = f"{outcome[:5]}_{day}"[:31]
                wide.to_excel(writer, sheet_name=sheet)
    print(f"[ok] Prism table saved: {out_path}")


# ── Output helper (unchanged) ─────────────────────────────────────────────────

def _write_outputs(df, out_dir, cfg, fname_tag):
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / f"{fname_tag}_all_days_concat.csv", index=False)

    per_animal = _per_animal_props(df)
    if per_animal.empty:
        print("  [warn] No valid CS+ trials with US detected.")
        return

    gm_no_sex = _group_means(per_animal, by_sex=False)
    _make_stacked_bars(gm_no_sex, out_dir, cfg, by_sex=False,
                        fname_tag=f"{fname_tag}_stacked_by_treatment_tiled")
    if cfg["eee_by_sex"]:
        gm_sex = _group_means(per_animal, by_sex=True)
        _make_stacked_bars(gm_sex, out_dir, cfg, by_sex=True,
                            fname_tag=f"{fname_tag}_stacked_by_sex_treatment_tiled")
    if cfg["prism_export"]:
        _prism_export(per_animal, out_dir)


def _find_behaviordata_for_cohort(cohort_id, behaviordata_dirs):
    for bd in behaviordata_dirs:
        meta = utils.load_metadata(bd)
        if meta is not None and "cohort_id" in meta.columns:
            if str(cohort_id) in meta["cohort_id"].astype(str).values:
                return bd
    return None


# ── Entry point ───────────────────────────────────────────────────────────────

def run(cfg, report=None):
    print("  Collecting and processing all CSVs in parallel...")
    df = _collect_all_parallel(cfg, report=report)

    if df.empty:
        print("  [warn] No EEE data found. Skipping EEE analysis.")
        return

    print("  Writing combined outputs...")
    _write_outputs(df.copy(), cfg["analysis_out"] / cfg["eee_subfolder"], cfg, "eee")

    if "cohort_id" in df.columns:
        for cohort_id, cohort_df in df.groupby("cohort_id", dropna=False):
            if pd.isna(cohort_id):
                continue
            cohort_bd = _find_behaviordata_for_cohort(cohort_id, cfg["behaviordata_dirs"])
            if cohort_bd is None:
                print(f"  [warn] Cannot locate BehaviorData for cohort '{cohort_id}'; skipping.")
                continue
            cohort_out = cohort_bd / "Analysis" / cfg["eee_subfolder"]
            print(f"  Writing cohort '{cohort_id}' outputs to {cohort_out}")
            _write_outputs(cohort_df.copy(), cohort_out, cfg, f"eee_{cohort_id}")

    print("  EEE analysis complete.")
