#!/usr/bin/env python3
"""Insect Benchmark Evaluation Pipeline & Metrics Harness.

This module provides:
1. Annotation schema validation for human-reviewed insect benchmark datasets.
2. Prediction schema validation for offline visual insect detector outputs.
3. Frame-level confusion matrix and classification metrics (precision, recall, F1, specificity).
4. Deterministic event-level bipartite matching via temporal IoU (tIoU).
5. False alarm rate per evaluated reviewed minute and duration coverage metrics.
6. Region masking: unreviewed gaps and uncertain regions are strictly excluded from scoring.
7. Machine-readable JSON and human-readable Markdown reporting.

Interval convention: Half-open intervals [start, end) where point t is included iff start <= t < end.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ANNOTATION_SCHEMA = REPO_ROOT / "schema" / "insect_benchmark_annotation.schema.json"
DEFAULT_PREDICTION_SCHEMA = REPO_ROOT / "schema" / "insect_prediction.schema.json"
DEFAULT_IOU_THRESHOLD = 0.3
DEFAULT_SAMPLING_FPS = 1.0
DEFAULT_CONFIDENCE_THRESHOLD = 0.5

FLOAT_TOLERANCE = 0.05  # 50ms tolerance for end-of-media floating point imprecision

VALID_SCENARIOS = {
    "crawling_insect",
    "flying_insect",
    "swarm",
    "spider_distractor",
    "spider_web_distractor",
    "foliage",
    "dust",
    "sparks",
    "bark",
    "clean_interior",
}

VALID_SPLITS = {"validation", "test"}
VALID_STATUSES = {"draft", "reviewed", "verified", "unreviewed"}


# ==============================================================================
# 1. Validation Logic
# ==============================================================================

def validate_interval_semantics(
    interval: Dict[str, Any],
    max_duration: float,
    context: str = "interval",
) -> None:
    """Validate half-open interval semantics [start, end)."""
    if "start" not in interval or "end" not in interval:
        raise ValueError(f"{context}: missing required 'start' or 'end' keys: {interval}")

    start = interval["start"]
    end = interval["end"]

    if not isinstance(start, (int, float)) or isinstance(start, bool) or not math.isfinite(float(start)):
        raise ValueError(f"{context}: 'start' must be finite numeric, got {start}")
    if not isinstance(end, (int, float)) or isinstance(end, bool) or not math.isfinite(float(end)):
        raise ValueError(f"{context}: 'end' must be finite numeric, got {end}")

    s = float(start)
    e = float(end)

    if s < 0.0:
        raise ValueError(f"{context}: 'start' ({s}) cannot be negative")
    if e <= s:
        raise ValueError(f"{context}: 'end' ({e}) must be strictly greater than 'start' ({s})")
    if max_duration > 0 and e > (max_duration + FLOAT_TOLERANCE):
        raise ValueError(
            f"{context}: 'end' ({e:.3f}s) exceeds media duration ({max_duration:.3f}s)"
        )


def check_interval_overlaps(
    intervals: List[Dict[str, Any]],
    context: str = "intervals",
) -> None:
    """Ensure intervals in the list do not overlap with each other."""
    sorted_ints = sorted(intervals, key=lambda x: (float(x["start"]), float(x["end"])))
    for i in range(len(sorted_ints) - 1):
        cur_end = float(sorted_ints[i]["end"])
        nxt_start = float(sorted_ints[i + 1]["start"])
        if nxt_start < cur_end:
            raise ValueError(
                f"{context}: overlapping intervals detected: [{sorted_ints[i]['start']}, {cur_end}) "
                f"and [{nxt_start}, {sorted_ints[i + 1]['end']})"
            )


def check_disjoint_sets(
    set_a: List[Dict[str, Any]],
    set_b: List[Dict[str, Any]],
    context_a: str,
    context_b: str,
) -> None:
    """Ensure two interval lists do not overlap each other."""
    for a in set_a:
        sa, ea = float(a["start"]), float(a["end"])
        for b in set_b:
            sb, eb = float(b["start"]), float(b["end"])
            # Check overlap between [sa, ea) and [sb, eb)
            if max(sa, sb) < min(ea, eb):
                raise ValueError(
                    f"Conflict between {context_a} [{sa}, {ea}) and {context_b} [{sb}, {eb})"
                )


def validate_annotation_data(data: Dict[str, Any]) -> None:
    """Validate insect annotation dictionary structure and semantics."""
    if not isinstance(data, dict):
        raise ValueError("Annotation root must be a JSON object")

    if data.get("schema_version") != 1:
        raise ValueError(f"Unsupported schema_version: {data.get('schema_version')} (expected 1)")

    clips = data.get("clips")
    if not isinstance(clips, list):
        raise ValueError("'clips' must be an array")

    seen_clip_ids: Set[str] = set()

    for idx, clip in enumerate(clips):
        prefix = f"Clip[{idx}] ({clip.get('clip_id', 'unknown')})"
        clip_id = clip.get("clip_id")
        if not clip_id or not isinstance(clip_id, str):
            raise ValueError(f"{prefix}: missing or invalid 'clip_id'")
        if clip_id in seen_clip_ids:
            raise ValueError(f"Duplicate clip_id: '{clip_id}'")
        seen_clip_ids.add(clip_id)

        dur = clip.get("media_duration_seconds")
        if not isinstance(dur, (int, float)) or dur <= 0:
            raise ValueError(f"{prefix}: 'media_duration_seconds' must be positive numeric")
        dur_f = float(dur)

        scenario = clip.get("scenario_category")
        if scenario not in VALID_SCENARIOS:
            raise ValueError(f"{prefix}: invalid scenario_category '{scenario}'")

        split = clip.get("split")
        if split not in VALID_SPLITS:
            raise ValueError(f"{prefix}: invalid split '{split}'")

        status = clip.get("annotation_status")
        if status not in VALID_STATUSES:
            raise ValueError(f"{prefix}: invalid annotation_status '{status}'")

        reviewed = clip.get("reviewed_intervals", [])
        positives = clip.get("positive_intervals", [])
        negatives = clip.get("explicit_negative_intervals", [])
        uncertains = clip.get("uncertain_intervals", [])

        # Validate internal intervals
        for r in reviewed:
            validate_interval_semantics(r, dur_f, f"{prefix} reviewed_interval")
        for p in positives:
            validate_interval_semantics(p, dur_f, f"{prefix} positive_interval")
        for n in negatives:
            validate_interval_semantics(n, dur_f, f"{prefix} negative_interval")
        for u in uncertains:
            validate_interval_semantics(u, dur_f, f"{prefix} uncertain_interval")

        # Overlap checks
        check_interval_overlaps(reviewed, f"{prefix} reviewed_intervals")
        check_interval_overlaps(positives, f"{prefix} positive_intervals")
        check_interval_overlaps(negatives, f"{prefix} explicit_negative_intervals")
        check_interval_overlaps(uncertains, f"{prefix} uncertain_intervals")

        # Mutual exclusivity
        check_disjoint_sets(positives, negatives, f"{prefix} positive", f"{prefix} negative")
        check_disjoint_sets(positives, uncertains, f"{prefix} positive", f"{prefix} uncertain")
        check_disjoint_sets(negatives, uncertains, f"{prefix} negative", f"{prefix} uncertain")


def validate_prediction_data(data: Dict[str, Any]) -> None:
    """Validate insect prediction dictionary structure and semantics."""
    if not isinstance(data, dict):
        raise ValueError("Prediction root must be a JSON object")

    if data.get("schema_version") != 1:
        raise ValueError(f"Unsupported schema_version: {data.get('schema_version')} (expected 1)")

    if not data.get("model_id"):
        raise ValueError("Missing 'model_id' in prediction root")

    preds = data.get("predictions")
    if not isinstance(preds, list):
        raise ValueError("'predictions' must be an array")

    for idx, cp in enumerate(preds):
        prefix = f"Prediction[{idx}] ({cp.get('clip_id', 'unknown')})"
        if not cp.get("clip_id"):
            raise ValueError(f"{prefix}: missing 'clip_id'")

        events = cp.get("interval_events", [])
        for ev in events:
            validate_interval_semantics(ev, 0.0, f"{prefix} interval_event")
            conf = ev.get("confidence")
            if not isinstance(conf, (int, float)) or conf < 0.0 or conf > 1.0:
                raise ValueError(f"{prefix}: event confidence must be in [0.0, 1.0], got {conf}")

        frames = cp.get("frame_detections", [])
        for fr in frames:
            ts = fr.get("timestamp")
            if not isinstance(ts, (int, float)) or ts < 0.0:
                raise ValueError(f"{prefix}: frame timestamp must be nonnegative, got {ts}")
            conf = fr.get("confidence")
            if not isinstance(conf, (int, float)) or conf < 0.0 or conf > 1.0:
                raise ValueError(f"{prefix}: frame confidence must be in [0.0, 1.0], got {conf}")


# ==============================================================================
# 2. Metric Calculations & Semantics
# ==============================================================================

def compute_temporal_iou(
    start_a: float,
    end_a: float,
    start_b: float,
    end_b: float,
) -> float:
    """Compute 1D temporal Intersection-over-Union between intervals [start, end)."""
    sa, ea = float(start_a), float(end_a)
    sb, eb = float(start_b), float(end_b)

    inter = max(0.0, min(ea, eb) - max(sa, sb))
    dur_a = ea - sa
    dur_b = eb - sb
    union = dur_a + dur_b - inter

    if union <= 0.0:
        return 0.0
    return max(0.0, min(1.0, inter / union))


def is_timestamp_in_intervals(ts: float, intervals: List[Dict[str, Any]]) -> bool:
    """Half-open check: start <= ts < end."""
    for inv in intervals:
        if float(inv["start"]) <= ts < float(inv["end"]):
            return True
    return False


@dataclass
class FrameEvaluationMetrics:
    total_samples: int = 0
    scored_samples: int = 0
    excluded_unreviewed_samples: int = 0
    excluded_uncertain_samples: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None
    accuracy: Optional[float] = None
    specificity: Optional[float] = None


def evaluate_clip_frames(
    clip_annotation: Dict[str, Any],
    clip_prediction: Optional[Dict[str, Any]],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    fps: float = DEFAULT_SAMPLING_FPS,
) -> FrameEvaluationMetrics:
    """Evaluate frame-level classification against human-reviewed regions.

    Semantics:
    - Gaps between reviewed intervals and unreviewed regions are strictly EXCLUDED.
    - Uncertain intervals are strictly EXCLUDED.
    - True Negatives are counted ONLY within explicit_negative_intervals.
    """
    duration = float(clip_annotation["media_duration_seconds"])
    dt = 1.0 / fps if fps > 0 else 1.0
    num_samples = int(math.ceil(duration / dt))

    reviewed = clip_annotation.get("reviewed_intervals", [])
    positives = clip_annotation.get("positive_intervals", [])
    negatives = clip_annotation.get("explicit_negative_intervals", [])
    uncertains = clip_annotation.get("uncertain_intervals", [])

    events = (clip_prediction.get("interval_events", []) if clip_prediction else [])
    active_events = [ev for ev in events if float(ev.get("confidence", 1.0)) >= threshold]

    frame_dets = (clip_prediction.get("frame_detections", []) if clip_prediction else [])
    # Build fast lookup for frame-level scores
    frame_scores = {round(float(fr["timestamp"]) / dt): float(fr["confidence"]) for fr in frame_dets}

    metrics = FrameEvaluationMetrics(total_samples=num_samples)

    for k in range(num_samples):
        t = round(k * dt, 4)

        # Exclusion checks
        if is_timestamp_in_intervals(t, uncertains):
            metrics.excluded_uncertain_samples += 1
            continue

        if not is_timestamp_in_intervals(t, reviewed):
            metrics.excluded_unreviewed_samples += 1
            continue

        is_gt_pos = is_timestamp_in_intervals(t, positives)
        is_gt_neg = is_timestamp_in_intervals(t, negatives)

        if not is_gt_pos and not is_gt_neg:
            # Reviewed, but neither tagged positive nor explicit negative (unscored)
            metrics.excluded_uncertain_samples += 1
            continue

        # Check prediction
        is_pred_pos = False
        if is_timestamp_in_intervals(t, active_events):
            is_pred_pos = True
        elif k in frame_scores and frame_scores[k] >= threshold:
            is_pred_pos = True

        metrics.scored_samples += 1

        if is_gt_pos and is_pred_pos:
            metrics.true_positives += 1
        elif is_gt_pos and not is_pred_pos:
            metrics.false_negatives += 1
        elif is_gt_neg and is_pred_pos:
            metrics.false_positives += 1
        elif is_gt_neg and not is_pred_pos:
            metrics.true_negatives += 1

    # Derived metrics with explicit None for undefined
    tp, fp = metrics.true_positives, metrics.false_positives
    fn, tn = metrics.false_negatives, metrics.true_negatives

    if (tp + fp) > 0:
        metrics.precision = round(float(tp) / float(tp + fp), 4)
    if (tp + fn) > 0:
        metrics.recall = round(float(tp) / float(tp + fn), 4)
    if metrics.precision is not None and metrics.recall is not None and (metrics.precision + metrics.recall) > 0:
        metrics.f1 = round(2.0 * (metrics.precision * metrics.recall) / (metrics.precision + metrics.recall), 4)

    total_scored = tp + fp + fn + tn
    if total_scored > 0:
        metrics.accuracy = round(float(tp + tn) / float(total_scored), 4)
    if (tn + fp) > 0:
        metrics.specificity = round(float(tn) / float(tn + fp), 4)

    return metrics


@dataclass
class EventEvaluationMetrics:
    ground_truth_events: int = 0
    predicted_events: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None
    mean_tiou: Optional[float] = None
    mean_onset_error_seconds: Optional[float] = None
    mean_offset_error_seconds: Optional[float] = None
    false_positive_duration_seconds: float = 0.0
    false_alarms_per_minute: Optional[float] = None
    duration_coverage_ratio: Optional[float] = None


def evaluate_clip_events(
    clip_annotation: Dict[str, Any],
    clip_prediction: Optional[Dict[str, Any]],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    fps: float = DEFAULT_SAMPLING_FPS,
) -> EventEvaluationMetrics:
    """Perform deterministic one-to-one bipartite event matching on positive intervals.

    Rules:
    - Candidate match requires tIoU >= iou_threshold.
    - Tie-breaking: sorted by tIoU descending, then prediction start ascending, then GT start ascending.
    - False positives in unreviewed/uncertain intervals are excluded from penalty.
    """
    positives = clip_annotation.get("positive_intervals", [])
    reviewed = clip_annotation.get("reviewed_intervals", [])
    uncertains = clip_annotation.get("uncertain_intervals", [])

    events = (clip_prediction.get("interval_events", []) if clip_prediction else [])
    active_preds = [ev for ev in events if float(ev.get("confidence", 1.0)) >= confidence_threshold]

    metrics = EventEvaluationMetrics(
        ground_truth_events=len(positives),
        predicted_events=len(active_preds),
    )

    # Candidate pairs
    candidate_pairs: List[Tuple[float, float, float, int, int, Dict[str, Any], Dict[str, Any]]] = []
    for p_idx, pred in enumerate(active_preds):
        ps, pe = float(pred["start"]), float(pred["end"])
        for g_idx, gt in enumerate(positives):
            gs, ge = float(gt["start"]), float(gt["end"])
            iou = compute_temporal_iou(ps, pe, gs, ge)
            if iou >= iou_threshold:
                # (-iou for desc sort, ps, gs, p_idx, g_idx, pred, gt)
                candidate_pairs.append((-iou, ps, gs, p_idx, g_idx, pred, gt))

    candidate_pairs.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))

    matched_preds: Set[int] = set()
    matched_gts: Set[int] = set()
    tious: List[float] = []
    onset_errors: List[float] = []
    offset_errors: List[float] = []

    for neg_iou, ps, gs, p_idx, g_idx, pred, gt in candidate_pairs:
        if p_idx not in matched_preds and g_idx not in matched_gts:
            matched_preds.add(p_idx)
            matched_gts.add(g_idx)
            tious.append(-neg_iou)
            onset_errors.append(ps - gs)
            offset_errors.append(float(pred["end"]) - float(gt["end"]))

    metrics.true_positives = len(matched_preds)
    metrics.false_negatives = len(positives) - len(matched_gts)

    # Check unmatched predictions: only count as false positives if they fall in reviewed region
    unmatched_fp = 0
    dt = 1.0 / fps if fps > 0 else 1.0
    fp_dur = 0.0

    for p_idx, pred in enumerate(active_preds):
        if p_idx not in matched_preds:
            ps, pe = float(pred["start"]), float(pred["end"])
            # Sample frames within [ps, pe)
            frames_in_pred = [ps + i * dt for i in range(int(math.ceil((pe - ps) / dt)))]
            # Check if prediction falls in negative or reviewed region
            in_negative = any(
                is_timestamp_in_intervals(t, clip_annotation.get("explicit_negative_intervals", []))
                for t in frames_in_pred
            )
            # If the prediction falls entirely within uncertain intervals, exclude from FP penalty
            in_uncertain = (
                all(is_timestamp_in_intervals(t, uncertains) for t in frames_in_pred)
                if frames_in_pred else is_timestamp_in_intervals(ps, uncertains)
            )

            if in_negative or (is_timestamp_in_intervals(ps, reviewed) and not in_uncertain):
                unmatched_fp += 1
                fp_dur += (pe - ps)

    metrics.false_positives = unmatched_fp
    metrics.false_positive_duration_seconds = round(fp_dur, 3)

    tp, fp, fn = metrics.true_positives, metrics.false_positives, metrics.false_negatives

    if (tp + fp) > 0:
        metrics.precision = round(float(tp) / float(tp + fp), 4)
    if (tp + fn) > 0:
        metrics.recall = round(float(tp) / float(tp + fn), 4)
    if metrics.precision is not None and metrics.recall is not None and (metrics.precision + metrics.recall) > 0:
        metrics.f1 = round(2.0 * (metrics.precision * metrics.recall) / (metrics.precision + metrics.recall), 4)

    if tious:
        metrics.mean_tiou = round(sum(tious) / len(tious), 4)
    if onset_errors:
        metrics.mean_onset_error_seconds = round(sum(onset_errors) / len(onset_errors), 4)
    if offset_errors:
        metrics.mean_offset_error_seconds = round(sum(offset_errors) / len(offset_errors), 4)

    # Reviewed duration in minutes for false alarm rate
    total_reviewed_sec = sum(float(r["end"]) - float(r["start"]) for r in reviewed)
    if total_reviewed_sec > 0:
        rev_mins = total_reviewed_sec / 60.0
        metrics.false_alarms_per_minute = round(float(fp) / rev_mins, 4)

    # Event duration coverage: compute true union of active predictions covering GT intervals
    total_gt_sec = sum(float(g["end"]) - float(g["start"]) for g in positives)
    if total_gt_sec > 0:
        covered_sec = 0.0
        for g in positives:
            gs, ge = float(g["start"]), float(g["end"])
            # Collect all overlapping sub-intervals inside this GT interval
            sub_intervals = []
            for p in active_preds:
                ps, pe = float(p["start"]), float(p["end"])
                inter_s = max(gs, ps)
                inter_e = min(ge, pe)
                if inter_e > inter_s:
                    sub_intervals.append((inter_s, inter_e))

            if sub_intervals:
                # Merge overlapping sub-intervals to prevent false double-counting
                sub_intervals.sort(key=lambda x: x[0])
                merged = [sub_intervals[0]]
                for nxt_s, nxt_e in sub_intervals[1:]:
                    prev_s, prev_e = merged[-1]
                    if nxt_s <= prev_e:
                        merged[-1] = (prev_s, max(prev_e, nxt_e))
                    else:
                        merged.append((nxt_s, nxt_e))
                for ms, me in merged:
                    covered_sec += (me - ms)

        metrics.duration_coverage_ratio = round(min(1.0, covered_sec / total_gt_sec), 4)

    return metrics


# ==============================================================================
# 3. Overall Benchmark Evaluation & Aggregation
# ==============================================================================

@dataclass
class ClipEvaluationResult:
    clip_id: str
    scenario_category: str
    split: str
    annotation_status: str
    media_duration_seconds: float
    is_evaluated: bool
    skip_reason: Optional[str] = None
    frame_metrics: Optional[FrameEvaluationMetrics] = None
    event_metrics: Optional[EventEvaluationMetrics] = None


@dataclass
class BenchmarkEvaluationSummary:
    schema_version: int = 1
    benchmark_id: str = ""
    model_id: str = ""
    evaluated_at_utc: str = ""
    total_clips: int = 0
    evaluated_clips: int = 0
    skipped_clips: int = 0
    overall_frame_metrics: Dict[str, Any] = field(default_factory=dict)
    overall_event_metrics: Dict[str, Any] = field(default_factory=dict)
    per_split_metrics: Dict[str, Any] = field(default_factory=dict)
    per_scenario_metrics: Dict[str, Any] = field(default_factory=dict)
    clip_results: List[Dict[str, Any]] = field(default_factory=list)


def aggregate_frame_metrics(results: List[ClipEvaluationResult]) -> Dict[str, Any]:
    """Micro and macro aggregation across evaluated clips."""
    tp = sum(r.frame_metrics.true_positives for r in results if r.frame_metrics)
    fp = sum(r.frame_metrics.false_positives for r in results if r.frame_metrics)
    fn = sum(r.frame_metrics.false_negatives for r in results if r.frame_metrics)
    tn = sum(r.frame_metrics.true_negatives for r in results if r.frame_metrics)

    prec = round(float(tp) / float(tp + fp), 4) if (tp + fp) > 0 else None
    rec = round(float(tp) / float(tp + fn), 4) if (tp + fn) > 0 else None
    f1 = round(2.0 * prec * rec / (prec + rec), 4) if (prec is not None and rec is not None and (prec + rec) > 0) else None

    # Macro averages
    clip_precs = [r.frame_metrics.precision for r in results if r.frame_metrics and r.frame_metrics.precision is not None]
    clip_recs = [r.frame_metrics.recall for r in results if r.frame_metrics and r.frame_metrics.recall is not None]
    clip_f1s = [r.frame_metrics.f1 for r in results if r.frame_metrics and r.frame_metrics.f1 is not None]

    macro_p = round(sum(clip_precs) / len(clip_precs), 4) if clip_precs else None
    macro_r = round(sum(clip_recs) / len(clip_recs), 4) if clip_recs else None
    macro_f1 = round(sum(clip_f1s) / len(clip_f1s), 4) if clip_f1s else None

    return {
        "micro": {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "true_negatives": tn,
            "precision": prec,
            "recall": rec,
            "f1": f1,
        },
        "macro": {
            "precision": macro_p,
            "recall": macro_r,
            "f1": macro_f1,
        },
    }


def aggregate_event_metrics(results: List[ClipEvaluationResult]) -> Dict[str, Any]:
    """Micro and macro aggregation for event metrics."""
    gt_ev = sum(r.event_metrics.ground_truth_events for r in results if r.event_metrics)
    pred_ev = sum(r.event_metrics.predicted_events for r in results if r.event_metrics)
    tp = sum(r.event_metrics.true_positives for r in results if r.event_metrics)
    fp = sum(r.event_metrics.false_positives for r in results if r.event_metrics)
    fn = sum(r.event_metrics.false_negatives for r in results if r.event_metrics)

    prec = round(float(tp) / float(tp + fp), 4) if (tp + fp) > 0 else None
    rec = round(float(tp) / float(tp + fn), 4) if (tp + fn) > 0 else None
    f1 = round(2.0 * prec * rec / (prec + rec), 4) if (prec is not None and rec is not None and (prec + rec) > 0) else None

    tious = [r.event_metrics.mean_tiou for r in results if r.event_metrics and r.event_metrics.mean_tiou is not None]
    mean_tiou = round(sum(tious) / len(tious), 4) if tious else None

    fp_dur = sum(r.event_metrics.false_positive_duration_seconds for r in results if r.event_metrics)

    return {
        "ground_truth_events": gt_ev,
        "predicted_events": pred_ev,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": prec,
        "recall": rec,
        "f1": f1,
        "mean_tiou": mean_tiou,
        "total_false_positive_duration_seconds": round(fp_dur, 3),
    }


def evaluate_insect_benchmark(
    annotations: Dict[str, Any],
    predictions: Optional[Dict[str, Any]] = None,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    fps: float = DEFAULT_SAMPLING_FPS,
) -> BenchmarkEvaluationSummary:
    """Run full evaluation over all benchmark clips."""
    validate_annotation_data(annotations)
    if predictions:
        validate_prediction_data(predictions)

    preds_by_clip: Dict[str, Dict[str, Any]] = {}
    if predictions:
        for p in predictions.get("predictions", []):
            preds_by_clip[p["clip_id"]] = p

    clips = annotations.get("clips", [])
    results: List[ClipEvaluationResult] = []

    for clip in clips:
        cid = clip["clip_id"]
        status = clip.get("annotation_status", "unreviewed")
        reviewed = clip.get("reviewed_intervals", [])

        if status == "unreviewed" or not reviewed:
            results.append(
                ClipEvaluationResult(
                    clip_id=cid,
                    scenario_category=clip["scenario_category"],
                    split=clip["split"],
                    annotation_status=status,
                    media_duration_seconds=float(clip["media_duration_seconds"]),
                    is_evaluated=False,
                    skip_reason="No human-reviewed intervals available",
                )
            )
            continue

        pred = preds_by_clip.get(cid)
        thresh = pred.get("threshold", confidence_threshold) if pred else confidence_threshold

        frame_m = evaluate_clip_frames(clip, pred, threshold=thresh, fps=fps)
        event_m = evaluate_clip_events(
            clip, pred, iou_threshold=iou_threshold, confidence_threshold=thresh, fps=fps
        )

        results.append(
            ClipEvaluationResult(
                clip_id=cid,
                scenario_category=clip["scenario_category"],
                split=clip["split"],
                annotation_status=status,
                media_duration_seconds=float(clip["media_duration_seconds"]),
                is_evaluated=True,
                frame_metrics=frame_m,
                event_metrics=event_m,
            )
        )

    evaluated = [r for r in results if r.is_evaluated]
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

    summary = BenchmarkEvaluationSummary(
        schema_version=1,
        benchmark_id=annotations.get("benchmark_id", "unknown"),
        model_id=predictions.get("model_id", "none") if predictions else "no_predictions",
        evaluated_at_utc=now_utc,
        total_clips=len(clips),
        evaluated_clips=len(evaluated),
        skipped_clips=len(clips) - len(evaluated),
        overall_frame_metrics=aggregate_frame_metrics(evaluated),
        overall_event_metrics=aggregate_event_metrics(evaluated),
        clip_results=[asdict(r) for r in results],
    )

    # Per-split breakdown
    for split in VALID_SPLITS:
        split_eval = [r for r in evaluated if r.split == split]
        if split_eval:
            summary.per_split_metrics[split] = {
                "frame": aggregate_frame_metrics(split_eval),
                "event": aggregate_event_metrics(split_eval),
            }

    # Per-scenario breakdown
    for sc in VALID_SCENARIOS:
        sc_eval = [r for r in evaluated if r.scenario_category == sc]
        if sc_eval:
            summary.per_scenario_metrics[sc] = {
                "frame": aggregate_frame_metrics(sc_eval),
                "event": aggregate_event_metrics(sc_eval),
            }

    return summary


# ==============================================================================
# 4. Report Formatting
# ==============================================================================

def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "null"
    return f"{val * 100:.1f}%"


def _fmt_num(val: Optional[float], decimals: int = 3) -> str:
    if val is None:
        return "null"
    return f"{val:.{decimals}f}"


def format_insect_markdown_report(summary: BenchmarkEvaluationSummary) -> str:
    """Format evaluation summary as a GitHub-flavored Markdown report."""
    lines: List[str] = [
        f"# Insect Benchmark Evaluation Report: `{summary.model_id}`\n",
        f"- **Benchmark ID:** `{summary.benchmark_id}`",
        f"- **Evaluated At (UTC):** `{summary.evaluated_at_utc}`",
        f"- **Total Clips:** {summary.total_clips} ({summary.evaluated_clips} evaluated, {summary.skipped_clips} skipped)\n",
        "## 1. Overall Performance Metrics\n",
    ]

    fm = summary.overall_frame_metrics.get("micro", {})
    lines.extend([
        "### Frame-Level Classification (1.0 fps)",
        "| Metric | Value | Details |",
        "| :--- | :---: | :--- |",
        f"| **Precision** | **{_fmt_pct(fm.get('precision'))}** | TP={fm.get('true_positives', 0)}, FP={fm.get('false_positives', 0)} |",
        f"| **Recall** | **{_fmt_pct(fm.get('recall'))}** | TP={fm.get('true_positives', 0)}, FN={fm.get('false_negatives', 0)} |",
        f"| **F1 Score** | **{_fmt_pct(fm.get('f1'))}** | Harmonic mean of precision and recall |",
        f"| **True Negatives** | {fm.get('true_negatives', 0)} | Human-verified negative interval frames |",
        "",
    ])

    em = summary.overall_event_metrics
    lines.extend([
        "### Event-Level Detection (tIoU >= 0.3)",
        "| Metric | Value | Details |",
        "| :--- | :---: | :--- |",
        f"| **Event Precision** | {_fmt_pct(em.get('precision'))} | Matched events / total predicted events |",
        f"| **Event Recall** | {_fmt_pct(em.get('recall'))} | Matched events / ground truth events |",
        f"| **Event F1** | {_fmt_pct(em.get('f1'))} | Harmonic mean of event precision & recall |",
        f"| **Mean tIoU** | {_fmt_num(em.get('mean_tiou'))} | Mean overlap among true positives |",
        f"| **False Alarm Duration** | {em.get('total_false_positive_duration_seconds', 0.0):.1f}s | Cumulative unmasked false positive time |",
        "",
        "## 2. Per-Clip Results\n",
        "| Clip ID | Scenario | Split | Status | Frame P | Frame R | Frame F1 | Event TP | Event FP | Event FN |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    for cr in summary.clip_results:
        cid = cr["clip_id"]
        sc = cr["scenario_category"]
        sp = cr["split"]
        st = cr["annotation_status"]
        if cr["is_evaluated"]:
            fr = cr["frame_metrics"] or {}
            ev = cr["event_metrics"] or {}
            p_str = _fmt_pct(fr.get("precision"))
            r_str = _fmt_pct(fr.get("recall"))
            f_str = _fmt_pct(fr.get("f1"))
            lines.append(
                f"| `{cid}` | `{sc}` | {sp} | {st} | {p_str} | {r_str} | {f_str} | "
                f"{ev.get('true_positives', 0)} | {ev.get('false_positives', 0)} | {ev.get('false_negatives', 0)} |"
            )
        else:
            lines.append(f"| `{cid}` | `{sc}` | {sp} | {st} | *SKIPPED* | - | - | - | - | - |")

    return "\n".join(lines) + "\n"


# ==============================================================================
# 5. CLI Implementation
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Insect Benchmark Evaluation & Validation Harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate-annotations
    p_va = subparsers.add_parser("validate-annotations", help="Validate an insect benchmark annotation file")
    p_va.add_argument("file", type=Path, help="Path to annotation JSON file")

    # validate-predictions
    p_vp = subparsers.add_parser("validate-predictions", help="Validate an insect prediction file")
    p_vp.add_argument("file", type=Path, help="Path to prediction JSON file")

    # evaluate
    p_ev = subparsers.add_parser("evaluate", help="Evaluate predictions against ground-truth annotations")
    p_ev.add_argument("--annotations", "-a", type=Path, required=True, help="Path to annotations JSON file")
    p_ev.add_argument("--predictions", "-p", type=Path, required=False, help="Path to predictions JSON file")
    p_ev.add_argument("--output-json", type=Path, required=False, help="Path to output JSON summary")
    p_ev.add_argument("--output-md", type=Path, required=False, help="Path to output Markdown summary")
    p_ev.add_argument("--iou-threshold", type=float, default=DEFAULT_IOU_THRESHOLD, help="Temporal IoU threshold")
    p_ev.add_argument("--threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD, help="Confidence threshold")
    p_ev.add_argument("--fps", type=float, default=DEFAULT_SAMPLING_FPS, help="Frame sampling rate (fps)")

    args = parser.parse_args()

    if args.command == "validate-annotations":
        with args.file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        validate_annotation_data(data)
        print(f"[+] Successfully validated annotation file: {args.file} ({len(data.get('clips', []))} clips)")
        return 0

    elif args.command == "validate-predictions":
        with args.file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        validate_prediction_data(data)
        print(f"[+] Successfully validated prediction file: {args.file} (model: {data.get('model_id')})")
        return 0

    elif args.command == "evaluate":
        with args.annotations.open("r", encoding="utf-8") as f:
            anno_data = json.load(f)
        pred_data = None
        if args.predictions:
            with args.predictions.open("r", encoding="utf-8") as f:
                pred_data = json.load(f)

        summary = evaluate_insect_benchmark(
            annotations=anno_data,
            predictions=pred_data,
            iou_threshold=args.iou_threshold,
            confidence_threshold=args.threshold,
            fps=args.fps,
        )

        md_report = format_insect_markdown_report(summary)

        if args.output_json:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            with args.output_json.open("w", encoding="utf-8") as f:
                json.dump(asdict(summary), f, indent=2)
            print(f"[+] Written JSON evaluation report: {args.output_json}")

        if args.output_md:
            args.output_md.parent.mkdir(parents=True, exist_ok=True)
            with args.output_md.open("w", encoding="utf-8") as f:
                f.write(md_report)
            print(f"[+] Written Markdown evaluation report: {args.output_md}")

        if not args.output_json and not args.output_md:
            print(md_report)

        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
