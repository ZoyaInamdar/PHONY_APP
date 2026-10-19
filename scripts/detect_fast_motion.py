#!/usr/bin/env python3
"""Rapid Motion and Camera Shake Detector for Trigger-Aware Player (Person C).

Detects visual 'fast_motion' triggers according to config.yaml (triggers.fast_motion):
- Evaluates native video frames resized to 128x72 via streaming ffmpeg process.
- Converts RGB frames to ITU-R BT.709 relative luminance.
- Computes Mean Absolute Difference (MAD) between consecutive frames.
- Integrates a scene cut filter: isolated single-frame jumps (MAD >= 0.40) are
  suppressed to prevent editorial montage cuts from masquerading as motion.
- Applies temporal smoothing (default window: 1.0s) to track sustained motion.
- Requires sustained activity exceeding motion threshold (default: 0.20) for at
  least min_segment_duration_seconds (default: 1.0s).
- Emits standardized cue dictionaries matching the shared detection contract:
    {
        "start": float,     # seconds, >= 0.0
        "end": float,       # seconds, > start
        "trigger": "fast_motion",
        "score": float,     # [0.0, 1.0] normalized motion intensity
        "source": "motion_detector"
    }

Limitation note:
This detector uses a frame-difference heuristic with scene cut suppression rather
than compute-heavy optical flow (Farneback/RAFT, explicitly disabled in config.yaml).
It is intended for intense camera shake and rapid continuous motion.

Usage:
    python scripts/detect_fast_motion.py --input "data/test_clips/video_negative_static.mp4"
    python scripts/detect_fast_motion.py --input "video.mp4" --output "cues_motion.json"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Sequence, Tuple, Union

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import yaml

DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_SOURCE = "motion_detector"
DEFAULT_TRIGGER = "fast_motion"

# Algorithmic defaults aligned with config.yaml (triggers.fast_motion)
DEFAULT_WIDTH = 128
DEFAULT_HEIGHT = 72
DEFAULT_THRESHOLD = 0.20
DEFAULT_HARD_CUT_MAD_THRESHOLD = 0.40
DEFAULT_TEMPORAL_SMOOTHING_WINDOW_SECONDS = 1.0
DEFAULT_MIN_SEGMENT_DURATION_SECONDS = 1.0
DEFAULT_MAX_SEGMENT_DURATION_SECONDS = 30.0
DEFAULT_MERGE_GAP_SECONDS = 2.0


def load_fast_motion_config(config_path: Union[str, Path] = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load fast_motion detector configuration parameters from config.yaml."""
    p = Path(config_path)
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return {}

    triggers_cfg = data.get("triggers", {})
    fm_cfg = triggers_cfg.get("fast_motion", {})
    algo_cfg = fm_cfg.get("algorithm", {})
    cut_cfg = algo_cfg.get("cut_detector_integration", {})

    return {
        "threshold": float(fm_cfg.get("threshold", DEFAULT_THRESHOLD)),
        "min_segment_duration_seconds": float(
            fm_cfg.get("min_segment_duration_seconds", DEFAULT_MIN_SEGMENT_DURATION_SECONDS)
        ),
        "max_segment_duration_seconds": float(
            fm_cfg.get("max_segment_duration_seconds", DEFAULT_MAX_SEGMENT_DURATION_SECONDS)
        ),
        "merge_gap_seconds": float(fm_cfg.get("merge_gap_seconds", DEFAULT_MERGE_GAP_SECONDS)),
        "temporal_smoothing_window_seconds": float(
            algo_cfg.get(
                "temporal_smoothing_window_seconds",
                DEFAULT_TEMPORAL_SMOOTHING_WINDOW_SECONDS,
            )
        ),
        "exclude_hard_cuts": bool(cut_cfg.get("exclude_hard_cuts", True)),
        "hard_cut_mad_threshold": float(
            cut_cfg.get("hard_cut_mad_threshold", DEFAULT_HARD_CUT_MAD_THRESHOLD)
        ),
        "source": str(fm_cfg.get("source", DEFAULT_SOURCE)),
    }


def get_video_metadata(video_path: Union[str, Path]) -> Dict[str, Any]:
    """Extract framerate and duration of video file using ffprobe."""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path.resolve()}")

    ffprobe_bin = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    if not shutil.which(ffprobe_bin) and not Path(ffprobe_bin).is_file():
        return {"fps": 24.0, "duration": 0.0}

    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,duration:format=duration",
        "-of", "json",
        str(path),
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            return {"fps": 24.0, "duration": 0.0}
        info = json.loads(res.stdout)
    except Exception:
        return {"fps": 24.0, "duration": 0.0}

    streams = info.get("streams", [])
    if not streams:
        return {"fps": 24.0, "duration": 0.0}

    stream = streams[0]
    r_fps = stream.get("r_frame_rate", "24/1")
    if "/" in r_fps:
        num, den = r_fps.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 24.0
    else:
        fps = float(r_fps) if r_fps else 24.0

    duration = None
    if "duration" in stream and stream["duration"]:
        try:
            duration = float(stream["duration"])
        except (ValueError, TypeError):
            pass
    if duration is None and "format" in info and "duration" in info["format"]:
        try:
            duration = float(info["format"]["duration"])
        except (ValueError, TypeError):
            pass

    return {
        "fps": fps if fps > 0 else 24.0,
        "duration": duration or 0.0,
    }


def stream_video_frames(
    video_path: Union[str, Path],
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Generator[np.ndarray, None, None]:
    """Yield RGB frames resized to width x height as uint8 numpy arrays of shape (height, width, 3)."""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path.resolve()}")

    if path.suffix.lower() in (".wav", ".mp3", ".aac", ".flac", ".ogg"):
        return

    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not shutil.which(ffmpeg_bin) and not Path(ffmpeg_bin).is_file():
        return

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
        return

    try:
        while True:
            chunk = proc.stdout.read(frame_bytes)
            if not chunk or len(chunk) < frame_bytes:
                break
            frame = np.frombuffer(chunk, dtype=np.uint8).reshape((height, width, 3))
            yield frame
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
        proc.wait()


def frame_to_relative_luminance(frame_rgb: np.ndarray) -> np.ndarray:
    """Convert uint8 RGB frame to normalized ITU-R BT.709 relative luminance in [0.0, 1.0]."""
    return (
        0.2126 * frame_rgb[:, :, 0].astype(np.float32)
        + 0.7152 * frame_rgb[:, :, 1].astype(np.float32)
        + 0.0722 * frame_rgb[:, :, 2].astype(np.float32)
    ) / 255.0


def filter_scene_cuts(
    mad_series: Sequence[float],
    hard_cut_threshold: float = DEFAULT_HARD_CUT_MAD_THRESHOLD,
    motion_threshold: float = DEFAULT_THRESHOLD,
) -> List[float]:
    """Filter out isolated scene cuts from the frame difference series.

    A scene cut is characterized by a single instantaneous jump (MAD >= hard_cut_threshold)
    where the surrounding frames exhibit low difference (< motion_threshold).
    Replaces isolated cut impulses with the average of surrounding background frames
    so that cuts do not trigger false positive motion alerts.
    """
    n = len(mad_series)
    if n == 0:
        return []

    filtered = list(mad_series)
    for i in range(n):
        val = filtered[i]
        if val >= hard_cut_threshold:
            prev_val = filtered[i - 1] if i > 0 else 0.0
            next_val = filtered[i + 1] if i < n - 1 else 0.0
            # If the spike is an isolated 1-frame impulse without sustained motion before/after
            if prev_val < motion_threshold and next_val < motion_threshold:
                filtered[i] = (prev_val + next_val) / 2.0

    return filtered


def apply_temporal_smoothing(
    series: Sequence[float],
    window_frames: int,
) -> List[float]:
    """Apply uniform moving average smoothing across a sliding window of frames."""
    if not series:
        return []
    if window_frames <= 1:
        return list(series)

    arr = np.array(series, dtype=np.float32)
    kernel = np.ones(window_frames, dtype=np.float32) / window_frames
    # Use mode='same' to maintain identical series length
    smoothed = np.convolve(arr, kernel, mode="same")
    return [float(x) for x in smoothed]


def detect_motion_intervals(
    mad_series: Sequence[float],
    fps: float,
    threshold: float = DEFAULT_THRESHOLD,
    min_duration_seconds: float = DEFAULT_MIN_SEGMENT_DURATION_SECONDS,
    max_segment_duration_seconds: float = DEFAULT_MAX_SEGMENT_DURATION_SECONDS,
    merge_gap_seconds: float = DEFAULT_MERGE_GAP_SECONDS,
    source: str = DEFAULT_SOURCE,
) -> List[Dict[str, Any]]:
    """Convert a frame-difference MAD series into standardized fast_motion cue intervals.

    Follows half-open interval semantics [start, end) where end is exclusive.
    Enforces minimum and maximum duration constraints and merges contiguous gaps.
    """
    if not mad_series or fps <= 0.0:
        return []

    frame_dur = 1.0 / float(fps)
    n = len(mad_series)

    # 1. Identify raw candidate segments above threshold
    raw_segments: List[Tuple[float, float, float]] = []  # (start_sec, end_sec, max_score)
    in_segment = False
    seg_start = 0.0
    seg_scores: List[float] = []

    for i, mad_val in enumerate(mad_series):
        t_start = i * frame_dur
        t_end = (i + 1) * frame_dur

        if mad_val >= threshold:
            if not in_segment:
                in_segment = True
                seg_start = t_start
                seg_scores = [mad_val]
            else:
                seg_scores.append(mad_val)
        else:
            if in_segment:
                in_segment = False
                seg_end = t_start
                max_mad = max(seg_scores)
                score = round(min(1.0, max(0.1, max_mad / 0.5)), 4)
                raw_segments.append((seg_start, seg_end, score))
                seg_scores = []

    if in_segment:
        seg_end = n * frame_dur
        max_mad = max(seg_scores) if seg_scores else threshold
        score = round(min(1.0, max(0.1, max_mad / 0.5)), 4)
        raw_segments.append((seg_start, seg_end, score))

    if not raw_segments:
        return []

    # 2. Merge segments separated by <= merge_gap_seconds
    merged: List[Dict[str, Any]] = []
    curr_start, curr_end, curr_score = raw_segments[0]

    for next_start, next_end, next_score in raw_segments[1:]:
        gap = next_start - curr_end
        if gap <= merge_gap_seconds:
            # Check if merging would exceed max_segment_duration_seconds
            potential_duration = next_end - curr_start
            if potential_duration <= max_segment_duration_seconds:
                curr_end = next_end
                curr_score = max(curr_score, next_score)
            else:
                # Close current and start next
                merged.append({
                    "start": round(curr_start, 3),
                    "end": round(curr_end, 3),
                    "score": curr_score,
                })
                curr_start = next_start
                curr_end = next_end
                curr_score = next_score
        else:
            merged.append({
                "start": round(curr_start, 3),
                "end": round(curr_end, 3),
                "score": curr_score,
            })
            curr_start = next_start
            curr_end = next_end
            curr_score = next_score

    merged.append({
        "start": round(curr_start, 3),
        "end": round(curr_end, 3),
        "score": curr_score,
    })

    # 3. Filter by min_duration and clamp by max_duration
    final_cues: List[Dict[str, Any]] = []
    for seg in merged:
        s = seg["start"]
        e = seg["end"]
        duration = e - s
        if duration >= min_duration_seconds:
            clamped_end = min(e, round(s + max_segment_duration_seconds, 3))
            final_cues.append({
                "start": s,
                "end": clamped_end,
                "trigger": DEFAULT_TRIGGER,
                "score": seg["score"],
                "source": source,
            })

    return final_cues


def detect_fast_motion(
    video_path: Union[str, Path],
    config: Optional[Dict[str, Any]] = None,
    threshold: Optional[float] = None,
    min_duration: Optional[float] = None,
    max_duration: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Detect rapid visual motion / camera shake in a video file.

    Parameters:
        video_path: Path to video file.
        config: Optional parsed config.yaml dictionary.
        threshold: Optional override for motion MAD threshold.
        min_duration: Optional override for min_segment_duration_seconds.
        max_duration: Optional override for max_segment_duration_seconds.

    Returns:
        List of standardized detection records compliant with the shared contract.
    """
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path.resolve()}")

    # Return empty list safely for audio-only files
    if path.suffix.lower() in (".wav", ".mp3", ".aac", ".flac", ".ogg"):
        return []

    # Load configuration
    cfg = config or load_fast_motion_config()
    triggers_cfg = cfg.get("triggers", {}) if "triggers" in cfg else cfg
    fm_cfg = triggers_cfg.get("fast_motion", {}) if "fast_motion" in triggers_cfg else cfg
    algo_cfg = fm_cfg.get("algorithm", {}) if "algorithm" in fm_cfg else cfg
    cut_cfg = algo_cfg.get("cut_detector_integration", {}) if "cut_detector_integration" in algo_cfg else cfg

    th = (
        float(threshold)
        if threshold is not None
        else float(fm_cfg.get("threshold", DEFAULT_THRESHOLD))
    )
    min_dur = (
        float(min_duration)
        if min_duration is not None
        else float(fm_cfg.get("min_segment_duration_seconds", DEFAULT_MIN_SEGMENT_DURATION_SECONDS))
    )
    max_dur = (
        float(max_duration)
        if max_duration is not None
        else float(fm_cfg.get("max_segment_duration_seconds", DEFAULT_MAX_SEGMENT_DURATION_SECONDS))
    )
    merge_gap = float(fm_cfg.get("merge_gap_seconds", DEFAULT_MERGE_GAP_SECONDS))
    smooth_sec = float(
        algo_cfg.get(
            "temporal_smoothing_window_seconds",
            DEFAULT_TEMPORAL_SMOOTHING_WINDOW_SECONDS,
        )
    )
    exclude_cuts = bool(cut_cfg.get("exclude_hard_cuts", True))
    hard_cut_th = float(
        cut_cfg.get("hard_cut_mad_threshold", DEFAULT_HARD_CUT_MAD_THRESHOLD)
    )
    source_name = str(fm_cfg.get("source", DEFAULT_SOURCE))

    # Probe metadata
    meta = get_video_metadata(path)
    fps = meta["fps"]

    # Stream frames and compute raw frame differences
    mad_list: List[float] = []
    prev_lum: Optional[np.ndarray] = None

    for frame_rgb in stream_video_frames(path, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT):
        lum = frame_to_relative_luminance(frame_rgb)
        if prev_lum is not None:
            mad = float(np.mean(np.abs(lum - prev_lum)))
            mad_list.append(mad)
        else:
            # First frame has no predecessor; set initial difference to 0
            mad_list.append(0.0)
        prev_lum = lum

    if not mad_list:
        return []

    # 1. Filter out isolated scene cut spikes
    if exclude_cuts:
        proc_series = filter_scene_cuts(
            mad_list,
            hard_cut_threshold=hard_cut_th,
            motion_threshold=th,
        )
    else:
        proc_series = mad_list

    # 2. Apply temporal smoothing window
    smooth_frames = max(1, int(round(smooth_sec * fps)))
    smoothed_series = apply_temporal_smoothing(proc_series, window_frames=smooth_frames)

    # 3. Detect and build continuous motion intervals
    return detect_motion_intervals(
        mad_series=smoothed_series,
        fps=fps,
        threshold=th,
        min_duration_seconds=min_dur,
        max_segment_duration_seconds=max_dur,
        merge_gap_seconds=merge_gap,
        source=source_name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rapid Motion and Camera Shake Detector (Person C)"
    )
    parser.add_argument(
        "--input", "-i", required=True, help="Input video file path"
    )
    parser.add_argument(
        "--output", "-o", default=None, help="Output JSON path for fast_motion detections"
    )
    parser.add_argument(
        "--config", "-c", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Motion MAD threshold (default from config or 0.20)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"[ERROR] Input video not found: {input_path}", file=sys.stderr)
        return 1

    cfg = load_fast_motion_config(args.config)
    detections = detect_fast_motion(
        video_path=input_path,
        config=cfg,
        threshold=args.threshold,
    )

    out_json = json.dumps(detections, indent=2)
    if args.output:
        out_p = Path(args.output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(out_json, encoding="utf-8")
        print(f"[OK] Wrote {len(detections)} fast_motion cues to {out_p}")
    else:
        print(out_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
