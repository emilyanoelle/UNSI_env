# -*- coding: utf-8 -*-
"""
runner.py  –  Single entry point for all PMA analyses.

Configure everything in config.py, then run this file.
"""

import os
import sys

# ── Config ─────────────────────────────────────────────────────────────────────
from config import (
    DATA_ROOT, SESSION_FILTER, OUTPUT_DIR_NAME,
    RUN_DOWNSAMPLE, RUN_TIME_ON_PLATFORM, RUN_NOSEPOKES,
    RUN_PLATFORM_LATENCY, RUN_SHOCK_SUMMARY, RUN_REWARDS,
    RUN_ACROSS_SESSION_AUC, RUN_ACROSS_SESSION_LATENCY, RUN_ACROSS_SESSION_SHOCKS,
    CUE_DICT_SOURCE, CUE_DICT,
)

# ── Utils ──────────────────────────────────────────────────────────────────────
from utils import (
    save_downsampled_csvs, get_session_cue_dict, extract_cue_dict,
    load_metadata_from_root, normalize_metadata_treatments,
)

# ── Analysis modules ───────────────────────────────────────────────────────────
from Time_on_Platform       import run_time_on_platform_analysis
from Nosepokes              import run_nosepoke_analysis
from PlatformLatency        import run_latency_pma, run_across_session_latency_summary
from Variable_Conflict_Shocks import run_shock_summary, run_across_session_summary
from ACROSS_SESSIONS_AUC    import run_across_session_auc
from Rewards                import run_reward_analysis


# ── Helpers ────────────────────────────────────────────────────────────────────

def _find_sessions(data_root, session_filter):
    """Return sorted list of REW* / VC* session folder names."""
    all_sessions = sorted(
        n for n in os.listdir(data_root)
        if os.path.isdir(os.path.join(data_root, n))
        and (n.startswith("REW") or n.startswith("VC"))
    )
    if session_filter:
        if session_filter not in all_sessions:
            sys.exit(f"❌  SESSION_FILTER '{session_filter}' not found in {data_root}")
        return [session_filter]
    return all_sessions


def _session_out(analysis_root, session):
    return os.path.join(analysis_root, session)


# ── Per-session loop ───────────────────────────────────────────────────────────

def run_sessions(data_root, analysis_root, sessions):
    for session in sessions:
        data_folder = os.path.join(data_root, session)
        out_base    = _session_out(analysis_root, session)
        print(f"\n{'='*70}\n📂  {session}\n{'='*70}")

        # Downsample
        if RUN_DOWNSAMPLE:
            print("🔽  Downsampling...")
            save_downsampled_csvs(data_folder)

        # ── Cue dictionary resolution ──────────────────────────────────────────
        downsampled_folder = os.path.join(data_folder, "downsampled")
        cue_dict  = None   # single shared dict (used by all modes as the canonical/fallback)
        cue_dicts = None   # per-animal dict map (only populated in per_animal mode)

        if CUE_DICT_SOURCE == "config":
            cue_dict = CUE_DICT
            print(f"   cue_dict: using config.py")

        elif CUE_DICT_SOURCE == "first_csv":
            if not os.path.isdir(downsampled_folder):
                print(f"⚠️   No downsampled/ folder – skipping {session}.")
                continue
            cue_dict = get_session_cue_dict(downsampled_folder)
            if cue_dict is None:
                print(f"⚠️   Could not extract cue_dict from first CSV – skipping {session}.")
                continue

        elif CUE_DICT_SOURCE == "per_animal":
            if not os.path.isdir(downsampled_folder):
                print(f"⚠️   No downsampled/ folder – skipping {session}.")
                continue
            cue_dicts = extract_cue_dict(downsampled_folder)
            if not cue_dicts:
                print(f"⚠️   Could not extract per-animal cue dicts – skipping {session}.")
                continue
            # Use the first animal's dict as a fallback for any functions
            # that still require a single cue_dict argument
            cue_dict = next(iter(cue_dicts.values()))
            print(f"   cue_dict: per-animal mode ({len(cue_dicts)} animals detected)")

        else:
            print(f"⚠️   Unknown CUE_DICT_SOURCE '{CUE_DICT_SOURCE}' – skipping {session}.")
            continue

        # Time on platform
        if RUN_TIME_ON_PLATFORM:
            print("⏱   Time-on-platform...")
            try:
                run_time_on_platform_analysis(
                    data_folder,
                    save_path=os.path.join(out_base, "time_on_platform"),
                    cue_dict=cue_dict,
                    cue_dicts=cue_dicts,
                )
            except Exception as e:
                print(f"   ❌  {e}")

        # Nosepokes
        if RUN_NOSEPOKES:
            print("🐭  Nosepokes...")
            try:
                run_nosepoke_analysis(
                    data_folder,
                    save_path=os.path.join(out_base, "nosepokes"),
                    cue_dict=cue_dict,
                    cue_dicts=cue_dicts,
                )
            except Exception as e:
                print(f"   ❌  {e}")

        # Platform latency
        if RUN_PLATFORM_LATENCY:
            print("⏳  Platform latency...")
            try:
                run_latency_pma(
                    data_folder,
                    save_path=os.path.join(out_base, "latency"),
                    cue_dict=cue_dict,
                    cue_dicts=cue_dicts,
                )
            except Exception as e:
                print(f"   ❌  {e}")

        # Shock summary (VC only)
        if RUN_SHOCK_SUMMARY and session.startswith("VC"):
            print("⚡  Shock summary...")
            try:
                run_shock_summary(
                    data_folder,
                    save_path=os.path.join(out_base, "shocks"),
                )
            except Exception as e:
                print(f"   ❌  {e}")

        # Reward analyses (all sessions)
        if RUN_REWARDS:
            print("🍬  Reward analysis...")
            try:
                run_reward_analysis(
                    data_folder,
                    save_path=os.path.join(out_base, "rewards"),
                    cue_dict=cue_dict,
                    cue_dicts=cue_dicts,
                )
            except Exception as e:
                print(f"   ❌  {e}")


# ── Across-session analyses ────────────────────────────────────────────────────

def run_across_sessions(data_root, analysis_root):
    across_base = os.path.join(analysis_root, "across_sessions")

    if RUN_ACROSS_SESSION_LATENCY:
        print("\n📈  Across-session latency...")
        try:
            run_across_session_latency_summary(
                analysis_root,
                save_folder=os.path.join(across_base, "latency"),
            )
        except Exception as e:
            print(f"   ❌  {e}")

    if RUN_ACROSS_SESSION_SHOCKS:
        print("\n📈  Across-session shocks...")
        try:
            run_across_session_summary(
                analysis_root,
                save_folder=os.path.join(across_base, "shocks"),
            )
        except Exception as e:
            print(f"   ❌  {e}")

    if RUN_ACROSS_SESSION_AUC:
        print("\n📈  Across-session AUC...")
        try:
            metadata_df = load_metadata_from_root(data_root)
            run_across_session_auc(
                analysis_root,
                metadata_df=metadata_df,
                save_folder=os.path.join(across_base, "auc"),
            )
        except Exception as e:
            print(f"   ❌  {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    analysis_root = os.path.join(DATA_ROOT, OUTPUT_DIR_NAME)
    os.makedirs(analysis_root, exist_ok=True)

    sessions = _find_sessions(DATA_ROOT, SESSION_FILTER)
    print(f"Sessions to process: {sessions}")
    print(f"Output root: {analysis_root}")

    run_sessions(DATA_ROOT, analysis_root, sessions)
    run_across_sessions(DATA_ROOT, analysis_root)

    print("\n✅  All analyses complete.")
