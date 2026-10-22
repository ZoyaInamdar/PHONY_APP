#!/usr/bin/env python3
"""Benchmark Evaluation Adapter & Labels Exporter.

Exports ground-truth annotations from benchmark_manifest.json to the standard
labels.csv and manifest.json format expected by scripts/evaluate_detections.py.

Key features:
- Filter by split ('train_dev', 'validation', 'locked_test', or 'all')
- Filter by dataset type ('synthetic', 'natural', or 'all')
- Strict verification gating: rejects 'pending_review' and 'rejected' events
  from verified ground-truth exports by default (--include-pending to override)
- Formats negative controls compatible with evaluate_detections.py auditing
- Prevents unreviewed clips from polluting evaluation metrics

Usage:
    python3 scripts/export_benchmark_labels.py --split validation
    python3 scripts/export_benchmark_labels.py --split locked_test --include-pending
    python3 scripts/export_benchmark_labels.py --output-labels data/benchmark/labels.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_PATH = REPO_ROOT / "data" / "benchmark" / "manifest.json"
DEFAULT_LABELS_OUT = REPO_ROOT / "data" / "benchmark" / "labels.csv"
DEFAULT_MANIFEST_OUT = REPO_ROOT / "data" / "benchmark" / "eval_manifest.json"


def export_benchmark_labels(
    benchmark_manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    split_filter: Optional[str] = "all",
    dataset_type_filter: Optional[str] = "all",
    include_pending: bool = False,
    output_labels_csv: Optional[Path | str] = None,
    output_manifest_json: Optional[Path | str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Export benchmark annotations to evaluate_detections.py compatible format.

    Args:
        benchmark_manifest_path: Path to benchmark manifest.
        split_filter: 'all', 'train_dev', 'validation', or 'locked_test'.
        dataset_type_filter: 'all', 'synthetic', or 'natural'.
        include_pending: If False, exclusively exports events with review_status == 'verified'.
                         Clips with ONLY pending/unreviewed events are excluded from
                         ground-truth evaluation.
        output_labels_csv: Optional path to write labels.csv.
        output_manifest_json: Optional path to write manifest.json.

    Returns:
        (exported_label_rows, exported_manifest_data)
    """
    manifest_file = Path(benchmark_manifest_path)
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Benchmark manifest not found: {manifest_file.resolve()}")

    with manifest_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    clips: List[Dict[str, Any]] = data.get("clips", [])
    csv_rows: List[Dict[str, str]] = []
    manifest_clips: List[Dict[str, Any]] = []

    for clip in clips:
        # Filter by split
        clip_split = clip.get("split", "")
        if split_filter and split_filter != "all" and clip_split != split_filter:
            continue

        # Filter by dataset type
        clip_type = clip.get("dataset_type", "")
        if dataset_type_filter and dataset_type_filter != "all" and clip_type != dataset_type_filter:
            continue

        clip_id = clip["clip_id"]
        is_negative = clip.get("is_negative_control", False)
        all_events = clip.get("events", [])

        # Filter events by review status
        if include_pending:
            eligible_events = [e for e in all_events if e.get("review_status") != "rejected"]
        else:
            eligible_events = [e for e in all_events if e.get("review_status") == "verified"]

        # If clip is neither an explicit negative control nor has any eligible events,
        # it is an unreviewed/pending-only clip and MUST be excluded from ground-truth evaluation
        # to prevent unverified data from polluting metrics.
        if not is_negative and not eligible_events:
            continue

        # Build CSV rows
        if is_negative:
            # Explicit negative control: emit blank start/end markers for evaluated triggers
            annotator = all_events[0].get("annotator_id", "benchmark_author") if all_events else "benchmark_negative_control"
            neg_triggers = clip.get("negative_triggers")
            if not neg_triggers:
                clip_mod = clip.get("modality", "video")
                if clip_mod == "audio":
                    neg_triggers = ["loud_spike", "gunshot", "explosion", "scream"]
                elif clip_mod == "subtitles":
                    neg_triggers = ["profanity"]
                else:
                    neg_triggers = ["flashing", "fast_motion", "jump_scare"]

            for neg_trig in neg_triggers:
                csv_rows.append({
                    "clip": clip_id,
                    "trigger": neg_trig,
                    "start": "",
                    "end": "",
                    "labeller": annotator,
                    "actual_rate": "",
                })
        else:
            for evt in eligible_events:
                csv_rows.append({
                    "clip": clip_id,
                    "trigger": evt["trigger"],
                    "start": f"{evt['start']:.3f}",
                    "end": f"{evt['end']:.3f}",
                    "labeller": evt.get("annotator_id", "annotator"),
                    "actual_rate": "",
                })

        # Build evaluator manifest entry
        primary_trigger = eligible_events[0]["trigger"] if eligible_events else None
        primary_start = eligible_events[0]["start"] if eligible_events else None
        primary_end = eligible_events[0]["end"] if eligible_events else None
        primary_labeller = eligible_events[0].get("annotator_id", "evaluator") if eligible_events else "evaluator"
        clip_review_status = "verified_negative" if is_negative else ("verified" if all(e.get("review_status") == "verified" for e in eligible_events) else "pending_review")

        manifest_clips.append({
            "clip_name": clip_id,
            "duration": clip.get("duration_seconds", 0.0),
            "fps": clip.get("fps", 24.0),
            "width": clip.get("width", 640),
            "height": clip.get("height", 360),
            "trigger": primary_trigger,
            "start": primary_start,
            "end": primary_end,
            "labeller": primary_labeller,
            "is_negative_control": is_negative,
            "negative_triggers": clip.get("negative_triggers", []),
            "review_status": clip_review_status,
            "metadata": {
                "event_type": "negative_control" if is_negative else (primary_trigger or "natural_scene"),
                "is_negative_control": is_negative,
                "negative_triggers": clip.get("negative_triggers", []),
                "review_status": clip_review_status,
                "contains_pending": any(e.get("review_status") == "pending_review" for e in all_events),
                "modality": clip.get("modality", "video"),
                "dataset_type": clip.get("dataset_type", "natural"),
                "split": clip.get("split", "validation"),
                "scene_id": clip.get("scene_id", ""),
                "license": clip.get("license", ""),
                "media_relpath": clip.get("media_relpath", ""),
                "contains_trigger": not is_negative,
                "triggers": [e["trigger"] for e in eligible_events],
            },
        })

    eval_manifest = {
        "generator_version": 1,
        "clip_count": len(manifest_clips),
        "split_filter": split_filter,
        "dataset_type_filter": dataset_type_filter,
        "include_pending": include_pending,
        "is_verified_benchmark": not include_pending,
        "contains_pending_review": include_pending,
        "clips": manifest_clips,
    }

    # Write CSV if requested
    if output_labels_csv:
        out_csv_path = Path(output_labels_csv)
        out_csv_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["clip", "trigger", "start", "end", "labeller", "actual_rate"]
        with out_csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)

    # Write Manifest if requested
    if output_manifest_json:
        out_json_path = Path(output_manifest_json)
        out_json_path.parent.mkdir(parents=True, exist_ok=True)
        with out_json_path.open("w", encoding="utf-8") as f:
            json.dump(eval_manifest, f, indent=2)

    return csv_rows, eval_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export benchmark manifest to evaluator format.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help=f"Path to benchmark manifest JSON (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--split",
        choices=["all", "train_dev", "validation", "locked_test"],
        default="all",
        help="Split to export (default: all)",
    )
    parser.add_argument(
        "--dataset-type",
        choices=["all", "synthetic", "natural"],
        default="all",
        help="Dataset type to export (default: all)",
    )
    parser.add_argument(
        "--include-pending",
        action="store_true",
        help="Include pending_review events (default: False, only verified events exported).",
    )
    parser.add_argument(
        "--output-labels",
        type=Path,
        default=DEFAULT_LABELS_OUT,
        help=f"Path for output labels.csv (default: {DEFAULT_LABELS_OUT})",
    )
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=DEFAULT_MANIFEST_OUT,
        help=f"Path for output eval_manifest.json (default: {DEFAULT_MANIFEST_OUT})",
    )
    args = parser.parse_args()

    rows, manifest = export_benchmark_labels(
        benchmark_manifest_path=args.manifest,
        split_filter=args.split,
        dataset_type_filter=args.dataset_type,
        include_pending=args.include_pending,
        output_labels_csv=args.output_labels,
        output_manifest_json=args.output_manifest,
    )

    print(
        f"Exported {len(rows)} label records and {len(manifest['clips'])} clip entries "
        f"(split={args.split}, type={args.dataset_type}, include_pending={args.include_pending}) "
        f"to {args.output_labels} and {args.output_manifest}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
