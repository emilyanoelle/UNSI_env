# -*- coding: utf-8 -*-
"""
ACROSS_SESSIONS_AUC.py  –  Aggregate per-mouse nosepoke AUC CSVs across sessions and plot.
"""

import os
import glob
import re
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from utils import normalize_metadata_treatments, treatment_from_mouse, legend_sort_key
from config import (
    CONTROL_TREATMENT, TREATMENT_COLORS, CUE_ORDER_DEFAULT,
)


# ── Collection ─────────────────────────────────────────────────────────────────

def _session_index(name):
    m = re.search(r"(\d+)", str(name))
    return int(m.group(1)) if m else np.nan


def _find_auc_col(df):
    for c in df.columns:
        if c.strip().lower() == "auc":
            return c
    cands = [c for c in df.columns if "auc" in c.lower()]
    return cands[0] if cands else None


def collect_nosepoke_auc_across_sessions(analysis_root, metadata_df):
    """
    Walk analysis_root/*/nosepokes/ for per-mouse AUC CSVs and return a long-format DataFrame.
    Expects CSVs containing 'auc' in their filename and a mouse/behavior ID column.
    """
    md = normalize_metadata_treatments(metadata_df)

    sessions = sorted(
        n for n in os.listdir(analysis_root)
        if os.path.isdir(os.path.join(analysis_root, n)) and (n.startswith("REW") or n.startswith("VC"))
    )

    recs = []
    for sess in sessions:
        nosepoke_dir = os.path.join(analysis_root, sess, "nosepokes")
        if not os.path.isdir(nosepoke_dir):
            continue
        auc_csvs = (glob.glob(os.path.join(nosepoke_dir, "*auc*per*mouse*.csv")) or
                    glob.glob(os.path.join(nosepoke_dir, "*auc*.csv")))
        if not auc_csvs:
            continue

        dfs = []
        for fp in auc_csvs:
            try:
                tmp = pd.read_csv(fp)
                if tmp.empty or _find_auc_col(tmp) is None:
                    continue
                if not set(tmp.columns).intersection({"Mouse ID", "Behavior_ID", "behavior_id"}):
                    continue
                dfs.append(tmp)
            except Exception:
                continue
        if not dfs:
            continue

        df = pd.concat(dfs, ignore_index=True)
        auc_col = _find_auc_col(df)
        cue_col = next((c for c in ("Cue", "cue", "Trial_Type") if c in df.columns), None)

        for _, row in df.iterrows():
            mid = str(row.get("Mouse ID", row.get("Behavior_ID", "Unknown"))).strip()
            bid = str(row.get("Behavior_ID", "")).strip().lower() or None
            auc_val = row.get(auc_col, np.nan)
            cue_val = str(row.get(cue_col, "All")).strip() if cue_col else "All"
            cue_val = {"LightTone": "Light-Tone", "ToneLight": "Tone-Light",
                       "light": "Light", "tone": "Tone", "conflict": "Conflict"}.get(cue_val, cue_val)
            if bid and bid in md.index:
                treat = md.at[bid, "treatment_group"]
                sex = md.at[bid, "sex"] if "sex" in md.columns else np.nan
            else:
                treat = treatment_from_mouse(mid, md)
                sex = np.nan
            recs.append({"Session": sess, "SessionIndex": _session_index(sess),
                         "Behavior_ID": bid, "Mouse_ID": mid, "Cue": cue_val,
                         "AUC": auc_val, "Treatment_Group": treat, "Sex": sex})

    if not recs:
        raise RuntimeError("No AUC records found in nosepokes/ subfolders.")
    return pd.DataFrame(recs).sort_values(["Cue", "SessionIndex", "Behavior_ID"])


# ── Plotting ───────────────────────────────────────────────────────────────────

def _mean_sem(values):
    vals = np.asarray(values, dtype=float)
    ok = ~np.isnan(vals)
    if not ok.any():
        return np.nan, np.nan
    m = vals[ok].mean()
    s = vals[ok].std() / np.sqrt(ok.sum()) if ok.sum() > 1 else np.nan
    return m, s


def plot_auc_by_treatment(df_all, save_folder, cue_order=None):
    cue_order = cue_order or CUE_ORDER_DEFAULT
    sessions = sorted(df_all["Session"].unique(), key=_session_index)
    cues = [c for c in cue_order if c in set(df_all["Cue"].unique())] or sorted(df_all["Cue"].unique())
    treatments = sorted(df_all["Treatment_Group"].dropna().unique(), key=legend_sort_key)
    xs = np.arange(len(sessions))

    fig, axes = plt.subplots(1, len(cues), figsize=(6 * len(cues), 5), sharex=True, sharey=True)
    if len(cues) == 1:
        axes = [axes]
    for ax, cue in zip(axes, cues):
        dfc = df_all[df_all["Cue"] == cue]
        for tr in treatments:
            means, sems = zip(*[_mean_sem(dfc.loc[(dfc["Treatment_Group"] == tr) & (dfc["Session"] == s), "AUC"].tolist())
                                 for s in sessions])
            ax.errorbar(xs, means, yerr=sems, fmt="-o", capsize=4,
                        label=tr, color=TREATMENT_COLORS.get(tr, "#000000"))
        ax.set_title(cue)
        ax.set_xticks(xs); ax.set_xticklabels(sessions, rotation=45, ha="right")
        ax.set_xlabel("Session"); ax.grid(True)
    axes[0].set_ylabel("Nosepoke AUC (a.u.)")
    axes[0].legend(title="Treatment")
    plt.tight_layout()
    out = os.path.join(save_folder, "nosepoke_auc_by_treatment.svg")
    plt.savefig(out, format="svg"); plt.close()
    print(f"✅  AUC by treatment saved: {out}")


def plot_auc_by_sex(df_all, save_folder, cue_order=None):
    cue_order = cue_order or CUE_ORDER_DEFAULT
    dfx = df_all[~df_all["Sex"].isna()].copy()
    if dfx.empty:
        print("⚠️  No sex data; skipping sex-stratified AUC plot.")
        return
    sessions = sorted(dfx["Session"].unique(), key=_session_index)
    cues = [c for c in cue_order if c in set(dfx["Cue"].unique())] or sorted(dfx["Cue"].unique())
    sexes = sorted(dfx["Sex"].dropna().unique())
    xs = np.arange(len(sessions))
    fig, axes = plt.subplots(1, len(cues), figsize=(6 * len(cues), 5), sharex=True, sharey=True)
    if len(cues) == 1:
        axes = [axes]
    for ax, cue in zip(axes, cues):
        dfc = dfx[dfx["Cue"] == cue]
        for sx in sexes:
            means, sems = zip(*[_mean_sem(dfc.loc[(dfc["Sex"] == sx) & (dfc["Session"] == s), "AUC"].tolist())
                                 for s in sessions])
            ax.errorbar(xs, means, yerr=sems, fmt="-o", capsize=4, label=str(sx))
        ax.set_title(cue)
        ax.set_xticks(xs); ax.set_xticklabels(sessions, rotation=45, ha="right")
        ax.set_xlabel("Session"); ax.grid(True)
    axes[0].set_ylabel("Nosepoke AUC (a.u.)")
    axes[0].legend(title="Sex")
    plt.tight_layout()
    out = os.path.join(save_folder, "nosepoke_auc_by_sex.svg")
    plt.savefig(out, format="svg"); plt.close()
    print(f"✅  AUC by sex saved: {out}")


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_across_session_auc(analysis_root, metadata_df, save_folder, cue_order=None):
    """Collect → CSV → plots."""
    os.makedirs(save_folder, exist_ok=True)
    df_all = collect_nosepoke_auc_across_sessions(analysis_root, metadata_df)
    df_all.to_csv(os.path.join(save_folder, "nosepoke_auc_long.csv"), index=False)
    plot_auc_by_treatment(df_all, save_folder, cue_order=cue_order)
    plot_auc_by_sex(df_all, save_folder, cue_order=cue_order)
    print(f"✅  Across-session AUC complete: {save_folder}")
