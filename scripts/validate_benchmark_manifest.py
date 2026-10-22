#!/usr/bin/env python3
"""Validator for Ground-Truth Benchmark Manifest.

Validates structural schema conformity, temporal interval arithmetic,
canonical trigger nomenclature, negative control consistency, media file existence,
and anti-leakage scene isolation across train/dev, validation, and locked test splits.

Usage:
    python3 scripts/validate_benchmark_manifest.py
    python3 scripts/validate_benchmark_manifest.py --manifest data/benchmark/manifest.json
    python3 scripts/validate_benchmark_manifest.py --no-check-media
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_PATH = REPO_ROOT / "schema" / "benchmark_manifest.schema.json"
DEFAULT_MANIFEST_PATH = REPO_ROOT / "data" / "benchmark" / "manifest.json"

CANONICAL_TRIGGERS: Set[str] = {
    "flashing",
    "profanity",
    "gunshot",
    "explosion",
    "scream",
    "loud_spike",
    "sustained_loudness",
    "spider",
    "insect",
    "fast_motion",
    "jump_scare",
    "visible_blood",
}


class BenchmarkValidationError(Exception):
    """Raised when benchmark manifest fails structural or business validation."""


def validate_manifest_dict(
    manifest_data: Dict[str, Any],
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
    check_media: bool = True,
    repo_root: Path | str = REPO_ROOT,
) -> Tuple[bool, List[str]]:
    """Validate parsed benchmark manifest data against schema and integrity rules.

    Returns:
        (is_valid, list_of_error_strings)
    """
    errors: List[str] = []
    root = Path(repo_root)

    # 1. JSON Schema validation
    schema_file = Path(schema_path)
    if not schema_file.is_file():
        errors.append(f"Benchmark schema file not found: {schema_file.resolve()}")
    else:
        try:
            import jsonschema
            with schema_file.open("r", encoding="utf-8") as sf:
                schema = json.load(sf)
            validator = jsonschema.Draft202012Validator(schema)
            for err in validator.iter_errors(manifest_data):
                loc = " -> ".join(str(p) for p in err.path) if err.path else "root"
                errors.append(f"Schema error at [{loc}]: {err.message}")
        except ImportError:
            errors.append("jsonschema library is required for validation but not installed.")
        except Exception as exc:
            errors.append(f"Failed to run jsonschema validation: {exc}")

    # If top-level keys are missing, cannot perform deep checks
    clips = manifest_data.get("clips")
    if not isinstance(clips, list):
        errors.append("Benchmark manifest missing 'clips' list.")
        return (False, errors)

    # 2. Uniqueness & Anti-leakage Tracking
    seen_clip_ids: Set[str] = set()
    seen_event_ids: Set[str] = set()
    scene_to_splits: Dict[str, Set[str]] = defaultdict(set)

    # 3. Clip & Event Level Validation
    for clip_idx, clip in enumerate(clips):
        clip_id = clip.get("clip_id", f"<index_{clip_idx}>")
        split = clip.get("split")
        scene_id = clip.get("scene_id")
        duration = clip.get("duration_seconds")
        is_negative = clip.get("is_negative_control", False)
        media_relpath = clip.get("media_relpath")
        events = clip.get("events", [])

        # Clip ID uniqueness
        if clip_id in seen_clip_ids:
            errors.append(f"Duplicate clip_id '{clip_id}' found at index {clip_idx}.")
        seen_clip_ids.add(clip_id)

        # Anti-leakage scene mapping
        if scene_id and split:
            scene_to_splits[scene_id].add(split)

        # Media path existence
        if check_media and media_relpath:
            full_media_path = root / media_relpath
            if not full_media_path.is_file():
                errors.append(
                    f"Clip '{clip_id}': media file does not exist at '{media_relpath}'."
                )

        # Negative control consistency
        if is_negative:
            positive_events = [
                e for e in events if e.get("review_status") != "rejected"
            ]
            if positive_events:
                errors.append(
                    f"Negative control clip '{clip_id}' contains {len(positive_events)} positive event annotations."
                )

        # Event level checks
        for event_idx, event in enumerate(events):
            event_id = event.get("event_id", f"<clip_{clip_id}_evt_{event_idx}>")
            trigger = event.get("trigger")
            start = event.get("start")
            end = event.get("end")
            status = event.get("review_status")

            # Event ID uniqueness
            if event_id in seen_event_ids:
                errors.append(f"Duplicate event_id '{event_id}' in clip '{clip_id}'.")
            seen_event_ids.add(event_id)

            # Trigger nomenclature
            if trigger not in CANONICAL_TRIGGERS:
                errors.append(
                    f"Clip '{clip_id}', event '{event_id}': non-canonical trigger '{trigger}'."
                )

            # Temporal intervals
            if start is not None and end is not None:
                if start < 0.0:
                    errors.append(
                        f"Clip '{clip_id}', event '{event_id}': start timestamp {start} is negative."
                    )
                if end <= start:
                    errors.append(
                        f"Clip '{clip_id}', event '{event_id}': invalid interval [{start}, {end}]. End must be strictly greater than start."
                    )
                if duration is not None and end > (duration + 1e-3):
                    errors.append(
                        f"Clip '{clip_id}', event '{event_id}': end timestamp {end} exceeds clip duration {duration}."
                    )

            if status not in {"verified", "pending_review", "rejected"}:
                errors.append(
                    f"Clip '{clip_id}', event '{event_id}': invalid review_status '{status}'."
                )

    # 4. Anti-leakage Enforcement: zero scene overlap across splits
    for scene_id, splits_found in scene_to_splits.items():
        if len(splits_found) > 1:
            errors.append(
                f"Scene leakage detected: scene_id '{scene_id}' appears in multiple splits: {sorted(splits_found)}."
            )

    is_valid = len(errors) == 0
    return is_valid, errors


def validate_manifest_file(
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
    check_media: bool = True,
    repo_root: Path | str = REPO_ROOT,
) -> Tuple[bool, List[str]]:
    """Load and validate manifest file from disk."""
    path = Path(manifest_path)
    if not path.is_file():
        return False, [f"Benchmark manifest file not found: {path.resolve()}"]

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return False, [f"Failed to read or parse JSON from {path}: {exc}"]

    return validate_manifest_dict(
        manifest_data=data,
        schema_path=schema_path,
        check_media=check_media,
        repo_root=repo_root,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ground-truth benchmark manifest.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help=f"Path to benchmark manifest JSON (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to JSON schema (default: {DEFAULT_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--no-check-media",
        action="store_true",
        help="Skip verification of physical media file existence on disk.",
    )
    args = parser.parse_args()

    is_valid, errors = validate_manifest_file(
        manifest_path=args.manifest,
        schema_path=args.schema,
        check_media=not args.no_check_media,
    )

    if not is_valid:
        print(f"FAILED: Benchmark manifest validation failed with {len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"SUCCESS: Benchmark manifest {args.manifest} is valid.")
    try:
        with args.manifest.open("r", encoding="utf-8") as f:
            data = json.load(f)
        clips = data.get("clips", [])
        verified_events = sum(
            1 for c in clips for e in c.get("events", []) if e.get("review_status") == "verified"
        )
        pending_events = sum(
            1 for c in clips for e in c.get("events", []) if e.get("review_status") == "pending_review"
        )
        neg_controls = sum(1 for c in clips if c.get("is_negative_control"))
        nat_video_verified = sum(
            1 for c in clips if c.get("dataset_type") == "natural" and c.get("modality") in {"video", "audiovisual"}
            for e in c.get("events", []) if e.get("review_status") == "verified"
        )
        print(f"  Clips: {len(clips)} (Negative controls: {neg_controls})")
        print(f"  Verified events: {verified_events} | Pending-review events: {pending_events}")
        print(f"  Natural video verified ground truth: {nat_video_verified} events "
              f"(Guard: natural video accuracy claims are unvalidated).")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
