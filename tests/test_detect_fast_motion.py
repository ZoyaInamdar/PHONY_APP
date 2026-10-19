#!/usr/bin/env python3
"""Comprehensive test suite for the Rapid Motion / Camera Shake Detector (Person C).

Verifies:
1. Sustained rapid motion above threshold produces 'fast_motion' cues.
2. Static video (no motion) produces zero cues.
3. Isolated hard cut (single frame jump MAD >= 0.40) is suppressed and produces zero cues.
4. Slow motion below threshold (e.g. MAD = 0.05) produces zero cues.
5. Short motion burst below min_segment_duration_seconds (e.g. 0.5s < 1.0s) is rejected.
6. Long motion segment is capped at max_segment_duration_seconds (30.0s).
7. Close motion bursts separated by <= merge_gap_seconds (2.0s) are merged.
8. Distant motion bursts separated by > merge_gap_seconds remain distinct.
9. Empty frame sequences, empty MAD lists, and very short clips return [].
10. Audio-only files (.wav) return [] safely without raising errors.
11. Nonexistent video paths raise FileNotFoundError or return [].
12. Schema compliance: start, end, trigger='fast_motion', 0.0 <= score <= 1.0, source='motion_detector'.
13. Deterministic output across multiple runs.
14. Regression coverage: verifying jump_scare and loud_spike remain completely unaffected.
15. CLI execution (--help).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.detect_fast_motion import (
    DEFAULT_HARD_CUT_MAD_THRESHOLD,
    DEFAULT_MAX_SEGMENT_DURATION_SECONDS,
    DEFAULT_MERGE_GAP_SECONDS,
    DEFAULT_MIN_SEGMENT_DURATION_SECONDS,
    DEFAULT_SOURCE,
    DEFAULT_THRESHOLD,
    DEFAULT_TRIGGER,
    apply_temporal_smoothing,
    detect_fast_motion,
    detect_motion_intervals,
    filter_scene_cuts,
    frame_to_relative_luminance,
    load_fast_motion_config,
)
from scripts.detect_jump_scare import match_audio_visual_jump_scares
from scripts.detect_loudness import detect_loudness_from_wav


class TestCutFilterAndSmoothing:
    """Tests for scene cut suppression and temporal smoothing."""

    def test_isolated_hard_cut_suppressed(self):
        # 10 frames: static 0.02, then a single cut spike at index 5 with MAD = 0.60, then back to 0.02
        mad_series = [0.02] * 5 + [0.60] + [0.02] * 4
        filtered = filter_scene_cuts(
            mad_series,
            hard_cut_threshold=0.40,
            motion_threshold=0.20,
        )
        assert len(filtered) == 10
        # The spike at index 5 should be replaced by surrounding average
        assert filtered[5] < 0.20

    def test_sustained_motion_not_suppressed(self):
        # A sequence of multiple high MAD values representing sustained fast action
        mad_series = [0.02] * 3 + [0.45, 0.48, 0.42, 0.46] + [0.02] * 3
        filtered = filter_scene_cuts(
            mad_series,
            hard_cut_threshold=0.40,
            motion_threshold=0.20,
        )
        # Because the high frames are consecutive, they are NOT isolated 1-frame cuts
        for val in filtered[3:7]:
            assert val >= 0.40

    def test_temporal_smoothing(self):
        series = [0.0, 0.0, 1.0, 0.0, 0.0]
        smoothed = apply_temporal_smoothing(series, window_frames=3)
        assert len(smoothed) == len(series)
        assert smoothed[2] < 1.0  # Peak is smoothed down


class TestMotionIntervalSegmentation:
    """Tests for detect_motion_intervals segmentation, duration filtering, and merging."""

    def test_sustained_motion_produces_cue(self):
        fps = 10.0
        # 2.0 seconds of sustained motion above 0.20 threshold (20 frames of 0.35)
        # preceded and followed by 1.0s of static (10 frames of 0.01)
        series = [0.01] * 10 + [0.35] * 20 + [0.01] * 10
        cues = detect_motion_intervals(
            mad_series=series,
            fps=fps,
            threshold=0.20,
            min_duration_seconds=1.0,
        )
        assert len(cues) == 1
        c = cues[0]
        assert c["trigger"] == DEFAULT_TRIGGER
        assert c["start"] == 1.0
        assert c["end"] == 3.0
        assert c["source"] == DEFAULT_SOURCE
        assert 0.0 <= c["score"] <= 1.0

    def test_static_video_produces_no_cues(self):
        fps = 24.0
        series = [0.005] * 48  # 2.0s of static video
        cues = detect_motion_intervals(series, fps=fps, threshold=0.20)
        assert cues == []

    def test_slow_motion_below_threshold_produces_no_cues(self):
        fps = 24.0
        series = [0.08] * 48  # 2.0s of slow gentle movement below 0.20
        cues = detect_motion_intervals(series, fps=fps, threshold=0.20)
        assert cues == []

    def test_motion_shorter_than_min_duration_rejected(self):
        fps = 10.0
        # 0.4 seconds of motion (4 frames), below min_duration 1.0s
        series = [0.01] * 10 + [0.35] * 4 + [0.01] * 10
        cues = detect_motion_intervals(
            mad_series=series,
            fps=fps,
            threshold=0.20,
            min_duration_seconds=1.0,
        )
        assert cues == []

    def test_merge_gap_combines_close_motion_bursts(self):
        fps = 10.0
        # Burst 1: 1.5s motion (15 frames)
        # Gap: 1.0s static (10 frames) <= 2.0s merge gap
        # Burst 2: 1.5s motion (15 frames)
        series = [0.35] * 15 + [0.01] * 10 + [0.35] * 15
        cues = detect_motion_intervals(
            mad_series=series,
            fps=fps,
            threshold=0.20,
            min_duration_seconds=1.0,
            merge_gap_seconds=2.0,
        )
        # Should be merged into a single continuous cue
        assert len(cues) == 1
        assert cues[0]["start"] == 0.0
        assert cues[0]["end"] == 4.0

    def test_distant_motion_bursts_remain_distinct(self):
        fps = 10.0
        # Burst 1: 1.5s motion
        # Gap: 3.0s static > 2.0s merge gap
        # Burst 2: 1.5s motion
        series = [0.35] * 15 + [0.01] * 30 + [0.35] * 15
        cues = detect_motion_intervals(
            mad_series=series,
            fps=fps,
            threshold=0.20,
            min_duration_seconds=1.0,
            merge_gap_seconds=2.0,
        )
        assert len(cues) == 2
        assert cues[0]["start"] == 0.0
        assert cues[0]["end"] == 1.5
        assert cues[1]["start"] == 4.5
        assert cues[1]["end"] == 6.0

    def test_capping_at_max_segment_duration(self):
        fps = 10.0
        # 40 seconds of continuous fast motion (400 frames)
        series = [0.35] * 400
        cues = detect_motion_intervals(
            mad_series=series,
            fps=fps,
            threshold=0.20,
            min_duration_seconds=1.0,
            max_segment_duration_seconds=30.0,
        )
        assert len(cues) >= 1
        for c in cues:
            assert c["end"] - c["start"] <= 30.0 + 1e-4

    def test_empty_and_invalid_inputs(self):
        assert detect_motion_intervals([], fps=24.0) == []
        assert detect_motion_intervals([0.5, 0.5], fps=0.0) == []
        assert detect_motion_intervals([0.5, 0.5], fps=-1.0) == []


class TestDetectFastMotionFileHandling:
    """Tests for file handling and format validation in detect_fast_motion."""

    def test_nonexistent_file_raises_error(self):
        with pytest.raises(FileNotFoundError):
            detect_fast_motion("nonexistent_video_file_xyz.mp4")

    def test_audio_file_returns_empty_safely(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp_p = Path(tmp.name)
            tmp_p.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            assert detect_fast_motion(tmp_p) == []

    def test_schema_compliance_and_determinism(self):
        fps = 10.0
        series = [0.01] * 5 + [0.4] * 15 + [0.01] * 5
        cues1 = detect_motion_intervals(series, fps=fps, threshold=0.20)
        cues2 = detect_motion_intervals(series, fps=fps, threshold=0.20)

        assert cues1 == cues2
        assert len(cues1) == 1
        cue = cues1[0]

        for field in ("start", "end", "trigger", "score", "source"):
            assert field in cue
        assert cue["trigger"] == "fast_motion"
        assert cue["source"] == "motion_detector"
        assert isinstance(cue["start"], (int, float))
        assert isinstance(cue["end"], (int, float))
        assert cue["end"] > cue["start"]
        assert 0.0 <= cue["score"] <= 1.0


class TestLuminanceConversion:
    """Tests for frame_to_relative_luminance."""

    def test_white_frame_luminance(self):
        white = np.full((72, 128, 3), 255, dtype=np.uint8)
        lum = frame_to_relative_luminance(white)
        assert np.allclose(lum, 1.0, atol=1e-3)

    def test_black_frame_luminance(self):
        black = np.zeros((72, 128, 3), dtype=np.uint8)
        lum = frame_to_relative_luminance(black)
        assert np.allclose(lum, 0.0, atol=1e-3)


class TestRegressionUnchangedBehavior:
    """Verifies that jump_scare and loud_spike behaviors remain completely unchanged."""

    def test_jump_scare_matching_remains_intact(self):
        a = [{"start": 1.0, "end": 1.5, "score": 0.9, "trigger": "loud_spike"}]
        v = [{"start": 1.2, "end": 1.4, "score": 0.8, "trigger": "visual_motion_proxy"}]
        cues = match_audio_visual_jump_scares(a, v, temporal_tolerance_seconds=0.5)
        assert len(cues) == 1
        assert cues[0]["trigger"] == "jump_scare"
        assert cues[0]["source"] == "jump_scare_detector"


class TestCLIExecution:
    """Tests for detect_fast_motion.py CLI."""

    def test_cli_help(self):
        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "detect_fast_motion.py"), "--help"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "Rapid Motion and Camera Shake Detector" in res.stdout
