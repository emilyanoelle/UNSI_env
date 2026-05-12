# -*- coding: utf-8 -*-
"""
runner.py
---------
START HERE. This is the only file you need to edit to run the pipeline.

Set your directories, toggle which analyses you want, configure your treatment
groups and colors, then run this file in Spyder (press play).

"""

from pathlib import Path
from utils import build_treatment_normalizer
import run_report
import shutil, pathlib

# This is for clearing the pycache, which will cause a slight delay
for _p in pathlib.Path(__file__).parent.rglob("__pycache__"):
    shutil.rmtree(_p, ignore_errors=True)

# ── CPU core usage ─────────────────────────────────────────────────────────
# How many worker processes to use in parallel.
# Rule of thumb: set this to the number of physical CPU cores you have minus
# one, so your machine stays responsive while the pipeline runs.
# You can also set it to None to let Python choose automatically.
# ---------------------------------------------------------------------------
N_WORKERS = 5

# ── Data directories ─────────────────────────────────────────────────────────
#
# List every BehaviorData folder you want to include. Each folder must contain
# its own animals_metadata.xlsx with a cohort_id column. If cohort_id is
# absent, the folder name is used as the cohort label automatically.
#
# Use raw strings (the r prefix) to avoid issues with backslashes on Windows.

BEHAVIORDATA_DIRS = [
    r"Z:\NIMH DIRP NSI\Projects\PFC Ketamine\Behavior\Fear Conditioning\Early Life Stress Cohort 1\BehaviorData"
]

# Top-level folder where combined across-cohort outputs are written.
# Per-session outputs are always written inside their own BehaviorData folder.
# Cohort-specific across-session outputs go to:
#   <BehaviorData>/<cohort_id>/Analysis/<subfolder>/
ANALYSIS_OUTPUT_DIR = r"Z:\NIMH DIRP NSI\Projects\PFC Ketamine\Behavior\Fear Conditioning\Analysis5"


# ── Treatment group configuration ────────────────────────────────────────────
#
# Define your canonical group names and every alias that might appear in your
# metadata spreadsheet. Matching is case-insensitive.
# The FIRST key is treated as the control group for figure ordering.

TREATMENT_ALIASES = {
    "ctrl": ["Control", "ctrl", ],
    "ELS":     ["ELS", "LBN"],
}

# Hex color for each canonical treatment label (keys must match above exactly).
# Female colors and unknown-sex colors are derived automatically as lighter
# tints — you do not need to specify them separately.
TREATMENT_COLORS = {
    "ctrl": "#96A6AC",
    "ELS":     "#E26E2B",
}


# ── Analysis toggles ─────────────────────────────────────────────────────────
#
# Set to True to run, False to skip.

RUN_SANITY_CHECK    = False     # tracking coverage + IQR outliers + trial window consistency
RUN_FREEZING        = False     # % time freezing + freezing bouts (if enabled below)
RUN_PLATFORM        = False    # % time on platform + latency to platform (if enabled below)
RUN_EEE             = False    # Evade / Escape / Endure shock outcome classification
RUN_US_LOCKED       = False    # % platform time locked to the shock delivery window
RUN_EVENT_RASTER    = False    # per-session event/behavior raster SVGs (pass 1 only)
RUN_SPEED           = True     # CS-locked speed analysis


# ── CS+ / CS- trial detection ────────────────────────────────────────────────
#
# Controls how CS+ and CS- trial boundaries are detected across ALL analyses
# (freezing, platform, EEE, US-locked). The same setting applies uniformly
# because column availability is a property of the AnyMaze export, not of
# the individual analysis.
#
# "ttl"         — use CS+/CS- ON/OFF activated columns (brief TTL pulses).
# "tone_status" — use continuous tone-status columns (value = 1 while tone
#                 is playing, 0 otherwise).
# "auto"        — try tone_status first; fall back to TTL if not found.
#                 Recommended when you are unsure which columns are present,
#                 or when your dataset mixes export configurations.

CS_DETECTION_MODE = "tone_status"

# Column name patterns for tone-status detection (used when mode is
# "tone_status" or "auto"). Write them in any natural form — they are
# normalised before matching, so "CS+ tone status", "csplus_tone_status",
# and "CS plus tone status" all resolve identically. Flexible substring
# matching is used, so the string just needs to appear within the column name.
# Change these only if your AnyMaze export uses different column names.
TONE_STATUS_COL_CSPLUS  = "cs plus tone status"
TONE_STATUS_COL_CSMINUS = "cs minus tone status"


# Column matching configuration
#
# Column aliases let you list the real column-name variations that appear in
# your AnyMaze exports. The pipeline normalizes both the CSV headers and these
# aliases before comparing them, so "In platform" and "in_platform" are treated
# the same. If more than one column matches a role, the pipeline raises an
# "Ambiguous ..." error instead of guessing.
#
# "strict"   - only use COLUMN_ALIASES. Missing required columns raise errors.
# "fallback" - try COLUMN_ALIASES first; if no alias matches, use the older
#              built-in heuristics such as finding columns containing "freez".

COLUMN_MATCH_MODE = "strict"

COLUMN_ALIASES = {
    "time": [
        "Time (s)",
        "Time",
    ],
    "freezing": [
        "Freezing",
        "Freezing state",
    ],
    "in_platform": [
        "In platform",
        "Inside platform",
    ],
    "latency_to_platform": [
        "latency_to_platform_s",
        "Latency to platform",
        "Latency to platform (s)",
    ],
    "csplus_tone_status": [
        "CS+ tone status",
        "CS plus tone status",
        TONE_STATUS_COL_CSPLUS,
    ],
    "csminus_tone_status": [
        "CS- tone status",
        "CS minus tone status",
        TONE_STATUS_COL_CSMINUS,
    ],
    "csplus_on": [
        "CS+ ON activated",
        "CS plus ON activated",
    ],
    "csplus_off": [
        "CS+ OFF activated",
        "CS plus OFF activated",
    ],
    "csminus_on": [
        "CS- ON activated",
        "CS minus ON activated",
    ],
    "csminus_off": [
        "CS- OFF activated",
        "CS minus OFF activated",
    ],
    "us_on": [
        "US ON activated",
        "Shock ON activated",
    ],
    "us_off": [
        "US OFF activated",
        "Shock OFF activated",
    ],
    "shocker_active": [
        "Shocker active",
    ],
    "speed": [
        "Speed (m/s)",
        "Speed"
    ]
}


# ── Trial caps ───────────────────────────────────────────────────────────────
#
# Maximum number of trials per animal per day included in figures and Prism
# tables. Trials beyond these limits are excluded from plots but are kept in
# the raw concatenated CSVs.

CS_TRIAL_CAP  = 10   # applies to CS+ and CS- trials
ITI_TRIAL_CAP = 20   # applies to ITI windows
EEE_TRIAL_CAP = 10   # CS+ trials used for evade/escape/endure classification


# ── File naming conventions ──────────────────────────────────────────────────
#
# Only files whose stem ends with CSV_SUFFIX are processed.
# Set to "" to process all .csv files found in a session folder.

CSV_SUFFIX = ""   # e.g. "_fixed" processes only "03-05-26_9C-1_fixed.csv"

# Subfolder names written inside each day folder (per-session outputs).
# Change these only if your existing folder structure uses different names.
FREEZING_SUBFOLDER  = "% time freezing"
PLATFORM_SUBFOLDER  = "% time on platform"
LATENCY_SUBFOLDER   = "latency to platform"
EEE_SUBFOLDER       = "Shock outcomes (evade-escape-endure)"
EVENT_RASTER_SUBFOLDER = "event rasters"


# ── Freezing sub-analyses ────────────────────────────────────────────────────

FREEZING_BOUTS      = False   # also compute and plot freezing bout counts
FREEZING_BY_SEX     = False  # generate sex × treatment breakdown figures
FREEZING_BY_LITTER  = False   # generate litter-level figures
                              # (requires litter_id column in metadata)


# ── Platform sub-analyses ────────────────────────────────────────────────────

PLATFORM_LATENCY    = True   # also compute latency to first platform entry
PLATFORM_BY_SEX     = True  # generate sex × treatment breakdown figures


# ── EEE sub-analyses ─────────────────────────────────────────────────────────

EEE_BY_SEX          = False  # generate sex × treatment stacked bar figures


# ── US-locked settings ───────────────────────────────────────────────────────
#
# Mode A (USE_SHOCKER_COLUMN = True):
#   Detects US windows from the 'Shocker active' column in AnyMaze CSVs,
#   clipped to within detected CS+ trial boundaries to exclude stray pulses.
#
# Mode B (USE_SHOCKER_COLUMN = False):
#   Derives the US window as the last US_DURATION_S seconds of each CS+ trial,
#   using the same trial detection logic as platform_analysis.py. Works for
#   all sessions including yoked controls.

USE_SHOCKER_COLUMN     = False
US_DURATION_S          = 2.0    # Mode B only: assumed shock window length (seconds)

US_CHANCE_BASELINE_PCT = 16.4   # Subtracted from platform_pct to give "above chance".
                                 # Default = 16.4%, the chance level for this paradigm,
                                 # based on the % space the platform occupies in the arena.

# Restrict which treatment groups are processed in the US-locked analysis.
# Only treatments defined in TREATMENT_ALIASES above are valid.
# Set to None to include all canonical groups, or provide a list to restrict,
# e.g. ["ELS"] runs only the ELS group.
INCLUDE_TREATMENTS     = None

# Explicitly skip specific animals by behavior_id, e.g. ["9C-1", "4B"].
EXCLUDE_BEHAVIOR_IDS   = []

# Controls the row order in US-locked heatmaps.
# "response"     — sort animals by mean above-chance value (highest on top).
#                  Useful for visualising the distribution of responders.
# "alphabetical" — sort by animal_id alphanumerically. Keeps each animal in
#                  the same row position across sessions for direct comparison.
HEATMAP_SORT           = "alphabetical"

# ── Speed Analysis settings ───────────────────────────────────────────────────────────

SPEED_SUBFOLDER  = "speed"   # subfolder name used inside Analysis/

# Window around each CS onset, in seconds.
# Pre = how many seconds before onset to include (default 30 s = 300 bins).
# Post = how many seconds after onset to include (default 60 s = 600 bins).
SPEED_PRE_S  = 30.0
SPEED_POST_S = 60.0

# Speed analysis always writes one combined parquet table for downstream
# analysis. Set this to True only when you also want Excel workbooks for
# manual visual checks.
SPEED_WRITE_EXCEL = False

# ── Prism export ─────────────────────────────────────────────────────────────
#
# When True, writes wide-format Prism-ready Excel tables for every enabled
# analysis. Rows = trial index, columns = individual animal IDs.

PRISM_EXPORT        = True

# ── DO NOT EDIT BELOW THIS LINE ──────────────────────────────────────────────

def main():
    behaviordata_dirs = [Path(p) for p in BEHAVIORDATA_DIRS]
    analysis_out      = Path(ANALYSIS_OUTPUT_DIR)

    for bd in behaviordata_dirs:
        if not bd.exists():
            raise SystemExit(f"[error] BehaviorData directory not found:\n  {bd}")

    analysis_out.mkdir(parents=True, exist_ok=True)

    for bd in behaviordata_dirs:
        meta_path = bd / "animals_metadata.xlsx"
        if not meta_path.exists():
            print(f"[warn] animals_metadata.xlsx not found at {meta_path}.\n"
                  "       Sex and litter sub-analyses will be skipped for this folder.")

    treatment_lookup = build_treatment_normalizer(TREATMENT_ALIASES)
    control_label    = list(TREATMENT_ALIASES.keys())[0]

    cfg = dict(
        n_workers               = N_WORKERS,
        behaviordata_dirs       = behaviordata_dirs,
        analysis_out            = analysis_out,
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
        event_raster_subfolder  = EVENT_RASTER_SUBFOLDER,
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
        heatmap_sort            = HEATMAP_SORT,
        column_match_mode       = COLUMN_MATCH_MODE,
        column_aliases          = COLUMN_ALIASES,
        cs_detection_mode       = CS_DETECTION_MODE,
        tone_status_col_csplus  = TONE_STATUS_COL_CSPLUS,
        tone_status_col_csminus = TONE_STATUS_COL_CSMINUS,
        speed_subfolder         = SPEED_SUBFOLDER,
        speed_pre_bins          = int(round(SPEED_PRE_S  * 10)),  # seconds → 100ms bins
        speed_post_bins         = int(round(SPEED_POST_S * 10)),
        speed_write_excel       = SPEED_WRITE_EXCEL,
        
        # run toggles (used by run_report)
        run_sanity_check        = RUN_SANITY_CHECK,
        run_freezing            = RUN_FREEZING,
        run_platform            = RUN_PLATFORM,
        run_eee                 = RUN_EEE,
        run_us_locked           = RUN_US_LOCKED,
        run_event_raster        = RUN_EVENT_RASTER,
        run_speed               = RUN_SPEED,
    )

    report = run_report.new_report(cfg)

    if RUN_SANITY_CHECK:
        print("\n" + "="*60)
        print("SANITY CHECKS")
        print("="*60)
        from sanity_check import run as run_sanity
        run_sanity(cfg)

    if RUN_FREEZING:
        print("\n" + "="*60)
        print("FREEZING ANALYSIS")
        print("="*60)
        from freezing_analysis import run as run_freezing
        run_freezing(cfg, report=report)

    if RUN_PLATFORM:
        print("\n" + "="*60)
        print("PLATFORM ANALYSIS")
        print("="*60)
        from platform_analysis import run as run_platform
        run_platform(cfg, report=report)

    if RUN_EEE:
        print("\n" + "="*60)
        print("EVADE / ESCAPE / ENDURE ANALYSIS")
        print("="*60)
        from eee_analysis import run as run_eee
        run_eee(cfg, report=report)

    if RUN_US_LOCKED:
        print("\n" + "="*60)
        print("US-LOCKED PLATFORM ANALYSIS")
        print("="*60)
        from us_locked_analysis import run as run_us_locked
        run_us_locked(cfg, report=report)

    if RUN_EVENT_RASTER:
        print("\n" + "="*60)
        print("EVENT AND BEHAVIORAL RASTERS")
        print("="*60)
        from event_raster_analysis import run as run_event_raster
        run_event_raster(cfg, report=report)

    if RUN_SPEED:
        print("\n" + "="*60)
        print("SPEED ANALYSIS")
        print("="*60)
        from speed_analysis import run as run_speed
        run_speed(cfg, report=report)

    run_report.write_excel_report(report, analysis_out)

    print("\n" + "="*60)
    print("Pipeline complete.")
    print("="*60)


if __name__ == "__main__":
    main()
