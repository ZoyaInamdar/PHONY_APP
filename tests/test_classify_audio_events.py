"""Unit and integration tests for Audio-Event Classifier (scripts/classify_audio_events.py).

Verifies Person A and B2 audio classifier requirements:
1. Label mapping for gunshot, explosion, and scream across candidate backends (YAMNet, AST, PANNs).
2. Confidence threshold filtering against config-specified thresholds.
3. Windowing and timestamp conversion matching cue schema ([start, end), score in [0,1]).
4. Score aggregation (max mapped class probability).
5. Explicit, non-silent failure on missing runtime, missing model files, or invalid inputs.
6. Real YAMNet ONNX inference execution on positive synthetic gunshot clip and negative baselines.
7. Verification that contract tests do not vacuously pass on empty detections.
"""

from pathlib import Path
import math
import warnings
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
    run_yamnet_inference,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestAudioClassifierMapping:
    """Tests for AudioSet class mapping across backends."""

    def test_yamnet_label_mappings(self):
        """YAMNet class labels correctly map to canonical project triggers without cross-contamination."""
        mapping = get_class_to_trigger_mapping(backend=BACKEND_YAMNET)
        assert mapping["gunshot, gunfire"] == "gunshot"
        assert mapping["machine gun"] == "gunshot"
        assert mapping["cap gun"] == "gunshot"
        assert mapping["fusillade"] == "gunshot"
        assert mapping["explosion"] == "explosion"
        assert mapping["burst, pop"] == "explosion"
        assert mapping["boom"] == "explosion"
        assert mapping["screaming"] == "scream"
        assert mapping["shout"] == "scream"
        assert mapping["yell"] == "scream"
        assert mapping["children shouting"] == "scream"
        assert mapping["bellow"] == "scream"
        assert mapping["whoop"] == "scream"
        assert mapping["wail, moan"] == "scream"
        assert "battle cry" not in mapping

        # Verify gunshot classes do not map to explosion
        for g_term in ("gunshot, gunfire", "machine gun", "fusillade", "cap gun"):
            assert mapping[g_term] == "gunshot"

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

    def test_all_yamnet_mapped_classes_exist_in_labels_txt(self):
        """All mapped classes in config and defaults exist in YAMNet labels.txt."""
        labels_path = REPO_ROOT / "models" / "yamnet" / "yamnet-onnx-float" / "labels.txt"
        if not labels_path.is_file():
            pytest.skip("YAMNet labels.txt not available")
        with labels_path.open("r", encoding="utf-8") as f:
            valid_labels = {line.strip().lower() for line in f if line.strip()}

        mapping = get_class_to_trigger_mapping(backend=BACKEND_YAMNET)
        for mapped_class in mapping.keys():
            assert mapped_class in valid_labels, (
                f"Configured class '{mapped_class}' does not exist in YAMNet labels.txt"
            )


class TestAudioPredictionMappingAndThresholds:
    """Unit tests for threshold filtering, timestamps, and raw cue formatting."""

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
                    "Screaming": 0.74,  # > 0.58 threshold
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
                    "Screaming": 0.55,         # Below 0.58 threshold
                },
            }
        ]

        cues = map_model_predictions_to_cues(raw_windows, backend=BACKEND_YAMNET)
        assert cues == []

    def test_calibrated_scream_threshold_accepts_recovered_scream(self):
        """Verify calibrated threshold (0.58) accepts genuine 0.5995 scream and rejects 0.57."""
        threshold = get_trigger_threshold("scream")
        assert threshold == 0.58

        # 0.5995 should produce a cue
        cues_hit = map_model_predictions_to_cues([
            {
                "start": 4261.44,
                "end": 4262.40,
                "class_probabilities": {"Screaming": 0.5995},
            }
        ], backend=BACKEND_YAMNET)
        assert len(cues_hit) == 1
        assert cues_hit[0]["trigger"] == "scream"
        assert cues_hit[0]["score"] == 0.5995

        # 0.57 should be filtered out
        cues_miss = map_model_predictions_to_cues([
            {
                "start": 4261.44,
                "end": 4262.40,
                "class_probabilities": {"Screaming": 0.57},
            }
        ], backend=BACKEND_YAMNET)
        assert cues_miss == []

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

    def test_raw_cue_fields_contract_mocked(self):
        """Emitted cues adhere strictly to raw detection schema contract (5 fields, no actions)."""
        raw_windows = [
            {
                "start": 1.25,
                "end": 2.21,
                "class_probabilities": {
                    "Gunshot, gunfire": 0.85,
                },
            }
        ]
        cues = map_model_predictions_to_cues(raw_windows, backend=BACKEND_YAMNET)
        assert len(cues) > 0, "Verification must test non-empty cues to prevent vacuous success"

        for cue in cues:
            assert set(cue.keys()) == {"start", "end", "trigger", "score", "source"}
            assert isinstance(cue["start"], (int, float))
            assert isinstance(cue["end"], (int, float))
            assert math.isfinite(cue["start"])
            assert math.isfinite(cue["end"])
            assert cue["start"] >= 0.0
            assert cue["end"] > cue["start"]
            assert cue["trigger"] == "gunshot"
            assert isinstance(cue["score"], float)
            assert math.isfinite(cue["score"])
            assert 0.0 <= cue["score"] <= 1.0
            assert cue["source"] == DEFAULT_SOURCE
            # Action separation check
            for forbidden in ("action", "mute", "skip", "hide", "warn"):
                assert forbidden not in cue

    def test_timestamps_and_score_bounds(self):
        """Verify strict numerical constraints on timestamps and confidence scores."""
        raw_windows = [
            {
                "start": 0.0,
                "end": 0.96,
                "class_probabilities": {"Gunshot, gunfire": 0.9999},
            }
        ]
        cues = map_model_predictions_to_cues(raw_windows, backend=BACKEND_YAMNET)
        assert len(cues) == 1
        c = cues[0]
        assert c["start"] == 0.0
        assert c["end"] == 0.96
        assert c["score"] == 0.9999


class TestRuntimeAndModelErrorHandling:
    """Tests proving that errors are raised explicitly rather than failing silently."""

    def test_nonexistent_audio_file_raises_error(self, tmp_path):
        """Nonexistent audio file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            classify_audio_events(tmp_path / "missing_file.wav")

    def test_missing_model_file_raises_error(self, tmp_path):
        """Missing ONNX model file raises FileNotFoundError."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        with pytest.raises(FileNotFoundError, match="YAMNet ONNX model not found"):
            classify_audio_events(
                clip_path,
                model_path=tmp_path / "nonexistent_yamnet.onnx",
            )

    def test_missing_labels_file_raises_error(self, tmp_path):
        """Missing labels file raises FileNotFoundError."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        with pytest.raises(FileNotFoundError, match="YAMNet labels file not found"):
            classify_audio_events(
                clip_path,
                labels_path=tmp_path / "nonexistent_labels.txt",
            )

    def test_missing_external_weights_raises_error(self, tmp_path):
        """Missing external weights file raises FileNotFoundError."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        # Create dummy ONNX file named yamnet.onnx without accompanying yamnet.data
        dummy_onnx = tmp_path / "yamnet.onnx"
        dummy_onnx.write_text("dummy", encoding="utf-8")
        real_labels = REPO_ROOT / "models" / "yamnet" / "yamnet-onnx-float" / "labels.txt"

        with pytest.raises(FileNotFoundError, match="external weights file not found"):
            run_yamnet_inference(
                audio_path=clip_path,
                model_path=dummy_onnx,
                labels_path=real_labels,
            )

    def test_unsupported_backend_raises_error(self):
        """Requesting unsupported execution backend raises NotImplementedError in strict mode."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        with pytest.raises(NotImplementedError, match="Backend 'AST' is not supported"):
            classify_audio_events(clip_path, backend=BACKEND_AST, strict=True)

    def test_non_strict_mode_emits_warning_on_failure(self, tmp_path):
        """In non-strict mode, failures emit a RuntimeWarning and return empty list."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        with pytest.warns(RuntimeWarning, match="Audio event classification failed"):
            cues = classify_audio_events(
                clip_path,
                model_path=tmp_path / "nonexistent.onnx",
                strict=False,
            )
            assert cues == []


class TestRealYAMNetInference:
    """Tests executing real YAMNet ONNX inference on actual disk assets."""

    @pytest.fixture(autouse=True)
    def verify_onnxruntime_installed(self):
        """Ensure onnxruntime is installed; fail explicitly if missing."""
        try:
            import onnxruntime
        except ImportError as exc:
            pytest.fail(f"onnxruntime is not installed in active environment: {exc}")

    def test_real_yamnet_inference_processes_windows_on_gunshot_clip(self):
        """Verify real YAMNet ONNX inference loads and processes audio windows."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        preds = run_yamnet_inference(clip_path)
        assert len(preds) == 7, f"Expected 7 windows from 4.0s clip, got {len(preds)}"

        # Verify silence in initial baseline windows
        assert preds[0]["class_probabilities"]["Silence"] > 0.99
        assert preds[1]["class_probabilities"]["Silence"] > 0.99

        # Window 3 (1.44s - 2.40s) spans the synthetic gunshot transient at 1.5s - 2.0s
        w3 = preds[3]
        assert w3["start"] == 1.44
        assert w3["end"] == 2.4
        probs = w3["class_probabilities"]
        assert "Gunshot, gunfire" in probs
        gunshot_prob = probs["Gunshot, gunfire"]

        # Real model inference confirmation: Gunshot is the top firearm class
        assert gunshot_prob > 0.15, f"Expected gunshot detection probability > 0.15, got {gunshot_prob}"
        assert gunshot_prob == pytest.approx(0.2013, abs=0.01)

    def test_real_yamnet_inference_produces_gunshot_cue_with_calibrated_threshold(self):
        """Verify qualifying predictions produce a valid canonical gunshot cue."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        # Run with threshold calibrated for synthetic transient model sensitivity
        calibrated_cfg = {"triggers": {"gunshot": {"threshold": 0.15}}}
        cues = classify_audio_events(clip_path, config=calibrated_cfg)

        assert len(cues) >= 1, "Must produce at least one gunshot cue on positive clip"
        assert any(c["start"] == pytest.approx(1.44, abs=0.1) for c in cues), "Expected cue at window 1.44s"

        for c in cues:
            assert c["trigger"] == "gunshot"
            assert c["score"] >= 0.15
            assert c["source"] == DEFAULT_SOURCE
            assert 0.0 <= c["start"] < c["end"]
            # Validate raw cue fields contract on real model output
            assert set(c.keys()) == {"start", "end", "trigger", "score", "source"}
            for forbidden in ("action", "mute", "skip", "hide", "warn"):
                assert forbidden not in c

    def test_real_yamnet_inference_production_threshold_behavior(self):
        """Document production threshold behavior on synthetic clips.

        Under the production threshold (0.65), synthetic acoustic transients
        score ~0.20 and are correctly rejected without raising errors.
        """
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_gunshot_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Audio test clip not available")

        # Run with default production threshold (0.65)
        cues = classify_audio_events(clip_path)
        assert cues == [], "Synthetic transient below 0.65 production threshold must yield no cues"

    def test_real_yamnet_inference_on_negative_ambient(self):
        """Negative ambient noise produces zero trigger detections after real inference."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_negative_ambient.mp4"
        if not clip_path.is_file():
            pytest.skip("Ambient negative test clip not available")

        # Verify inference actually ran and produced window predictions
        preds = run_yamnet_inference(clip_path)
        assert len(preds) > 0, "Inference must process windows rather than silently returning empty"
        max_gunshot = max(w["class_probabilities"].get("Gunshot, gunfire", 0.0) for w in preds)
        assert max_gunshot < 0.01, f"Ambient noise produced unexpected gunshot probability: {max_gunshot}"

        # Standard classifier output must be empty
        cues = classify_audio_events(clip_path)
        assert cues == []

    def test_real_yamnet_inference_on_negative_dialogue(self):
        """Calm dialogue produces zero trigger detections after real inference."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_negative_dialogue.mp4"
        if not clip_path.is_file():
            pytest.skip("Dialogue negative test clip not available")

        # Verify inference actually ran and evaluated audio
        preds = run_yamnet_inference(clip_path)
        assert len(preds) > 0, "Inference must process windows rather than silently returning empty"
        max_gunshot = max(w["class_probabilities"].get("Gunshot, gunfire", 0.0) for w in preds)
        assert max_gunshot < 0.01, f"Dialogue produced unexpected gunshot probability: {max_gunshot}"

        # Standard classifier output must be empty
        cues = classify_audio_events(clip_path)
        assert cues == []

    def test_real_yamnet_inference_on_synthetic_scream_clip(self):
        """Verify real YAMNet ONNX inference executes on audio_scream_test.mp4.

        Documents that synthetic vibrato tone (+FM modulation) generates valid windows
        with silence baseline at start/end, but achieves low scream class probability
        (~0.0075) below the production 0.60 threshold because it lacks natural human
        vocal tract formants and non-linear biphonation.
        """
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_scream_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Scream test clip not available")

        preds = run_yamnet_inference(clip_path)
        assert len(preds) == 7, f"Expected 7 windows from 4.0s clip, got {len(preds)}"

        # Initial and trailing silence
        assert preds[0]["class_probabilities"]["Silence"] > 0.99
        assert preds[6]["class_probabilities"]["Silence"] > 0.99

        # Peak scream probability remains well below production 0.60 threshold
        max_scream = max(w["class_probabilities"].get("Screaming", 0.0) for w in preds)
        assert max_scream < 0.02, f"Expected synthetic scream probability < 0.02, got {max_scream}"

        # Production classification yields no false cues under 0.60 threshold
        cues = classify_audio_events(clip_path)
        assert cues == []

    def test_real_yamnet_inference_produces_scream_cue_with_calibrated_threshold(self):
        """Verify scream cue schema contract compliance when threshold is calibrated."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_scream_test.mp4"
        if not clip_path.is_file():
            pytest.skip("Scream test clip not available")

        # Calibrate threshold to detect the synthetic vibrato transient
        calibrated_cfg = {"triggers": {"scream": {"threshold": 0.005}}}
        cues = classify_audio_events(clip_path, config=calibrated_cfg)

        assert len(cues) >= 1, "Must produce at least one scream cue with calibrated threshold"
        scream_cues = [c for c in cues if c["trigger"] == "scream"]
        assert len(scream_cues) >= 1

        for cue in scream_cues:
            assert set(cue.keys()) == {"start", "end", "trigger", "score", "source"}
            assert cue["trigger"] == "scream"
            assert isinstance(cue["start"], (int, float))
            assert isinstance(cue["end"], (int, float))
            assert cue["end"] > cue["start"]
            assert cue["score"] >= 0.005
            assert cue["source"] == DEFAULT_SOURCE
            for forbidden in ("action", "mute", "skip", "hide", "warn"):
                assert forbidden not in cue

    def test_real_yamnet_inference_on_multi_event_mix(self):
        """Verify real YAMNet inference on composite audio multi-event mix."""
        clip_path = REPO_ROOT / "data" / "test_clips" / "audio_multi_event_mix.mp4"
        if not clip_path.is_file():
            pytest.skip("Multi-event mix test clip not available")

        preds = run_yamnet_inference(clip_path)
        assert len(preds) == 17, f"Expected 17 windows from 9.0s clip, got {len(preds)}"

        # Scream scores in synthetic interval stay below production threshold
        max_scream = max(w["class_probabilities"].get("Screaming", 0.0) for w in preds)
        assert max_scream < 0.58
