"""Unit tests for Audio-Event Classifier (scripts/classify_audio_events.py).

Verifies Person A audio classifier requirements:
1. Label mapping for gunshot, explosion, and scream across candidate backends (YAMNet, AST, PANNs).
2. Confidence threshold filtering against config-specified thresholds (gunshot=0.65, explosion=0.70, scream=0.60).
3. Windowing and timestamp conversion matching cue schema ([start, end), score in [0,1]).
4. Score aggregation (max mapped class probability).
5. Handling of empty or sub-threshold predictions.
6. Real YAMNet ONNX inference execution on benchmark audio clip.
"""

from pathlib import Path
import pytest

from scripts.classify_audio_events import (
    BACKEND_AST,
    BACKEND_PANNS,
    BACKEND_YAMNET,
    DEFAULT_SOURCE,
    classify_audio_events,
    get_class_to_trigger_mapping,
    get_trigger_threshold,
    map_model_predictions_to_cues,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestAudioClassifierMapping:
    """Tests for AudioSet class mapping across backends."""

    def test_yamnet_label_mappings(self):
        """YAMNet class labels correctly map to canonical project triggers."""
        mapping = get_class_to_trigger_mapping(backend=BACKEND_YAMNET)
        assert mapping["gunshot, gunfire"] == "gunshot"
        assert mapping["machine gun"] == "gunshot"
        assert mapping["cap gun"] == "gunshot"
        assert mapping["explosion"] == "explosion"
        assert mapping["burst, pop"] == "explosion"
        assert mapping["boom"] == "explosion"
        assert mapping["screaming"] == "scream"
        assert mapping["shout"] == "scream"
        assert mapping["yell"] == "scream"

    def test_ast_label_mappings(self):
        """AST class labels correctly map to canonical project triggers."""
        mapping = get_class_to_trigger_mapping(backend=BACKEND_AST)
        assert mapping["gunshot, gunfire"] == "gunshot"
        assert mapping["artillery fire"] == "gunshot"
        assert mapping["explosion"] == "explosion"
        assert mapping["screaming"] == "scream"

    def test_panns_label_mappings(self):
        """PANNs class labels correctly map to canonical project triggers."""
        mapping = get_class_to_trigger_mapping(backend=BACKEND_PANNS)
        assert mapping["gunshot, gunfire"] == "gunshot"
        assert mapping["explosion"] == "explosion"
        assert mapping["screaming"] == "scream"


class TestAudioPredictionMappingAndThresholds:
    """Tests for threshold filtering, timestamps, and cue formatting."""

    def test_above_threshold_produces_cues(self):
        """Model predictions exceeding thresholds produce valid cues."""
        raw_windows = [
            {
                "start": 1.0,
                "end": 2.5,
                "class_probabilities": {
                    "Gunshot, gunfire": 0.82,  # > 0.65 threshold
                    "Speech": 0.15,
                },
            },
            {
                "start": 3.0,
                "end": 4.5,
                "class_probabilities": {
                    "Screaming": 0.74,  # > 0.60 threshold
                },
            },
        ]

        cues = map_model_predictions_to_cues(raw_windows, backend=BACKEND_YAMNET)
        assert len(cues) == 2
        assert cues[0]["trigger"] == "gunshot"
        assert cues[0]["score"] == 0.82
        assert cues[0]["start"] == 1.0
        assert cues[0]["end"] == 2.5
        assert cues[0]["source"] == DEFAULT_SOURCE

        assert cues[1]["trigger"] == "scream"
        assert cues[1]["score"] == 0.74
        assert cues[1]["start"] == 3.0
        assert cues[1]["end"] == 4.5

    def test_below_threshold_filtered_out(self):
        """Model predictions below configured thresholds are filtered out."""
        raw_windows = [
            {
                "start": 0.0,
                "end": 1.5,
                "class_probabilities": {
                    "Gunshot, gunfire": 0.50,  # Below 0.65 threshold
                    "Explosion": 0.60,         # Below 0.70 threshold
                    "Screaming": 0.55,         # Below 0.60 threshold
                },
            }
        ]

        cues = map_model_predictions_to_cues(raw_windows, backend=BACKEND_YAMNET)
        assert cues == []

    def test_score_aggregation_takes_max_probability(self):
        """When multiple mapped labels for the same trigger occur, score takes max."""
        raw_windows = [
            {
                "start": 1.0,
                "end": 2.5,
                "class_probabilities": {
                    "Gunshot, gunfire": 0.72,
                    "Machine gun": 0.88,  # Higher score for same trigger
                },
            }
        ]

        cues = map_model_predictions_to_cues(raw_windows, backend=BACKEND_YAMNET)
        assert len(cues) == 1
        assert cues[0]["trigger"] == "gunshot"
        assert cues[0]["score"] == 0.88

    def test_empty_window_predictions(self):
        """Empty input produces empty output list."""
        cues = map_model_predictions_to_cues([], backend=BACKEND_YAMNET)
        assert cues == []


class TestRealYAMNetInference:
    """Tests for actual YAMNet model inference."""

    def test_real_yamnet_inference_on_gunshot_clip(self):
        """Verify real YAMNet ONNX inference runs and detects gunshot with valid cue contract."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        cues = classify_audio_events(clip_path)
        assert len(cues) >= 1
        c = cues[0]
        assert c["trigger"] == "gunshot"
        assert c["score"] >= 0.65
        assert c["source"] == DEFAULT_SOURCE
        assert 0.0 <= c["start"] < c["end"]
        assert c["start"] == pytest.approx(1.44, abs=0.1)

    def test_real_yamnet_inference_on_negative_ambient(self):
        """Negative ambient noise produces zero trigger detections."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_negative_ambient.mp4"
        if not clip_path.is_file():
            pytest.skip("Ambient negative test clip not available")

        cues = classify_audio_events(clip_path)
        assert cues == []

    def test_real_yamnet_inference_on_negative_dialogue(self):
        """Calm dialogue produces zero trigger detections."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_negative_dialogue.mp4"
        if not clip_path.is_file():
            pytest.skip("Dialogue negative test clip not available")

        cues = classify_audio_events(clip_path)
        assert cues == []

    def test_raw_cue_fields_contract(self):
        """Emitted cues adhere strictly to raw detection schema contract."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        cues = classify_audio_events(clip_path)
        for cue in cues:
            assert isinstance(cue["start"], (int, float))
            assert isinstance(cue["end"], (int, float))
            assert cue["start"] >= 0.0
            assert cue["end"] > cue["start"]
            assert cue["trigger"] in ("gunshot", "explosion", "scream")
            assert 0.0 <= cue["score"] <= 1.0
            assert cue["source"] == DEFAULT_SOURCE

    def test_nonexistent_audio_file_raises_error(self, tmp_path):
        """Nonexistent audio file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            classify_audio_events(tmp_path / "missing.wav")

