"""Tests for independent detector evaluation and threshold tuning infrastructure."""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from scripts.evaluate_detector_independent import (
    evaluate_detector_run,
    match_predictions_to_ground_truth,
)
from scripts.tune_detector_thresholds import tune_detector


def test_greedy_bipartite_matching_no_duplicate_predictions():
    """Verify 1-to-1 matching prevents a single prediction from matching multiple GTs or vice versa."""
    ground_truth = [
        {"start": 1.0, "end": 3.0, "trigger": "flashing"},
        {"start": 3.5, "end": 5.0, "trigger": "flashing"},
    ]

    # Two overlapping predictions targeting the first ground truth event
    predictions = [
        {"start": 1.1, "end": 2.9, "trigger": "flashing", "score": 0.95},
        {"start": 1.2, "end": 3.1, "trigger": "flashing", "score": 0.85},
    ]

    res = match_predictions_to_ground_truth(predictions, ground_truth, iou_threshold=0.3)
    assert res["true_positives"] == 1, "Only highest scoring prediction should match the first GT"
    assert res["false_positives"] == 1, "Second prediction must be counted as unmatched false positive"
    assert res["false_negatives"] == 1, "Second GT remains unmatched false negative"


def test_insufficient_validation_data_reported_for_unverified_natural_media():
    """Verify that detectors report INSUFFICIENT_VALIDATION_DATA when verified labels are absent."""
    res = evaluate_detector_run(
        detector_name="flashing",
        split_filter="train_dev",  # train_dev has only synthetic fixtures, 0 natural media
        dataset_type_filter="natural",
    )
    assert res["summary"]["natural"]["status"] == "INSUFFICIENT_VALIDATION_DATA"
    assert "No verified natural ground-truth events" in res["summary"]["natural"]["reason"]


def test_tuning_refuses_locked_test_split():
    """Verify that tuning against locked_test split raises an immediate error to prevent data leakage."""
    with pytest.raises(ValueError, match="CRITICAL SAFETY VIOLATION: Threshold tuning against 'locked_test'"):
        tune_detector(
            detector_name="flashing",
            split="locked_test",
        )
