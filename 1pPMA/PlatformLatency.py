# -*- coding: utf-8 -*-
"""
PlatformLatency.py  –  Latency-to-platform analysis (within- and across-session).
"""

import os
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from utils import (
    load_behavior_fractional, normalize_metadata_treatments,
    load_metadata, treatment_from_mouse,
)
from config import (
    CUE_DURATIONS, TREATMENT_COLORS, TREATMENT_SEM_COLORS, SEM_ALPHA,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_behavior_id(mouse_id):
    parts = mouse_id.split("_")
    if len(parts) < 3:
        raise ValueError(f"Cannot parse Behavior_ID from: {mouse_id}")
    return parts[1]


def _nearest_idx(times, t):
    return int(np.nanargmin(np.abs(times - t)))


# ── Per-mouse latency computation ──────────────────────────────────────────────

def compute_latency_single_mouse(df, cue_dict, mouse_id, window_len=35.0):
    """
    Compute latency to platform for every trial of every cue type.
    Latency measured from tone onset (Light-Tone: tone onset = trial start + 15 s).
    Returns a list of row dicts.
    """
    time = df["Time (s)"].values
    plat = df["In platform"].fillna(0).values
    behavior_id = _extract_behavior_id(mouse_id)
    rows = []

    for trial_type, trial_times in cue_dict.items():
        for i, t_trial in enumerate(sorted(trial_times), start=1):
            tone_onset = t_trial + (CUE_DURATIONS.get("Light", 15) if trial_type == "Light-Tone" else 0)
            idx = np.where((time >= tone_onset) & (time <= tone_onset + window_len))[0]
            if idx.size == 0:
                continue
            onset_idx = idx[0]
            if plat[onset_idx] >= 0.5:
                latency, never_on = 0.0, False
            else:
                after = np.where(plat[idx] >= 0.5)[0]
                if after.size > 0:
                    latency, never_on = float(time[idx[after[0]]] - tone_onset), False
                else:
                    latency, never_on = np.nan, True
            rows.append(dict(
                Mouse_ID=mouse_id, Behavior_ID=behavior_id,
                Trial_Type=trial_type, Trial_Index=i,
                Tone_Onset_s=tone_onset, Window_Length_s=window_len,
                Latency_s=latency, Never_On_Platform=never_on,
            ))
    return rows


# ── Within-session runner ──────────────────────────────────────────────────────

def run_latency_pma(data_folder, save_path, cue_dict, cue_dicts=None):
    """Compute per-trial latency for all mice and save latency_summary.csv."""
    os.makedirs(save_path, exist_ok=True)
    metadata = normalize_metadata_treatments(load_metadata(data_folder))
    all_rows = []

    for file in os.listdir(data_folder):
        if not file.endswith(".csv") or file.endswith("_downsampled.csv"):
            continue
        mouse_id = file.replace(".csv", "")
        animal_cue = cue_dicts.get(mouse_id, cue_dict) if cue_dicts else cue_dict
        df = load_behavior_fractional(os.path.join(data_folder, file))
        all_rows.extend(compute_latency_single_mouse(df, animal_cue, mouse_id))

    if not all_rows:
        print("⏭️  No latency rows produced.")
        return

    df_lat = pd.DataFrame(all_rows)
    df_lat["Session"] = df_lat["Mouse_ID"].astype(str).str.split("_").str[-1]
    df_lat["treatment_group"] = df_lat["Mouse_ID"].apply(
        lambda mid: treatment_from_mouse(mid, metadata)
    ).fillna("Unknown")
    df_lat = df_lat[~df_lat["treatment_group"].str.lower().isin({"misc", "unknown"})]
    df_lat["Cue"] = df_lat["Trial_Type"]

    out = os.path.join(save_path, "latency_summary.csv")
    df_lat.to_csv(out, index=False)
    print(f"📄  Latency summary saved: {out}")


# ── Across-session collectors ──────────────────────────────────────────────────

def collect_latency_across_sessions(analysis_root):
    """
    Collect latency_summary.csv from all VC* session subfolders under analysis_root.
    Expects: analysis_root/VC*/latency/latency_summary.csv
    """
    dfs = []
    for sub in sorted(os.listdir(analysis_root)):
        if not sub.lower().startswith("vc"):
            continue
        csv_path = os.path.join(analysis_root, sub, "latency", "latency_summary.csv")
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            df["Session"] = sub
            dfs.append(df)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


# ── Across-session plots ───────────────────────────────────────────────────────

def _plot_latency_line(latency_df, save_folder, panel_cols=2):
    """Mean latency ± SEM per session, one panel per trial type."""
    df = latency_df.dropna(subset=["Trial_Type", "Latency_s"]).copy()
    df["Trial_Type"] = df["Trial_Type"].astype(str)
    trial_order = sorted(df["Trial_Type"].unique())
    treat_order = sorted(df["treatment_group"].unique())
    session_order = sorted(df["Session"].unique())
    xs = np.arange(len(session_order))
    x_map = {s: i for i, s in enumerate(session_order)}
    n_rows = math.ceil(len(trial_order) / panel_cols)

    fig, axes = plt.subplots(n_rows, panel_cols,
                             figsize=(panel_cols * 6, n_rows * 4.8),
                             sharey=True, squeeze=False)
    for i, tt in enumerate(trial_order):
        r, c = divmod(i, panel_cols)
        ax = axes[r, c]
        df_tt = df[df["Trial_Type"] == tt]
        for treat in treat_order:
            df_tr = df_tt[df_tt["treatment_group"] == treat]
            if df_tr.empty:
                continue
            color = TREATMENT_COLORS.get(treat, "#666")
            ax.scatter(df_tr["Session"].map(x_map), df_tr["Latency_s"],
                       s=15, alpha=0.25, color=color, zorder=1)
            mouse_means = (df_tr.groupby(["Mouse_ID", "Session"])["Latency_s"]
                           .mean().reset_index())
            stats = (mouse_means.groupby("Session")["Latency_s"]
                     .agg(["mean", "sem"]).reindex(session_order))
            ax.plot(xs, stats["mean"], lw=2.5, color=color, zorder=3)
            ax.fill_between(xs, stats["mean"] - stats["sem"], stats["mean"] + stats["sem"],
                            color=TREATMENT_SEM_COLORS.get(treat, "#aaa"),
                            alpha=SEM_ALPHA, zorder=2)
        ax.set_title(tt)
        ax.set_xticks(xs)
        ax.set_xticklabels(session_order, rotation=45)
        if c == 0:
            ax.set_ylabel("Latency to Platform (s)")
    for j in range(len(trial_order), n_rows * panel_cols):
        r, c = divmod(j, panel_cols)
        axes[r, c].axis("off")
    fig.tight_layout()
    fig.savefig(os.path.join(save_folder, "latency_across_sessions.svg"))
    plt.close(fig)


def _plot_never_on_platform(latency_df, save_folder, panel_cols=2):
    """% trials never reaching platform per session, one panel per cue."""
    df = latency_df.copy()
    df["Cue"] = df["Trial_Type"]
    df["Never_On_Platform"] = df["Never_On_Platform"].astype(bool)
    cue_order = sorted(df["Cue"].unique())
    treat_order = sorted(df["treatment_group"].unique())
    session_order = sorted(df["Session"].unique())
    xs = np.arange(len(session_order))
    session_to_x = {s: i for i, s in enumerate(session_order)}
    n_rows = math.ceil(len(cue_order) / panel_cols)

    fig, axes = plt.subplots(n_rows, panel_cols,
                             figsize=(panel_cols * 5.8, n_rows * 4.6),
                             sharey=True, squeeze=False)
    fig.suptitle("Probability of Never Reaching Platform", fontsize=14, y=0.98)

    for i, cue in enumerate(cue_order):
        r, c = divmod(i, panel_cols)
        ax = axes[r, c]
        df_cue = df[df["Cue"] == cue]
        for treat in treat_order:
            df_t = df_cue[df_cue["treatment_group"] == treat]
            if df_t.empty:
                continue
            color = TREATMENT_COLORS.get(treat, "#555555")
            ax.scatter(df_t["Session"].map(session_to_x),
                       df_t["Never_On_Platform"].astype(int) * 100,
                       s=16, alpha=0.15, color=color, zorder=1)
            mouse_rates = (df_t.groupby(["Mouse_ID", "Session"])["Never_On_Platform"]
                           .mean().reset_index())
            mouse_rates["Percent"] = mouse_rates["Never_On_Platform"] * 100
            for _, mdf in mouse_rates.groupby("Mouse_ID"):
                ax.plot(mdf["Session"].map(session_to_x), mdf["Percent"],
                        color=color, alpha=0.25, linewidth=1, zorder=2)
            stats = (mouse_rates.groupby("Session")["Percent"]
                     .agg(["mean", "sem"]).reindex(session_order))
            ax.plot(xs, stats["mean"], color=color, linewidth=2.8, zorder=3)
            ax.fill_between(xs, stats["mean"] - stats["sem"], stats["mean"] + stats["sem"],
                            color=TREATMENT_SEM_COLORS.get(treat, "#CCCCCC"),
                            alpha=SEM_ALPHA, zorder=2)
        ax.set_title(cue)
        ax.set_xticks(xs)
        ax.set_xticklabels(session_order, rotation=45, ha="right", fontsize=9)
        ax.set_ylim(0, 100)
        ax.grid(True, axis="y", linestyle="--", linewidth=0.4, alpha=0.6)
        if c == 0:
            ax.set_ylabel("% Trials Never on Platform")

    for j in range(len(cue_order), n_rows * panel_cols):
        r, c = divmod(j, panel_cols)
        axes[r, c].axis("off")

    handles = [plt.Line2D([0], [0], color=TREATMENT_COLORS.get(t, "#555"), lw=3, label=t)
               for t in treat_order]
    fig.legend(handles, treat_order, title="Treatment",
               loc="upper center", bbox_to_anchor=(0.5, 0.92),
               ncol=len(treat_order), frameon=True)
    fig.tight_layout(rect=[0, 0, 1, 0.9])
    fig.savefig(os.path.join(save_folder, "never_on_platform_across_sessions.svg"),
                bbox_inches="tight")
    plt.close(fig)
    print(f"✅  Never-on-platform plot saved: {save_folder}")


# ── Across-session runner ──────────────────────────────────────────────────────

def run_across_session_latency_summary(analysis_root, save_folder):
    """Collect latency data from all VC sessions and produce across-session plots + CSV."""
    df = collect_latency_across_sessions(analysis_root)
    if df.empty:
        print("⏭️  No across-session latency data found.")
        return
    os.makedirs(save_folder, exist_ok=True)
    _plot_latency_line(df, save_folder)
    _plot_never_on_platform(df, save_folder)
    df.to_csv(os.path.join(save_folder, "latency_combined.csv"), index=False)
    print(f"✅  Across-session latency summary complete: {save_folder}")
