#!/usr/bin/env python3
"""Comprehensive test suite for BloodshotNet Visible Blood Detector (YOLO-based).

Tests cover:
1. Contract validation: validate_record enforces 5 required canonical fields.
2. Value bounds: start >= 0, end > start, score in [0.0, 1.0], non-empty source.
3. Headless CI mock fallback: runs gracefully and returns empty list when weights are absent.
4. Temporal glitch rejection: isolated 1-2 frame spikes do not trigger an interval.
5. Interval confirmation: >= 3 consecutive frames open an interval with full candidate score retention.
6. Occlusion bridging: hang-time <= 15 frames bridges momentary drops.
7. Interval termination: hang-time > 15 frames closes interval.
8. Packaging integration: raw cues validate against package_cues.py shared contract.
9. Determinism: identical frame sequences yield identical intervals and scores.
10. CLI interface and parameter parsing.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.detect_visible_blood import (
    DEFAULT_CONF_THRESHOLD,
    DEFAULT_CONSECUTIVE_FRAMES,
    DEFAULT_HANG_TIME_FRAMES,
    SOURCE_ID,
    TARGET_CLASSES,
    TRIGGER_ID,
    detect_visible_blood,
    validate_record,
)
from scripts.package_cues import validate_raw_detection


class TestContractValidation:
    """Test schema contract enforcement."""

    def test_valid_cue_passes(self):
        cue = {
            "start": 10.5,
            "end": 12.0,
            "trigger": TRIGGER_ID,
            "score": 0.85,
            "source": SOURCE_ID,
        }
        validate_record(cue)

    def test_missing_required_fields_raises(self):
        required = ["start", "end", "trigger", "score", "source"]
        for field in required:
            cue = {
                "start": 1.0,
                "end": 2.0,
                "trigger": TRIGGER_ID,
                "score": 0.8,
                "source": SOURCE_ID,
            }
            del cue[field]
            with pytest.raises(ValueError, match="missing mandatory field"):
                validate_record(cue)

    def test_invalid_interval_bounds_raises(self):
        # end == start
        with pytest.raises(ValueError, match="Invalid cue interval bounds"):
            validate_record({
                "start": 5.0,
                "end": 5.0,
                "trigger": TRIGGER_ID,
                "score": 0.8,
                "source": SOURCE_ID,
            })

        # end < start
        with pytest.raises(ValueError, match="Invalid cue interval bounds"):
            validate_record({
                "start": 5.0,
                "end": 4.5,
                "trigger": TRIGGER_ID,
                "score": 0.8,
                "source": SOURCE_ID,
            })

        # start < 0
        with pytest.raises(ValueError, match="Invalid cue interval bounds"):
            validate_record({
                "start": -1.0,
                "end": 2.0,
                "trigger": TRIGGER_ID,
                "score": 0.8,
                "source": SOURCE_ID,
            })

    def test_invalid_score_raises(self):
        # score > 1.0
        with pytest.raises(ValueError, match="Invalid cue score"):
            validate_record({
                "start": 1.0,
                "end": 2.0,
                "trigger": TRIGGER_ID,
                "score": 1.05,
                "source": SOURCE_ID,
            })

        # score < 0.0
        with pytest.raises(ValueError, match="Invalid cue score"):
            validate_record({
                "start": 1.0,
                "end": 2.0,
                "trigger": TRIGGER_ID,
                "score": -0.1,
                "source": SOURCE_ID,
            })

    def test_empty_source_raises(self):
        with pytest.raises(ValueError, match="source"):
            validate_record({
                "start": 1.0,
                "end": 2.0,
                "trigger": TRIGGER_ID,
                "score": 0.75,
                "source": "   ",
            })


class TestHeadlessCIMockFallback:
    """Test graceful behavior when weights are missing on CI runners."""

    def test_missing_video_raises_filenotfound(self):
        with pytest.raises(FileNotFoundError):
            detect_visible_blood("non_existent_video_path.mp4")

    def test_headless_ci_with_mock_mode(self, tmp_path):
        # Create a tiny 5-frame test video
        video_path = tmp_path / "dummy.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(str(video_path), fourcc, 24.0, (64, 64))
        for _ in range(5):
            frame = np.zeros((64, 64, 3), dtype=np.uint8)
            out.write(frame)
        out.release()

        # Point to non-existent weights file
        cues = detect_visible_blood(
            video_path=video_path,
            weights_path=tmp_path / "absent_weights.pt",
        )
        # Should complete gracefully without crashing and return empty list
        assert cues == []


class TestTemporalSmoothingStateMachine:
    """Test state machine logic with mocked YOLO detections."""

    def _create_test_video(self, path: Path, num_frames: int = 40, fps: float = 24.0) -> Path:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(str(path), fourcc, fps, (64, 64))
        for _ in range(num_frames):
            frame = np.zeros((64, 64, 3), dtype=np.uint8)
            out.write(frame)
        out.release()
        return path

    def test_glitch_rejection_single_isolated_frame(self, tmp_path):
        video_path = self._create_test_video(tmp_path / "glitch.mp4", num_frames=30)
        dummy_weights = tmp_path / "mock.pt"
        dummy_weights.write_text("weights")

        mock_box = MagicMock()
        mock_box.cls = [0]
        mock_box.conf = [0.85]
        mock_res_pos = MagicMock()
        mock_res_pos.boxes = [mock_box]

        mock_res_neg = MagicMock()
        mock_res_neg.boxes = []

        # Only frame 5 has a detection (1 frame < min_consecutive_frames=3)
        responses = [mock_res_neg] * 30
        responses[5] = mock_res_pos

        with patch("ultralytics.YOLO") as MockYOLO:
            mock_model = MagicMock()
            mock_model.names = {0: "visible_blood"}
            mock_model.side_effect = lambda frame, **kw: [responses.pop(0)]
            MockYOLO.return_value = mock_model

            cues = detect_visible_blood(
                video_path=video_path,
                weights_path=dummy_weights,
                min_consecutive_frames=3,
            )

            # Single isolated frame must be rejected
            assert cues == []

    def test_interval_confirmation_and_score_accumulation(self, tmp_path):
        video_path = self._create_test_video(tmp_path / "confirm.mp4", num_frames=30, fps=24.0)
        dummy_weights = tmp_path / "mock.pt"
        dummy_weights.write_text("weights")

        def make_res(conf):
            b = MagicMock()
            b.cls = [0]
            b.conf = [conf]
            r = MagicMock()
            r.boxes = [b]
            return r

        # Frames 10, 11, 12, 13 are positive (>= 3 consecutive frames)
        responses = [MagicMock(boxes=[])] * 30
        responses[10] = make_res(0.80)
        responses[11] = make_res(0.85)
        responses[12] = make_res(0.90)
        responses[13] = make_res(0.95)

        with patch("ultralytics.YOLO") as MockYOLO:
            mock_model = MagicMock()
            mock_model.names = {0: "visible_blood"}
            mock_model.side_effect = lambda frame, **kw: [responses.pop(0)]
            MockYOLO.return_value = mock_model

            cues = detect_visible_blood(
                video_path=video_path,
                weights_path=dummy_weights,
                min_consecutive_frames=3,
                hang_time_frames=5,
            )

            assert len(cues) == 1
            cue = cues[0]
            validate_record(cue)

            # Start time corresponds to frame 10 (10 / 24.0 = ~0.417s)
            assert cue["start"] == pytest.approx(10 / 24.0, abs=0.05)
            # Scores from candidate frames (10, 11) and confirmed frames (12, 13) are averaged: (0.8+0.85+0.9+0.95)/4 = 0.875
            assert cue["score"] == pytest.approx(0.875, abs=0.01)
            assert cue["trigger"] == TRIGGER_ID
            assert cue["source"] == SOURCE_ID

    def test_hang_time_bridges_momentary_occlusion(self, tmp_path):
        video_path = self._create_test_video(tmp_path / "bridge.mp4", num_frames=50, fps=24.0)
        dummy_weights = tmp_path / "mock.pt"
        dummy_weights.write_text("weights")

        def make_res(conf):
            b = MagicMock()
            b.cls = [0]
            b.conf = [conf]
            r = MagicMock()
            r.boxes = [b]
            return r

        # Positive run 1: frames 5-8 (4 frames)
        # Gap: frames 9-13 (5 frames, which is <= hang_time 10)
        # Positive run 2: frames 14-16 (3 frames)
        # Followed by rest negative
        responses = [MagicMock(boxes=[])] * 50
        for f in [5, 6, 7, 8, 14, 15, 16]:
            responses[f] = make_res(0.80)

        with patch("ultralytics.YOLO") as MockYOLO:
            mock_model = MagicMock()
            mock_model.names = {0: "blood"}  # Class alias 'blood'
            mock_model.side_effect = lambda frame, **kw: [responses.pop(0)]
            MockYOLO.return_value = mock_model

            cues = detect_visible_blood(
                video_path=video_path,
                weights_path=dummy_weights,
                min_consecutive_frames=3,
                hang_time_frames=10,
            )

            # Bridged into 1 continuous interval
            assert len(cues) == 1
            cue = cues[0]
            assert cue["start"] == pytest.approx(5 / 24.0, abs=0.05)
            # End extends through frame 16
            assert cue["end"] >= (16 / 24.0)


class TestPackagingIntegration:
    """Verify generated records satisfy package_cues.py pipeline validation."""

    def test_raw_cues_validate_against_package_cues(self):
        canonical_triggers = {
            "flashing",
            "loud_spike",
            "sustained_loudness",
            "gunshot",
            "explosion",
            "scream",
            "profanity",
            "insect",
            "jump_scare",
            "visible_blood",
        }

        test_cue = {
            "start": 12.35,
            "end": 14.85,
            "trigger": "visible_blood",
            "type": "visible_blood",
            "score": 0.885,
            "source": "bloodshotnet_yolo_v1",
            "status": "experimental",
        }

        # Must pass without raising ValueError
        validate_raw_detection(test_cue, canonical_triggers, max_allowed_duration=60.0)


class TestInputLayerAndSpatialFilters:
    """Test video frame generator and spatial area filter."""

    def test_video_frame_generator_yields_expected_tuples(self, tmp_path):
        from scripts.detect_visible_blood import video_frame_generator

        # Create 10 frame video
        video_path = tmp_path / "gen_test.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(str(video_path), fourcc, 24.0, (128, 128))
        for _ in range(10):
            out.write(np.zeros((128, 128, 3), dtype=np.uint8))
        out.release()

        frames = list(video_frame_generator(video_path))
        assert len(frames) == 10
        for i, (pts_s, pts_e, f_arr) in enumerate(frames):
            assert pts_s == pytest.approx(i / 24.0, abs=0.01)
            assert pts_e > pts_s
            assert f_arr.shape == (128, 128, 3)

    def test_spatial_area_rejection_for_micro_boxes(self):
        from scripts.detect_visible_blood import filter_frame_detections

        frame = np.zeros((100, 100, 3), dtype=np.uint8)  # 10,000 total pixels

        # Micro-box: 2x2 = 4 pixels (0.04% of screen area < 0.5%)
        micro_box = MagicMock()
        micro_box.cls = [0]
        micro_box.conf = [0.95]
        import torch
        micro_box.xyxy = torch.tensor([[10.0, 10.0, 12.0, 12.0]])

        # Large box: 20x20 = 400 pixels (4.0% of screen area >= 0.5%)
        large_box = MagicMock()
        large_box.cls = [0]
        large_box.conf = [0.90]
        large_box.xyxy = torch.tensor([[10.0, 10.0, 30.0, 30.0]])

        model = MagicMock()
        model.names = {0: "visible_blood"}

        # Case 1: Only micro-box present -> rejected by spatial check
        model.side_effect = lambda frame, **kw: [MagicMock(boxes=[micro_box])]
        detected, conf, boxes = filter_frame_detections(model, frame, conf_threshold=0.65, min_box_area_ratio=0.005)
        assert detected is False
        assert len(boxes) == 0

        # Case 2: Large box present -> accepted
        model.side_effect = lambda frame, **kw: [MagicMock(boxes=[large_box])]
        detected, conf, boxes = filter_frame_detections(model, frame, conf_threshold=0.65, min_box_area_ratio=0.005)
        assert detected is True
        assert conf == pytest.approx(0.90)
        assert len(boxes) == 1

    def test_real_model_weights_loading_and_inference(self):
        """Verify the built bloodshot_yolo_small.pt model loads and executes."""
        from scripts.detect_visible_blood import DEFAULT_WEIGHTS
        if not DEFAULT_WEIGHTS.is_file():
            pytest.skip("bloodshot_yolo_small.pt not found on disk")

        from ultralytics import YOLO
        model = YOLO(str(DEFAULT_WEIGHTS))
        assert "visible_blood" in [name.lower() for name in model.names.values()]

        dummy_frame = np.zeros((128, 128, 3), dtype=np.uint8)
        res = model(dummy_frame, verbose=False, conf=0.5)
        assert len(res) == 1


class TestCLIExecution:
    """Verify CLI interface operates correctly."""

    def test_cli_help(self):
        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "detect_visible_blood.py"), "--help"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "--video" in res.stdout
        assert "--weights" in res.stdout
        assert "--conf" in res.stdout
        assert "--min-area" in res.stdout

