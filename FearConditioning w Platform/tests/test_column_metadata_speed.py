from pathlib import Path
import importlib
import sys

import matplotlib.colors as mcolors
import pandas as pd
import pytest


PIPELINE_DIR = Path(__file__).resolve().parents[1]


def load_pipeline_modules():
    for name in ["utils", "run_report", "speed_analysis"]:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(PIPELINE_DIR))
    try:
        utils = importlib.import_module("utils")
        speed_analysis = importlib.import_module("speed_analysis")
        return utils, speed_analysis
    finally:
        sys.path.remove(str(PIPELINE_DIR))


def load_pipeline_module(name):
    for module_name in ["utils", "run_report", name]:
        sys.modules.pop(module_name, None)
    sys.path.insert(0, str(PIPELINE_DIR))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(PIPELINE_DIR))


def test_column_alias_ambiguity_raises():
    utils, _ = load_pipeline_modules()
    df = pd.DataFrame({"Freezing": [0], "Freezing state": [1]})
    cfg = {
        "column_match_mode": "strict",
        "column_aliases": {"freezing": ["Freezing", "Freezing state"]},
    }

    with pytest.raises(ValueError, match="Ambiguous column match"):
        utils.find_freeze_col(df, cfg)


def test_load_csv_usecols_matches_normalized_headers(tmp_path):
    utils, _ = load_pipeline_modules()
    path = tmp_path / "sample.csv"
    pd.DataFrame({
        "Time (s)": [0.0, 1.0],
        "Freezing state": [0, 1],
        "Unused signal": [99, 100],
    }).to_csv(path, index=False)

    header = utils.load_csv_header(path)
    df = utils.load_csv(path, usecols=["time_s", "freezing_state"])

    assert header.columns.tolist() == ["time_s", "freezing_state", "unused_signal"]
    assert df.columns.tolist() == ["time_s", "freezing_state"]
    assert df["freezing_state"].tolist() == [0, 1]


def test_metadata_filename_parser_prefers_longest_valid_subject_id():
    utils, _ = load_pipeline_modules()
    meta = pd.DataFrame({"behavior_id": ["6069-c", "6069-cobalt"]})
    path = Path("03-10-25_asdf-_sdf6069-cobalt_ajdf.csv")

    test_date, behavior_id = utils.parse_filename_bits(path, meta)

    assert test_date == "03-10-25"
    assert behavior_id == "6069-cobalt"


def test_metadata_lookup_raises_on_ambiguous_rows():
    utils, _ = load_pipeline_modules()
    meta = pd.DataFrame({
        "behavior_id": ["6069-b", "6069-b"],
        "animal_id": ["A", "B"],
    })

    with pytest.raises(ValueError, match="Ambiguous metadata match"):
        utils.find_metadata_for_behavior(meta, "6069-b")


def test_load_metadata_fills_blank_cohort_id_from_earliest_session_date(tmp_path):
    utils, _ = load_pipeline_modules()
    bd = tmp_path / "BehaviorData"
    bd.mkdir()
    day_dir = bd / "d07_contextB_conditioning"
    day_dir.mkdir()
    (day_dir / "03-10-25_mouse-1.csv").touch()
    (day_dir / "02-28-25_mouse-2.csv").touch()
    pd.DataFrame({
        "behavior_id": ["mouse-1", "mouse-2"],
        "cohort_id": [None, "explicit-cohort"],
    }).to_excel(bd / "animals_metadata.xlsx", index=False)

    meta = utils.load_metadata(bd)

    assert meta["cohort_id"].tolist() == ["cohort_022825", "explicit-cohort"]


def test_load_metadata_fills_all_blank_cohort_id_from_earliest_session_date(tmp_path):
    utils, _ = load_pipeline_modules()
    bd = tmp_path / "data"
    bd.mkdir()
    day_dir = bd / "d07_contextB_conditioning"
    day_dir.mkdir()
    (day_dir / "03-10-25_mouse-1.csv").touch()
    (day_dir / "03-12-25_mouse-2.csv").touch()
    pd.DataFrame({
        "behavior_id": ["mouse-1", "mouse-2"],
        "cohort_id": [None, None],
    }).to_excel(bd / "animals_metadata.xlsx", index=False)

    meta = utils.load_metadata(bd)

    assert meta["cohort_id"].tolist() == ["cohort_031025", "cohort_031025"]


def test_metadata_for_csv_reports_missing_metadata():
    utils, _ = load_pipeline_modules()
    meta = pd.DataFrame({"behavior_id": ["present-subject"]})
    path = Path("03-10-25_missing-subject.csv")

    test_date, behavior_id, row, reason = utils.metadata_for_csv(path, meta)

    assert test_date == "03-10-25"
    assert behavior_id == "missing-subject"
    assert row is None
    assert reason == "behavior_id 'missing-subject' not found in metadata"


@pytest.mark.parametrize("module_name", [
    "freezing_analysis",
    "platform_analysis",
    "eee_analysis",
    "us_locked_analysis",
    "event_raster_analysis",
    "speed_analysis",
])
def test_analysis_modules_skip_csvs_missing_metadata(module_name, tmp_path):
    module = load_pipeline_module(module_name)
    csv_path = tmp_path / "03-10-25_missing-subject.csv"
    day_dir = tmp_path / "d01_contextA_test"
    meta = pd.DataFrame({"behavior_id": ["present-subject"]})

    if module_name == "eee_analysis":
        result = module._process_file(csv_path, meta, day_dir, tmp_path, {})
    else:
        result = module._process_file(csv_path, meta, day_dir, {})

    report_data = result[-1]
    assert report_data["exclusions"][csv_path.name] == \
        "behavior_id 'missing-subject' not found in metadata"

    if module_name == "freezing_analysis":
        assert result[0] == []
        assert result[1] == []
    elif module_name == "event_raster_analysis":
        assert result[0] is None
        assert result[1] == []
    else:
        assert result[0] == []


def test_eee_run_does_not_write_per_day_outputs(tmp_path, monkeypatch):
    eee_analysis = load_pipeline_module("eee_analysis")
    behaviordata = tmp_path / "BehaviorData"
    day_dir = behaviordata / "d01_contextA_test"
    day_dir.mkdir(parents=True)
    df = pd.DataFrame({
        "cohort_id": ["cohort-a"],
        "_day_dir": [str(day_dir)],
        "outcome": ["evade"],
    })
    calls = []

    monkeypatch.setattr(
        eee_analysis,
        "_collect_all_parallel",
        lambda cfg, report=None: df.copy(),
    )
    monkeypatch.setattr(
        eee_analysis,
        "_find_behaviordata_for_cohort",
        lambda cohort_id, behaviordata_dirs: behaviordata,
    )
    monkeypatch.setattr(
        eee_analysis,
        "_write_outputs",
        lambda out_df, out_dir, cfg, fname_tag: calls.append((Path(out_dir), fname_tag)),
    )
    cfg = {
        "analysis_out": tmp_path / "combined",
        "eee_subfolder": "Shock outcomes (evade-escape-endure)",
        "behaviordata_dirs": [behaviordata],
    }

    eee_analysis.run(cfg)

    assert calls == [
        (tmp_path / "combined" / "Shock outcomes (evade-escape-endure)", "eee"),
        (
            behaviordata / "Analysis" / "Shock outcomes (evade-escape-endure)",
            "eee_cohort-a",
        ),
    ]


def test_eee_stacked_outputs_use_tiled_filenames(tmp_path, monkeypatch):
    eee_analysis = load_pipeline_module("eee_analysis")
    saved = []

    def fake_save_fig(fig, out_path):
        saved.append(out_path.name)
        eee_analysis.plt.close(fig)

    monkeypatch.setattr(eee_analysis.utils, "save_fig", fake_save_fig)
    df = pd.DataFrame({
        "_day_folder": ["d01_contextA_test", "d02_contextB_test"],
        "day": ["d01", "d02"],
        "context": ["contextA", "contextB"],
        "session_label": ["test", "test"],
        "treatment_group": ["ctrl", "ctrl"],
        "sex": ["F", "F"],
        "cohort_id": ["cohort-a", "cohort-a"],
        "animal_id": ["mouse-1", "mouse-1"],
        "outcome": ["evade", "escape"],
    })
    cfg = {
        "canonical_groups": ["ctrl"],
        "eee_by_sex": True,
        "prism_export": False,
    }

    eee_analysis._write_outputs(df, tmp_path, cfg, "eee")

    assert saved == [
        "eee_stacked_by_treatment_tiled.svg",
        "eee_stacked_by_sex_treatment_tiled.svg",
    ]


def test_eee_stacked_plot_labels_omit_n_counts():
    eee_analysis = load_pipeline_module("eee_analysis")
    fig, ax = eee_analysis.plt.subplots()
    group_means = {
        "ctrl": {"evade_pct": 100.0, "escape_pct": 0.0, "endure_pct": 0.0},
        "ELS": {"evade_pct": 0.0, "escape_pct": 100.0, "endure_pct": 0.0},
    }

    eee_analysis._stacked_bar_panel(
        ax,
        group_means,
        ["ctrl", "ELS"],
        ["ctrl\nn=4", "ELS\nN = 3"],
    )

    try:
        labels = [tick.get_text() for tick in ax.get_xticklabels()]
        assert labels == ["ctrl", "ELS"]
    finally:
        eee_analysis.plt.close(fig)


def test_trial_detection_uses_configured_aliases():
    utils, _ = load_pipeline_modules()
    df = pd.DataFrame({
        "Time (s)": [0, 1, 2, 3, 4, 5],
        "CS+ ON activated": [0, 1, 0, 0, 0, 0],
        "CS+ OFF activated": [0, 0, 0, 1, 0, 0],
        "CS- ON activated": [0, 0, 0, 0, 1, 0],
        "CS- OFF activated": [0, 0, 0, 0, 0, 1],
    })
    cfg = {
        "column_match_mode": "strict",
        "column_aliases": {
            "csplus_on": ["CS+ ON activated"],
            "csplus_off": ["CS+ OFF activated"],
            "csminus_on": ["CS- ON activated"],
            "csminus_off": ["CS- OFF activated"],
        },
        "cs_detection_mode": "ttl",
    }

    trials, _, source = utils.detect_trials(df, "Time (s)", cfg)

    assert source.startswith("TTL")
    assert [(trial["type"], trial["start"], trial["end"]) for trial in trials] == [
        ("CS+", 1.0, 3.0),
        ("CS-", 4.0, 5.0),
    ]


def test_speed_downsample_handles_nonnumeric_columns():
    _, speed_analysis = load_pipeline_modules()
    df = pd.DataFrame({
        "time": [0.00, 0.05, 0.11],
        "speed": [1.0, 3.0, 5.0],
        "notes": ["a", "b", "c"],
    })

    downsampled = speed_analysis._downsample(df, "time")

    assert downsampled.loc[0, "speed"] == pytest.approx(2.0)
    assert downsampled.loc[1, "speed"] == pytest.approx(5.0)
    assert "notes" in downsampled.columns


def test_speed_source_columns_keep_only_speed_and_trial_detection_columns():
    _, speed_analysis = load_pipeline_modules()
    df = pd.DataFrame({
        "time": [0],
        "speed": [1],
        "freezing": [0],
        "in_platform": [0],
        "csplus_tone_status": [0],
        "csminus_tone_status": [0],
    })
    cfg = {
        "column_match_mode": "strict",
        "column_aliases": {
            "csplus_tone_status": ["csplus_tone_status"],
            "csminus_tone_status": ["csminus_tone_status"],
        },
        "cs_detection_mode": "tone_status",
    }

    selected = speed_analysis._speed_source_columns(df, "time", "speed", cfg)

    assert selected == ["time", "speed", "csplus_tone_status", "csminus_tone_status"]


def test_pass1_distance_sem_fill_matches_line_color(tmp_path, monkeypatch):
    _, speed_analysis = load_pipeline_modules()
    captured = {}

    def fake_save_fig(fig, out_path):
        captured["fig"] = fig

    monkeypatch.setattr(speed_analysis.utils, "save_fig", fake_save_fig)
    rows = []
    for treatment, base in [("group-a", 1.0), ("group-b", 2.0)]:
        for animal_ix in range(2):
            rows.append({
                "animal_id": f"{treatment}-{animal_ix}",
                "treatment_group": treatment,
                "trial_kind": "CS+",
                "cs_type": "CS+_1",
                "start_time_s": 10.0,
                "distance_m": base + animal_ix * 0.1,
            })
    cfg = {
        "canonical_groups": ["group-a", "group-b"],
        "treatment_colors": {},
    }

    speed_analysis.make_trial_distance_figure(
        pd.DataFrame(rows), cfg, tmp_path / "distance_by_trial.svg")

    fig = captured["fig"]
    try:
        ax = fig.axes[0]
        line_colors = [mcolors.to_hex(line.get_color()) for line in ax.lines[:2]]
        fill_colors = [
            mcolors.to_hex(collection.get_facecolor()[0])
            for collection in ax.collections[:2]
        ]
        assert fill_colors == line_colors
    finally:
        speed_analysis.plt.close(fig)


def test_total_freezing_bars_use_configured_treatment_colors(tmp_path, monkeypatch):
    freezing_analysis = load_pipeline_module("freezing_analysis")
    captured = {}

    def fake_save_fig(fig, out_path):
        captured["fig"] = fig
        captured["out_path"] = out_path

    monkeypatch.setattr(freezing_analysis.utils, "save_fig", fake_save_fig)
    df = pd.DataFrame([
        {
            "_day_folder": "d01_contextA_habituation",
            "day": "d01",
            "context": "contextA",
            "session_label": "habituation",
            "animal_id": "mouse-1",
            "behavior_id": "mouse-1",
            "treatment_group": "ctrl",
            "sex": "F",
            "litter_id": "litter-1",
            "cohort_id": "cohort-a",
            "_trial_type": condition,
            "freeze_pct": 25.0,
        }
        for condition in ("Pre-trial", "CS+", "CS-")
    ])
    cfg = {
        "canonical_groups": ["ctrl"],
        "treatment_lookup": {"control": "ctrl", "ctrl": "ctrl"},
        "treatment_colors": {"Control": "#123456"},
    }

    freezing_analysis._total_freezing_bar_plots(df, tmp_path, cfg, "freezing_test")

    fig = captured["fig"]
    try:
        assert captured["out_path"].name == "freezing_test_total_time_freezing_bars_tiled.svg"
        assert mcolors.to_hex(fig.axes[0].patches[0].get_facecolor()) == "#123456"
    finally:
        freezing_analysis.plt.close(fig)


@pytest.mark.parametrize("module_name,value_col,expected_name", [
    ("freezing_analysis", "freeze_pct", "freezing_test_groupmeans_tiled.svg"),
    ("platform_analysis", "platform_pct", "platform_test_groupmeans_tiled.svg"),
])
def test_groupmeans_tiled_combines_trial_types_into_one_file(
        module_name, value_col, expected_name, tmp_path, monkeypatch):
    module = load_pipeline_module(module_name)
    captured = []

    def fake_save_fig(fig, out_path):
        captured.append((fig, out_path))

    monkeypatch.setattr(module.utils, "save_fig", fake_save_fig)
    rows = []
    for day in ["d01_contextA_test", "d02_contextB_test"]:
        for trial_type in ["CS+", "CS-", "ITI"]:
            for trial_index in [1, 2]:
                rows.append({
                    "_day_folder": day,
                    "_trial_type": trial_type,
                    "treatment_group": "ctrl",
                    "trial_index": trial_index,
                    value_col: 20.0 + trial_index,
                })
    cfg = {
        "canonical_groups": ["ctrl"],
        "treatment_colors": {"ctrl": "#123456"},
    }

    module._tiled_group_means(
        pd.DataFrame(rows), value_col, "Value", "Test Plot",
        tmp_path, cfg, expected_name.replace("_groupmeans_tiled.svg", ""),
        ylim=(0, 100),
    )

    assert len(captured) == 1
    fig, out_path = captured[0]
    try:
        assert out_path.name == expected_name
        assert len(fig.axes) == 6
    finally:
        module.plt.close(fig)


def test_speed_trial_onsets_are_detected_on_downsampled_data():
    _, speed_analysis = load_pipeline_modules()
    df_raw = pd.DataFrame({
        "time": [0.00, 0.08, 0.11, 0.30],
        "speed": [1.0, 2.0, 3.0, 4.0],
        "csplus_on_activated": [0, 0, 1, 0],
        "csplus_off_activated": [0, 0, 0, 1],
        "csminus_on_activated": [0, 0, 0, 0],
        "csminus_off_activated": [0, 0, 0, 0],
    })
    cfg = {
        "column_match_mode": "fallback",
        "column_aliases": {},
        "cs_detection_mode": "ttl",
    }
    df_ds = speed_analysis._downsample(df_raw, "time")

    cs_plus_idx, cs_minus_idx, iti_idx = speed_analysis._find_trial_onsets(
        df_ds, "time", cfg)

    assert cs_plus_idx == [1]
    assert cs_minus_idx == []
    assert iti_idx == []
    assert df_ds.loc[cs_plus_idx[0], "time"] == pytest.approx(0.1)


def test_speed_dataframe_keeps_identifiers_before_bin_columns():
    _, speed_analysis = load_pipeline_modules()
    rows = [{
        "Bin0": 1.0,
        "trial_kind": "CS+",
        "cs_type": "CS+_1",
        "trial_index": 1,
        "source_behaviordata": "BehaviorData",
        "cohort_id": "cohort-a",
        "animal_id": "mouse-1",
    }]
    cfg = {"speed_pre_bins": 0, "speed_post_bins": 0}

    df = speed_analysis._speed_dataframe(rows, cfg)

    assert df.columns.tolist() == [
        "source_behaviordata",
        "cohort_id",
        "animal_id",
        "trial_kind",
        "cs_type",
        "trial_index",
        "Bin0",
    ]


def test_write_speed_parquet_writes_single_combined_table(tmp_path):
    _, speed_analysis = load_pipeline_modules()
    df = pd.DataFrame({
        "source_behaviordata": ["BehaviorData", "BehaviorData"],
        "cohort_id": ["cohort-a", "cohort-a"],
        "animal_id": ["mouse-1", 102],
        "trial_kind": ["CS+", "CS-"],
        "cs_type": ["CS+_1", "CS-_1"],
        "trial_index": [1, 1],
        "Bin0": [1.25, 2.5],
    })
    cfg = {
        "analysis_out": tmp_path,
        "speed_subfolder": "speed",
    }

    speed_analysis._write_speed_parquet(df, cfg)

    out_path = tmp_path / "speed" / "speed_trial_windows.parquet"
    assert out_path.exists()
    saved = pd.read_parquet(out_path)
    assert saved.loc[0, "trial_kind"] == "CS+"
    assert saved.loc[0, "Bin0"] == pytest.approx(1.25)
    assert saved.loc[1, "animal_id"] == "102"
    assert saved.loc[1, "Bin0"] == pytest.approx(2.5)


def test_shock_avoidance_keeps_subjects_with_zero_avoided(tmp_path):
    us_locked_analysis = load_pipeline_module("us_locked_analysis")
    df = pd.DataFrame({
        "treatment_group": ["cre-wt", "cre-wt", "cre-wt", "pv ko"],
        "cohort_id": ["cohort-a", "cohort-a", "cohort-a", "cohort-a"],
        "animal_id": ["mouse-1", "mouse-1", "mouse-2", "mouse-3"],
        "_day_folder": ["d07_contextB_conditioning"] * 4,
        "us_number": [1, 2, 1, 1],
        "platform_pct": [100.0, 0.0, 0.0, 100.0],
    })

    us_locked_analysis._shock_avoidance(df, tmp_path)

    saved = pd.read_csv(tmp_path / "us_locked_shock_avoidance.csv")
    saved = saved.sort_values("animal_id").reset_index(drop=True)
    assert saved["animal_id"].tolist() == ["mouse-1", "mouse-2", "mouse-3"]
    assert saved["treatment_group"].tolist() == ["cre-wt", "cre-wt", "pv ko"]
    assert saved["shocks_avoided"].tolist() == [1, 0, 1]


def test_statistics_prepare_frame_uses_cohort_safe_subject_ids():
    statistics_utils = load_pipeline_module("statistics_utils")
    df = pd.DataFrame({
        "cohort_id": ["cohort-a", "cohort-b", "cohort-a", "cohort-b", "cohort-a", "cohort-x"],
        "behavior_id": ["mouse-1", "mouse-1", "mouse-1", "mouse-1", "mouse-2", "ghost-1"],
        "animal_id": ["animal-1", "animal-1", "animal-1", "animal-1", "animal-2", "ghost-1"],
        "treatment_group": ["ctrl", "ctrl", "ctrl", "ctrl", "ELS", "KO"],
        "_day_folder": ["d01_contextA_test"] * 6,
        "trial_type": ["CS+", "CS+", "CS+", "CS+", "Pre-trial", "CS+"],
        "trial_index": [1, 1, 2, 2, 1, 1],
        "freeze_pct": [10.0, 11.0, 20.0, 21.0, 5.0, 99.0],
    })

    prepared = statistics_utils._prepare_measure_frame(
        df, statistics_utils.MEASURES[0], {
            "cs_trial_cap": 10,
            "iti_trial_cap": 20,
            "stats_include_treatments": ["ctrl", "ELS"],
        })

    assert set(prepared["trial_type"]) == {"CS+"}
    assert set(prepared["subject_id"]) == {"cohort-a|mouse-1", "cohort-b|mouse-1"}
    assert prepared["time"].tolist() == ["1", "1", "2", "2"]


def test_statistics_backend_missing_rscript_writes_error_log(tmp_path):
    statistics_utils = load_pipeline_module("statistics_utils")
    input_df = pd.DataFrame({
        "measure": ["freezing"],
        "subject_id": ["cohort-a|mouse-1"],
        "treatment_group": ["ctrl"],
        "cohort_id": ["cohort-a"],
        "session": ["d01_contextA_test"],
        "trial_type": ["CS+"],
        "time": ["1"],
        "time_numeric": [1.0],
        "value": [10.0],
    })
    missing_rscript = tmp_path / "definitely_missing_Rscript.exe"

    results_path, log_path = statistics_utils._run_r_backend(
        input_df,
        tmp_path,
        "freezing_statistics",
        {
            "stats_rscript_path": str(missing_rscript),
            "stats_use_greenhouse_geisser": True,
        },
        "combined",
        "collapsed",
        "freezing",
    )

    assert results_path.exists()
    log = pd.read_csv(log_path)
    assert log.loc[0, "status"] == "error"
    assert "Rscript command not found" in log.loc[0, "message"]
