# -*- coding: utf-8 -*-
"""
Variable_Conflict_Shocks.py  –  Shock outcome classification and summary plots.
"""

import os
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.patches import Patch

from utils import load_metadata, extract_cue_dict, normalize_metadata_treatments
from config import CUE_DURATIONS, OUTCOME_COLORS, TREATMENT_COLORS, TREATMENT_SEM_COLORS, SEM_ALPHA

TONE_TYPES    = ["Tone", "Conflict", "Tone-Light", "Light-Tone"]
OUTCOME_ORDER = ["Avoided", "Escaped", "Fully_Shocked"]


# ── Classification helpers ─────────────────────────────────────────────────────

def _find_behavior_id_col(metadata):
    for c in metadata.columns:
        if c.lower().strip() == "behavior_id":
            return c
    raise ValueError("Metadata must include a 'behavior_id' column.")


def _tone_onset_offset(trial_type):
    return 15.0 if trial_type == "Light-Tone" else 0.0


def _load_downsampled(file_path):
    df = pd.read_csv(file_path)
    col_map = {
        "Time (s)": "time_s", "Time(s)": "time_s",
        "New speaker active": "tone_cue",
        "Cue light active": "cue_light",
        "House light active": "house_light",
        "In platform": "in_platform", "In Platform": "in_platform",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    req = ["time_s", "tone_cue", "in_platform"]
    if not all(c in df.columns for c in req):
        print(f"⚠️  Skipping {os.path.basename(file_path)}: missing {set(req) - set(df.columns)}")
        return None
    for col in req:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["time_s"]).reset_index(drop=True)


def _classify_last2(df, tone_onset, tone_len):
    times = df["time_s"].values
    start_i = int(np.nanargmin(np.abs(times - tone_onset)))
    i, tone_idxs = start_i, []
    while i < len(df) and df.iloc[i]["tone_cue"] == 1:
        tone_idxs.append(i)
        i += 1
    if not tone_idxs:
        end_i = int(np.nanargmin(np.abs(times - (tone_onset + tone_len))))
        tone_idxs = list(range(min(start_i, end_i), max(start_i, end_i) + 1))
    if len(tone_idxs) < 2:
        return None
    vals = df.iloc[tone_idxs[-2:]]["in_platform"].astype(float).values
    if np.any(np.isnan(vals)):
        return None
    if np.all(vals == 1.0):
        return "Avoided"
    if vals[0] == 0.0 and vals[1] == 1.0:
        return "Escaped"
    if np.all(vals == 0.0):
        return "Fully_Shocked"
    return None


def classify_shock_outcome(df, behavior_id, cue_dict, md_idx):
    treatment = md_idx.loc[behavior_id, "treatment_group"] if behavior_id in md_idx.index else "Unknown"
    all_trials = sorted(
        [(tt, float(t)) for tt in TONE_TYPES for t in cue_dict.get(tt, [])],
        key=lambda x: x[1],
    )
    tone_len = float(CUE_DURATIONS.get("Tone", 30))
    counters, rows = {t: 0 for t in TONE_TYPES}, []
    for idx, (trial_type, trial_start) in enumerate(all_trials, start=1):
        counters[trial_type] += 1
        tone_onset = trial_start + _tone_onset_offset(trial_type)
        outcome = _classify_last2(df, tone_onset, tone_len)
        if outcome is None:
            continue
        rows.append(dict(
            Behavior_ID=behavior_id, Treatment_Group=treatment,
            Trial_Type=trial_type,
            Trial_Label=f"{trial_type.replace('-','').replace(' ','')}_{counters[trial_type]:02d}",
            Trial_Index=idx, Trial_Start_Time=trial_start, Outcome=outcome,
        ))
    return rows


# ── Within-session figure helpers ──────────────────────────────────────────────

def _stacked100_pooled(trial_df, save_path):
    agg = trial_df.groupby(["Treatment_Group", "Trial_Type", "Outcome"]).size().reset_index(name="Count")
    if agg.empty:
        return
    agg["Total"] = agg.groupby(["Treatment_Group", "Trial_Type"])["Count"].transform("sum")
    agg["Percent"] = (agg["Count"] / agg["Total"].replace(0, np.nan) * 100).fillna(0)
    treat_order = sorted(agg["Treatment_Group"].unique())
    trial_order = [t for t in TONE_TYPES if t in agg["Trial_Type"].unique()]
    pivot = (agg.pivot_table(index=["Treatment_Group", "Trial_Type"],
                              columns="Outcome", values="Percent", fill_value=0)
               .reindex(columns=OUTCOME_ORDER, fill_value=0))
    n_treat, n_trials = len(treat_order), len(trial_order)
    bar_w = 0.8 / max(1, n_treat)
    xs = np.arange(n_trials)
    fig, ax = plt.subplots(figsize=(max(8, n_trials * 2.6), 5.5))
    for j, treat in enumerate(treat_order):
        offsets = xs + (j - (n_treat - 1) / 2) * bar_w
        bottom = np.zeros(n_trials)
        for outcome in OUTCOME_ORDER:
            heights = np.array([pivot.loc[(treat, tt), outcome]
                                 if (treat, tt) in pivot.index else 0.0
                                 for tt in trial_order])
            ax.bar(offsets, heights, bar_w, bottom=bottom,
                   color=OUTCOME_COLORS[outcome], edgecolor="black", linewidth=0.6,
                   label=outcome if j == 0 else None)
            bottom += heights
    ax.set_xticks(xs); ax.set_xticklabels(trial_order)
    ax.set_ylim(0, 100); ax.set_ylabel("Percent of Trials")
    ax.set_title("Outcome by Trial Type × Treatment (POOLED)")
    ax.legend(title="Outcome", bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.tight_layout()
    fig.savefig(os.path.join(save_path, "stacked100_POOLED.svg"), bbox_inches="tight")
    plt.close(fig)


def _stacked100_permouse(trial_df, save_path):
    pm = trial_df.groupby(["Behavior_ID", "Treatment_Group", "Trial_Type", "Outcome"]).size().reset_index(name="Count")
    if pm.empty:
        return
    pm["Total"] = pm.groupby(["Behavior_ID", "Treatment_Group", "Trial_Type"])["Count"].transform("sum")
    pm["Percent"] = (pm["Count"] / pm["Total"].replace(0, np.nan) * 100).fillna(0)
    agg = pm.groupby(["Treatment_Group", "Trial_Type", "Outcome"])["Percent"].mean().reset_index()
    treat_order = sorted(agg["Treatment_Group"].unique())
    trial_order = [t for t in TONE_TYPES if t in agg["Trial_Type"].unique()]
    pivot = (agg.pivot_table(index=["Treatment_Group", "Trial_Type"],
                              columns="Outcome", values="Percent", fill_value=0)
               .reindex(columns=OUTCOME_ORDER, fill_value=0))
    n_treat, n_trials = len(treat_order), len(trial_order)
    bar_w = 0.8 / max(1, n_treat)
    xs = np.arange(n_trials)
    fig, ax = plt.subplots(figsize=(max(8, n_trials * 2.6), 5.5))
    for j, treat in enumerate(treat_order):
        offsets = xs + (j - (n_treat - 1) / 2) * bar_w
        bottom = np.zeros(n_trials)
        for outcome in OUTCOME_ORDER:
            heights = np.array([pivot.loc[(treat, tt), outcome]
                                 if (treat, tt) in pivot.index else 0.0
                                 for tt in trial_order])
            ax.bar(offsets, heights, bar_w, bottom=bottom,
                   color=OUTCOME_COLORS[outcome], edgecolor="black", linewidth=0.6,
                   label=outcome if j == 0 else None)
            bottom += heights
    ax.set_xticks(xs); ax.set_xticklabels(trial_order)
    ax.set_ylim(0, 100); ax.set_ylabel("Percent of Trials")
    ax.set_title("Outcome by Trial Type × Treatment (PER-MOUSE avg)")
    ax.legend(title="Outcome", bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.tight_layout()
    fig.savefig(os.path.join(save_path, "stacked100_PERMOUSE.svg"), bbox_inches="tight")
    plt.close(fig)


def _barplot_grid_with_points(trial_df, save_path):
    pm = trial_df.groupby(["Behavior_ID", "Treatment_Group", "Trial_Type", "Outcome"]).size().reset_index(name="Count")
    if pm.empty:
        return
    totals = pm.groupby(["Behavior_ID", "Treatment_Group", "Trial_Type"])["Count"].sum().reset_index(name="Total")
    pm = pm.merge(totals, on=["Behavior_ID", "Treatment_Group", "Trial_Type"], how="left")
    pm["Percent"] = (pm["Count"] / pm["Total"].replace(0, np.nan) * 100).fillna(0)
    treat_order = sorted(pm["Treatment_Group"].unique())
    trial_order = [t for t in TONE_TYPES if t in pm["Trial_Type"].unique()]
    g = sns.catplot(data=pm, kind="bar", x="Treatment_Group", y="Percent", hue="Outcome",
                    col="Trial_Type", col_wrap=2, order=treat_order, hue_order=OUTCOME_ORDER,
                    palette=OUTCOME_COLORS, edgecolor="black", linewidth=0.8,
                    height=4.2, aspect=1.2, sharey=True, errorbar=None, legend=False)
    for ax, trial in zip(g.axes.flat, trial_order):
        sub = pm[pm["Trial_Type"] == trial]
        sns.stripplot(data=sub, x="Treatment_Group", y="Percent", hue="Outcome",
                      order=treat_order, hue_order=OUTCOME_ORDER, dodge=True,
                      jitter=0.12, size=4, alpha=0.7, edgecolor="black", linewidth=0.4,
                      ax=ax, palette=OUTCOME_COLORS, legend=False)
        ax.set_ylim(0, 110); ax.set_xlabel("Treatment"); ax.set_ylabel("Percent of Trials")
        ax.grid(True, axis="y", linestyle="--", linewidth=0.4, alpha=0.6)
    g.set_titles("{col_name}")
    g.fig.suptitle("Average Outcomes by Trial Type", y=1.02, fontsize=14)
    g.fig.legend(handles=[Patch(facecolor=OUTCOME_COLORS[o], edgecolor="black", label=o) for o in OUTCOME_ORDER],
                 labels=OUTCOME_ORDER, title="Outcome",
                 loc="center left", bbox_to_anchor=(1.02, 0.5))
    g.fig.tight_layout()
    g.savefig(os.path.join(save_path, "barplot_outcomes_by_trialtype.svg"), bbox_inches="tight")
    plt.close(g.fig)


# ── Within-session runner ──────────────────────────────────────────────────────

def run_shock_summary(data_folder, save_path):
    """Classify shock outcomes and produce within-session summary plots."""
    os.makedirs(save_path, exist_ok=True)
    downsampled_folder = os.path.join(data_folder, "downsampled")
    cue_dicts = extract_cue_dict(downsampled_folder)

    metadata = load_metadata(data_folder)
    bid_col = _find_behavior_id_col(metadata)
    md = metadata.copy()
    md.columns = md.columns.str.lower()
    bid_lower = bid_col.lower()
    md[bid_lower] = md[bid_lower].astype(str).str.lower()
    md_idx = md.set_index(bid_lower)

    all_rows, skipped = [], []
    for file in os.listdir(downsampled_folder):
        if not file.endswith(".csv"):
            continue
        mouse_id = os.path.splitext(file)[0]
        behavior_id = mouse_id.split("_")[1].lower() if "_" in mouse_id else mouse_id.lower()
        cue_dict = cue_dicts.get(mouse_id, {})
        if not cue_dict:
            skipped.append(file); continue
        df = _load_downsampled(os.path.join(downsampled_folder, file))
        if df is None:
            skipped.append(file); continue
        rows = classify_shock_outcome(df, behavior_id, cue_dict, md_idx)
        if not rows:
            skipped.append(file); continue
        all_rows.extend(rows)

    if not all_rows:
        print("⏭️  No trials classified.")
        return

    trial_df = pd.DataFrame(all_rows).sort_values(["Behavior_ID", "Trial_Start_Time"])
    trial_df.to_csv(os.path.join(save_path, "shock_trial_outcomes.csv"), index=False)

    # Per-mouse summary CSV
    counts = (trial_df.groupby(["Behavior_ID", "Treatment_Group", "Trial_Type"])["Outcome"]
              .value_counts().unstack(fill_value=0).reset_index())
    totals = (trial_df.groupby(["Behavior_ID", "Treatment_Group"])["Outcome"]
              .value_counts().unstack(fill_value=0).reset_index())
    totals["Trial_Type"] = "Total"
    combined = pd.concat([counts, totals.reindex(columns=counts.columns, fill_value=0)], ignore_index=True)
    combined.to_csv(os.path.join(save_path, "shock_outcomes_by_trialtype.csv"), index=False)

    _stacked100_pooled(trial_df, save_path)
    _stacked100_permouse(trial_df, save_path)
    _barplot_grid_with_points(trial_df, save_path)

    # Cumulative shock plots
    trial_df["Shocked"] = (trial_df["Outcome"] == "Fully_Shocked").astype(int)
    trial_df = trial_df.sort_values(["Behavior_ID", "Trial_Start_Time"])
    trial_df["Trial_Number"] = trial_df.groupby("Behavior_ID").cumcount() + 1
    trial_df["Cumulative_Shocks"] = trial_df.groupby("Behavior_ID")["Shocked"].cumsum()

    avg = (trial_df.groupby(["Treatment_Group", "Trial_Number"])["Cumulative_Shocks"]
           .mean().reset_index())
    plt.figure(figsize=(8, 5))
    for grp in avg["Treatment_Group"].unique():
        g = avg[avg["Treatment_Group"] == grp]
        plt.plot(g["Trial_Number"], g["Cumulative_Shocks"], label=grp, linewidth=2)
    plt.title("Average Cumulative Shocks"); plt.xlabel("Trial"); plt.ylabel("Mean Cumulative Shocks")
    plt.legend(); plt.tight_layout()
    plt.savefig(os.path.join(save_path, "cumulative_shocks_group_mean.svg")); plt.close()

    mice = trial_df["Behavior_ID"].unique()
    n_cols = 4
    n_rows = math.ceil(len(mice) / n_cols)
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(4 * n_cols, 3 * n_rows), sharey=True)
    axes = axes.flatten()
    for i, mouse in enumerate(mice):
        mdf = trial_df[trial_df["Behavior_ID"] == mouse]
        axes[i].plot(mdf["Trial_Number"], mdf["Cumulative_Shocks"], linewidth=1.6)
        axes[i].set_title(mouse, fontsize=10); axes[i].grid(True, linestyle="--", linewidth=0.5)
    for j in range(len(mice), len(axes)):
        fig.delaxes(axes[j])
    fig.suptitle("Cumulative Shocks per Mouse"); fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(os.path.join(save_path, "cumulative_shocks_individuals.svg")); plt.close(fig)

    print(f"✅  Shock summary complete – {trial_df['Behavior_ID'].nunique()} mice.")
    if skipped:
        print(f"   Skipped: {', '.join(skipped)}")


# ── Across-session helpers ─────────────────────────────────────────────────────

def collect_shock_trials_across_sessions(analysis_root):
    """
    Collect shock_trial_outcomes.csv from all VC* subfolders.
    Expects: analysis_root/VC*/shocks/shock_trial_outcomes.csv
    """
    dfs = []
    for sub in sorted(os.listdir(analysis_root)):
        if not sub.lower().startswith("vc"):
            continue
        csv_path = os.path.join(analysis_root, sub, "shocks", "shock_trial_outcomes.csv")
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            df["Session"] = sub
            dfs.append(df)
        else:
            print(f"⚠️  Not found: {csv_path}")
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


def _per_mouse_outcome_percents(trial_df):
    counts = (trial_df.groupby(["Behavior_ID", "Treatment_Group", "Session", "Trial_Type", "Outcome"])
              .size().reset_index(name="Count"))
    totals = (counts.groupby(["Behavior_ID", "Treatment_Group", "Session", "Trial_Type"])["Count"]
              .sum().reset_index(name="N"))
    wide = (counts.pivot_table(index=["Behavior_ID", "Treatment_Group", "Session", "Trial_Type"],
                                columns="Outcome", values="Count", fill_value=0).reset_index())
    for o in OUTCOME_ORDER:
        if o not in wide.columns:
            wide[o] = 0
    wide = wide.merge(totals, on=["Behavior_ID", "Treatment_Group", "Session", "Trial_Type"], how="left")
    for o in OUTCOME_ORDER:
        wide[o] = (wide[o] / wide["N"].replace(0, np.nan) * 100).fillna(0)
    return wide[["Behavior_ID", "Treatment_Group", "Session", "Trial_Type", "N"] + OUTCOME_ORDER]


def _treatment_mean_percents(pm):
    agg = pm.groupby(["Treatment_Group", "Session", "Trial_Type"])[OUTCOME_ORDER].mean().reset_index()
    n = (pm.groupby(["Treatment_Group", "Session", "Trial_Type"])["Behavior_ID"]
         .nunique().reset_index(name="N_mice"))
    return agg.merge(n, on=["Treatment_Group", "Session", "Trial_Type"], how="left")


def _plot_stacked100_across(trial_df, save_folder, pooled=True):
    label = "POOLED" if pooled else "PERMOUSE"
    trial_order = [t for t in TONE_TYPES if t in trial_df["Trial_Type"].unique()]
    treat_order = sorted(trial_df["Treatment_Group"].unique())
    session_order = sorted(trial_df["Session"].unique())
    xs = np.arange(len(session_order))
    tick_labels = session_order

    if pooled:
        agg = (trial_df.groupby(["Treatment_Group", "Trial_Type", "Session", "Outcome"])
               .size().reset_index(name="Count"))
        agg["Total"] = agg.groupby(["Treatment_Group", "Trial_Type", "Session"])["Count"].transform("sum")
        agg["Percent"] = agg["Count"] / agg["Total"] * 100
        pivot = (agg.pivot_table(index=["Treatment_Group", "Trial_Type", "Session"],
                                  columns="Outcome", values="Percent", fill_value=0)
                   .reindex(columns=OUTCOME_ORDER, fill_value=0))
    else:
        pm = _per_mouse_outcome_percents(trial_df)
        means = _treatment_mean_percents(pm)
        pivot = means.set_index(["Treatment_Group", "Trial_Type", "Session"])[OUTCOME_ORDER]

    n_rows, n_cols = len(trial_order), len(treat_order)
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(n_cols * 4.2, n_rows * 4.8),
                             sharey=True, squeeze=False)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.18, hspace=0.32, wspace=0.18)
    fig.suptitle(f"Across-Session Shock Outcomes (100% {label})", y=0.95, fontsize=14)

    for r, trial in enumerate(trial_order):
        for c, treat in enumerate(treat_order):
            ax = axes[r, c]
            bottom = np.zeros(len(session_order))
            for outcome in OUTCOME_ORDER:
                heights = [float(pivot.loc[(treat, trial, s), outcome])
                           if (treat, trial, s) in pivot.index else 0.0
                           for s in session_order]
                ax.bar(xs, heights, bottom=bottom,
                       color=OUTCOME_COLORS[outcome], edgecolor="black", linewidth=0.5)
                bottom += np.array(heights)
            if r == 0:
                ax.set_title(treat, fontsize=13)
            if c == 0:
                ax.set_ylabel(f"{trial}\n(% of trials)", fontsize=12)
            ax.set_ylim(0, 100)
            ax.set_xticks(xs); ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)

    handles = [Patch(facecolor=OUTCOME_COLORS[o], edgecolor="black", label=o) for o in OUTCOME_ORDER]
    fig.legend(handles, OUTCOME_ORDER, title="Outcome",
               loc="upper center", bbox_to_anchor=(0.5, 0.93),
               ncol=len(OUTCOME_ORDER), frameon=True, fontsize=9)
    fig.savefig(os.path.join(save_folder, f"across_session_stacked100_{label}.svg"), bbox_inches="tight")
    plt.close(fig)
    print(f"✅  Saved {label} across-session stacked100")


def _plot_line_collapsed(trial_df, save_folder):
    pm = _per_mouse_outcome_percents(trial_df)
    # collapse across trial types (weighted by N)
    for o in OUTCOME_ORDER:
        pm[f"{o}_count"] = pm[o] * pm["N"] / 100.0
    agg_cols = {"N": ("N", "sum")}
    for o in OUTCOME_ORDER:
        agg_cols[f"{o}_count"] = (f"{o}_count", "sum")
    collapsed = (pm.groupby(["Behavior_ID", "Treatment_Group", "Session"])
                 .agg(**agg_cols).reset_index().rename(columns={"N": "N_total"}))
    for o in OUTCOME_ORDER:
        collapsed[o] = (collapsed[f"{o}_count"] / collapsed["N_total"] * 100).fillna(0)
    means = (collapsed.groupby(["Treatment_Group", "Session"])[OUTCOME_ORDER]
             .mean().reset_index())
    treat_order = sorted(means["Treatment_Group"].unique())
    session_order = sorted(means["Session"].unique())
    xs = np.arange(len(session_order))
    lookup = means.set_index(["Treatment_Group", "Session"])
    fig, axes = plt.subplots(1, len(treat_order), figsize=(5.5 * len(treat_order), 4.5),
                             sharey=True, squeeze=False)
    fig.suptitle("Across-Session Outcomes (PER-MOUSE, collapsed across trial types)", y=0.98)
    for c, treat in enumerate(treat_order):
        ax = axes[0, c]
        for outcome in OUTCOME_ORDER:
            ys = [lookup.loc[(treat, s), outcome] if (treat, s) in lookup.index else np.nan
                  for s in session_order]
            ax.plot(xs, ys, marker="o", linewidth=2, color=OUTCOME_COLORS[outcome], label=outcome)
        ax.set_title(treat); ax.set_xticks(xs)
        ax.set_xticklabels(session_order, rotation=45, ha="right", fontsize=9)
        ax.set_ylim(0, 100); ax.grid(True, axis="y", linestyle="--", alpha=0.6)
        if c == 0:
            ax.set_ylabel("Percent of Trials")
    handles = [Patch(facecolor=OUTCOME_COLORS[o], edgecolor="black", label=o) for o in OUTCOME_ORDER]
    fig.legend(handles, OUTCOME_ORDER, title="Outcome",
               loc="upper center", bbox_to_anchor=(0.5, 0.92), ncol=3)
    fig.tight_layout(rect=[0, 0, 1, 0.9])
    fig.savefig(os.path.join(save_folder, "across_session_line_collapsed.svg"), bbox_inches="tight")
    plt.close(fig)


# ── Across-session runner ──────────────────────────────────────────────────────

def run_across_session_summary(analysis_root, save_folder):
    """Collect shock trial data from all VC sessions and produce across-session plots + CSVs."""
    trial_df = collect_shock_trials_across_sessions(analysis_root)
    if trial_df.empty:
        print("❌  No across-session shock data found.")
        return
    os.makedirs(save_folder, exist_ok=True)

    pm = _per_mouse_outcome_percents(trial_df)
    pm.to_csv(os.path.join(save_folder, "per_mouse_outcome_percents.csv"), index=False)
    _treatment_mean_percents(pm).to_csv(
        os.path.join(save_folder, "treatment_mean_outcome_percents.csv"), index=False)

    _plot_stacked100_across(trial_df, save_folder, pooled=True)
    _plot_stacked100_across(trial_df, save_folder, pooled=False)
    _plot_line_collapsed(trial_df, save_folder)
    print(f"✅  Across-session shock summary complete: {save_folder}")
