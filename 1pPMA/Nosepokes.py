# -*- coding: utf-8 -*-
"""
Nosepokes.py  –  Cue-aligned nosepoke analysis.
"""

import os
import numpy as np
import pandas as pd

import math
import matplotlib.pyplot as plt

from utils import (
    analyze_multiple_mice, create_mouse_grid, create_group_mean_grid,
    normalize_metadata_treatments, load_metadata, treatment_from_mouse,
    load_behavior_fractional,
)
from config import CUE_LIGHT_WINDOW, CUE_DURATIONS, POST_TRIAL_BUFFER, SEM_ALPHA


def run_nosepoke_analysis(data_folder, save_path, cue_dict, cue_dicts=None):
    """Full per-session nosepoke analysis: combined CSV → group grid → per-mouse grid → AUC → session histogram."""
    prefix = analyze_multiple_mice(
        data_folder, save_path, cue_dict,
        behavior_col="Nose poke active",
        file_suffix="nosepoke",
        cue_dicts=cue_dicts,
    )
    if prefix is None:
        return
    create_group_mean_grid(
        save_path, cue_dict, data_folder, prefix,
        file_suffix="nosepoke",
        ylabel="Probability in Noseport\n(mean ± SEM)",
    )
    create_mouse_grid(save_path, cue_dict, prefix, file_suffix="nosepoke")
    compute_nosepoke_light_auc(save_path, data_folder, cue_dict, prefix)
    plot_session_nosepoke_histogram(data_folder, save_path, cue_dict)
    print("✅  Nosepoke analysis complete.")


# ── Full-session binned histogram ──────────────────────────────────────────────

def plot_session_nosepoke_histogram(data_folder, save_path, cue_dict, bin_size=30,
                                    images_per_row=4, file_ext="svg"):
    """
    Full-session nosepoke histogram: total nosepoke seconds per 30 s bin,
    one panel per animal, cue periods highlighted.

    This is a whole-session view (not cue-aligned) that shows absolute
    nosepoke counts across time.  Useful for spotting session-wide
    engagement patterns and verifying cue timing.

    Method:
      - Resample to 1 Hz (fractional).
      - Divide session into non-overlapping 30 s bins.
      - Sum Nose poke active within each bin (= seconds spent nosepoking).
      - Shade the cue-active periods using cue_dict onsets + CUE_DURATIONS.

    Outputs:
      session_nosepoke_histogram.svg  – grid of per-animal bar charts
    """
    mice_data = {}
    for file in sorted(os.listdir(data_folder)):
        if not file.endswith(".csv") or file.endswith("_downsampled.csv"):
            continue
        mouse_id = file.replace(".csv", "")
        df = load_behavior_fractional(os.path.join(data_folder, file))
        if "Nose poke active" not in df.columns:
            continue

        total_time = int(df["Time (s)"].max())
        n_bins = total_time // bin_size
        bins, counts = [], []
        for i in range(n_bins):
            t0, t1 = i * bin_size, (i + 1) * bin_size
            segment = df[(df["Time (s)"] >= t0) & (df["Time (s)"] < t1)]["Nose poke active"]
            bins.append(t0)
            counts.append(float(segment.sum()))
        mice_data[mouse_id] = (bins, counts)

    if not mice_data:
        print("⏭️   No session nosepoke data to plot.")
        return

    n_mice = len(mice_data)
    n_rows = math.ceil(n_mice / images_per_row)
    fig, axes = plt.subplots(n_rows, images_per_row,
                             figsize=(images_per_row * 5, n_rows * 3.5),
                             squeeze=False)
    axes = axes.flatten()

    for idx, (mouse_id, (bins, counts)) in enumerate(mice_data.items()):
        ax = axes[idx]
        ax.bar(bins, counts, width=bin_size, align="edge", edgecolor="black",
               color="#4C9BE8", linewidth=0.5)

        # Shade every detected cue period
        shaded = {}
        for cue_type, onsets in cue_dict.items():
            dur = CUE_DURATIONS.get(cue_type, 30)
            color = {"Light": "yellow", "Tone": "salmon",
                     "Conflict": "orange", "Light-Tone": "khaki",
                     "Tone-Light": "lightsalmon"}.get(cue_type, "lightgray")
            for t in onsets:
                label = cue_type if cue_type not in shaded else ""
                ax.axvspan(t, t + dur, color=color, alpha=0.35, label=label)
                shaded[cue_type] = True

        ax.set_title(mouse_id, fontsize=8)
        ax.set_xlabel("Time (s)", fontsize=7)
        ax.set_ylabel("Nosepoke (s / bin)", fontsize=7)
        ax.tick_params(labelsize=7)

    # Legend on first panel only
    if mice_data:
        axes[0].legend(fontsize=6, loc="upper right", framealpha=0.7)

    for j in range(n_mice, len(axes)):
        axes[j].axis("off")

    plt.tight_layout()
    out = os.path.join(save_path, "session_nosepoke_histogram.svg")
    fig.savefig(out, format=file_ext)
    plt.close(fig)
    print(f"🖼️   Session nosepoke histogram saved: {out}")


# ── Per-mouse AUC during LIGHT window ─────────────────────────────────────────

def compute_nosepoke_light_auc(save_path, data_folder, cue_dict, prefix,
                                baseline_window=(-10, 0)):
    """
    Per-mouse baseline-subtracted AUC during LIGHT-ON only.
    Saves {prefix}_nosepoke_LIGHT_auc_per_mouse.csv  (consumed by ACROSS_SESSIONS_AUC).
    """
    csv_path = os.path.join(save_path, f"{prefix}_combined_nosepoke_summary.csv")
    if not os.path.exists(csv_path):
        print("❌  Nosepoke combined summary not found.")
        return None

    df_all = pd.read_csv(csv_path)
    metadata = normalize_metadata_treatments(load_metadata(data_folder))
    light_onset_map = {k: v[0] for k, v in CUE_LIGHT_WINDOW.items()}
    cues_to_use = [c for c in cue_dict if c in light_onset_map and (df_all["Cue"] == c).any()]
    if not cues_to_use:
        print("⏭️  No light-containing cues in nosepoke summary.")
        return None

    rows = []
    for cue in cues_to_use:
        onset = light_onset_map[cue]
        base_lo, base_hi = onset + baseline_window[0], onset + baseline_window[1]
        light_lo, light_hi = CUE_LIGHT_WINDOW[cue]
        for mouse_id, dm in df_all[df_all["Cue"] == cue].groupby("Mouse ID"):
            trt = treatment_from_mouse(mouse_id, metadata)
            if trt == "Unknown":
                continue
            base = dm[(dm["Time (s)"] >= base_lo) & (dm["Time (s)"] <= base_hi)]
            seg = dm[(dm["Time (s)"] >= light_lo) & (dm["Time (s)"] <= light_hi)]
            if base.empty or seg.empty:
                continue
            baseline_mean = base["Mean"].mean()
            t, y = seg["Time (s)"].values, seg["Mean"].values
            auc_bs = np.trapz(y - baseline_mean, t)
            rows.append({
                "Mouse ID": mouse_id, "treatment_group": trt, "Cue": cue,
                "baseline_mean": baseline_mean, "auc_raw": np.trapz(y, t),
                "auc_baseline_subtracted": auc_bs,
                "auc_per_sec": auc_bs / (light_hi - light_lo),
            })

    if not rows:
        print("⏭️  No AUC rows computed.")
        return None
    out = pd.DataFrame(rows)
    out_path = os.path.join(save_path, f"{prefix}_nosepoke_LIGHT_auc_per_mouse.csv")
    out.to_csv(out_path, index=False)
    print(f"📄  Nosepoke LIGHT AUC per-mouse saved: {out_path}")
    return out_path
