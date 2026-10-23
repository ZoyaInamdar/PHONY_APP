"""Unit tests for the Visual Insect Benchmark Evaluation Pipeline.

Covers:
1. Valid and invalid annotation schemas and semantics
2. Valid and invalid prediction schemas
3. Negative and uncertain intervals (masking & exclusion)
4. Partial annotation and excluded unreviewed gaps
5. Half-open interval boundary semantics [start, end)
6. Frame-level confusion counts and metrics
7. Empty predictions and empty ground truth handling
8. Deterministic event matching, duplicate predictions, and temporal IoU
9. Invalid or out-of-duration timestamps
10. Missing predictions and unreviewed clips handling
11. JSON/Markdown report output consistency and serialization
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from scripts.evaluate_insect import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_IOU_THRESHOLD,
    compute_temporal_iou,
    evaluate_clip_events,
    evaluate_clip_frames,
    evaluate_insect_benchmark,
    format_insect_markdown_report,
    is_timestamp_in_intervals,
    validate_annotation_data,
    validate_interval_semantics,
    validate_prediction_data,
)


# ==============================================================================
# 1. Annotation & Prediction Schema Validation Tests
# ==============================================================================

class TestAnnotationValidation:
    def test_valid_annotation_structure(self):
        """Minimal valid annotation passes validation."""
        valid_data = {
            "schema_version": 1,
            "benchmark_id": "test_bench_v1",
            "created_at": "2026-10-23T00:00:00Z",
            "description": "Test set",
            "clips": [
                {
                    "clip_id": "clip_01",
                    "source_video_title": "Test video",
                    "media_path": "media/clip_01.webm",
                    "scenario_category": "crawling_insect",
                    "split": "validation",
                    "annotator": "ReviewerA",
                    "annotation_status": "verified",
                    "media_duration_seconds": 10.0,
                    "reviewed_intervals": [{"start": 0.0, "end": 10.0}],
                    "positive_intervals": [{"start": 2.0, "end": 5.0}],
                    "explicit_negative_intervals": [{"start": 0.0, "end": 2.0}, {"start": 5.0, "end": 10.0}],
                    "uncertain_intervals": [],
                }
            ],
        }
        validate_annotation_data(valid_data)

    def test_invalid_schema_version(self):
        """Unsupported schema version is rejected."""
        with pytest.raises(ValueError, match="Unsupported schema_version"):
            validate_annotation_data({"schema_version": 2, "clips": []})

    def test_duplicate_clip_ids_rejected(self):
        """Duplicate clip IDs must raise ValueError."""
        data = {
            "schema_version": 1,
            "benchmark_id": "bench",
            "clips": [
                {
                    "clip_id": "dup_clip",
                    "source_video_title": "Video 1",
                    "media_path": "path1",
                    "scenario_category": "crawling_insect",
                    "split": "validation",
                    "annotator": "A",
                    "annotation_status": "reviewed",
                    "media_duration_seconds": 5.0,
                    "reviewed_intervals": [{"start": 0.0, "end": 5.0}],
                },
                {
                    "clip_id": "dup_clip",
                    "source_video_title": "Video 2",
                    "media_path": "path2",
                    "scenario_category": "dust",
                    "split": "test",
                    "annotator": "B",
                    "annotation_status": "reviewed",
                    "media_duration_seconds": 5.0,
                    "reviewed_intervals": [{"start": 0.0, "end": 5.0}],
                },
            ],
        }
        with pytest.raises(ValueError, match="Duplicate clip_id: 'dup_clip'"):
            validate_annotation_data(data)

    def test_invalid_scenario_category(self):
        """Invalid scenario category is rejected."""
        data = {
            "schema_version": 1,
            "benchmark_id": "bench",
            "clips": [
                {
                    "clip_id": "clip_01",
                    "source_video_title": "Video",
                    "media_path": "path",
                    "scenario_category": "invalid_category_name",
                    "split": "validation",
                    "annotator": "A",
                    "annotation_status": "reviewed",
                    "media_duration_seconds": 5.0,
                    "reviewed_intervals": [{"start": 0.0, "end": 5.0}],
                }
            ],
        }
        with pytest.raises(ValueError, match="invalid scenario_category"):
            validate_annotation_data(data)

    def test_overlapping_positive_intervals_rejected(self):
        """Overlapping positive intervals inside the same clip are rejected."""
        data = {
            "schema_version": 1,
            "benchmark_id": "bench",
            "clips": [
                {
                    "clip_id": "clip_01",
                    "source_video_title": "Video",
                    "media_path": "path",
                    "scenario_category": "flying_insect",
                    "split": "test",
                    "annotator": "A",
                    "annotation_status": "reviewed",
                    "media_duration_seconds": 10.0,
                    "reviewed_intervals": [{"start": 0.0, "end": 10.0}],
                    "positive_intervals": [
                        {"start": 1.0, "end": 4.0},
                        {"start": 3.0, "end": 6.0},  # overlaps [1, 4)
                    ],
                }
            ],
        }
        with pytest.raises(ValueError, match="overlapping intervals detected"):
            validate_annotation_data(data)

    def test_positive_negative_collision_rejected(self):
        """Positive and explicit negative intervals cannot overlap."""
        data = {
            "schema_version": 1,
            "benchmark_id": "bench",
            "clips": [
                {
                    "clip_id": "clip_01",
                    "source_video_title": "Video",
                    "media_path": "path",
                    "scenario_category": "bark",
                    "split": "test",
                    "annotator": "A",
                    "annotation_status": "reviewed",
                    "media_duration_seconds": 10.0,
                    "reviewed_intervals": [{"start": 0.0, "end": 10.0}],
                    "positive_intervals": [{"start": 2.0, "end": 5.0}],
                    "explicit_negative_intervals": [{"start": 4.0, "end": 8.0}],  # overlaps [4, 5)
                }
            ],
        }
        with pytest.raises(ValueError, match="Conflict between .* positive .* and .* negative"):
            validate_annotation_data(data)


class TestPredictionValidation:
    def test_valid_prediction_structure(self):
        """Valid prediction data passes validation."""
        pred = {
            "schema_version": 1,
            "model_id": "siglip2_zero_shot",
            "predictions": [
                {
                    "clip_id": "clip_01",
                    "interval_events": [{"start": 1.0, "end": 3.0, "confidence": 0.8}],
                    "frame_detections": [{"timestamp": 1.0, "confidence": 0.85}],
                }
            ],
        }
        validate_prediction_data(pred)

    def test_invalid_confidence_score(self):
        """Confidence score > 1.0 or < 0.0 is rejected."""
        pred = {
            "schema_version": 1,
            "model_id": "test_model",
            "predictions": [
                {
                    "clip_id": "c1",
                    "interval_events": [{"start": 1.0, "end": 2.0, "confidence": 1.5}],
                }
            ],
        }
        with pytest.raises(ValueError, match="confidence must be in"):
            validate_prediction_data(pred)


# ==============================================================================
# 2. Interval Semantics & Half-Open Boundaries
# ==============================================================================

class TestIntervalSemantics:
    def test_negative_start_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            validate_interval_semantics({"start": -0.5, "end": 2.0}, 10.0)

    def test_zero_duration_rejected(self):
        """Zero duration interval [start == end) is invalid."""
        with pytest.raises(ValueError, match="must be strictly greater"):
            validate_interval_semantics({"start": 3.0, "end": 3.0}, 10.0)

    def test_inverted_interval_rejected(self):
        with pytest.raises(ValueError, match="must be strictly greater"):
            validate_interval_semantics({"start": 5.0, "end": 2.0}, 10.0)

    def test_interval_exceeding_duration_rejected(self):
        with pytest.raises(ValueError, match="exceeds media duration"):
            validate_interval_semantics({"start": 5.0, "end": 12.0}, 10.0)

    def test_half_open_boundary_membership(self):
        """Interval [1.0, 3.0) includes 1.0, 2.999, but excludes 3.0."""
        invs = [{"start": 1.0, "end": 3.0}]
        assert is_timestamp_in_intervals(1.0, invs) is True
        assert is_timestamp_in_intervals(2.0, invs) is True
        assert is_timestamp_in_intervals(2.999, invs) is True
        assert is_timestamp_in_intervals(3.0, invs) is False
        assert is_timestamp_in_intervals(0.999, invs) is False

    def test_abutting_intervals_do_not_overlap(self):
        """Intervals [1.0, 2.0) and [2.0, 3.0) share zero duration."""
        iou = compute_temporal_iou(1.0, 2.0, 2.0, 3.0)
        assert iou == 0.0


# ==============================================================================
# 3. Frame-Level Evaluation & Masking Tests
# ==============================================================================

class TestFrameEvaluation:
    def test_perfect_frame_classification(self):
        clip = {
            "clip_id": "clip_perfect",
            "media_duration_seconds": 6.0,
            "reviewed_intervals": [{"start": 0.0, "end": 6.0}],
            "positive_intervals": [{"start": 2.0, "end": 4.0}],
            "explicit_negative_intervals": [{"start": 0.0, "end": 2.0}, {"start": 4.0, "end": 6.0}],
            "uncertain_intervals": [],
        }
        pred = {
            "clip_id": "clip_perfect",
            "interval_events": [{"start": 2.0, "end": 4.0, "confidence": 0.9}],
        }
        m = evaluate_clip_frames(clip, pred, threshold=0.5, fps=1.0)
        assert m.true_positives == 2  # t=2, 3
        assert m.true_negatives == 4  # t=0, 1, 4, 5
        assert m.false_positives == 0
        assert m.false_negatives == 0
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0

    def test_unreviewed_gaps_strictly_excluded(self):
        """Frames outside reviewed intervals are never counted as negative."""
        clip = {
            "clip_id": "clip_gap",
            "media_duration_seconds": 10.0,
            # Only [2.0, 6.0) is reviewed. [0.0, 2.0) and [6.0, 10.0) are unreviewed gaps.
            "reviewed_intervals": [{"start": 2.0, "end": 6.0}],
            "positive_intervals": [{"start": 3.0, "end": 5.0}],
            "explicit_negative_intervals": [{"start": 2.0, "end": 3.0}, {"start": 5.0, "end": 6.0}],
            "uncertain_intervals": [],
        }
        # Detector predicts false alarm at t=8.0 (in unreviewed region)
        pred = {
            "clip_id": "clip_gap",
            "interval_events": [{"start": 8.0, "end": 10.0, "confidence": 0.9}],
        }
        m = evaluate_clip_frames(clip, pred, threshold=0.5, fps=1.0)
        assert m.excluded_unreviewed_samples == 6  # t=0, 1, 6, 7, 8, 9
        assert m.scored_samples == 4  # t=2, 3, 4, 5
        assert m.false_positives == 0  # prediction in unreviewed gap is NOT penalized as FP
        assert m.true_positives == 0
        assert m.false_negatives == 2  # t=3, 4 missed

    def test_uncertain_intervals_strictly_excluded(self):
        """Uncertain interval frames are excluded from scoring."""
        clip = {
            "clip_id": "clip_unc",
            "media_duration_seconds": 5.0,
            "reviewed_intervals": [{"start": 0.0, "end": 5.0}],
            "positive_intervals": [{"start": 0.0, "end": 2.0}],
            "explicit_negative_intervals": [{"start": 3.0, "end": 5.0}],
            "uncertain_intervals": [{"start": 2.0, "end": 3.0}],  # t=2 is uncertain
        }
        pred = {
            "clip_id": "clip_unc",
            "interval_events": [{"start": 2.0, "end": 3.0, "confidence": 0.9}],
        }
        m = evaluate_clip_frames(clip, pred, threshold=0.5, fps=1.0)
        assert m.excluded_uncertain_samples == 1  # t=2
        assert m.false_positives == 0  # prediction in uncertain region is excluded
        assert m.true_negatives == 2  # t=3, 4
        assert m.false_negatives == 2  # t=0, 1 missed

    def test_pure_negative_clip_metrics(self):
        """Pure negative control clip with no predictions yields TN and null Precision/Recall."""
        clip = {
            "clip_id": "clip_clean",
            "media_duration_seconds": 5.0,
            "reviewed_intervals": [{"start": 0.0, "end": 5.0}],
            "positive_intervals": [],
            "explicit_negative_intervals": [{"start": 0.0, "end": 5.0}],
            "uncertain_intervals": [],
        }
        pred = {"clip_id": "clip_clean", "interval_events": []}
        m = evaluate_clip_frames(clip, pred, threshold=0.5, fps=1.0)
        assert m.true_negatives == 5
        assert m.true_positives == 0
        assert m.false_positives == 0
        assert m.false_negatives == 0
        assert m.precision is None
        assert m.recall is None
        assert m.f1 is None
        assert m.specificity == 1.0


# ==============================================================================
# 4. Event Matching & Temporal IoU Tests
# ==============================================================================

class TestEventEvaluation:
    def test_event_matching_above_threshold(self):
        clip = {
            "clip_id": "c1",
            "reviewed_intervals": [{"start": 0.0, "end": 10.0}],
            "positive_intervals": [{"start": 2.0, "end": 6.0}],  # len 4.0
        }
        # Pred: [2.5, 6.5) len 4.0 -> inter [2.5, 6.0) = 3.5, union = 4.5 -> iou = 3.5/4.5 = 0.777 >= 0.3
        pred = {
            "clip_id": "c1",
            "interval_events": [{"start": 2.5, "end": 6.5, "confidence": 0.9}],
        }
        m = evaluate_clip_events(clip, pred, iou_threshold=0.3)
        assert m.true_positives == 1
        assert m.false_positives == 0
        assert m.false_negatives == 0
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.mean_onset_error_seconds == 0.5
        assert m.mean_offset_error_seconds == 0.5
        assert m.mean_tiou == pytest.approx(3.5 / 4.5, abs=1e-3)

    def test_duplicate_prediction_matches_at_most_once(self):
        """Two overlapping predictions cannot both match a single ground-truth event."""
        clip = {
            "clip_id": "c_dup",
            "reviewed_intervals": [{"start": 0.0, "end": 10.0}],
            "positive_intervals": [{"start": 2.0, "end": 6.0}],
            "explicit_negative_intervals": [{"start": 0.0, "end": 2.0}, {"start": 6.0, "end": 10.0}],
        }
        # Two predictions targeting the same GT event
        pred = {
            "clip_id": "c_dup",
            "interval_events": [
                {"start": 2.0, "end": 6.0, "confidence": 0.95},  # tIoU=1.0
                {"start": 2.2, "end": 5.8, "confidence": 0.85},  # tIoU=0.9
            ],
        }
        m = evaluate_clip_events(clip, pred, iou_threshold=0.3)
        assert m.true_positives == 1
        assert m.false_positives == 1  # second prediction is flagged as duplicate / false positive
        assert m.false_negatives == 0
        assert m.precision == 0.5

    def test_empty_predictions_handling(self):
        clip = {
            "clip_id": "c_empty",
            "reviewed_intervals": [{"start": 0.0, "end": 5.0}],
            "positive_intervals": [{"start": 1.0, "end": 3.0}],
        }
        m = evaluate_clip_events(clip, None)
        assert m.true_positives == 0
        assert m.false_positives == 0
        assert m.false_negatives == 1
        assert m.precision is None
        assert m.recall == 0.0
        assert m.f1 is None

    def test_duration_coverage_ratio_duplicate_predictions_not_double_counted(self):
        """Duplicate/overlapping predictions must not double count towards duration coverage."""
        clip = {
            "clip_id": "c_dup",
            "reviewed_intervals": [{"start": 0.0, "end": 10.0}],
            "positive_intervals": [{"start": 0.0, "end": 10.0}],
            "explicit_negative_intervals": [],
            "uncertain_intervals": [],
        }
        pred = {
            "clip_id": "c_dup",
            "interval_events": [
                {"start": 0.0, "end": 5.0, "confidence": 0.9},
                {"start": 0.0, "end": 5.0, "confidence": 0.8},  # Exact duplicate covering first half
            ],
        }
        m = evaluate_clip_events(clip, pred, iou_threshold=0.3)
        # Should be exactly 0.50 (5.0s / 10.0s), NOT 1.0
        assert m.duration_coverage_ratio == 0.5000

    def test_event_evaluation_uncertain_intervals_not_penalized_as_fp(self):
        """Unmatched predictions falling in uncertain intervals are excluded from false positive penalty."""
        clip = {
            "clip_id": "c_unc_event",
            "reviewed_intervals": [{"start": 0.0, "end": 10.0}],
            "positive_intervals": [],
            "explicit_negative_intervals": [{"start": 0.0, "end": 4.0}, {"start": 7.0, "end": 10.0}],
            "uncertain_intervals": [{"start": 4.0, "end": 7.0}],  # Uncertain zone
        }
        pred = {
            "clip_id": "c_unc_event",
            "interval_events": [
                {"start": 4.5, "end": 6.5, "confidence": 0.95},  # Inside uncertain zone
            ],
        }
        m = evaluate_clip_events(clip, pred, iou_threshold=0.3)
        assert m.false_positives == 0
        assert m.false_positive_duration_seconds == 0.0


# ==============================================================================
# 5. Full Benchmark Integration & Fixture Tests
# ==============================================================================

class TestBenchmarkHarnessIntegration:
    def test_synthetic_fixtures_evaluation(self):
        """Evaluate bundled synthetic fixtures end-to-end."""
        anno_path = REPO_ROOT / "data" / "benchmark" / "fixtures" / "synthetic_insect_annotations.json"
        pred_path = REPO_ROOT / "data" / "benchmark" / "fixtures" / "synthetic_insect_predictions.json"

        with anno_path.open("r", encoding="utf-8") as f:
            anno_data = json.load(f)
        with pred_path.open("r", encoding="utf-8") as f:
            pred_data = json.load(f)

        summary = evaluate_insect_benchmark(
            annotations=anno_data,
            predictions=pred_data,
            iou_threshold=0.3,
            confidence_threshold=0.5,
            fps=1.0,
        )

        assert summary.total_clips == 4
        assert summary.evaluated_clips == 3
        assert summary.skipped_clips == 1

        # Check frame micro metrics
        fm = summary.overall_frame_metrics["micro"]
        assert fm["true_positives"] == 5
        assert fm["false_positives"] == 0
        assert fm["false_negatives"] == 1
        assert fm["true_negatives"] == 16
        assert fm["precision"] == 1.0
        assert fm["recall"] == pytest.approx(5.0 / 6.0, abs=1e-3)

        # Check event metrics
        em = summary.overall_event_metrics
        assert em["true_positives"] == 2
        assert em["false_positives"] == 0
        assert em["false_negatives"] == 0
        assert em["precision"] == 1.0
        assert em["recall"] == 1.0

        # Check Markdown formatting does not crash
        md = format_insect_markdown_report(summary)
        assert "# Insect Benchmark Evaluation Report" in md
        assert "`synthetic_clip_01_crawling_pos`" in md
        assert "*SKIPPED*" in md  # unreviewed clip 4 is marked skipped

    def test_non_insect_evaluation_regression_safety(self):
        """Existing canonical evaluate_detections.py triggers remain untouched."""
        from scripts.evaluate_detections import CANONICAL_TRIGGERS, DETECTOR_REGISTRY

        assert "insect" in CANONICAL_TRIGGERS
        assert "insect" in DETECTOR_REGISTRY
        assert DETECTOR_REGISTRY["insect"]["status"] == "ready_for_evaluation"
        assert DETECTOR_REGISTRY["loud_spike"]["status"] == "available"
        assert DETECTOR_REGISTRY["flashing"]["status"] == "available"
