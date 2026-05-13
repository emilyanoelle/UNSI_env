# Fear Conditioning with Platform-Mediated Avoidance Protocol
**Credit: Eastman's schematic; adapted for ENV 2026 pipeline**

---

## Overview

A nine-day cue discrimination and platform-mediated avoidance paradigm using two distinct contexts (A and B) in standard fear-conditioning chambers (MED Associates) controlled by AnyMaze (Stoelting Co.). The paradigm assesses freezing, active avoidance (platform use), and shock-outcome classification (Evade / Escape / Endure) across acquisition, extinction, and test phases.

---

## Experimental Design Summary

| Day | Context | Phase | CS+ | CS- | Shock | Platform |
|-----|---------|-------|-----|-----|-------|----------|
| 1 | A | Habituation | 5× | 5× | No | No |
| 2–3 | B | Conditioning | 10×/day | 10×/day | Yes (CS+ only) | No |
| 4–5 | A | Extinction | 10×/day | 10×/day | No | No |
| 6 | A | Habituation + Platform | 10× | 10× | No | Yes |
| 7 | B | Test (shock + platform) | 10× | 10× | Yes (CS+ only) | Yes |
| 8 | A | Test (shock-free avoidance) | 10× | — | No | Yes |
| 9 | A | Final test | 10× | 10× | No | Yes |

---

## Animals

- Mice (strain, age, sex, and cohort info to be filled in per experiment)
- 3 days of handling prior to behavioral testing
- Housed under standard conditions; transport and testing occur under red-light conditions

---

## Apparatus

### Context A
- Metal insert walls with clear Plexiglas lid resting on top
- Vinegar odor (applied to a cotton pad beneath the chamber floor before each session)
- **Platform:** A Plexiglas piece placed over the shock grid floor (shock grid covers remain in place beneath the platform). Note: the use of a smooth Plexiglas cover *on top of* a shock grid versus a sleek Plexiglas floor (shock grid removed) appears to affect platform-mediated avoidance acquisition (observed ENV 2026). Maintain consistency within and across cohorts and document clearly which configuration is used.

### Context B
- Standard MED Associates shocker floor (grid floor configuration, no added odor)
- Platform present on Days 7–9 only

### Stimulus Parameters
- **Tone volume:** ~80–90 dB (measure and document with a sound level meter at the start of each cohort)
- **CS+ frequency:** 120 kHz
- **CS− frequency:** 4 kHz
- **Tone duration:** 30 s
- **Shock intensity:** 0.4 mA
- **Shock timing:** 2 s, co-terminating with the last 2 s of the CS+ (i.e., shock onset = CS+ onset + 28 s)
- **ITI:** 60 s (20 ITI periods per session on conditioning and test days; 10 on habituation days)
- **Baseline period:** 2 min before the first trial on every day

---

## Trial Sequence

### Day 1 — Fixed order
```
CS+ / CS− / CS+ / CS− / CS+ / CS− / CS+ / CS− / CS+ / CS−
```
*(5 CS+, 5 CS−, interleaved)*

### Days 2–9 — Pseudorandomized (same order each day)
```
+, −, −, +, −, +, +, −, +, −, −, +, −, +, +, −, +, −, −, +
```
*(10 CS+, 10 CS−)*

---

## Pre-Session Procedure (All Days)

1. **30 min before start:** Transport mice from the housing room to the behavioral testing room. Place cages on a cart or in a sound-attenuated holding shelf. Turn on red lights. Do not disturb mice during this acclimation period.
2. Wipe all chamber walls, floors, and platforms with the appropriate cleaning solution before each animal:
   - Context A: 1–3% acetic acid (vinegar solution)
   - Context B: 70% ethanol or equivalent odor-neutral cleaner
3. Apply fresh vinegar scent to the cotton pad beneath Context A chambers.
4. If a platform is used that session, ensure the Plexiglas cover is clean, dry, and seated flat over the shock grid before placing the animal.
5. Verify AnyMaze is recording the correct protocol file for the day and that all channel assignments (tone, shocker, tracking zones) are correct before beginning.

---

## Day-by-Day Procedures

### Day 1 — Habituation (Context A, no shock, no platform)
- Place mouse in Context A.
- 3 min baseline (no stimuli).
- 10 trials: 5 CS+ and 5 CS−, fixed order (see above), 30 s each, no shock.
- ~15 min total session time.

### Days 2–3 — Fear Conditioning (Context B, shock, no platform)
- Place mouse in Context B.
- 3 min baseline.
- 20 trials pseudorandomized: 10 CS+ (each co-terminating with 0.4 mA, 2 s footshock) and 10 CS− (no shock).
- ~30 min total session time.
- Run each animal once per day on both days.

### Days 4–5 — Extinction (Context A, no shock, no platform)
- Place mouse in Context A (no platform).
- 3 min baseline.
- 20 trials pseudorandomized: 10 CS+ and 10 CS−, no shock.
- ~30 min total session time.
- Run each animal once per day on both days.

### Day 6 — Platform Habituation (Context A, no shock, platform present)
- Place mouse in Context A with platform.
- 3 min baseline.
- 20 trials pseudorandomized: 10 CS+ and 10 CS−, no shock.
- ~30 min total session time.
- Purpose: habituate animals to the platform in the absence of shock before testing.

### Day 7 — Platform Test in Context B (Context B, shock, platform present)
- Place mouse in Context B with platform.
- 3 min baseline.
- 20 trials pseudorandomized: 10 CS+ (with 0.4 mA shock, co-terminating) and 10 CS−.
- ~30 min total session time.
- Primary measure: platform-mediated avoidance during CS+ (Evade / Escape / Endure classification).

### Day 8 — Shock-Free Avoidance Test (Context A, no shock, platform present)
- Place mouse in Context A with platform.
- 3 min baseline.
- 10 CS+ trials only, no shock, pseudorandomized subset.
- ~15–20 min total session time.
- Purpose: assess whether avoidance expression persists without reinforcement (shock-free active avoidance).

### Day 9 — Final Test (Context A, no shock, platform present)
- Place mouse in Context A with platform.
- 3 min baseline.
- 20 trials pseudorandomized: 10 CS+ and 10 CS−, no shock.
- ~30 min total session time.

---

## AnyMaze Export — Required Data Columns

The following columns must be exported from AnyMaze for compatibility with the analysis pipeline. Exact column names are listed as they should appear in the CSV (the pipeline normalizes capitalization and spacing, but using these names exactly prevents matching errors):

| Column Name | Required For | Notes |
|---|---|---|
| `Time (s)` | All analyses | Continuous time vector |
| `Freezing` | Freezing analysis | Binary (1 = freezing, 0 = not) |
| `In platform` | Platform, EEE, US-locked | Binary (1 = on platform, 0 = off) |
| `CS+ tone status` | Trial detection | Continuous 1 during CS+, 0 otherwise — preferred over TTL pulses |
| `CS- tone status` | Trial detection | Continuous 1 during CS−, 0 otherwise |
| `Shocker active` | EEE, US-locked | Binary shock delivery column — required if `USE_SHOCKER_COLUMN = True` |

**Optional but recommended:**
| Column Name | Notes |
|---|---|
| `CS+ ON activated` / `CS+ OFF activated` | TTL-based trial detection fallback |
| `CS- ON activated` / `CS- OFF activated` | TTL-based trial detection fallback |
| `US ON activated` / `US OFF activated` | Used if `USE_SHOCKER_COLUMN = False` |

> **Note:** If your AnyMaze export uses `CS plus tone status` instead of `CS+ tone status`, both are accepted after normalization. However, using the exact names above eliminates the need for alias configuration. Confirm column names appear in exported CSVs before beginning a new cohort and add any variants to `COLUMN_ALIASES` in `runner.py`.

---

## File Naming and Folder Structure

Name exported CSV files using the convention:
```
MM-DD-YY_BehaviorID_index_fixed.csv
```
Example: `03-05-26_9C-1_1_fixed.csv`

Organize session folders within each BehaviorData cohort folder as follows:
```
BehaviorData/
├── animals_metadata.xlsx
├── d01_contextA_habituation/
├── d02_contextB_conditioning/
├── d03_contextB_conditioning/
├── d04_contextA_extinction/
├── d05_contextA_extinction/
├── d06_contextA_platformhabituation/
├── d07_contextB_test/
├── d08_contextA_test/
└── d09_contextA_test/
```

---

## Metadata Spreadsheet (`animals_metadata.xlsx`)

One spreadsheet per BehaviorData folder. Required and recommended columns:

| Column | Required | Notes |
|---|---|---|
| `behavior_id` | Yes | Must match the ID token in CSV filenames exactly |
| `animal_id` | Recommended | Internal animal identifier |
| `treatment_group` | Recommended | Must match `TREATMENT_ALIASES` in `runner.py` |
| `sex` | Recommended | `M` or `F` |
| `litter_id` | If using litter figures | Required for `FREEZING_BY_LITTER = True` |
| `cohort_id` | Recommended | Used to label per-cohort outputs; defaults to folder name if absent |

---

## Analysis Pipeline Configuration Notes

Before running the pipeline for this paradigm, set the following in `runner.py`:

```python
CS_DETECTION_MODE      = "tone_status"   # preferred for this setup
USE_SHOCKER_COLUMN     = True            # uses Shocker active column for EEE and US-locked
US_DURATION_S          = 2.0             # shock window = last 2 s of CS+
US_CHANCE_BASELINE_PCT = 16.4           # chance-level platform occupancy for this paradigm

RUN_SANITY_CHECK       = True
RUN_FREEZING           = True
RUN_PLATFORM           = True
RUN_EEE                = True
RUN_US_LOCKED          = True            # set False if Shocker active column absent

CS_TRIAL_CAP           = 10
ITI_TRIAL_CAP          = 20
EEE_TRIAL_CAP          = 10

PRISM_EXPORT           = True
```

Run `behaviordata_schema_checker.py` before the first full pipeline run on any new cohort to verify folder structure, metadata formatting, and CSV column compatibility.

---

## Notes and Caveats

- **Platform configuration matters.** The use of a Plexiglas sheet placed *over* the shock grid (shock grid covers, ENV 2026) differs from a configuration where the shock grid is removed and the animal walks on the Plexiglas chamber floor directly. Avoidance acquisition differences have been observed between these setups. Document platform configuration explicitly for every cohort and do not mix configurations within a study.
- **Tone frequencies** should be filled in before distributing this protocol. Confirm that CS+ and CS− are perceptually discriminable and that the chosen frequencies do not overlap with ambient noise in the testing room.
- **Sound level:** Measure and record actual dB output at the start of each cohort using a sound level meter positioned at cage-floor height inside the chamber.
- **Inter-animal cleaning:** Clean chambers between every animal. Context identity depends on consistent olfactory and tactile cues.
- **Pseudorandomization:** The same trial sequence is used each day (Days 2–9). Do not alter the sequence mid-cohort.
- **Cohort ID uniqueness:** Ensure `behavior_id` values are unique across cohorts if multiple cohorts are analyzed together. Use a cohort prefix if needed (e.g., `C1_9C-1`).
