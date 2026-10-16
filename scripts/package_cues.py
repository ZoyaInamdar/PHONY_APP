#!/usr/bin/env python3
"""Packaging pipeline for Trigger-Aware Player cues.

Merges raw detection records and packages them into the index.json format.
Enforces:
- Deterministic, collision-safe cue IDs
- Maximum segment duration policy (splits segment when merge would exceed limit)
- Same trigger and source merge requirement (never cross-merges)
- Strict validation of raw inputs and packaged index
- Atomic file writing (failed runs never leave partial/corrupt files)

Usage:
    python scripts/package_cues.py --input data/cues.json --output data/index.json
    python scripts/package_cues.py --input data/cues.json --output data/index.json --film-id sintel --duration 52.0 --fps 24.0
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# Ensure repository root is on sys.path for direct CLI script execution
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from scripts.validate_index import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SCHEMA_PATH,
    get_canonical_triggers_from_config,
    load_config_data,
    load_schema,
    validate_domain_rules,
    validate_schema_compliance,
)

DEFAULT_CUES_INPUT_PATH = Path("data/cues.json")
DEFAULT_INDEX_OUTPUT_PATH = Path("data/index.json")
DEFAULT_MAX_SEGMENT_DURATION = 60.0
DEFAULT_MERGE_GAP = 2.5


class CueIdCollisionError(ValueError):
    """Raised when two distinct cue records generate the exact same cue ID."""
    pass


def generate_cue_id(film_id: str, trigger: str, start: float, end: float) -> str:
    """Generate a deterministic, reproducible cue ID from film, trigger, and millisecond timestamps.

    Conversion rule:
        start_ms = int(round(start * 1000.0))
        end_ms = int(round(end * 1000.0))
        If rounding causes end_ms <= start_ms, end_ms is guaranteed to be at least start_ms + 1.

    Format:
        cue_{film_id}_{trigger}_{start_ms:06d}_{end_ms:06d}
    """
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        raise ValueError(f"Timestamps must be numeric: start={start}, end={end}")

    start_f = float(start)
    end_f = float(end)

    if not math.isfinite(start_f) or not math.isfinite(end_f):
        raise ValueError(f"Timestamps must be finite numbers: start={start_f}, end={end_f}")

    if start_f < 0.0:
        raise ValueError(f"start timestamp must be non-negative: {start_f}")

    if end_f <= start_f:
        raise ValueError(f"end timestamp ({end_f}) must be strictly greater than start ({start_f})")

    start_ms = int(round(start_f * 1000.0))
    end_ms = int(round(end_f * 1000.0))

    if end_ms <= start_ms:
        end_ms = start_ms + 1

    clean_film_id = str(film_id).strip().replace(" ", "_").lower()
    clean_trigger = str(trigger).strip().lower()

    return f"cue_{clean_film_id}_{clean_trigger}_{start_ms:06d}_{end_ms:06d}"


def validate_raw_detection(
    det: Dict[str, Any],
    canonical_triggers: Set[str],
    max_allowed_duration: float = DEFAULT_MAX_SEGMENT_DURATION,
    cue_index: int = 0,
) -> None:
    """Validate a single raw detection record according to the shared contract."""
    if not isinstance(det, dict):
        raise ValueError(f"Raw detection at index {cue_index} must be a dictionary.")

    for field in ["start", "end", "trigger", "score", "source"]:
        if field not in det:
            raise ValueError(f"Raw detection at index {cue_index} missing required field '{field}': {det}")

    start = det["start"]
    end = det["end"]
    trigger = det["trigger"]
    score = det["score"]
    source = det["source"]

    if not isinstance(start, (int, float)) or isinstance(start, bool) or not math.isfinite(float(start)):
        raise ValueError(f"Raw detection at index {cue_index}: 'start' must be a finite number: {start}")
    if not isinstance(end, (int, float)) or isinstance(end, bool) or not math.isfinite(float(end)):
        raise ValueError(f"Raw detection at index {cue_index}: 'end' must be a finite number: {end}")

    start_f = float(start)
    end_f = float(end)

    if start_f < 0.0:
        raise ValueError(f"Raw detection at index {cue_index}: 'start' must be >= 0.0, got {start_f}")
    if end_f <= start_f:
        raise ValueError(
            f"Raw detection at index {cue_index}: 'end' ({end_f}) must be strictly greater than 'start' ({start_f})"
        )

    duration = end_f - start_f
    if duration > max_allowed_duration:
        raise ValueError(
            f"Raw detection at index {cue_index} duration ({duration:.3f}s) exceeds configured maximum allowable "
            f"segment duration ({max_allowed_duration:.3f}s) for trigger '{trigger}': start={start_f}, end={end_f}"
        )

    if trigger not in canonical_triggers:
        raise ValueError(
            f"Raw detection at index {cue_index}: Trigger '{trigger}' is not recognized in canonical triggers {sorted(canonical_triggers)}"
        )

    if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(float(score)):
        raise ValueError(f"Raw detection at index {cue_index}: 'score' must be a finite number: {score}")
    score_f = float(score)
    if score_f < 0.0 or score_f > 1.0:
        raise ValueError(f"Raw detection at index {cue_index}: 'score' must be in [0.0, 1.0], got {score_f}")

    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"Raw detection at index {cue_index}: 'source' must be a non-empty string.")


def merge_detections(
    detections: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    max_segment_duration: float = DEFAULT_MAX_SEGMENT_DURATION,
    default_merge_gap: float = DEFAULT_MERGE_GAP,
    canonical_triggers: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Merge raw detection records according to the maximum segment duration and merge gap policy.

    Policy rules:
    1. Only merge detections when BOTH 'trigger' and 'source' match.
    2. The gap between the current merged segment and the next detection must be <= merge_gap_seconds.
    3. The resulting segment must never exceed max_segment_duration_seconds.
    4. If the next detection would cause the segment to exceed max duration, finalize the current
       segment and start a new segment with the next detection.
    5. Single raw detections exceeding max_segment_duration_seconds are rejected with a validation error.
    6. Overlapping detections of the same trigger and source are merged.
    7. No detections are silently discarded.
    8. Preserves deterministic chronological ordering: sorted by start, end, trigger, source.
    """
    if not detections:
        return []

    if canonical_triggers is None:
        if config and "triggers" in config and isinstance(config["triggers"], dict):
            canonical_triggers = set(config["triggers"].keys())
        else:
            canonical_triggers = {
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

    # Extract global merge settings from config if available
    if config and "merge" in config and isinstance(config["merge"], dict):
        merge_sec = config["merge"]
        default_merge_gap = float(merge_sec.get("default_merge_gap_seconds", default_merge_gap))
        max_segment_duration = float(merge_sec.get("max_segment_duration_seconds", max_segment_duration))

    # 1. Validate all raw detection records first
    for idx, det in enumerate(detections):
        trigger = str(det.get("trigger", ""))
        # Check per-trigger max duration override if specified in config
        t_max_dur = max_segment_duration
        if config and "triggers" in config and trigger in config["triggers"]:
            t_max_dur = float(config["triggers"][trigger].get("max_segment_duration_seconds", max_segment_duration))
        validate_raw_detection(det, canonical_triggers, max_allowed_duration=t_max_dur, cue_index=idx)

    # 2. Group detections strictly by (trigger, source)
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = collections.defaultdict(list)
    for det in detections:
        key = (str(det["trigger"]), str(det["source"]))
        groups[key].append(det)

    all_merged: List[Dict[str, Any]] = []

    # 3. Process each group independently
    for (trigger, source), group in groups.items():
        # Look up per-trigger gap and max duration settings
        t_merge_gap = default_merge_gap
        t_max_duration = max_segment_duration
        if config and "triggers" in config and trigger in config["triggers"]:
            t_conf = config["triggers"][trigger]
            t_merge_gap = float(t_conf.get("merge_gap_seconds", default_merge_gap))
            t_max_duration = float(t_conf.get("max_segment_duration_seconds", max_segment_duration))

        # Sort group deterministically by (start, end, score)
        sorted_group = sorted(
            group,
            key=lambda d: (float(d["start"]), float(d["end"]), float(d["score"])),
        )

        current_segment: Optional[Dict[str, Any]] = None

        for det in sorted_group:
            d_start = float(det["start"])
            d_end = float(det["end"])
            d_score = float(det["score"])

            if current_segment is None:
                current_segment = {
                    "start": d_start,
                    "end": d_end,
                    "trigger": trigger,
                    "score": d_score,
                    "source": source,
                }
                continue

            # Calculate gap between current merged segment and next detection
            if d_start <= current_segment["end"]:
                gap = 0.0  # Overlapping or contiguous interval
            else:
                gap = d_start - current_segment["end"]

            # Potential merged boundaries
            potential_start = current_segment["start"]
            potential_end = max(current_segment["end"], d_end)
            potential_duration = potential_end - potential_start

            # Check merge conditions:
            # 1. gap <= t_merge_gap
            # 2. potential_duration <= t_max_duration
            if gap <= t_merge_gap and potential_duration <= t_max_duration:
                # Merge into current segment
                current_segment["end"] = potential_end
                current_segment["score"] = max(current_segment["score"], d_score)
            else:
                # Cannot merge: finalize current segment and begin new segment
                all_merged.append(current_segment)
                current_segment = {
                    "start": d_start,
                    "end": d_end,
                    "trigger": trigger,
                    "score": d_score,
                    "source": source,
                }

        if current_segment is not None:
            all_merged.append(current_segment)

    # 4. Final deterministic sort across all groups by start, end, trigger, source
    all_merged.sort(
        key=lambda s: (float(s["start"]), float(s["end"]), str(s["trigger"]), str(s["source"]))
    )

    return all_merged


def adapt_subtitle_cues_to_raw_detections(
    subtitle_data: Union[Dict[str, Any], List[Dict[str, Any]]],
    default_source: str = "subtitles",
) -> List[Dict[str, Any]]:
    """Adapt Person C subtitle cues or detection records into raw detection dictionaries.

    Accepts:
    1. A list of cue dictionaries (e.g. from cues.json or cues_to_dicts):
       [{"id": "c1", "start": 1.0, "end": 3.5, "kind": "speech", "flagged_words": [...]}]
    2. A dictionary containing a "cues" list: {"cues": [...]}
    3. A dictionary containing a "detections" list: {"provenance": ..., "detections": [...]}
    4. A raw list of detection dictionaries already matching the detector contract:
       [{"start": 1.0, "end": 3.5, "trigger": "profanity", "score": 1.0, "source": "subtitles"}]

    Rules:
    - Filters out ordinary speech or sound descriptions without flagged words.
    - If a cue dictionary contains non-empty "flagged_words" or "flagged", it is converted
      to a canonical profanity raw detection:
      {"start": round(start, 3), "end": round(end, 3), "trigger": "profanity", "score": 1.0, "source": default_source}.
    - Existing raw detections (having "trigger") are validated and preserved without alteration.
    - Does not mutate input objects.
    - Returns a list of standardized raw detection dictionaries.
    """
    if subtitle_data is None:
        return []

    raw_items: List[Any]
    if isinstance(subtitle_data, dict):
        if "detections" in subtitle_data and isinstance(subtitle_data["detections"], list):
            raw_items = subtitle_data["detections"]
        elif "cues" in subtitle_data and isinstance(subtitle_data["cues"], list):
            raw_items = subtitle_data["cues"]
        else:
            raw_items = []
    elif isinstance(subtitle_data, list):
        raw_items = subtitle_data
    else:
        raise ValueError(
            f"subtitle_data must be a dictionary or list, got {type(subtitle_data).__name__}"
        )

    adapted_detections: List[Dict[str, Any]] = []

    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue

        # Case A: Already a detection record with explicit 'trigger'
        if "trigger" in item:
            start_val = item.get("start")
            end_val = item.get("end")
            if not isinstance(start_val, (int, float)) or not isinstance(end_val, (int, float)):
                raise ValueError(
                    f"Detection at index {idx} has non-numeric start/end: {item}"
                )
            start_f = float(start_val)
            end_f = float(end_val)
            if start_f < 0.0 or end_f <= start_f:
                raise ValueError(
                    f"Detection at index {idx} has invalid interval [{start_f}, {end_f}]"
                )
            score_f = float(item.get("score", 1.0))
            source_str = str(item.get("source", default_source))
            trigger_str = str(item["trigger"])

            adapted_detections.append({
                "start": round(start_f, 3),
                "end": round(end_f, 3),
                "trigger": trigger_str,
                "score": score_f,
                "source": source_str,
            })
            continue

        # Case B: Subtitle cue dictionary with flagged words / flagged terms
        flagged_list = item.get("flagged_words") or item.get("flagged") or []
        if flagged_list:
            start_val = item.get("start")
            end_val = item.get("end")
            if not isinstance(start_val, (int, float)) or not isinstance(end_val, (int, float)):
                raise ValueError(
                    f"Subtitle cue at index {idx} has non-numeric start/end: {item}"
                )
            start_f = float(start_val)
            end_f = float(end_val)
            if start_f < 0.0 or end_f <= start_f:
                raise ValueError(
                    f"Subtitle cue at index {idx} has invalid interval [{start_f}, {end_f}]"
                )

            adapted_detections.append({
                "start": round(start_f, 3),
                "end": round(end_f, 3),
                "trigger": "profanity",
                "score": 1.0,
                "source": default_source,
            })
            continue

        # Case C: Ordinary dialogue or sound description without flagged words -> skip

    return adapted_detections


def package_cues(
    raw_data: Dict[str, Any] | List[Dict[str, Any]],
    film_id: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    fps: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
    synthetic: Optional[bool] = None,
    description: Optional[str] = None,
    schema: Optional[Dict[str, Any]] = None,
    subtitles: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Package raw detections into a fully-validated player index dictionary.

    - Resolves metadata defaults
    - Integrates subtitle profanity cues via adapter if provided
    - Applies merge policy
    - Generates deterministic, collision-safe cue IDs
    - Validates packaged output against index schema and business rules
    """
    # 1. Extract raw detections list and metadata
    if isinstance(raw_data, dict):
        detections = list(raw_data.get("detections", []))
        if film_id is None:
            film_id = raw_data.get("film_id")
        if synthetic is None:
            synthetic = raw_data.get("synthetic", True)
        if description is None:
            description = raw_data.get("description")
        if duration_seconds is None and "timeline" in raw_data and isinstance(raw_data["timeline"], dict):
            duration_seconds = raw_data["timeline"].get("duration_seconds")
        if subtitles is None and "subtitles" in raw_data:
            subtitles = raw_data.get("subtitles")
    elif isinstance(raw_data, list):
        detections = list(raw_data)
    else:
        raise ValueError(f"raw_data must be a dictionary or list, got {type(raw_data)}")

    # Integrate subtitle profanity cues if provided
    if subtitles is not None:
        sub_dets = adapt_subtitle_cues_to_raw_detections(subtitles)
        detections.extend(sub_dets)

    # Resolve film_id default
    if not film_id:
        if config and "project_metadata" in config:
            film_id = config["project_metadata"].get("default_film_id", "sintel")
        else:
            film_id = "sintel"

    if synthetic is None:
        synthetic = True

    # Resolve timeline duration
    if duration_seconds is None:
        if config and "evaluation" in config:
            eval_clips = config["evaluation"].get("evaluation_clips", {}).get("clips", [])
            for clip in eval_clips:
                if clip.get("clip_id") == f"{film_id}_trailer":
                    duration_seconds = float(clip.get("duration_seconds", 52.0))
                    break
        if duration_seconds is None:
            duration_seconds = 52.0  # Sintel trailer baseline default

    duration_seconds = float(duration_seconds)
    if not math.isfinite(duration_seconds) or duration_seconds <= 0.0:
        raise ValueError(f"duration_seconds must be a positive finite number, got {duration_seconds}")

    if fps is not None:
        fps = float(fps)
        if not math.isfinite(fps) or fps <= 0.0:
            raise ValueError(f"fps must be a positive finite number, got {fps}")

    canonical_triggers = set(config["triggers"].keys()) if config and "triggers" in config else None

    # 2. Merge raw detections
    merged_segments = merge_detections(
        detections=detections,
        config=config,
        canonical_triggers=canonical_triggers,
    )

    # 3. Generate deterministic cue IDs and detect collisions
    packaged_cues: List[Dict[str, Any]] = []
    seen_ids: Dict[str, Dict[str, Any]] = {}

    for seg in merged_segments:
        start_f = float(seg["start"])
        end_f = float(seg["end"])
        trigger_str = str(seg["trigger"])
        source_str = str(seg["source"])
        score_f = float(seg["score"])

        cue_id = generate_cue_id(film_id, trigger_str, start_f, end_f)

        # Check for collision
        if cue_id in seen_ids:
            prev = seen_ids[cue_id]
            if (
                prev["start"] != start_f
                or prev["end"] != end_f
                or prev["trigger"] != trigger_str
                or prev["source"] != source_str
            ):
                raise CueIdCollisionError(
                    f"Cue ID collision detected for ID '{cue_id}': distinct cues "
                    f"prev=(start={prev['start']}, end={prev['end']}, trigger='{prev['trigger']}', source='{prev['source']}') and "
                    f"curr=(start={start_f}, end={end_f}, trigger='{trigger_str}', source='{source_str}') "
                    f"both produced identical cue ID."
                )

        cue_record = {
            "id": cue_id,
            "start": start_f,
            "end": end_f,
            "trigger": trigger_str,
            "score": score_f,
            "source": source_str,
        }
        seen_ids[cue_id] = cue_record
        packaged_cues.append(cue_record)

    # 4. Construct packaged index dictionary
    index_data: Dict[str, Any] = {
        "schema_version": 1,
        "film_id": film_id,
        "synthetic": bool(synthetic),
        "description": description or f"Packaged player cue index for {film_id}.",
        "timeline": {
            "duration_seconds": duration_seconds,
            "timestamp_unit": "seconds",
            "interval_convention": "half_open",
        },
        "cues": packaged_cues,
    }
    if fps is not None:
        index_data["timeline"]["fps"] = fps

    # 5. Validate the generated index before returning
    if schema is None:
        schema = load_schema(DEFAULT_SCHEMA_PATH)

    schema_errors = validate_schema_compliance(index_data, schema)
    if schema_errors:
        raise ValueError(f"Packaged index failed schema compliance: {schema_errors}")

    canonical_triggers_set = canonical_triggers or get_canonical_triggers_from_config(DEFAULT_CONFIG_PATH)
    domain_errors = validate_domain_rules(index_data, canonical_triggers_set)
    if domain_errors:
        raise ValueError(f"Packaged index failed domain business rules: {domain_errors}")

    return index_data


def atomic_write_json(data: Dict[str, Any], output_path: Path | str, indent: int = 2) -> None:
    """Serialize data to JSON and write to output_path atomically via a temporary file."""
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    json_text = json.dumps(data, indent=indent, allow_nan=False) + "\n"

    # Write to a temporary file in the same directory, then replace atomically
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    try:
        temp_file.write(json_text)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()

        # Atomic rename / replace
        os.replace(temp_path, path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def package_and_save_index(
    input_path: Path | str = DEFAULT_CUES_INPUT_PATH,
    output_path: Path | str = DEFAULT_INDEX_OUTPUT_PATH,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
    film_id: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    fps: Optional[float] = None,
    subtitles_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Load raw cues, apply merge and packaging, validate, and write atomically to output_path."""
    in_path = Path(input_path)
    if not in_path.is_file():
        raise FileNotFoundError(f"Raw cues input file not found: {in_path.resolve()}")

    with in_path.open("r", encoding="utf-8") as f:
        raw_data = json.load(f)

    subtitles_data = None
    if subtitles_path is not None:
        sub_p = Path(subtitles_path)
        if not sub_p.is_file():
            raise FileNotFoundError(f"Subtitles file not found: {sub_p.resolve()}")
        with sub_p.open("r", encoding="utf-8") as f:
            subtitles_data = json.load(f)

    config_data = load_config_data(config_path)
    schema_data = load_schema(schema_path)

    index_data = package_cues(
        raw_data=raw_data,
        film_id=film_id,
        duration_seconds=duration_seconds,
        fps=fps,
        config=config_data,
        schema=schema_data,
        subtitles=subtitles_data,
    )

    atomic_write_json(index_data, output_path)
    return index_data


def main() -> int:
    """CLI entrypoint for cues packaging."""
    parser = argparse.ArgumentParser(
        description="Package raw detection records into a Trigger-Aware Player cue index (index.json)."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_CUES_INPUT_PATH,
        help="Path to raw detections JSON (default: data/cues.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_INDEX_OUTPUT_PATH,
        help="Path to output packaged index JSON (default: data/index.json)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to pipeline config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help="Path to schema/index.schema.json (default: schema/index.schema.json)",
    )
    parser.add_argument(
        "--film-id",
        type=str,
        default=None,
        help="Film identifier (e.g. 'sintel'). Defaults to metadata in input or config.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Declared media duration in seconds. Defaults to metadata in input or config.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=None,
        help="Optional video frame rate in frames per second.",
    )
    parser.add_argument(
        "--subtitles",
        type=Path,
        default=None,
        help="Optional path to subtitle cues or detections JSON (e.g. data/cues.json or detections/subtitles.json).",
    )
    args = parser.parse_args()

    print(f"Packaging cues from: {args.input.resolve()}")
    if args.subtitles:
        print(f"Integrating subtitles from: {args.subtitles.resolve()}")
    print(f"Target output index: {args.output.resolve()}")

    try:
        index_data = package_and_save_index(
            input_path=args.input,
            output_path=args.output,
            config_path=args.config,
            schema_path=args.schema,
            film_id=args.film_id,
            duration_seconds=args.duration,
            fps=args.fps,
            subtitles_path=args.subtitles,
        )
        cues_count = len(index_data.get("cues", []))
        print(f"\nSUCCESS: Packaged {cues_count} cue(s) into {args.output.resolve()} cleanly.")
        return 0
    except Exception as exc:
        print(f"\nFAILURE: Packaging failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
