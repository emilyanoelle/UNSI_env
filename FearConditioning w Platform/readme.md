# Fear Conditioning Behavioral Analysis Pipeline

## Overview

This pipeline processes behavioral data from fear conditioning experiments recorded in AnyMaze. It computes % time freezing, freezing bout counts, % time on platform, latency to platform, and shock outcome classification (Evade / Escape / Endure) across multiple session days and multiple cohorts, then produces tiled figures and Prism-ready tables.

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
8. [How Column Names Are Normalized and Matched](#8-how-column-names-are-normalized-and-matched)
9. [The Math](#9-the-math)
10. [Output Files and Folder Structure](#10-output-files-and-folder-structure)
11. [Troubleshooting](#11-troubleshooting)
12. [US-Locked Platform Analysis](#12-us-locked-platform-analysis)
13. [Schema Checker](#13-schema-checker)

---

## Acknowledgements

The procedures of the US-locked analysis were taken from Eastman Lewis and Kenya Barnes, modified only to ensure ease of integration into this pipeline. Portions of this codebase were developed with assistance from Claude (Sonnet 4.6, Anthropic) and validated by ENV 2026.

---

## 1. Quick Start

1. Place all pipeline files in the same folder on your computer:
   ```
   runner.py
   utils.py
   freezing_analysis.py
   platform_analysis.py
   eee_analysis.py
   us_locked_analysis.py
   sanity_check.py
   behaviordata_schema_checker.py
   ```

2. Open `runner.py` in Spyder.

3. Set `BEHAVIORDATA_DIRS` to a list of one or more BehaviorData folder paths.

4. Set `ANALYSIS_OUTPUT_DIR` to the folder where combined across-cohort outputs should be written.

5. Set your treatment group names and colors (see [Section 5](#5-runner-configuration--full-reference)).

6. Toggle which analyses you want using the `True`/`False` switches.

7. Press play (▶). Outputs appear inside each BehaviorData folder and in the combined output directory.

> **First time with a new dataset?** Run `behaviordata_schema_checker.py` first (see [Section 13](#13-schema-checker)) to validate your folder structure, metadata, and CSV columns before running the full pipeline.

---

## 2. File Overview

| File | What it does |
|---|---|
| `runner.py` | **Start here.** All user settings live here. Calls the analysis scripts. |
| `utils.py` | Shared functions used by all analysis scripts. Do not edit unless you know what you are changing. |
| `freezing_analysis.py` | Two-pass pipeline: per-day % time freezing and bout counts, then cumulative figures across all cohorts. |
| `platform_analysis.py` | Two-pass pipeline: per-day % time on platform and latency, then cumulative figures across all cohorts. |
| `eee_analysis.py` | Classifies CS+ trials as Evade, Escape, or Endure across all days and cohorts. |
| `us_locked_analysis.py` | Computes % platform time locked to the shock (US) delivery window specifically, rather than the full CS+ trial. |
| `sanity_check.py` | Produces tracking coverage and IQR outlier workbooks, plus trial window timing consistency workbooks, from raw CSVs. |
| `behaviordata_schema_checker.py` | Standalone validator — checks folder structure, metadata, file naming, and CSV column compatibility before you run the pipeline. |

---

## 3. Required Data Structure

### 3.1 Top-level layout

You can point the pipeline at **one or more** BehaviorData folders. Each folder must follow the same structure:

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

When you supply multiple BehaviorData folders in `BEHAVIORDATA_DIRS`, each folder is treated as a separate cohort. Per-session outputs are always written inside their own BehaviorData folder. Combined across-cohort outputs (with all cohorts collapsed by treatment group) are written to `ANALYSIS_OUTPUT_DIR`. Per-cohort across-session outputs are written to `<BehaviorData>/Analysis/<subfolder>/`.

### 3.2 Session folder naming

Session folders **must** begin with `d##_` where `##` is a two-digit day number. The pipeline uses this prefix to sort days chronologically. The full expected format is:

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
- The session label (e.g. `habituation`) can be any descriptive text without spaces

Older folder formats (`Day 1 habituation_2`) are also supported for backward compatibility, but the `d##_context#_label` format is preferred and is what `behaviordata_schema_checker.py` checks for.

### 3.3 Raw CSV file naming

Each animal's AnyMaze export file must include a date token in `MM-DD-YY` format, followed by a behavior ID:

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
- `BehaviorID` — the animal's behavior ID, which must match (or be fuzzy-matchable to) an entry in `animals_metadata.xlsx`
- `_index` and `_fixed` suffixes are handled automatically and stripped during parsing
- By default, all `.csv` files in a session folder are processed. Set `CSV_SUFFIX` in `runner.py` to restrict processing to files whose names end with a specific string (e.g. `"_fixed"`)

### 3.4 Required columns in AnyMaze CSVs

Column names are normalized automatically (spaces collapsed, case lowered, `+` → `plus`, `-` → `minus`, parentheses removed), so minor formatting differences are tolerated.

See [Section 8](#8-how-column-names-are-normalized-and-matched) for the exact rules, examples, and ambiguous cases. The short version is that aliases in `runner.py` are checked after header normalization, and ambiguous matches raise an error instead of silently choosing the first option.

**Always required:**
- A time column — any column whose normalized name starts with `time` (e.g. `Time (s)`)

**For freezing analysis:**
- A freezing state column — any column whose normalized name contains `freez` (e.g. `Freezing`)

**For platform analysis and EEE:**
- `In platform` — binary column (1 = on platform, 0 = off). Normalized to `in_platform`

**For trial detection (all analyses):**
- Either continuous tone-status columns (`CS+ tone status`, `CS- tone status`) or TTL ON/OFF event columns (`CS+ ON activated`, `CS+ OFF activated`, `CS- ON activated`, `CS- OFF activated`). See [Section 7](#7-how-trials-are-detected) for details on which to use and how to configure detection mode.

**For EEE analysis:**
- If `USE_SHOCKER_COLUMN = True`, `Shocker active` is used as the US/shock source.
- If `USE_SHOCKER_COLUMN = False`, explicit US/shock TTL columns are used if present (for example `US ON activated`, `US OFF activated`, `Shock ON activated`, `Shock OFF activated`).

**For US-locked analysis (documented Mode A only):**
- `Shocker active` - binary column indicating shock delivery. This name is configurable through `COLUMN_ALIASES["shocker_active"]`; see [Section 8.5](#85-us-and-shock-columns).

---

## 4. Metadata Spreadsheet

`animals_metadata.xlsx` must live directly inside each BehaviorData folder. It links behavior IDs (from filenames) to animal-level information.

### Required columns

| Column | Description |
|---|---|
| `behavior_id` | The ID used in CSV filenames (e.g. `9C-1`, `10a`) |

### Recommended columns

| Column | Description |
|---|---|
| `animal_id` | Your internal animal identifier (falls back to `behavior_id` if absent) |
| `treatment_group` | Group label (e.g. `ctrl`, `ELS`) — aliases are resolved via `TREATMENT_ALIASES` in `runner.py` |
| `sex` | `M`, `F`, or leave blank (normalized automatically; blank becomes `Unknown`) |
| `litter_id` | Required only if `FREEZING_BY_LITTER = True` |
| `cohort_id` | Groups animals into cohorts for per-cohort output figures. If this column is absent, the BehaviorData folder name is used as the cohort label automatically, and a notice is printed. |

### Column name formatting

Column names in the spreadsheet are normalized the same way as CSV columns. `Behavior ID` and `behavior_id` both normalize to `behavior_id`, but `BehaviorID` normalizes to `behaviorid` and is **not** recognized by the current code because camel-case words are not split automatically. Using the exact names listed above is safest.

### Multi-cohort metadata

When multiple BehaviorData folders are listed in `BEHAVIORDATA_DIRS`, each folder needs its own `animals_metadata.xlsx`. If the same `behavior_id` value appears in more than one cohort's metadata, the pipeline prints a warning — ensure behavior IDs are unique across cohorts, or verify that any duplication is intentional.

---

## 5. Runner Configuration — Full Reference

Open `runner.py`. Everything between the top and the `DO NOT EDIT BELOW THIS LINE` marker is yours to change.

### 5.1 Data directories

```python
BEHAVIORDATA_DIRS = [
    r"Z:\path\to\Cohort1\BehaviorData",
    r"Z:\path\to\Cohort2\BehaviorData",
]

ANALYSIS_OUTPUT_DIR = r"Z:\path\to\combined\Analysis"
```

List every BehaviorData folder you want to include. Use raw strings (the `r` prefix) to avoid issues with backslashes on Windows. The combined output directory receives figures and CSVs that collapse all cohorts together by treatment group.

### 5.2 Treatment group configuration

```python
TREATMENT_ALIASES = {
    "control": ["ctrl", "cntrl", "control", "Control", "CTRL"],
    "ELS":     ["ELS", "els", "LBN", "lbn"],
}
```

- The **keys** (`"control"`, `"ELS"`) are the canonical labels that appear in all outputs and figures
- The **values** are every spelling or capitalization of that label that might appear in your metadata spreadsheets
- Matching is case-insensitive
- The **first key** is treated as the control group for figure ordering
- Add more groups by adding more key-value pairs

```python
TREATMENT_COLORS = {
    "control": "#0F52BA",
    "ELS":     "#EC5800",
}
```

Colors must use the same keys as `TREATMENT_ALIASES`. Female colors and unknown-sex colors are derived automatically as lighter tints of these base colors.

### 5.3 Analysis toggles

```python
RUN_SANITY_CHECK = True    # tracking coverage, IQR outliers, trial window consistency
RUN_FREEZING     = True    # % time freezing + freezing bouts (if enabled below)
RUN_PLATFORM     = False   # % time on platform + latency (if enabled below)
RUN_EEE          = False   # Evade / Escape / Endure shock outcome classification
RUN_US_LOCKED    = False   # % platform time locked to the shock delivery window
```

Set any of these to `False` to skip that analysis entirely.

### 5.4 CS+ / CS- trial detection

All analysis scripts share a single trial detection configuration, set once in `runner.py`:

```python
CS_DETECTION_MODE = "tone_status"
```

The three modes are:

**`"ttl"`** — detect trial boundaries from brief CS+/CS- ON/OFF TTL pulse columns. The pipeline looks for columns whose normalized names contain `cs`, `plus` (or `minus`), and `on`/`off`.

**`"tone_status"`** — detect trial boundaries from continuous tone-status columns that hold a value of 1 for the full duration of the tone and 0 otherwise. More reliable when TTL pulses are brief or inconsistent across recording boxes.

**`"auto"`** — try tone-status detection first; fall back to TTL if tone-status columns are not found. Recommended when you are unsure which columns your AnyMaze export contains, or when your dataset mixes export configurations across sessions.

If you use `"tone_status"` or `"auto"`, configure the column name patterns here:

```python
TONE_STATUS_COL_CSPLUS  = "cs plus tone status"
TONE_STATUS_COL_CSMINUS = "cs minus tone status"
```

Aliases are checked first, then these patterns are used as fallback matchers when `COLUMN_MATCH_MODE = "fallback"`. Change these strings or the `COLUMN_ALIASES` entries only if your AnyMaze export uses different column names.

### 5.5 Column matching configuration

Column aliases let you configure expected header variations in `runner.py` without editing the analysis scripts:

```python
COLUMN_MATCH_MODE = "fallback"  # or "strict"

COLUMN_ALIASES = {
    "time": ["Time (s)", "Time"],
    "freezing": ["Freezing", "Freezing state"],
    "in_platform": ["In platform", "Inside platform"],
    "us_on": ["US ON activated", "Shock ON activated"],
    "us_off": ["US OFF activated", "Shock OFF activated"],
    "shocker_active": ["Shocker active"],
}
```

Use `strict` when you want the pipeline to use only the aliases you listed. Use `fallback` when you want aliases first, then the older heuristic matching. In both modes, ambiguous matches raise a clear `Ambiguous column match...` error.

### 5.6 Trial caps

```python
CS_TRIAL_CAP  = 10   # max CS+ or CS- trials per animal per day in figures/Prism tables
ITI_TRIAL_CAP = 20   # max ITI windows per animal per day in figures/Prism tables
EEE_TRIAL_CAP = 10   # max CS+ trials used for EEE classification
```

Trials beyond these limits are excluded from figures and Prism tables. They remain in the raw concatenated CSVs.

### 5.7 File naming conventions

```python
CSV_SUFFIX = ""   # set to e.g. "_fixed" to process only files ending in "_fixed.csv"
```

```python
FREEZING_SUBFOLDER  = "% time freezing"
PLATFORM_SUBFOLDER  = "% time on platform"
LATENCY_SUBFOLDER   = "latency to platform"
EEE_SUBFOLDER       = "Shock outcomes (evade-escape-endure)"
```

These subfolder names are used both when writing per-day outputs (Pass 1) and when reading them back for cumulative analysis (Pass 2). If you change these names after running Pass 1, Pass 2 will not find the existing summaries and will warn that they are missing.

### 5.8 Sub-analysis toggles

```python
# Freezing
FREEZING_BOUTS      = True    # compute and plot freezing bout counts
FREEZING_BY_SEX     = False   # sex × treatment breakdown figures
FREEZING_BY_LITTER  = True    # litter-level figures (requires litter_id in metadata)

# Platform
PLATFORM_LATENCY    = True    # compute latency to first platform entry per trial
PLATFORM_BY_SEX     = False   # sex × treatment breakdown figures

# EEE
EEE_BY_SEX          = False   # sex × treatment stacked bar figures

# Prism
PRISM_EXPORT        = True    # write Prism-ready Excel tables for all enabled analyses
```

### 5.9 US/shock-window settings

```python
USE_SHOCKER_COLUMN     = False   # True = EEE/US-locked use Shocker active
US_DURATION_S          = 2.0    # US-locked Mode B only: assumed shock window duration in seconds
US_CHANCE_BASELINE_PCT = 16.4   # subtracted from platform_pct to give "above chance" values
INCLUDE_TREATMENTS     = None   # None = all groups; list e.g. ["ELS"] to restrict
EXCLUDE_BEHAVIOR_IDS   = []     # explicitly skip animals by behavior_id
HEATMAP_SORT           = "response"  # "response" (highest responders on top) or "alphabetical"
```

See [Section 12](#12-us-locked-platform-analysis) for a full description of US-locked modes. For EEE, `USE_SHOCKER_COLUMN = True` uses `Shocker active`; `False` uses explicit US/Shock ON/OFF TTL columns.

---

## 6. How Files and Animals Are Matched

### 6.1 Extracting the behavior ID from a filename

Given a clean filename like `03-05-26_9C-1_1_fixed.csv`, the pipeline:

1. Finds the date token: `03-05-26`
2. Takes everything after the date and separator: `9C-1_1_fixed`
3. Strips trailing `_fixed`, `_1_fixed`, `_2_fixed`, etc.: `9C-1`
4. Takes only the leading token before any underscore: `9C-1`

So `03-05-26_9C-1_1_fixed.csv` → behavior ID token `9C-1`.

If that leading token does not match metadata, the pipeline does a metadata-aware scan of the rest of the filename. It looks for known `behavior_id` values from `animals_metadata.xlsx`, prefers the longest valid match, and requires the match to end cleanly before a separator such as `_`, whitespace, brackets, or the end of the filename.

Examples:

| Metadata contains | Filename | Parsed behavior ID |
|---|---|---|
| `6069-cobalt` | `03-10-25_asdf-_sdf6069-cobalt_ajdf.csv` | `6069-cobalt` |
| `6069-c` and `6069-cobalt` | `03-10-25_asdf-_sdf6069-cobalt_ajdf.csv` | `6069-cobalt` |
| `6069-c` | `03-10-25_asdf-_sdf6069-c_ajdf.csv` | `6069-c` |
| `6069-c` | `03-10-25_asdf-_sdf6069-cobalt_ajdf.csv` | no metadata-aware match for `6069-c`; the filename token continues |

This prevents a short ID such as `6069-c` from being pulled out of a longer token such as `6069-cobalt`. If no metadata-aware match is found, the parser falls back to the leading-token behavior described above.

### 6.2 Matching to metadata

The extracted token is looked up in `animals_metadata.xlsx` using a six-step fallback chain. Each step is tried in order; the first match wins.

| Step | Strategy | Example |
|---|---|---|
| 1 | Exact match (case-insensitive, stripped) | `9c-1` matches `9C-1` |
| 2 | Alphanumeric-only match (remove all non-alphanumeric characters) | `9c1` matches `9C-1` |
| 3 | Strip trailing `-N` suffix and match the base | `9c` matches metadata entry `9C` |
| 4 | Numeric suffix heuristics — match by trailing number, with or without letter | `1` matches `animal-1`; `4a` matches `4A` |
| 5 | Token boundary match — look for the ID surrounded by `-` or `_` | `3a` matches `group_3a_cohort2` |
| 6 | Prefix or suffix match on alphanumeric-only strings | `3a` matches `box3a` |

If none of these steps find a match, the file is skipped and a warning is printed. The most reliable fix is to ensure the behavior ID in your filenames exactly matches the `behavior_id` column in your spreadsheet.

### 6.3 Treatment label normalization

After the metadata row is found, the `treatment_group` value is looked up case-insensitively against your `TREATMENT_ALIASES` dictionary. If no alias matches, the raw value from the spreadsheet is used as-is. You never need to rename anything in your data files — just list all the variants you use as aliases in `runner.py`.

---

## 7. How Trials Are Detected

All five analysis scripts use a single shared trial detection function in `utils.py`. The mode is configured once in `runner.py` via `CS_DETECTION_MODE` and applies uniformly across all analyses.

### 7.1 TTL-based detection (`"ttl"`)

Rising edges (transitions from ≤ 0 to > 0) in the CS+/CS- ON columns mark trial onsets. Trial offsets come from the corresponding OFF column; if the OFF column is absent or never fires, offset defaults to onset + 30 seconds. The pipeline searches for columns matching patterns like `cs plus on activat`, `cs minus on activat`, and their OFF equivalents using flexible substring matching.

### 7.2 Tone-status-based detection (`"tone_status"`)

Contiguous blocks where the tone-status column value is > 0 define trial boundaries. Onset is the first sample in each block; offset is the last sample before the value drops back to 0. Column name patterns are configurable via `TONE_STATUS_COL_CSPLUS` and `TONE_STATUS_COL_CSMINUS`. This mode is generally more reliable than TTL detection for maintained tones or multi-box setups.

### 7.3 Auto mode (`"auto"`)

Tone-status detection is attempted first. If the required columns are not found, the pipeline falls back to TTL detection automatically. The method actually used is recorded in the per-subject log of the US-locked run report and in the `cs_source` value returned internally by `utils.detect_trials()`.

### 7.4 Trial indexing and ITI windows

Trials are numbered separately by type (CS+, CS-, ITI) in chronological order. So the first CS+ is `trial_index` 1, the second CS+ is `trial_index` 2, and so on — regardless of any CS- or ITI windows that fall in between. ITI windows are defined as the 60-second period immediately following each trial's offset.

---

## 8. How Column Names Are Normalized and Matched

This section describes the current code behavior exactly. The pipeline first normalizes every header, then resolves each needed column through `COLUMN_ALIASES` in `runner.py`. If no alias matches and `COLUMN_MATCH_MODE = "fallback"`, the older heuristic rules are used. If more than one column matches the same role, the run raises an `Ambiguous column match...` error instead of guessing.

### 8.1 The normalization function

All raw AnyMaze CSV files are read through `utils.load_csv()`, and metadata spreadsheet headers are normalized in `utils.load_metadata()`.

```python
def norm_colname(c: str) -> str:
    c = str(c).strip().lower()
    c = c.replace("\u00a0", " ")
    c = re.sub(r"\s+", " ", c)
    c = c.replace("(", "").replace(")", "")
    c = c.replace("+", "plus").replace("-", "minus")
    c = c.replace("/", " ").replace("\\", " ")
    c = c.replace(" ", "_")
    return c

def load_csv(path: Path) -> pd.DataFrame:
    ...
    return df.rename(columns={c: norm_colname(c) for c in df.columns})
```

Examples:

| Original header | Normalized header |
|---|---|
| `Time (s)` | `time_s` |
| `CS+ tone status` | `csplus_tone_status` |
| `CS plus tone status` | `cs_plus_tone_status` |
| `CS- ON activated` | `csminus_on_activated` |
| `In platform` | `in_platform` |
| `Inside platform` | `inside_platform` |
| `Not Freezing` | `not_freezing` |
| `Behavior ID` | `behavior_id` |
| `BehaviorID` | `behaviorid` |

Important limits:

- CamelCase is not split. `BehaviorID` becomes `behaviorid`, not `behavior_id`.
- Underscores are kept. `in_platform` stays `in_platform`.
- Most punctuation is not removed unless listed in the function above.
- The code only understands synonyms that are listed in `COLUMN_ALIASES`, or that match the older heuristics when `COLUMN_MATCH_MODE = "fallback"`.

### 8.2 Alias matching, fallback mode, and ambiguity

Column roles are configured in `runner.py`:

```python
COLUMN_MATCH_MODE = "fallback"  # or "strict"

COLUMN_ALIASES = {
    "freezing": ["Freezing", "Freezing state"],
    "in_platform": ["In platform", "Inside platform"],
    "us_on": ["US ON activated", "Shock ON activated"],
    "us_off": ["US OFF activated", "Shock OFF activated"],
}
```

The shared resolver in `utils.py` does this:

```python
def resolve_column(df, role, cfg, fallback=None, required=True):
    # 1. exact normalized matches from COLUMN_ALIASES[role]
    # 2. if COLUMN_MATCH_MODE == "fallback", try the old heuristic matcher
    # 3. if more than one column matches, raise ValueError("Ambiguous ...")
```

In `strict` mode, required columns must appear in `COLUMN_ALIASES`; no heuristic guessing is used. In `fallback` mode, aliases still win first, but if no alias matches, the old heuristic is attempted.

If a CSV contains both `Not Freezing` and `Freezing`, and both match the `freezing` role, the pipeline now fails loudly:

```text
Ambiguous column match for 'freezing': matched 'not_freezing', 'freezing'.
Update COLUMN_ALIASES['freezing'] in runner.py so only one column matches.
```

### 8.3 Required signal columns

| Purpose | Current rule | Matches | Does not match | If missing |
|---|---|---|---|---|
| Time | `COLUMN_ALIASES["time"]`, then fallback `startswith("time")` | `Time`, `Time (s)`, `time_seconds` | `Elapsed time` unless added as an alias | File is skipped with `No column found for 'time'` |
| Freezing | `COLUMN_ALIASES["freezing"]`, then fallback contains `freez` | `Freezing`, `Freezing state` | `Immobility` unless added as an alias | File is skipped for freezing |
| Platform state | `COLUMN_ALIASES["in_platform"]`, then fallback exact `in_platform` | `In platform`, `Inside platform`, `in_platform` | `On platform` unless added as an alias | File is skipped for platform, EEE, and US-locked |
| Platform latency | `COLUMN_ALIASES["latency_to_platform"]`, then latency fallback | `latency_to_platform_s`, `Latency to platform`, `latency` | `time_to_platform` unless added as an alias | Latency is computed from the platform state |
| Metadata behavior ID | Exact normalized column `behavior_id` | `Behavior ID`, `behavior_id` | `BehaviorID`, `Mouse ID` | Metadata cannot be used |

The platform example is now configurable. If one CSV has `In platform` and another CSV has `Inside platform`, both are accepted because both names are listed under `COLUMN_ALIASES["in_platform"]` by default. If your export says `On platform`, add it to that alias list.

### 8.4 Trial detection columns

Trial detection is configured by `CS_DETECTION_MODE` in `runner.py`, and its columns are also covered by `COLUMN_ALIASES`.

For tone-status mode, the resolver first checks:

```python
COLUMN_ALIASES["csplus_tone_status"]
COLUMN_ALIASES["csminus_tone_status"]
```

If no alias matches and `COLUMN_MATCH_MODE = "fallback"`, the configured patterns are normalized, then matched in two passes:

```python
def find_one(pattern: str) -> Optional[str]:
    norm = norm_colname(pattern)
    matches = [c for c in cols if norm in c]
    if matches:
        return matches[0]
    tokens = norm.split("_")
    matches = [c for c in cols if all(t in c for t in tokens)]
    return matches[0] if matches else None
```

With the default pattern `cs plus tone status`, all of these can match CS+:

```text
CS plus tone status    -> cs_plus_tone_status
CS+ tone status        -> csplus_tone_status
box1 CS+ tone status   -> box1_csplus_tone_status
```

If multiple columns match the same tone role, the resolver raises an `Ambiguous column match...` error. If tone-status mode is required and either CS+ or CS- is missing, `find_tone_status_cols()` raises a `ValueError`, which can stop the run. If mode is `auto`, the code tries tone-status first and falls back to TTL detection if tone-status columns are not found.

For TTL mode, the resolver checks these aliases first:

```python
COLUMN_ALIASES["csplus_on"]
COLUMN_ALIASES["csplus_off"]
COLUMN_ALIASES["csminus_on"]
COLUMN_ALIASES["csminus_off"]
```

If no alias matches and `COLUMN_MATCH_MODE = "fallback"`, the code searches normalized names for ordered substrings:

```python
csplus_on = (
    like(["cs", "plus", "on", "activat"])
    or like(["csplus", "on"])
    or like(["cs", "plus", "on"])
)
```

The same pattern is used for CS+ OFF, CS- ON, and CS- OFF. `like(["cs", "plus", "on", "activat"])` effectively means:

```text
the column contains "cs", then later "plus", then later "on", then later "activat"
```

If a CS ON column is found but the matching OFF column is missing or never fires, the trial end defaults to onset + 30 seconds. If the ON column is missing, that trial type is not detected.

### 8.5 US and shock columns

When `USE_SHOCKER_COLUMN = True`, EEE and US-locked use the `Shocker active` column. This is the usual setting when your AnyMaze export has a binary shock-delivery column.

When `USE_SHOCKER_COLUMN = False`, EEE searches for explicit US/shock TTL columns through the shared resolver. It checks `COLUMN_ALIASES["us_on"]` and `COLUMN_ALIASES["us_off"]` first. If no alias matches and `COLUMN_MATCH_MODE = "fallback"`, it uses token-based fallback matching:

```python
us_on = (
    like(["us", "on", "activat"])
    or like(["shock", "on", "activat"])
    or like(["us", "on"])
    or like(["shock", "on"])
)
us_off = (
    like(["us", "off", "activat"])
    or like(["shock", "off", "activat"])
    or like(["us", "off"])
    or like(["shock", "off"])
)
```

Best column names:

```text
US ON activated
US OFF activated
Shock ON activated
Shock OFF activated
```

The fallback matcher treats `us` as a token, so `cs_plus_off_activated` is not considered a US column just because `plus` contains the letters `us`. For the safest behavior, keep `US ON activated` / `US OFF activated` in `COLUMN_ALIASES` and use `COLUMN_MATCH_MODE = "strict"`.

For US-locked Mode A, `Shocker active` is resolved through `COLUMN_ALIASES["shocker_active"]`, with fallback to columns containing both `shocker` and `active`.

### 8.6 Duplicate normalized names

Avoid having two columns that normalize to the same name. For example:

```text
In platform -> in_platform
in_platform -> in_platform
```

Pandas allows duplicate column labels, but the shared resolver treats duplicate matches as ambiguous. Keep one canonical version of each required column.

### 8.7 Common confusion cases

- `Not Freezing` and `Freezing` in the same file: the pipeline raises an ambiguous `freezing` column error if both match.
- `Inside platform` instead of `In platform`: accepted by default because it is listed in `COLUMN_ALIASES["in_platform"]`.
- `BehaviorID` instead of `Behavior ID`: the metadata column is not recognized because it normalizes to `behaviorid`.
- `Elapsed time` instead of `Time (s)`: add `Elapsed time` to `COLUMN_ALIASES["time"]`.
- Extra helper columns containing the same keywords can be selected by fallback mode. Use aliases plus `COLUMN_MATCH_MODE = "strict"` to disable heuristic guessing.
- Files are handled independently. Add every expected spelling to `COLUMN_ALIASES`.

When in doubt, use these exact human-readable headers:

```text
Time (s)
Freezing
In platform
CS+ tone status
CS- tone status
US ON activated
US OFF activated
```

For metadata, use:

```text
behavior_id
animal_id
treatment_group
sex
litter_id
cohort_id
```

---

## 9. The Math

### 9.1 % Time freezing

For each trial or ITI window `[start, end)`, the binary freezing signal is integrated using a piecewise-constant (step function) model:

```
freeze_seconds = Σ overlap(segment_i, [start, end)) × freezing_state_i
freeze_pct     = 100 × freeze_seconds / window_duration
```

Each segment spans from sample `i` to sample `i+1`, and the signal is assumed to hold its value until the next sample.

### 9.2 Freezing bouts

A bout is a contiguous period where the freezing signal is > 0 within a trial window. The pipeline scans sample by sample, recording 0→1 transitions (bout onsets) and 1→0 transitions (bout offsets). If the signal is already 1 at the window boundary, a bout is considered to have started at that boundary. Bout count per trial is the number of bouts detected in that window.

### 9.3 % Time on platform

Identical in method to % time freezing, using the `in_platform` binary signal:

```
platform_seconds = Σ overlap(segment_i, [start, end)) × platform_state_i
platform_pct     = 100 × platform_seconds / window_duration
```

### 9.4 Latency to platform

For each CS+ or CS- window, latency is the elapsed time from window start to the first 0→1 transition in `in_platform`. If the animal is already on the platform at window start, latency = 0. If the animal never enters the platform, latency = NaN. If the CSV already contains an explicit latency column (e.g. `latency_to_platform_s`), that value is used directly.

### 9.5 Evade / Escape / Endure classification

Each CS+ trial with a detected US window is classified into one of three outcomes:

- **Evade:** Animal was on the platform for the entire US window (`platform fraction ≥ 1 − 1e-6`)
- **Endure:** Animal was off the platform for the entire US window (`platform fraction ≤ 1e-6`)
- **Escape:** A 0→1 transition in `in_platform` occurs between US onset and US offset (animal reached the platform after the shock started). Any partial contact that fits neither Evade nor Endure is also classified as Escape.

Trials where no US is detected, or where US onset falls after CS+ offset, are classified as `no_us` and excluded from all percentage calculations.

Per-animal proportions are computed before group averaging, weighting each animal equally regardless of trial count:

```
evade_pct  = 100 × (# evade trials) / (# valid CS+ trials)
escape_pct = 100 × (# escape trials) / (# valid CS+ trials)
endure_pct = 100 × (# endure trials) / (# valid CS+ trials)
```

### 9.6 Group means and SEM

The shaded ribbon in all line figures represents ±1 SEM:

```
SEM = SD / √n
```

where `n` is the number of animals contributing data at that trial index on that day. Animals missing data at a given trial index are excluded from that point's calculation only.

---

## 10. Output Files and Folder Structure

### 10.1 Per-day outputs (written during Pass 1)

Both the freezing and platform pipelines run in two passes. Pass 1 writes a per-day summary CSV into each session folder before cumulative figures are produced:

```
BehaviorData/
└── d01_contextA_habituation/
    └── Analysis/
        ├── % time freezing/
        │   ├── freezing_summary.csv
        │   └── freezing_bouts/              ← if FREEZING_BOUTS = True
        │       └── Freezing_Bouts_Long.csv
        └── % time on platform/
            └── platform_summary.csv
```

Pass 2 reads these per-day summaries back and assembles the cumulative figures and Prism tables.

### 10.2 Across-session outputs per BehaviorData folder

After Pass 2, each BehaviorData folder receives its own analysis outputs for the cohort it contains:

```
BehaviorData/
└── Analysis/
    ├── sanity_checks/                        ← if RUN_SANITY_CHECK = True
    │   ├── sanity_check_tracking.xlsx
    │   └── sanity_check_trial_windows.xlsx
    ├── % time freezing/
    │   ├── freezing_<cohort_id>_all_days_concat.csv
    │   ├── freezing_<cohort_id>_csplus_individual_tiled.svg
    │   ├── freezing_<cohort_id>_csplus_groupmeans_tiled.svg
    │   ├── freezing_<cohort_id>_csplus_by_sex_tiled.svg    ← if FREEZING_BY_SEX = True
    │   ├── freezing_<cohort_id>_prism_ready.xlsx           ← if PRISM_EXPORT = True
    │   ├── freezing_bouts/                                 ← if FREEZING_BOUTS = True
    │   └── litter_breakdown/                               ← if FREEZING_BY_LITTER = True
    └── % time on platform/
        └── ...
```

### 10.3 Combined across-cohort outputs

The combined output directory (`ANALYSIS_OUTPUT_DIR`) receives figures that collapse all cohorts together by treatment group:

```
Analysis/
├── % time freezing/
│   ├── freezing_all_days_concat.csv
│   ├── freezing_csplus_individual_tiled.svg
│   ├── freezing_csplus_groupmeans_tiled.svg
│   ├── freezing_prism_ready.xlsx
│   └── freezing_bouts/
├── % time on platform/
│   ├── platform_all_days_concat.csv
│   ├── platform_csplus_groupmeans_tiled.svg
│   ├── platform_prism_ready.xlsx
│   └── latency_to_platform/                ← if PLATFORM_LATENCY = True
├── Shock outcomes (evade-escape-endure)/
│   ├── eee_<cohort_id>_all_days_concat.csv
│   ├── eee_<cohort_id>_stacked_by_treatment.svg
│   └── eee_<cohort_id>_prism_ready.xlsx
└── US_lock_plttime/
    ├── us_locked_all_days_concat.csv
    ├── control/
    │   └── us_locked_heatmap_control_tiled.svg
    └── ELS/
        └── us_locked_heatmap_ELS_tiled.svg
```

### 10.4 Concatenated CSV columns

All concatenated CSVs share a common set of identifier columns:

| Column | Description |
|---|---|
| `animal_id` | From metadata |
| `behavior_id` | Extracted from filename |
| `treatment_group` | Canonical label after alias resolution |
| `sex` | `M`, `F`, or `Unknown` |
| `litter_id` | From metadata if present |
| `cohort_id` | From metadata, or BehaviorData folder name if absent |
| `test_date` | From filename (`MM-DD-YY`) |
| `day` | Parsed from folder name (e.g. `d01`) |
| `context` | Parsed from folder name (e.g. `contextA`) |
| `session_label` | Parsed from folder name (e.g. `habituation`) |
| `_day_folder` | Full folder name (used for sorting) |
| `_source_csv` | Original filename |
| `trial_type` | `CS+`, `CS-`, or `ITI` |
| `trial_index` | 1-based index within that trial type for that animal × day |

### 10.5 Prism-ready Excel format

Each Prism-ready `.xlsx` file contains one sheet per combination of treatment group × day × trial type. Within each sheet: rows = trial index (1, 2, 3, ...), columns = individual animal IDs, values = the metric (e.g. `freeze_pct`, `platform_pct`, `latency_to_platform_s`). This matches the expected input format for repeated-measures analyses in GraphPad Prism.

---

## 11. Troubleshooting

**`behavior_id 'XYZ' not in metadata`**
The behavior ID extracted from the filename could not be matched to any row in `animals_metadata.xlsx` using any of the six matching strategies (see [Section 6.2](#62-matching-to-metadata)). The most reliable fix is to ensure the ID in your filenames exactly matches the `behavior_id` column in your spreadsheet. Run `behaviordata_schema_checker.py` to get a report of all mismatches before running the full pipeline.

**`No time column found`**
The CSV has no column whose normalized name starts with `time`. Check your AnyMaze export settings — the time column is usually called `Time (s)`.

**`No freezing column found`**
No column contains the substring `freez`. Check that AnyMaze exported a freezing state column and that it is not named something like `Immobility`.

**`No trials detected`**
The CS+/CS- detection columns were not found or never fire in the recording. Verify `CS_DETECTION_MODE` is appropriate for your export format, and check that your AnyMaze CSV contains the expected columns. Use `behaviordata_schema_checker.py` to diagnose which files are missing trial detection columns.

**`Missing per-day summary: .../Analysis/% time freezing/freezing_summary.csv`**
Pass 2 cannot find the summary written by Pass 1. This means either Pass 1 has not been run yet, or the `FREEZING_SUBFOLDER` / `PLATFORM_SUBFOLDER` settings were changed between runs. Re-run the full pipeline with consistent subfolder names.

**Figures appear but some panels are blank**
A blank panel means no data was found for that treatment group × day combination after filtering. This is expected if not all groups ran on all days. It can also happen if `CS_TRIAL_CAP` is set too low, removing all trials for some animals.

**`behavior_id values appear in more than one cohort's metadata`**
Duplicate behavior IDs across cohorts can cause rows to be silently merged during pivoting. Make animal IDs globally unique across cohorts (e.g. prefix each ID with a cohort number: `C1_01`, `C2_01`).

**Import error: `cannot import name 'run' from 'platform'`**
Python has a built-in module called `platform`. This pipeline uses `platform_analysis.py` to avoid that conflict. If you see this error, verify the file is named `platform_analysis.py` and that `runner.py` imports it as `from platform_analysis import run as run_platform`.

**Litter figures are empty**
`litter_id` must be present as a column in `animals_metadata.xlsx`. If it is absent or all values are blank, litter figures are skipped with an info message. Set `FREEZING_BY_LITTER = False` if you do not have litter data.

---

## 12. US-Locked Platform Analysis

This analysis computes % time on platform specifically during the US (shock) delivery window of each CS+ trial, rather than across the full CS+ period. This isolates the avoidance/escape response at the moment of shock.

### Mode A — Shocker active column (`USE_SHOCKER_COLUMN = True`)

The pipeline reads the `Shocker active` column directly from the AnyMaze CSV and detects contiguous blocks where the value is 1. Each block is clipped to within the boundaries of the nearest CS+ trial to exclude any stray pulses caused by timing imprecision in the recording hardware.

Use this mode when you have a reliable `Shocker active` column in your export and want the most accurate US timing.

### Mode B — Derived window (`USE_SHOCKER_COLUMN = False`)

The US window is derived as the final `US_DURATION_S` seconds of each detected CS+ trial. Trial boundaries come from the same tone-status or TTL detection used by all other analyses. This mode works for all sessions including yoked controls, where the shocker column may not reflect the actual shock schedule.

### Above-chance values

All heatmaps display **% platform time during US minus chance level**, not raw platform percentage. The chance level is set by `US_CHANCE_BASELINE_PCT` (default 16.4%, reflecting the chance-level occupancy for this paradigm). Animals that consistently outperform chance appear in warm colors; animals at or below chance appear in cool colors.

### Run report

Every time the US-locked analysis runs, it writes `us_locked_run_report.txt` into the day output folder. This report includes: the timestamp, all relevant runner settings, which animals were excluded and why, and per-subject notes on which columns were used for detection. Retain this file alongside your data for reproducibility.

### Multi-animal ID warning

In combined across-cohort figures, animals are labeled by `animal_id` only. If the same `animal_id` appears in more than one cohort within the same day/treatment panel, rows can be merged during pivoting and averaged silently. The pipeline prints a warning when this occurs. The recommended fix is to make animal IDs globally unique across cohorts before analysis.

---

## 13. Schema Checker

`behaviordata_schema_checker.py` is a standalone diagnostic tool. Run it before your first full pipeline run on a new dataset, or any time you add new data and want to verify compatibility.

### What it checks

- Whether each BehaviorData folder exists and contains `animals_metadata.xlsx`
- Whether the metadata file has the required `behavior_id` column and all recommended columns
- Whether `behavior_id` values in the metadata are unique and non-blank
- Whether session folders follow the expected `d##_...` naming convention
- Whether each CSV file contains a parseable date token and a behavior ID token in its name
- Whether each CSV contains a time column, a freezing column, and trial detection columns (either tone-status or TTL style)
- Whether each CSV's behavior ID token can be found in the metadata

### How to use it

Open `behaviordata_schema_checker.py` and set `BEHAVIORDATA_DIRS` to the same list of folders you plan to use in `runner.py`. Optionally set `REPORT_OUT` to a path where the full report should be saved as a text file. Run the file. A summary of errors and warnings is printed to the console, and suggested fixes are listed at the end.

The checker does not modify any files and does not run the pipeline — it only reads and reports.
