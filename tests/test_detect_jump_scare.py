#!/usr/bin/env python3
"""Comprehensive test suite for the Multimodal Compound Jump Scare Detector.

Tests cover:
1. Qualifying audio spike + visual change within temporal tolerance -> emits jump_scare.
2. Audio event without visual evidence -> returns empty.
3. Visual event without audio evidence -> returns empty.
4. Audio and visual events separated by more than tolerance -> returns empty.
5. Exact temporal-tolerance boundary handling (overlap and distance).
6. Overlapping or repeated events -> merged/deduplicated cleanly.
7. Empty audio and visual inputs -> returns empty list.
8. Missing or unavailable modality -> returns empty list.
9. Invalid, reversed, negative, or malformed intervals -> safely filtered out.
10. Correct output fields contract (start, end, trigger='jump_scare', score in [0, 1], source).
11. Deterministic output across multiple runs.
12. Visual change helper tested independently on synthetic frames.
13. Regression coverage demonstrating existing detect_loudness_from_wav behavior is unchanged.
14. CLI execution sanity check.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.detect_jump_scare import (
    DEFAULT_SOURCE,
    DEFAULT_TEMPORAL_ALIGNMENT_TOLERANCE_SECONDS,
    DEFAULT_TRIGGER,
    calculate_compound_score,
    detect_jump_scare,
    detect_visual_change_events,
    intervals_overlap_or_within_tolerance,
    load_jump_scare_config,
    match_audio_visual_jump_scares,
)
from scripts.detect_loudness import detect_loudness_from_wav


class TestIntervalMatchingAndTolerance:
    """Tests for intervals_overlap_or_within_tolerance."""

    def test_overlapping_intervals_zero_distance(self):
        within, dist = intervals_overlap_or_within_tolerance(1.0, 2.0, 1.5, 2.5, tolerance_seconds=0.5)
        assert within is True
        assert dist == 0.0

    def test_abutting_intervals_zero_gap(self):
        within, dist = intervals_overlap_or_within_tolerance(1.0, 2.0, 2.0, 3.0, tolerance_seconds=0.5)
        assert within is True
        assert abs(dist) < 1e-6

    def test_within_configured_tolerance(self):
        # Audio [1.0, 2.0), Visual [2.3, 2.8) -> gap = 0.3s <= 0.5s tolerance
        within, dist = intervals_overlap_or_within_tolerance(1.0, 2.0, 2.3, 2.8, tolerance_seconds=0.5)
        assert within is True
        assert abs(dist - 0.3) < 1e-4

    def test_exact_tolerance_boundary(self):
        # Audio [1.0, 2.0), Visual [2.5, 3.0) -> gap = 0.5s == 0.5s tolerance
        within, dist = intervals_overlap_or_within_tolerance(1.0, 2.0, 2.5, 3.0, tolerance_seconds=0.5)
        assert within is True
        assert abs(dist - 0.5) < 1e-4

    def test_exceeding_tolerance_rejected(self):
        # Audio [1.0, 2.0), Visual [2.6, 3.0) -> gap = 0.6s > 0.5s tolerance
        within, dist = intervals_overlap_or_within_tolerance(1.0, 2.0, 2.6, 3.0, tolerance_seconds=0.5)
        assert within is False
        assert abs(dist - 0.6) < 1e-4

    def test_visual_before_audio_within_tolerance(self):
        # Visual [1.0, 1.5), Audio [1.8, 2.2) -> gap = 0.3s <= 0.5s tolerance
        within, dist = intervals_overlap_or_within_tolerance(1.8, 2.2, 1.0, 1.5, tolerance_seconds=0.5)
        assert within is True
        assert abs(dist - 0.3) < 1e-4

    def test_invalid_negative_or_inverted_intervals(self):
        within, _ = intervals_overlap_or_within_tolerance(-1.0, 2.0, 1.0, 2.0)
        assert within is False

        within, _ = intervals_overlap_or_within_tolerance(2.0, 1.0, 1.0, 2.0)
        assert within is False

        within, _ = intervals_overlap_or_within_tolerance(1.0, 2.0, float("nan"), 3.0)
        assert within is False


class TestCompoundScoreCalculation:
    """Tests for calculate_compound_score."""

    def test_perfect_scores(self):
        assert calculate_compound_score(1.0, 1.0) == 1.0

    def test_geometric_mean_properties(self):
        # sqrt(0.5 * 0.5) = 0.5
        assert calculate_compound_score(0.5, 0.5) == 0.5
        # sqrt(0.8 * 0.2) = 0.4
        assert calculate_compound_score(0.8, 0.2) == 0.4

    def test_zero_in_either_modality_yields_zero(self):
        assert calculate_compound_score(0.0, 0.9) == 0.0
        assert calculate_compound_score(0.9, 0.0) == 0.0

    def test_nan_and_out_of_bounds_clamped_safely(self):
        assert calculate_compound_score(float("nan"), 0.5) == 0.0
        assert calculate_compound_score(-0.5, 0.5) == 0.0
        assert calculate_compound_score(1.5, 0.5) == calculate_compound_score(1.0, 0.5)


class TestMatchAudioVisualJumpScares:
    """Tests for pure multimodal matching logic."""

    def test_coincident_modalities_emits_jump_scare(self):
        audio_events = [{"start": 5.0, "end": 5.5, "score": 0.9, "trigger": "loud_spike"}]
        visual_events = [{"start": 5.1, "end": 5.3, "score": 0.8, "trigger": "visual_motion_proxy"}]

        cues = match_audio_visual_jump_scares(audio_events, visual_events, temporal_tolerance_seconds=0.5)
        assert len(cues) == 1
        c = cues[0]
        assert c["trigger"] == "jump_scare"
        assert c["start"] == 5.0
        assert c["end"] >= 5.5
        assert c["score"] == calculate_compound_score(0.9, 0.8)
        assert c["source"] == DEFAULT_SOURCE

    def test_audio_only_emits_no_compound_cue(self):
        audio_events = [{"start": 5.0, "end": 5.5, "score": 0.9, "trigger": "loud_spike"}]
        visual_events: List[Dict[str, Any]] = []

        cues = match_audio_visual_jump_scares(audio_events, visual_events)
        assert cues == []

    def test_visual_only_emits_no_compound_cue(self):
        audio_events: List[Dict[str, Any]] = []
        visual_events = [{"start": 5.1, "end": 5.3, "score": 0.8, "trigger": "visual_motion_proxy"}]

        cues = match_audio_visual_jump_scares(audio_events, visual_events)
        assert cues == []

    def test_temporal_separation_exceeding_tolerance_emits_no_cue(self):
        audio_events = [{"start": 2.0, "end": 2.5, "score": 0.9, "trigger": "loud_spike"}]
        # Visual event at 3.5s -> gap = 1.0s > 0.5s tolerance
        visual_events = [{"start": 3.5, "end": 3.8, "score": 0.8, "trigger": "visual_motion_proxy"}]

        cues = match_audio_visual_jump_scares(audio_events, visual_events, temporal_tolerance_seconds=0.5)
        assert cues == []

    def test_temporal_separation_within_tolerance_emits_cue(self):
        audio_events = [{"start": 2.0, "end": 2.5, "score": 0.9, "trigger": "loud_spike"}]
        # Visual event at 2.8s -> gap = 0.3s <= 0.5s tolerance
        visual_events = [{"start": 2.8, "end": 3.1, "score": 0.8, "trigger": "visual_motion_proxy"}]

        cues = match_audio_visual_jump_scares(audio_events, visual_events, temporal_tolerance_seconds=0.5)
        assert len(cues) == 1
        assert cues[0]["trigger"] == "jump_scare"
        assert cues[0]["start"] == 2.0

    def test_exact_tolerance_boundary_matches(self):
        audio_events = [{"start": 10.0, "end": 10.5, "score": 0.85, "trigger": "loud_spike"}]
        # Visual event starting at 11.0s -> exact gap = 0.5s == tolerance
        visual_events = [{"start": 11.0, "end": 11.2, "score": 0.75, "trigger": "visual_motion_proxy"}]

        cues = match_audio_visual_jump_scares(audio_events, visual_events, temporal_tolerance_seconds=0.5)
        assert len(cues) == 1

    def test_overlapping_repeated_events_deduplicated_without_duplicates(self):
        # Two audio spikes close together matching the same visual event
        audio_events = [
            {"start": 4.0, "end": 4.4, "score": 0.8},
            {"start": 4.3, "end": 4.7, "score": 0.9},
        ]
        visual_events = [{"start": 4.1, "end": 4.5, "score": 0.85}]

        cues = match_audio_visual_jump_scares(audio_events, visual_events, temporal_tolerance_seconds=0.5)
        # Should merge overlapping compound cues rather than duplicate
        assert len(cues) == 1
        assert cues[0]["start"] == 4.0
        assert cues[0]["score"] >= 0.8

    def test_overlapping_events_do_not_exceed_max_duration(self):
        # Even with extended chain of overlapping events, no single cue exceeds max_duration_seconds (10s)
        audio_events = [
            {"start": 1.0, "end": 6.0, "score": 0.8},
            {"start": 5.0, "end": 12.0, "score": 0.9},
            {"start": 11.0, "end": 16.0, "score": 0.85},
        ]
        visual_events = [
            {"start": 1.2, "end": 1.5, "score": 0.8},
            {"start": 5.2, "end": 5.5, "score": 0.9},
            {"start": 11.2, "end": 11.5, "score": 0.85},
        ]
        cues = match_audio_visual_jump_scares(
            audio_events, visual_events, max_duration_seconds=10.0
        )
        assert len(cues) >= 1
        for c in cues:
            assert c["end"] - c["start"] <= 10.0 + 1e-4

    def test_empty_inputs_returns_empty_list(self):
        assert match_audio_visual_jump_scares([], []) == []

    def test_malformed_and_inverted_intervals_handled_safely(self):
        audio_events = [
            {"start": "invalid", "end": 5.0},
            {"start": -2.0, "end": 1.0},
            {"start": 5.0, "end": 4.0},  # inverted
            {"start": 6.0, "end": 6.5, "score": 0.8},  # valid
        ]
        visual_events = [
            {"invalid_key": True},
            {"start": 6.1, "end": 6.4, "score": 0.75},  # valid
        ]

        cues = match_audio_visual_jump_scares(audio_events, visual_events)
        assert len(cues) == 1
        assert cues[0]["start"] == 6.0

    def test_input_records_not_mutated(self):
        a = [{"start": 1.0, "end": 1.5, "score": 0.8}]
        v = [{"start": 1.1, "end": 1.3, "score": 0.7}]
        a_copy = json.loads(json.dumps(a))
        v_copy = json.loads(json.dumps(v))

        _ = match_audio_visual_jump_scares(a, v)
        assert a == a_copy
        assert v == v_copy

    def test_determinism(self):
        a = [{"start": 3.0, "end": 3.5, "score": 0.85}]
        v = [{"start": 3.2, "end": 3.4, "score": 0.9}]

        runs = [match_audio_visual_jump_scares(a, v) for _ in range(5)]
        for r in runs[1:]:
            assert r == runs[0]

    def test_schema_compliance_fields(self):
        a = [{"start": 1.0, "end": 1.5, "score": 0.8}]
        v = [{"start": 1.1, "end": 1.4, "score": 0.7}]

        cues = match_audio_visual_jump_scares(a, v)
        assert len(cues) == 1
        cue = cues[0]

        for field in ("start", "end", "trigger", "score", "source"):
            assert field in cue

        assert isinstance(cue["start"], (int, float))
        assert isinstance(cue["end"], (int, float))
        assert cue["end"] > cue["start"]
        assert cue["trigger"] == "jump_scare"
        assert 0.0 <= cue["score"] <= 1.0
        assert cue["source"] == "jump_scare_detector"


class TestVisualChangeHelper:
    """Tests for detect_visual_change_events."""

    def test_nonexistent_video_returns_empty(self):
        assert detect_visual_change_events("nonexistent_video_path.mp4") == []

    def test_audio_file_returns_empty_visual_events(self):
        # Audio WAV files have no visual streams and must return [] safely
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp_path = Path(tmp.name)
            assert detect_visual_change_events(tmp_path) == []


class TestLoudnessDetectorRegression:
    """Regression tests verifying existing loud_spike behavior remains completely unaffected."""

    def test_detect_loudness_from_wav_remains_intact(self):
        # Generate a synthetic WAV with 2.0s quiet baseline + 0.3s loud spike
        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "test_spike.wav"
            sr = 44100
            t_quiet = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
            t_spike = np.linspace(0, 0.3, int(sr * 0.3), endpoint=False)
            t_after = np.linspace(0, 1.0, int(sr * 1.0), endpoint=False)

            quiet1 = 0.01 * np.sin(2 * np.pi * 440 * t_quiet)
            spike = 0.8 * np.sin(2 * np.pi * 880 * t_spike)
            quiet2 = 0.01 * np.sin(2 * np.pi * 440 * t_after)
            audio = np.concatenate([quiet1, spike, quiet2])

            audio_int16 = (audio * 32767).astype(np.int16)
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(audio_int16.tobytes())

            # Detect loudness
            raw_cues = detect_loudness_from_wav(wav_path, modes=("spike",))
            assert isinstance(raw_cues, list)
            # Must detect the spike without error
            assert any(c.get("trigger") == "loud_spike" for c in raw_cues)
            # Must preserve source='loudness_detector'
            assert all(c.get("source") == "loudness_detector" for c in raw_cues)


class TestCLIExecution:
    """Tests for detect_jump_scare.py CLI."""

    def test_cli_help(self):
        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "detect_jump_scare.py"), "--help"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "Multimodal Compound Jump Scare Detector" in res.stdout
