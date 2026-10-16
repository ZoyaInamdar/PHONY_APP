#!/usr/bin/env python3
"""Repeatable dataset validation for Trigger-Aware Player synthetic test clips.

Validates data/test_clips/manifest.json, data/test_clips/labels.csv, and media files on disk:
- Total MP4 files on disk vs manifest entries and unique clip names
- Missing manifest files and unindexed disk files
- Duplicate manifest clip names
- Total label rows and counts by trigger
- Reference validity (all labeled clips exist on disk)
- Numeric interval validation (0 <= start < end)
- Flashing actual_rate validation against manifest technical metadata
- Presence of loudness test clips and their labeled ground truth
- Multi-event clip integrity
- Negative control classification

Usage:
    python scripts/validate_synthetic_dataset.py
    python scripts/validate_synthetic_dataset.py --clips-dir data/test_clips
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLIPS_DIR = REPO_ROOT / "data" / "test_clips"

CANONICAL_TRIGGERS = {
    "flashing",
    "loud_spike",
    "gunshot",
    "explosion",
    "scream",
    "profanity",
    "spider",
    "fast_motion",
    "jump_scare",
}


def validate_dataset(clips_dir: Path | str = DEFAULT_CLIPS_DIR) -> Dict[str, Any]:
    """Perform comprehensive validation of manifest.json, labels.csv, and test clips."""
    dir_path = Path(clips_dir)
    errors: List[str] = []
    warnings: List[str] = []

    # 1. Media files on disk
    mp4_files = sorted([f.name for f in dir_path.glob("*.mp4")])
    total_mp4s = len(mp4_files)

    # 2. Manifest validation
    manifest_path = dir_path / "manifest.json"
    manifest_entries: List[Dict[str, Any]] = []
    manifest_clip_names: List[str] = []
    manifest_clip_count_header: int | None = None

    if not manifest_path.is_file():
        errors.append(f"manifest.json not found in {dir_path}")
    else:
        try:
            with manifest_path.open("r", encoding="utf-8") as f:
                manifest_data = json.load(f)
            manifest_clip_count_header = manifest_data.get("clip_count")
            manifest_entries = manifest_data.get("clips", [])
            manifest_clip_names = [c.get("clip_name", "") for c in manifest_entries]
        except Exception as exc:
            errors.append(f"Failed to read/parse manifest.json: {exc}")

    manifest_counts = Counter(manifest_clip_names)
    duplicates = [name for name, count in manifest_counts.items() if count > 1]
    unique_manifest_names = sorted(set(manifest_clip_names))

    if duplicates:
        errors.append(f"Duplicate clip names in manifest.json: {duplicates}")

    if manifest_clip_count_header is not None and manifest_clip_count_header != len(unique_manifest_names):
        errors.append(
            f"Header clip_count ({manifest_clip_count_header}) does not match distinct clip count ({len(unique_manifest_names)})"
        )

    # Disk vs Manifest comparisons
    missing_from_disk = [name for name in unique_manifest_names if name not in mp4_files]
    if missing_from_disk:
        errors.append(f"Manifest references files missing from disk: {missing_from_disk}")

    unindexed_on_disk = [name for name in mp4_files if name not in unique_manifest_names]
    unindexed_classified = []
    for f in unindexed_on_disk:
        # Check if intentionally excluded
        unindexed_classified.append((f, "requires_investigation"))
    if unindexed_classified:
        warnings.append(f"Files on disk absent from manifest: {unindexed_classified}")

    # 3. Labels.csv validation
    labels_path = dir_path / "labels.csv"
    label_rows: List[Dict[str, Any]] = []
    if not labels_path.is_file():
        errors.append(f"labels.csv not found in {dir_path}")
    else:
        try:
            with labels_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                expected_cols = ["clip", "trigger", "start", "end", "labeller", "actual_rate"]
                if reader.fieldnames != expected_cols:
                    errors.append(f"labels.csv columns {reader.fieldnames} do not match expected {expected_cols}")
                label_rows = list(reader)
        except Exception as exc:
            errors.append(f"Failed to read labels.csv: {exc}")

    label_trigger_counts = Counter(r.get("trigger", "") for r in label_rows)

    # Validate individual label rows
    manifest_by_name = {c.get("clip_name"): c for c in manifest_entries}
    missing_media_in_labels = []
    invalid_intervals = []
    flash_missing_actual_rate = []
    flash_rate_mismatches = []

    for r in label_rows:
        clip_name = r.get("clip", "")
        trig = r.get("trigger", "")
        start_str = r.get("start", "")
        end_str = r.get("end", "")
        act_rate_str = r.get("actual_rate", "").strip()

        # Check media exists
        if clip_name not in mp4_files:
            missing_media_in_labels.append(clip_name)

        # Check canonical trigger
        if trig not in CANONICAL_TRIGGERS:
            errors.append(f"Non-canonical trigger '{trig}' for clip {clip_name}")

        # Check numeric interval
        try:
            st = float(start_str)
            en = float(end_str)
            if st < 0.0 or en <= st:
                invalid_intervals.append((clip_name, st, en))
        except (ValueError, TypeError):
            invalid_intervals.append((clip_name, start_str, end_str))

        # Check flashing actual_rate
        if trig == "flashing":
            if not act_rate_str:
                flash_missing_actual_rate.append(clip_name)
            else:
                try:
                    act_rate_val = float(act_rate_str)
                    manifest_entry = manifest_by_name.get(clip_name)
                    if manifest_entry:
                        manifest_rate = manifest_entry.get("metadata", {}).get("actual_flash_rate")
                        if manifest_rate is not None:
                            if not math.isclose(act_rate_val, float(manifest_rate), rel_tol=1e-3, abs_tol=1e-3):
                                flash_rate_mismatches.append(
                                    (clip_name, act_rate_val, float(manifest_rate))
                                )
                except ValueError:
                    flash_missing_actual_rate.append(clip_name)

    if missing_media_in_labels:
        errors.append(f"Labels reference nonexistent media files: {set(missing_media_in_labels)}")
    if invalid_intervals:
        errors.append(f"Invalid or reversed intervals in labels.csv: {invalid_intervals}")
    if flash_missing_actual_rate:
        errors.append(f"Flashing rows with missing actual_rate: {flash_missing_actual_rate}")
    if flash_rate_mismatches:
        errors.append(f"Flashing actual_rate mismatches with manifest: {flash_rate_mismatches}")

    # 4. Loudness clips presence and labels
    loudness_clips = ["audio_loud_spike_isolated.mp4", "audio_loud_spike_test.mp4"]
    for lc in loudness_clips:
        if lc not in mp4_files:
            errors.append(f"Loudness test clip missing from disk: {lc}")
        if lc not in manifest_by_name:
            errors.append(f"Loudness test clip missing from manifest.json: {lc}")

    loudness_labels = [r for r in label_rows if r.get("trigger") == "loud_spike"]
    if len(loudness_labels) != 2:
        errors.append(f"Expected 2 loud_spike labels, found {len(loudness_labels)}")

    # 5. Multi-event clip integrity
    multi_event_labels = [r for r in label_rows if r.get("clip") == "audio_multi_event_mix.mp4"]
    multi_event_triggers = {r.get("trigger") for r in multi_event_labels}
    expected_multi_triggers = {"gunshot", "scream", "explosion"}
    if multi_event_triggers != expected_multi_triggers:
        errors.append(
            f"audio_multi_event_mix.mp4 has unexpected triggers: {multi_event_triggers} != {expected_multi_triggers}"
        )

    # 6. Speech triggers test integrity
    speech_labels = [r for r in label_rows if r.get("clip") == "speech_triggers_test.mp4"]
    if len(speech_labels) != 3:
        errors.append(f"speech_triggers_test.mp4 expected 3 profanity labels, found {len(speech_labels)}")

    # 7. Negative controls not in labels
    for r in label_rows:
        c = r.get("clip", "")
        if "negative" in c or "detail" in c or "sync" in c:
            errors.append(f"Negative/utility clip '{c}' accidentally labeled in labels.csv")

    report = {
        "total_mp4_files": total_mp4s,
        "manifest_header_clip_count": manifest_clip_count_header,
        "total_manifest_entries": len(manifest_entries),
        "unique_manifest_clips": len(unique_manifest_names),
        "duplicate_manifest_clips": duplicates,
        "missing_manifest_files": missing_from_disk,
        "unindexed_disk_files": unindexed_on_disk,
        "total_label_rows": len(label_rows),
        "label_counts_by_trigger": dict(label_trigger_counts),
        "flashing_actual_rates_valid": len(flash_missing_actual_rate) == 0 and len(flash_rate_mismatches) == 0,
        "loudness_clips_valid": all(lc in mp4_files and lc in manifest_by_name for lc in loudness_clips),
        "loudness_labels_count": len(loudness_labels),
        "multi_event_mix_labels_count": len(multi_event_labels),
        "speech_triggers_labels_count": len(speech_labels),
        "errors": errors,
        "warnings": warnings,
        "is_valid": len(errors) == 0,
    }
    return report


def print_report(report: Dict[str, Any]) -> None:
    """Print formatted validation report to stdout."""
    print("=" * 70)
    print("PHONY_APP Synthetic Test Dataset Validation Report")
    print("=" * 70)
    print(f"Total MP4 files on disk:         {report['total_mp4_files']}")
    print(f"Manifest header clip_count:      {report['manifest_header_clip_count']}")
    print(f"Total manifest entries:          {report['total_manifest_entries']}")
    print(f"Unique manifest clip names:      {report['unique_manifest_clips']}")
    print(f"Duplicate manifest clip names:   {report['duplicate_manifest_clips'] or 'None'}")
    print(f"Missing manifest files on disk:  {report['missing_manifest_files'] or 'None'}")
    print(f"Unindexed disk files:            {report['unindexed_disk_files'] or 'None'}")
    print("-" * 70)
    print(f"Total label rows (labels.csv):   {report['total_label_rows']}")
    print(f"Label counts by trigger:         {report['label_counts_by_trigger']}")
    print(f"Flashing actual_rates valid:     {report['flashing_actual_rates_valid']}")
    print(f"Loudness test clips valid:       {report['loudness_clips_valid']} ({report['loudness_labels_count']} labels)")
    print(f"Multi-event mix events:          {report['multi_event_mix_labels_count']}")
    print(f"Speech triggers test events:     {report['speech_triggers_labels_count']}")
    print("-" * 70)
    if report["warnings"]:
        print("WARNINGS:")
        for w in report["warnings"]:
            print(f"  [WARN] {w}")
    if report["errors"]:
        print("ERRORS:")
        for e in report["errors"]:
            print(f"  [ERROR] {e}")
        print("=" * 70)
        print("OVERALL STATUS: FAILED")
        print("=" * 70)
    else:
        print("OVERALL STATUS: PASSED (All validation checks succeeded)")
        print("=" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate synthetic test dataset.")
    parser.add_argument(
        "--clips-dir",
        type=str,
        default=str(DEFAULT_CLIPS_DIR),
        help=f"Path to test clips directory (default: {DEFAULT_CLIPS_DIR})",
    )
    args = parser.parse_args()
    report = validate_dataset(args.clips_dir)
    print_report(report)
    return 0 if report["is_valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
