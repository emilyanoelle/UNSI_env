# -*- coding: utf-8 -*-
"""
Rewards.py  –  Reward consumption, rewards-in-light-trials, and Reward-Avoidance Index.

Three analyses, all using the Feeder active / In Reward / In platform columns
exported directly from AnyMaze.

Reward volume assumption: 16 µL per second of Feeder active.
Change FEEDER_RATE_UL_PER_SEC in runner.py if your pump differs.
"""

import os
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from utils import load_behavior_fractional, normalize_metadata_treatments, load_metadata, treatment_from_mouse
from runner import (
    CUE_DURATIONS, FEEDER_RATE_UL_PER_SEC,
    TREATMENT_COLORS, TREATMENT_SEM_COLORS, SEM_ALPHA,
)


def _active_seconds(series):
    return float(pd.to_numeric(series, errors="coerce").fillna(0).clip(0, 1).sum())


# ── Total reward consumption ───────────────────────────────────────────────────

def compute_reward_consumption(data_folder, save_path):
    """
    Per-animal total reward consumed across the entire session.

    Method:
      - Resample raw CSV to 1 Hz (fractional).
      - Sum fractional seconds where Feeder active is on.
      - Total reward (µL) = feeder_seconds × FEEDER_RATE_UL_PER_SEC.
      - Convert to mL for reporting.

    Outputs:
      reward_summary.csv       – one row per animal
      reward_summary.svg       – bar chart of total mL per animal
    """
    os.makedirs(save_path, exist_ok=True)
    rows = []

    for file in sorted(os.listdir(data_folder)):
        if not file.endswith(".csv") or file.endswith("_downsampled.csv"):
            continue
        mouse_id = file.replace(".csv", "")
        df = load_behavior_fractional(os.path.join(data_folder, file))

        if "Feeder active" not in df.columns:
            print(f"[warn] 'Feeder active' not found: {mouse_id}")
            continue

        feeder_s = _active_seconds(df["Feeder active"])
        total_uL = feeder_s * FEEDER_RATE_UL_PER_SEC
        rows.append({
            "Mouse_ID":          mouse_id,
            "Feeder_Active_s":   feeder_s,
            "Total_Reward_uL":   total_uL,
            "Total_Reward_mL":   total_uL / 1000,
        })

    if not rows:
        print("[skip] No feeder data found; skipping reward consumption.")
        return

    df_out = pd.DataFrame(rows)
    df_out.to_csv(os.path.join(save_path, "reward_summary.csv"), index=False)

    fig, ax = plt.subplots(figsize=(max(6, len(rows) * 0.9), 5))
    ax.bar(df_out["Mouse_ID"], df_out["Total_Reward_mL"], edgecolor="black", color="#4C9BE8")
    ax.set_xlabel("Animal")
    ax.set_ylabel("Total Reward Consumed (mL)")
    ax.set_title("Reward Consumption per Animal")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    fig.savefig(os.path.join(save_path, "reward_summary.svg"), format="svg")
    plt.close(fig)
    print(f"[ok] Reward consumption saved: {save_path}")


# ── Rewards during light trials ────────────────────────────────────────────────

def compute_rewards_in_light_trials(data_folder, save_path, cue_dict, cue_dicts=None):
    """
    Feeder activations restricted to the 30 s window after each Light cue onset.
    Uses Light cue onsets from cue_dict, or per-animal from cue_dicts.
    Saves rewards_in_light_trials.csv.
    """
    os.makedirs(save_path, exist_ok=True)
    rows = []

    for file in sorted(os.listdir(data_folder)):
        if not file.endswith(".csv") or file.endswith("_downsampled.csv"):
            continue
        mouse_id = file.replace(".csv", "")
        animal_cue = cue_dicts.get(mouse_id, cue_dict) if cue_dicts else cue_dict
        light_onsets = animal_cue.get("Light", [])

        if not light_onsets:
            continue

        df = load_behavior_fractional(os.path.join(data_folder, file))
        if "Feeder active" not in df.columns:
            continue

        light_seconds = set()
        for t in light_onsets:
            light_seconds.update(range(int(t), int(t) + 31))

        df_win = df[df["Time (s)"].round().astype(int).isin(light_seconds)]
        feeder_s = _active_seconds(df_win["Feeder active"])
        total_uL = feeder_s * FEEDER_RATE_UL_PER_SEC

        rows.append({
            "Mouse_ID":                 mouse_id,
            "N_light_trials":           len(light_onsets),
            "Feeder_Active_in_Light_s": feeder_s,
            "Reward_in_Light_uL":       total_uL,
            "Reward_in_Light_mL":       total_uL / 1000,
            "Feeder_s_per_trial":       feeder_s / len(light_onsets),
        })

    if not rows:
        print("[skip] No data for rewards-in-light-trials.")
        return

    pd.DataFrame(rows).to_csv(
        os.path.join(save_path, "rewards_in_light_trials.csv"), index=False
    )
    print(f"[ok] Rewards in light trials saved: {save_path}")


# ── Reward-Avoidance Index ─────────────────────────────────────────────────────

def compute_reward_avoidance_index(data_folder, save_path, cue_dict, cue_dicts=None):
    """
    Reward-Avoidance (R-A) Index per cue type per animal.

    Formula:
      Index = (Seconds_In_Reward − Seconds_In_Platform) /
              (Seconds_In_Reward + Seconds_In_Platform)

    Range: −1 (all time on platform) to +1 (all time in reward zone).
    NaN if neither zone was occupied during the cue (denominator = 0).

    Requires AnyMaze columns: 'In Reward' and 'In platform'.
    Cue windows use CUE_DURATIONS from runner.py.
    """
    os.makedirs(save_path, exist_ok=True)
    required = {"In Reward", "In platform"}
    rows = []

    for file in sorted(os.listdir(data_folder)):
        if not file.endswith(".csv") or file.endswith("_downsampled.csv"):
            continue
        mouse_id = file.replace(".csv", "")
        df = load_behavior_fractional(os.path.join(data_folder, file))
        animal_cue = cue_dicts.get(mouse_id, cue_dict) if cue_dicts else cue_dict

        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            print(f"[warn] {mouse_id}: missing columns {missing}; skipping.")
            continue

        for cue_type, onsets in animal_cue.items():
            if not onsets:
                continue
            dur = CUE_DURATIONS.get(cue_type, 30)

            for i, t in enumerate(onsets, start=1):
                window = df[(df["Time (s)"] >= t) & (df["Time (s)"] < t + dur)]
                r = (window["In Reward"]   > 0.5).sum()
                p = (window["In platform"] > 0.5).sum()
                denom = r + p
                rows.append({
                    "Mouse_ID":           mouse_id,
                    "Cue_Type":           cue_type,
                    "Trial_Index":        i,
                    "Seconds_In_Reward":  int(r),
                    "Seconds_In_Platform": int(p),
                    "RA_Index":           (r - p) / denom if denom > 0 else np.nan,
                })

    if not rows:
        print("[skip] No R-A index data computed (check 'In Reward' / 'In platform' columns).")
        return

    df_per_cue = pd.DataFrame(rows)
    df_per_cue.to_csv(
        os.path.join(save_path, "reward_avoidance_index_per_trial.csv"), index=False
    )

    # Summary: sum seconds across trials, recompute index
    summary = (
        df_per_cue.groupby(["Mouse_ID", "Cue_Type"])[["Seconds_In_Reward", "Seconds_In_Platform"]]
        .sum().reset_index()
    )
    denom = summary["Seconds_In_Reward"] + summary["Seconds_In_Platform"]
    summary["RA_Index"] = np.where(
        denom > 0,
        (summary["Seconds_In_Reward"] - summary["Seconds_In_Platform"]) / denom,
        np.nan,
    )
    summary.to_csv(
        os.path.join(save_path, "reward_avoidance_index_summary.csv"), index=False
    )

    # Group mean ± SEM bar chart
    try:
        metadata = normalize_metadata_treatments(load_metadata(data_folder))
        summary["treatment_group"] = summary["Mouse_ID"].apply(
            lambda mid: treatment_from_mouse(mid, metadata)
        )
    except Exception:
        summary["treatment_group"] = "Unknown"

    cue_types = [c for c in CUE_DURATIONS if c in summary["Cue_Type"].unique()]
    treatments = sorted(summary["treatment_group"].dropna().unique())
    n_cues = len(cue_types)
    n_treat = len(treatments)
    bar_w = 0.8 / max(1, n_treat)
    xs = np.arange(n_cues)

    fig, ax = plt.subplots(figsize=(max(6, n_cues * 2.2), 5))
    for j, treat in enumerate(treatments):
        sub = summary[summary["treatment_group"] == treat]
        means = [sub[sub["Cue_Type"] == c]["RA_Index"].mean() for c in cue_types]
        sems  = [sub[sub["Cue_Type"] == c]["RA_Index"].sem()  for c in cue_types]
        offsets = xs + (j - (n_treat - 1) / 2) * bar_w
        ax.bar(offsets, means, bar_w, yerr=sems, capsize=4,
               color=TREATMENT_COLORS.get(treat, "#888"),
               edgecolor="black", linewidth=0.7, label=treat)

    ax.axhline(0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xticks(xs); ax.set_xticklabels(cue_types)
    ax.set_ylabel("Reward-Avoidance Index\n(+1 = all reward zone, −1 = all platform)")
    ax.set_title("Reward-Avoidance Index by Cue Type")
    ax.legend(title="Treatment")
    plt.tight_layout()
    fig.savefig(os.path.join(save_path, "reward_avoidance_index.svg"), format="svg")
    plt.close(fig)
    print(f"[ok] Reward-Avoidance Index saved: {save_path}")


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_reward_analysis(data_folder, save_path, cue_dict, cue_dicts=None):
    """Run all three reward analyses for one session."""
    compute_reward_consumption(
        data_folder, os.path.join(save_path, "reward_consumption")
    )
    compute_rewards_in_light_trials(
        data_folder, os.path.join(save_path, "rewards_in_light_trials"), cue_dict, cue_dicts
    )
    compute_reward_avoidance_index(
        data_folder, os.path.join(save_path, "reward_avoidance_index"), cue_dict, cue_dicts
    )
    print("[ok] Reward analysis complete.")
