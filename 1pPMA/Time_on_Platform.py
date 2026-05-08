# -*- coding: utf-8 -*-
"""
Time_on_Platform.py  –  Cue-aligned time-on-platform analysis.
"""

import os
import numpy as np
import pandas as pd

from utils import (
    analyze_multiple_mice, create_mouse_grid, create_group_mean_grid,
    load_behavior_fractional, normalize_metadata_treatments,
    load_metadata, treatment_from_mouse,
)
from runner import CUE_LIGHT_WINDOW, LIGHT_CUES


def run_time_on_platform_analysis(data_folder, save_path, cue_dict, cue_dicts=None):
    """Full per-session platform analysis: combined CSV → group grid → per-mouse grid."""
    prefix = analyze_multiple_mice(
        data_folder, save_path, cue_dict,
        behavior_col="In platform",
        file_suffix="platform",
        cue_dicts=cue_dicts,
    )
    if prefix is None:
        return
    create_group_mean_grid(
        save_path, cue_dict, data_folder, prefix,
        file_suffix="platform",
        ylabel="Probability on Platform\n(mean ± SEM)",
    )
    create_mouse_grid(save_path, cue_dict, prefix, file_suffix="platform")
    print("✅  Time-on-platform analysis complete.")


# ── Across-session platform AUC (light window) ────────────────────────────────

def compute_platform_light_auc(save_path, data_folder, cue_dict, prefix,
                                baseline_window=(-10, 0)):
    """
    Per-mouse baseline-subtracted AUC during the LIGHT-ON window.
    Saves {prefix}_platform_LIGHT_auc_per_mouse.csv.
    """
    csv_path = os.path.join(save_path, f"{prefix}_combined_platform_summary.csv")
    if not os.path.exists(csv_path):
        print("❌  Platform combined summary not found.")
        return None

    df_all = pd.read_csv(csv_path)
    metadata = normalize_metadata_treatments(load_metadata(data_folder))
    light_onset_map = {k: v[0] for k, v in CUE_LIGHT_WINDOW.items()}
    cues_to_use = [c for c in cue_dict if c in light_onset_map and (df_all["Cue"] == c).any()]
    if not cues_to_use:
        print("⏭️  No light-containing cues in platform summary.")
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
        return None
    out = pd.DataFrame(rows)
    out_path = os.path.join(save_path, f"{prefix}_platform_LIGHT_auc_per_mouse.csv")
    out.to_csv(out_path, index=False)
    print(f"📄  Platform LIGHT AUC per-mouse saved: {out_path}")
    return out_path
