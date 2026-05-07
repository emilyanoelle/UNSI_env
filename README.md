# UNSI Lab Behavioral Analysis Pipelines

This repository contains Python pipelines for turning raw behavioral exports into organized summaries, Prism-ready tables, run reports, and polished vector-based figures. The workflows were built around real lab data, where file names, metadata, column names, cohorts, and session structures are not always perfectly tidy.

The two main pipelines are:

- **1-Port Platform Mediated Avoidance**
- **Fear Conditioning with Platform**

Each pipeline has its own detailed README and a `runner.py` configuration file. Most users only need to edit the configuration section at the top of `runner.py`, then run the script.

## Why These Pipelines Are Useful

These pipelines are designed to make behavioral analysis reproducible, inspectable, and friendly to use:

- Read raw behavior exports directly from organized session folders
- Match subjects to metadata spreadsheets across sessions and cohorts
- Use configurable aliases to handle column-name variation across exports
- Improve behavior ID parsing from messy file names using metadata-aware matching
- Normalize treatment groups, sex labels, cohort labels, and trial types
- Generate publication-friendly **SVG vector figures**
- Export Prism-ready Excel workbooks for downstream statistics
- Write run reports documenting what was analyzed, skipped, and why

The aliasing and parsing pieces are especially valuable outside academia too: they make the code more resilient to inconsistent real-world data inputs, while still failing loudly when ambiguity could produce the wrong result.

## Getting Started

Before running a pipeline, you need:

- Python 3.7 or higher
- A way to run Python scripts, ideally Spyder or VS Code
- The required Python packages installed
- Your data organized according to the pipeline README
- A metadata spreadsheet linking behavior IDs to animal information
- A local copy of this repository, ideally connected to GitHub through your Python IDE

Start by reading the README for the pipeline you want to use:

- [`1pPMA/README.md`](1pPMA/README.md)
- [`FearConditioning w Platform/readme.md`](FearConditioning%20w%20Platform/readme.md)

Basic workflow:

1. Clone or download this repository.
2. Connect your Python IDE to the repository if possible.
3. Make your own branch before changing configuration or analysis code.
4. Choose the pipeline folder for your assay.
5. Read that pipeline's README.
6. Organize your raw data and metadata spreadsheet as described.
7. Open `runner.py` in Spyder, VS Code, or another Python IDE.
8. Edit the configuration section at the top of `runner.py`.
9. Run the script.
10. Review the generated figures, CSVs, Excel tables, and run report.

If you are new to GitHub or branches, these are useful starting points:

- [GitHub Get Started Guide](https://docs.github.com/en/get-started)
- [Learn Git Branching](https://learngitbranching.js.org/)
- [Atlassian guide to Git branches](https://www.atlassian.com/git/tutorials/using-branches)

The recommended workflow is to keep the repository connected to GitHub and make your own branch for edits. You can also copy the pipeline files into your own local folders, but that is not recommended because it makes updates harder to track, makes bug fixes harder to pull in, and increases the chance that different users end up running slightly different versions of the analysis.

## Engineering Highlights

These scripts are more than one-off analysis notebooks. They include several production-style data workflow features:

- **Config-driven execution:** users change settings in `runner.py`, not analysis internals.
- **Column aliasing:** users can define expected variations like `In platform`, `Inside platform`, or `Shocker active`.
- **Strict/fallback matching:** strict mode avoids heuristic guessing; fallback mode supports older exports.
- **Ambiguity protection:** ambiguous column matches raise clear errors instead of silently choosing the wrong signal.
- **Metadata-aware ID parsing:** messy file names can still be matched to known subjects, with longest valid ID matching to avoid partial-subject mistakes.
- **Run reports:** Excel reports capture settings, included/excluded subjects, columns used, and warnings.
- **Vector output:** figures are saved as SVGs, which stay crisp for posters, manuscripts, slide decks, and portfolios.

---

## Example Outputs for 1-Port Platform Mediated Avoidance

### Cue-aligned nosepoke probability

Trial-aligned nosepoke activity is averaged within each animal and then across treatment groups, making it easier to visualize cue-evoked behavior over time. Similar cue-aligned analyses are produced for other behavioral measures, including time on platform.

<div align="center">
  <img width="1282" height="687" alt="Cue-aligned nosepoke probability plot" src="https://github.com/user-attachments/assets/0ad7e2b4-7628-4383-ba64-8d2dc54b15b8" />
</div>

### Session-wide nosepoke activity

Full-session histograms show subject-specific reward seeking over time using 30-second bins. These views help reveal session dynamics such as pursuit, satiation, and individual variability.

<div align="center">
  <img src="https://github.com/user-attachments/assets/fffde13b-5685-4ccd-b9c7-8013010f236f" width="55%" alt="Session-wide nosepoke histogram">
</div>

### Shock outcomes across trials

Trial-by-trial avoidance classifications summarize how subjects respond to aversive cues over the session. Additional outputs quantify latency, individual performance, and cross-session trends.

<div align="center">
  <img src="https://github.com/user-attachments/assets/2b7e8150-e692-41c6-8a19-af19b4e9a36b" width="55%" alt="Shock outcome classification plot">
</div>

---

## Example Outputs for Fear Conditioning with Platform

### Percent time freezing

Percent time freezing is computed within each trial window, then summarized across animals and treatment groups. The tiled SVG output makes it easy to compare cue-evoked freezing across trials and days.

<div align="center">
  <img src="https://github.com/user-attachments/assets/ae88c4fc-d255-4aa2-a0f9-9637c6ac0110" width="90%" alt="Percent time freezing tiled SVG plot">
</div>

### Shock outcome classification

CS+ trials are classified as **Evade**, **Escape**, or **Endure** based on platform occupancy during the shock window. This makes avoidance strategy visible at both the individual and treatment-group level.

<div align="center">
  <img src="https://github.com/user-attachments/assets/a29ee84f-3df9-420e-b479-3ce63e29d663" width="75%" alt="Evade escape endure stacked bar plot">
</div>

### US-locked platform time

Platform occupancy is computed specifically during the US/shock window, isolating avoidance performance at the moment of shock delivery rather than across the entire cue period.

<div align="center">
  <img src="https://github.com/user-attachments/assets/76e6580c-8801-448c-8289-d4505e209d82" width="55%" alt="US-locked platform time heatmap">
</div>

## Notes

The figures shown here are representative outputs. Most generated figures are saved as SVG files so they can be edited or resized without losing quality.
