# Fear Conditioning Behavioral Analysis Pipeline

## Overview

This pipeline processes behavioral data from fear conditioning experiments recorded in AnyMaze. It computes % time freezing, freezing bout counts, % time on platform, latency to platform, and shock outcome classification (Evade / Escape / Endure) across multiple session days, then produces tiled figures and Prism-ready tables.

**You only need to edit one file: `runner.py`.** Everything else runs automatically from there.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [File Overview](#2-file-overview)
3. [Required Data Structure](#3-required-data-structure)
4. [Metadata Spreadsheet](#4-metadata-spreadsheet)
5. [Runner Configuration — Full Reference](#5-runner-configuration--full-reference)
6. [How Files and Animals Are Matched](#6-how-files-and-animals-are-matched)
7. [How Trials Are Detected](#7-how-trials-are-detected)
8. [The Math](#8-the-math)
9. [Output Files and Folder Structure](#9-output-files-and-folder-structure)
10. [Troubleshooting](#10-troubleshooting)
11. [US-Locked Platform Analysis](#11-us-locked-platform-analysis)

---

## Acknowledgements

The procedures of the us_locked analysis were taken from Eastman Lewis and Kenya Barnes, modified only to ensure ease of integration into this pipeline.

## 1. Quick Start

1. Place all five pipeline files in the same folder on your computer:
   ```
   runner.py
   freezing_analysis.py
   platform_analysis.py
   eee_analysis.py
   utils.py
   us_locked_analysis.py
   ```

2. Open `runner.py` in Spyder.

3. Set `BEHAVIORDATA_DIR` to the full path of your BehaviorData folder.

4. Set your treatment group names and colors (see [Section 5](#5-runner-configuration--full-reference)).

5. Toggle which analyses you want using the `True`/`False` switches.

6. Press play (▶). Outputs appear inside your BehaviorData folder.

---

## 2. File Overview

| File | What it does |
|---|---|
| `runner.py` | **Start here.** All user settings live here. Calls the analysis scripts. |
| `utils.py` | Shared functions used by all analysis scripts. Do not edit unless you know what you are changing. |
| `freezing_analysis.py` | Computes % time freezing and freezing bout counts across all days. |
| `platform_analysis.py` | Computes % time on platform and latency to platform across all days. |
| `eee_analysis.py` | Classifies CS+ trials as Evade, Escape, or Endure across all days. |
| `us_locked_analysis.py` | Computes % platform time locked to the shock (US) delivery window specifically, rather than the full CS+ trial. |

---

## 3. Required Data Structure

### 3.1 Top-level layout

Your BehaviorData folder must look like this:

```
BehaviorData/
├── animals_metadata.xlsx          ← required (see Section 4)
├── d01_contextA_habituation/      ← session day folder
│   ├── 03-05-26_9C-1_1_fixed.csv
│   ├── 03-05-26_9C-2_1_fixed.csv
│   └── ...
├── d02_contextB_conditioning/
│   ├── 03-06-26_9C-1_1_fixed.csv
│   └── ...
└── d03_contextA_recall/
    └── ...
```

### 3.2 Session folder naming

Session folders **must** begin with `d##_` where `##` is a two-digit day number. The pipeline uses this to sort days chronologically. The full expected format is:

```
d{day}_{context}_{session_label}
```

Examples of valid names:
```
d01_contextA_habituation
d02_contextB_conditioning
d03_contextA_recall
d10_contextB_extinction
```

- `d01`, `d02`, etc. — day number, always two digits with a leading zero
- `contextA` or `contextB` — case-insensitive; use the letter that matches your experimental design
- The session label (e.g. `habituation`) can be any text without spaces

**Older folder formats** (`Day 1 habituation_2`) are also supported for backward compatibility, but the `d##_context#_label` format is preferred.

### 3.3 Raw CSV file naming

Each animal's AnyMaze export file must follow this pattern:

```
MM-DD-YY_BehaviorID_index_fixed.csv
```

Examples:
```
03-05-26_9C-1_1_fixed.csv
03-05-26_10a_2_fixed.csv
03-05-26_4B_1_fixed.csv
```

- `MM-DD-YY` — date of the recording session
- `BehaviorID` — the animal's behavior ID, which must match (or be matchable to) an entry in `animals_metadata.xlsx`
- `_index` and `_fixed` suffixes are handled automatically and stripped during parsing
- By default, only files ending in `_fixed.csv` are processed. You can change this in `runner.py` by editing `CSV_SUFFIX`

### 3.4 Required columns in AnyMaze CSVs

Column names are normalized automatically (spaces collapsed, case lowered, `+` → `plus`, `-` → `minus`, parentheses removed), so minor formatting differences are tolerated.

**Always required:**
- A time column — any column whose name starts with `time` (e.g. `Time (s)`)

**For freezing analysis:**
- A freezing state column — any column whose name contains `freez` (e.g. `Freezing`)
- TTL columns for CS+ and CS- onset/offset — the pipeline looks for columns matching patterns like `CS+ ON activated`, `CS+ OFF activated`, `CS- ON activated`, `CS- OFF activated`. Spacing and case vary; the matching is flexible (see [Section 6](#6-how-files-and-animals-are-matched))

**For platform analysis:**
- `In platform` — binary column (1 = on platform, 0 = off). Normalized to `in_platform`
- Optionally `Not in platform` for a sanity check
- CS+/CS- detection uses either tone-status columns (`CS+ tone status`, `CS- tone status`) if present, or falls back to TTL ON/OFF columns

**For EEE analysis:**
- `In platform` — same as above
- CS+ TTL columns — same as freezing
- US (shock) TTL columns — columns containing `us on`, `shock on`, `footshock on`, and their OFF equivalents

---

## 4. Metadata Spreadsheet

`animals_metadata.xlsx` must live directly inside your BehaviorData folder. It links behavior IDs (from filenames) to animal-level information.

### Required columns

| Column | Description |
|---|---|
| `behavior_id` | The ID used in CSV filenames (e.g. `9C-1`, `10a`) |
| `animal_id` | Your internal animal identifier |
| `treatment_group` | Group label (e.g. `ctrl`, `ELS`) — aliases handled by runner.py |
| `sex` | `M`, `F`, or leave blank (normalized automatically) |

### Optional columns

| Column | Description |
|---|---|
| `litter_id` | Required only if `FREEZING_BY_LITTER = True` |
| `cohort_id` | Carried through to outputs but not used in analysis |

### Column name formatting

Column names in the spreadsheet are normalized the same way as CSV columns, so `Behavior ID`, `behavior_id`, and `BehaviorID` will all be recognized. However, the closest match to the exact names above is safest.

---

## 5. Runner Configuration — Full Reference

Open `runner.py`. Everything between the top and the `DO NOT EDIT BELOW THIS LINE` marker is yours to change.

### 5.1 Data directory

```python
BEHAVIORDATA_DIR = r"Z:\path\to\your\BehaviorData"
```

Use a raw string (the `r` prefix) to avoid issues with backslashes on Windows.

### 5.2 Treatment group configuration

```python
TREATMENT_ALIASES = {
    "control": ["ctrl", "cntrl", "control", "Control"],
    "ELS":     ["ELS", "els", "LBN", "lbn"],
}
```

- The **keys** (`"control"`, `"ELS"`) are the canonical labels that appear in all outputs and figures
- The **values** are every spelling or capitalization of that label that might appear in your metadata spreadsheet or CSV files
- Matching is case-insensitive
- The **first key** is treated as the control group (used for ordering in figures)
- Add more groups by adding more key-value pairs if you have three or more conditions

```python
TREATMENT_COLORS = {
    "control": "#0F52BA",   # hex color codes
    "ELS":     "#EC5800",
}
```

Colors must use the same keys as `TREATMENT_ALIASES`. Female colors and unknown-sex colors are derived automatically as lighter tints of these base colors — you do not need to specify them separately.

### 5.3 Analysis toggles

```python
RUN_FREEZING  = True
RUN_PLATFORM  = True
RUN_EEE       = True
RUN_US_LOCKED = True
```

Set any of these to `False` to skip that analysis entirely.

### 5.4 CS+ / CS- trial detection

All four analysis scripts use the same underlying trial detection logic, controlled by a single setting:

```python
CS_DETECTION_MODE = "auto"
```

The three modes are:

**`"ttl"`** — detect trial boundaries from CS+/CS- ON/OFF TTL pulse columns. These are brief event markers that fire when the tone starts and stops. The pipeline looks for columns containing the words `cs`, `plus` (or `minus`), and `on`/`off`.

**`"tone_status"`** — detect trial boundaries from continuous tone-status columns, which hold a value of 1 for the entire duration of the tone and 0 otherwise. These are more reliable when TTL pulses are brief or inconsistent across boxes.

**`"auto"`** — try tone-status first; fall back to TTL if the tone-status columns are not found. This is the default and is recommended when you are unsure which columns your AnyMaze export contains.

If you use `"tone_status"` or `"auto"`, you can configure the exact column name patterns to search for. The patterns are matched flexibly — any natural-language form resolves correctly:

```python
TONE_STATUS_COL_CSPLUS  = "cs plus tone status"
TONE_STATUS_COL_CSMINUS = "cs minus tone status"
```

For example, `"CS+ tone status"`, `"csplus_tone_status"`, and `"CS plus tone status"` all match the default pattern. If your AnyMaze export uses a different column name (e.g. `"Tone CS+ active"`), change the string here and the pipeline will find it automatically.

### 5.5 Sub-analysis toggles

These only apply when their parent analysis is enabled.

```python
# Freezing
FREEZING_BOUTS      = True    # also compute freezing bout counts
FREEZING_BY_SEX     = True    # generate sex × treatment figures
FREEZING_BY_LITTER  = False   # generate litter-level figures (needs litter_id in metadata)

# Platform
PLATFORM_LATENCY    = True    # also compute latency to platform
PLATFORM_BY_SEX     = True    # generate sex × treatment figures

# EEE
EEE_BY_SEX          = True    # generate sex × treatment stacked bars

# Prism
PRISM_EXPORT        = True    # export Prism-ready Excel tables for all enabled analyses
```

### 5.6 Trial caps

```python
CS_TRIAL_CAP  = 10   # max CS+ or CS- trials per animal per day used in figures
ITI_TRIAL_CAP = 20   # max ITI windows per animal per day used in figures
EEE_TRIAL_CAP = 10   # max CS+ trials used for EEE classification
```

Trials beyond these numbers are excluded from figures and Prism tables. They are still present in the raw concatenated CSVs.

### 5.7 File naming conventions

```python
CSV_SUFFIX = "_fixed"
```

Only files whose stem ends with this string are processed. Set to `""` to process all `.csv` files in a session folder.

```python
FREEZING_SUBFOLDER  = "% time freezing"
PLATFORM_SUBFOLDER  = "% time on platform"
LATENCY_SUBFOLDER   = "latency to platform"
EEE_SUBFOLDER       = "Shock outcomes (evade-escape-endure)"
```

These are the subfolder names the pipeline expects to find inside each day folder when looking for pre-processed per-animal CSVs (for platform and freezing). Change these only if your folder structure uses different names.

> **Note for EEE:** The EEE script reads raw AnyMaze CSVs directly from the top level of each day folder, not from a subfolder. This is because EEE classification requires the raw time-series signal, not a summarized output.

---

## 6. How Files and Animals Are Matched

This section explains the exact logic used to go from a filename to a row in your metadata spreadsheet. Understanding this helps diagnose any `behavior_id not found` warnings.

### 6.1 Extracting the behavior ID from a filename

Given a filename like `03-05-26_9C-1_1_fixed.csv`, the pipeline:

1. Finds the date portion: `03-05-26`
2. Takes everything after the date and the separator: `9C-1_1_fixed`
3. Strips trailing `_fixed`, `_1_fixed`, `_2_fixed` etc.: `9C-1_1` → `9C-1`
4. Takes only the leading token before any underscore: `9C-1`

So `03-05-26_9C-1_1_fixed.csv` → behavior ID token `9C-1`.

Filenames with spaces after the date separator (e.g. `03-05-26_ 3a_fixed.csv`) are handled — leading spaces are stripped.

### 6.2 Matching to metadata

The extracted token is then looked up in `animals_metadata.xlsx` using a six-step fallback chain. Each step is tried in order; the first match wins.

| Step | Strategy | Example |
|---|---|---|
| 1 | Exact match (case-insensitive, stripped) | `9c-1` matches `9C-1` |
| 2 | Alphanumeric-only match (remove all non-alphanumeric characters) | `9c1` matches `9C-1` |
| 3 | Strip trailing `-N` suffix and match the base | `9c` matches metadata entry `9C` |
| 4 | Numeric suffix heuristics — match by trailing number, with or without letter | `1` matches `animal-1`; `4a` matches `4A` |
| 5 | Token boundary match — look for the ID surrounded by `-` or `_` | `3a` matches `group_3a_cohort2` |
| 6 | Prefix or suffix match on alphanumeric-only strings | `3a` matches `box3a` |

If none of these six steps find a match, the file is skipped and a warning is printed. The most common cause is a behavior ID in the filename that differs meaningfully from what is in the spreadsheet (e.g. `9C1` in the file but `9C-1` in the spreadsheet — this is handled by step 2; but `animal9` vs `9C-1` would not match).

### 6.3 Treatment label normalization

After the metadata row is found, the `treatment_group` value from the spreadsheet is looked up in your `TREATMENT_ALIASES` dictionary (defined in `runner.py`). This lookup is case-insensitive. If no alias matches, the raw value from the spreadsheet is used as-is. This means you do not need to rename anything in your data files — you just need to list all the variants you use as aliases in `runner.py`.

---

## 7. How Trials Are Detected

All four analysis scripts (freezing, platform, EEE, and US-locked) use a single shared trial detection function in `utils.py`. The mode is set once in `runner.py` via `CS_DETECTION_MODE` and applies uniformly across all analyses. This ensures that trial boundaries are defined consistently regardless of which metric is being computed.

### 7.1 TTL-based detection (`CS_DETECTION_MODE = "ttl"`)

Trials are detected from TTL event columns in the AnyMaze CSV. The pipeline looks for columns matching patterns like:

- CS+ ON: column contains `cs`, `plus`, `on`, `activat` (in that order)
- CS+ OFF: column contains `cs`, `plus`, `off`, `activat`
- CS- ON/OFF: same pattern with `minus`

Column matching uses substring search with flexible ordering — it does not require an exact column name.

**Rising edge detection:** A TTL "event" is defined as a transition from ≤ 0 to > 0 in the column's values. The time of that sample is taken as the trial onset. Trial offset is the next time the corresponding OFF column rises above 0; if no OFF column is found or it never fires, the offset defaults to `onset + TRIAL_LEN_S` (30 seconds by default).

### 7.2 Tone-status-based detection (`CS_DETECTION_MODE = "tone_status"`)

If the CSV contains continuous tone-status columns (e.g. `CS+ tone status`, `CS- tone status`), trials are detected from contiguous blocks where the value is > 0. This is more reliable than TTL pulse detection when the CS is a maintained tone rather than a brief event marker, or when AnyMaze is recording across multiple boxes with inconsistent TTL propagation.

The column name patterns are configurable via `TONE_STATUS_COL_CSPLUS` and `TONE_STATUS_COL_CSMINUS` in `runner.py`. Matching is flexible — any natural-language form of the pattern resolves correctly (see [Section 5.4](#54-cs--cs--trial-detection)).

### 7.3 Auto mode (`CS_DETECTION_MODE = "auto"`)

Tone-status detection is attempted first. If the tone-status columns are not found (a `ValueError` is raised internally), the pipeline falls back to TTL detection automatically. This is the default and is recommended when you are unsure which columns your AnyMaze export contains, or when your dataset mixes export configurations across sessions.

The source description (which columns were actually used) is recorded in the per-subject log of the US-locked run report and is available in all other analysis scripts via the `cs_source` return value of `utils.detect_trials()`.

### 7.4 Trial indexing

Trials are numbered separately by type (CS+, CS-, ITI) in the order they appear in time. So the first CS+ is trial_index 1, the second CS+ is trial_index 2, and so on — regardless of any CS- or ITI windows in between. This index is what appears on the x-axis in all figures.

**ITI windows** are defined as the `ITI_LEN_S` (60 seconds by default) immediately following each trial's offset.

---

## 8. The Math

### 8.1 % Time freezing

For each trial or ITI window `[start, end)`, the pipeline integrates the binary freezing signal over time:

```
freeze_seconds = Σ overlap(segment_i, [start, end)) × freezing_state_i
freeze_pct     = 100 × freeze_seconds / window_duration
```

Where each `segment_i` spans from sample `i` to sample `i+1` (or to the end of the recording for the last sample), and `freezing_state_i` is 1 if the animal is freezing at sample `i`, 0 otherwise. This is a piecewise-constant (step function) integration — the signal is assumed to hold its value until the next sample.

### 8.2 Freezing bouts

A freezing bout is a contiguous period where the freezing signal is > 0 within a trial window. The pipeline scans through the signal sample by sample, recording the time of each 0→1 transition (bout onset) and each 1→0 transition (bout offset). If the signal is already 1 at the window start, a bout is considered to have started at the window boundary. Similarly, if the signal is still 1 at the window end, the bout is closed at the window boundary.

**Bout count** per trial is derived by counting the number of bouts detected in that window. Note: trials with zero bouts contribute a count of 0.

### 8.3 % Time on platform

Identical in method to % time freezing, but uses the `in_platform` binary signal instead of the `freezing` signal:

```
platform_seconds = Σ overlap(segment_i, [start, end)) × platform_state_i
platform_pct     = 100 × platform_seconds / window_duration
```

### 8.4 Latency to platform

For each CS+ or CS- window, latency is the time elapsed from the window start to the first moment the animal transitions from off-platform to on-platform (a 0→1 transition in `in_platform`):

```
latency = time_of_first_entry − window_start
```

If the animal is already on the platform at the window start, latency = 0. If the animal never enters the platform during the window, latency = NaN. NaN values are excluded from mean calculations but appear in raw output CSVs.

If your CSV already contains an explicit latency column (named something like `latency_to_platform_s` or `latency_s`), that value is used directly instead of being computed.

### 8.5 Evade / Escape / Endure classification

Each CS+ trial with a detected US (shock) window is classified into one of three outcomes:

**Evade:** The animal was on the platform for the **entire** US window.
```
platform fraction during US ≥ 1 − 1e-6
```

**Endure:** The animal was off the platform for the **entire** US window.
```
platform fraction during US ≤ 1e-6
```

**Escape:** The animal was off the platform at US onset but reached the platform before US offset — i.e., a 0→1 transition in `in_platform` occurs strictly between US start and US end.

Any partial platform contact that does not fit the full-on or full-off definitions, and where no clean 0→1 transition is detected, is also classified as Escape (conservative fallback).

Trials where no US TTL is detected, or where the US onset falls after the CS+ offset, are classified as `no_us` and excluded from all percentage calculations and figures.

**Per-animal proportions** are computed before group averaging:

```
evade_pct (animal)  = 100 × (# evade trials) / (# valid CS+ trials)
escape_pct (animal) = 100 × (# escape trials) / (# valid CS+ trials)
endure_pct (animal) = 100 × (# endure trials) / (# valid CS+ trials)
```

Group means are then computed by averaging per-animal values (equal weight per animal, regardless of how many trials each animal contributed).

### 8.6 Group means and SEM in figures

For all line figures (freezing, platform, latency), the shaded ribbon around group mean lines represents ±1 standard error of the mean:

```
SEM = SD / √n
```

Where `n` is the number of animals contributing data at that trial index on that day. Animals with missing data at a given trial index are excluded from that point's calculation only — they are not excluded from the entire day.

---

## 9. Output Files and Folder Structure

All outputs are written inside your BehaviorData folder. Nothing is written outside of it.

```
BehaviorData/
├── % time freezing/
│   ├── freezing_all_days_concat.csv         ← all animals, all days, long format
│   ├── freezing_csplus_individual_tiled.svg
│   ├── freezing_csplus_groupmeans_tiled.svg
│   ├── freezing_csplus_by_sex_tiled.svg      ← if FREEZING_BY_SEX = True
│   ├── freezing_csminus_*.svg
│   ├── freezing_iti_*.svg
│   ├── freezing_prism_ready.xlsx             ← if PRISM_EXPORT = True
│   ├── freezing_bouts/                       ← if FREEZING_BOUTS = True
│   │   ├── freezing_bouts_all_days_concat.csv
│   │   ├── bouts_csplus_*.svg
│   │   └── freezing_bouts_prism_ready.xlsx
│   └── litter_breakdown/                     ← if FREEZING_BY_LITTER = True
│       └── freezing_csplus_by_litter_tiled.svg
│
├── % time on platform/
│   ├── platform_all_days_concat.csv
│   ├── platform_csplus_*.svg
│   ├── platform_prism_ready.xlsx
│   └── latency_to_platform/                  ← if PLATFORM_LATENCY = True
│       ├── latency_all_days_concat.csv
│       ├── latency_csplus_*.svg
│       └── latency_prism_ready.xlsx
│
├── Shock outcomes (evade-escape-endure)/
│   ├── shock_outcomes_all_days_concat.csv
│   ├── stacked_eee_by_treatment_tiled.svg
│   ├── stacked_eee_by_sex_treatment_tiled.svg ← if EEE_BY_SEX = True
│   └── eee_prism_ready.xlsx                   ← if PRISM_EXPORT = True
│
└── US locked platform time/
    ├── us_locked_all_days_concat.csv           ← long format, one row per US event
    ├── us_locked_run_report.txt                ← always written (see Section 11)
    ├── control/                                ← one folder per treatment group
    │   ├── control_shock_avoidance.csv
    │   ├── us_locked_heatmap_control.svg
    │   ├── control_prism_ready.xlsx
    │   └── cohort_1/                           ← if SEPARATE_BY_COHORT = True
    │       ├── us_locked_heatmap_control_cohort1.svg
    │       └── ...
    └── ELS/
        └── ...
```

### Concatenated CSV columns

All concatenated CSVs share a common set of identifier columns:

| Column | Description |
|---|---|
| `animal_id` | From metadata |
| `behavior_id` | Extracted from filename |
| `treatment_group` | Canonical label (after alias resolution) |
| `sex` | `M`, `F`, or `Unknown` |
| `litter_id` | From metadata if present |
| `test_date` | From filename (MM-DD-YY) |
| `day` | Parsed from folder name (e.g. `d01`) |
| `context` | Parsed from folder name (e.g. `contextA`) |
| `session_label` | Parsed from folder name (e.g. `habituation`) |
| `_day_folder` | Full folder name (used internally for sorting) |
| `_source_csv` | Original filename |
| `trial_type` | `CS+`, `CS-`, or `ITI` |
| `trial_index` | 1-based index within that trial type for that animal × day |

### Prism-ready Excel format

Each Prism-ready `.xlsx` file contains one sheet per combination of treatment group × day × trial type. Within each sheet:
- **Rows** = trial index (1, 2, 3, ...)
- **Columns** = individual animal IDs
- **Values** = the metric (freeze_pct, platform_pct, latency, etc.)

This matches the expected input format for repeated-measures analyses in GraphPad Prism.

---

## 10. Troubleshooting

**`behavior_id 'XYZ' not in metadata`**
The behavior ID extracted from the filename could not be matched to any row in `animals_metadata.xlsx` using any of the six matching strategies. Check that the ID in your filename and in the spreadsheet are close enough for one of the strategies to catch (see [Section 6.2](#62-matching-to-metadata)). The most reliable fix is to ensure the behavior ID in your filenames exactly matches the `behavior_id` column in your spreadsheet.

**`No time column found`**
The CSV has no column whose normalized name starts with `time`. Check your AnyMaze export settings — the time column is usually called `Time (s)`.

**`No freezing column found`**
No column contains the substring `freez`. Check that AnyMaze exported a freezing state column and that it is not named something unusual like `Immobility`.

**`No trials detected`**
The TTL or tone-status columns for CS+ or CS- onset were not found or never fire in the recording. Check that your AnyMaze export includes the relevant columns, and verify `CS_DETECTION_MODE` is set appropriately. In `"ttl"` mode, check that column names contain the words `cs`, `plus` (or `minus`), and `on`/`off`. In `"tone_status"` mode, check that `TONE_STATUS_COL_CSPLUS` and `TONE_STATUS_COL_CSMINUS` match your actual column names.

**`Missing subfolder: .../% time freezing`**
The pipeline expects a subfolder named `% time freezing` (or whatever `FREEZING_SUBFOLDER` is set to) inside each day folder. This subfolder is where per-animal output CSVs should live. If you have not run the per-animal step yet, or your subfolders are named differently, update `FREEZING_SUBFOLDER` in `runner.py`.

**Figures appear but some panels are blank**
A blank panel means there was no data for that treatment group × day combination after filtering. This is expected if not all groups ran on all days. It can also happen if trial capping (`CS_TRIAL_CAP`) is set too low and removes all trials for some animals.

**Import error: `cannot import name 'run' from 'platform'`**
Python has a built-in module called `platform`. Rename `platform.py` to `platform_analysis.py` and update the import in `runner.py` to `from platform_analysis import run as run_platform`.

**Litter figures are empty**
`litter_id` must be present as a column in `animals_metadata.xlsx`. If it is missing or all values are blank, litter figures will be skipped with a warning. Set `FREEZING_BY_LITTER = False` if you do not have litter data.
