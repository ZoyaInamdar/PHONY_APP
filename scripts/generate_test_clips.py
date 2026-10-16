#!/usr/bin/env python3
"""Automated Synthetic Test-Clip and Ground-Truth Generator (Person C).

Generates synthetic audio/video test clips with mathematically precise ground-truth
timestamps for detector validation, calibration, and regression testing.

Outputs:
- MP4 test clips in data/test_clips/
- Ground-truth labels in data/test_clips/labels.csv (canonical triggers)
- Sync-marker labels in data/test_clips/sync_labels.csv (non-canonical sync markers)
- Clip specifications in data/test_clips/manifest.json
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

# Repository root discovery
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "test_clips"
DEFAULT_LABELS_PATH = DEFAULT_OUTPUT_DIR / "labels.csv"
DEFAULT_SYNC_LABELS_PATH = DEFAULT_OUTPUT_DIR / "sync_labels.csv"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

# Canonical triggers allowed in labels.csv (from config.yaml)
CANONICAL_TRIGGERS = {
    "flashing",
    "loud_spike",
    "gunshot",
    "explosion",
    "scream",
    "profanity",
    "violence",
    "gore",
    "spider",
}


@dataclasses.dataclass
class ClipSpec:
    """Specification of a generated synthetic test clip."""
    clip_name: str
    duration: float
    fps: float
    width: int = 640
    height: int = 360
    trigger: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None
    labeller: str = "synthetic_generator"
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_label_dict(self) -> Optional[Dict[str, Any]]:
        """Return label row dictionary if this clip contains a canonical trigger."""
        if self.trigger is None or self.start is None or self.end is None:
            return None
        return {
            "clip": self.clip_name,
            "trigger": self.trigger,
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "labeller": self.labeller,
        }


@dataclasses.dataclass
class SyncMarkerSpec:
    """Specification of a calibration sync marker (flash + beep)."""
    clip_name: str
    marker: str
    start: float
    end: float
    frequency_hz: float = 1000.0
    labeller: str = "synthetic_generator"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clip": self.clip_name,
            "marker": self.marker,
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "frequency_hz": self.frequency_hz,
            "labeller": self.labeller,
            "notes": self.notes,
        }


def find_tool(tool_name: str) -> str:
    """Find executable path for ffmpeg or ffprobe, searching PATH and local WinGet dirs."""
    which_path = shutil.which(tool_name)
    if which_path:
        return which_path

    # Fallback for Windows local WinGet installations
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_pkgs = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.is_dir():
            candidates = list(winget_pkgs.glob(f"**/{tool_name}.exe"))
            if candidates:
                return str(candidates[0])

    return tool_name


def write_labels_csv(
    records: List[Dict[str, Any]],
    output_path: Union[str, Path] = DEFAULT_LABELS_PATH,
    allowed_triggers: Optional[set[str]] = None,
) -> Path:
    """Write primary ground-truth labels to CSV.

    Header format: clip,trigger,start,end,labeller,actual_rate
    Validates that every trigger matches canonical trigger IDs.

    Args:
        records: List of label dictionaries.
        output_path: Target CSV file path.
        allowed_triggers: Set of permitted trigger IDs (defaults to CANONICAL_TRIGGERS).

    Returns:
        Path to the written CSV file.
    """
    valid_triggers = allowed_triggers or CANONICAL_TRIGGERS
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["clip", "trigger", "start", "end", "labeller", "actual_rate"]

    for row in records:
        trig = row.get("trigger")
        if trig not in valid_triggers:
            raise ValueError(
                f"Invalid trigger '{trig}' in record {row}. Must be one of {sorted(valid_triggers)}"
            )
        start = float(row["start"])
        end = float(row["end"])
        if start < 0.0 or end <= start:
            raise ValueError(f"Invalid interval [{start}, {end}) in record {row}")

    sorted_records = sorted(records, key=lambda r: (r["clip"], float(r["start"])))

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in sorted_records:
            row_dict = {
                "clip": r["clip"],
                "trigger": r["trigger"],
                "start": f"{float(r['start']):.3f}",
                "end": f"{float(r['end']):.3f}",
                "labeller": r.get("labeller", "synthetic_generator"),
            }
            if "actual_rate" in r and r["actual_rate"] is not None:
                row_dict["actual_rate"] = r["actual_rate"]
            writer.writerow(row_dict)

    return out_path



def write_sync_labels_csv(
    records: List[Dict[str, Any]],
    output_path: Union[str, Path] = DEFAULT_SYNC_LABELS_PATH,
) -> Path:
    """Write sync calibration ground truth to a separate CSV.

    Header format: clip,marker,start,end,frequency_hz,labeller,notes

    Args:
        records: List of sync marker dictionaries.
        output_path: Target CSV path.

    Returns:
        Path to written CSV file.
    """
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["clip", "marker", "start", "end", "frequency_hz", "labeller", "notes"]

    sorted_records = sorted(records, key=lambda r: (r["clip"], float(r["start"])))

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in sorted_records:
            writer.writerow({
                "clip": r["clip"],
                "marker": r.get("marker", "sync_marker"),
                "start": f"{float(r['start']):.3f}",
                "end": f"{float(r['end']):.3f}",
                "frequency_hz": r.get("frequency_hz", 1000.0),
                "labeller": r.get("labeller", "synthetic_generator"),
                "notes": r.get("notes", ""),
            })

    return out_path


def write_manifest_json(
    specs: List[ClipSpec],
    output_path: Union[str, Path] = DEFAULT_OUTPUT_DIR / "manifest.json",
) -> Path:
    """Write detailed metadata manifest of all generated clips."""
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "generator_version": 1,
        "clip_count": len(specs),
        "clips": [dataclasses.asdict(s) for s in specs],
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return out_path


def read_labels_csv(csv_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Read and parse a ground-truth labels CSV file."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Labels file not found: {path}")

    records: List[Dict[str, Any]] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append({
                "clip": row["clip"],
                "trigger": row["trigger"],
                "start": float(row["start"]),
                "end": float(row["end"]),
                "labeller": row.get("labeller", ""),
            })
    return records


def generate_sync_clip(
    output_dir: Union[str, Path] = DEFAULT_OUTPUT_DIR,
    filename: str = "sync_test.mp4",
    duration: float = 10.0,
    fps: float = 24.0,
    width: int = 640,
    height: int = 360,
    marker_intervals: Optional[List[Tuple[float, float]]] = None,
    frequency_hz: float = 1000.0,
    beep_amplitude: float = 0.5,
) -> Tuple[Path, List[SyncMarkerSpec]]:
    """Generate an MP4 sync clip with aligned visual flash and audio beep at known times.

    Ground-truth sync markers are written to sync_labels.csv (not labels.csv),
    preserving config.yaml canonical trigger integrity.

    Args:
        output_dir: Directory where the clip and sync_labels.csv are stored.
        filename: Name of the generated MP4 file (default: sync_test.mp4).
        duration: Total clip duration in seconds (default: 10.0s).
        fps: Video framerate (default: 24.0).
        width: Video width (default: 640).
        height: Video height (default: 360).
        marker_intervals: List of (start_seconds, end_seconds) for the markers.
        frequency_hz: Audio tone frequency in Hz (default: 1000.0 Hz).
        beep_amplitude: Normalized amplitude for beep [0.0, 1.0].

    Returns:
        Tuple of (path to created MP4, list of SyncMarkerSpecs).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / filename

    if marker_intervals is None:
        marker_intervals = [(2.0, 2.2), (5.0, 5.2), (8.0, 8.2)]

    ffmpeg_bin = find_tool("ffmpeg")
    import numpy as np
    import tempfile
    import wave

    sample_rate = 44100
    total_audio_samples = int(duration * sample_rate)
    audio_data = np.zeros(total_audio_samples, dtype=np.int16)

    sync_specs: List[SyncMarkerSpec] = []
    enable_clauses: List[str] = []

    for idx, (st, en) in enumerate(marker_intervals, 1):
        st_round = round(st, 3)
        en_round = round(en, 3)
        marker_name = f"sync_marker_{idx}"
        sync_specs.append(
            SyncMarkerSpec(
                clip_name=filename,
                marker=marker_name,
                start=st_round,
                end=en_round,
                frequency_hz=frequency_hz,
                labeller="synthetic_generator",
                notes=f"Simultaneous visual flash and {frequency_hz:.0f}Hz beep",
            )
        )
        enable_clauses.append(f"between(t,{st_round:.3f},{en_round:.3f})")

        # Synthesize audio beep
        start_samp = int(st_round * sample_rate)
        end_samp = min(total_audio_samples, int(en_round * sample_rate))
        if end_samp > start_samp:
            t = np.arange(end_samp - start_samp) / sample_rate
            beep = (beep_amplitude * 32767 * np.sin(2 * np.pi * frequency_hz * t)).astype(np.int16)
            audio_data[start_samp:end_samp] = beep

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        temp_wav_path = Path(tf.name)

    try:
        with wave.open(str(temp_wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_data.tobytes())

        filter_expr = "+".join(enable_clauses)
        vf_filter = f"drawbox=t=fill:c=white:enable='{filter_expr}'"

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration}",
            "-i", str(temp_wav_path),
            "-vf", vf_filter,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            str(mp4_path),
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg failed to generate sync clip:\n{proc.stderr}")
    finally:
        if temp_wav_path.exists():
            temp_wav_path.unlink()

    # Save sync labels
    sync_csv_path = out_dir / "sync_labels.csv"
    write_sync_labels_csv([s.to_dict() for s in sync_specs], sync_csv_path)

    return mp4_path, sync_specs


def compute_flash_timing(fps: float, target_flash_rate: float) -> Tuple[int, int, float]:
    """Calculate whole-number bright and dark frames per flash cycle and actual generated rate.

    Design constraint:
        Each phase is a whole number of frames (e.g. at 24 fps, 4 flashes per second
        is 3 frames bright and 3 dark).
        If a rate won't divide evenly, the actual generated rate is recorded in the labels.

    Args:
        fps: Video framerate.
        target_flash_rate: Desired flashes per second (Hz).

    Returns:
        (bright_frames, dark_frames, actual_flash_rate)
    """
    half_cycle = max(1, int(round(fps / (2.0 * target_flash_rate))))
    bright_frames = half_cycle
    dark_frames = half_cycle
    total_cycle_frames = bright_frames + dark_frames
    actual_flash_rate = round(fps / total_cycle_frames, 3)
    return bright_frames, dark_frames, actual_flash_rate


def get_area_geometry(area_name: str, width: int = 640, height: int = 360) -> Tuple[int, int, int, int, float]:
    """Calculate (x, y, w, h, fraction) for a given area type.

    Types:
        - full: 100% of screen.
        - half: 50% of screen (centered horizontal band).
        - quarter: 25% of screen (centered rectangle, WCAG cutoff threshold).
        - small_patch: 6.25% of screen (160x90 centered, sub-threshold case).

    Returns:
        (x, y, w, h, area_fraction)
    """
    total_pixels = width * height
    if area_name == "full":
        return 0, 0, width, height, 1.0
    elif area_name == "half":
        h = height // 2
        y = (height - h) // 2
        return 0, y, width, h, (width * h) / total_pixels
    elif area_name == "quarter":
        w = width // 2
        h = height // 2
        x = (width - w) // 2
        y = (height - h) // 2
        return x, y, w, h, (w * h) / total_pixels
    elif area_name == "small_patch":
        w = width // 4
        h = height // 4
        x = (width - w) // 2
        y = (height - h) // 2
        return x, y, w, h, (w * h) / total_pixels
    else:
        raise ValueError(f"Unknown area type '{area_name}'. Choose from full, half, quarter, small_patch.")


def generate_flash_clip(
    output_dir: Union[str, Path],
    filename: str,
    fps: float,
    target_flash_rate: float,
    area: str = "full",
    contrast_step: float = 0.50,
    width: int = 640,
    height: int = 360,
    lead_in_seconds: float = 1.0,
    num_cycles: int = 8,
    lead_out_seconds: float = 1.0,
) -> Tuple[Path, ClipSpec]:
    """Generate a single flash test clip with precise frame-accurate ground truth.

    Label constraint:
        The truth label spans strictly from the first flash frame to the last flash frame:
        start = first_flash_frame / fps
        end = last_flash_end_frame / fps
        trigger = 'flashing'

    Args:
        output_dir: Destination directory for MP4.
        filename: Destination MP4 filename.
        fps: Target framerate (e.g. 24, 25, 30, 60).
        target_flash_rate: Desired flash frequency (e.g. 2, 3, 4, 6 Hz).
        area: Screen area ('full', 'half', 'quarter', 'small_patch').
        contrast_step: Luminance delta step (0.05, 0.10, 0.20, 0.50).
        width: Video width (default: 640).
        height: Video height (default: 360).
        lead_in_seconds: Baseline static time before first flash (default: 1.0s).
        num_cycles: Number of full flash cycles (default: 8).
        lead_out_seconds: Static baseline time after last flash (default: 1.0s).

    Returns:
        Tuple of (path to generated MP4, ClipSpec with metadata and truth label).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / filename

    ffmpeg_bin = find_tool("ffmpeg")
    bright_frames, dark_frames, actual_flash_rate = compute_flash_timing(fps, target_flash_rate)
    cycle_frames = bright_frames + dark_frames
    total_flash_frames = num_cycles * cycle_frames

    first_flash_frame = int(round(lead_in_seconds * fps))
    last_flash_end_frame = first_flash_frame + total_flash_frames
    lead_out_frames = int(round(lead_out_seconds * fps))
    total_frames = last_flash_end_frame + lead_out_frames
    duration = round(total_frames / fps, 3)

    start_truth = round(first_flash_frame / fps, 3)
    end_truth = round(last_flash_end_frame / fps, 3)

    # Base neutral gray is 128 (0x808080)
    dark_val = max(0, min(255, int(round(128 - (contrast_step * 128)))))
    bright_val = max(0, min(255, int(round(128 + (contrast_step * 128)))))
    dark_hex = f"0x{dark_val:02x}{dark_val:02x}{dark_val:02x}"
    bright_hex = f"0x{bright_val:02x}{bright_val:02x}{bright_val:02x}"

    x, y, w, h, area_fraction = get_area_geometry(area, width, height)

    # Video filter: base neutral gray background, box region flashes between dark and bright during flash window
    vf = (
        f"drawbox=x={x}:y={y}:w={w}:h={h}:c={dark_hex}:t=fill:"
        f"enable='between(n,{first_flash_frame},{last_flash_end_frame - 1})',"
        f"drawbox=x={x}:y={y}:w={w}:h={h}:c={bright_hex}:t=fill:"
        f"enable='between(n,{first_flash_frame},{last_flash_end_frame - 1})*"
        f"lt(mod(n-{first_flash_frame},{cycle_frames}),{bright_frames})'"
    )

    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x808080:s={width}x{height}:r={fps}:d={duration}",
        "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=mono:d={duration}",
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "64k",
        "-t", f"{duration:.3f}",
        str(mp4_path),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed to generate flash clip '{filename}':\n{proc.stderr}")

    spec = ClipSpec(
        clip_name=filename,
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        trigger="flashing",
        start=start_truth,
        end=end_truth,
        labeller="synthetic_generator",
        metadata={
            "target_flash_rate": target_flash_rate,
            "actual_flash_rate": actual_flash_rate,
            "bright_frames": bright_frames,
            "dark_frames": dark_frames,
            "cycle_frames": cycle_frames,
            "area": area,
            "area_fraction": area_fraction,
            "contrast_step": contrast_step,
            "dark_val": dark_val,
            "bright_val": bright_val,
            "lead_in_seconds": lead_in_seconds,
            "lead_out_seconds": lead_out_seconds,
            "first_flash_frame": first_flash_frame,
            "last_flash_end_frame": last_flash_end_frame,
        },
    )

    return mp4_path, spec


def generate_flash_test_suite(
    output_dir: Union[str, Path] = DEFAULT_OUTPUT_DIR,
) -> Tuple[List[Path], List[ClipSpec]]:
    """Generate the standardized benchmark suite of flash clips varying fps, rates, areas, contrast.

    Variations covered:
        - Frame rates: 24, 25, 30, 60 fps
        - Flash rates: 2, 3, 4, 6 flashes/sec
        - Areas: full, half, quarter, small_patch
        - Contrast steps: 0.05, 0.10, 0.20, 0.50

    Returns:
        Tuple of (list of generated MP4 paths, list of ClipSpecs).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_paths: List[Path] = []
    specs: List[ClipSpec] = []

    # 1. Rate suite (varying flash rates at 24 fps, full area, 0.50 contrast)
    for rate in [2, 3, 4, 6]:
        fname = f"flash_rate_{rate}hz_24fps_full_c050.mp4"
        p, s = generate_flash_clip(out_dir, fname, fps=24.0, target_flash_rate=rate, area="full", contrast_step=0.50)
        generated_paths.append(p)
        specs.append(s)

    # 2. Frame-rate suite (varying fps at 4 flashes/sec, full area, 0.50 contrast)
    for fps in [25.0, 30.0, 60.0]:
        fname = f"flash_fps_{int(fps)}fps_4hz_full_c050.mp4"
        p, s = generate_flash_clip(out_dir, fname, fps=fps, target_flash_rate=4.0, area="full", contrast_step=0.50)
        generated_paths.append(p)
        specs.append(s)

    # 3. Area suite (varying areas at 24 fps, 4 flashes/sec, 0.50 contrast)
    for area in ["half", "quarter", "small_patch"]:
        fname = f"flash_area_{area}_24fps_4hz_c050.mp4"
        p, s = generate_flash_clip(out_dir, fname, fps=24.0, target_flash_rate=4.0, area=area, contrast_step=0.50)
        generated_paths.append(p)
        specs.append(s)

    # 4. Contrast step suite (varying contrast at 24 fps, 4 flashes/sec, full area)
    for contrast in [0.05, 0.10, 0.20]:
        c_str = f"{int(contrast * 100):03d}"
        fname = f"flash_contrast_{c_str}_24fps_4hz_full.mp4"
        p, s = generate_flash_clip(out_dir, fname, fps=24.0, target_flash_rate=4.0, area="full", contrast_step=contrast)
        generated_paths.append(p)
        specs.append(s)

    # 5. Additional representative edge cases for harness validation
    edge_cases = [
        ("flash_edge_25fps_3hz_full_c050.mp4", 25.0, 3.0, "full", 0.50),
        ("flash_edge_30fps_2hz_full_c050.mp4", 30.0, 2.0, "full", 0.50),
        ("flash_edge_60fps_6hz_full_c050.mp4", 60.0, 6.0, "full", 0.50),
    ]
    for fname, fps, rate, area, contrast in edge_cases:
        p, s = generate_flash_clip(out_dir, fname, fps=fps, target_flash_rate=rate, area=area, contrast_step=contrast)
        generated_paths.append(p)
        specs.append(s)

    # Write labels.csv
    labels_records = []
    for s in specs:
        row = s.to_label_dict()
        if row:
            row["actual_rate"] = s.metadata.get("actual_flash_rate")
            labels_records.append(row)

    write_labels_csv(labels_records, out_dir / "labels.csv")
    write_manifest_json(specs, out_dir / "manifest.json")

    return generated_paths, specs


def generate_detail_pattern_clip(
    output_dir: Union[str, Path] = DEFAULT_OUTPUT_DIR,
    filename: str = "detail_test_pattern.mp4",
    duration: float = 5.0,
    fps: float = 24.0,
    width: int = 640,
    height: int = 360,
) -> Tuple[Path, ClipSpec]:
    """Generate a short MP4 video of a test pattern containing fine, small text.

    This is for Person A to check blur, dim, and blackout behaviors on the
    Vega virtual device.

    Constraint:
        This clip has NO trigger label, so it is strictly NOT written to labels.csv.

    Args:
        output_dir: Destination directory for MP4.
        filename: Destination MP4 filename (default: detail_test_pattern.mp4).
        duration: Clip duration in seconds (default: 5.0s).
        fps: Target framerate (default: 24.0).
        width: Video width (default: 640).
        height: Video height (default: 360).

    Returns:
        Tuple of (path to generated MP4, ClipSpec with trigger=None).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / filename

    from PIL import Image, ImageDraw
    import tempfile

    img = Image.new("RGB", (width, height), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)

    # 1. High-frequency grid lines (every 20px)
    for x in range(0, width, 20):
        draw.line([(x, 0), (x, height)], fill=(215, 215, 215), width=1)
    for y in range(0, height, 20):
        draw.line([(0, y), (width, y)], fill=(215, 215, 215), width=1)

    # 2. 1-pixel alternating line resolution chart in top-right corner
    for y in range(20, 100, 2):
        draw.line([(width - 120, y), (width - 20, y)], fill=(0, 0, 0), width=1)

    # 3. Fine text blocks of varying sizes and legibility
    draw.text((30, 20), "TRIGGER-AWARE PLAYER — VEGA TEST PATTERN", fill=(0, 0, 0))
    draw.text((30, 42), "PURPOSE: Fine text resolution, blur decay, and blackout check", fill=(60, 60, 60))
    draw.text((30, 70), "Line 1 (Fine 10pt): The quick brown fox jumps over the lazy dog 0123456789", fill=(0, 0, 0))
    draw.text((30, 95), "Line 2 (Fine 8pt): Small text legibility test for Vega Virtual Device canvas scrim", fill=(20, 20, 20))
    draw.text((30, 120), "Line 3 (Contrast test): High contrast dark on light text pattern for blur decay", fill=(0, 0, 0))
    draw.text((30, 145), "Line 4 (Sub-pixel test): Testing anti-aliasing and letter edge preservation", fill=(40, 40, 40))
    draw.text((30, 170), "Line 5 (Latin sample): Lorem ipsum dolor sit amet, consectetur adipiscing elit", fill=(50, 50, 50))

    # 4. Standard grayscale contrast calibration swatches
    swatches = [
        ("BLACK (0%)", (0, 0, 0), (255, 255, 255)),
        ("25% GRAY", (64, 64, 64), (255, 255, 255)),
        ("50% GRAY", (128, 128, 128), (0, 0, 0)),
        ("75% GRAY", (192, 192, 192), (0, 0, 0)),
        ("WHITE (100%)", (255, 255, 255), (0, 0, 0)),
    ]
    swatch_w = 100
    swatch_h = 65
    start_x = 30
    y_pos = 240
    for idx, (label, bg_col, text_col) in enumerate(swatches):
        bx = start_x + idx * (swatch_w + 20)
        draw.rectangle([bx, y_pos, bx + swatch_w, y_pos + swatch_h], fill=bg_col, outline=(100, 100, 100))
        draw.text((bx + 10, y_pos + 25), label, fill=text_col)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        temp_png_path = Path(tf.name)

    ffmpeg_bin = find_tool("ffmpeg")
    try:
        img.save(temp_png_path)
        cmd = [
            ffmpeg_bin,
            "-y",
            "-loop", "1",
            "-i", str(temp_png_path),
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=mono:d={duration}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-t", f"{duration:.3f}",
            "-r", str(fps),
            "-c:a", "aac",
            "-b:a", "64k",
            str(mp4_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg failed to generate detail test pattern:\n{proc.stderr}")
    finally:
        if temp_png_path.exists():
            temp_png_path.unlink()

    spec = ClipSpec(
        clip_name=filename,
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        trigger=None,
        start=None,
        end=None,
        labeller="synthetic_generator",
        metadata={
            "description": "Fine-detail text and contrast pattern for blur/blackout verification",
            "has_fine_text": True,
            "has_1px_grid": True,
            "has_contrast_swatches": True,
        },
    )

    return mp4_path, spec


def synthesize_audio_event(
    event_type: str,
    duration: float,
    sample_rate: int = 44100,
) -> Any:
    """Synthesize an acoustic waveform for positive or negative audio triggers.

    Returns a 1D numpy array of float values in [-1.0, 1.0].
    """
    import numpy as np

    total_samples = int(duration * sample_rate)
    t = np.linspace(0, duration, total_samples, endpoint=False)

    if event_type == "gunshot":
        # Sharp impulse crack with fast exponential decay
        decay_transient = np.exp(-t / 0.04)
        noise = np.random.normal(0, 1, total_samples) * decay_transient
        sub_resonance = 0.5 * np.sin(2 * np.pi * 80 * t) * np.exp(-t / 0.1)
        audio = noise + sub_resonance
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = (audio / peak) * 0.95
        return audio

    elif event_type == "explosion":
        # Low-frequency detonation rumble with downward pitch sweep and turbulent decay
        decay_rumble = np.exp(-t / 0.45)
        sweep_freq = np.linspace(55, 25, total_samples)
        sub_detonation = 0.8 * np.sin(2 * np.pi * sweep_freq * t) * np.exp(-t / 0.6)
        noise = np.random.normal(0, 1, total_samples) * decay_rumble
        audio = noise + sub_detonation
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = (audio / peak) * 0.95
        return audio

    elif event_type == "scream":
        # High-pitched vocal distress vocalization with pitch vibrato and formants
        f_vibrato = 750 + 200 * np.sin(2 * np.pi * 5 * t)
        formant1 = np.sin(2 * np.pi * f_vibrato * t)
        formant2 = 0.6 * np.sin(2 * np.pi * 2 * f_vibrato * t)
        breath_noise = 0.25 * np.random.normal(0, 1, total_samples)
        envelope = np.minimum(1.0, t / 0.1) * np.exp(-np.maximum(0, t - 0.8) / 0.5)
        audio = (formant1 + formant2 + breath_noise) * envelope
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = (audio / peak) * 0.90
        return audio

    elif event_type == "ambient_noise":
        # Gentle continuous pink/brownian noise at -30 dBFS
        noise = np.random.normal(0, 0.03, total_samples)
        return noise

    elif event_type == "calm_dialogue":
        # Modulated conversational speech cadence with pauses, lacking spikes
        f0 = 160 + 20 * np.sin(2 * np.pi * 2 * t)
        cadence = 0.5 * (1 + np.sin(2 * np.pi * 1.25 * t))
        audio = 0.08 * cadence * (np.sin(2 * np.pi * f0 * t) + 0.5 * np.sin(2 * np.pi * 2 * f0 * t))
        return audio

    else:
        raise ValueError(f"Unknown event_type '{event_type}'")


def generate_audio_clip(
    output_dir: Union[str, Path],
    filename: str,
    duration: float,
    events: List[Tuple[str, float, float]],
    fps: float = 24.0,
    width: int = 640,
    height: int = 360,
    sample_rate: int = 44100,
) -> Tuple[Path, List[ClipSpec]]:
    """Generate an MP4 clip with a black video track and placed audio events.

    Args:
        output_dir: Destination directory.
        filename: Target MP4 filename.
        duration: Total clip duration in seconds.
        events: List of (event_type, start_sec, end_sec).
        fps: Video framerate (default: 24.0).
        width: Video width (default: 640).
        height: Video height (default: 360).
        sample_rate: Audio sampling rate (default: 44100).

    Returns:
        Tuple of (path to created MP4, list of ClipSpecs for positive triggers).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / filename

    import numpy as np
    import tempfile
    import wave

    total_samples = int(duration * sample_rate)
    master_audio = np.zeros(total_samples, dtype=np.float32)

    specs: List[ClipSpec] = []

    for event_type, start_s, end_s in events:
        event_dur = end_s - start_s
        event_wave = synthesize_audio_event(event_type, event_dur, sample_rate)

        start_samp = int(round(start_s * sample_rate))
        end_samp = start_samp + len(event_wave)

        if end_samp > total_samples:
            event_wave = event_wave[: total_samples - start_samp]
            end_samp = total_samples

        master_audio[start_samp:end_samp] += event_wave

        # Only create label spec for canonical triggers (positive cases)
        if event_type in CANONICAL_TRIGGERS:
            specs.append(
                ClipSpec(
                    clip_name=filename,
                    duration=duration,
                    fps=fps,
                    width=width,
                    height=height,
                    trigger=event_type,
                    start=round(start_s, 3),
                    end=round(end_s, 3),
                    labeller="synthetic_generator",
                    metadata={
                        "event_type": event_type,
                        "modality": "audio",
                        "acoustic_model": "synthetic_physical_transient",
                    },
                )
            )

    # If no positive events were present, create a negative spec
    if not specs:
        specs.append(
            ClipSpec(
                clip_name=filename,
                duration=duration,
                fps=fps,
                width=width,
                height=height,
                trigger=None,
                start=None,
                end=None,
                labeller="synthetic_generator",
                metadata={"description": "Negative baseline clip without acoustic spikes"},
            )
        )

    # Prevent clipping and convert to 16-bit PCM
    peak = np.max(np.abs(master_audio))
    if peak > 0.99:
        master_audio = (master_audio / peak) * 0.95

    int_samples = (master_audio * 32767).astype(np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        temp_wav_path = Path(tf.name)

    ffmpeg_bin = find_tool("ffmpeg")
    try:
        with wave.open(str(temp_wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(int_samples.tobytes())

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration}",
            "-i", str(temp_wav_path),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", f"{duration:.3f}",
            str(mp4_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg failed to generate audio clip '{filename}':\n{proc.stderr}")
    finally:
        if temp_wav_path.exists():
            temp_wav_path.unlink()

    return mp4_path, specs


def generate_audio_test_suite(
    output_dir: Union[str, Path] = DEFAULT_OUTPUT_DIR,
    include_negatives: bool = True,
) -> Tuple[List[Path], List[ClipSpec]]:
    """Generate audio benchmark clips (gunshot, explosion, scream, and negatives).

    Appends positive trigger labels to labels.csv and updates manifest.json.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    generated_paths: List[Path] = []
    specs: List[ClipSpec] = []

    # 1. Gunshot test clip
    p, s = generate_audio_clip(
        out_dir,
        "audio_gunshot_test.mp4",
        duration=4.0,
        events=[("gunshot", 1.5, 2.0)],
    )
    generated_paths.append(p)
    specs.extend(s)

    # 2. Explosion test clip
    p, s = generate_audio_clip(
        out_dir,
        "audio_explosion_test.mp4",
        duration=5.0,
        events=[("explosion", 1.0, 3.2)],
    )
    generated_paths.append(p)
    specs.extend(s)

    # 3. Scream test clip
    p, s = generate_audio_clip(
        out_dir,
        "audio_scream_test.mp4",
        duration=4.0,
        events=[("scream", 1.2, 2.7)],
    )
    generated_paths.append(p)
    specs.extend(s)

    # 4. Multi-event composite mix
    p, s = generate_audio_clip(
        out_dir,
        "audio_multi_event_mix.mp4",
        duration=9.0,
        events=[
            ("gunshot", 1.0, 1.5),
            ("scream", 3.0, 4.5),
            ("explosion", 6.0, 8.2),
        ],
    )
    generated_paths.append(p)
    specs.extend(s)

    # 5. Negative audio clips (if requested)
    if include_negatives:
        # Negative 1: Ambient room tone
        p, s = generate_audio_clip(
            out_dir,
            "audio_negative_ambient.mp4",
            duration=5.0,
            events=[("ambient_noise", 0.0, 5.0)],
        )
        generated_paths.append(p)
        specs.extend(s)

        # Negative 2: Calm conversational speech
        p, s = generate_audio_clip(
            out_dir,
            "audio_negative_dialogue.mp4",
            duration=5.0,
            events=[("calm_dialogue", 0.0, 5.0)],
        )
        generated_paths.append(p)
        specs.extend(s)

    # Update labels.csv (load existing labels if any, append new positive triggers)
    labels_csv_path = out_dir / "labels.csv"
    existing_records: List[Dict[str, Any]] = []
    if labels_csv_path.is_file():
        existing_records = read_labels_csv(labels_csv_path)

    new_positive_rows = [sp.to_label_dict() for sp in specs if sp.to_label_dict() is not None]

    # Deduplicate against existing by (clip, start, trigger)
    seen = {(r["clip"], round(float(r["start"]), 3), r["trigger"]) for r in existing_records}
    merged_rows = list(existing_records)
    for row in new_positive_rows:
        key = (row["clip"], round(float(row["start"]), 3), row["trigger"])
        if key not in seen:
            seen.add(key)
            merged_rows.append(row)

    write_labels_csv(merged_rows, labels_csv_path)

    return generated_paths, specs


DEFAULT_SPEECH_CUES: List[Dict[str, Any]] = [
    {
        "index": 1,
        "start": 1.0,
        "end": 3.5,
        "text": "What the hell is going on here?",
        "kind": "speech",
        "expected_flagged": ["hell"],
        "trigger": "profanity",
    },
    {
        "index": 2,
        "start": 4.5,
        "end": 6.0,
        "text": "[sighs heavily]",
        "kind": "sound_description",
        "expected_flagged": [],
        "trigger": None,
    },
    {
        "index": 3,
        "start": 7.0,
        "end": 9.5,
        "text": "Don't give me that crap again.",
        "kind": "speech",
        "expected_flagged": ["crap"],
        "trigger": "profanity",
    },
    {
        "index": 4,
        "start": 10.5,
        "end": 12.5,
        "text": "Damn right, we are finishing this project.",
        "kind": "speech",
        "expected_flagged": ["damn"],
        "trigger": "profanity",
    },
    {
        "index": 5,
        "start": 13.5,
        "end": 16.0,
        "text": "This is a completely clean sentence without triggers.",
        "kind": "speech",
        "expected_flagged": [],
        "trigger": None,
    },
]


def format_srt_timestamp(seconds: float) -> str:
    """Format floating seconds into standard SRT timestamp 00:00:00,000."""
    millis = int(round(seconds * 1000))
    hours = millis // 3600000
    millis %= 3600000
    minutes = millis // 60000
    millis %= 60000
    secs = millis // 1000
    millis %= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt_file(cues: List[Dict[str, Any]], output_path: Union[str, Path]) -> Path:
    """Write subtitle cues to standard SubRip (.srt) file."""
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for idx, cue in enumerate(cues, 1):
        st_str = format_srt_timestamp(float(cue["start"]))
        en_str = format_srt_timestamp(float(cue["end"]))
        lines.append(str(idx))
        lines.append(f"{st_str} --> {en_str}")
        lines.append(cue["text"])
        lines.append("")
    out_p.write_text("\n".join(lines), encoding="utf-8")
    return out_p


def synthesize_speech_line(text: str, target_wav: Path) -> Path:
    """Synthesize speech audio for a text line using system TTS or acoustic formant fallback."""
    import platform

    if platform.system() == "Windows" and shutil.which("powershell"):
        escaped_text = text.replace("'", "''")
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Add-Type -AssemblyName System.Speech; "
            f"$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$synth.SetOutputToWaveFile('{target_wav.resolve()}'); "
            f"$synth.Speak('{escaped_text}'); "
            f"$synth.Dispose();",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and target_wav.is_file() and target_wav.stat().st_size > 44:
                return target_wav
        except Exception:
            pass

    # Acoustic speech-formant cadence fallback
    import numpy as np
    import wave

    sr = 44100
    words = len(text.split())
    dur = max(1.5, words * 0.4)
    total_samples = int(dur * sr)
    t = np.linspace(0, dur, total_samples, endpoint=False)
    f0 = 150 + 30 * np.sin(2 * np.pi * 3 * t)
    cadence = 0.5 * (1 + np.sin(2 * np.pi * 1.5 * t))
    audio = 0.4 * cadence * (np.sin(2 * np.pi * f0 * t) + 0.6 * np.sin(2 * np.pi * 2 * f0 * t))
    int_samples = (audio * 32767).astype(np.int16)
    with wave.open(str(target_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(int_samples.tobytes())
    return target_wav


def verify_subtitle_parser_on_speech_clip(
    srt_path: Union[str, Path],
    wordlist_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Hook the existing subtitle parser into this script to verify it catches the trigger words."""
    from scripts.parse_subtitles import load_wordlist, match_profanity_in_cues, parse_srt_file

    w_path = Path(wordlist_path) if wordlist_path else REPO_ROOT / "data" / "profanity_wordlist.txt"
    words = load_wordlist(w_path)
    sub_file = parse_srt_file(str(srt_path))

    raw_detections = match_profanity_in_cues(sub_file, words)

    total_cues = len(sub_file)
    sound_desc_count = sum(1 for c in sub_file if getattr(c, "kind", "") == "sound_description")
    speech_count = sum(1 for c in sub_file if getattr(c, "kind", "") == "speech")
    flagged_cues = [c for c in sub_file if getattr(c, "flagged", [])]

    if total_cues < 5:
        raise AssertionError(f"Expected at least 5 cues, got {total_cues}")
    if sound_desc_count < 1:
        raise AssertionError("Expected at least 1 sound_description cue")
    if len(flagged_cues) < 3:
        raise AssertionError(f"Expected at least 3 flagged profanity cues, got {len(flagged_cues)}")

    return {
        "total_cues": total_cues,
        "speech_cues": speech_count,
        "sound_descriptions": sound_desc_count,
        "flagged_cues": len(flagged_cues),
        "raw_detections": raw_detections,
    }


def generate_speech_test_clip(
    output_dir: Union[str, Path] = DEFAULT_OUTPUT_DIR,
    mp4_filename: str = "speech_triggers_test.mp4",
    srt_filename: str = "speech_triggers_truth.srt",
    cues: Optional[List[Dict[str, Any]]] = None,
    fps: float = 24.0,
    width: int = 640,
    height: int = 360,
    sample_rate: int = 44100,
) -> Tuple[Path, Path, List[ClipSpec]]:
    """Generate synthesized speech audio MP4 with matching truth SRT and parser validation.

    Args:
        output_dir: Destination directory.
        mp4_filename: Target MP4 filename.
        srt_filename: Target SRT filename.
        cues: List of cue dictionaries (defaults to DEFAULT_SPEECH_CUES).
        fps: Video framerate.
        width: Video width.
        height: Video height.
        sample_rate: Audio sampling rate.

    Returns:
        Tuple of (mp4_path, srt_path, positive ClipSpecs).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / mp4_filename
    srt_path = out_dir / srt_filename

    used_cues = cues or DEFAULT_SPEECH_CUES

    # 1. Write the matching truth .srt file
    write_srt_file(used_cues, srt_path)

    # 2. Hook and execute subtitle parser against this truth file
    parser_audit = verify_subtitle_parser_on_speech_clip(srt_path)

    # 3. Synthesize speech lines and assemble audio track
    import numpy as np
    import tempfile
    import wave

    max_end = max(float(c["end"]) for c in used_cues)
    total_duration = math.ceil(max_end + 1.0)
    total_samples = int(total_duration * sample_rate)
    master_audio = np.zeros(total_samples, dtype=np.float32)

    specs: List[ClipSpec] = []

    for cue in used_cues:
        st_s = float(cue["start"])
        en_s = float(cue["end"])
        text = cue["text"]
        kind = cue.get("kind", "speech")

        # Synthesize audio if speech
        if kind == "speech":
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                line_wav = Path(tf.name)
            try:
                synthesize_speech_line(text, line_wav)
                with wave.open(str(line_wav), "rb") as wf:
                    n = wf.getnframes()
                    raw = wf.readframes(n)
                    sig = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                start_idx = int(st_s * sample_rate)
                end_idx = min(total_samples, start_idx + len(sig))
                available_dur = int((en_s - st_s) * sample_rate)
                copy_len = min(len(sig), available_dur, total_samples - start_idx)

                master_audio[start_idx : start_idx + copy_len] += sig[:copy_len]
            finally:
                if line_wav.exists():
                    line_wav.unlink()

        # If trigger is profanity, record ClipSpec
        if cue.get("trigger") == "profanity":
            specs.append(
                ClipSpec(
                    clip_name=mp4_filename,
                    duration=float(total_duration),
                    fps=fps,
                    width=width,
                    height=height,
                    trigger="profanity",
                    start=round(st_s, 3),
                    end=round(en_s, 3),
                    labeller="synthetic_generator",
                    metadata={
                        "text": text,
                        "kind": kind,
                        "flagged_words": cue.get("expected_flagged", []),
                        "modality": "speech_subtitles",
                    },
                )
            )

    # Normalize audio
    peak = np.max(np.abs(master_audio))
    if peak > 0.99:
        master_audio = (master_audio / peak) * 0.95
    int_samples = (master_audio * 32767).astype(np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        temp_wav_path = Path(tf.name)

    ffmpeg_bin = find_tool("ffmpeg")
    try:
        with wave.open(str(temp_wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(int_samples.tobytes())

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={total_duration}",
            "-i", str(temp_wav_path),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", f"{total_duration:.3f}",
            str(mp4_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg failed to generate speech clip:\n{proc.stderr}")
    finally:
        if temp_wav_path.exists():
            temp_wav_path.unlink()

    # Append profanity triggers to labels.csv
    labels_csv_path = out_dir / "labels.csv"
    existing_records: List[Dict[str, Any]] = []
    if labels_csv_path.is_file():
        existing_records = read_labels_csv(labels_csv_path)

    new_positive_rows = [sp.to_label_dict() for sp in specs if sp.to_label_dict() is not None]
    seen = {(r["clip"], round(float(r["start"]), 3), r["trigger"]) for r in existing_records}
    merged_rows = list(existing_records)
    for row in new_positive_rows:
        key = (row["clip"], round(float(row["start"]), 3), row["trigger"])
        if key not in seen:
            seen.add(key)
            merged_rows.append(row)

    write_labels_csv(merged_rows, labels_csv_path)

    return mp4_path, srt_path, specs


def generate_negative_video_clips(
    output_dir: Union[str, Path] = DEFAULT_OUTPUT_DIR,
    width: int = 640,
    height: int = 360,
    fps: float = 24.0,
) -> Tuple[List[Path], List[ClipSpec]]:
    """Generate negative video clips (smooth spatial gradients, slow fades, static gray).

    These clips have NO rapid luminance transitions (< 0.5 Hz / delta Y << 0.1),
    providing clean negative controls for the flash detector.

    Constraint:
        These clips have NO trigger label (trigger=None) and are strictly NOT written to labels.csv.

    Args:
        output_dir: Destination directory.
        width: Video width (default: 640).
        height: Video height (default: 360).
        fps: Framerate (default: 24.0).

    Returns:
        Tuple of (list of generated MP4 paths, list of negative ClipSpecs).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = find_tool("ffmpeg")

    generated_paths: List[Path] = []
    specs: List[ClipSpec] = []

    # 1. Smooth moving spatial gradient (horizontal sinusoidal luminance wave)
    p1 = out_dir / "video_negative_smooth_gradient.mp4"
    vf_grad = (
        "geq=r='128+35*sin(2*PI*(X/W + T*0.1))':"
        "g='128+35*sin(2*PI*(X/W + T*0.1))':"
        "b='128+35*sin(2*PI*(X/W + T*0.1))'"
    )
    cmd1 = [
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", f"color=c=0x808080:s={width}x{height}:r={fps}:d=5",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=5",
        "-vf", vf_grad,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        "-t", "5.000",
        str(p1),
    ]
    subprocess.run(cmd1, capture_output=True, check=True)
    generated_paths.append(p1)
    specs.append(
        ClipSpec(
            clip_name="video_negative_smooth_gradient.mp4",
            duration=5.0,
            fps=fps,
            width=width,
            height=height,
            trigger=None,
            metadata={"description": "Smooth spatial sinusoidal luminance gradient, delta Y < 0.05"},
        )
    )

    # 2. Slow fade in and fade out (0.2 Hz, far below 3 Hz flash threshold)
    p2 = out_dir / "video_negative_slow_fade.mp4"
    cmd2 = [
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", f"color=c=0x404040:s={width}x{height}:r={fps}:d=5",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=5",
        "-vf", "fade=t=in:st=0:d=2.5,fade=t=out:st=2.5:d=2.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        "-t", "5.000",
        str(p2),
    ]
    subprocess.run(cmd2, capture_output=True, check=True)
    generated_paths.append(p2)
    specs.append(
        ClipSpec(
            clip_name="video_negative_slow_fade.mp4",
            duration=5.0,
            fps=fps,
            width=width,
            height=height,
            trigger=None,
            metadata={"description": "Slow continuous fade (0.2 Hz) below WCAG 3 Hz flash cutoff"},
        )
    )

    # 3. Static neutral gray video
    p3 = out_dir / "video_negative_static.mp4"
    cmd3 = [
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", f"color=c=0x808080:s={width}x{height}:r={fps}:d=5",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        "-t", "5.000",
        str(p3),
    ]
    subprocess.run(cmd3, capture_output=True, check=True)
    generated_paths.append(p3)
    specs.append(
        ClipSpec(
            clip_name="video_negative_static.mp4",
            duration=5.0,
            fps=fps,
            width=width,
            height=height,
            trigger=None,
            metadata={"description": "Completely static 50% neutral gray video"},
        )
    )

    return generated_paths, specs





def main() -> None:
    """CLI entrypoint for generating synthetic test clips."""
    parser = argparse.ArgumentParser(
        description="Automated Synthetic Test-Clip and Ground-Truth Generator (Person C)."
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory to output generated test clips (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Generate only the sync test clip (flash + beep).",
    )
    parser.add_argument(
        "--flash-only",
        action="store_true",
        help="Generate only the flash test cases suite.",
    )
    parser.add_argument(
        "--detail-only",
        action="store_true",
        help="Generate only the fine-detail pattern clip for blur checks.",
    )
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="Generate only the audio benchmark suite (gunshots, explosions, screams, negatives).",
    )
    parser.add_argument(
        "--speech-only",
        action="store_true",
        help="Generate only the speech triggers test clip, matching .srt, and parser verification.",
    )
    parser.add_argument(
        "--negatives-only",
        action="store_true",
        help="Generate only negative video and audio clips (gradients, slow fades, ambient, dialogue).",
    )

    args = parser.parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("Trigger-Aware Player — Synthetic Test-Clip Generator")
    print(f"Target Output Directory: {out_dir}")
    print("=" * 70)

    if args.sync_only:
        print("[1/1] Generating sync test clip...")
        sync_path, sync_specs = generate_sync_clip(out_dir)
        print(f"Generated sync clip: {sync_path}")
        print(f"Wrote sync ground truth: {out_dir / 'sync_labels.csv'}")
    elif args.flash_only:
        print("[1/1] Generating flash benchmark suite...")
        paths, specs = generate_flash_test_suite(out_dir)
        print(f"Generated {len(paths)} flash test clips in {out_dir}")
        print(f"Wrote labels to: {out_dir / 'labels.csv'}")
        print(f"Wrote manifest to: {out_dir / 'manifest.json'}")
    elif args.detail_only:
        print("[1/1] Generating fine-detail pattern clip for blur checks...")
        detail_path, detail_spec = generate_detail_pattern_clip(out_dir)
        print(f"Generated detail pattern clip: {detail_path}")
        print("(Note: Not written to labels.csv as this clip has no trigger label)")
    elif args.audio_only:
        print("[1/1] Generating audio benchmark suite (gunshot, explosion, scream, negatives)...")
        audio_paths, audio_specs = generate_audio_test_suite(out_dir, include_negatives=True)
        print(f"Generated {len(audio_paths)} audio test clips in {out_dir}")
        print(f"Updated labels in: {out_dir / 'labels.csv'}")
    elif args.speech_only:
        print("[1/1] Generating speech trigger test clip and matching .srt...")
        sp_mp4, sp_srt, sp_specs = generate_speech_test_clip(out_dir)
        print(f"Generated speech MP4: {sp_mp4}")
        print(f"Generated matching SRT: {sp_srt}")
        print(f"Verified subtitle parser caught {len(sp_specs)} profanity triggers.")
        print(f"Updated labels in: {out_dir / 'labels.csv'}")
    elif args.negatives_only:
        print("[1/2] Generating negative video clips (gradients, slow fades, static)...")
        neg_v_paths, neg_v_specs = generate_negative_video_clips(out_dir)
        print(f"Generated {len(neg_v_paths)} negative video clips in {out_dir}")

        print("[2/2] Generating negative audio clips (ambient, calm dialogue)...")
        neg_a_paths, neg_a_specs = generate_audio_test_suite(out_dir, include_negatives=True)
        print(f"Generated negative audio controls in {out_dir}")
        print("(Note: All negative clips are strictly omitted from labels.csv)")
    else:
        print("[1/6] Generating sync test clip...")
        sync_path, sync_specs = generate_sync_clip(out_dir)
        print(f"Generated sync clip: {sync_path}")
        print(f"Wrote sync ground truth: {out_dir / 'sync_labels.csv'}")

        print("\n[2/6] Generating flash benchmark suite...")
        flash_paths, flash_specs = generate_flash_test_suite(out_dir)
        print(f"Generated {len(flash_paths)} flash test clips in {out_dir}")
        print(f"Wrote primary labels to: {out_dir / 'labels.csv'}")

        print("\n[3/6] Generating fine-detail pattern clip for blur checks...")
        detail_path, detail_spec = generate_detail_pattern_clip(out_dir)
        print(f"Generated detail pattern clip: {detail_path}")
        print("(Note: Not written to labels.csv as this clip has no trigger label)")

        print("\n[4/6] Generating audio benchmark suite (gunshot, explosion, scream, negatives)...")
        audio_paths, audio_specs = generate_audio_test_suite(out_dir, include_negatives=True)
        print(f"Generated {len(audio_paths)} audio test clips in {out_dir}")
        print(f"Updated primary labels in: {out_dir / 'labels.csv'}")

        print("\n[5/6] Generating speech trigger test clip and matching .srt...")
        sp_mp4, sp_srt, sp_specs = generate_speech_test_clip(out_dir)
        print(f"Generated speech MP4: {sp_mp4}")
        print(f"Generated matching SRT: {sp_srt}")
        print(f"Verified subtitle parser caught {len(sp_specs)} profanity triggers.")
        print(f"Updated primary labels in: {out_dir / 'labels.csv'}")

        print("\n[6/6] Generating negative video clips (gradients, slow fades, static)...")
        neg_v_paths, neg_v_specs = generate_negative_video_clips(out_dir)
        print(f"Generated {len(neg_v_paths)} negative video clips in {out_dir}")
        print("(Note: All negative clips are strictly omitted from labels.csv)")

        # Update manifest with all specs
        all_specs = flash_specs + [detail_spec] + audio_specs + sp_specs + neg_v_specs
        write_manifest_json(all_specs, out_dir / "manifest.json")

    print("\nGeneration complete!")


if __name__ == "__main__":
    main()






