"""Unit tests for the Detection Evaluation Harness (Person B).

Tests cover:
- Temporal IoU (tIoU) edge cases, boundaries, and validation
- Greedy bipartite one-to-one matching with deterministic tie-breaking
- Classification metrics with explicit null for undefined cases
- Temporal error metrics (onset/offset, signed and absolute)
- Ground-truth label and manifest ingestion validation
- Annotation coverage auditing
- Unsupported trigger handling
- Report generation and CLI invocation
"""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from scripts.evaluate_detections import (
    CANONICAL_TRIGGERS,
    DEFAULT_IOU_THRESHOLD,
    DETECTOR_REGISTRY,
    audit_annotation_coverage,
    calculate_classification_metrics,
    calculate_temporal_metrics,
    compute_temporal_iou,
    evaluate_clip,
    evaluate_dataset,
    format_markdown_report,
    load_clips_manifest,
    load_ground_truth_labels,
    match_events_one_to_one,
)


# ==============================================================================
# 1. Temporal IoU Calculation Tests
# ==============================================================================

class TestComputeTemporalIoU:
    def test_perfect_overlap(self):
        """Identical intervals have tIoU = 1.0."""
        iou = compute_temporal_iou(1.0, 3.0, 1.0, 3.0)
        assert iou == pytest.approx(1.0)

    def test_partial_overlap(self):
        """Standard overlapping intervals."""
        # [1.0, 3.0) len 2.0, [2.0, 4.0) len 2.0 -> intersection [2, 3] = 1.0, union = 3.0
        iou = compute_temporal_iou(1.0, 3.0, 2.0, 4.0)
        assert iou == pytest.approx(1.0 / 3.0)

        # Contained interval: [0.0, 4.0) and [1.0, 3.0) -> inter=2.0, union=4.0 -> 0.5
        iou2 = compute_temporal_iou(0.0, 4.0, 1.0, 3.0)
        assert iou2 == pytest.approx(0.5)

    def test_exact_threshold_boundary_0_5(self):
        """Intervals yielding exactly 0.500 tIoU."""
        # [1.0, 3.0) len 2.0, [1.0, 2.0) len 1.0 -> inter=1.0, union=2.0 -> 0.500
        iou = compute_temporal_iou(1.0, 3.0, 1.0, 2.0)
        assert iou == pytest.approx(0.50)
        assert iou >= DEFAULT_IOU_THRESHOLD

    def test_just_below_threshold_boundary(self):
        """Intervals yielding slightly below 0.500 tIoU."""
        # [1.0, 3.0) len 2.0, [1.0, 1.99) len 0.99 -> inter=0.99, union=2.0 -> 0.495
        iou = compute_temporal_iou(1.0, 3.0, 1.0, 1.99)
        assert iou == pytest.approx(0.495)
        assert iou < DEFAULT_IOU_THRESHOLD

    def test_disjoint_intervals(self):
        """Non-overlapping intervals have tIoU = 0.0."""
        iou = compute_temporal_iou(1.0, 2.0, 3.0, 4.0)
        assert iou == 0.0

    def test_abutting_intervals(self):
        """Touching intervals with zero intersection length have tIoU = 0.0."""
        iou = compute_temporal_iou(1.0, 2.0, 2.0, 3.0)
        assert iou == 0.0

    def test_invalid_negative_timestamps(self):
        """Negative timestamps must raise ValueError."""
        with pytest.raises(ValueError, match="Interval start timestamps must be >= 0.0"):
            compute_temporal_iou(-1.0, 2.0, 1.0, 3.0)

    def test_invalid_inverted_intervals(self):
        """Interval with end <= start must raise ValueError."""
        with pytest.raises(ValueError, match="strictly greater than start"):
            compute_temporal_iou(3.0, 1.0, 1.0, 2.0)
        with pytest.raises(ValueError, match="strictly greater than start"):
            compute_temporal_iou(1.0, 1.0, 1.0, 2.0)

    def test_invalid_non_numeric_and_boolean(self):
        """Boolean or string inputs must raise ValueError."""
        with pytest.raises(ValueError, match="must be numeric"):
            compute_temporal_iou(True, 2.0, 1.0, 3.0)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="must be numeric"):
            compute_temporal_iou("1.0", 2.0, 1.0, 3.0)  # type: ignore[arg-type]

    def test_invalid_non_finite_values(self):
        """Infinity or NaN must raise ValueError."""
        with pytest.raises(ValueError, match="must be finite"):
            compute_temporal_iou(float("inf"), 2.0, 1.0, 3.0)
        with pytest.raises(ValueError, match="must be finite"):
            compute_temporal_iou(float("nan"), 2.0, 1.0, 3.0)


# ==============================================================================
# 2. One-to-One Temporal Matching Tests
# ==============================================================================

class TestMatchEventsOneToOne:
    def test_perfect_single_match(self):
        """Single matching prediction and ground truth event."""
        preds = [{"start": 1.0, "end": 3.0, "trigger": "loud_spike", "score": 0.9}]
        gts = [{"start": 1.0, "end": 3.0, "trigger": "loud_spike"}]

        res = match_events_one_to_one(preds, gts, iou_threshold=0.5)

        assert res["true_positives"] == 1
        assert res["false_positives"] == 0
        assert res["false_negatives"] == 0
        assert len(res["matches"]) == 1

        m = res["matches"][0]
        assert m["iou"] == 1.0
        assert m["onset_error"] == 0.0
        assert m["offset_error"] == 0.0

    def test_exact_threshold_matching_decision(self):
        """Boundary test: 0.5 matches (TP), 0.495 fails to match (FP + FN)."""
        # Exactly 0.5 IoU
        preds_pass = [{"start": 1.0, "end": 2.0, "trigger": "loud_spike"}]
        gts = [{"start": 1.0, "end": 3.0, "trigger": "loud_spike"}]
        res_pass = match_events_one_to_one(preds_pass, gts, iou_threshold=0.5)
        assert res_pass["true_positives"] == 1
        assert res_pass["false_positives"] == 0
        assert res_pass["false_negatives"] == 0

        # Below 0.5 IoU (0.495)
        preds_fail = [{"start": 1.0, "end": 1.99, "trigger": "loud_spike"}]
        res_fail = match_events_one_to_one(preds_fail, gts, iou_threshold=0.5)
        assert res_fail["true_positives"] == 0
        assert res_fail["false_positives"] == 1
        assert res_fail["false_negatives"] == 1

    def test_wrong_trigger_rejection(self):
        """Intervals with identical timestamps but mismatched triggers must NOT match."""
        preds = [{"start": 1.0, "end": 3.0, "trigger": "loud_spike"}]
        gts = [{"start": 1.0, "end": 3.0, "trigger": "profanity"}]

        res = match_events_one_to_one(preds, gts, iou_threshold=0.5)
        assert res["true_positives"] == 0
        assert res["false_positives"] == 1
        assert res["false_negatives"] == 1

    def test_competing_predictions_greedy_highest_iou(self):
        """Two predictions compete for a single GT event; highest IoU wins, lower is FP."""
        gts = [{"start": 1.0, "end": 5.0, "trigger": "loud_spike"}]
        preds = [
            {"start": 1.0, "end": 3.0, "trigger": "loud_spike", "id": "p_low_iou"},  # IoU = 2/4 = 0.50
            {"start": 1.0, "end": 4.5, "trigger": "loud_spike", "id": "p_high_iou"}, # IoU = 3.5/4 = 0.875
        ]

        res = match_events_one_to_one(preds, gts, iou_threshold=0.5)
        assert res["true_positives"] == 1
        assert res["false_positives"] == 1
        assert res["false_negatives"] == 0

        matched_p = res["matches"][0]["prediction"]
        assert matched_p["id"] == "p_high_iou"
        assert res["unmatched_predictions"][0]["id"] == "p_low_iou"

    def test_competing_ground_truth_for_single_prediction(self):
        """Two ground truth events overlap one prediction; highest IoU wins, other is FN."""
        preds = [{"start": 2.0, "end": 6.0, "trigger": "loud_spike"}]
        gts = [
            {"start": 2.0, "end": 5.5, "trigger": "loud_spike", "id": "gt_high_iou"}, # inter 3.5, union 4.0 -> 0.875
            {"start": 3.5, "end": 7.0, "trigger": "loud_spike", "id": "gt_mid_iou"},  # inter 2.5, union 5.0 -> 0.50
        ]

        res = match_events_one_to_one(preds, gts, iou_threshold=0.5)
        assert res["true_positives"] == 1
        assert res["false_positives"] == 0
        assert res["false_negatives"] == 1

        matched_gt = res["matches"][0]["ground_truth"]
        assert matched_gt["id"] == "gt_high_iou"
        assert res["unmatched_ground_truth"][0]["id"] == "gt_mid_iou"

    def test_deterministic_tie_breaking(self):
        """Identical IoUs and timestamps resolve deterministically by insertion index."""
        gts = [{"start": 1.0, "end": 3.0, "trigger": "loud_spike", "id": "gt_0"}]
        preds = [
            {"start": 1.0, "end": 3.0, "trigger": "loud_spike", "id": "p_first"},
            {"start": 1.0, "end": 3.0, "trigger": "loud_spike", "id": "p_second"},
        ]

        for _ in range(5):
            res = match_events_one_to_one(preds, gts, iou_threshold=0.5)
            assert res["matches"][0]["prediction"]["id"] == "p_first"
            assert res["unmatched_predictions"][0]["id"] == "p_second"

    def test_multiple_events_mixed_outcomes(self):
        """Test multiple events resulting in a mix of TPs, FPs, and FNs."""
        preds = [
            {"start": 1.0, "end": 2.5, "trigger": "loud_spike"},  # Matches gt 1 (inter 1.5, union 1.5 -> 1.0)
            {"start": 5.0, "end": 6.0, "trigger": "loud_spike"},  # False positive (no gt near 5.0)
            {"start": 10.0, "end": 12.0, "trigger": "profanity"}, # Matches gt 3
        ]
        gts = [
            {"start": 1.0, "end": 2.5, "trigger": "loud_spike"},
            {"start": 7.0, "end": 8.0, "trigger": "loud_spike"},  # False negative (no pred near 7.0)
            {"start": 10.0, "end": 12.0, "trigger": "profanity"},
        ]

        res = match_events_one_to_one(preds, gts, iou_threshold=0.5)
        assert res["true_positives"] == 2
        assert res["false_positives"] == 1
        assert res["false_negatives"] == 1


# ==============================================================================
# 3. Metric Calculation Tests
# ==============================================================================

class TestMetricCalculations:
    def test_classification_metrics_standard(self):
        """Standard valid counts produce correct precision, recall, and F1."""
        metrics = calculate_classification_metrics(tp=4, fp=1, fn=1)
        assert metrics["precision"] == pytest.approx(4 / 5)
        assert metrics["recall"] == pytest.approx(4 / 5)
        assert metrics["f1"] == pytest.approx(4 / 5)

    def test_classification_metrics_empty_negative_control(self):
        """Negative control clip with 0 predictions and 0 GT has undefined (null) metrics."""
        metrics = calculate_classification_metrics(tp=0, fp=0, fn=0)
        assert metrics["precision"] is None
        assert metrics["recall"] is None
        assert metrics["f1"] is None

    def test_classification_metrics_zero_predictions_with_gt(self):
        """No predictions for positive GT clip: precision is null, recall is 0.0, f1 is null."""
        metrics = calculate_classification_metrics(tp=0, fp=0, fn=2)
        assert metrics["precision"] is None
        assert metrics["recall"] == 0.0
        assert metrics["f1"] is None

    def test_classification_metrics_false_alarms_only(self):
        """Predictions on negative clip: precision is 0.0, recall is null, f1 is null."""
        metrics = calculate_classification_metrics(tp=0, fp=2, fn=0)
        assert metrics["precision"] == 0.0
        assert metrics["recall"] is None
        assert metrics["f1"] is None

    def test_temporal_metrics_empty(self):
        """Zero matches produce null for all temporal metrics."""
        temp = calculate_temporal_metrics([])
        assert temp["mean_iou"] is None
        assert temp["mean_onset_error"] is None
        assert temp["mean_offset_error"] is None
        assert temp["mean_abs_onset_error"] is None
        assert temp["mean_abs_offset_error"] is None

    def test_temporal_metrics_calculation(self):
        """Correct calculation of signed and absolute onset/offset errors."""
        matches = [
            {"iou": 0.8, "onset_error": -0.2, "offset_error": 0.1, "abs_onset_error": 0.2, "abs_offset_error": 0.1},
            {"iou": 0.6, "onset_error": 0.4, "offset_error": -0.3, "abs_onset_error": 0.4, "abs_offset_error": 0.3},
        ]
        temp = calculate_temporal_metrics(matches)
        assert temp["mean_iou"] == pytest.approx(0.70)
        assert temp["mean_onset_error"] == pytest.approx(0.10)
        assert temp["mean_offset_error"] == pytest.approx(-0.10)
        assert temp["mean_abs_onset_error"] == pytest.approx(0.30)
        assert temp["mean_abs_offset_error"] == pytest.approx(0.20)


# ==============================================================================
# 4. Ingestion & Annotation Integrity Tests
# ==============================================================================

class TestIngestionAndAudit:
    def test_load_ground_truth_labels_valid(self, tmp_path: Path):
        """Load valid labels.csv successfully."""
        csv_path = tmp_path / "test_labels.csv"
        csv_path.write_text(
            "clip,trigger,start,end,labeller,actual_rate\n"
            "clip_a.mp4,loud_spike,1.5,3.0,person_b,24.0\n"
            "clip_a.mp4,loud_spike,5.0,6.0,person_b,24.0\n"
            "clip_b.mp4,profanity,0.5,1.2,person_c,\n",
            encoding="utf-8",
        )

        labels = load_ground_truth_labels(csv_path)
        assert "clip_a.mp4" in labels
        assert len(labels["clip_a.mp4"]) == 2
        assert labels["clip_a.mp4"][0]["trigger"] == "loud_spike"
        assert labels["clip_a.mp4"][0]["actual_rate"] == 24.0
        assert "clip_b.mp4" in labels
        assert labels["clip_b.mp4"][0]["actual_rate"] is None

    def test_load_ground_truth_labels_missing_file(self):
        """Missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_ground_truth_labels("non_existent_file_xyz.csv")

    def test_load_ground_truth_labels_invalid_timestamps(self, tmp_path: Path):
        """Invalid timestamp values raise ValueError."""
        csv_path = tmp_path / "bad_labels.csv"
        csv_path.write_text(
            "clip,trigger,start,end,labeller,actual_rate\n"
            "clip_a.mp4,loud_spike,5.0,2.0,person_b,24.0\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="invalid interval"):
            load_ground_truth_labels(csv_path)

    def test_load_clips_manifest_valid(self, tmp_path: Path):
        """Load valid manifest.json successfully."""
        manifest_path = tmp_path / "test_manifest.json"
        data = {
            "clips": [
                {"clip_name": "clip_1.mp4", "duration": 5.0, "trigger": "loud_spike"},
                {"clip_name": "clip_neg.mp4", "duration": 4.0, "trigger": None},
            ]
        }
        manifest_path.write_text(json.dumps(data), encoding="utf-8")

        manifest = load_clips_manifest(manifest_path)
        assert "clip_1.mp4" in manifest
        assert "clip_neg.mp4" in manifest
        assert manifest["clip_1.mp4"]["duration"] == 5.0

    def test_audit_annotation_coverage_integrity(self):
        """Audit correctly identifies positive, negative control, and uncovered clips."""
        manifest = {
            "pos_spike.mp4": {"clip_name": "pos_spike.mp4", "trigger": "loud_spike"},
            "neg_ambient.mp4": {
                "clip_name": "neg_ambient.mp4",
                "trigger": None,
                "metadata": {"event_type": "ambient"},
            },
            "unlabeled_extra.mp4": {
                "clip_name": "unlabeled_extra.mp4",
                "trigger": "other_unknown",
                "metadata": {"event_type": "unknown"},
            },
        }
        labels = {
            "pos_spike.mp4": [{"clip": "pos_spike.mp4", "trigger": "loud_spike", "start": 1.0, "end": 2.0}],
        }

        audit = audit_annotation_coverage(manifest, labels, supported_triggers={"loud_spike"})
        spike_cov = audit["per_trigger_coverage"]["loud_spike"]

        assert "pos_spike.mp4" in spike_cov["positive_clips"]
        assert "neg_ambient.mp4" in spike_cov["negative_control_clips"]
        assert "unlabeled_extra.mp4" in spike_cov["uncovered_clips"]
        assert spike_cov["total_evaluable_clips"] == 2

    def test_unsupported_triggers_tracked_without_metrics(self):
        """Unsupported triggers are tracked in audit with ground truth counts and no fabricated metrics."""
        manifest = {"flashing_clip.mp4": {"clip_name": "flashing_clip.mp4", "trigger": "flashing"}}
        labels = {
            "flashing_clip.mp4": [
                {"clip": "flashing_clip.mp4", "trigger": "flashing", "start": 0.5, "end": 1.5}
            ]
        }

        audit = audit_annotation_coverage(manifest, labels, supported_triggers={"loud_spike"})
        assert "flashing" in audit["unsupported_triggers"]
        assert audit["unsupported_triggers"]["flashing"]["ground_truth_events_in_labels_csv"] == 1
        assert audit["unsupported_triggers"]["flashing"]["status"] == "missing_detector"


# ==============================================================================
# 5. Full Evaluation Pipeline & Report Formatting Tests
# ==============================================================================

class TestEvaluationPipeline:
    def test_evaluate_clip(self):
        """Evaluate a clip with multiple triggers and verify clean separation."""
        preds = [
            {"start": 1.0, "end": 2.5, "trigger": "loud_spike"},
            {"start": 3.0, "end": 4.0, "trigger": "profanity"},
        ]
        gts = [
            {"start": 1.0, "end": 2.5, "trigger": "loud_spike"},
        ]

        res = evaluate_clip(
            clip_name="test_clip.mp4",
            trigger="loud_spike",
            predictions=preds,
            ground_truth=gts,
            iou_threshold=0.5,
        )

        assert res["clip_name"] == "test_clip.mp4"
        assert res["trigger"] == "loud_spike"
        assert res["true_positives"] == 1
        assert res["false_positives"] == 0
        assert res["false_negatives"] == 0
        assert res["precision"] == 1.0
        assert res["recall"] == 1.0
        assert res["f1"] == 1.0

    def test_format_markdown_report_structure(self):
        """Markdown report formatting handles nulls and generates valid headings and tables."""
        mock_eval = {
            "schema_version": 1,
            "evaluation_timestamp_utc": "2026-10-16T12:00:00Z",
            "parameters": {
                "iou_threshold": 0.5,
                "matching_strategy": "one_to_one_greedy_highest_iou",
                "tie_breaking": "highest_iou_desc_pred_start_asc_gt_start_asc",
                "null_representation": "null_for_undefined_divisions",
            },
            "coverage_audit": {
                "unsupported_triggers": {
                    "flashing": {
                        "component": "flash_detector",
                        "status": "missing_detector",
                        "owner": "Person A",
                        "ground_truth_events_in_labels_csv": 16,
                    }
                }
            },
            "summary": {
                "evaluated_triggers": ["loud_spike"],
                "unsupported_triggers": ["flashing"],
                "total_clips_evaluated": 1,
                "counts": {"true_positives": 1, "false_positives": 0, "false_negatives": 0},
                "dataset_micro_metrics": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
                "dataset_macro_metrics": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
            },
            "per_trigger": {
                "loud_spike": {
                    "trigger": "loud_spike",
                    "status": "evaluated",
                    "component": "loudness_detector",
                    "modality": "audio",
                    "clips_evaluated_count": 1,
                    "positive_clips_count": 1,
                    "negative_control_clips_count": 0,
                    "counts": {
                        "ground_truth_events": 1,
                        "predicted_events": 1,
                        "true_positives": 1,
                        "false_positives": 0,
                        "false_negatives": 0,
                    },
                    "micro_metrics": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
                    "macro_metrics": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
                    "temporal_metrics": {
                        "mean_iou": 1.0,
                        "mean_onset_error": 0.0,
                        "mean_offset_error": 0.0,
                        "mean_abs_onset_error": 0.0,
                        "mean_abs_offset_error": 0.0,
                    },
                }
            },
            "per_clip": [
                {
                    "clip_name": "audio_loud_spike_test.mp4",
                    "trigger": "loud_spike",
                    "ground_truth_count": 1,
                    "prediction_count": 1,
                    "true_positives": 1,
                    "false_positives": 0,
                    "false_negatives": 0,
                    "precision": 1.0,
                    "recall": 1.0,
                    "f1": 1.0,
                    "mean_iou": 1.0,
                    "mean_abs_onset_error": 0.0,
                    "mean_abs_offset_error": 0.0,
                }
            ],
        }

        md = format_markdown_report(mock_eval)
        assert "# Detector Evaluation Report" in md
        assert "## 1. Executive Summary" in md
        assert "## 2. Methodology & Mathematical Definitions" in md
        assert "## 3. Per-Trigger Benchmark Results" in md
        assert "## 4. Per-Clip Detailed Results" in md
        assert "## 5. Annotation Integrity & Unsupported Detectors" in md
        assert "## 6. Reproducibility" in md
        assert "loud_spike" in md
        assert "flashing" in md


# ==============================================================================
# 6. CLI Execution Test
# ==============================================================================

class TestCLIExecution:
    def test_cli_help(self):
        """CLI entry point accepts --help and prints argument documentation."""
        cmd = [sys.executable, "scripts/evaluate_detections.py", "--help"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "Detection Evaluation Harness for Trigger-Aware Player" in res.stdout
        assert "--manifest" in res.stdout
        assert "--labels" in res.stdout
        assert "--iou-threshold" in res.stdout
        assert "--output-json" in res.stdout
        assert "--output-md" in res.stdout
