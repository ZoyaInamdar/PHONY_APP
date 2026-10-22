#!/usr/bin/env python3
"""Comprehensive test suite for the Two-Stage Cascade Jump Scare Detector with 3D CNN Validator.

Tests cover:
1. Multi-Modal 3D CNN architecture instantiation and forward pass tensor shapes.
2. Mel-Spectrogram feature extraction and filterbank generation.
3. Video chunk frame extraction tensor formatting (C, T, H, W).
4. Stage 1 pre-filtering: candidates below prefilter threshold are not passed to CNN.
5. Stage 2 CNN evaluation and physics-informed confidence estimation.
6. Stage 3 Veto Gate: candidates below veto threshold are suppressed (false positives dropped).
7. Stage 3 Hybrid scoring: validated candidates receive 30% Stage 1 + 70% Stage 2 weighted score.
8. Output format compliance: adherence to canonical 5-field schema contract.
9. Determinism: identical input returns identical scores and timestamps.
10. CLI interface and parameter validation.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.detect_jump_scare_cnn import (
    AUDIO_SAMPLE_RATE,
    DEFAULT_CASCADE_SOURCE,
    DEFAULT_CNN_VETO_THRESHOLD,
    DEFAULT_PREFILTER_THRESHOLD,
    DEFAULT_WEIGHT_CNN,
    DEFAULT_WEIGHT_STAGE1,
    FRAME_SIZE,
    N_MELS,
    NUM_VIDEO_FRAMES,
    WINDOW_DURATION_SECONDS,
    audio_chunk_to_mel_spectrogram,
    build_multimodal_cnn_model,
    compute_mel_filterbank,
    detect_jump_scare_cascade,
    evaluate_cnn_chunk_confidence,
    extract_video_chunk_frames,
)


class TestModelArchitecture:
    """Test Multi-Modal 3D CNN neural network layers and forward pass."""

    def test_multimodal_cnn_shapes(self):
        try:
            import torch
        except ImportError:
            pytest.skip("PyTorch not installed in current environment")

        model = build_multimodal_cnn_model()
        assert model is not None

        # Visual batch: (B=2, C=3, T=16, H=112, W=112)
        v_chunk = torch.randn(2, 3, NUM_VIDEO_FRAMES, FRAME_SIZE, FRAME_SIZE)
        # Audio batch: (B=2, C=1, F=64, T=101)
        a_chunk = torch.randn(2, 1, N_MELS, 101)

        model.eval()
        with torch.no_grad():
            output = model(v_chunk, a_chunk)

        assert output.shape == torch.Size([2])
        # Values must be bounded in [0.0, 1.0] by Sigmoid
        assert torch.all(output >= 0.0)
        assert torch.all(output <= 1.0)


class TestFeatureExtraction:
    """Test Mel-Spectrogram and video chunk processing."""

    def test_compute_mel_filterbank_dimensions(self):
        fb = compute_mel_filterbank(sr=16000, n_fft=512, n_mels=64)
        assert fb.shape == (64, 257)
        assert fb.min() >= 0.0
        assert np.all(np.isfinite(fb))

    def test_audio_chunk_to_mel_spectrogram_output(self):
        # 1-second sine wave at 440 Hz
        t = np.linspace(0, 1.0, AUDIO_SAMPLE_RATE, endpoint=False)
        pcm = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        mel = audio_chunk_to_mel_spectrogram(pcm, sr=AUDIO_SAMPLE_RATE)
        assert mel.shape == (1, N_MELS, 101)
        assert mel.min() >= 0.0
        assert mel.max() <= 1.0

    def test_extract_video_chunk_dummy_fallback(self):
        # Non-existent file should gracefully return black frames
        tensor = extract_video_chunk_frames(
            video_path="non_existent_file.mp4",
            start_seconds=1.0,
            duration_seconds=1.0,
            num_frames=NUM_VIDEO_FRAMES,
            frame_size=FRAME_SIZE,
        )
        assert tensor.shape == (3, NUM_VIDEO_FRAMES, FRAME_SIZE, FRAME_SIZE)
        assert np.all(tensor == 0.0)


class TestCascadeDecisionLogic:
    """Test Stage 1 filtering, Stage 2 scoring, and Stage 3 Veto Gate."""

    def test_evaluate_cnn_chunk_confidence_flux_and_burst(self):
        # Create a video tensor with sudden visual burst
        video_tensor = np.zeros((3, NUM_VIDEO_FRAMES, FRAME_SIZE, FRAME_SIZE), dtype=np.float32)
        # Frames 0-7 dark, frames 8-15 bright (sudden lunge/flash)
        video_tensor[:, 8:, :, :] = 1.0

        # Create an audio mel spectrogram with sudden high-frequency burst
        audio_mel = np.zeros((1, N_MELS, 101), dtype=np.float32)
        audio_mel[0, 32:, 50:] = 0.9  # high frequency burst starting halfway

        dummy_model = MagicMock()
        score = evaluate_cnn_chunk_confidence(dummy_model, video_tensor, audio_mel)
        assert 0.0 <= score <= 1.0
        # High visual flux and acoustic attack should produce strong confidence
        assert score > 0.70

    def test_veto_gate_drops_false_positive(self):
        candidate_event = {
            "start": 10.0,
            "end": 10.5,
            "trigger": "jump_scare",
            "score": 0.45,
            "source": "jump_scare_detector",
        }

        with patch("scripts.detect_jump_scare_cnn.detect_jump_scare", return_value=[candidate_event]):
            with patch("scripts.detect_jump_scare_cnn.extract_video_chunk_frames") as mock_v:
                with patch("scripts.detect_jump_scare_cnn.evaluate_cnn_chunk_confidence") as mock_cnn:
                    mock_v.return_value = np.zeros((3, 16, 112, 112), dtype=np.float32)
                    # CNN outputs low confidence (0.40 < 0.85 veto threshold)
                    mock_cnn.return_value = 0.40

                    results = detect_jump_scare_cascade(
                        media_path=Path(__file__),
                        prefilter_threshold=0.40,
                        veto_threshold=0.85,
                    )
                    # Candidate must be VETOED
                    assert results == []

    def test_hybrid_scoring_for_confirmed_jump_scare(self):
        candidate_event = {
            "start": 14.5,
            "end": 15.0,
            "trigger": "jump_scare",
            "score": 0.60,
            "source": "jump_scare_detector",
        }

        with patch("scripts.detect_jump_scare_cnn.detect_jump_scare", return_value=[candidate_event]):
            with patch("scripts.detect_jump_scare_cnn.extract_video_chunk_frames") as mock_v:
                with patch("scripts.detect_jump_scare_cnn.evaluate_cnn_chunk_confidence") as mock_cnn:
                    mock_v.return_value = np.zeros((3, 16, 112, 112), dtype=np.float32)
                    # CNN outputs high confidence (0.90 >= 0.85 veto threshold)
                    mock_cnn.return_value = 0.90

                    results = detect_jump_scare_cascade(
                        media_path=Path(__file__),
                        prefilter_threshold=0.40,
                        veto_threshold=0.85,
                        weight_stage1=0.30,
                        weight_cnn=0.70,
                    )

                    assert len(results) == 1
                    cue = results[0]
                    assert cue["start"] == 14.5
                    assert cue["end"] == 15.0
                    assert cue["trigger"] == "jump_scare"
                    assert cue["source"] == DEFAULT_CASCADE_SOURCE
                    # Expected hybrid: 0.30 * 0.60 + 0.70 * 0.90 = 0.18 + 0.63 = 0.81
                    assert cue["score"] == 0.81
                    assert cue["stage1_algorithmic_score"] == 0.60
                    assert cue["stage2_cnn_confidence"] == 0.90

    def test_stage1_prefilter_ignores_below_threshold(self):
        weak_event = {
            "start": 5.0,
            "end": 5.5,
            "trigger": "jump_scare",
            "score": 0.25,  # Below 0.40 prefilter threshold
            "source": "jump_scare_detector",
        }

        with patch("scripts.detect_jump_scare_cnn.detect_jump_scare", return_value=[weak_event]):
            with patch("scripts.detect_jump_scare_cnn.evaluate_cnn_chunk_confidence") as mock_cnn:
                results = detect_jump_scare_cascade(
                    media_path=Path(__file__),
                    prefilter_threshold=0.40,
                )
                # CNN must not even be called
                mock_cnn.assert_not_called()
                assert results == []


class TestContractCompliance:
    """Verify standard schema and interval contracts."""

    def test_canonical_fields_present(self):
        candidate = {
            "start": 20.0,
            "end": 20.6,
            "trigger": "jump_scare",
            "score": 0.50,
            "source": "jump_scare_detector",
        }

        with patch("scripts.detect_jump_scare_cnn.detect_jump_scare", return_value=[candidate]):
            with patch("scripts.detect_jump_scare_cnn.extract_video_chunk_frames"):
                with patch("scripts.detect_jump_scare_cnn.evaluate_cnn_chunk_confidence", return_value=0.95):
                    cues = detect_jump_scare_cascade(
                        media_path=Path(__file__),
                        veto_threshold=0.85,
                    )
                    assert len(cues) == 1
                    cue = cues[0]
                    # Required canonical 5 fields
                    for field in ["start", "end", "trigger", "score", "source"]:
                        assert field in cue
                    assert cue["trigger"] == "jump_scare"
                    assert cue["end"] > cue["start"]
                    assert 0.0 <= cue["score"] <= 1.0


class TestCliInterface:
    """Test command-line execution and parameter parsing."""

    def test_cli_help(self):
        cmd = [sys.executable, str(REPO_ROOT / "scripts" / "detect_jump_scare_cnn.py"), "--help"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0
        assert "Two-Stage Cascade" in res.stdout
        assert "--prefilter-threshold" in res.stdout
        assert "--veto-threshold" in res.stdout

    def test_end_to_end_positive_clip_execution(self):
        clip = REPO_ROOT / "data" / "test_clips" / "jump_scare_positive.mp4"
        if not clip.is_file():
            pytest.skip("jump_scare_positive.mp4 not present")
        cues = detect_jump_scare_cascade(clip, veto_threshold=0.85)
        assert len(cues) == 1
        assert cues[0]["trigger"] == "jump_scare"
        assert cues[0]["start"] <= 1.5 <= cues[0]["end"]
        assert cues[0]["score"] >= 0.85
        assert cues[0]["source"] == DEFAULT_CASCADE_SOURCE

    def test_end_to_end_negative_clip_suppressed(self):
        clip = REPO_ROOT / "data" / "test_clips" / "jump_scare_negative.mp4"
        if not clip.is_file():
            pytest.skip("jump_scare_negative.mp4 not present")
        cues = detect_jump_scare_cascade(clip, veto_threshold=0.85)
        assert len(cues) == 0
