[README.md](https://github.com/user-attachments/files/27246527/README.md)
# UNSI Lab Behavioral Pipelines

## Overview
Within this hub are folders containing python analysis files for specific behavioral assays, each with their own README.
<br><br>
## Example Outputs for 1-Port Platform Mediated Avoidance
### Cue-aligned nosepoke probability (trial-averaged)
In order to quantify cue-evoked behavior, trial-aligned nosepoke activity is averaged across trials within each animal and then across animals within a treatment group, allowing visualization of how behavior evolves relative to cue onset. Similar cue-aligned analyses are performed for additional behavioral measures, including time on platform.
<div align="center"><img width="1282" height="687" alt="{E14E7062-9A11-4481-B537-C0E793A28097}" src="https://github.com/user-attachments/assets/0ad7e2b4-7628-4383-ba64-8d2dc54b15b8" /></div> 

### Session-wide nosepoke activity (30 s binned histogram)
In order to visualize subject-specific reward seeking over time and capture potential changes in pursuit (e.g., satiation), nosepoke activity is plotted as a full-session histogram binned in 30-second intervals, where each bin reflects total time spent nosepoking. Additional analyses, such as total reward received across treatment groups per session, are also performed.
<div align="center"><img src="https://github.com/user-attachments/assets/fffde13b-5685-4ccd-b9c7-8013010f236f" width="48%"></div>

### Shock outcomes across trials
In order to summarize how subjects respond to and learn from aversive cues over time, trial-by-trial classification of avoidance behavior (Avoided, Escaped, Fully Shocked) is plotted across the session. Further plots and tables are produced to assess individual subject performance, latency to utilize the platform from cue onset, and more.
<div align="center"><img src="https://github.com/user-attachments/assets/2b7e8150-e692-41c6-8a19-af19b4e9a36b" width="48%"></div>

<hr>

### % Time Freezing (trial-aligned)
In order to quantify cue-evoked freezing behavior, % time freezing is computed within each trial window and averaged across trials within each animal and then across animals within a treatment group, allowing visualization of how cue-evoked freezing evolves across trials and days.
<div align="center"><img src="https://github.com/user-attachments/assets/02682ee2-3f4f-4f71-bce3-a4ffe9e818a0" width="48%"></div>

### Shock Outcome Classification (Evade / Escape / Endure)
In order to characterize behavioral responses to aversive stimuli, CS+ trials are classified as Evade, Escape, or Endure based on platform occupancy during the shock window, and summarized across trials and animals within each treatment group to reveal differences in avoidance strategy.
<div align="center"><img src="https://github.com/user-attachments/assets/a29ee84f-3df9-420e-b479-3ce63e29d663" width="48%"></div>

### US-locked Platform Time
In order to isolate behavior specifically during shock delivery, platform occupancy is computed within the US (shock) window for each trial, enabling assessment of avoidance performance independent of the full cue period and comparison across treatment groups.
<div align="center"><img src="https://github.com/user-attachments/assets/76e6580c-8801-448c-8289-d4505e209d82" width="48%"></div>

