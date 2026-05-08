# -*- coding: utf-8 -*-
"""
runner.py - Single entry point and configuration home for all PMA analyses.

Edit the settings at the top of this file, then run this file.
"""

import os
import sys

# When this file is run as a script, analysis modules still import settings from
# "runner". This alias points those imports at this already-running module.
sys.modules["runner"] = sys.modules[__name__]


# ── Data directories ─────────────────────────────────────────────────────────
DATA_ROOT = (
    r"Z:\path\to\your\BehaviorData"
)

# Set to a folder name (e.g. "VC03") to process only that session; None = all sessions
SESSION_FILTER = None

# Top-level output folder created inside DATA_ROOT
OUTPUT_DIR_NAME = "Analysis"


# ── Analysis flags ───────────────────────────────────────────────────────────
RUN_DOWNSAMPLE             = True
RUN_TIME_ON_PLATFORM       = True
RUN_NOSEPOKES              = True
RUN_PLATFORM_LATENCY       = True
RUN_SHOCK_SUMMARY          = True   # VC sessions only
RUN_REWARDS                = True   # total consumption + rewards in light trials + R-A index
RUN_ACROSS_SESSION_AUC     = True
RUN_ACROSS_SESSION_LATENCY = True
RUN_ACROSS_SESSION_SHOCKS  = True   # VC sessions only


# ── Treatment labels ─────────────────────────────────────────────────────────
CONTROL_TREATMENT = "ctrl"

TREATMENT_ALIASES = {
    "ctrl": "ctrl", "Ctrl": "ctrl", "CTRL": "ctrl", "TdTOM": "ctrl",
    "ko":   "KO",   "Cre":  "KO",
}

EXCLUDED_TREATMENTS = {"misc"}


# ── Plot colors ──────────────────────────────────────────────────────────────
TREATMENT_COLORS = {
    "ctrl":    "#3B3B38",
    "KO":      "#1EB2F2",
    "Unknown": "#888888",
}
TREATMENT_SEM_COLORS = {
    "ctrl":    "#B0B0AE",
    "KO":      "#A4DAF7",
    "Unknown": "#CCCCCC",
}
SEM_ALPHA = 0.65

OUTCOME_COLORS = {
    "Avoided":       "#368ebf",
    "Escaped":       "orange",
    "Fully_Shocked": "lightcoral",
}


# ── Task timing ──────────────────────────────────────────────────────────────
CUE_DURATIONS = {
    "Light":      30,
    "Tone":       30,
    "Conflict":   30,
    "Light-Tone": 45,
    "Tone-Light": 45,
}
POST_TRIAL_BUFFER = 30   # seconds after cue offset included in cue-aligned window

LIGHT_DURATION = 30
LIGHT_ONSET = {          # seconds from trial start to light onset, per cue type
    "Light":      0,
    "Conflict":   0,
    "Light-Tone": 0,
    "Tone-Light": 15,
}
LIGHT_CUES       = set(LIGHT_ONSET.keys())
CUE_LIGHT_WINDOW = {k: (v, v + LIGHT_DURATION) for k, v in LIGHT_ONSET.items()}

CUE_ORDER_DEFAULT = ["Light", "Tone", "Conflict", "Light-Tone", "Tone-Light"]


# ── Cue dictionary ───────────────────────────────────────────────────────────
# Controls how trial onset times are determined.
#
# "config"     -> use CUE_DICT defined below (same times for every animal/session)
# "first_csv"  -> auto-detect from the first animal's downsampled CSV each session
# "per_animal" -> auto-detect independently from each animal's own downsampled CSV
#
# The "config" option name is kept for backward compatibility; the actual
# CUE_DICT now lives here in runner.py.
CUE_DICT_SOURCE = "first_csv"

# Used only when CUE_DICT_SOURCE = "config".
# Onset times are in seconds from the start of the recording.
# All five keys must be present; set unused trial types to empty lists.
CUE_DICT = {
    "Light":      [],
    "Tone":       [],
    "Conflict":   [],
    "Light-Tone": [],
    "Tone-Light": [],
}


# ── Reward parameters ────────────────────────────────────────────────────────
FEEDER_RATE_UL_PER_SEC = 16   # uL dispensed per second of Feeder active; adjust for your pump


# ── Helpers ─────────────────────────────────────────────────────────────────
def _find_sessions(data_root, session_filter):
    """Return sorted list of REW* / VC* session folder names."""
    all_sessions = sorted(
        n for n in os.listdir(data_root)
        if os.path.isdir(os.path.join(data_root, n))
        and (n.startswith("REW") or n.startswith("VC"))
    )
    if session_filter:
        if session_filter not in all_sessions:
            sys.exit(f"[error] SESSION_FILTER '{session_filter}' not found in {data_root}")
        return [session_filter]
    return all_sessions


def _session_out(analysis_root, session):
    return os.path.join(analysis_root, session)


# ── Per-session loop ─────────────────────────────────────────────────────────
def run_sessions(data_root, analysis_root, sessions):
    from utils import save_downsampled_csvs, get_session_cue_dict, extract_cue_dict
    from Time_on_Platform import run_time_on_platform_analysis
    from Nosepokes import run_nosepoke_analysis
    from PlatformLatency import run_latency_pma
    from Variable_Conflict_Shocks import run_shock_summary
    from Rewards import run_reward_analysis

    for session in sessions:
        data_folder = os.path.join(data_root, session)
        out_base = _session_out(analysis_root, session)
        print(f"\n{'='*70}\nSession: {session}\n{'='*70}")

        # Downsample
        if RUN_DOWNSAMPLE:
            print("[run] Downsampling...")
            save_downsampled_csvs(data_folder)

        # Cue dictionary resolution
        downsampled_folder = os.path.join(data_folder, "downsampled")
        cue_dict = None    # single shared dict (used by all modes as the canonical/fallback)
        cue_dicts = None   # per-animal dict map (only populated in per_animal mode)

        if CUE_DICT_SOURCE == "config":
            cue_dict = CUE_DICT
            print("   cue_dict: using CUE_DICT from runner.py")

        elif CUE_DICT_SOURCE == "first_csv":
            if not os.path.isdir(downsampled_folder):
                print(f"[warn] No downsampled/ folder; skipping {session}.")
                continue
            cue_dict = get_session_cue_dict(downsampled_folder)
            if cue_dict is None:
                print(f"[warn] Could not extract cue_dict from first CSV; skipping {session}.")
                continue

        elif CUE_DICT_SOURCE == "per_animal":
            if not os.path.isdir(downsampled_folder):
                print(f"[warn] No downsampled/ folder; skipping {session}.")
                continue
            cue_dicts = extract_cue_dict(downsampled_folder)
            if not cue_dicts:
                print(f"[warn] Could not extract per-animal cue dicts; skipping {session}.")
                continue
            # Use the first animal's dict as a fallback for any functions
            # that still require a single cue_dict argument.
            cue_dict = next(iter(cue_dicts.values()))
            print(f"   cue_dict: per-animal mode ({len(cue_dicts)} animals detected)")

        else:
            print(f"[warn] Unknown CUE_DICT_SOURCE '{CUE_DICT_SOURCE}'; skipping {session}.")
            continue

        # Time on platform
        if RUN_TIME_ON_PLATFORM:
            print("[run] Time-on-platform...")
            try:
                run_time_on_platform_analysis(
                    data_folder,
                    save_path=os.path.join(out_base, "time_on_platform"),
                    cue_dict=cue_dict,
                    cue_dicts=cue_dicts,
                )
            except Exception as e:
                print(f"[error] {e}")

        # Nosepokes
        if RUN_NOSEPOKES:
            print("[run] Nosepokes...")
            try:
                run_nosepoke_analysis(
                    data_folder,
                    save_path=os.path.join(out_base, "nosepokes"),
                    cue_dict=cue_dict,
                    cue_dicts=cue_dicts,
                )
            except Exception as e:
                print(f"[error] {e}")

        # Platform latency
        if RUN_PLATFORM_LATENCY:
            print("[run] Platform latency...")
            try:
                run_latency_pma(
                    data_folder,
                    save_path=os.path.join(out_base, "latency"),
                    cue_dict=cue_dict,
                    cue_dicts=cue_dicts,
                )
            except Exception as e:
                print(f"[error] {e}")

        # Shock summary (VC only)
        if RUN_SHOCK_SUMMARY and session.startswith("VC"):
            print("[run] Shock summary...")
            try:
                run_shock_summary(
                    data_folder,
                    save_path=os.path.join(out_base, "shocks"),
                )
            except Exception as e:
                print(f"[error] {e}")

        # Reward analyses (all sessions)
        if RUN_REWARDS:
            print("[run] Reward analysis...")
            try:
                run_reward_analysis(
                    data_folder,
                    save_path=os.path.join(out_base, "rewards"),
                    cue_dict=cue_dict,
                    cue_dicts=cue_dicts,
                )
            except Exception as e:
                print(f"[error] {e}")


# ── Across-session analyses ─────────────────────────────────────────────────
def run_across_sessions(data_root, analysis_root):
    from utils import load_metadata_from_root
    from PlatformLatency import run_across_session_latency_summary
    from Variable_Conflict_Shocks import run_across_session_summary
    from ACROSS_SESSIONS_AUC import run_across_session_auc

    across_base = os.path.join(analysis_root, "across_sessions")

    if RUN_ACROSS_SESSION_LATENCY:
        print("\n[run] Across-session latency...")
        try:
            run_across_session_latency_summary(
                analysis_root,
                save_folder=os.path.join(across_base, "latency"),
            )
        except Exception as e:
            print(f"[error] {e}")

    if RUN_ACROSS_SESSION_SHOCKS:
        print("\n[run] Across-session shocks...")
        try:
            run_across_session_summary(
                analysis_root,
                save_folder=os.path.join(across_base, "shocks"),
            )
        except Exception as e:
            print(f"[error] {e}")

    if RUN_ACROSS_SESSION_AUC:
        print("\n[run] Across-session AUC...")
        try:
            metadata_df = load_metadata_from_root(data_root)
            run_across_session_auc(
                analysis_root,
                metadata_df=metadata_df,
                save_folder=os.path.join(across_base, "auc"),
            )
        except Exception as e:
            print(f"[error] {e}")


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    analysis_root = os.path.join(DATA_ROOT, OUTPUT_DIR_NAME)
    os.makedirs(analysis_root, exist_ok=True)

    sessions = _find_sessions(DATA_ROOT, SESSION_FILTER)
    print(f"Sessions to process: {sessions}")
    print(f"Output root: {analysis_root}")

    run_sessions(DATA_ROOT, analysis_root, sessions)
    run_across_sessions(DATA_ROOT, analysis_root)

    print("\n[ok] All analyses complete.")


if __name__ == "__main__":
    main()
