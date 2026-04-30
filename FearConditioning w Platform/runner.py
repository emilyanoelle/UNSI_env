# -*- coding: utf-8 -*-
"""
runner.py
---------
START HERE. This is the only file you need to edit to run the pipeline.

Set your directory, toggle which analyses you want, configure your treatment
groups and colors, then run this file in Spyder (press play).
"""

from pathlib import Path
from utils import build_treatment_normalizer

# =============================================================================
# REQUIRED: Data directory
# =============================================================================

BEHAVIORDATA_DIR = r"Z:\NIMH DIRP NSI\Projects\PFC Ketamine\Behavior\Fear Conditioning\Early Life Stress Cohort 1\BehaviorData"

# =============================================================================
# TREATMENT GROUP CONFIGURATION
# =============================================================================
# Define your canonical group names and all aliases that might appear in your
# data files or metadata spreadsheet. Matching is case-insensitive.
#
# The FIRST key is treated as the control group for ordering/color purposes.

TREATMENT_ALIASES = {
    "control": ["ctrl", "cntrl", "control", "Control", "CTRL"],
    "ELS":     ["ELS", "els", "LBN", "lbn"],
}

# Colors for each canonical treatment label (must match keys above exactly).
TREATMENT_COLORS = {
    "control": "#0F52BA",
    "ELS":     "#EC5800",
}

# =============================================================================
# ANALYSIS TOGGLES
# =============================================================================
# Set to True to run, False to skip.

RUN_FREEZING        = True   # % time freezing + freezing bouts (if enabled below)
RUN_PLATFORM        = True   # % time on platform + latency to platform (if enabled below)
RUN_EEE             = True   # Evade / Escape / Endure shock outcomes
RUN_US_LOCKED       = True   # % platform time locked to shock delivery window

# =============================================================================
# CS+ / CS- TRIAL DETECTION
# =============================================================================
# Controls how CS+ and CS- trial boundaries are detected across ALL analyses
# (freezing, platform, EEE, US-locked). The column availability is a property
# of the AnyMaze export configuration and is the same for all analyses on the
# same dataset.
#
# "ttl"         — use CS+/CS- ON/OFF activated columns (brief TTL pulses).
# "tone_status" — use continuous tone-status columns (1 while tone is on).
# "auto"        — try tone_status first; fall back to TTL if not found.
#                 Recommended if you are unsure which columns are present.
CS_DETECTION_MODE = "tone_status"

# Column name patterns for tone-status detection (used when mode is
# "tone_status" or "auto"). Write them in any natural form — they are
# normalised before matching, so "CS+ tone status", "csplus_tone_status",
# and "CS plus tone status" all resolve identically. Partial/flexible matching
# is used, so the string just needs to appear within the column name.
# Change these if your AnyMaze export uses different column names.
TONE_STATUS_COL_CSPLUS  = "cs plus tone status"
TONE_STATUS_COL_CSMINUS = "cs minus tone status"

# =============================================================================
# TRIAL CAPS
# =============================================================================
# Maximum number of trials per animal per day used in figures.
# Trials beyond these limits are excluded from plots (but kept in raw CSVs).

CS_TRIAL_CAP  = 10
ITI_TRIAL_CAP = 20
EEE_TRIAL_CAP = 10   # CS+ trials used for evade/escape/endure classification

# =============================================================================
# FILE NAMING CONVENTIONS
# =============================================================================
# Only files whose stem ends with this string are processed.
# Set to "" to process all .csv files in a session folder.

CSV_SUFFIX = ""   # e.g. "_fixed" only processes "03-05-26_9C-1_fixed.csv"

# Subfolder names inside each day folder where raw CSVs live.
# Change these only if your folder structure differs.
FREEZING_SUBFOLDER  = "% time freezing"
PLATFORM_SUBFOLDER  = "% time on platform"
LATENCY_SUBFOLDER   = "latency to platform"
EEE_SUBFOLDER       = "Shock outcomes (evade-escape-endure)"

# =============================================================================
# SUB-ANALYSIS TOGGLES
# =============================================================================

# --- Freezing sub-analyses ---
FREEZING_BOUTS      = True   # compute and plot freezing bout counts
FREEZING_BY_SEX     = False   # generate sex × treatment breakdown figures
FREEZING_BY_LITTER  = True  # generate litter-level figures (requires litter_id in metadata)

# --- Platform sub-analyses ---
PLATFORM_LATENCY    = True   # compute latency to platform alongside % time on platform
PLATFORM_BY_SEX     = False   # generate sex × treatment breakdown figures

# --- EEE sub-analyses ---
EEE_BY_SEX          = False   # generate sex × treatment stacked bar figures

# --- US-locked sub-settings ---

# Mode A (True): detect US window from 'Shocker active' column in AnyMaze CSV,
#   clipped to within CS+ trial boundaries to exclude stray electronic pulses.
# Mode B (False): derive US window as last US_DURATION_S seconds of each CS+
#   trial, using the same detection logic as platform_analysis.py. Works for
#   all sessions including yoked.
USE_SHOCKER_COLUMN     = False

US_DURATION_S          = 2.0    # Mode B only: shock window length in seconds
US_CHANCE_BASELINE_PCT = 16.4   # subtracted from platform_pct for "above chance"
                                 # (default = 16.4%, chance level for this paradigm)

# Only treatments defined in TREATMENT_ALIASES above are valid.
# Any subject whose treatment label does not match a canonical group is
# automatically excluded. Set to None to include all canonical groups, or
# provide a list to restrict (e.g. ["ELS"] runs only the ELS group).
INCLUDE_TREATMENTS     = None

EXCLUDE_BEHAVIOR_IDS   = []     # explicitly skip specific animals, e.g. ["9C-1"]

# Separate plots and outputs per cohort (requires cohort_id in metadata).
# When True, outputs are written per-treatment AND per-treatment x cohort.
SEPARATE_BY_COHORT     = True

# "response"     — sort animals by mean above-chance value (highest on top).
#                  Useful for visualising the distribution of responders.
# "alphabetical" — sort by animal_id alphanumerically. Keeps each animal in
#                  the same row across sessions for direct visual comparison.
HEATMAP_SORT           = "response"

# --- Prism export ---
PRISM_EXPORT        = True   # export Prism-ready Excel tables for all enabled analyses

# =============================================================================
# DO NOT EDIT BELOW THIS LINE
# =============================================================================

def main():
    behaviordata = Path(BEHAVIORDATA_DIR)
    if not behaviordata.exists():
        raise SystemExit(f"[error] BehaviorData directory not found:\n  {behaviordata}")

    meta_path = behaviordata / "animals_metadata.xlsx"
    if not meta_path.exists():
        print(f"[warn] animals_metadata.xlsx not found at {meta_path}.\n"
              "       Sex and litter sub-analyses will be skipped.")

    # Build the flat alias lookup once; pass it to every analysis module.
    treatment_lookup = build_treatment_normalizer(TREATMENT_ALIASES)
    control_label    = list(TREATMENT_ALIASES.keys())[0]

    # Bundle all config into a single dict so analysis scripts have one argument.
    cfg = dict(
        behaviordata            = behaviordata,
        treatment_lookup        = treatment_lookup,
        treatment_colors        = TREATMENT_COLORS,
        control_label           = control_label,
        canonical_groups        = list(TREATMENT_ALIASES.keys()),
        cs_trial_cap            = CS_TRIAL_CAP,
        iti_trial_cap           = ITI_TRIAL_CAP,
        eee_trial_cap           = EEE_TRIAL_CAP,
        csv_suffix              = CSV_SUFFIX,
        freezing_subfolder      = FREEZING_SUBFOLDER,
        platform_subfolder      = PLATFORM_SUBFOLDER,
        latency_subfolder       = LATENCY_SUBFOLDER,
        eee_subfolder           = EEE_SUBFOLDER,
        freezing_bouts          = FREEZING_BOUTS,
        freezing_by_sex         = FREEZING_BY_SEX,
        freezing_by_litter      = FREEZING_BY_LITTER,
        platform_latency        = PLATFORM_LATENCY,
        platform_by_sex         = PLATFORM_BY_SEX,
        eee_by_sex              = EEE_BY_SEX,
        prism_export            = PRISM_EXPORT,
        use_shocker_column      = USE_SHOCKER_COLUMN,
        us_duration_s           = US_DURATION_S,
        us_chance_baseline_pct  = US_CHANCE_BASELINE_PCT,
        include_treatments      = INCLUDE_TREATMENTS,
        exclude_behavior_ids    = EXCLUDE_BEHAVIOR_IDS,
        separate_by_cohort      = SEPARATE_BY_COHORT,
        heatmap_sort            = HEATMAP_SORT,
        cs_detection_mode       = CS_DETECTION_MODE,
        tone_status_col_csplus  = TONE_STATUS_COL_CSPLUS,
        tone_status_col_csminus = TONE_STATUS_COL_CSMINUS,
    )

    if RUN_FREEZING:
        print("\n" + "="*60)
        print("FREEZING ANALYSIS")
        print("="*60)
        from freezing_analysis import run as run_freezing
        run_freezing(cfg)

    if RUN_PLATFORM:
        print("\n" + "="*60)
        print("PLATFORM ANALYSIS")
        print("="*60)
        from platform_analysis import run as run_platform
        run_platform(cfg)

    if RUN_EEE:
        print("\n" + "="*60)
        print("EVADE / ESCAPE / ENDURE ANALYSIS")
        print("="*60)
        from eee_analysis import run as run_eee
        run_eee(cfg)

    if RUN_US_LOCKED:
        print("\n" + "="*60)
        print("US-LOCKED PLATFORM ANALYSIS")
        print("="*60)
        from us_locked_analysis import run as run_us_locked
        run_us_locked(cfg)

    print("\n" + "="*60)
    print("Pipeline complete.")
    print("="*60)


if __name__ == "__main__":
    main()
