# -*- coding: utf-8 -*-
"""
config.py  –  Single source of truth for all user-configurable settings.
Edit this file; do not touch the analysis modules.
"""

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_ROOT = (
    r"Z:\NIMH DIRP NSI\Projects\PFC Enkephalin Project\1PortPMA\Behaviors\MOR-FLOX\MOR-FLOX 2025-08\BehaviorData"
)

# Set to a folder name (e.g. "VC03") to process only that session; None = all sessions
SESSION_FILTER = None

# Top-level output folder created inside DATA_ROOT
OUTPUT_DIR_NAME = "Analysis"

# ── Analysis flags ─────────────────────────────────────────────────────────────
RUN_DOWNSAMPLE             = True
RUN_TIME_ON_PLATFORM       = True
RUN_NOSEPOKES              = True
RUN_PLATFORM_LATENCY       = True
RUN_SHOCK_SUMMARY          = True   # VC sessions only
RUN_REWARDS                = True   # total consumption + rewards in light trials + R-A index
RUN_ACROSS_SESSION_AUC     = True
RUN_ACROSS_SESSION_LATENCY = True
RUN_ACROSS_SESSION_SHOCKS  = True   # VC sessions only

# ── Treatment labels ───────────────────────────────────────────────────────────
CONTROL_TREATMENT = "ctrl"

TREATMENT_ALIASES = {
    "ctrl": "ctrl", "Ctrl": "ctrl", "CTRL": "ctrl", "TdTOM": "ctrl",
    "ko":   "KO",   "Cre":  "KO",
}

EXCLUDED_TREATMENTS = {"misc"}

# ── Plot colors ────────────────────────────────────────────────────────────────
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

# ── Task timing ────────────────────────────────────────────────────────────────
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

# ── Cue dictionary ─────────────────────────────────────────────────────────────
# Controls how trial onset times are determined.
#
# "config"     → use CUE_DICT defined below (same times for every animal and session)
# "first_csv"  → auto-detect from the first animal's downsampled CSV each session
# "per_animal" → auto-detect independently from each animal's own downsampled CSV
#
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

# ── Reward parameters ──────────────────────────────────────────────────────────
FEEDER_RATE_UL_PER_SEC = 16   # µL dispensed per second of Feeder active; adjust for your pump
