from pathlib import Path
import importlib
import sys

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


def test_column_alias_ambiguity_raises():
    utils, _ = load_pipeline_modules()
    df = pd.DataFrame({"Freezing": [0], "Freezing state": [1]})
    cfg = {
        "column_match_mode": "strict",
        "column_aliases": {"freezing": ["Freezing", "Freezing state"]},
    }

    with pytest.raises(ValueError, match="Ambiguous column match"):
        utils.find_freeze_col(df, cfg)


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
        "source_behaviordata": ["BehaviorData"],
        "cohort_id": ["cohort-a"],
        "animal_id": ["mouse-1"],
        "trial_kind": ["CS+"],
        "cs_type": ["CS+_1"],
        "trial_index": [1],
        "Bin0": [1.25],
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
