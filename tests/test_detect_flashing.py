"""Unit tests for Rapid Flashing / Strobe Lights detector (scripts/detect_flashing.py).

Verifies Person A flashing detection requirements:
1. No flashes in a stable video (constant or uniform frames).
2. Brightness changes below threshold (delta Y < 0.10, e.g. subtle shifts or slow fades).
3. Repeated brightness changes inside configured frequency window (e.g. 4 Hz, 3 Hz).
4. Changes outside frequency window (single isolated cuts, unidirectional changes without oscillation).
5. Correct timestamps and output schema compliance ([start, end), score in [0,1], trigger='flashing').
6. Missing/corrupt input handling (non-existent file, zero frames, empty input).
7. End-of-video boundary behavior (flashing right up to the final frame).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List

import numpy as np
import pytest

from scripts.detect_flashing import (
    DEFAULT_SOURCE,
    DEFAULT_TRIGGER,
    compute_relative_luminance_grid,
    detect_flashing,
    detect_flashing_from_frames,
    get_video_metadata,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_solid_frame(gray_val: int, width: int = 128, height: int = 72) -> np.ndarray:
    """Create a solid RGB frame with constant luminance."""
    return np.full((height, width, 3), gray_val, dtype=np.uint8)


def make_flashing_frame_sequence(
    fps: float = 24.0,
    flash_hz: float = 4.0,
    dark_val: int = 64,
    bright_val: int = 192,
    lead_in_sec: float = 1.0,
    flash_dur_sec: float = 2.0,
    lead_out_sec: float = 1.0,
    width: int = 128,
    height: int = 72,
) -> List[np.ndarray]:
    """Generate in-memory synthetic frame sequence alternating between dark and bright."""
    half_cycle = max(1, int(round(fps / (2.0 * flash_hz))))
    cycle_frames = 2 * half_cycle

    lead_in_frames = int(round(lead_in_sec * fps))
    flash_frames = int(round(flash_dur_sec * fps))
    lead_out_frames = int(round(lead_out_sec * fps))

    frames = []
    # Neutral lead-in
    neutral_val = int((dark_val + bright_val) / 2)
    for _ in range(lead_in_frames):
        frames.append(make_solid_frame(neutral_val, width, height))

    # Flashing phase
    for i in range(flash_frames):
        phase = i % cycle_frames
        val = bright_val if phase < half_cycle else dark_val
        frames.append(make_solid_frame(val, width, height))

    # Neutral lead-out
    for _ in range(lead_out_frames):
        frames.append(make_solid_frame(neutral_val, width, height))

    return frames


class TestFlashingDetector:
    """Comprehensive test suite for detect_flashing logic."""

    def test_1_no_flashes_in_stable_video(self):
        """Stable video with unchanging frames produces zero detections."""
        frames = [make_solid_frame(128) for _ in range(72)]  # 3 seconds at 24 fps
        dets = detect_flashing_from_frames(frames, fps=24.0)
        assert dets == []

    def test_2_brightness_change_below_threshold(self):
        """Brightness changes below cell_luminance_threshold (0.10) produce zero detections."""
        # 128 to 135 corresponds to delta Y ~ 0.027 in linearized sRGB
        frames = []
        for i in range(72):
            val = 135 if (i % 6) < 3 else 128
            frames.append(make_solid_frame(val))
        dets = detect_flashing_from_frames(frames, fps=24.0, cell_threshold=0.10)
        assert dets == []

    def test_2_gradual_fade_below_threshold(self):
        """Gradual linear fade where frame-to-frame delta is tiny produces zero detections."""
        frames = [make_solid_frame(int(64 + i)) for i in range(120)]
        dets = detect_flashing_from_frames(frames, fps=24.0)
        assert dets == []

    def test_3_repeated_brightness_changes_inside_frequency_window(self):
        """4 Hz repetitive flashing produces exactly one segment with accurate timing."""
        fps = 24.0
        lead_in = 1.0
        flash_dur = 2.0
        frames = make_flashing_frame_sequence(
            fps=fps,
            flash_hz=4.0,
            dark_val=64,
            bright_val=192,
            lead_in_sec=lead_in,
            flash_dur_sec=flash_dur,
            lead_out_sec=1.0,
        )

        dets = detect_flashing_from_frames(frames, fps=fps)
        assert len(dets) == 1
        d = dets[0]
        assert d["trigger"] == "flashing"
        assert d["source"] == DEFAULT_SOURCE
        assert d["start"] == pytest.approx(1.0, abs=0.05)
        assert d["end"] == pytest.approx(3.0, abs=0.05)
        assert 0.0 <= d["score"] <= 1.0

    def test_4_changes_outside_frequency_window_single_cut(self):
        """A single scene cut (instant jump from dark to bright without oscillation) produces no detections."""
        # 30 frames dark (64), then 30 frames bright (192)
        frames = [make_solid_frame(64) for _ in range(30)] + [make_solid_frame(192) for _ in range(30)]
        dets = detect_flashing_from_frames(frames, fps=24.0)
        assert dets == []

    def test_4_changes_outside_frequency_window_two_unidirectional_cuts(self):
        """Two cuts in the same direction without oscillation produce zero detections."""
        frames = (
            [make_solid_frame(40) for _ in range(20)]
            + [make_solid_frame(100) for _ in range(20)]
            + [make_solid_frame(180) for _ in range(20)]
        )
        dets = detect_flashing_from_frames(frames, fps=24.0)
        assert dets == []

    def test_5_output_schema_and_timestamp_constraints(self):
        """Detection dictionaries strictly conform to shared cue contract."""
        fps = 30.0
        frames = make_flashing_frame_sequence(
            fps=fps, flash_hz=3.0, lead_in_sec=0.5, flash_dur_sec=1.5, lead_out_sec=0.5
        )
        dets = detect_flashing_from_frames(frames, fps=fps)
        assert len(dets) >= 1

        for d in dets:
            assert isinstance(d["start"], float)
            assert isinstance(d["end"], float)
            assert d["start"] >= 0.0
            assert d["end"] > d["start"]
            assert d["trigger"] == DEFAULT_TRIGGER
            assert isinstance(d["score"], float)
            assert 0.0 <= d["score"] <= 1.0
            assert d["source"] == DEFAULT_SOURCE

    def test_6_missing_and_corrupt_input_handling(self, tmp_path):
        """Gracefully handle missing files, empty files, or corrupt non-video files."""
        # Nonexistent file
        with pytest.raises(FileNotFoundError):
            detect_flashing(tmp_path / "nonexistent_video.mp4")

        # Corrupt file
        corrupt_file = tmp_path / "corrupt.mp4"
        corrupt_file.write_bytes(b"not a valid video stream content")
        dets = detect_flashing(corrupt_file)
        assert dets == []

        # Empty frame stream
        empty_dets = detect_flashing_from_frames([], fps=24.0)
        assert empty_dets == []

    def test_7_end_of_video_boundary_behavior(self):
        """Flash sequence that continues directly to the final frame terminates cleanly at end of video."""
        fps = 24.0
        # 1s lead-in, 2s flashing with NO lead-out (video ends immediately after flashing)
        frames = make_flashing_frame_sequence(
            fps=fps, flash_hz=4.0, lead_in_sec=1.0, flash_dur_sec=2.0, lead_out_sec=0.0
        )
        dets = detect_flashing_from_frames(frames, fps=fps)
        assert len(dets) == 1
        d = dets[0]
        assert d["start"] == pytest.approx(1.0, abs=0.05)
        # End should be at 3.0s (the end of the 2s flash window)
        assert d["end"] == pytest.approx(3.0, abs=0.05)

    def test_spatial_area_threshold_filtering(self):
        """Sub-threshold spatial area (< 25% of grid cells) does not trigger flashing."""
        fps = 24.0
        frames = []
        total_cells = 16 * 9  # 144
        # Create frames where only top 10% (14 cells) flash, remaining 90% stays neutral
        for i in range(72):
            frame = np.full((72, 128, 3), 128, dtype=np.uint8)
            val = 220 if (i % 6) < 3 else 40
            frame[:8, :16, :] = val  # 1 row of 2 cells = 2 cells << 36 cells (25%)
            frames.append(frame)

        dets = detect_flashing_from_frames(frames, fps=fps, area_threshold=0.25)
        assert dets == []

    def test_cli_execution(self, tmp_path):
        """Test standalone CLI execution writing output JSON."""
        test_clip = REPO_ROOT / "data" / "test_clips" / "flash_rate_4hz_24fps_full_c050.mp4"
        if not test_clip.is_file():
            pytest.skip("Test clip not found on disk")

        out_json = tmp_path / "cues.json"
        cmd = [
            sys.executable,
            str(REPO_ROOT / "scripts" / "detect_flashing.py"),
            "--input", str(test_clip),
            "--output", str(out_json),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert res.returncode == 0
        assert out_json.is_file()

        with out_json.open("r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["trigger"] == "flashing"
        assert data[0]["start"] == pytest.approx(1.0, abs=0.05)
        assert data[0]["end"] == pytest.approx(3.0, abs=0.05)
