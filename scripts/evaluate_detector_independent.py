#!/usr/bin/env python3
"""Independent Detector Evaluation Pipeline.

Executes a single detector (or all available detectors) independently on selected
benchmark clips, saving raw detector outputs and reproduction metadata in:
    data/evaluation/raw/<detector>/<run_id>/

Key requirements:
1. Does NOT modify production indexes (data/index.json or film indexes).
2. Saves unmodified raw detector outputs, scores, and intermediate metrics.
3. Performs strict 1-to-1 greedy bipartite matching between predictions and ground-truth intervals.
4. Separates metrics strictly between 'synthetic' fixtures and 'natural' media.
5. If verified ground-truth annotations are missing, outputs status 'INSUFFICIENT_VALIDATION_DATA'.
6. Records exact configuration, thresholds, clip file hashes, and execution provenance.

Usage:
    python3 scripts/evaluate_detector_independent.py --detector flashing --split train_dev
    python3 scripts/evaluate_detector_independent.py --detector loud_spike --split validation
    python3 scripts/evaluate_detector_independent.py --detector all --split validation
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluate_detections import (
    DETECTOR_REGISTRY,
    DEFAULT_CONFIG_PATH,
    DEFAULT_IOU_THRESHOLD,
    compute_temporal_iou,
    load_detector_config,
    run_detector_for_clip,
)

DEFAULT_MANIFEST_PATH = REPO_ROOT / "data" / "benchmark" / "manifest.json"
DEFAULT_EVAL_BASE = REPO_ROOT / "data" / "evaluation" / "raw"


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def match_predictions_to_ground_truth(
    predictions: List[Dict[str, Any]],
    ground_truth: List[Dict[str, Any]],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> Dict[str, Any]:
    """Strict 1-to-1 greedy bipartite interval matching.

    A prediction matches at most one ground-truth event, and each ground-truth
    event matches at most one prediction.
    """
    sorted_preds = sorted(predictions, key=lambda p: float(p.get("score", 1.0)), reverse=True)
    unmatched_gt = list(ground_truth)
    matches: List[Dict[str, Any]] = []
    unmatched_preds: List[Dict[str, Any]] = []

    for pred in sorted_preds:
        p_start = float(pred["start"])
        p_end = float(pred["end"])

        best_iou = 0.0
        best_gt_idx = -1

        for idx, gt in enumerate(unmatched_gt):
            iou = compute_temporal_iou(p_start, p_end, float(gt["start"]), float(gt["end"]))
            if iou >= iou_threshold and iou > best_iou:
                best_iou = iou
                best_gt_idx = idx

        if best_gt_idx >= 0:
            gt_matched = unmatched_gt.pop(best_gt_idx)
            matches.append({
                "prediction": pred,
                "ground_truth": gt_matched,
                "iou": round(best_iou, 4),
                "onset_error": round(p_start - float(gt_matched["start"]), 4),
                "offset_error": round(p_end - float(gt_matched["end"]), 4),
            })
        else:
            unmatched_preds.append(pred)

    return {
        "matches": matches,
        "unmatched_predictions": unmatched_preds,
        "unmatched_ground_truth": unmatched_gt,
        "true_positives": len(matches),
        "false_positives": len(unmatched_preds),
        "false_negatives": len(unmatched_gt),
    }


def evaluate_detector_run(
    detector_name: str,
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    split_filter: Optional[str] = "validation",
    dataset_type_filter: Optional[str] = "all",
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    output_base_dir: Path | str = DEFAULT_EVAL_BASE,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> Dict[str, Any]:
    """Execute an independent evaluation run for a specified detector."""
    m_path = Path(manifest_path)
    with m_path.open("r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    cfg = load_detector_config(config_path)
    det_info = DETECTOR_REGISTRY.get(detector_name)
    if not det_info:
        raise ValueError(f"Unknown detector trigger '{detector_name}'. Valid: {list(DETECTOR_REGISTRY.keys())}")

    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    run_dir = Path(output_base_dir) / detector_name / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Filter eligible clips
    clips = manifest_data.get("clips", [])
    selected_clips = []
    for c in clips:
        if split_filter and split_filter != "all" and c.get("split") != split_filter:
            continue
        if dataset_type_filter and dataset_type_filter != "all" and c.get("dataset_type") != dataset_type_filter:
            continue
        selected_clips.append(c)

    # Check detector implementation availability
    if det_info["status"] != "available":
        run_record = {
            "run_id": run_id,
            "detector": detector_name,
            "status": "UNAVAILABLE",
            "reason": f"Detector implementation status is '{det_info['status']}'. Script: {det_info.get('script')}",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with (run_dir / "run_metadata.json").open("w", encoding="utf-8") as f:
            json.dump(run_record, f, indent=2)
        return run_record

    raw_detections: Dict[str, Any] = {}
    clip_hashes: Dict[str, str] = {}
    skipped_clips: List[Dict[str, str]] = []

    # Per-modality evaluation accumulator
    stratified_results: Dict[str, Dict[str, Any]] = {
        "synthetic": {"tp": 0, "fp": 0, "fn": 0, "clips_evaluated": 0, "matches": [], "fp_clips": []},
        "natural": {"tp": 0, "fp": 0, "fn": 0, "clips_evaluated": 0, "matches": [], "fp_clips": []},
    }

    with tempfile.TemporaryDirectory(prefix=f"eval_{detector_name}_") as tmp_dir:
        working_path = Path(tmp_dir)

        for clip in selected_clips:
            clip_id = clip["clip_id"]
            media_rel = clip.get("media_relpath", "")
            media_path = REPO_ROOT / media_rel
            dtype = clip.get("dataset_type", "synthetic")

            if not media_path.is_file():
                skipped_clips.append({
                    "clip_id": clip_id,
                    "reason": f"Missing media file at {media_path.resolve()}",
                })
                continue

            clip_hashes[clip_id] = compute_file_sha256(media_path)

            # Determine verified ground truth events for this trigger
            verified_gt = [
                evt for evt in clip.get("events", [])
                if evt.get("trigger") == detector_name and evt.get("review_status") == "verified"
            ]

            is_neg = clip.get("is_negative_control", False)
            neg_trigs = clip.get("negative_triggers")
            is_neg_for_this = is_neg and (neg_trigs is None or detector_name in neg_trigs)

            # If not negative for this trigger and has 0 verified events, it's an unannotated clip
            if not is_neg_for_this and not verified_gt:
                # Excluded from scoring
                continue

            # Run detector
            t0 = time.perf_counter()
            try:
                preds = run_detector_for_clip(
                    clip_name=clip_id,
                    trigger=detector_name,
                    clips_dir=media_path.parent,
                    working_dir=working_path,
                    config=cfg,
                    clip_metadata=clip,
                )
                duration_sec = round(time.perf_counter() - t0, 4)
            except Exception as exc:
                skipped_clips.append({
                    "clip_id": clip_id,
                    "reason": f"Execution error: {exc}",
                })
                continue

            raw_detections[clip_id] = {
                "predictions": preds,
                "execution_time_seconds": duration_sec,
                "verified_gt_count": len(verified_gt),
                "is_negative_control": is_neg_for_this,
            }

            # Match intervals
            match_res = match_predictions_to_ground_truth(
                predictions=preds,
                ground_truth=verified_gt,
                iou_threshold=iou_threshold,
            )

            strat = stratified_results.setdefault(dtype, {"tp": 0, "fp": 0, "fn": 0, "clips_evaluated": 0, "matches": [], "fp_clips": []})
            strat["tp"] += match_res["true_positives"]
            strat["fp"] += match_res["false_positives"]
            strat["fn"] += match_res["false_negatives"]
            strat["clips_evaluated"] += 1
            strat["matches"].extend(match_res["matches"])
            if match_res["false_positives"] > 0:
                strat["fp_clips"].append({"clip_id": clip_id, "fp_count": match_res["false_positives"]})

    # Save raw detections
    with (run_dir / "raw_detections.json").open("w", encoding="utf-8") as f:
        json.dump(raw_detections, f, indent=2)

    # Compute metrics per stratum
    summary_report: Dict[str, Any] = {}
    for dtype, strat in stratified_results.items():
        tp = strat["tp"]
        fp = strat["fp"]
        fn = strat["fn"]
        clips_eval = strat["clips_evaluated"]

        if clips_eval == 0 or (tp + fn == 0 and fp == 0):
            summary_report[dtype] = {
                "status": "INSUFFICIENT_VALIDATION_DATA",
                "clips_evaluated": clips_eval,
                "reason": f"No verified {dtype} ground-truth events or active negative controls in {split_filter} split.",
            }
        else:
            prec = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
            rec = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
            f1 = round(2 * prec * rec / (prec + rec), 4) if (prec + rec) > 0 else 0.0

            onset_errors = [m["onset_error"] for m in strat["matches"]]
            mean_onset = round(sum(onset_errors) / len(onset_errors), 4) if onset_errors else 0.0

            summary_report[dtype] = {
                "status": "EVALUATED",
                "clips_evaluated": clips_eval,
                "true_positives": tp,
                "false_positives": fp,
                "false_negatives": fn,
                "precision": prec,
                "recall": rec,
                "f1": f1,
                "mean_onset_error_seconds": mean_onset,
                "matches_count": len(strat["matches"]),
            }

    metadata = {
        "run_id": run_id,
        "detector": detector_name,
        "split_filter": split_filter,
        "dataset_type_filter": dataset_type_filter,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "time_base": "presentation_timestamps_seconds",
        "iou_threshold": iou_threshold,
        "detector_info": det_info,
        "clip_hashes": clip_hashes,
        "skipped_clips": skipped_clips,
        "summary": summary_report,
    }

    with (run_dir / "run_metadata.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"[{detector_name}] Independent evaluation complete -> {run_dir}")
    print(f"  Summary: {json.dumps(summary_report, indent=2)}")
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Evaluate a detector independently on benchmark splits.")
    parser.add_argument("--detector", required=True, help="Detector name (flashing, loud_spike, etc.) or 'all'")
    parser.add_argument("--split", default="validation", help="Benchmark split ('train_dev', 'validation', 'locked_test', or 'all')")
    parser.add_argument("--dataset-type", default="all", help="'synthetic', 'natural', or 'all'")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Path to manifest.json")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml")
    parser.add_argument("--output-dir", default=str(DEFAULT_EVAL_BASE), help="Output base directory")
    parser.add_argument("--iou", type=float, default=DEFAULT_IOU_THRESHOLD, help="IoU matching threshold")

    args = parser.parse_args()

    detectors = list(DETECTOR_REGISTRY.keys()) if args.detector == "all" else [args.detector]
    for det in detectors:
        evaluate_detector_run(
            detector_name=det,
            manifest_path=args.manifest,
            split_filter=args.split,
            dataset_type_filter=args.dataset_type,
            config_path=args.config,
            output_base_dir=args.output_dir,
            iou_threshold=args.iou,
        )


if __name__ == "__main__":
    main()
