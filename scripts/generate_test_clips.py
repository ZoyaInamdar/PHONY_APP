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

    Header format: clip,trigger,start,end,labeller
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

    fieldnames = ["clip", "trigger", "start", "end", "labeller"]

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
            writer.writerow({
                "clip": r["clip"],
                "trigger": r["trigger"],
                "start": f"{float(r['start']):.3f}",
                "end": f"{float(r['end']):.3f}",
                "labeller": r.get("labeller", "synthetic_generator"),
            })

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

