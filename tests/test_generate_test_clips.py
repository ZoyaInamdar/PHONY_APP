"""Unit and integration tests for synthetic test-clip generator (Person C).

Verifies:
1. Whole-number frame phase constraints for flash rates.
2. Area geometries and contrast step levels.
3. Synchronized flash+beep calibration clip and separate sync_labels.csv.
4. Generated MP4 media files have valid video and audio streams.
5. Primary ground-truth labels in labels.csv strictly match generated clip durations:
   0.0 <= start < end <= clip_duration.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict

import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.generate_test_clips import (
    CANONICAL_TRIGGERS,
    ClipSpec,
    compute_flash_timing,
    find_tool,
    generate_flash_clip,
    generate_sync_clip,
    get_area_geometry,
    read_labels_csv,
    write_labels_csv,
    write_sync_labels_csv,
)


def probe_clip(clip_path: Path) -> Dict[str, Any]:
    """Probe an MP4 file with ffprobe to extract duration and stream properties."""
    ffprobe_bin = find_tool("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,duration",
        "-of", "json",
        str(clip_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def test_compute_flash_timing_design_constraints():
    """Verify each phase is a whole number of frames and records actual rate."""
    # 24 fps at 4 flashes/sec -> 3 frames bright, 3 dark -> actual 4.0 Hz
    b, d, rate = compute_flash_timing(fps=24.0, target_flash_rate=4.0)
    assert b == 3
    assert d == 3
    assert rate == 4.0

    # 24 fps at 2 flashes/sec -> 6 frames bright, 6 dark -> actual 2.0 Hz
    b, d, rate = compute_flash_timing(fps=24.0, target_flash_rate=2.0)
    assert b == 6
    assert d == 6
    assert rate == 2.0

    # 24 fps at 3 flashes/sec -> 4 frames bright, 4 dark -> actual 3.0 Hz
    b, d, rate = compute_flash_timing(fps=24.0, target_flash_rate=3.0)
    assert b == 4
    assert d == 4
    assert rate == 3.0

    # 24 fps at 6 flashes/sec -> 2 frames bright, 2 dark -> actual 6.0 Hz
    b, d, rate = compute_flash_timing(fps=24.0, target_flash_rate=6.0)
    assert b == 2
    assert d == 2
    assert rate == 6.0

    # 25 fps at 4 flashes/sec -> 3 frames bright, 3 dark -> cycle 6 frames -> actual rate 4.167 Hz
    b, d, rate = compute_flash_timing(fps=25.0, target_flash_rate=4.0)
    assert b == 3
    assert d == 3
    assert rate == pytest.approx(25.0 / 6.0, 0.001)

    # 30 fps at 4 flashes/sec -> 4 frames bright, 4 dark -> cycle 8 frames -> actual rate 3.75 Hz
    b, d, rate = compute_flash_timing(fps=30.0, target_flash_rate=4.0)
    assert b == 4
    assert d == 4
    assert rate == pytest.approx(30.0 / 8.0, 0.001)

    # 60 fps at 4 flashes/sec -> 8 frames bright, 8 dark -> cycle 16 frames -> actual rate 3.75 Hz
    b, d, rate = compute_flash_timing(fps=60.0, target_flash_rate=4.0)
    assert b == 8
    assert d == 8
    assert rate == pytest.approx(60.0 / 16.0, 0.001)


def test_area_geometries():
    """Verify area geometry coordinates and surface fractions."""
    w, h = 640, 360
    # Full
    x, y, box_w, box_h, frac = get_area_geometry("full", w, h)
    assert (x, y, box_w, box_h) == (0, 0, 640, 360)
    assert frac == 1.0

    # Half (50%)
    x, y, box_w, box_h, frac = get_area_geometry("half", w, h)
    assert box_w * box_h == 640 * 180
    assert frac == pytest.approx(0.5, 0.01)

    # Quarter (25% - WCAG boundary)
    x, y, box_w, box_h, frac = get_area_geometry("quarter", w, h)
    assert (box_w, box_h) == (320, 180)
    assert frac == pytest.approx(0.25, 0.01)

    # Small patch (6.25% - sub-threshold)
    x, y, box_w, box_h, frac = get_area_geometry("small_patch", w, h)
    assert (box_w, box_h) == (160, 90)
    assert frac == pytest.approx(0.0625, 0.01)

    with pytest.raises(ValueError):
        get_area_geometry("invalid_area", w, h)


def test_write_and_read_labels_csv(tmp_path: Path):
    """Verify labels.csv serialization, sorting, and validation."""
    labels_file = tmp_path / "labels.csv"
    records = [
        {"clip": "clip_b.mp4", "trigger": "flashing", "start": 2.0, "end": 4.5, "labeller": "synthetic_generator"},
        {"clip": "clip_a.mp4", "trigger": "flashing", "start": 1.0, "end": 3.0, "labeller": "synthetic_generator", "actual_rate": 4.0},
    ]

    out_path = write_labels_csv(records, labels_file)
    assert out_path.is_file()

    loaded = read_labels_csv(labels_file)
    assert len(loaded) == 2
    # Verify sorting by clip
    assert loaded[0]["clip"] == "clip_a.mp4"
    assert loaded[1]["clip"] == "clip_b.mp4"

    # Invalid trigger rejection
    with pytest.raises(ValueError, match="Invalid trigger"):
        write_labels_csv([{"clip": "c.mp4", "trigger": "non_canonical", "start": 1.0, "end": 2.0}], labels_file)

    # Invalid interval rejection
    with pytest.raises(ValueError, match="Invalid interval"):
        write_labels_csv([{"clip": "c.mp4", "trigger": "flashing", "start": 2.0, "end": 1.0}], labels_file)


def test_generate_sync_clip_and_duration_check(tmp_path: Path):
    """Verify sync clip generation, streams presence, and sync_labels.csv."""
    mp4_path, specs = generate_sync_clip(
        output_dir=tmp_path,
        filename="test_sync.mp4",
        duration=3.0,
        fps=24.0,
        marker_intervals=[(1.0, 1.2), (2.0, 2.2)],
    )

    assert mp4_path.is_file()
    assert len(specs) == 2

    # Probe file with ffprobe
    probe_data = probe_clip(mp4_path)
    streams = probe_data.get("streams", [])
    codec_types = {s["codec_type"] for s in streams}
    assert "video" in codec_types
    assert "audio" in codec_types

    duration_s = float(probe_data["format"]["duration"])
    assert duration_s == pytest.approx(3.0, abs=0.1)

    # Verify sync_labels.csv is written and isolated from primary labels.csv
    sync_csv = tmp_path / "sync_labels.csv"
    assert sync_csv.is_file()
    with sync_csv.open("r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2
        for row in reader:
            assert 0.0 <= float(row["start"]) < float(row["end"]) <= duration_s
            assert row["clip"] == "test_sync.mp4"


def test_labels_match_generated_clip_durations(tmp_path: Path):
    """Core Delivery 1 Test: Ensure intervals in labels.csv match generated clip durations."""
    test_cases = [
        ("flash_24fps_4hz_full.mp4", 24.0, 4.0, "full", 0.50),
        ("flash_25fps_3hz_quarter.mp4", 25.0, 3.0, "quarter", 0.20),
        ("flash_30fps_2hz_half.mp4", 30.0, 2.0, "half", 0.10),
        ("flash_60fps_6hz_patch.mp4", 60.0, 6.0, "small_patch", 0.05),
    ]

    all_specs: list[ClipSpec] = []
    for fname, fps, rate, area, contrast in test_cases:
        p, spec = generate_flash_clip(
            output_dir=tmp_path,
            filename=fname,
            fps=fps,
            target_flash_rate=rate,
            area=area,
            contrast_step=contrast,
            lead_in_seconds=0.5,
            num_cycles=4,
            lead_out_seconds=0.5,
        )
        assert p.is_file()
        all_specs.append(spec)

    labels_csv_path = tmp_path / "labels.csv"
    write_labels_csv(
        [s.to_label_dict() for s in all_specs if s.to_label_dict()],
        labels_csv_path,
    )

    # Read back and probe media to verify truth intervals against real video streams
    labels = read_labels_csv(labels_csv_path)
    assert len(labels) == len(test_cases)

    for row in labels:
        clip_file = tmp_path / row["clip"]
        assert clip_file.is_file(), f"Clip file missing: {clip_file}"

        probe_data = probe_clip(clip_file)
        streams = probe_data.get("streams", [])
        codec_types = {s["codec_type"] for s in streams}
        assert "video" in codec_types, f"Clip {row['clip']} must contain a video track"
        assert "audio" in codec_types, f"Clip {row['clip']} must contain an audio track"

        media_duration = float(probe_data["format"]["duration"])
        start = float(row["start"])
        end = float(row["end"])

        # Label constraint 1: Start must be non-negative
        assert start >= 0.0, f"Start time {start} is negative in {row['clip']}"

        # Label constraint 2: End must be strictly greater than start
        assert end > start, f"End time {end} <= start {start} in {row['clip']}"

        # Label constraint 3: End must not exceed clip duration
        assert end <= media_duration + 0.05, (
            f"Label interval [{start}, {end}) exceeds clip duration {media_duration} in {row['clip']}"
        )

        # Label constraint 4: Trigger must be canonical 'flashing'
        assert row["trigger"] in CANONICAL_TRIGGERS
        assert row["trigger"] == "flashing"
