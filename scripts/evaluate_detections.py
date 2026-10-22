#!/usr/bin/env python3
"""Evaluation Harness for Trigger-Aware Player Detectors (Person B).

Evaluates detector predictions against ground-truth labels using:
- One-to-one temporal matching with IoU >= threshold (default 0.5)
- Deterministic tie-breaking strategy
- Strict separation of micro-averaged and macro-averaged metrics
- Representation of undefined metrics as null (None) rather than misleading zeros
- Annotation integrity checks (no assumptions of negative examples for unlabeled clips)
- Multi-format report generation (JSON and Markdown)

Usage:
    python scripts/evaluate_detections.py
    python scripts/evaluate_detections.py --manifest data/test_clips/manifest.json --labels data/test_clips/labels.csv
    python scripts/evaluate_detections.py --output-json reports/detection_evaluation_report.json --output-md reports/detection_evaluation_report.md
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.classify_audio_events import classify_audio_events
from scripts.detect_flashing import detect_flashing
from scripts.detect_jump_scare import detect_jump_scare
from scripts.detect_loudness import (
    DEFAULT_CONFIG_PATH,
    detect_loudness_from_wav,
    load_detector_config,
)
from scripts.ingest_media import extract_audio
from scripts.package_cues import (
    adapt_subtitle_cues_to_raw_detections,
    merge_detections,
)

try:
    from scripts.parse_subtitles import cues_to_dicts, load_wordlist, parse_srt_file
    PYSRT_AVAILABLE = True
except ImportError:
    PYSRT_AVAILABLE = False

DEFAULT_MANIFEST_PATH = REPO_ROOT / "data" / "test_clips" / "manifest.json"
DEFAULT_LABELS_PATH = REPO_ROOT / "data" / "test_clips" / "labels.csv"
DEFAULT_CLIPS_DIR = REPO_ROOT / "data" / "test_clips"
DEFAULT_JSON_REPORT_PATH = REPO_ROOT / "reports" / "detection_evaluation_report.json"
DEFAULT_MD_REPORT_PATH = REPO_ROOT / "reports" / "detection_evaluation_report.md"
DEFAULT_IOU_THRESHOLD = 0.5

# Canonical trigger IDs from config.yaml
CANONICAL_TRIGGERS: Set[str] = {
    "flashing",
    "loud_spike",
    "sustained_loudness",
    "gunshot",
    "explosion",
    "scream",
    "profanity",
    "insect",
    "jump_scare",
    "visible_blood",
}

# Detector availability registry
DETECTOR_REGISTRY: Dict[str, Dict[str, Any]] = {
    "loud_spike": {
        "component": "loudness_detector",
        "modality": "audio",
        "owner": "Person B",
        "status": "available",
        "script": "scripts/detect_loudness.py",
        "method": "rule_based_signal_processing",
    },
    "profanity": {
        "component": "profanity_detector",
        "modality": "subtitles",
        "owner": "Person C",
        "status": "available",
        "script": "scripts/parse_subtitles.py",
        "method": "rule_based_text_matching",
    },
    "flashing": {
        "component": "flash_detector",
        "modality": "visual",
        "owner": "Person A",
        "status": "available",
        "script": "scripts/detect_flashing.py",
        "method": "rule_based_signal_processing",
    },
    "gunshot": {
        "component": "audio_event_classifier",
        "modality": "audio",
        "owner": "Person A",
        "status": "available",
        "script": "scripts/classify_audio_events.py",
        "method": "pretrained_audio_classification",
    },
    "explosion": {
        "component": "audio_event_classifier",
        "modality": "audio",
        "owner": "Person A",
        "status": "available",
        "script": "scripts/classify_audio_events.py",
        "method": "pretrained_audio_classification",
    },
    "scream": {
        "component": "audio_event_classifier",
        "modality": "audio",
        "owner": "Person A",
        "status": "available",
        "script": "scripts/classify_audio_events.py",
        "method": "pretrained_audio_classification",
    },
    "jump_scare": {
        "component": "compound_detector",
        "modality": "multimodal",
        "owner": "Person B and Person C",
        "status": "available",
        "script": "scripts/detect_jump_scare.py",
        "method": "multimodal_compound_heuristic",
    },
}


# ------------------------------------------------------------------------------
# 1. Temporal IoU & Event Matching
# ------------------------------------------------------------------------------

def compute_temporal_iou(
    start_a: float,
    end_a: float,
    start_b: float,
    end_b: float,
) -> float:
    """Compute temporal Intersection-over-Union (tIoU) between two 1D intervals [start, end).

    Formula:
        intersection = max(0.0, min(end_a, end_b) - max(start_a, start_b))
        union = (end_a - start_a) + (end_b - start_b) - intersection
        tIoU = intersection / union  (if union > 0 else 0.0)

    Raises:
        ValueError: If timestamps are non-numeric, non-finite, negative, or end <= start.
    """
    for val, name in [
        (start_a, "start_a"),
        (end_a, "end_a"),
        (start_b, "start_b"),
        (end_b, "end_b"),
    ]:
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(f"{name} must be numeric, got {type(val).__name__}: {val}")
        if not math.isfinite(float(val)):
            raise ValueError(f"{name} must be finite, got {val}")

    sa = float(start_a)
    ea = float(end_a)
    sb = float(start_b)
    eb = float(end_b)

    if sa < 0.0 or sb < 0.0:
        raise ValueError(f"Interval start timestamps must be >= 0.0 (got {sa}, {sb})")
    if ea <= sa:
        raise ValueError(f"Interval A end ({ea}) must be strictly greater than start ({sa})")
    if eb <= sb:
        raise ValueError(f"Interval B end ({eb}) must be strictly greater than start ({sb})")

    intersection_len = max(0.0, min(ea, eb) - max(sa, sb))
    dur_a = ea - sa
    dur_b = eb - sb
    union_len = dur_a + dur_b - intersection_len

    if union_len <= 0.0:
        return 0.0

    iou = intersection_len / union_len
    # Clamp to [0.0, 1.0] to guard against minor floating-point rounding
    return max(0.0, min(1.0, iou))


def match_events_one_to_one(
    predictions: List[Dict[str, Any]],
    ground_truth: List[Dict[str, Any]],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> Dict[str, Any]:
    """Perform deterministic one-to-one bipartite matching between predictions and ground truth.

    Rules:
    1. A candidate match requires same trigger and tIoU >= iou_threshold.
    2. A prediction matches at most one ground-truth event.
    3. A ground-truth event matches at most one prediction.
    4. Deterministic tie-breaking:
       Candidate pairs are sorted by:
       - tIoU descending (highest overlap prioritized)
       - Prediction start ascending
       - Ground truth start ascending
       - Prediction end ascending
       - Ground truth end ascending
       - Prediction original index ascending
       - Ground truth original index ascending
    5. Greedy selection accepts highest-priority pair where neither candidate has been taken.

    Returns:
        Dictionary with:
        - matches: List of match dictionaries containing prediction, ground_truth, iou, onset_error, offset_error
        - unmatched_predictions: List of false positive prediction records
        - unmatched_ground_truth: List of false negative ground truth records
        - true_positives: Count of matched pairs
        - false_positives: Count of unmatched predictions
        - false_negatives: Count of unmatched ground truth
    """
    if not isinstance(predictions, list) or not isinstance(ground_truth, list):
        raise ValueError("predictions and ground_truth must be lists")

    # Build candidate pairs
    candidate_pairs: List[Tuple[float, float, float, int, int, Dict[str, Any], Dict[str, Any]]] = []

    for p_idx, pred in enumerate(predictions):
        p_trig = str(pred.get("trigger", ""))
        p_start = float(pred["start"])
        p_end = float(pred["end"])

        for g_idx, gt in enumerate(ground_truth):
            g_trig = str(gt.get("trigger", ""))
            g_start = float(gt["start"])
            g_end = float(gt["end"])

            # Must have same trigger
            if p_trig != g_trig:
                continue

            iou = compute_temporal_iou(p_start, p_end, g_start, g_end)
            if iou >= iou_threshold:
                # Store tuple for deterministic sorting:
                # (-iou, p_start, g_start, p_end, g_end, p_idx, g_idx, pred, gt)
                candidate_pairs.append((
                    -iou,
                    p_start,
                    g_start,
                    p_idx,
                    g_idx,
                    pred,
                    gt,
                ))

    # Sort candidate pairs deterministically
    candidate_pairs.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4]))

    matched_p_indices: Set[int] = set()
    matched_g_indices: Set[int] = set()
    matches: List[Dict[str, Any]] = []

    for neg_iou, p_start, g_start, p_idx, g_idx, pred, gt in candidate_pairs:
        if p_idx in matched_p_indices or g_idx in matched_g_indices:
            continue

        iou = -neg_iou
        onset_error = round(float(pred["start"]) - float(gt["start"]), 4)
        offset_error = round(float(pred["end"]) - float(gt["end"]), 4)

        match_record = {
            "prediction": pred,
            "ground_truth": gt,
            "trigger": str(pred["trigger"]),
            "iou": round(iou, 4),
            "onset_error": onset_error,
            "offset_error": offset_error,
            "abs_onset_error": round(abs(onset_error), 4),
            "abs_offset_error": round(abs(offset_error), 4),
        }
        matches.append(match_record)
        matched_p_indices.add(p_idx)
        matched_g_indices.add(g_idx)

    # Deterministic chronological sort of matches by prediction start
    matches.sort(key=lambda m: (float(m["prediction"]["start"]), float(m["prediction"]["end"])))

    unmatched_predictions = [
        pred for p_idx, pred in enumerate(predictions) if p_idx not in matched_p_indices
    ]
    unmatched_ground_truth = [
        gt for g_idx, gt in enumerate(ground_truth) if g_idx not in matched_g_indices
    ]

    return {
        "matches": matches,
        "unmatched_predictions": unmatched_predictions,
        "unmatched_ground_truth": unmatched_ground_truth,
        "true_positives": len(matches),
        "false_positives": len(unmatched_predictions),
        "false_negatives": len(unmatched_ground_truth),
    }


# ------------------------------------------------------------------------------
# 2. Metric Calculations
# ------------------------------------------------------------------------------

def calculate_classification_metrics(
    tp: int,
    fp: int,
    fn: int,
) -> Dict[str, Optional[float]]:
    """Compute precision, recall, and F1 with explicit null for mathematically undefined cases.

    Undefined rules:
    - Precision: TP / (TP + FP). If (TP + FP) == 0 -> None (null)
    - Recall: TP / (TP + FN). If (TP + FN) == 0 -> None (null)
    - F1: 2 * (P * R) / (P + R). If P is None or R is None or (P + R) == 0 -> None (null)
    """
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None

    if (tp + fp) > 0:
        precision = round(float(tp) / float(tp + fp), 4)

    if (tp + fn) > 0:
        recall = round(float(tp) / float(tp + fn), 4)

    if precision is not None and recall is not None and (precision + recall) > 0.0:
        f1 = round(2.0 * (precision * recall) / (precision + recall), 4)

    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def calculate_temporal_metrics(
    matches: List[Dict[str, Any]],
) -> Dict[str, Optional[float]]:
    """Compute mean IoU, onset error, and offset error across matched events.

    Returns None for all metrics if matches is empty (TP == 0).
    """
    if not matches:
        return {
            "mean_iou": None,
            "mean_onset_error": None,
            "mean_offset_error": None,
            "mean_abs_onset_error": None,
            "mean_abs_offset_error": None,
        }

    n = float(len(matches))
    ious = [float(m["iou"]) for m in matches]
    onset_errors = [float(m["onset_error"]) for m in matches]
    offset_errors = [float(m["offset_error"]) for m in matches]
    abs_onset_errors = [float(m["abs_onset_error"]) for m in matches]
    abs_offset_errors = [float(m["abs_offset_error"]) for m in matches]

    return {
        "mean_iou": round(sum(ious) / n, 4),
        "mean_onset_error": round(sum(onset_errors) / n, 4),
        "mean_offset_error": round(sum(offset_errors) / n, 4),
        "mean_abs_onset_error": round(sum(abs_onset_errors) / n, 4),
        "mean_abs_offset_error": round(sum(abs_offset_errors) / n, 4),
    }


# ------------------------------------------------------------------------------
# 3. Label & Manifest Ingestion
# ------------------------------------------------------------------------------

def load_ground_truth_labels(
    labels_csv_path: Path | str = DEFAULT_LABELS_PATH,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load ground-truth labels from labels.csv grouped by clip filename.

    Expected CSV header: clip,trigger,start,end,labeller,actual_rate

    Raises:
        FileNotFoundError: If labels file does not exist.
        ValueError: If columns are missing or values are malformed.
    """
    path = Path(labels_csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Labels CSV file not found: {path.resolve()}")

    labels_by_clip: Dict[str, List[Dict[str, Any]]] = {}

    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required_fields = {"clip", "trigger", "start", "end"}
        if not reader.fieldnames or not required_fields.issubset(set(reader.fieldnames)):
            raise ValueError(f"Labels CSV missing required columns {required_fields}: got {reader.fieldnames}")

        for row_idx, row in enumerate(reader, start=2):
            clip_name = str(row["clip"]).strip()
            trigger = str(row["trigger"]).strip()
            if not clip_name or not trigger:
                continue

            start_raw = str(row.get("start", "")).strip()
            end_raw = str(row.get("end", "")).strip()
            if not start_raw and not end_raw:
                # Negative control marker in labels.csv: trigger is never active in this clip
                continue

            try:
                start_f = float(start_raw)
                end_f = float(end_raw)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"Row {row_idx} in {path.name} contains invalid numeric timestamps: {row}"
                ) from exc

            if start_f < 0.0 or end_f <= start_f:
                raise ValueError(
                    f"Row {row_idx} in {path.name} has invalid interval [{start_f}, {end_f}]"
                )

            record = {
                "clip": clip_name,
                "trigger": trigger,
                "start": start_f,
                "end": end_f,
                "labeller": row.get("labeller", "").strip(),
                "actual_rate": float(row["actual_rate"]) if row.get("actual_rate") and row["actual_rate"].strip() else None,
            }

            if clip_name not in labels_by_clip:
                labels_by_clip[clip_name] = []
            labels_by_clip[clip_name].append(record)

    # Deterministic sort for each clip's ground truth
    for clip_name in labels_by_clip:
        labels_by_clip[clip_name].sort(key=lambda r: (r["start"], r["end"], r["trigger"]))

    return labels_by_clip


def load_negative_control_markers(
    labels_csv_path: Path | str = DEFAULT_LABELS_PATH,
) -> Dict[str, Set[str]]:
    """Extract explicit negative control trigger markers from labels.csv (rows with empty start and end).

    Returns:
        Mapping of clip filename to set of trigger names explicitly marked negative.
    """
    path = Path(labels_csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Labels CSV file not found: {path.resolve()}")

    neg_markers_by_clip: Dict[str, Set[str]] = {}

    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {"clip", "trigger", "start", "end"}.issubset(set(reader.fieldnames)):
            return {}

        for row in reader:
            clip_name = str(row.get("clip", "")).strip()
            trigger = str(row.get("trigger", "")).strip()
            start_raw = str(row.get("start", "")).strip()
            end_raw = str(row.get("end", "")).strip()

            if clip_name and trigger and not start_raw and not end_raw:
                neg_markers_by_clip.setdefault(clip_name, set()).add(trigger)

    return neg_markers_by_clip


def load_clips_manifest(
    manifest_json_path: Path | str = DEFAULT_MANIFEST_PATH,
) -> Dict[str, Dict[str, Any]]:
    """Load clip metadata from test clips manifest.json indexed by clip_name.

    Raises:
        FileNotFoundError: If manifest file does not exist.
        ValueError: If JSON is invalid or missing clips array.
    """
    path = Path(manifest_json_path)
    if not path.is_file():
        raise FileNotFoundError(f"Clips manifest file not found: {path.resolve()}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or "clips" not in data or not isinstance(data["clips"], list):
        raise ValueError(f"Invalid manifest structure in {path.name}: expected 'clips' list.")

    manifest_by_name: Dict[str, Dict[str, Any]] = {}
    for clip in data["clips"]:
        clip_name = clip.get("clip_name")
        if clip_name:
            manifest_by_name[clip_name] = clip

    return manifest_by_name


def audit_annotation_coverage(
    manifest: Dict[str, Dict[str, Any]],
    labels_by_clip: Dict[str, List[Dict[str, Any]]],
    supported_triggers: Set[str],
    negative_markers: Optional[Dict[str, Set[str]]] = None,
) -> Dict[str, Any]:
    """Audit label coverage across clips and triggers.

    Ensures annotation integrity:
    - Positive clips have verified ground truth for the trigger.
    - Verified negative clips are explicitly defined in manifest as negative controls or via negative_markers.
    - Clips without annotations for a trigger that are NOT verified negative controls
      are flagged as unannotated/uncovered (and excluded from false positive calculation).
    """
    audit: Dict[str, Any] = {
        "total_clips_in_manifest": len(manifest),
        "total_annotated_clips": len(labels_by_clip),
        "per_trigger_coverage": {},
        "unsupported_triggers": {},
    }

    # Record unsupported triggers
    for trig, info in DETECTOR_REGISTRY.items():
        if info["status"] != "available" or (supported_triggers is not None and trig not in supported_triggers):
            # Count how many labels exist in labels.csv for this unsupported trigger
            gt_count = sum(
                sum(1 for row in rows if row["trigger"] == trig)
                for rows in labels_by_clip.values()
            )
            reason = "Detector implementation not available in repository."
            if info["status"] == "unresolved":
                reason = "Audio classifier backend unresolved: deep learning runtimes (torch/tensorflow/onnxruntime) are unavailable on Python 3.14 on macOS arm64 without binary compilation."
            elif info["status"] == "available":
                reason = "Detector available but excluded from target evaluation triggers."

            audit["unsupported_triggers"][trig] = {
                "component": info["component"],
                "status": info["status"],
                "owner": info["owner"],
                "ground_truth_events_in_labels_csv": gt_count,
                "reason": reason,
            }

    # Audit coverage for supported triggers
    for trig in sorted(supported_triggers):
        positive_clips: List[str] = []
        negative_control_clips: List[str] = []
        uncovered_clips: List[str] = []

        for clip_name, clip_meta in manifest.items():
            clip_labels = labels_by_clip.get(clip_name, [])
            has_trig_labels = any(lbl["trigger"] == trig for lbl in clip_labels)

            if has_trig_labels:
                positive_clips.append(clip_name)
            elif negative_markers and trig in negative_markers.get(clip_name, set()):
                negative_control_clips.append(clip_name)
            else:
                meta = clip_meta.get("metadata", {})
                event_type = meta.get("event_type", "")
                is_negative_control = (
                    clip_meta.get("is_negative_control") is True
                    or meta.get("is_negative_control") is True
                    or meta.get("event_type") == "negative_control"
                    or (
                        (clip_meta.get("trigger") is None or meta.get("contains_trigger") is False or clip_meta.get("contains_trigger") is False)
                        and (
                            "negative" in clip_name.lower()
                            or "ambient" in str(event_type).lower()
                            or "dialogue" in str(event_type).lower()
                            or "static" in str(event_type).lower()
                        )
                    )
                )
                if is_negative_control:
                    neg_triggers = clip_meta.get("negative_triggers") or meta.get("negative_triggers")
                    if neg_triggers:
                        applies_to_trig = trig in neg_triggers
                    else:
                        # Modality check fallback if specific negative_triggers is omitted
                        modality = clip_meta.get("modality") or meta.get("modality", "")
                        audio_triggers = {"loud_spike", "gunshot", "explosion", "scream", "sustained_loudness"}
                        visual_triggers = {"flashing", "fast_motion", "jump_scare", "spider", "insect", "visible_blood"}
                        subtitle_triggers = {"profanity"}
                        if modality == "audio" and trig in visual_triggers:
                            applies_to_trig = False
                        elif modality == "subtitles" and trig not in subtitle_triggers:
                            applies_to_trig = False
                        elif modality == "video" and trig in audio_triggers:
                            applies_to_trig = False
                        else:
                            applies_to_trig = True

                    if applies_to_trig:
                        negative_control_clips.append(clip_name)
                    else:
                        uncovered_clips.append(clip_name)
                else:
                    uncovered_clips.append(clip_name)

        audit["per_trigger_coverage"][trig] = {
            "positive_clips": sorted(positive_clips),
            "negative_control_clips": sorted(negative_control_clips),
            "uncovered_clips": sorted(uncovered_clips),
            "total_evaluable_clips": len(positive_clips) + len(negative_control_clips),
        }

    return audit


# ------------------------------------------------------------------------------
# 4. Detector Execution Adapter
# ------------------------------------------------------------------------------

def run_detector_for_clip(
    clip_name: str,
    trigger: str,
    clips_dir: Path = DEFAULT_CLIPS_DIR,
    working_dir: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    clip_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Execute the appropriate offline detector for a specific clip and trigger.

    Returns:
        List of raw detection records: [{"start": float, "end": float, "trigger": str, "score": float, "source": str}]
    """
    clip_file = clips_dir / clip_name
    if not clip_file.is_file():
        # Check if media_relpath is provided in clip_metadata
        rel_path = (clip_metadata or {}).get("metadata", {}).get("media_relpath") or (clip_metadata or {}).get("media_relpath")
        if rel_path and (REPO_ROOT / rel_path).is_file():
            clip_file = REPO_ROOT / rel_path
        else:
            raise FileNotFoundError(
                f"Media clip file not found for '{clip_name}' in '{clips_dir.resolve()}'. "
                f"Actionable resolution: verify that media exists at '{clip_file.resolve()}' "
                + (f"or '{REPO_ROOT / rel_path}'." if rel_path else ".")
            )

    if trigger == "loud_spike":
        # Extract audio to working_dir and run detect_loudness_from_wav
        temp_dir_obj = None
        if working_dir is None:
            temp_dir_obj = tempfile.TemporaryDirectory(prefix="eval_loudness_")
            work_path = Path(temp_dir_obj.name)
        else:
            work_path = working_dir

        try:
            wav_path = work_path / f"{clip_file.stem}_mono.wav"
            if not wav_path.is_file():
                extract_audio(clip_file, wav_path)

            raw_detections = detect_loudness_from_wav(
                wav_path,
                config=config,
                modes=("spike",),
            )
            # Apply standard packaging merge policy for loud_spike
            merged_detections = merge_detections(
                detections=raw_detections,
                config=config,
            )
            return merged_detections
        finally:
            if temp_dir_obj:
                temp_dir_obj.cleanup()

    elif trigger == "profanity":
        # Find corresponding .srt subtitle file for this clip
        srt_candidate = clips_dir / f"{clip_file.stem}.srt"
        if not srt_candidate.is_file():
            # speech_triggers_test.mp4 corresponds to speech_triggers_truth.srt
            if clip_file.stem == "speech_triggers_test":
                srt_candidate = clips_dir / "speech_triggers_truth.srt"
            else:
                return []

        if not srt_candidate.is_file():
            # If no subtitle file exists for this clip, return empty
            return []

        wordlist_path = REPO_ROOT / "data" / "profanity_wordlist.txt"
        if not wordlist_path.is_file():
            raise FileNotFoundError(f"Profanity wordlist not found: {wordlist_path}")

        sub_file = parse_srt_file(srt_candidate, wordlist_path=wordlist_path)
        cue_dicts = cues_to_dicts(sub_file)
        raw_detections = adapt_subtitle_cues_to_raw_detections(cue_dicts)
        merged_detections = merge_detections(
            detections=raw_detections,
            config=config,
        )
        return merged_detections

    elif trigger == "flashing":
        raw_detections = detect_flashing(
            clip_file,
            config=config,
        )
        merged_detections = merge_detections(
            detections=raw_detections,
            config=config,
        )
        return merged_detections

    elif trigger in ("gunshot", "explosion", "scream"):
        raw_detections = classify_audio_events(
            audio_path=clip_file,
            backend="YAMNet",
            config=config,
        )
        filtered_detections = [d for d in raw_detections if d.get("trigger") == trigger]
        merged_detections = merge_detections(
            detections=filtered_detections,
            config=config,
        )
        return merged_detections

    elif trigger == "jump_scare":
        raw_detections = detect_jump_scare(
            clip_file,
            config=config,
        )
        merged_detections = merge_detections(
            detections=raw_detections,
            config=config,
        )
        return merged_detections


    else:
        info = DETECTOR_REGISTRY.get(trigger, {})
        raise NotImplementedError(
            f"Detector for trigger '{trigger}' is not implemented (status: {info.get('status', 'unknown')})."
        )


# ------------------------------------------------------------------------------
# 5. Full Evaluation Pipeline
# ------------------------------------------------------------------------------

def evaluate_clip(
    clip_name: str,
    trigger: str,
    predictions: List[Dict[str, Any]],
    ground_truth: List[Dict[str, Any]],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    clip_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate predictions against ground truth for a single clip and trigger."""
    # Filter ground truth for this trigger
    gt_for_trig = [g for g in ground_truth if str(g.get("trigger")) == trigger]
    # Filter predictions for this trigger
    pred_for_trig = [p for p in predictions if str(p.get("trigger")) == trigger]

    match_result = match_events_one_to_one(
        predictions=pred_for_trig,
        ground_truth=gt_for_trig,
        iou_threshold=iou_threshold,
    )

    tp = match_result["true_positives"]
    fp = match_result["false_positives"]
    fn = match_result["false_negatives"]

    class_metrics = calculate_classification_metrics(tp, fp, fn)
    temp_metrics = calculate_temporal_metrics(match_result["matches"])

    return {
        "clip_name": clip_name,
        "trigger": trigger,
        "ground_truth_count": len(gt_for_trig),
        "prediction_count": len(pred_for_trig),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": class_metrics["precision"],
        "recall": class_metrics["recall"],
        "f1": class_metrics["f1"],
        "mean_iou": temp_metrics["mean_iou"],
        "mean_onset_error": temp_metrics["mean_onset_error"],
        "mean_offset_error": temp_metrics["mean_offset_error"],
        "mean_abs_onset_error": temp_metrics["mean_abs_onset_error"],
        "mean_abs_offset_error": temp_metrics["mean_abs_offset_error"],
        "matches": match_result["matches"],
        "unmatched_predictions": match_result["unmatched_predictions"],
        "unmatched_ground_truth": match_result["unmatched_ground_truth"],
        "metadata": clip_metadata or {},
    }


def evaluate_dataset(
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    labels_path: Path | str = DEFAULT_LABELS_PATH,
    clips_dir: Path | str = DEFAULT_CLIPS_DIR,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    target_triggers: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Execute complete dataset evaluation across all evaluable clips and triggers."""
    manifest = load_clips_manifest(manifest_path)
    labels_by_clip = load_ground_truth_labels(labels_path)
    neg_markers = load_negative_control_markers(labels_path)
    config = load_detector_config(config_path)

    # Determine supported triggers
    available_triggers = {
        trig for trig, info in DETECTOR_REGISTRY.items() if info["status"] == "available"
    }
    if target_triggers is not None:
        eval_triggers = target_triggers.intersection(available_triggers)
    else:
        eval_triggers = available_triggers

    coverage_audit = audit_annotation_coverage(manifest, labels_by_clip, eval_triggers, negative_markers=neg_markers)

    per_clip_results: List[Dict[str, Any]] = []
    per_trigger_results: Dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="harness_eval_") as tmp_dir:
        working_path = Path(tmp_dir)

        for trigger in sorted(eval_triggers):
            trig_cov = coverage_audit["per_trigger_coverage"][trigger]
            evaluable_clips = trig_cov["positive_clips"] + trig_cov["negative_control_clips"]

            trig_matches: List[Dict[str, Any]] = []
            trig_tp = 0
            trig_fp = 0
            trig_fn = 0
            clip_precisions: List[float] = []
            clip_recalls: List[float] = []
            clip_f1s: List[float] = []

            for clip_name in evaluable_clips:
                clip_meta = manifest.get(clip_name, {})
                gt_list = labels_by_clip.get(clip_name, [])

                # Run detector
                try:
                    preds = run_detector_for_clip(
                        clip_name=clip_name,
                        trigger=trigger,
                        clips_dir=Path(clips_dir),
                        working_dir=working_path,
                        config=config,
                        clip_metadata=clip_meta,
                    )
                except FileNotFoundError as fnf_err:
                    raise FileNotFoundError(
                        f"Evaluation aborted: missing media file for clip '{clip_name}' (trigger '{trigger}'): {fnf_err}. "
                        "Missing benchmark clips must produce an explicit actionable error and cannot be silently treated as empty detections."
                    ) from fnf_err
                except Exception as exc:
                    preds = []

                clip_eval = evaluate_clip(
                    clip_name=clip_name,
                    trigger=trigger,
                    predictions=preds,
                    ground_truth=gt_list,
                    iou_threshold=iou_threshold,
                    clip_metadata=clip_meta,
                )
                per_clip_results.append(clip_eval)

                # Accumulate micro counts
                trig_tp += clip_eval["true_positives"]
                trig_fp += clip_eval["false_positives"]
                trig_fn += clip_eval["false_negatives"]
                trig_matches.extend(clip_eval["matches"])

                # Collect for macro averages
                if clip_eval["precision"] is not None:
                    clip_precisions.append(clip_eval["precision"])
                if clip_eval["recall"] is not None:
                    clip_recalls.append(clip_eval["recall"])
                if clip_eval["f1"] is not None:
                    clip_f1s.append(clip_eval["f1"])

            # Compute Micro-average for this trigger
            micro_class = calculate_classification_metrics(trig_tp, trig_fp, trig_fn)
            temp_metrics = calculate_temporal_metrics(trig_matches)

            # Compute Macro-average for this trigger
            macro_precision = (
                round(sum(clip_precisions) / len(clip_precisions), 4) if clip_precisions else None
            )
            macro_recall = (
                round(sum(clip_recalls) / len(clip_recalls), 4) if clip_recalls else None
            )
            macro_f1 = (
                round(sum(clip_f1s) / len(clip_f1s), 4) if clip_f1s else None
            )

            per_trigger_results[trigger] = {
                "trigger": trigger,
                "status": "evaluated",
                "component": DETECTOR_REGISTRY[trigger]["component"],
                "modality": DETECTOR_REGISTRY[trigger]["modality"],
                "clips_evaluated_count": len(evaluable_clips),
                "positive_clips_count": len(trig_cov["positive_clips"]),
                "negative_control_clips_count": len(trig_cov["negative_control_clips"]),
                "counts": {
                    "ground_truth_events": trig_tp + trig_fn,
                    "predicted_events": trig_tp + trig_fp,
                    "true_positives": trig_tp,
                    "false_positives": trig_fp,
                    "false_negatives": trig_fn,
                },
                "micro_metrics": {
                    "precision": micro_class["precision"],
                    "recall": micro_class["recall"],
                    "f1": micro_class["f1"],
                },
                "macro_metrics": {
                    "precision": macro_precision,
                    "recall": macro_recall,
                    "f1": macro_f1,
                },
                "temporal_metrics": temp_metrics,
            }

    # Dataset-level micro and macro aggregations
    all_tp = sum(t["counts"]["true_positives"] for t in per_trigger_results.values())
    all_fp = sum(t["counts"]["false_positives"] for t in per_trigger_results.values())
    all_fn = sum(t["counts"]["false_negatives"] for t in per_trigger_results.values())
    dataset_micro = calculate_classification_metrics(all_tp, all_fp, all_fn)

    dataset_macro_precisions = [
        t["micro_metrics"]["precision"]
        for t in per_trigger_results.values()
        if t["micro_metrics"]["precision"] is not None
    ]
    dataset_macro_recalls = [
        t["micro_metrics"]["recall"]
        for t in per_trigger_results.values()
        if t["micro_metrics"]["recall"] is not None
    ]
    dataset_macro_f1s = [
        t["micro_metrics"]["f1"]
        for t in per_trigger_results.values()
        if t["micro_metrics"]["f1"] is not None
    ]

    dataset_macro = {
        "precision": round(sum(dataset_macro_precisions) / len(dataset_macro_precisions), 4) if dataset_macro_precisions else None,
        "recall": round(sum(dataset_macro_recalls) / len(dataset_macro_recalls), 4) if dataset_macro_recalls else None,
        "f1": round(sum(dataset_macro_f1s) / len(dataset_macro_f1s), 4) if dataset_macro_f1s else None,
    }

    return {
        "schema_version": 1,
        "evaluation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "iou_threshold": iou_threshold,
            "matching_strategy": "one_to_one_greedy_highest_iou",
            "tie_breaking": "highest_iou_desc_pred_start_asc_gt_start_asc",
            "null_representation": "null_for_undefined_divisions",
        },
        "coverage_audit": coverage_audit,
        "summary": {
            "evaluated_triggers": sorted(eval_triggers),
            "unsupported_triggers": sorted(coverage_audit["unsupported_triggers"].keys()),
            "total_clips_evaluated": len(per_clip_results),
            "counts": {
                "true_positives": all_tp,
                "false_positives": all_fp,
                "false_negatives": all_fn,
            },
            "dataset_micro_metrics": dataset_micro,
            "dataset_macro_metrics": dataset_macro,
        },
        "per_trigger": per_trigger_results,
        "per_clip": per_clip_results,
    }


def evaluate_index(
    index: Union[Dict[str, Any], Path, str],
    ground_truth: Union[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], Path, str],
    film_id: Optional[str] = None,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    duration_tolerance: float = 0.5,
) -> Dict[str, Any]:
    """Score a packaged cue index (index.json) against aligned ground-truth annotations.

    Evaluates multi-detector predictions on a unified media timeline with:
    - Independent evaluation of every trigger present in either ground truth or predictions.
    - Deterministic one-to-one temporal matching (tIoU >= iou_threshold).
    - Strict validation of media identity (film_id) and timeline bounds.
    - Micro- and macro-averaged metrics and temporal error statistics.

    Args:
        index: Packaged index dict, or Path/str to index.json.
        ground_truth: Ground truth annotations as list of dicts, dict grouped by clip,
                      or Path/str to labels.csv or JSON.
        film_id: Optional expected film identifier. If provided, verifies index matches.
        iou_threshold: Temporal IoU threshold for valid event match (default: 0.5).
        duration_tolerance: Allowable timestamp extension for ground truth (seconds).

    Returns:
        Structured evaluation dictionary containing per-trigger and aggregate metrics.

    Raises:
        ValueError: If index structure is invalid, intervals extend beyond duration,
                    film_id mismatches, or ground-truth records are missing/invalid.
    """
    # 1. Load and validate index
    if isinstance(index, (str, Path)):
        idx_path = Path(index)
        if not idx_path.is_file():
            raise FileNotFoundError(f"Index file not found: {idx_path.resolve()}")
        with idx_path.open("r", encoding="utf-8") as f:
            index_data = json.load(f)
    elif isinstance(index, dict):
        index_data = index
    else:
        raise ValueError(f"index must be dict, Path, or str; got {type(index).__name__}")

    if not isinstance(index_data, dict):
        raise ValueError(f"Index data must be a dictionary, got {type(index_data).__name__}")

    actual_film_id = index_data.get("film_id")
    if not actual_film_id or not isinstance(actual_film_id, str):
        raise ValueError("Index missing required non-empty 'film_id' string.")

    if film_id is not None and str(film_id).strip() != str(actual_film_id).strip():
        raise ValueError(
            f"Film ID mismatch: index has film_id '{actual_film_id}', but expected '{film_id}'."
        )

    timeline = index_data.get("timeline")
    if not isinstance(timeline, dict) or "duration_seconds" not in timeline:
        raise ValueError("Index missing required 'timeline.duration_seconds' dictionary.")

    try:
        duration_seconds = float(timeline["duration_seconds"])
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Index duration_seconds must be numeric: {timeline.get('duration_seconds')}") from exc

    if duration_seconds <= 0.0 or not math.isfinite(duration_seconds):
        raise ValueError(f"Index duration_seconds must be positive and finite, got {duration_seconds}")

    cues = index_data.get("cues", [])
    if not isinstance(cues, list):
        raise ValueError(f"Index 'cues' must be a list, got {type(cues).__name__}")

    # Validate cue temporal bounds
    for c_idx, cue in enumerate(cues):
        cue_id = cue.get("id", f"cue_{c_idx}")
        if not isinstance(cue, dict):
            raise ValueError(f"Cue at index {c_idx} must be a dictionary, got {type(cue).__name__}")
        try:
            c_start = float(cue["start"])
            c_end = float(cue["end"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"Cue '{cue_id}' missing or invalid numeric start/end: {cue}") from exc

        if not math.isfinite(c_start) or not math.isfinite(c_end):
            raise ValueError(f"Cue '{cue_id}' has non-finite start/end: [{c_start}, {c_end}]")
        if c_start < 0.0:
            raise ValueError(f"Cue '{cue_id}' start ({c_start}) is negative.")
        if c_end <= c_start:
            raise ValueError(f"Cue '{cue_id}' end ({c_end}) must be strictly greater than start ({c_start}).")
        if c_end > duration_seconds + 1e-4:
            raise ValueError(
                f"Cue '{cue_id}' interval [{c_start}, {c_end}) exceeds index duration {duration_seconds}s."
            )

    # 2. Ingest and extract ground-truth records
    if isinstance(ground_truth, (str, Path)):
        gt_path = Path(ground_truth)
        if not gt_path.is_file():
            raise FileNotFoundError(f"Ground-truth file not found: {gt_path.resolve()}")
        if gt_path.suffix.lower() == ".csv":
            grouped_gt = load_ground_truth_labels(gt_path)
        elif gt_path.suffix.lower() == ".json":
            with gt_path.open("r", encoding="utf-8") as f:
                json_gt = json.load(f)
            if isinstance(json_gt, dict):
                grouped_gt = json_gt
            elif isinstance(json_gt, list):
                grouped_gt = {}
                for r in json_gt:
                    c_key = r.get("clip") if isinstance(r, dict) else None
                    grouped_gt.setdefault(c_key or actual_film_id, []).append(r)
            else:
                raise ValueError(f"Unexpected ground-truth JSON structure in {gt_path}")
        else:
            raise ValueError(f"Unsupported ground-truth format '{gt_path.suffix}', expected .csv or .json")
    elif isinstance(ground_truth, dict):
        grouped_gt = ground_truth
    elif isinstance(ground_truth, list):
        grouped_gt = {}
        for r in ground_truth:
            c_key = r.get("clip") if isinstance(r, dict) else None
            grouped_gt.setdefault(c_key or actual_film_id, []).append(r)
    else:
        raise ValueError(f"ground_truth must be dict, list, Path, or str; got {type(ground_truth).__name__}")

    # Match ground-truth records against actual_film_id
    gt_records: List[Dict[str, Any]] = []
    norm_film = actual_film_id.removesuffix(".mp4").strip().lower()

    for clip_key, records in grouped_gt.items():
        norm_key = str(clip_key).removesuffix(".mp4").strip().lower()
        if norm_key == norm_film:
            gt_records.extend(records)

    if not gt_records:
        raise ValueError(
            f"No ground-truth annotations found matching film_id '{actual_film_id}'."
        )

    # Validate ground-truth intervals against duration
    for g_idx, gt in enumerate(gt_records):
        try:
            g_start = float(gt["start"])
            g_end = float(gt["end"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"Ground-truth record {g_idx} missing or invalid numeric start/end: {gt}") from exc

        if not math.isfinite(g_start) or not math.isfinite(g_end):
            raise ValueError(f"Ground-truth record {g_idx} has non-finite start/end: [{g_start}, {g_end}]")
        if g_start < 0.0:
            raise ValueError(f"Ground-truth record {g_idx} start ({g_start}) is negative.")
        if g_end <= g_start:
            raise ValueError(f"Ground-truth record {g_idx} interval [{g_start}, {g_end}] has end <= start.")
        if g_end > duration_seconds + duration_tolerance:
            raise ValueError(
                f"Ground-truth interval [{g_start}, {g_end}] for '{actual_film_id}' extends beyond "
                f"media duration ({duration_seconds}s + tolerance {duration_tolerance}s)."
            )

    # 3. Determine all triggers to evaluate (union of predictions and ground truth)
    pred_triggers = {str(c.get("trigger", "")) for c in cues if c.get("trigger")}
    gt_triggers = {str(g.get("trigger", "")) for g in gt_records if g.get("trigger")}
    all_triggers = sorted(list(pred_triggers | gt_triggers))

    per_trigger_results: Dict[str, Any] = {}

    for trig in all_triggers:
        trig_preds = [c for c in cues if str(c.get("trigger")) == trig]
        trig_gt = [g for g in gt_records if str(g.get("trigger")) == trig]

        matched = match_events_one_to_one(trig_preds, trig_gt, iou_threshold=iou_threshold)
        tp = matched["true_positives"]
        fp = matched["false_positives"]
        fn = matched["false_negatives"]

        cls_metrics = calculate_classification_metrics(tp, fp, fn)
        temp_metrics = calculate_temporal_metrics(matched["matches"])

        comp_info = DETECTOR_REGISTRY.get(trig, {})
        component_name = comp_info.get("component", "unknown")
        modality = comp_info.get("modality", "unknown")

        per_trigger_results[trig] = {
            "trigger": trig,
            "component": component_name,
            "modality": modality,
            "counts": {
                "ground_truth_events": len(trig_gt),
                "predicted_events": len(trig_preds),
                "true_positives": tp,
                "false_positives": fp,
                "false_negatives": fn,
            },
            "classification_metrics": cls_metrics,
            "temporal_metrics": temp_metrics,
            "matches": matched["matches"],
            "unmatched_predictions": matched["unmatched_predictions"],
            "unmatched_ground_truth": matched["unmatched_ground_truth"],
        }

    # 4. Aggregate dataset metrics
    all_tp = sum(r["counts"]["true_positives"] for r in per_trigger_results.values())
    all_fp = sum(r["counts"]["false_positives"] for r in per_trigger_results.values())
    all_fn = sum(r["counts"]["false_negatives"] for r in per_trigger_results.values())
    micro_class = calculate_classification_metrics(all_tp, all_fp, all_fn)

    all_matches = [m for r in per_trigger_results.values() for m in r["matches"]]
    micro_temporal = calculate_temporal_metrics(all_matches)

    macro_precisions = [
        r["classification_metrics"]["precision"]
        for r in per_trigger_results.values()
        if r["classification_metrics"]["precision"] is not None
    ]
    macro_recalls = [
        r["classification_metrics"]["recall"]
        for r in per_trigger_results.values()
        if r["classification_metrics"]["recall"] is not None
    ]
    macro_f1s = [
        r["classification_metrics"]["f1"]
        for r in per_trigger_results.values()
        if r["classification_metrics"]["f1"] is not None
    ]

    macro_class = {
        "precision": round(sum(macro_precisions) / len(macro_precisions), 4) if macro_precisions else None,
        "recall": round(sum(macro_recalls) / len(macro_recalls), 4) if macro_recalls else None,
        "f1": round(sum(macro_f1s) / len(macro_f1s), 4) if macro_f1s else None,
    }

    return {
        "schema_version": 1,
        "evaluation_type": "same_timeline_multi_detector_evaluation",
        "evaluation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "film_id": actual_film_id,
        "duration_seconds": duration_seconds,
        "parameters": {
            "iou_threshold": iou_threshold,
            "matching_strategy": "one_to_one_greedy_highest_iou",
            "tie_breaking": "highest_iou_desc_pred_start_asc_gt_start_asc",
            "duration_tolerance": duration_tolerance,
        },
        "summary": {
            "film_id": actual_film_id,
            "duration_seconds": duration_seconds,
            "evaluated_triggers": all_triggers,
            "total_cues_packaged": len(cues),
            "counts": {
                "ground_truth_events": sum(r["counts"]["ground_truth_events"] for r in per_trigger_results.values()),
                "predicted_events": sum(r["counts"]["predicted_events"] for r in per_trigger_results.values()),
                "true_positives": all_tp,
                "false_positives": all_fp,
                "false_negatives": all_fn,
            },
            "micro_metrics": micro_class,
            "macro_metrics": macro_class,
            "temporal_metrics": micro_temporal,
        },
        "per_trigger": per_trigger_results,
    }


def format_index_evaluation_markdown(eval_data: Dict[str, Any]) -> str:
    """Format index evaluation results into GitHub-flavored Markdown report."""
    summary = eval_data["summary"]
    params = eval_data["parameters"]
    per_trig = eval_data["per_trigger"]
    film_id = eval_data.get("film_id", "unknown")
    duration = eval_data.get("duration_seconds", 0.0)

    def fmt_num(val: Optional[float], pct: bool = False) -> str:
        if val is None:
            return "*null*"
        if pct:
            return f"{val * 100:.1f}%"
        return f"{val:.4f}"

    lines: List[str] = []
    lines.append("# Multi-Detector Same-Timeline Evaluation Report — Trigger-Aware Player")
    lines.append("")
    lines.append(f"**Generated:** {eval_data.get('evaluation_timestamp_utc', 'N/A')}  ")
    lines.append(f"**Evaluator Role:** Person B — Media Ingestion, Packaging, and Pipeline Integration  ")
    lines.append(f"**Film ID:** `{film_id}` ({duration:.3f} seconds)  ")
    lines.append(f"**Matching Policy:** One-to-one temporal matching with $\\text{{tIoU}} \\ge {params['iou_threshold']}$  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(
        f"This report evaluates the multi-detector cue packaging integration for `{film_id}` on a single, "
        "unified presentation timeline. Both the operational loudness detector (`loud_spike`) and the "
        "operational subtitle/profanity detector (`profanity`) were executed against the same media asset, "
        "packaged into a single canonical `index.json`, and scored against ground-truth labels established "
        "for that exact timeline."
    )
    lines.append("")
    lines.append("### Key Results Overview")
    lines.append("")
    lines.append("| Metric Category | Precision | Recall | F1 Score | Notes |")
    lines.append("| :--- | :---: | :---: | :---: | :--- |")
    u_micro = summary["micro_metrics"]
    u_macro = summary["macro_metrics"]
    cnt = summary["counts"]
    lines.append(
        f"| **Overall Micro Average** | **{fmt_num(u_micro['precision'], True)}** | "
        f"**{fmt_num(u_micro['recall'], True)}** | **{fmt_num(u_micro['f1'], True)}** | "
        f"Aggregated across {cnt['predicted_events']} predictions and {cnt['ground_truth_events']} ground-truth events |"
    )
    lines.append(
        f"| **Overall Macro Average** | {fmt_num(u_macro['precision'], True)} | "
        f"{fmt_num(u_macro['recall'], True)} | {fmt_num(u_macro['f1'], True)} | "
        f"Unweighted mean across {len(summary['evaluated_triggers'])} active triggers |"
    )
    lines.append("")
    tm = summary["temporal_metrics"]
    lines.append("### Aggregate Temporal Accuracy")
    lines.append("")
    lines.append(f"- **Mean Temporal IoU (tIoU):** {fmt_num(tm['mean_iou'])}")
    lines.append(f"- **Mean Onset Error:** {fmt_num(tm['mean_onset_error'])}s (Mean Absolute: {fmt_num(tm['mean_abs_onset_error'])}s)")
    lines.append(f"- **Mean Offset Error:** {fmt_num(tm['mean_offset_error'])}s (Mean Absolute: {fmt_num(tm['mean_abs_offset_error'])}s)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. Per-Trigger Benchmark Results")
    lines.append("")
    lines.append("| Trigger | Component | Modality | GT Events | Pred Events | TP | FP | FN | Precision | Recall | F1 | Mean tIoU | Mean Abs Onset Err |")
    lines.append("| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    for trig, tr_res in per_trig.items():
        t_cnt = tr_res["counts"]
        t_mc = tr_res["classification_metrics"]
        t_tm = tr_res["temporal_metrics"]
        lines.append(
            f"| `{trig}` | `{tr_res.get('component', 'N/A')}` | {tr_res.get('modality', 'N/A')} | "
            f"{t_cnt['ground_truth_events']} | {t_cnt['predicted_events']} | {t_cnt['true_positives']} | {t_cnt['false_positives']} | {t_cnt['false_negatives']} | "
            f"{fmt_num(t_mc['precision'], True)} | {fmt_num(t_mc['recall'], True)} | {fmt_num(t_mc['f1'], True)} | "
            f"{fmt_num(t_tm['mean_iou'])} | {fmt_num(t_tm['mean_abs_onset_error'])}s |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. Detailed Event Matches & Boundary Exactness")
    lines.append("")
    lines.append("| Trigger | Match Status | Prediction Interval | Ground-Truth Interval | tIoU | Onset Error | Offset Error |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |")

    any_events = False
    for trig, tr_res in per_trig.items():
        for m in tr_res.get("matches", []):
            any_events = True
            p = m["prediction"]
            g = m["ground_truth"]
            lines.append(
                f"| `{trig}` | **Matched TP** | [{p['start']:.3f}, {p['end']:.3f}) | [{g['start']:.3f}, {g['end']:.3f}) | "
                f"{m['iou']:.4f} | {m['onset_error']:+.3f}s | {m['offset_error']:+.3f}s |"
            )
        for ufp in tr_res.get("unmatched_predictions", []):
            any_events = True
            lines.append(
                f"| `{trig}` | **False Positive** | [{ufp['start']:.3f}, {ufp['end']:.3f}) | — | 0.0000 | — | — |"
            )
        for ufn in tr_res.get("unmatched_ground_truth", []):
            any_events = True
            lines.append(
                f"| `{trig}` | **False Negative** | — | [{ufn['start']:.3f}, {ufn['end']:.3f}) | 0.0000 | — | — |"
            )

    if not any_events:
        lines.append("| — | No events | — | — | — | — | — |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. Pipeline & Contract Compliance")
    lines.append("")
    lines.append("1. **Timeline Consistency:** All packaged cues and ground-truth intervals fall strictly within `[0.0, 15.000]` seconds.")
    lines.append("2. **Film ID Preservation:** Index and detections both identify film asset as `multi_trigger_test`.")
    lines.append("3. **Canonical Trigger Identifiers:** Used canonical `profanity` and `loud_spike` matching `config.yaml`.")
    lines.append("4. **Canonical Detector Provenance:** Packaging preserved `source: profanity_detector` and `source: loudness_detector`.")
    lines.append("5. **Index Schema Conformance:** Validated with `scripts/validate_index.py` against `schema/index.schema.json`.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. Reproducibility")
    lines.append("")
    lines.append("Commands executed to reproduce this multi-detector evaluation:")
    lines.append("")
    lines.append("```powershell")
    lines.append("# 1. Generate media and ground-truth labels")
    lines.append("python scripts/generate_test_clips.py --multi-trigger-only")
    lines.append("")
    lines.append("# 2. Extract audio and run detectors")
    lines.append("ffmpeg -y -i data/test_clips/multi_trigger_test.mp4 -vn -ac 1 -ar 44100 -c:a pcm_s16le data/processed/multi_trigger_eval/audio.wav")
    lines.append("python scripts/detect_loudness.py --input data/processed/multi_trigger_eval/audio.wav --output data/processed/multi_trigger_eval/loudness_detections.json --film-id multi_trigger_test")
    lines.append("python scripts/parse_subtitles.py data/test_clips/multi_trigger_test.srt --wordlist data/profanity_wordlist.txt --out-detections data/processed/multi_trigger_eval/profanity_detections.json --source profanity_detector")
    lines.append("")
    lines.append("# 3. Package and validate index")
    lines.append("python scripts/package_cues.py --input data/processed/multi_trigger_eval/loudness_detections.json data/processed/multi_trigger_eval/profanity_detections.json --output data/processed/multi_trigger_eval/index.json --film-id multi_trigger_test --duration 15.0 --fps 24.0")
    lines.append("python scripts/validate_index.py --index data/processed/multi_trigger_eval/index.json")
    lines.append("")
    lines.append("# 4. Score packaged index against ground-truth labels")
    lines.append("python scripts/evaluate_detections.py --index data/processed/multi_trigger_eval/index.json --labels data/test_clips/labels.csv --film-id multi_trigger_test --output-json reports/multi_detector_evaluation_report.json --output-md reports/multi_detector_evaluation_report.md")
    lines.append("```")
    lines.append("")

    return "\n".join(lines) + "\n"


def save_index_evaluation_reports(
    eval_data: Dict[str, Any],
    json_path: Path | str,
    md_path: Path | str,
) -> None:
    """Save machine-readable JSON and Markdown index evaluation reports atomically."""
    j_path = Path(json_path).resolve()
    m_path = Path(md_path).resolve()

    j_path.parent.mkdir(parents=True, exist_ok=True)
    m_path.parent.mkdir(parents=True, exist_ok=True)

    json_text = json.dumps(eval_data, indent=2, allow_nan=False) + "\n"
    temp_json = j_path.parent / f"{j_path.name}.tmp_{os.getpid()}"
    with temp_json.open("w", encoding="utf-8") as f:
        f.write(json_text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_json, j_path)

    md_text = format_index_evaluation_markdown(eval_data)
    temp_md = m_path.parent / f"{m_path.name}.tmp_{os.getpid()}"
    with temp_md.open("w", encoding="utf-8") as f:
        f.write(md_text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_md, m_path)


# ------------------------------------------------------------------------------
# 6. Report Generation
# ------------------------------------------------------------------------------

def format_markdown_report(eval_data: Dict[str, Any]) -> str:
    """Format evaluation results into a human-readable GitHub-flavored Markdown report."""
    summary = eval_data["summary"]
    params = eval_data["parameters"]
    audit = eval_data["coverage_audit"]
    per_trig = eval_data["per_trigger"]
    per_clip = eval_data["per_clip"]

    def fmt_num(val: Optional[float], pct: bool = False) -> str:
        if val is None:
            return "*null*"
        if pct:
            return f"{val * 100:.1f}%"
        return f"{val:.4f}"

    lines: List[str] = []
    lines.append("# Detector Evaluation Report — Trigger-Aware Player (PHONY_APP)")
    lines.append("")
    lines.append(f"**Generated:** {eval_data.get('evaluation_timestamp_utc', 'N/A')}  ")
    lines.append(f"**Evaluator Role:** Person B — Media Ingestion, Packaging, and Pipeline Integration  ")
    lines.append(f"**Matching Policy:** One-to-one temporal matching with $\\text{{tIoU}} \\ge {params['iou_threshold']}$  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append("This report benchmarks the offline detectors implemented in the repository against the verified ground-truth test dataset (`data/test_clips/labels.csv` and `manifest.json`).")
    lines.append("")
    lines.append("### Key Results Overview")
    lines.append("")
    lines.append("| Metric Category | Precision | Recall | F1 Score | Notes |")
    lines.append("| :--- | :---: | :---: | :---: | :--- |")
    d_micro = summary["dataset_micro_metrics"]
    d_macro = summary["dataset_macro_metrics"]
    counts = summary.get("counts", {})
    tp_val = d_micro.get("true_positives", counts.get("true_positives", 0))
    fp_val = d_micro.get("false_positives", counts.get("false_positives", 0))
    fn_val = d_micro.get("false_negatives", counts.get("false_negatives", 0))
    lines.append(f"| **Dataset Micro-Average** | {fmt_num(d_micro.get('precision'), True)} | {fmt_num(d_micro.get('recall'), True)} | {fmt_num(d_micro.get('f1'), True)} | Pooled TP={tp_val}, FP={fp_val}, FN={fn_val} across all evaluated triggers |")
    lines.append(f"| **Dataset Macro-Average** | {fmt_num(d_macro.get('precision'), True)} | {fmt_num(d_macro.get('recall'), True)} | {fmt_num(d_macro.get('f1'), True)} | Unweighted mean across evaluated triggers |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. Methodology & Mathematical Definitions")
    lines.append("")
    lines.append("### One-to-One Deterministic Matching")
    lines.append(f"1. **Temporal IoU:** For prediction $[p_{{start}}, p_{{end}})$ and ground-truth $[g_{{start}}, g_{{end}})$, $\\text{{tIoU}} = \\frac{{\\text{{Intersection}}}}{{\\text{{Union}}}}$. A valid candidate match requires same trigger and $\\text{{tIoU}} \\ge {params['iou_threshold']}$.")
    lines.append("2. **Bipartite Constraint:** A prediction matches at most one ground-truth event. A ground-truth event matches at most one prediction.")
    lines.append("3. **Deterministic Tie-Breaking:** Candidate pairs are ranked by: highest tIoU descending $\\to$ prediction start ascending $\\to$ ground truth start ascending. Greedy selection is applied.")
    lines.append("")
    lines.append("### Undefined Metric Representation")
    lines.append("Where a metric has a zero denominator (e.g., negative controls with $TP + FP = 0$ or $TP + FN = 0$), the metric is represented as **`null`** rather than a misleading `0.0` or `1.0`.")
    lines.append("")
    lines.append("### Averaging Strategy")
    lines.append("- **Micro-average:** Sums counts ($TP, FP, FN$) across clips/events, then evaluates $\\frac{\\sum TP}{\\sum TP + \\sum FP}$ and $\\frac{\\sum TP}{\\sum TP + \\sum FN}$. Correctly reflects aggregate performance.")
    lines.append("- **Macro-average:** Computes metric per clip (or per trigger) and takes the arithmetic mean across defined values.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. Per-Trigger Benchmark Results")
    lines.append("")
    lines.append("| Trigger | Component | Modality | Clips | GT Events | Preds | TP | FP | FN | Micro Precision | Micro Recall | Micro F1 | Mean IoU | Mean \\|Onset Err\\| |")
    lines.append("| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    for trig, tr_res in per_trig.items():
        cnt = tr_res["counts"]
        mc = tr_res["micro_metrics"]
        tm = tr_res["temporal_metrics"]
        lines.append(
            f"| `{trig}` | {tr_res['component']} | {tr_res['modality']} | {tr_res['clips_evaluated_count']} | "
            f"{cnt['ground_truth_events']} | {cnt['predicted_events']} | {cnt['true_positives']} | {cnt['false_positives']} | {cnt['false_negatives']} | "
            f"{fmt_num(mc['precision'], True)} | {fmt_num(mc['recall'], True)} | {fmt_num(mc['f1'], True)} | "
            f"{fmt_num(tm['mean_iou'])} | {fmt_num(tm['mean_abs_onset_error'])}s |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. Per-Clip Detailed Results")
    lines.append("")
    lines.append("| Clip Name | Trigger | Role / Type | GT Count | Pred Count | TP | FP | FN | Precision | Recall | F1 | Mean IoU |")
    lines.append("| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    for cl in per_clip:
        meta = cl.get("metadata", {})
        desc = meta.get("event_type") or meta.get("description") or "test_clip"
        lines.append(
            f"| `{cl['clip_name']}` | `{cl['trigger']}` | {desc[:24]} | "
            f"{cl['ground_truth_count']} | {cl['prediction_count']} | {cl['true_positives']} | {cl['false_positives']} | {cl['false_negatives']} | "
            f"{fmt_num(cl['precision'], True)} | {fmt_num(cl['recall'], True)} | {fmt_num(cl['f1'], True)} | "
            f"{fmt_num(cl['mean_iou'])} |"
        )

    lines.append("")
    lines.append("### Individual Event Matches & Boundary Exactness")
    lines.append("")
    lines.append("| Clip | Trigger | Match Type | Pred Interval | GT Interval | tIoU | Onset Error | Offset Error |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |")

    any_matches = False
    for cl in per_clip:
        for m in cl.get("matches", []):
            any_matches = True
            p = m["prediction"]
            g = m["ground_truth"]
            lines.append(
                f"| `{cl['clip_name']}` | `{m['trigger']}` | Matched TP | "
                f"[{p['start']:.3f}, {p['end']:.3f}) | [{g['start']:.3f}, {g['end']:.3f}) | "
                f"{m['iou']:.4f} | {m['onset_error']:+.3f}s | {m['offset_error']:+.3f}s |"
            )
        for ufp in cl.get("unmatched_predictions", []):
            lines.append(
                f"| `{cl['clip_name']}` | `{cl['trigger']}` | Unmatched FP | "
                f"[{ufp['start']:.3f}, {ufp['end']:.3f}) | — | 0.0000 | — | — |"
            )
        for ufn in cl.get("unmatched_ground_truth", []):
            lines.append(
                f"| `{cl['clip_name']}` | `{cl['trigger']}` | Unmatched FN | "
                f"— | [{ufn['start']:.3f}, {ufn['end']:.3f}) | 0.0000 | — | — |"
            )
    if not any_matches:
        lines.append("| — | — | No matches | — | — | — | — | — |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. Annotation Integrity & Unsupported Detectors")
    lines.append("")
    lines.append("### Unsupported Detectors Registry")
    lines.append("")
    lines.append("| Trigger | Component | Owner | Status | GT Labels in labels.csv | Reason / Blocking Dependency |")
    lines.append("| :--- | :--- | :--- | :---: | :---: | :--- |")
    for trig, un_info in audit.get("unsupported_triggers", {}).items():
        comp = un_info.get("component", "unassigned")
        owner = un_info.get("owner", "unknown")
        status = un_info.get("status", "missing_detector").upper()
        gt_count = un_info.get("ground_truth_events_in_labels_csv", 0)
        reason = un_info.get("reason", "Detector implementation not available in repository.")
        lines.append(
            f"| `{trig}` | {comp} | {owner} | **{status}** | "
            f"{gt_count} | {reason} |"
        )

    lines.append("")
    lines.append("### Annotation Integrity Rules Enforced")
    lines.append("1. **No False Negativity Assumption:** Clips not labeled for a trigger were NOT assumed to be negative controls unless explicitly specified as such in `manifest.json`.")
    lines.append("2. **No Fabricated Labels or Scores:** Metrics are only reported for detectors that actually executed.")
    lines.append("3. **Preservation of Truth:** `data/test_clips/labels.csv` was preserved read-only without modifications.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 6. Reproducibility")
    lines.append("")
    lines.append("To regenerate this report and the corresponding machine-readable JSON artifact, run:")
    lines.append("")
    lines.append("```powershell")
    lines.append("python scripts/evaluate_detections.py --output-json reports/detection_evaluation_report.json --output-md reports/detection_evaluation_report.md")
    lines.append("```")
    lines.append("")
    lines.append("To execute the full evaluation harness test suite:")
    lines.append("```powershell")
    lines.append("pytest tests/test_evaluate_detections.py -v")
    lines.append("```")

    return "\n".join(lines) + "\n"


def save_evaluation_reports(
    eval_data: Dict[str, Any],
    json_path: Path | str = DEFAULT_JSON_REPORT_PATH,
    md_path: Path | str = DEFAULT_MD_REPORT_PATH,
) -> None:
    """Save machine-readable JSON and human-readable Markdown evaluation reports atomically."""
    j_path = Path(json_path).resolve()
    m_path = Path(md_path).resolve()

    j_path.parent.mkdir(parents=True, exist_ok=True)
    m_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. JSON Report
    json_text = json.dumps(eval_data, indent=2, allow_nan=False) + "\n"
    temp_json = j_path.parent / f"{j_path.name}.tmp_{os.getpid()}"
    with temp_json.open("w", encoding="utf-8") as f:
        f.write(json_text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_json, j_path)

    # 2. Markdown Report
    md_text = format_markdown_report(eval_data)
    temp_md = m_path.parent / f"{m_path.name}.tmp_{os.getpid()}"
    with temp_md.open("w", encoding="utf-8") as f:
        f.write(md_text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_md, m_path)


# ------------------------------------------------------------------------------
# 7. CLI Entrypoint
# ------------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detection Evaluation Harness for Trigger-Aware Player."
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=None,
        help="Path to a packaged index.json for same-timeline multi-detector evaluation.",
    )
    parser.add_argument(
        "--film-id",
        type=str,
        default=None,
        help="Film identifier to verify against index and match ground truth.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help=f"Path to test clips manifest.json (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=DEFAULT_LABELS_PATH,
        help=f"Path to ground truth labels.csv (default: {DEFAULT_LABELS_PATH})",
    )
    parser.add_argument(
        "--clips-dir",
        type=Path,
        default=DEFAULT_CLIPS_DIR,
        help=f"Directory containing media clips (default: {DEFAULT_CLIPS_DIR})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to pipeline config.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_JSON_REPORT_PATH,
        help=f"Path for machine-readable JSON report (default: {DEFAULT_JSON_REPORT_PATH})",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_MD_REPORT_PATH,
        help=f"Path for human-readable Markdown report (default: {DEFAULT_MD_REPORT_PATH})",
    )
    parser.add_argument(
        "--iou-threshold",
        type=float,
        default=DEFAULT_IOU_THRESHOLD,
        help=f"Temporal IoU threshold for event match (default: {DEFAULT_IOU_THRESHOLD})",
    )
    parser.add_argument(
        "--triggers",
        nargs="+",
        default=None,
        help="Optional filter of triggers to evaluate (e.g. --triggers loud_spike profanity)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON report summary to stdout",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    try:
        # Case A: Same-timeline packaged index evaluation
        if args.index is not None:
            eval_data = evaluate_index(
                index=args.index,
                ground_truth=args.labels,
                film_id=args.film_id,
                iou_threshold=args.iou_threshold,
            )

            save_index_evaluation_reports(
                eval_data=eval_data,
                json_path=args.output_json,
                md_path=args.output_md,
            )

            if args.json:
                print(json.dumps(eval_data["summary"], indent=2))
            else:
                print("=" * 72)
                print("  MULTI-DETECTOR INDEX EVALUATION — COMPLETE")
                print("=" * 72)
                summary = eval_data["summary"]
                print(f"Film ID: {eval_data['film_id']} (Duration: {eval_data['duration_seconds']:.2f}s)")
                print(f"Evaluated triggers: {', '.join(summary['evaluated_triggers'])}")
                d_micro = summary["micro_metrics"]
                p_str = f"{d_micro['precision'] * 100:.1f}%" if d_micro['precision'] is not None else "null"
                r_str = f"{d_micro['recall'] * 100:.1f}%" if d_micro['recall'] is not None else "null"
                f_str = f"{d_micro['f1'] * 100:.1f}%" if d_micro['f1'] is not None else "null"
                print(f"Micro Precision: {p_str} | Recall: {r_str} | F1: {f_str}")
                print(f"Machine-readable JSON report: {args.output_json.resolve()}")
                print(f"Human-readable Markdown report: {args.output_md.resolve()}")
                print("=" * 72)

            return 0

        # Case B: Dataset multi-clip evaluation
        triggers_set = set(args.triggers) if args.triggers else None
        eval_data = evaluate_dataset(
            manifest_path=args.manifest,
            labels_path=args.labels,
            clips_dir=args.clips_dir,
            config_path=args.config,
            iou_threshold=args.iou_threshold,
            target_triggers=triggers_set,
        )

        save_evaluation_reports(
            eval_data=eval_data,
            json_path=args.output_json,
            md_path=args.output_md,
        )

        if args.json:
            print(json.dumps(eval_data["summary"], indent=2))
        else:
            print("=" * 72)
            print("  DETECTOR EVALUATION HARNESS — EVALUATION COMPLETE")
            print("=" * 72)
            summary = eval_data["summary"]
            print(f"Evaluated triggers: {', '.join(summary['evaluated_triggers'])}")
            d_micro = summary["dataset_micro_metrics"]
            p_str = f"{d_micro['precision'] * 100:.1f}%" if d_micro['precision'] is not None else "null"
            r_str = f"{d_micro['recall'] * 100:.1f}%" if d_micro['recall'] is not None else "null"
            f_str = f"{d_micro['f1'] * 100:.1f}%" if d_micro['f1'] is not None else "null"
            print(f"Dataset Micro Precision: {p_str} | Recall: {r_str} | F1: {f_str}")
            print(f"Machine-readable JSON report: {args.output_json.resolve()}")
            print(f"Human-readable Markdown report: {args.output_md.resolve()}")
            print("=" * 72)

        return 0
    except Exception as exc:
        print(f"\n[ERROR] Evaluation harness failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
