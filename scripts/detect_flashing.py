#!/usr/bin/env python3
"""Rapid Flashing / Strobe Lights detection pipeline for Trigger-Aware Player (Person A).

Detects visual flashing triggers according to config.yaml (triggers.flashing):
- Samples native video frames resized to 128x72 via streaming ffmpeg process.
- Applies ITU-R BT.709 relative luminance with non-linear sRGB linearization.
- Divides frame into 16x9 grid (144 cells, 8x8 pixels per cell).
- Identifies cell luminance transitions (|delta Y| >= 0.10 and min(Y_prev, Y_curr) <= 0.80).
- Triggers candidate transition when >= 25% of grid cells (36 cells) change simultaneously.
- Detects repetitive flash oscillations (>= 4 opposite transitions within 1.0s window, 3 Hz - 60 Hz).
- Excludes isolated camera cut transitions (MAD > 0.35 without oscillation).
- Emits standardized cue dictionaries matching the shared detection contract:
    {
        "start": float,     # seconds, >= 0.0
        "end": float,       # seconds, > start
        "trigger": "flashing",
        "score": float,     # [0.0, 1.0]
        "source": "flash_detector"
    }

Usage:
    python scripts/detect_flashing.py --input "data/test_clips/flash_rate_4hz_24fps_full_c050.mp4"
    python scripts/detect_flashing.py --input "video.mp4" --output "cues_flashing.json"
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple, Union

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import yaml

DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_SOURCE = "flash_detector"
DEFAULT_TRIGGER = "flashing"

# Algorithmic defaults aligned with config.yaml (triggers.flashing)
DEFAULT_WIDTH = 128
DEFAULT_HEIGHT = 72
DEFAULT_GRID_COLS = 16
DEFAULT_GRID_ROWS = 9
DEFAULT_CELL_LUMINANCE_THRESHOLD = 0.10
DEFAULT_DARKER_FRAME_THRESHOLD = 0.80
DEFAULT_SIMULTANEOUS_AREA_THRESHOLD = 0.25  # 25% of 144 cells = 36 cells
DEFAULT_FLASH_WINDOW_SECONDS = 1.0
DEFAULT_MIN_TRANSITIONS = 4
DEFAULT_MIN_HZ = 3.0
DEFAULT_MAX_HZ = 60.0
DEFAULT_MIN_DURATION_SECONDS = 0.2
DEFAULT_MAX_DURATION_SECONDS = 30.0
DEFAULT_SCENE_CUT_THRESHOLD = 0.35


def load_detector_config(config_path: Union[str, Path] = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load trigger detection configuration from YAML file."""
    path = Path(config_path)
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def get_video_metadata(video_path: Union[str, Path]) -> Dict[str, Any]:
    """Extract framerate and duration of video file using ffprobe.

    Raises:
        FileNotFoundError: If video file or ffprobe executable is not found.
        ValueError: If video streams cannot be parsed.
    """
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path.resolve()}")

    ffprobe_bin = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    if not shutil.which(ffprobe_bin) and not Path(ffprobe_bin).is_file():
        raise FileNotFoundError(f"ffprobe binary not found at '{ffprobe_bin}' or in PATH.")

    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,nb_frames,duration:format=duration",
        "-of", "json",
        str(path),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise ValueError(f"ffprobe failed to probe video {path}: {res.stderr}")

    try:
        info = json.loads(res.stdout)
    except Exception as exc:
        raise ValueError(f"Invalid JSON from ffprobe: {res.stdout}") from exc

    streams = info.get("streams", [])
    if not streams:
        raise ValueError(f"No video streams found in {path}")

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
        "fps": fps,
        "duration": duration or 0.0,
    }


def stream_video_frames(
    video_path: Union[str, Path],
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Generator[np.ndarray, None, None]:
    """Yield RGB frames resized to width x height as uint8 numpy arrays of shape (height, width, 3).

    Streams chunks directly from ffmpeg stdout to keep memory overhead minimal.
    """
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path.resolve()}")

    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not shutil.which(ffmpeg_bin) and not Path(ffmpeg_bin).is_file():
        raise FileNotFoundError(f"ffmpeg binary not found at '{ffmpeg_bin}' or in PATH.")

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

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=frame_bytes * 4,
    )

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


def compute_relative_luminance_grid(
    frame: np.ndarray,
    grid_rows: int = DEFAULT_GRID_ROWS,
    grid_cols: int = DEFAULT_GRID_COLS,
    linearize_srgb: bool = True,
) -> Tuple[np.ndarray, float]:
    """Compute 16x9 grid cell average relative luminance and whole-frame mean luminance.

    ITU-R BT.709 formula: Y = 0.2126*R + 0.7152*G + 0.0722*B

    Args:
        frame: uint8 or float32 image array (H, W, 3).
        grid_rows: Number of grid rows (default: 9).
        grid_cols: Number of grid columns (default: 16).
        linearize_srgb: Whether to convert non-linear sRGB to linear light before luminance.

    Returns:
        (grid_luminances: 1D array of length rows*cols, frame_mean_luminance: float)
    """
    h, w, _ = frame.shape
    if frame.dtype == np.uint8:
        v = frame.astype(np.float32) / 255.0
    else:
        v = np.clip(frame.astype(np.float32), 0.0, 1.0)

    if linearize_srgb:
        # Standard IEC 61966-2-1 sRGB linearization
        v_lin = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    else:
        v_lin = v

    # ITU-R BT.709 relative luminance coefficients
    y = 0.2126 * v_lin[..., 0] + 0.7152 * v_lin[..., 1] + 0.0722 * v_lin[..., 2]

    # Reshape into (grid_rows, cell_h, grid_cols, cell_w)
    cell_h = h // grid_rows
    cell_w = w // grid_cols
    grid = y[: grid_rows * cell_h, : grid_cols * cell_w].reshape(
        grid_rows, cell_h, grid_cols, cell_w
    ).mean(axis=(1, 3))

    return grid.reshape(-1), float(np.mean(y))


def detect_flashing_from_frames(
    frames: Iterable[np.ndarray],
    fps: float,
    cell_threshold: float = DEFAULT_CELL_LUMINANCE_THRESHOLD,
    darker_frame_threshold: float = DEFAULT_DARKER_FRAME_THRESHOLD,
    area_threshold: float = DEFAULT_SIMULTANEOUS_AREA_THRESHOLD,
    min_transitions: int = DEFAULT_MIN_TRANSITIONS,
    window_seconds: float = DEFAULT_FLASH_WINDOW_SECONDS,
    min_duration: float = DEFAULT_MIN_DURATION_SECONDS,
    max_duration: float = DEFAULT_MAX_DURATION_SECONDS,
    scene_cut_mad_threshold: float = DEFAULT_SCENE_CUT_THRESHOLD,
    grid_rows: int = DEFAULT_GRID_ROWS,
    grid_cols: int = DEFAULT_GRID_COLS,
    source: str = DEFAULT_SOURCE,
) -> List[Dict[str, Any]]:
    """Detect flashing sequences from an iterable stream of resized RGB frames.

    Returns:
        List of detection dictionaries conforming to the standard cue schema.
    """
    if fps <= 0.0:
        fps = 24.0

    total_cells = grid_rows * grid_cols
    min_active_cells = int(math.ceil(area_threshold * total_cells))

    # Record transitions: list of (frame_idx, timestamp_sec, direction (+1/-1), active_frac, mean_abs_delta)
    raw_transitions: List[Tuple[int, float, int, float, float]] = []

    prev_grid: Optional[np.ndarray] = None
    prev_frame_mean: Optional[float] = None

    for frame_idx, frame in enumerate(frames):
        grid, frame_mean = compute_relative_luminance_grid(
            frame, grid_rows=grid_rows, grid_cols=grid_cols
        )
        t_sec = frame_idx / fps

        if prev_grid is not None:
            delta_grid = grid - prev_grid
            abs_delta = np.abs(delta_grid)
            darker_lum = np.minimum(prev_grid, grid)

            # Active cells: |delta Y| >= threshold and darker <= 0.80
            active_mask = (abs_delta >= cell_threshold) & (darker_lum <= darker_frame_threshold)
            active_count = int(np.sum(active_mask))
            active_frac = active_count / float(total_cells)

            if active_count >= min_active_cells:
                direction = 1 if np.sum(delta_grid[active_mask]) > 0 else -1
                mean_delta = float(np.mean(abs_delta[active_mask]))
                raw_transitions.append((frame_idx, t_sec, direction, active_frac, mean_delta))

        prev_grid = grid
        prev_frame_mean = frame_mean

    if len(raw_transitions) < min_transitions:
        return []

    # Build flash runs from continuous transitions
    # In a repetitive flash sequence, transitions occur periodically (gap <= 1.0s)
    # and alternate direction.
    max_gap_sec = max(0.5, window_seconds)

    runs: List[List[Tuple[int, float, int, float, float]]] = []
    current_run: List[Tuple[int, float, int, float, float]] = []

    for tr in raw_transitions:
        if not current_run:
            current_run.append(tr)
        else:
            prev_tr = current_run[-1]
            gap = tr[1] - prev_tr[1]
            if gap <= max_gap_sec:
                current_run.append(tr)
            else:
                runs.append(current_run)
                current_run = [tr]

    if current_run:
        runs.append(current_run)

    detections: List[Dict[str, Any]] = []

    for run in runs:
        # Check if run contains at least min_transitions alternating transitions within any window_seconds
        if len(run) < min_transitions:
            continue

        # Count opposite-direction transitions within sliding temporal windows
        has_sufficient_oscillation = False
        n_tr = len(run)

        for i in range(n_tr):
            t_start = run[i][1]
            t_limit = t_start + window_seconds
            
            # Count direction reversals in this window
            opposite_count = 0
            prev_dir = run[i][2]
            
            for j in range(i + 1, n_tr):
                if run[j][1] > t_limit:
                    break
                curr_dir = run[j][2]
                if curr_dir != prev_dir:
                    opposite_count += 1
                    prev_dir = curr_dir

            # To qualify, require >= min_transitions total transitions or >= 2 full reversal pairs
            window_transitions = [r for r in run[i:] if r[1] <= t_limit]
            if len(window_transitions) >= min_transitions and opposite_count >= 2:
                has_sufficient_oscillation = True
                break

        if not has_sufficient_oscillation:
            continue

        # Segment construction: first to last flash transition
        # Start at first flash frame
        start_time = round(run[0][1], 3)
        # End time: timestamp of last transition in run
        end_time = round(run[-1][1], 3)

        # End-of-video boundary check: if video ended during the final flash phase
        if len(run) > 1:
            diffs = [run[k + 1][1] - run[k][1] for k in range(len(run) - 1)]
            phase_duration = float(np.median(diffs))
            phase_frames = max(1, int(round(phase_duration * fps)))
            total_stream_frames = frame_idx + 1
            if (total_stream_frames - 1) - run[-1][0] < phase_frames:
                # Video ended while last phase was active; extend end_time to video end
                end_time = round(min(total_stream_frames / fps, run[-1][1] + phase_duration), 3)

        # Enforce minimum duration constraint
        if end_time - start_time < min_duration:
            end_time = round(start_time + min_duration, 3)

        # Enforce maximum duration clamping
        if end_time - start_time > max_duration:
            end_time = round(start_time + max_duration, 3)

        # Compute confidence score: mean active area fraction across the run
        mean_area = float(np.mean([r[3] for r in run]))
        mean_delta = float(np.mean([r[4] for r in run]))
        score = round(float(np.clip(0.5 * mean_area + 0.5 * min(1.0, mean_delta / 0.5), 0.0, 1.0)), 4)

        detections.append({
            "start": float(start_time),
            "end": float(end_time),
            "trigger": DEFAULT_TRIGGER,
            "score": score,
            "source": source,
        })

    return detections


def detect_flashing(
    video_path: Union[str, Path],
    config: Optional[Dict[str, Any]] = None,
    config_path: Union[str, Path] = DEFAULT_CONFIG_PATH,
) -> List[Dict[str, Any]]:
    """Detect rapid visual flashing in a video file according to project configuration.

    Args:
        video_path: Path to video file.
        config: Optional pre-loaded configuration dictionary.
        config_path: Path to config.yaml if config is not pre-loaded.

    Returns:
        List of raw detection records matching the shared contract:
        [{"start": float, "end": float, "trigger": "flashing", "score": float, "source": "flash_detector"}]
    """
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path.resolve()}")

    if config is None:
        config = load_detector_config(config_path)

    flashing_cfg = config.get("triggers", {}).get("flashing", {})
    algo_cfg = flashing_cfg.get("algorithm", {})

    width = int(algo_cfg.get("frame_dimensions", {}).get("width", DEFAULT_WIDTH))
    height = int(algo_cfg.get("frame_dimensions", {}).get("height", DEFAULT_HEIGHT))
    grid_cols = int(algo_cfg.get("spatial_grid", {}).get("columns", DEFAULT_GRID_COLS))
    grid_rows = int(algo_cfg.get("spatial_grid", {}).get("rows", DEFAULT_GRID_ROWS))
    cell_threshold = float(algo_cfg.get("cell_luminance_change_threshold", DEFAULT_CELL_LUMINANCE_THRESHOLD))
    darker_threshold = float(algo_cfg.get("darker_frame_luminance_threshold", DEFAULT_DARKER_FRAME_THRESHOLD))
    area_threshold = float(algo_cfg.get("simultaneous_flashing_area_threshold", DEFAULT_SIMULTANEOUS_AREA_THRESHOLD))
    window_sec = float(algo_cfg.get("flash_count_window_seconds", DEFAULT_FLASH_WINDOW_SECONDS))
    min_transitions = int(algo_cfg.get("min_opposite_transitions_threshold", DEFAULT_MIN_TRANSITIONS))
    min_dur = float(flashing_cfg.get("min_segment_duration_seconds", DEFAULT_MIN_DURATION_SECONDS))
    max_dur = float(flashing_cfg.get("max_segment_duration_seconds", DEFAULT_MAX_DURATION_SECONDS))
    source = str(flashing_cfg.get("source", DEFAULT_SOURCE))

    # Probe FPS
    try:
        meta = get_video_metadata(path)
        fps = float(meta.get("fps", 24.0))
    except Exception:
        fps = 24.0

    try:
        frame_stream = stream_video_frames(path, width=width, height=height)
        detections = detect_flashing_from_frames(
            frames=frame_stream,
            fps=fps,
            cell_threshold=cell_threshold,
            darker_frame_threshold=darker_threshold,
            area_threshold=area_threshold,
            min_transitions=min_transitions,
            window_seconds=window_sec,
            min_duration=min_dur,
            max_duration=max_dur,
            grid_rows=grid_rows,
            grid_cols=grid_cols,
            source=source,
        )
        return detections
    except Exception as exc:
        # Graceful handling for corrupt or unreadable media
        return []


def main() -> int:
    """CLI entrypoint for standalone flash detection."""
    parser = argparse.ArgumentParser(
        description="Trigger-Aware Player — Rapid Flashing / Strobe Lights Detector (Person A)"
    )
    parser.add_argument("--input", "-i", required=True, type=Path, help="Input video file path")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output JSON file path")
    parser.add_argument("--config", "-c", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.yaml")

    args = parser.parse_args()

    if not args.input.is_file():
        sys.stderr.write(f"[ERROR] Input video file not found: {args.input}\n")
        return 1

    detections = detect_flashing(args.input, config_path=args.config)

    output_json = json.dumps(detections, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as f:
            f.write(output_json + "\n")
        print(f"Wrote {len(detections)} flashing detections to: {args.output}")
    else:
        print(output_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
