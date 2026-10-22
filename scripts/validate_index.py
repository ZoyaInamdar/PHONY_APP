#!/usr/bin/env python3
"""Validation script for Trigger-Aware Player packaged cue index (index.json).

Validates an index against schema/index.schema.json using JSON Schema Draft 2020-12
and enforces domain business rules:
- True configuration-to-schema trigger synchronization (no drift)
- Interval validity (end > start, non-negative, finite)
- Timestamp bounds against timeline duration
- Strict rejection of NaN and infinite values
- Canonical trigger ID verification against config.yaml
- Unique cue IDs
- Chronological sorting order (start, end, trigger, source)
- Optional media duration verification against actual video via ffprobe

Usage:
    python scripts/validate_index.py
    python scripts/validate_index.py --index data/index.json --schema schema/index.schema.json
    python scripts/validate_index.py --index data/index.json --media data/media/sintel_trailer.mp4 --duration-tolerance 0.1
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure repository root is on sys.path for direct CLI script execution
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import jsonschema
import yaml

DEFAULT_INDEX_PATH = Path("data/index.json")
DEFAULT_SCHEMA_PATH = Path("schema/index.schema.json")
DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_DURATION_TOLERANCE = 0.1


def reject_json_constant(c: str) -> None:
    """Callback to reject non-standard JSON constants like NaN, Infinity, -Infinity."""
    raise ValueError(f"Non-finite numeric constant '{c}' is not permitted in JSON index.")


def load_index(index_path: Path | str = DEFAULT_INDEX_PATH) -> Dict[str, Any]:
    """Load and parse a JSON index file, strictly rejecting non-finite constants."""
    path = Path(index_path)
    if not path.is_file():
        raise FileNotFoundError(f"Index file not found: {path.resolve()}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f, parse_constant=reject_json_constant)
    if not isinstance(data, dict):
        raise ValueError(f"Index file at {path} must contain a top-level JSON object.")
    return data


def load_schema(schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> Dict[str, Any]:
    """Load and parse the JSON schema file."""
    path = Path(schema_path)
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {path.resolve()}")
    with path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    if not isinstance(schema, dict):
        raise ValueError(f"Schema file at {path} must contain a top-level JSON object.")
    return schema


def load_config_data(config_path: Path | str = DEFAULT_CONFIG_PATH) -> Optional[Dict[str, Any]]:
    """Load config.yaml if present, returning None if unreadable."""
    path = Path(config_path)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg if isinstance(cfg, dict) else None
    except Exception:
        return None


def get_canonical_triggers_from_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> Set[str]:
    """Extract canonical trigger IDs from config.yaml, or fall back to known baseline."""
    cfg = load_config_data(config_path)
    if cfg and "triggers" in cfg and isinstance(cfg["triggers"], dict):
        return set(cfg["triggers"].keys())
    # Fallback to canonical list if config is missing or unreadable
    return {
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


def check_trigger_synchronization(
    config_data: Dict[str, Any], schema_data: Dict[str, Any]
) -> List[str]:
    """Ensure true synchronization between canonical trigger IDs in config.yaml and index schema enum.

    Detects missing, unexpected, duplicate, misspelled, or renamed triggers.
    """
    errors: List[str] = []

    # 1. Extract triggers from config.yaml
    cfg_triggers = config_data.get("triggers", {})
    if not isinstance(cfg_triggers, dict) or not cfg_triggers:
        errors.append("Trigger synchronization error: config.yaml has no valid 'triggers' mapping.")
        return errors
    config_trigger_set = set(cfg_triggers.keys())

    # 2. Extract enum list from schema
    try:
        schema_enum = (
            schema_data["$defs"]["packaged_cue"]["properties"]["trigger"]["enum"]
        )
    except KeyError as exc:
        errors.append(f"Trigger synchronization error: schema missing trigger enum at expected path: {exc}")
        return errors

    if not isinstance(schema_enum, list):
        errors.append("Trigger synchronization error: schema trigger enum is not a list.")
        return errors

    # Check for duplicate entries in schema enum
    counts = collections.Counter(schema_enum)
    duplicates = [trig for trig, count in counts.items() if count > 1]
    if duplicates:
        errors.append(
            f"Trigger synchronization error: Duplicate trigger ID(s) found in schema enum: {sorted(duplicates)}"
        )

    schema_trigger_set = set(schema_enum)

    # Compare actual sets
    missing_in_schema = config_trigger_set - schema_trigger_set
    unexpected_in_schema = schema_trigger_set - config_trigger_set

    if missing_in_schema:
        errors.append(
            f"Trigger synchronization error: Trigger(s) defined in config.yaml missing from schema enum: {sorted(missing_in_schema)}"
        )
    if unexpected_in_schema:
        errors.append(
            f"Trigger synchronization error: Unexpected trigger(s) in schema enum not defined in config.yaml: {sorted(unexpected_in_schema)}"
        )

    return errors


def validate_schema_compliance(
    index_data: Dict[str, Any], schema: Dict[str, Any]
) -> List[str]:
    """Validate index dict against the JSON Schema Draft 2020-12."""
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    errors: List[str] = []
    for err in validator.iter_errors(index_data):
        path_str = " -> ".join(str(p) for p in err.path) if err.path else "root"
        errors.append(f"Schema error at [{path_str}]: {err.message}")
    return errors


def check_for_non_finite_numbers(obj: Any, path: str = "root") -> List[str]:
    """Recursively check for NaN or infinite float values."""
    errors: List[str] = []
    if isinstance(obj, float):
        if math.isnan(obj):
            errors.append(f"Invalid non-finite number (NaN) at [{path}].")
        elif math.isinf(obj):
            errors.append(f"Invalid non-finite number (Infinity) at [{path}].")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            errors.extend(check_for_non_finite_numbers(v, f"{path} -> {k}"))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            errors.extend(check_for_non_finite_numbers(item, f"{path}[{idx}]"))
    return errors


def probe_media_duration(media_path: Path | str) -> float:
    """Probe media presentation duration in seconds using ffprobe.

    Raises FileNotFoundError if media file is missing.
    Raises RuntimeError if ffprobe is not installed in PATH.
    Raises ValueError if duration cannot be extracted or is non-finite.
    """
    path = Path(media_path)
    if not path.is_file():
        raise FileNotFoundError(f"Media file not found: {path.resolve()}")

    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe executable not found in PATH; cannot perform media duration verification.")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path.resolve()),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffprobe execution failed: {exc.stderr.strip()}") from exc

    try:
        data = json.loads(proc.stdout)
        duration_str = data["format"]["duration"]
        duration = float(duration_str)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not parse valid presentation duration from ffprobe output: {exc}") from exc

    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Probed media duration must be positive and finite, got {duration}")

    return duration


def verify_media_duration(
    declared_duration: float,
    media_path: Path | str,
    tolerance: float = DEFAULT_DURATION_TOLERANCE,
) -> Tuple[bool, Optional[str]]:
    """Compare declared index timeline duration with actual media presentation duration.

    Returns:
        (is_match, error_message_if_any)
    """
    try:
        actual_duration = probe_media_duration(media_path)
    except Exception as exc:
        return False, f"Media verification failed: {exc}"

    diff = abs(declared_duration - actual_duration)
    if diff > tolerance:
        return (
            False,
            f"Media duration mismatch: index declared {declared_duration:.3f}s, but actual media duration is "
            f"{actual_duration:.3f}s (difference {diff:.3f}s exceeds tolerance {tolerance:.3f}s).",
        )
    return True, None


def validate_domain_rules(
    index_data: Dict[str, Any],
    canonical_triggers: Set[str],
) -> List[str]:
    """Enforce domain business rules beyond basic JSON schema validation."""
    errors: List[str] = []

    # 1. Non-finite numbers check across the entire structure
    errors.extend(check_for_non_finite_numbers(index_data))

    # 2. Timeline validation
    timeline = index_data.get("timeline")
    film_duration: Optional[float] = None
    if isinstance(timeline, dict):
        dur = timeline.get("duration_seconds")
        if isinstance(dur, (int, float)) and not isinstance(dur, bool):
            dur_f = float(dur)
            if not math.isfinite(dur_f) or dur_f <= 0.0:
                errors.append(f"timeline.duration_seconds must be positive and finite, got {dur}.")
            else:
                film_duration = dur_f
        else:
            errors.append(f"timeline.duration_seconds must be a positive number, got {dur}.")

        # Validate fps / frame_rate if present
        for fps_key in ["fps", "frame_rate"]:
            if fps_key in timeline:
                fps_val = timeline[fps_key]
                if not isinstance(fps_val, (int, float)) or isinstance(fps_val, bool) or not math.isfinite(float(fps_val)) or float(fps_val) <= 0.0:
                    errors.append(f"timeline.{fps_key} must be a positive finite number, got {fps_val}.")

    # 3. Cues validation
    cues = index_data.get("cues")
    if not isinstance(cues, list):
        # Already reported by schema validation
        return errors

    seen_cue_ids: Set[str] = set()
    prev_sort_key: Optional[Tuple[float, float, str, str]] = None

    for idx, cue in enumerate(cues):
        if not isinstance(cue, dict):
            errors.append(f"cue[{idx}] must be a JSON object.")
            continue

        cue_id = cue.get("id")
        cue_label = f"cues[{idx}] (id='{cue_id}')"

        # Unique cue ID
        if isinstance(cue_id, str):
            if not cue_id.strip():
                errors.append(f"{cue_label}: Cue 'id' must be a non-empty string.")
            elif cue_id in seen_cue_ids:
                errors.append(f"Duplicate cue id '{cue_id}' detected at {cue_label}.")
            seen_cue_ids.add(cue_id)
        else:
            errors.append(f"Missing or invalid id at cues[{idx}].")

        # Start and End intervals
        start = cue.get("start")
        end = cue.get("end")
        has_valid_times = True

        if not isinstance(start, (int, float)) or isinstance(start, bool):
            has_valid_times = False
            errors.append(f"{cue_label}: 'start' must be a numeric timestamp.")
        if not isinstance(end, (int, float)) or isinstance(end, bool):
            has_valid_times = False
            errors.append(f"{cue_label}: 'end' must be a numeric timestamp.")

        if has_valid_times:
            start_f = float(start)
            end_f = float(end)

            if not math.isfinite(start_f) or start_f < 0.0:
                errors.append(f"{cue_label}: 'start' ({start_f}) must be a finite number >= 0.0.")

            if not math.isfinite(end_f) or end_f <= start_f:
                errors.append(
                    f"{cue_label}: 'end' ({end_f}) must be strictly greater than 'start' ({start_f}) and finite."
                )

            if film_duration is not None:
                if start_f > film_duration:
                    errors.append(
                        f"{cue_label}: 'start' ({start_f}) exceeds declared film duration ({film_duration}s)."
                    )
                if end_f > film_duration:
                    errors.append(
                        f"{cue_label}: 'end' ({end_f}) exceeds declared film duration ({film_duration}s)."
                    )

            # Chronological ordering check: start, then end, then trigger, then source
            trigger_str = str(cue.get("trigger", ""))
            source_str = str(cue.get("source", ""))
            current_sort_key = (start_f, end_f, trigger_str, source_str)

            if prev_sort_key is not None:
                if current_sort_key < prev_sort_key:
                    errors.append(
                        f"{cue_label}: Chronological sorting violation. Current cue key {current_sort_key} "
                        f"appears after previous cue key {prev_sort_key}."
                    )
            prev_sort_key = current_sort_key

        # Canonical Trigger validation
        trigger = cue.get("trigger")
        if isinstance(trigger, str):
            if trigger not in canonical_triggers:
                errors.append(
                    f"{cue_label}: Trigger '{trigger}' is not recognized in canonical triggers {sorted(canonical_triggers)}."
                )

        # Score range
        score = cue.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            score_f = float(score)
            if not math.isfinite(score_f) or score_f < 0.0 or score_f > 1.0:
                errors.append(f"{cue_label}: Score {score_f} must be a finite number within [0.0, 1.0].")
        else:
            errors.append(f"{cue_label}: Score must be a numeric value in [0.0, 1.0].")

        # Source check
        source = cue.get("source")
        if isinstance(source, str):
            if not source.strip():
                errors.append(f"{cue_label}: 'source' must be a non-empty string.")
        else:
            errors.append(f"{cue_label}: 'source' must be a string.")

    return errors


def validate_index(
    index_path: Path | str = DEFAULT_INDEX_PATH,
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    media_path: Optional[Path | str] = None,
    duration_tolerance: float = DEFAULT_DURATION_TOLERANCE,
) -> Tuple[bool, List[str]]:
    """Validate an index JSON file against schema, domain rules, config trigger synchronization,
    and optionally actual media duration via ffprobe.

    Returns:
        (is_valid, list_of_error_strings)
    """
    try:
        index_data = load_index(index_path)
    except Exception as exc:
        return False, [f"Failed to load index file: {exc}"]

    try:
        schema = load_schema(schema_path)
    except Exception as exc:
        return False, [f"Failed to load schema file: {exc}"]

    all_errors = []

    # Check configuration synchronization if config.yaml exists
    config_data = load_config_data(config_path)
    if config_data:
        all_errors.extend(check_trigger_synchronization(config_data, schema))
        canonical_triggers = set(config_data.get("triggers", {}).keys())
    else:
        canonical_triggers = get_canonical_triggers_from_config(config_path)

    # Schema compliance
    all_errors.extend(validate_schema_compliance(index_data, schema))

    # Domain rules compliance
    all_errors.extend(validate_domain_rules(index_data, canonical_triggers))

    # Media verification if requested
    if media_path is not None:
        timeline = index_data.get("timeline", {})
        declared_duration = timeline.get("duration_seconds")
        if isinstance(declared_duration, (int, float)) and math.isfinite(float(declared_duration)):
            is_match, media_err = verify_media_duration(
                float(declared_duration), media_path, tolerance=duration_tolerance
            )
            if not is_match and media_err:
                all_errors.append(media_err)
        else:
            all_errors.append("Media verification requested, but index lacks a valid timeline.duration_seconds.")

    return len(all_errors) == 0, all_errors


def main() -> int:
    """CLI entrypoint for index validation."""
    parser = argparse.ArgumentParser(
        description="Validate Trigger-Aware Player packaged index (index.json)."
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=DEFAULT_INDEX_PATH,
        help="Path to index.json (default: data/index.json)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help="Path to schema/index.schema.json (default: schema/index.schema.json)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--media",
        type=Path,
        default=None,
        help="Optional path to media video file to verify declared timeline duration.",
    )
    parser.add_argument(
        "--duration-tolerance",
        type=float,
        default=DEFAULT_DURATION_TOLERANCE,
        help=f"Acceptable duration tolerance in seconds when verifying media (default: {DEFAULT_DURATION_TOLERANCE}).",
    )
    args = parser.parse_args()

    print(f"Validating packaged index: {args.index.resolve()}")
    print(f"Using JSON schema:         {args.schema.resolve()}")
    print(f"Using config for triggers: {args.config.resolve()}")
    if args.media:
        print(f"Verifying actual media:    {args.media.resolve()} (tolerance: {args.duration_tolerance}s)")

    is_valid, errors = validate_index(
        index_path=args.index,
        schema_path=args.schema,
        config_path=args.config,
        media_path=args.media,
        duration_tolerance=args.duration_tolerance,
    )

    if is_valid:
        print("\nSUCCESS: index.json passed all schema and domain validation checks.")
        return 0
    else:
        print(f"\nFAILURE: Found {len(errors)} validation error(s):")
        for idx, err in enumerate(errors, 1):
            print(f"  {idx}. {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
