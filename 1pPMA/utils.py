# -*- coding: utf-8 -*-
"""
utils.py  –  Core helpers shared across all analysis modules.
Constants live in runner.py; import from there.
"""

import os
import re
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

from runner import (
    CONTROL_TREATMENT, TREATMENT_ALIASES, EXCLUDED_TREATMENTS,
    TREATMENT_COLORS, TREATMENT_SEM_COLORS, SEM_ALPHA,
    CUE_DURATIONS, POST_TRIAL_BUFFER, LIGHT_CUES, CUE_LIGHT_WINDOW,
    CUE_ORDER_DEFAULT,
)


# ── Metadata ───────────────────────────────────────────────────────────────────

def load_metadata(data_folder, filename="animals_metadata.xlsx"):
    """Load metadata from one level above data_folder."""
    path = os.path.join(os.path.dirname(data_folder), filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Metadata not found: {path}")
    df = pd.read_excel(path) if path.endswith(".xlsx") else pd.read_csv(path)
    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
    return df


def load_metadata_from_root(data_root, filename="animals_metadata.xlsx"):
    """Load metadata directly from data_root (for across-session calls)."""
    path = os.path.join(data_root, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Metadata not found: {path}")
    df = pd.read_excel(path) if path.endswith(".xlsx") else pd.read_csv(path)
    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
    return df


def _normalize_label(x):
    if not isinstance(x, str):
        return "Unknown"
    key = x.strip()
    return TREATMENT_ALIASES.get(key, TREATMENT_ALIASES.get(key.lower(), key))


def normalize_metadata_treatments(metadata):
    md = metadata.copy()
    md.columns = md.columns.str.lower()
    if "behavior_id" not in md.columns or "treatment_group" not in md.columns:
        raise ValueError("Metadata must include 'behavior_id' and 'treatment_group'.")
    md = md.set_index("behavior_id")
    md.index = md.index.str.lower()
    md["treatment_group"] = md["treatment_group"].apply(_normalize_label)
    return md


def treatment_from_mouse(mouse_id, md):
    """Extract behavior_id from mouse_id string and look up normalized treatment."""
    parts = str(mouse_id).split("_")
    beh = parts[1].lower() if len(parts) > 1 else None
    if beh is None or beh not in md.index:
        return "Unknown"
    return _normalize_label(md.at[beh, "treatment_group"])


def apply_baseline_category(series, control=CONTROL_TREATMENT):
    present = [v for v in series.dropna().unique() if v != "Unknown"]
    cats = [control] + sorted(c for c in present if c != control) if control in present else sorted(present)
    return pd.Categorical(series, categories=cats, ordered=True)


def legend_sort_key(label, control=CONTROL_TREATMENT):
    return (0 if label == control else 1, label)


# ── File utilities ─────────────────────────────────────────────────────────────

def extract_session_info(filename):
    match = re.match(r"(\d{2}-\d{2}-\d{2})_([A-Za-z0-9]+)_([A-Za-z0-9]+)", filename)
    if match:
        date_str, bid_raw, trial_id = match.groups()
        session_date = datetime.strptime(date_str, "%m-%d-%y").date()
        bid = bid_raw.zfill(2) if bid_raw.isdigit() else bid_raw
        return session_date, bid, trial_id
    return None, None, None


def concatenate_csvs(folder_path, output_filename="combined.csv"):
    output_path = os.path.join(folder_path, output_filename)
    files = [f for f in os.listdir(folder_path) if f.endswith(".csv") and f != output_filename]
    if not files:
        print("⚠️  No CSV files found.")
        return
    pd.concat([pd.read_csv(os.path.join(folder_path, f)) for f in files], ignore_index=True).to_csv(output_path, index=False)
    print(f"✅  Combined CSV saved: {output_path}")


# ── Downsampling ───────────────────────────────────────────────────────────────

def _prepare_numeric_resample_frame(df, time_col="Time (s)"):
    df = df.copy()
    df[time_col] = pd.to_numeric(df[time_col], errors="coerce")
    df = df.dropna(subset=[time_col])
    for col in df.columns:
        if col != time_col:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Timedelta"] = pd.to_timedelta(df[time_col], unit="s")
    return df


def downsample_data(file_path):
    df = pd.read_csv(file_path, encoding="ISO-8859-1")
    drop = ["Head position X", "Head position Y", "Tail position X", "Tail position Y"]
    df = df.drop(columns=[c for c in drop if c in df.columns], errors="ignore")
    df = df.replace(r"^\s*$", np.nan, regex=True)
    df = _prepare_numeric_resample_frame(df)
    df = df.set_index("Timedelta").resample("1s").mean()
    binary_cols = [
        "Freezing", "In platform", "In Grid", "In Reward Zone", "Nose poke active",
        "House light active", "Cue light active", "Feeder active", "ttl active",
        "New speaker active", "New shocker active", "Consumption active",
        "Lost Reward Opportunity active", "Non-Rewarded Poke active",
        "Received Shock Total active",
    ]
    for col in binary_cols:
        if col in df.columns:
            df[col] = (df[col] >= 0.5).astype(int)
    df["Time (s)"] = df.index.total_seconds()
    return df.reset_index(drop=True)


def save_downsampled_csvs(data_folder):
    out_dir = os.path.join(data_folder, "downsampled")
    os.makedirs(out_dir, exist_ok=True)
    for file in os.listdir(data_folder):
        if not file.endswith(".csv"):
            continue
        mouse_id = file.replace(".csv", "")
        df = downsample_data(os.path.join(data_folder, file))
        df.to_csv(os.path.join(out_dir, f"{mouse_id}_downsampled.csv"), index=False)
        print(f"💾  Downsampled: {mouse_id}")


# ── Cue extraction ─────────────────────────────────────────────────────────────

def extract_cue_dict(downsampled_folder):
    """Return {mouse_id: cue_dict} for all downsampled CSVs in folder."""

    def _process(df):
        if "New speaker active" in df.columns:
            df["tone_cue"] = df["New speaker active"]
        elif "Speaker Channel 1 active" in df.columns:
            df["tone_cue"] = df["Speaker Channel 1 active"]
        else:
            raise ValueError("Tone cue column not found.")
        df = df.rename(columns={"House light active": "house_light", "Cue light active": "cue_light"})
        df["time"] = df["Time (s)"]
        df["is_iti"] = (df["house_light"] == 1) & (df["tone_cue"] == 0)
        df["is_iti"] = df["is_iti"].fillna(False).astype(bool)
        iti_ends = df.index[df["is_iti"] & (~df["is_iti"].shift(-1).astype("boolean").fillna(False))].tolist()
        cue_dict = {"Light": [], "Tone-Light": [], "Light-Tone": [], "Tone": [], "Conflict": []}
        for idx in iti_ends:
            start = idx + 1
            if start >= len(df):
                continue
            window = df.iloc[start:start + 50]
            cue_on, tone_on = window["cue_light"] == 1, window["tone_cue"] == 1
            try:
                if not cue_on.any() and tone_on.any():
                    cue_dict["Tone"].append(df.at[start, "time"])
                elif cue_on.any() and not tone_on.any():
                    cue_dict["Light"].append(df.at[start, "time"])
                elif cue_on.idxmax() == tone_on.idxmax():
                    cue_dict["Conflict"].append(df.at[start, "time"])
                elif cue_on.idxmax() > tone_on.idxmax():
                    cue_dict["Tone-Light"].append(df.at[start, "time"])
                else:
                    cue_dict["Light-Tone"].append(df.at[start, "time"])
            except ValueError:
                continue
        return cue_dict

    results = {}
    for file in os.listdir(downsampled_folder):
        if not file.endswith(".csv"):
            continue
        try:
            df = downsample_data(os.path.join(downsampled_folder, file))
            results[os.path.splitext(file)[0]] = _process(df)
        except Exception as e:
            print(f"⚠️  {file}: {e}")
    return results


def get_session_cue_dict(downsampled_folder):
    """Return the cue_dict from the first available mouse in a downsampled folder."""
    cue_dicts = extract_cue_dict(downsampled_folder)
    if not cue_dicts:
        return None
    first = next(iter(cue_dicts))
    print(f"   cue_dict sourced from: {first}")
    return cue_dicts[first]


# ── Plotting helpers ───────────────────────────────────────────────────────────

def draw_cue_bars(ax, cue_label):
    if cue_label == "Light":
        ax.axvspan(0, 30, color="yellow", alpha=0.3, label="Light")
    elif cue_label == "Tone":
        ax.axvspan(0, 30, color="red", alpha=0.3, label="Tone")
    elif cue_label == "Conflict":
        ax.axvspan(0, 30, color="yellow", alpha=0.3, label="Light")
        ax.axvspan(0, 30, color="red", alpha=0.2, label="Tone")
    elif cue_label == "Light-Tone":
        ax.axvspan(0, 30, color="yellow", alpha=0.3, label="Light")
        ax.axvspan(15, 45, color="red", alpha=0.2, label="Tone")
    elif cue_label == "Tone-Light":
        ax.axvspan(0, 30, color="red", alpha=0.2, label="Tone")
        ax.axvspan(15, 45, color="yellow", alpha=0.3, label="Light")


# ── Consolidated cue-aligned analysis ─────────────────────────────────────────

def load_behavior_fractional(file_path):
    """Resample raw behavior CSV to 1 Hz; all binary columns become continuous fractions."""
    df = pd.read_csv(file_path, encoding="ISO-8859-1")
    drop = ["Head position X", "Head position Y", "Tail position X", "Tail position Y"]
    df = df.drop(columns=[c for c in drop if c in df.columns], errors="ignore")
    df = df.replace(r"^\s*$", np.nan, regex=True)
    df = _prepare_numeric_resample_frame(df)
    df_ds = df.set_index("Timedelta").resample("1s").mean()
    df_ds["Time (s)"] = df_ds.index.total_seconds()
    return df_ds.reset_index(drop=True)


def analyze_cue_aligned(df, behavior_col, cue_times, cue_label, mouse_id, all_summaries, window_before=15):
    """
    Align behavior_col traces to cue onsets and append a per-cue summary DataFrame
    to all_summaries.  Combined CSV columns: Mouse ID, Cue, Time (s), Mean, SEM.
    """
    window_after = CUE_DURATIONS.get(cue_label, 30) + POST_TRIAL_BUFFER
    expected_len = window_before + window_after + 1
    cue_onsets = df.index[df["Time (s)"].apply(lambda x: any(abs(x - t) < 0.5 for t in cue_times))]
    aligned = []
    for onset in cue_onsets:
        seg = df[behavior_col].iloc[max(0, onset - window_before):min(len(df) - 1, onset + window_after) + 1].values
        if len(seg) == expected_len:
            aligned.append(seg)
    if not aligned:
        return
    aligned = np.array(aligned)
    mean = aligned.mean(axis=0)
    sem = aligned.std(axis=0) / np.sqrt(aligned.shape[0])
    time_axis = np.arange(-window_before, window_after + 1)
    all_summaries.append(pd.DataFrame({
        "Mouse ID": mouse_id, "Cue": cue_label,
        "Time (s)": time_axis, "Mean": mean, "SEM": sem,
    }))


def analyze_multiple_mice(data_folder, save_path, cue_dict, behavior_col, file_suffix,
                           cue_dicts=None):
    """
    Load every raw CSV in data_folder, run cue-aligned analysis, and save a combined
    summary CSV.  Returns the session prefix string (or None on failure).

    cue_dict:  shared fallback dict used when cue_dicts is None or has no entry for an animal.
    cue_dicts: optional {mouse_id: cue_dict} for per-animal trial times.
    file_suffix: "platform" or "nosepoke"  → {prefix}_combined_{suffix}_summary.csv
    """
    os.makedirs(save_path, exist_ok=True)
    all_summaries, prefix = [], None
    for file in sorted(os.listdir(data_folder)):
        if not file.endswith(".csv") or file.endswith("_downsampled.csv"):
            continue
        mouse_id = file.replace(".csv", "")
        if prefix is None:
            prefix = mouse_id
        animal_cue = cue_dicts.get(mouse_id, cue_dict) if cue_dicts else cue_dict
        df = load_behavior_fractional(os.path.join(data_folder, file))
        for cue_label, cue_times in animal_cue.items():
            if cue_times:
                analyze_cue_aligned(df, behavior_col, cue_times, cue_label, mouse_id, all_summaries)
    if not all_summaries:
        print(f"⏭️  No summaries produced for {file_suffix}.")
        return None
    combined = pd.concat(all_summaries, ignore_index=True)
    out = os.path.join(save_path, f"{prefix}_combined_{file_suffix}_summary.csv")
    combined.to_csv(out, index=False)
    print(f"📄  Combined {file_suffix} summary saved: {out}")
    return prefix


def create_mouse_grid(save_path, cue_dict, prefix, file_suffix, file_ext="svg", images_per_row=5):
    """Per-cue grid of individual mouse cue-aligned traces."""
    csv_path = os.path.join(save_path, f"{prefix}_combined_{file_suffix}_summary.csv")
    if not os.path.exists(csv_path):
        return
    df_all = pd.read_csv(csv_path)
    for cue_label in sorted(cue_dict):
        df_cue = df_all[df_all["Cue"] == cue_label]
        mice = sorted(df_cue["Mouse ID"].unique())
        if not mice:
            continue
        n_rows = math.ceil(len(mice) / images_per_row)
        fig, axes = plt.subplots(n_rows, images_per_row,
                                 figsize=(images_per_row * 4, n_rows * 3), squeeze=False)
        axes = axes.flatten()
        for i, mid in enumerate(mice):
            ax = axes[i]
            dm = df_cue[df_cue["Mouse ID"] == mid]
            draw_cue_bars(ax, cue_label)
            ax.plot(dm["Time (s)"], dm["Mean"], color="black")
            ax.fill_between(dm["Time (s)"], dm["Mean"] - dm["SEM"], dm["Mean"] + dm["SEM"],
                            alpha=SEM_ALPHA, color="#CCCCCC")
            ax.set_xlim(-15, CUE_DURATIONS.get(cue_label, 30) + POST_TRIAL_BUFFER)
            ax.set_ylim(0, 1.0)
            ax.set_title(mid, fontsize=8)
            ax.label_outer()
        for j in range(len(mice), len(axes)):
            axes[j].axis("off")
        plt.tight_layout()
        fig.savefig(os.path.join(save_path, f"{prefix}_grid_{cue_label}.{file_ext}"), format=file_ext)
        plt.close(fig)
    print(f"🖼️  Mouse grids saved: {save_path}")


def create_group_mean_grid(save_path, cue_dict, data_folder, prefix, file_suffix, ylabel,
                           file_ext="svg", images_per_row=3):
    """Grid of group-mean ± SEM traces by treatment, one panel per cue."""
    csv_path = os.path.join(save_path, f"{prefix}_combined_{file_suffix}_summary.csv")
    if not os.path.exists(csv_path):
        return
    df_all = pd.read_csv(csv_path)
    metadata = normalize_metadata_treatments(load_metadata(data_folder))
    cues = [c for c in cue_dict if (df_all["Cue"] == c).any()]
    if not cues:
        return

    n_rows = math.ceil(len(cues) / images_per_row)
    fig, axes = plt.subplots(n_rows, images_per_row,
                             figsize=(images_per_row * 5, n_rows * 4), squeeze=False)

    for i, cue_label in enumerate(cues):
        r, c = divmod(i, images_per_row)
        ax = axes[r][c]
        df_cue = df_all[df_all["Cue"] == cue_label]
        groups = {}
        for mid, dm in df_cue.groupby("Mouse ID"):
            trt = treatment_from_mouse(mid, metadata)
            groups.setdefault(trt, []).append(dm)
        if not groups:
            ax.axis("off")
            continue
        draw_cue_bars(ax, cue_label)
        ax.set_xlim(-15, CUE_DURATIONS.get(cue_label, 30) + POST_TRIAL_BUFFER)
        ax.set_ylim(0, 1.0)
        ax.set_title(cue_label)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(ylabel)
        ax.label_outer()
        for trt in sorted(groups, key=lambda t: legend_sort_key(t)):
            dfs = groups[trt]
            merged = dfs[0][["Time (s)"]].copy()
            for idx, d in enumerate(dfs):
                merged = merged.merge(
                    d[["Time (s)", "Mean"]].rename(columns={"Mean": f"m{idx}"}),
                    on="Time (s)", how="inner"
                )
            t = merged["Time (s)"]
            vals = merged.drop(columns=["Time (s)"])
            m = vals.mean(axis=1).values
            s = vals.std(axis=1).values / np.sqrt(len(dfs))
            ax.plot(t, m, label=trt, color=TREATMENT_COLORS.get(trt, "#000000"))
            ax.fill_between(t, m - s, m + s,
                            color=TREATMENT_SEM_COLORS.get(trt, "#CCCCCC"), alpha=SEM_ALPHA)

    for j in range(len(cues), n_rows * images_per_row):
        r, c = divmod(j, images_per_row)
        axes[r][c].axis("off")

    handles, labels = [], []
    for row in axes:
        for ax in row:
            h, l = ax.get_legend_handles_labels()
            for handle, label in zip(h, l):
                if label not in labels:
                    handles.append(handle)
                    labels.append(label)
    if labels:
        fig.legend(handles, labels, loc="lower center", ncol=len(labels), bbox_to_anchor=(0.5, -0.02))
        plt.tight_layout(rect=[0, 0.05, 1, 1])
    else:
        plt.tight_layout()

    out = os.path.join(save_path, f"{prefix}_group_mean_{file_suffix}.{file_ext}")
    fig.savefig(out, format=file_ext)
    plt.close(fig)
    print(f"✅  Group mean grid saved: {out}")
