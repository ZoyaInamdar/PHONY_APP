#!/usr/bin/env python3
"""Multimodal Compound Jump Scare Detector for Trigger-Aware Player (Person B & C).

Detects compound 'jump_scare' events by fusing:
1. Audio evidence: Acoustic spikes detected by Person B's existing loudness detector
   (`scripts/detect_loudness.py`).
2. Visual evidence: Rapid inter-frame visual change / motion proxy detected across
   low-resolution frame differences.

A 'jump_scare' cue is emitted ONLY when qualifying evidence from BOTH modalities
occurs within a configurable temporal alignment window (default: 0.5s).

Contracts & Timing Invariants:
- Half-Open Intervals: All intervals strictly follow [start, end) where end is exclusive.
- Deterministic Output: Same inputs always produce identical output cues.
- Shared Contract Compliance:
    {
        "start": float,     # seconds, >= 0.0
        "end": float,       # seconds, > start
        "trigger": "jump_scare",
        "score": float,     # [0.0, 1.0] conservative compound geometric mean
        "source": "jump_scare_detector"
    }

Usage:
    python scripts/detect_jump_scare.py --input "data/test_clips/sync_test.mp4"
    python scripts/detect_jump_scare.py --input "video.mp4" --output "cues_jump_scare.json"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Sequence, Tuple, Union

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import yaml

from scripts.detect_loudness import (
    DEFAULT_CONFIG_PATH,
    detect_loudness_from_wav,
    load_detector_config,
)
from scripts.ingest_media import extract_audio

DEFAULT_SOURCE = "jump_scare_detector"
DEFAULT_TRIGGER = "jump_scare"

# Algorithmic defaults aligned with config.yaml (triggers.jump_scare.algorithm)
DEFAULT_TEMPORAL_ALIGNMENT_TOLERANCE_SECONDS = 0.5
DEFAULT_MIN_FRAME_DIFFERENCE = 0.25
DEFAULT_MIN_SEGMENT_DURATION_SECONDS = 0.5
DEFAULT_MAX_SEGMENT_DURATION_SECONDS = 10.0
DEFAULT_LOW_RES_WIDTH = 128
DEFAULT_LOW_RES_HEIGHT = 72


def load_jump_scare_config(config_path: Union[str, Path] = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load jump_scare detector configuration parameters from config.yaml."""
    cfg = load_detector_config(config_path)
    triggers_cfg = cfg.get("triggers", {})
    js_cfg = triggers_cfg.get("jump_scare", {})
    algo_cfg = js_cfg.get("algorithm", {})
    return {
        "temporal_alignment_tolerance_seconds": float(
            algo_cfg.get(
                "temporal_alignment_tolerance_seconds",
                DEFAULT_TEMPORAL_ALIGNMENT_TOLERANCE_SECONDS,
            )
        ),
        "min_frame_difference": float(
            algo_cfg.get("visual_change_condition", {}).get(
                "min_frame_difference", DEFAULT_MIN_FRAME_DIFFERENCE
            )
        ),
        "min_segment_duration_seconds": float(
            js_cfg.get("min_segment_duration_seconds", DEFAULT_MIN_SEGMENT_DURATION_SECONDS)
        ),
        "max_segment_duration_seconds": float(
            js_cfg.get("max_segment_duration_seconds", DEFAULT_MAX_SEGMENT_DURATION_SECONDS)
        ),
        "source": str(js_cfg.get("source", DEFAULT_SOURCE)),
    }


def intervals_overlap_or_within_tolerance(
    start_a: float,
    end_a: float,
    start_b: float,
    end_b: float,
    tolerance_seconds: float = DEFAULT_TEMPORAL_ALIGNMENT_TOLERANCE_SECONDS,
) -> Tuple[bool, float]:
    """Check if two half-open intervals [start_a, end_a) and [start_b, end_b) overlap
    or are separated by at most tolerance_seconds.

    Returns:
        (is_within_tolerance, separation_distance)
        If intervals overlap, separation_distance is 0.0.
    """
    if not (math.isfinite(start_a) and math.isfinite(end_a) and math.isfinite(start_b) and math.isfinite(end_b)):
        return False, float("inf")
    if start_a < 0.0 or start_b < 0.0 or end_a <= start_a or end_b <= start_b:
        return False, float("inf")

    # Overlapping condition for half-open intervals: max(start_a, start_b) < min(end_a, end_b)
    overlap = max(start_a, start_b) < min(end_a, end_b)
    if overlap:
        return True, 0.0

    # Non-overlapping: calculate distance between intervals
    if end_a <= start_b:
        gap = start_b - end_a
    elif end_b <= start_a:
        gap = start_a - end_b
    else:
        gap = 0.0

    gap = max(0.0, float(gap))
    return gap <= (tolerance_seconds + 1e-9), gap


def calculate_compound_score(score_audio: float, score_visual: float) -> float:
    """Calculate compound confidence score for multimodal jump scare detection.

    Uses geometric mean sqrt(score_audio * score_visual).
    This ensures:
    - Conservative combination: if either modality is 0, compound score is 0.
    - Never implies higher statistical confidence than source modalities.
    - Result is bounded in [0.0, 1.0].
    """
    sa = float(score_audio)
    sv = float(score_visual)
    if not (math.isfinite(sa) and math.isfinite(sv)):
        return 0.0
    sa = max(0.0, min(1.0, sa))
    sv = max(0.0, min(1.0, sv))
    return round(math.sqrt(sa * sv), 4)


def match_audio_visual_jump_scares(
    audio_events: Sequence[Dict[str, Any]],
    visual_events: Sequence[Dict[str, Any]],
    temporal_tolerance_seconds: float = DEFAULT_TEMPORAL_ALIGNMENT_TOLERANCE_SECONDS,
    min_duration_seconds: float = DEFAULT_MIN_SEGMENT_DURATION_SECONDS,
    max_duration_seconds: float = DEFAULT_MAX_SEGMENT_DURATION_SECONDS,
    source: str = DEFAULT_SOURCE,
) -> List[Dict[str, Any]]:
    """Match audio spike events and visual change events to produce compound jump_scare cues.

    Strict Requirements:
    1. A jump_scare event is emitted ONLY when BOTH audio and visual events occur within
       the configured temporal tolerance.
    2. Inputs are never modified.
    3. Handles empty, missing, or malformed intervals safely.
    4. Deterministic output, sorted chronologically.
    5. Deduplicates closely overlapping compound cues to prevent duplicate player actions.
    """
    if not audio_events or not visual_events:
        return []

    # Filter and validate audio events
    valid_audio: List[Dict[str, Any]] = []
    for ev in audio_events:
        if not isinstance(ev, dict):
            continue
        try:
            s = float(ev["start"])
            e = float(ev["end"])
            sc = float(ev.get("score", 1.0))
            if math.isfinite(s) and math.isfinite(e) and s >= 0.0 and e > s:
                valid_audio.append({"start": s, "end": e, "score": max(0.0, min(1.0, sc))})
        except (KeyError, ValueError, TypeError):
            continue

    # Filter and validate visual events
    valid_visual: List[Dict[str, Any]] = []
    for ev in visual_events:
        if not isinstance(ev, dict):
            continue
        try:
            s = float(ev["start"])
            e = float(ev["end"])
            sc = float(ev.get("score", 1.0))
            if math.isfinite(s) and math.isfinite(e) and s >= 0.0 and e > s:
                valid_visual.append({"start": s, "end": e, "score": max(0.0, min(1.0, sc))})
        except (KeyError, ValueError, TypeError):
            continue

    if not valid_audio or not valid_visual:
        return []

    # Sort deterministically
    valid_audio.sort(key=lambda x: (x["start"], x["end"]))
    valid_visual.sort(key=lambda x: (x["start"], x["end"]))

    candidates: List[Dict[str, Any]] = []

    # Match pairs where temporal gap <= tolerance
    for a in valid_audio:
        matching_v = []
        for v in valid_visual:
            within_tol, dist = intervals_overlap_or_within_tolerance(
                a["start"], a["end"], v["start"], v["end"], tolerance_seconds=temporal_tolerance_seconds
            )
            if within_tol:
                matching_v.append((v, dist))

        if not matching_v:
            continue

        # Pick best visual match (closest distance, then highest score)
        matching_v.sort(key=lambda pair: (pair[1], -pair[0]["score"], pair[0]["start"]))
        best_v, _ = matching_v[0]

        # Segment building policy from config.yaml: align_to_audio_spike
        start_time = round(a["start"], 3)
        end_time = round(max(a["end"], start_time + min_duration_seconds), 3)
        if end_time - start_time > max_duration_seconds:
            end_time = round(start_time + max_duration_seconds, 3)

        compound_score = calculate_compound_score(a["score"], best_v["score"])

        candidates.append({
            "start": start_time,
            "end": end_time,
            "trigger": DEFAULT_TRIGGER,
            "score": compound_score,
            "source": source,
        })

    if not candidates:
        return []

    # Sort candidates chronologically
    candidates.sort(key=lambda x: (x["start"], x["end"]))

    # Deduplicate overlapping compound cues
    deduped: List[Dict[str, Any]] = []
    for cand in candidates:
        if not deduped:
            deduped.append(cand)
            continue
        prev = deduped[-1]
        # If consecutive cues overlap, merge them safely within max_duration_seconds
        if cand["start"] < prev["end"]:
            extended_end = max(prev["end"], cand["end"])
            if extended_end - prev["start"] <= max_duration_seconds:
                prev["end"] = extended_end
                prev["score"] = max(prev["score"], cand["score"])
            else:
                prev["end"] = round(prev["start"] + max_duration_seconds, 3)
                prev["score"] = max(prev["score"], cand["score"])
                if cand["end"] > prev["end"]:
                    next_cand = dict(cand)
                    next_cand["start"] = prev["end"]
                    if next_cand["end"] - next_cand["start"] >= min_duration_seconds:
                        deduped.append(next_cand)
        else:
            deduped.append(cand)

    return deduped


def detect_visual_change_events(
    video_path: Union[str, Path],
    min_frame_difference: float = DEFAULT_MIN_FRAME_DIFFERENCE,
    width: int = DEFAULT_LOW_RES_WIDTH,
    height: int = DEFAULT_LOW_RES_HEIGHT,
    fps: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Detect rapid visual onset / motion change events across video frames.

    Modular visual evidence helper for jump scare compounding.
    Computes inter-frame mean absolute difference (MAD) of normalized luminance.

    Limitation note:
    This helper serves as visual proxy evidence for abrupt visual onsets coincident
    with audio spikes. It functions independently as a multimodal shock detector.

    Returns:
        List of visual change event dictionaries with fields:
        {'start': float, 'end': float, 'score': float, 'trigger': 'visual_motion_proxy', 'source': 'visual_change_helper'}
    """
    path = Path(video_path)
    if not path.is_file():
        return []

    # If audio-only WAV file or unsupported format, no visual evidence can be derived
    if path.suffix.lower() in (".wav", ".mp3", ".aac", ".flac", ".ogg"):
        return []

    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not shutil.which(ffmpeg_bin) and not Path(ffmpeg_bin).is_file():
        return []

    # Probe fps if not provided
    if fps is None or fps <= 0.0:
        ffprobe_bin = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
        if shutil.which(ffprobe_bin) or Path(ffprobe_bin).is_file():
            try:
                cmd_probe = [
                    ffprobe_bin,
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=r_frame_rate",
                    "-of", "json",
                    str(path),
                ]
                res = subprocess.run(cmd_probe, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    r_fps = data.get("streams", [{}])[0].get("r_frame_rate", "24/1")
                    if "/" in r_fps:
                        num, den = r_fps.split("/")
                        fps = float(num) / float(den) if float(den) != 0 else 24.0
                    else:
                        fps = float(r_fps)
            except Exception:
                fps = 24.0
        else:
            fps = 24.0

    frame_bytes = width * height * 3
    cmd = [
        ffmpeg_bin,
        "-v", "error",
        "-i", str(path),
        "-vf", f"scale={width}:{height}",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-",
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=frame_bytes * 4,
        )
    except Exception:
        return []

    events: List[Dict[str, Any]] = []
    prev_gray: Optional[np.ndarray] = None
    frame_idx = 0
    frame_dur = 1.0 / max(1.0, float(fps))

    try:
        while True:
            chunk = proc.stdout.read(frame_bytes)
            if not chunk or len(chunk) < frame_bytes:
                break

            frame_rgb = np.frombuffer(chunk, dtype=np.uint8).reshape((height, width, 3))
            # ITU-R BT.709 relative luminance
            gray = (
                0.2126 * frame_rgb[:, :, 0].astype(np.float32)
                + 0.7152 * frame_rgb[:, :, 1].astype(np.float32)
                + 0.0722 * frame_rgb[:, :, 2].astype(np.float32)
            ) / 255.0

            if prev_gray is not None:
                mad = float(np.mean(np.abs(gray - prev_gray)))
                if mad >= min_frame_difference:
                    t_start = round(frame_idx * frame_dur, 3)
                    t_end = round(t_start + frame_dur, 3)
                    score = round(min(1.0, mad / 0.5), 4)
                    events.append({
                        "start": t_start,
                        "end": t_end,
                        "score": score,
                        "trigger": "visual_motion_proxy",
                        "source": "visual_change_helper",
                    })

            prev_gray = gray
            frame_idx += 1
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
        proc.wait()

    return events


def detect_jump_scare(
    media_path: Union[str, Path],
    config: Optional[Dict[str, Any]] = None,
    temporal_tolerance: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Detect compound jump_scare events in an audiovisual media file.

    1. Runs Person B's existing loud_spike detector (`scripts/detect_loudness.py`).
    2. Runs visual onset change helper (`detect_visual_change_events`).
    3. Fuses audio and visual cues with temporal tolerance.
    """
    path = Path(media_path)
    if not path.is_file():
        raise FileNotFoundError(f"Media file not found: {path.resolve()}")

    # Load configuration
    cfg = config or {}
    triggers_cfg = cfg.get("triggers", {})
    js_cfg = triggers_cfg.get("jump_scare", {})
    algo_cfg = js_cfg.get("algorithm", {})

    tol = (
        temporal_tolerance
        if temporal_tolerance is not None
        else float(
            algo_cfg.get(
                "temporal_alignment_tolerance_seconds",
                DEFAULT_TEMPORAL_ALIGNMENT_TOLERANCE_SECONDS,
            )
        )
    )
    min_diff = float(
        algo_cfg.get("visual_change_condition", {}).get(
            "min_frame_difference", DEFAULT_MIN_FRAME_DIFFERENCE
        )
    )
    min_dur = float(
        js_cfg.get("min_segment_duration_seconds", DEFAULT_MIN_SEGMENT_DURATION_SECONDS)
    )
    max_dur = float(
        js_cfg.get("max_segment_duration_seconds", DEFAULT_MAX_SEGMENT_DURATION_SECONDS)
    )
    source_name = str(js_cfg.get("source", DEFAULT_SOURCE))

    # 1. Extract audio and run Person B's loudness detector
    temp_dir_obj: Optional[tempfile.TemporaryDirectory] = None
    try:
        if path.suffix.lower() == ".wav":
            wav_path = path
        else:
            temp_dir_obj = tempfile.TemporaryDirectory(prefix="jumpscare_audio_")
            wav_path = Path(temp_dir_obj.name) / "audio_mono.wav"
            extract_audio(str(path), str(wav_path), sample_rate=44100, channels=1)

        raw_loudness = detect_loudness_from_wav(
            wav_path=wav_path,
            config=config,
            modes=("spike",),
        )
        audio_spikes = [ev for ev in raw_loudness if ev.get("trigger") == "loud_spike"]
    except Exception as exc:
        audio_spikes = []
    finally:
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()

    # If no audio spikes found, cannot satisfy multimodal jump scare
    if not audio_spikes:
        return []

    # 2. Extract visual change events
    visual_events = detect_visual_change_events(
        video_path=path,
        min_frame_difference=min_diff,
    )

    # If no visual change events found, cannot satisfy multimodal jump scare
    if not visual_events:
        return []

    # 3. Fuse modalities using temporal matching rule
    return match_audio_visual_jump_scares(
        audio_events=audio_spikes,
        visual_events=visual_events,
        temporal_tolerance_seconds=tol,
        min_duration_seconds=min_dur,
        max_duration_seconds=max_dur,
        source=source_name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Multimodal Compound Jump Scare Detector (Person B & C)"
    )
    parser.add_argument(
        "--input", "-i", required=True, help="Input media file path (video or audio)"
    )
    parser.add_argument(
        "--output", "-o", default=None, help="Output JSON path for jump_scare detections"
    )
    parser.add_argument(
        "--config", "-c", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml"
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="Temporal alignment tolerance in seconds (default: from config or 0.5s)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        return 1

    cfg = load_detector_config(args.config)
    detections = detect_jump_scare(
        media_path=input_path,
        config=cfg,
        temporal_tolerance=args.tolerance,
    )

    out_json = json.dumps(detections, indent=2)
    if args.output:
        out_p = Path(args.output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(out_json, encoding="utf-8")
        print(f"[OK] Wrote {len(detections)} jump_scare cues to {out_p}")
    else:
        print(out_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
