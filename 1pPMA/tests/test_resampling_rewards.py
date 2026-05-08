from pathlib import Path
import importlib
import sys

import pandas as pd
import pytest


PIPELINE_DIR = Path(__file__).resolve().parents[1]


def load_pipeline_modules():
    for name in ["runner", "utils", "Rewards"]:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(PIPELINE_DIR))
    try:
        utils = importlib.import_module("utils")
        rewards = importlib.import_module("Rewards")
        return utils, rewards
    finally:
        sys.path.remove(str(PIPELINE_DIR))


def test_fractional_resampling_handles_nonnumeric_columns(tmp_path):
    utils, _ = load_pipeline_modules()
    csv_path = tmp_path / "01_123_A.csv"
    pd.DataFrame({
        "Time (s)": [0.0, 0.5, 1.0],
        "Feeder active": [1.0, 0.0, 0.5],
        "Notes": ["start", "middle", "end"],
    }).to_csv(csv_path, index=False)

    df = utils.load_behavior_fractional(csv_path)

    assert df.loc[0, "Feeder active"] == pytest.approx(0.5)
    assert df.loc[1, "Feeder active"] == pytest.approx(0.5)
    assert "Notes" in df.columns


def test_reward_duration_sums_fractional_seconds(tmp_path):
    _, rewards = load_pipeline_modules()
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "out"
    data_dir.mkdir()
    pd.DataFrame({
        "Time (s)": [0.0, 0.5, 1.0, 1.5],
        "Feeder active": [1.0, 0.0, 0.25, 0.75],
        "Notes": ["a", "b", "c", "d"],
    }).to_csv(data_dir / "01_123_A.csv", index=False)

    rewards.compute_reward_consumption(data_dir, out_dir)

    summary = pd.read_csv(out_dir / "reward_summary.csv")
    assert summary.loc[0, "Feeder_Active_s"] == pytest.approx(1.0)
    assert summary.loc[0, "Total_Reward_uL"] == pytest.approx(16.0)
