"""Unit tests for the Offline Visual Insect Detector Prototype.

Tests cover:
1. Video probing and frame sampling timestamps.
2. Prompt configuration validation and structure.
3. Upstream SigLIP 2 scoring semantics, logit scaling, independent sigmoid probabilities.
4. Distractor suppression separation (native SigLIP score vs optional heuristic).
5. Gemma text contract: lowercasing, EOS appending, pad token ID 0, 64-token truncation/padding.
6. Bilinear image preprocessing, tensor layout, and input normalization.
7. External tensor data verification and missing weight handling.
8. Prompt embeddings schema validation, non-finite value rejection, and stale artifact detection.
9. Score-to-decision threshold behavior and interval merging.
10. Prediction schema compatibility and validation.
11. Explicit mock-versus-real runtime behavior.
12. Refusal to overwrite existing predictions without flag.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from scripts.detect_insect import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_EMBED_DIM,
    DEFAULT_EOS_TOKEN_ID,
    DEFAULT_LOGIT_BIAS,
    DEFAULT_LOGIT_SCALE,
    DEFAULT_MERGE_GAP_SECONDS,
    DEFAULT_MODEL_DIR,
    DEFAULT_PAD_TOKEN_ID,
    DEFAULT_PROMPT_CONFIG,
    DEFAULT_PROMPT_EMBEDDINGS,
    DEFAULT_SAMPLING_FPS,
    DEFAULT_TEXT_MAX_LENGTH,
    DEFAULT_VISION_MODEL_FP16,
    DEFAULT_DECISION_POLICY,
    DEFAULT_MARGIN_THRESHOLD,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_CONSECUTIVE_FRAMES,
    BaseVisionLanguageBackend,
    MockInsectDetectionBackend,
    SigLIP2OnnxBackend,
    SigLIP2TextEncoder,
    SigLIP2TokenizerAdapter,
    cmd_predict,
    compute_siglip2_scores,
    evaluate_frame_decision,
    format_prompt_tokens,
    generate_prompt_embeddings,
    merge_frame_detections_to_intervals,
    preprocess_frame_siglip,
    probe_video_metadata,
    run_insect_detection_on_video,
    sample_video_frames,
    validate_and_load_prompt_embeddings,
)
from scripts.evaluate_insect import validate_prediction_data


# ==============================================================================
# 1. Prompt Configuration Tests
# ==============================================================================

class TestPromptConfiguration:
    def test_default_prompt_config_structure(self):
        """Default prompt configuration has required positive and negative sets."""
        assert "positive_prompts" in DEFAULT_PROMPT_CONFIG
        assert "negative_prompts" in DEFAULT_PROMPT_CONFIG
        assert len(DEFAULT_PROMPT_CONFIG["positive_prompts"]) >= 3
        assert len(DEFAULT_PROMPT_CONFIG["negative_prompts"]) >= 3
        assert DEFAULT_PROMPT_CONFIG["scoring_method"] == "siglip2_independent_sigmoid"
        assert DEFAULT_PROMPT_CONFIG["logit_scale"] == DEFAULT_LOGIT_SCALE
        assert DEFAULT_PROMPT_CONFIG["logit_bias"] == DEFAULT_LOGIT_BIAS
        assert DEFAULT_PROMPT_CONFIG["apply_distractor_suppression"] is False

    def test_positive_prompts_cover_insect_scenarios(self):
        """Positive prompts cover crawling, flying, and swarms."""
        joined = " ".join(DEFAULT_PROMPT_CONFIG["positive_prompts"]).lower()
        assert "insect" in joined
        assert "crawling" in joined or "bug" in joined
        assert "flying" in joined or "wasp" in joined
        assert "swarm" in joined

    def test_negative_prompts_cover_distractors(self):
        """Negative prompts cover spiders, foliage, leaves, dust, bark."""
        joined = " ".join(DEFAULT_PROMPT_CONFIG["negative_prompts"]).lower()
        assert "spider" in joined
        assert "leaves" in joined
        assert "bark" in joined
        assert "dust" in joined or "sparks" in joined


# ==============================================================================
# 2. Upstream SigLIP 2 Scoring Semantics & Numerical Tests
# ==============================================================================

class TestSigLIP2Scoring:
    def test_exact_sigmoid_neutral_decision_boundary(self):
        """When cosine similarity = -logit_bias / logit_scale, logit is 0.0 and probability is 0.5."""
        scale = 112.66890226978423
        bias = -16.771724700927734
        neutral_cosine = -bias / scale  # ~0.148858

        img = np.array([[1.0, 0.0]])
        pos = np.array([[neutral_cosine, math.sqrt(max(0.0, 1.0 - neutral_cosine ** 2))]])
        neg = np.array([[0.0, 1.0]])

        res = compute_siglip2_scores(img, pos, neg, logit_scale=scale, logit_bias=bias)
        assert abs(res["raw_scores"]["native_sigmoid_max"] - 0.5000) < 0.001
        assert res["confidence"] == 0.5000

    def test_zero_cosine_similarity_produces_low_probability(self):
        """Orthogonal vector (cosine=0) produces sigmoid(logit_bias) which is near zero."""
        scale = DEFAULT_LOGIT_SCALE
        bias = DEFAULT_LOGIT_BIAS
        expected_prob = 1.0 / (1.0 + math.exp(-bias))  # sigmoid(-16.77) ~ 5.2e-8

        img = np.array([[1.0, 0.0]])
        pos = np.array([[0.0, 1.0]])  # orthogonal
        neg = np.array([[0.0, 1.0]])

        res = compute_siglip2_scores(img, pos, neg, logit_scale=scale, logit_bias=bias)
        assert res["raw_scores"]["positive_cosine_max"] == 0.0
        assert res["raw_scores"]["native_sigmoid_max"] < 0.0001
        assert res["confidence"] < 0.0001

    def test_independent_label_scores_do_not_sum_to_one(self):
        """SigLIP scores labels independently; label probabilities do not sum to 1."""
        img = np.array([[1.0, 0.0, 0.0]])
        # Three prompts with positive alignment
        pos = np.array([
            [0.8, 0.6, 0.0],
            [0.7, 0.7, 0.0],
        ])
        neg = np.array([[0.6, 0.8, 0.0]])

        res = compute_siglip2_scores(
            img, pos, neg,
            positive_prompts=["pos1", "pos2"],
            negative_prompts=["neg1"],
        )
        per_prompt = res["raw_scores"]["per_prompt_scores"]
        all_sigmoids = [v["sigmoid"] for v in per_prompt.values()]
        # High cosines produce sigmoid values near 1.0, sum is > 1.0
        assert sum(all_sigmoids) > 1.0

    def test_distractor_suppression_heuristic_separation(self):
        """Distractor suppression is strictly separated: native SigLIP score remains unmodified by default."""
        img = np.array([[1.0, 0.0, 0.0]])
        pos = np.array([[0.20, 0.98, 0.0]])  # moderate insect match
        neg = np.array([[0.35, 0.0, 0.94]])  # stronger distractor (e.g. spider)

        # Default: apply_distractor_suppression=False -> confidence is native SigLIP score
        res_native = compute_siglip2_scores(img, pos, neg, apply_distractor_suppression=False)
        assert res_native["confidence"] == res_native["raw_scores"]["native_sigmoid_max"]
        assert res_native["raw_scores"]["distractor_suppression_applied"] is False

        # When explicitly enabled, heuristic score dampens confidence
        res_suppressed = compute_siglip2_scores(img, pos, neg, apply_distractor_suppression=True)
        assert res_suppressed["confidence"] < res_suppressed["raw_scores"]["native_sigmoid_max"]
        assert res_suppressed["confidence"] == res_suppressed["raw_scores"]["heuristic_distractor_suppressed_score"]
        assert res_suppressed["raw_scores"]["distractor_suppression_applied"] is True

    def test_raw_scores_separation_of_cosines_logits_and_sigmoids(self):
        """Scoring output preserves raw cosines, logits, and sigmoids distinctly."""
        img = np.random.randn(1, 768)
        pos = np.random.randn(3, 768)
        neg = np.random.randn(4, 768)

        pos_prompts = [f"pos_{i}" for i in range(3)]
        neg_prompts = [f"neg_{i}" for i in range(4)]

        res = compute_siglip2_scores(
            img, pos, neg,
            positive_prompts=pos_prompts,
            negative_prompts=neg_prompts,
        )
        raw = res["raw_scores"]
        assert "positive_cosine_max" in raw
        assert "negative_cosine_max" in raw
        assert "positive_logit_max" in raw
        assert "negative_logit_max" in raw
        assert "native_sigmoid_max" in raw
        assert "negative_sigmoid_max" in raw
        assert raw["logit_scale"] == round(DEFAULT_LOGIT_SCALE, 4)
        assert raw["logit_bias"] == round(DEFAULT_LOGIT_BIAS, 4)
        assert 0.0 <= res["confidence"] <= 1.0

        # Check per-prompt scores
        per_prompt = raw["per_prompt_scores"]
        assert len(per_prompt) == 7
        for p in pos_prompts:
            item = per_prompt[p]
            expected_logit = item["cosine"] * DEFAULT_LOGIT_SCALE + DEFAULT_LOGIT_BIAS
            assert abs(item["logit"] - round(expected_logit, 4)) <= 0.05

    def test_unnormalized_vectors_auto_normalized(self):
        """Vectors with non-unit norm are automatically normalized without altering cosine math."""
        img_norm = np.array([[1.0, 0.0]])
        img_scaled = np.array([[100.0, 0.0]])
        pos = np.array([[0.2, 0.8]])
        neg = np.array([[-0.5, 0.5]])

        res_norm = compute_siglip2_scores(img_norm, pos, neg)
        res_scaled = compute_siglip2_scores(img_scaled, pos, neg)
        assert res_norm["confidence"] == res_scaled["confidence"]
        assert res_norm["raw_scores"]["positive_cosine_max"] == res_scaled["raw_scores"]["positive_cosine_max"]

    def test_non_finite_embedding_rejection(self):
        """Non-finite values (NaN or Inf) in embeddings raise ValueError."""
        nan_img = np.array([[np.nan, 1.0]])
        pos = np.array([[1.0, 0.0]])
        neg = np.array([[0.0, 1.0]])

        with pytest.raises(ValueError, match="Image embedding contains non-finite values"):
            compute_siglip2_scores(nan_img, pos, neg)

        inf_pos = np.array([[np.inf, 0.0]])
        with pytest.raises(ValueError, match="Prompt embeddings contain non-finite values"):
            compute_siglip2_scores(pos, inf_pos, neg)


# ==============================================================================
# 3. Gemma Text Contract & Token Sequence Formatting Tests
# ==============================================================================

class TestGemmaTextContract:
    def test_special_token_constants(self):
        """Special token constants match upstream Gemma tokenizer specifications."""
        assert DEFAULT_PAD_TOKEN_ID == 0
        assert DEFAULT_EOS_TOKEN_ID == 1
        assert DEFAULT_TEXT_MAX_LENGTH == 64

    def test_format_prompt_tokens_appends_eos(self):
        """Token sequence has <eos> appended if not already the final token."""
        raw_ids = [105, 230, 450]
        formatted = format_prompt_tokens(raw_ids, eos_token_id=1, pad_token_id=0, max_length=64)
        assert formatted[0:3] == [105, 230, 450]
        assert formatted[3] == 1  # <eos>
        assert formatted[4] == 0  # <pad>
        assert len(formatted) == 64

    def test_format_prompt_tokens_does_not_duplicate_existing_eos(self):
        """If <eos> is already the final token, it is preserved without duplication."""
        raw_ids = [105, 230, 1]  # Already ends with EOS
        formatted = format_prompt_tokens(raw_ids, eos_token_id=1, pad_token_id=0, max_length=64)
        assert formatted[0:3] == [105, 230, 1]
        assert formatted[3] == 0  # <pad>, not a second <eos>
        assert len(formatted) == 64

    def test_format_prompt_tokens_padding_to_64(self):
        """Empty sequence is padded with <eos> followed by 63 <pad> tokens."""
        formatted = format_prompt_tokens([], eos_token_id=1, pad_token_id=0, max_length=64)
        assert len(formatted) == 64
        assert formatted[0] == 1  # <eos>
        assert all(t == 0 for t in formatted[1:])

    def test_format_prompt_tokens_truncation(self):
        """Long token sequence exceeding 64 tokens is truncated to exactly 64 tokens."""
        long_ids = list(range(10, 100))  # 90 tokens
        formatted = format_prompt_tokens(long_ids, eos_token_id=1, pad_token_id=0, max_length=64)
        assert len(formatted) == 64
        # First 64 tokens of (long_ids + [1])
        assert formatted == (long_ids + [1])[:64]


# ==============================================================================
# 4. Text Embedding Generation & Artifact Validation Tests
# ==============================================================================

class TestTextEmbeddingGenerationAndValidation:
    def test_validate_and_load_valid_artifact(self, tmp_path):
        """Valid prompt embeddings artifact loads with full metadata."""
        artifact_path = tmp_path / "prompt_embeddings.json"
        pos_prompts = ["a photo of an insect"]
        neg_prompts = ["a photo of an empty room"]
        payload = {
            "schema_version": 1,
            "model_family": "SigLIP-2",
            "model_id": "google/siglip2-base-patch16-224",
            "embed_dim": 768,
            "text_max_length": 64,
            "pad_token_id": 0,
            "eos_token_id": 1,
            "logit_scale": DEFAULT_LOGIT_SCALE,
            "logit_bias": DEFAULT_LOGIT_BIAS,
            "normalized": True,
            "positive_prompts": pos_prompts,
            "negative_prompts": neg_prompts,
            "positive_embeddings": [[0.01] * 768],
            "negative_embeddings": [[0.02] * 768],
        }
        artifact_path.write_text(json.dumps(payload), encoding="utf-8")

        loaded = validate_and_load_prompt_embeddings(
            artifact_path,
            expected_positive_prompts=pos_prompts,
            expected_negative_prompts=neg_prompts,
        )
        assert loaded["positive_embeddings"].shape == (1, 768)
        assert loaded["negative_embeddings"].shape == (1, 768)
        assert loaded["logit_scale"] == DEFAULT_LOGIT_SCALE
        assert loaded["logit_bias"] == DEFAULT_LOGIT_BIAS

    def test_stale_artifact_detection_rejects_modified_prompts(self, tmp_path):
        """Artifact with different prompts than configured is rejected as stale."""
        artifact_path = tmp_path / "prompt_embeddings.json"
        payload = {
            "schema_version": 1,
            "embed_dim": 768,
            "positive_prompts": ["old prompt"],
            "negative_prompts": ["old neg"],
            "positive_embeddings": [[0.1] * 768],
            "negative_embeddings": [[0.1] * 768],
        }
        artifact_path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="Stale prompt embeddings artifact"):
            validate_and_load_prompt_embeddings(
                artifact_path,
                expected_positive_prompts=["new prompt"],
                expected_negative_prompts=["old neg"],
            )

    def test_incompatible_schema_version_rejected(self, tmp_path):
        """Unsupported schema version raises ValueError."""
        artifact_path = tmp_path / "prompt_embeddings.json"
        payload = {
            "schema_version": 99,
            "embed_dim": 768,
            "positive_embeddings": [[0.1] * 768],
            "negative_embeddings": [[0.1] * 768],
        }
        artifact_path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="Unsupported prompt embeddings schema_version"):
            validate_and_load_prompt_embeddings(artifact_path)

    def test_incompatible_embedding_dimension_rejected(self, tmp_path):
        """Embeddings with dimension != 768 raise ValueError."""
        artifact_path = tmp_path / "prompt_embeddings.json"
        payload = {
            "schema_version": 1,
            "embed_dim": 512,  # Wrong dimension!
            "positive_embeddings": [[0.1] * 512],
            "negative_embeddings": [[0.1] * 512],
        }
        artifact_path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="Incompatible embed_dim"):
            validate_and_load_prompt_embeddings(artifact_path)

    def test_generator_fails_cleanly_when_text_encoder_missing(self, tmp_path):
        """generate_prompt_embeddings reports exact missing text model files."""
        out_path = tmp_path / "prompt_embeddings.json"
        with pytest.raises(FileNotFoundError, match="SigLIP 2 text model not found"):
            generate_prompt_embeddings(
                positive_prompts=["a photo of an insect"],
                negative_prompts=["a photo of an empty room"],
                output_path=out_path,
                text_model_path=tmp_path / "text_model_fp16.onnx",
                tokenizer_path=tmp_path / "tokenizer.json",
            )

    def test_generator_fails_cleanly_when_text_data_missing(self, tmp_path):
        """generate_prompt_embeddings reports missing text external data."""
        text_model = tmp_path / "text_model_fp16.onnx"
        text_model.write_bytes(b"dummy graph")
        out_path = tmp_path / "prompt_embeddings.json"

        with pytest.raises(FileNotFoundError, match="SigLIP 2 external text tensor data missing"):
            generate_prompt_embeddings(
                positive_prompts=["a photo of an insect"],
                negative_prompts=["a photo of an empty room"],
                output_path=out_path,
                text_model_path=text_model,
                tokenizer_path=tmp_path / "tokenizer.json",
            )

    def test_generator_fails_cleanly_when_tokenizer_missing(self, tmp_path):
        """generate_prompt_embeddings reports missing tokenizer when text encoder is provided."""
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input_ids"
        mock_session.get_inputs.return_value = [mock_input]
        mock_encoder = SigLIP2TextEncoder(session=mock_session)

        out_path = tmp_path / "prompt_embeddings.json"

        with pytest.raises(FileNotFoundError, match="SigLIP 2 tokenizer file not found"):
            generate_prompt_embeddings(
                positive_prompts=["a photo of an insect"],
                negative_prompts=["a photo of an empty room"],
                output_path=out_path,
                text_encoder=mock_encoder,
                tokenizer_path=tmp_path / "tokenizer.json",
            )


# ==============================================================================
# 5. SigLIP 2 Tokenizer Adapter Tests
# ==============================================================================

class TestSigLIP2TokenizerAdapter:
    def test_missing_tokenizer_file_raises_error(self, tmp_path):
        """Missing tokenizer.json raises FileNotFoundError with candidate source guidance."""
        with pytest.raises(FileNotFoundError, match="SigLIP 2 tokenizer file not found"):
            SigLIP2TokenizerAdapter(tokenizer_path=tmp_path / "nonexistent_tokenizer.json")

    def test_non_string_prompt_raises_type_error(self):
        """Non-string prompt input raises TypeError."""
        adapter = SigLIP2TokenizerAdapter(tokenizer_instance=lambda t: [10, 20])
        with pytest.raises(TypeError, match="Prompt must be a string"):
            adapter.encode(12345)  # type: ignore

    def test_adapter_lowercasing_and_sequence_formatting(self):
        """Adapter strips, lowercases prompt, appends EOS, and pads to 64 tokens."""
        recorded_inputs = []

        def mock_tok(text: str) -> List[int]:
            recorded_inputs.append(text)
            return [101, 102]

        adapter = SigLIP2TokenizerAdapter(tokenizer_instance=mock_tok)
        tokens = adapter.encode("  A Photo OF an INSECT   ")

        assert recorded_inputs == ["a photo of an insect"]
        assert len(tokens) == 64
        assert tokens[0:2] == [101, 102]
        assert tokens[2] == DEFAULT_EOS_TOKEN_ID  # 1
        assert all(t == DEFAULT_PAD_TOKEN_ID for t in tokens[3:])

    def test_adapter_batch_encode(self):
        """batch_encode produces (N, 64) int64 numpy array."""
        adapter = SigLIP2TokenizerAdapter(tokenizer_instance=lambda text: [42])
        prompts = ["first prompt", "second prompt", "third prompt"]
        batch = adapter.batch_encode(prompts)

        assert isinstance(batch, np.ndarray)
        assert batch.shape == (3, 64)
        assert batch.dtype == np.int64
        for i in range(3):
            assert batch[i, 0] == 42
            assert batch[i, 1] == DEFAULT_EOS_TOKEN_ID
            assert all(batch[i, j] == DEFAULT_PAD_TOKEN_ID for j in range(2, 64))

    def test_adapter_batch_encode_empty(self):
        """batch_encode on empty list returns (0, 64) array."""
        adapter = SigLIP2TokenizerAdapter(tokenizer_instance=lambda text: [42])
        batch = adapter.batch_encode([])
        assert batch.shape == (0, 64)
        assert batch.dtype == np.int64

    def test_adapter_truncation_for_long_sequence(self):
        """Sequences exceeding 64 tokens are truncated to exactly 64 tokens."""
        adapter = SigLIP2TokenizerAdapter(tokenizer_instance=lambda text: list(range(100)))
        tokens = adapter.encode("very long text")
        assert len(tokens) == 64


# ==============================================================================
# 6. SigLIP 2 ONNX Text Encoder Tests
# ==============================================================================

class TestSigLIP2TextEncoder:
    def test_missing_model_raises_error(self, tmp_path):
        """Missing text_model_fp16.onnx raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="SigLIP 2 text model not found"):
            SigLIP2TextEncoder(model_path=tmp_path / "text_model_fp16.onnx")

    def test_missing_data_sibling_raises_error(self, tmp_path):
        """Existing ONNX model without .onnx_data sibling raises FileNotFoundError."""
        model_file = tmp_path / "text_model_fp16.onnx"
        model_file.write_bytes(b"dummy graph")
        with pytest.raises(FileNotFoundError, match="SigLIP 2 external text tensor data missing"):
            SigLIP2TextEncoder(model_path=model_file)

    def test_invalid_input_shape_raises_error(self):
        """Input ids not having shape (B, 64) raise ValueError."""
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input_ids"
        mock_session.get_inputs.return_value = [mock_input]

        encoder = SigLIP2TextEncoder(session=mock_session)

        # 1D array
        with pytest.raises(ValueError, match="Expected input_ids shape"):
            encoder.encode(np.array([1, 2, 3]))

        # Wrong sequence length (50 instead of 64)
        with pytest.raises(ValueError, match="Expected input_ids shape"):
            encoder.encode(np.zeros((2, 50), dtype=np.int64))

    def test_encoder_empty_batch(self):
        """Empty input batch returns (0, 768) float32 array without calling session."""
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input_ids"
        mock_session.get_inputs.return_value = [mock_input]

        encoder = SigLIP2TextEncoder(session=mock_session)
        empty_in = np.empty((0, 64), dtype=np.int64)
        res = encoder.encode(empty_in)
        assert res.shape == (0, DEFAULT_EMBED_DIM)
        assert res.dtype == np.float32
        mock_session.run.assert_not_called()

    def test_encoder_normalizes_output_vectors(self):
        """Encoder executes session and ensures output vectors have unit L2 norm."""
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input_ids"
        mock_session.get_inputs.return_value = [mock_input]

        # Raw unnormalized embeddings with varying magnitudes
        raw_embeds = np.random.randn(3, 768).astype(np.float32) * 5.0
        mock_session.run.return_value = [raw_embeds]

        encoder = SigLIP2TextEncoder(session=mock_session)
        input_ids = np.zeros((3, 64), dtype=np.int64)
        out = encoder.encode(input_ids)

        assert out.shape == (3, 768)
        assert out.dtype == np.float32
        norms = np.linalg.norm(out, axis=-1)
        assert np.allclose(norms, 1.0, atol=1e-5)

    def test_encoder_non_finite_output_rejected(self):
        """NaN or Inf outputs from session raise ValueError."""
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input_ids"
        mock_session.get_inputs.return_value = [mock_input]

        bad_embeds = np.zeros((1, 768), dtype=np.float32)
        bad_embeds[0, 5] = np.nan
        mock_session.run.return_value = [bad_embeds]

        encoder = SigLIP2TextEncoder(session=mock_session)
        with pytest.raises(ValueError, match="Text encoder produced non-finite embedding values"):
            encoder.encode(np.zeros((1, 64), dtype=np.int64))

    def test_encoder_zero_norm_output_rejected(self):
        """All-zero embeddings from session raise ValueError."""
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input_ids"
        mock_session.get_inputs.return_value = [mock_input]

        mock_session.run.return_value = [np.zeros((1, 768), dtype=np.float32)]

        encoder = SigLIP2TextEncoder(session=mock_session)
        with pytest.raises(ValueError, match="Text encoder produced zero-norm embedding vector"):
            encoder.encode(np.zeros((1, 64), dtype=np.int64))

    def test_encode_prompts_maps_prompt_strings_to_vectors(self):
        """encode_prompts associates each prompt string with its 768-dim vector."""
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input_ids"
        mock_session.get_inputs.return_value = [mock_input]

        raw_embeds = np.ones((2, 768), dtype=np.float32)
        mock_session.run.return_value = [raw_embeds]

        encoder = SigLIP2TextEncoder(session=mock_session)
        adapter = SigLIP2TokenizerAdapter(tokenizer_instance=lambda t: [10])

        prompts = ["a photo of an insect", "a photo of a spider"]
        embeds, prompt_map = encoder.encode_prompts(prompts, adapter)

        assert embeds.shape == (2, 768)
        assert len(prompt_map) == 2
        assert "a photo of an insect" in prompt_map
        assert "a photo of a spider" in prompt_map
        assert len(prompt_map["a photo of an insect"]) == 768


# ==============================================================================
# 7. End-to-End Text Embedding Generation with Adapters Tests
# ==============================================================================

class TestTextEmbeddingGenerationWithAdapters:
    def test_generate_prompt_embeddings_with_mock_adapters(self, tmp_path):
        """generate_prompt_embeddings serializes valid artifact with prompt_to_embedding mapping."""
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input_ids"
        mock_session.get_inputs.return_value = [mock_input]

        def mock_run(output_names, feed_dict):
            b_size = feed_dict["input_ids"].shape[0]
            # Return deterministic unit vectors
            vecs = np.ones((b_size, 768), dtype=np.float32)
            return [vecs]

        mock_session.run = mock_run

        tok_adapter = SigLIP2TokenizerAdapter(tokenizer_instance=lambda t: [5, 6, 7])
        encoder = SigLIP2TextEncoder(session=mock_session)

        out_path = tmp_path / "prompt_embeddings.json"
        pos_prompts = ["a photo of an insect", "a photo of a wasp"]
        neg_prompts = ["a photo of a room"]

        res = generate_prompt_embeddings(
            positive_prompts=pos_prompts,
            negative_prompts=neg_prompts,
            output_path=out_path,
            tokenizer_adapter=tok_adapter,
            text_encoder=encoder,
        )

        assert res["status"] == "success"
        assert res["positive_count"] == 2
        assert res["negative_count"] == 1
        assert res["total_prompts"] == 3

        # Read back JSON
        with out_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["schema_version"] == 1
        assert data["model_family"] == "SigLIP-2"
        assert data["embed_dim"] == 768
        assert len(data["positive_embeddings"]) == 2
        assert len(data["negative_embeddings"]) == 1
        assert "prompt_to_embedding" in data
        assert len(data["prompt_to_embedding"]) == 3
        for p in pos_prompts + neg_prompts:
            assert p in data["prompt_to_embedding"]
            assert len(data["prompt_to_embedding"][p]) == 768

        # Validate with validate_and_load_prompt_embeddings
        loaded = validate_and_load_prompt_embeddings(
            out_path,
            expected_positive_prompts=pos_prompts,
            expected_negative_prompts=neg_prompts,
        )
        assert loaded["positive_embeddings"].shape == (2, 768)
        assert loaded["negative_embeddings"].shape == (1, 768)
        assert len(loaded["prompt_to_embedding"]) == 3


# ==============================================================================
# 5. Image Preprocessing Tests
# ==============================================================================

class TestImagePreprocessing:
    def test_valid_bgr_frame_preprocessing(self):
        """Standard BGR image is resized to (1, 3, 224, 224) float32 in [-1.0, 1.0]."""
        dummy_frame = np.full((480, 640, 3), 128, dtype=np.uint8)
        tensor = preprocess_frame_siglip(dummy_frame)

        assert tensor.shape == (1, 3, 224, 224)
        assert tensor.dtype == np.float32
        assert -1.0 <= np.min(tensor) <= 1.0
        assert -1.0 <= np.max(tensor) <= 1.0

    def test_bgr_to_rgb_channel_inversion(self):
        """Pure blue frame (BGR: 255, 0, 0) becomes pure blue in RGB channel 2."""
        blue_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        blue_frame[:, :, 0] = 255  # B=255, G=0, R=0

        tensor = preprocess_frame_siglip(blue_frame)
        assert np.isclose(tensor[0, 0, 0, 0], -1.0, atol=1e-3)  # R
        assert np.isclose(tensor[0, 1, 0, 0], -1.0, atol=1e-3)  # G
        assert np.isclose(tensor[0, 2, 0, 0], 1.0, atol=1e-3)   # B

    def test_extreme_pixel_values_rescaling(self):
        """0 maps to -1.0 and 255 maps to 1.0."""
        black_frame = np.zeros((50, 50, 3), dtype=np.uint8)
        white_frame = np.full((50, 50, 3), 255, dtype=np.uint8)

        black_tensor = preprocess_frame_siglip(black_frame)
        white_tensor = preprocess_frame_siglip(white_frame)

        assert np.isclose(np.min(black_tensor), -1.0, atol=1e-3)
        assert np.isclose(np.max(black_tensor), -1.0, atol=1e-3)
        assert np.isclose(np.min(white_tensor), 1.0, atol=1e-3)
        assert np.isclose(np.max(white_tensor), 1.0, atol=1e-3)

    def test_invalid_and_empty_frames_raise_error(self):
        """None, empty arrays, or invalid shapes raise clean ValueError."""
        with pytest.raises(ValueError, match="Cannot preprocess None frame"):
            preprocess_frame_siglip(None)

        with pytest.raises(ValueError, match="Invalid or empty frame array"):
            preprocess_frame_siglip(np.array([], dtype=np.uint8))

        with pytest.raises(ValueError, match="Expected 3-channel BGR frame"):
            preprocess_frame_siglip(np.zeros((100, 100), dtype=np.uint8))  # 2D grayscale

        with pytest.raises(ValueError, match="Expected 3-channel BGR frame"):
            preprocess_frame_siglip(np.zeros((100, 100, 4), dtype=np.uint8))  # 4-channel RGBA

    def test_downscaling_antialiasing_consistency(self):
        """Downscaling from high resolution (e.g. 1080p) produces valid finite (1, 3, 224, 224) float32 tensor."""
        hi_res = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # Add high-frequency thin line (e.g. insect leg/antennae)
        hi_res[540, :, :] = 255
        tensor = preprocess_frame_siglip(hi_res)

        assert tensor.shape == (1, 3, 224, 224)
        assert tensor.dtype == np.float32
        assert np.isfinite(tensor).all()
        # Ensure the feature was not completely erased
        assert np.max(tensor) > -1.0


# ==============================================================================
# 6. External Tensor Data & Model Asset Handling Tests
# ==============================================================================

class TestExternalTensorDataHandling:
    def test_missing_model_file_raises_error(self, tmp_path):
        """Missing .onnx file raises clean FileNotFoundError."""
        model_path = tmp_path / "vision_model_fp16.onnx"
        prompts_path = tmp_path / "prompt_embeddings.json"

        with pytest.raises(FileNotFoundError, match="SigLIP 2 ONNX vision model not found"):
            SigLIP2OnnxBackend(model_path=model_path, prompt_embeddings_path=prompts_path)

    def test_missing_external_tensor_data_sibling_raises_error(self, tmp_path):
        """Existing .onnx file missing its required .onnx_data sibling raises FileNotFoundError."""
        model_path = tmp_path / "vision_model_fp16.onnx"
        model_path.write_bytes(b"dummy onnx graph")
        prompts_path = tmp_path / "prompt_embeddings.json"

        with pytest.raises(FileNotFoundError, match="Required external tensor data file missing"):
            SigLIP2OnnxBackend(model_path=model_path, prompt_embeddings_path=prompts_path)

    def test_missing_prompt_embeddings_raises_error(self, tmp_path):
        """Existing .onnx and .onnx_data files missing prompt_embeddings.json raises FileNotFoundError."""
        model_path = tmp_path / "vision_model_fp16.onnx"
        model_path.write_bytes(b"dummy graph")
        data_path = tmp_path / "vision_model_fp16.onnx_data"
        data_path.write_bytes(b"dummy weights")
        prompts_path = tmp_path / "prompt_embeddings.json"

        with pytest.raises(FileNotFoundError, match="Prompt embeddings file not found"):
            SigLIP2OnnxBackend(model_path=model_path, prompt_embeddings_path=prompts_path)


# ==============================================================================
# 7. Score-to-Decision Threshold & Interval Merging Tests
# ==============================================================================

class TestIntervalMerging:
    def test_empty_frame_detections(self):
        """Empty detections yield empty interval events."""
        assert merge_frame_detections_to_intervals([]) == []

    def test_no_positive_frames_above_threshold(self):
        """Frames below threshold produce no merged intervals."""
        frames = [
            {"timestamp": 0.0, "confidence": 0.2},
            {"timestamp": 1.0, "confidence": 0.3},
            {"timestamp": 2.0, "confidence": 0.49},
        ]
        res = merge_frame_detections_to_intervals(frames, threshold=0.5)
        assert res == []

    def test_contiguous_positive_frames_merged(self):
        """Consecutive positive frames [1.0, 2.0, 3.0] merge into a single interval."""
        frames = [
            {"timestamp": 0.0, "confidence": 0.1},
            {"timestamp": 1.0, "confidence": 0.8},
            {"timestamp": 2.0, "confidence": 0.9},
            {"timestamp": 3.0, "confidence": 0.85},
            {"timestamp": 4.0, "confidence": 0.1},
        ]
        res = merge_frame_detections_to_intervals(
            frames,
            threshold=0.5,
            sample_interval=1.0,
            merge_gap_seconds=1.0,
            padding_before_seconds=0.0,
            padding_after_seconds=0.0,
        )
        assert len(res) == 1
        assert res[0]["start"] == 1.0
        assert res[0]["end"] == 4.0
        assert res[0]["trigger"] == "insect"
        assert res[0]["action"] == "hide"

    def test_gap_tolerance_merging(self):
        """Positive frames separated by gap <= merge_gap_seconds are merged."""
        frames = [
            {"timestamp": 1.0, "confidence": 0.8},
            {"timestamp": 3.0, "confidence": 0.8},
        ]
        res = merge_frame_detections_to_intervals(
            frames,
            threshold=0.5,
            sample_interval=1.0,
            merge_gap_seconds=2.0,
            padding_before_seconds=0.0,
            padding_after_seconds=0.0,
        )
        assert len(res) == 1
        assert res[0]["start"] == 1.0
        assert res[0]["end"] == 4.0

    def test_gap_tolerance_splits_distant_events(self):
        """Positive frames separated by gap > merge_gap_seconds remain separate events."""
        frames = [
            {"timestamp": 1.0, "confidence": 0.8},
            {"timestamp": 5.0, "confidence": 0.8},
        ]
        res = merge_frame_detections_to_intervals(
            frames,
            threshold=0.5,
            sample_interval=1.0,
            merge_gap_seconds=2.0,
            padding_before_seconds=0.0,
            padding_after_seconds=0.0,
        )
        assert len(res) == 2
        assert res[0]["start"] == 1.0
        assert res[0]["end"] == 2.0
        assert res[1]["start"] == 5.0
        assert res[1]["end"] == 6.0

    def test_padding_and_clamping(self):
        """Padding expands interval start and end, clamped to [0.0, media_duration]."""
        frames = [
            {"timestamp": 0.5, "confidence": 0.9},
        ]
        res = merge_frame_detections_to_intervals(
            frames,
            threshold=0.5,
            sample_interval=1.0,
            padding_before_seconds=1.0,
            padding_after_seconds=1.5,
            media_duration_seconds=10.0,
        )
        assert len(res) == 1
        assert res[0]["start"] == 0.0
        assert res[0]["end"] == 3.0

    def test_merge_intervals_filters_non_finite_frames(self):
        """Frames with NaN/Inf timestamps or confidences are ignored."""
        frames = [
            {"timestamp": float("nan"), "confidence": 0.9},
            {"timestamp": 1.0, "confidence": float("inf")},
            {"timestamp": 2.0, "confidence": 0.8},
        ]
        res = merge_frame_detections_to_intervals(
            frames,
            threshold=0.5,
            sample_interval=1.0,
            padding_before_seconds=0.0,
            padding_after_seconds=0.0,
        )
        assert len(res) == 1
        assert res[0]["start"] == 2.0
        assert res[0]["end"] == 3.0


# ==============================================================================
# 8. Video Error Handling Tests
# ==============================================================================

class TestVideoErrorHandling:
    def test_missing_video_probe_raises_error(self):
        """probe_video_metadata raises FileNotFoundError on missing file."""
        with pytest.raises(FileNotFoundError, match="Video file not found"):
            probe_video_metadata(Path("/tmp/nonexistent_video.webm"))

    def test_missing_video_sampling_raises_error(self):
        """sample_video_frames raises FileNotFoundError on missing file."""
        with pytest.raises(FileNotFoundError, match="Video file not found"):
            next(sample_video_frames(Path("/tmp/nonexistent_video.webm")))


# ==============================================================================
# 9. Prediction Pipeline & Explicit Mock vs Real Tests
# ==============================================================================

class TestPredictionPipeline:
    def test_mock_pipeline_prediction_schema_compliance(self, tmp_path):
        """Mock prediction output strictly adheres to schema/insect_prediction.schema.json."""
        frame_dets = [
            {"timestamp": 0.0, "confidence": 0.1, "decision": False},
            {"timestamp": 1.0, "confidence": 0.85, "decision": True},
            {"timestamp": 2.0, "confidence": 0.90, "decision": True},
            {"timestamp": 3.0, "confidence": 0.2, "decision": False},
        ]
        events = merge_frame_detections_to_intervals(
            frame_dets,
            threshold=0.5,
            sample_interval=1.0,
            padding_before_seconds=0.5,
            padding_after_seconds=0.5,
            media_duration_seconds=5.0,
        )

        payload = {
            "schema_version": 1,
            "model_id": "mock_pipeline_test",
            "model_version": "2.1.0-test",
            "default_threshold": 0.5,
            "ground_truth_status": "unreviewed",
            "prompt_configuration": DEFAULT_PROMPT_CONFIG,
            "predictions": [
                {
                    "clip_id": "test_clip_01",
                    "media_duration_seconds": 5.0,
                    "sampled_frames_count": len(frame_dets),
                    "threshold": 0.5,
                    "interval_events": events,
                    "frame_detections": frame_dets,
                }
            ],
        }

        # Validate with evaluate_insect validator
        validate_prediction_data(payload)

    def test_real_inference_fails_clearly_without_weights(self, tmp_path):
        """Predict without --mock fails with exit code 1 when real weights are missing."""
        args = argparse.Namespace(
            output=tmp_path / "out.json",
            overwrite=True,
            mock=False,
            mock_confidence=None,
            input=None,
            model_path=tmp_path / "nonexistent_model.onnx",
            prompt_embeddings=tmp_path / "nonexistent_embeddings.json",
            fps=1.0,
            threshold=0.5,
            merge_gap=2.0,
            apply_distractor_suppression=False,
        )
        ret = cmd_predict(args)
        assert ret == 1
        assert not (tmp_path / "out.json").exists()

    def test_refusal_to_overwrite_existing_file(self, tmp_path):
        """CLI predict refuses to overwrite existing file unless --overwrite is set."""
        out_file = tmp_path / "predictions.json"
        out_file.write_text("existing content", encoding="utf-8")

        args = argparse.Namespace(
            output=out_file,
            overwrite=False,
            mock=True,
            mock_confidence=0.5,
            input=None,
            model_path=None,
            prompt_embeddings=None,
            fps=1.0,
            threshold=0.5,
            merge_gap=2.0,
            apply_distractor_suppression=False,
        )

        ret = cmd_predict(args)
        assert ret == 1
        assert out_file.read_text(encoding="utf-8") == "existing content"

    def test_invalid_non_finite_confidence_raises_error(self, tmp_path):
        """run_insect_detection_on_video rejects non-finite confidence from backend."""
        import cv2

        test_video = tmp_path / "dummy.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(test_video), fourcc, 10.0, (224, 224))
        for _ in range(5):
            writer.write(np.zeros((224, 224, 3), dtype=np.uint8))
        writer.release()

        bad_backend = MagicMock()
        bad_backend.predict_frame.return_value = {"confidence": float("nan")}

        with pytest.raises(ValueError, match="Invalid confidence score from detector backend"):
            run_insect_detection_on_video(test_video, backend=bad_backend)


# ==============================================================================
# 10. Real-Model Smoke Tests (Non-Mocked ONNX Execution)
# ==============================================================================

HAS_REAL_MODEL_ASSETS = (
    DEFAULT_VISION_MODEL_FP16.is_file()
    and (DEFAULT_MODEL_DIR / "vision_model_fp16.onnx_data").is_file()
    and (DEFAULT_MODEL_DIR / "text_model_fp16.onnx").is_file()
    and (DEFAULT_MODEL_DIR / "text_model_fp16.onnx_data").is_file()
    and (DEFAULT_MODEL_DIR / "tokenizer.json").is_file()
)

real_model_skip = pytest.mark.skipif(
    not HAS_REAL_MODEL_ASSETS,
    reason="Real SigLIP 2 ONNX model weights or tokenizer not available in model staging",
)


@real_model_skip
class TestRealModelSmokeTests:
    """Non-mocked smoke tests executing actual SigLIP 2 ONNX models and tokenizer."""

    def test_real_tokenizer_with_actual_vocab(self):
        """Real tokenizer loads genuine Gemma BPE vocabulary and formats sequences."""
        adapter = SigLIP2TokenizerAdapter(tokenizer_path=DEFAULT_MODEL_DIR / "tokenizer.json")

        prompts = [
            "a photo of an insect",
            "a close-up photo of a crawling bug or beetle",
        ]
        batch = adapter.batch_encode(prompts)

        assert isinstance(batch, np.ndarray)
        assert batch.shape == (2, 64)
        assert batch.dtype == np.int64
        # Verify EOS is appended and padded with 0
        for i in range(2):
            seq = batch[i].tolist()
            assert 1 in seq  # EOS token
            eos_pos = seq.index(1)
            assert all(t == 0 for t in seq[eos_pos + 1:])

    def test_real_text_encoder_execution(self):
        """Real text encoder executes ONNX graph and outputs normalized 768-dim embeddings."""
        adapter = SigLIP2TokenizerAdapter(tokenizer_path=DEFAULT_MODEL_DIR / "tokenizer.json")
        encoder = SigLIP2TextEncoder(model_path=DEFAULT_MODEL_DIR / "text_model_fp16.onnx")

        prompts = ["a photo of an insect", "a photo of a spider on a web"]
        embeds, prompt_map = encoder.encode_prompts(prompts, adapter)

        assert embeds.shape == (2, 768)
        assert embeds.dtype == np.float32
        assert np.isfinite(embeds).all()

        # Check unit L2-norm
        norms = np.linalg.norm(embeds, axis=-1)
        assert np.allclose(norms, 1.0, atol=1e-4)

        assert "a photo of an insect" in prompt_map
        assert len(prompt_map["a photo of an insect"]) == 768

    def test_real_vision_encoder_frame_prediction(self):
        """Real vision tower scores synthetic frame against real prompt embeddings."""
        backend = SigLIP2OnnxBackend(
            model_path=DEFAULT_VISION_MODEL_FP16,
            prompt_embeddings_path=DEFAULT_PROMPT_EMBEDDINGS,
            expected_positive_prompts=DEFAULT_PROMPT_CONFIG["positive_prompts"],
            expected_negative_prompts=DEFAULT_PROMPT_CONFIG["negative_prompts"],
        )

        dummy_frame = np.full((480, 640, 3), 120, dtype=np.uint8)
        res = backend.predict_frame(dummy_frame)

        assert 0.0 <= res["confidence"] <= 1.0
        raw = res["raw_scores"]
        assert "positive_cosine_max" in raw
        assert "negative_cosine_max" in raw
        assert "native_sigmoid_max" in raw
        expected_total_prompts = len(DEFAULT_PROMPT_CONFIG["positive_prompts"]) + len(DEFAULT_PROMPT_CONFIG["negative_prompts"])
        assert len(raw["per_prompt_scores"]) == expected_total_prompts

    def test_real_model_synthetic_video_pipeline(self, tmp_path):
        """Real ONNX model processes synthetic video and yields schema-compliant output."""
        import cv2

        test_video = tmp_path / "smoke_test.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(test_video), fourcc, 10.0, (224, 224))
        for i in range(20):
            frame = np.full((224, 224, 3), (i * 10, 100, 200), dtype=np.uint8)
            writer.write(frame)
        writer.release()

        backend = SigLIP2OnnxBackend(
            model_path=DEFAULT_VISION_MODEL_FP16,
            prompt_embeddings_path=DEFAULT_PROMPT_EMBEDDINGS,
        )

        res = run_insect_detection_on_video(
            video_path=test_video,
            backend=backend,
            target_fps=2.0,
            threshold=0.5,
        )

        assert res["clip_id"] == "smoke_test"
        assert res["sampled_frames_count"] == 4
        assert isinstance(res["interval_events"], list)
        assert res["processing_time_seconds"] > 0.0

        payload = {
            "schema_version": 1,
            "model_id": "siglip2_base_patch16_224_onnx",
            "model_version": "2.0.0-prototype",
            "default_threshold": 0.5,
            "ground_truth_status": "unreviewed",
            "prompt_configuration": DEFAULT_PROMPT_CONFIG,
            "predictions": [res],
        }
        validate_prediction_data(payload)


# ==============================================================================
# 9. Configurable Decision Policies & Candidate Verification Tests
# ==============================================================================

class TestConfigurableDecisionPolicies:
    def test_production_policy_threshold_semantics(self):
        """Production policy strictly evaluates conf >= threshold (default 0.50)."""
        raw_scores = {"positive_cosine_max": 0.14, "negative_cosine_max": 0.08}

        # Above 0.50
        dec_high, meta_high = evaluate_frame_decision(0.55, raw_scores, threshold=0.50, decision_policy="production")
        assert dec_high is True
        assert meta_high["policy"] == "production"
        assert meta_high["decision"] is True

        # Below 0.50
        dec_low, meta_low = evaluate_frame_decision(0.35, raw_scores, threshold=0.50, decision_policy="production")
        assert dec_low is False
        assert meta_low["policy"] == "production"
        assert meta_low["decision"] is False

    def test_relative_margin_clearly_positive_emits_event(self):
        """Clearly positive insect frames with strong margin emit valid interval events."""
        # Simulated sequence of positive frames matching our real insect detections
        frames = []
        for i in range(5):
            t = float(i) * 0.25
            raw = {"positive_cosine_max": 0.143, "negative_cosine_max": 0.078}
            dec, meta = evaluate_frame_decision(0.35, raw, decision_policy="relative_margin", margin_threshold=0.03, min_confidence=0.10)
            assert dec is True
            raw["decision_policy"] = meta
            frames.append({
                "timestamp": t,
                "confidence": 0.35,
                "decision": dec,
                "raw_scores": raw,
            })

        events = merge_frame_detections_to_intervals(
            frames,
            sample_interval=0.25,
            media_duration_seconds=10.0,
            min_consecutive_frames=2,
        )
        assert len(events) == 1
        ev = events[0]
        assert ev["start"] == 0.0  # padded
        assert ev["end"] > 1.0
        assert ev["trigger"] == "insect"
        assert ev["action"] == "hide"
        assert ev["confidence"] == 0.35

    def test_relative_margin_clearly_negative_background_emits_zero_events(self):
        """Negative background frames with near-zero or negative margin emit no events."""
        frames = []
        for i in range(5):
            t = float(i) * 0.25
            # Negative control: positive similarity is lower than or comparable to negative distractors
            raw = {"positive_cosine_max": 0.052, "negative_cosine_max": 0.066}
            dec, meta = evaluate_frame_decision(0.0001, raw, decision_policy="relative_margin", margin_threshold=0.03, min_confidence=0.10)
            assert dec is False
            raw["decision_policy"] = meta
            frames.append({
                "timestamp": t,
                "confidence": 0.0001,
                "decision": dec,
                "raw_scores": raw,
            })

        events = merge_frame_detections_to_intervals(
            frames,
            sample_interval=0.25,
            media_duration_seconds=10.0,
        )
        assert events == []

    def test_relative_margin_borderline_cases(self):
        """Borderline cases test threshold boundaries precisely."""
        # Case 1: Exactly at margin threshold and min confidence -> True
        raw1 = {"positive_cosine_max": 0.110, "negative_cosine_max": 0.080}
        dec1, _ = evaluate_frame_decision(0.100, raw1, decision_policy="relative_margin", margin_threshold=0.03, min_confidence=0.10)
        assert dec1 is True

        # Case 2: Margin slightly below threshold (0.029 < 0.030) -> False
        raw2 = {"positive_cosine_max": 0.109, "negative_cosine_max": 0.080}
        dec2, _ = evaluate_frame_decision(0.250, raw2, decision_policy="relative_margin", margin_threshold=0.03, min_confidence=0.10)
        assert dec2 is False

        # Case 3: Margin well above threshold, but confidence floor failed (0.08 < 0.10) -> False
        raw3 = {"positive_cosine_max": 0.140, "negative_cosine_max": 0.070}
        dec3, _ = evaluate_frame_decision(0.080, raw3, decision_policy="relative_margin", margin_threshold=0.03, min_confidence=0.10)
        assert dec3 is False

    def test_temporal_confirmation_filters_isolated_spikes(self):
        """min_consecutive_frames filters out isolated single-frame noise spikes."""
        # Frame at 1.0s and frame at 8.0s (isolated)
        frames = [
            {"timestamp": 1.0, "confidence": 0.40, "decision": True},
            {"timestamp": 8.0, "confidence": 0.40, "decision": True},
        ]
        # With min_consecutive_frames=2, both isolated frames are discarded
        filtered = merge_frame_detections_to_intervals(
            frames,
            sample_interval=0.5,
            merge_gap_seconds=1.0,
            min_consecutive_frames=2,
        )
        assert filtered == []

        # When two consecutive frames occur at 1.0s and 1.5s
        frames_consecutive = [
            {"timestamp": 1.0, "confidence": 0.40, "decision": True},
            {"timestamp": 1.5, "confidence": 0.40, "decision": True},
        ]
        accepted = merge_frame_detections_to_intervals(
            frames_consecutive,
            sample_interval=0.5,
            merge_gap_seconds=1.0,
            min_consecutive_frames=2,
        )
        assert len(accepted) == 1

    def test_unsupported_decision_policy_raises_error(self):
        """Unsupported policy string raises ValueError with actionable message."""
        with pytest.raises(ValueError, match="Unsupported decision_policy"):
            evaluate_frame_decision(0.5, {}, decision_policy="non_existent_policy")

    def test_candidate_policy_schema_backward_compatibility(self, tmp_path):
        """Inference with relative_margin policy outputs fully schema-compliant predictions."""
        import cv2
        test_video = tmp_path / "candidate_test.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(test_video), fourcc, 10.0, (224, 224))
        for i in range(15):
            writer.write(np.full((224, 224, 3), 100, dtype=np.uint8))
        writer.release()

        backend = SigLIP2OnnxBackend(
            model_path=DEFAULT_VISION_MODEL_FP16,
            prompt_embeddings_path=DEFAULT_PROMPT_EMBEDDINGS,
        )

        res = run_insect_detection_on_video(
            video_path=test_video,
            backend=backend,
            target_fps=2.0,
            decision_policy="relative_margin",
            margin_threshold=0.03,
            min_confidence=0.10,
        )

        assert "frame_detections" in res
        for fr in res["frame_detections"]:
            assert "decision" in fr
            assert "decision_policy" in fr["raw_scores"]
            assert fr["raw_scores"]["decision_policy"]["policy"] == "relative_margin"

        payload = {
            "schema_version": 1,
            "model_id": "siglip2_base_patch16_224_onnx",
            "model_version": "2.0.0-prototype",
            "default_threshold": 0.5,
            "ground_truth_status": "unreviewed",
            "prompt_configuration": DEFAULT_PROMPT_CONFIG,
            "notes": "Testing relative_margin candidate policy",
            "predictions": [res],
        }
        validate_prediction_data(payload)

    def test_actual_selected_default_policy(self):
        """The default policy is relative_margin and is applied when no policy is explicitly passed."""
        assert DEFAULT_DECISION_POLICY == "relative_margin"
        raw = {"positive_cosine_max": 0.143, "negative_cosine_max": 0.080}
        dec, meta = evaluate_frame_decision(0.35, raw)
        assert meta["policy"] == "relative_margin"
        assert dec is True

    def test_deterministic_four_control_scenes(self):
        """Deterministic control scenes (wall, dark room, foliage, bark) evaluate to False."""
        # Simulated scores from our 4 verified control scenes
        controls = [
            ("Clean Wall", {"positive_cosine_max": 0.0553, "negative_cosine_max": 0.0634}, 0.0000),
            ("Dark Room", {"positive_cosine_max": 0.0624, "negative_cosine_max": 0.0548}, 0.0001),
            ("Foliage Scene", {"positive_cosine_max": 0.0473, "negative_cosine_max": 0.0579}, 0.0000),
            ("Bark Texture", {"positive_cosine_max": 0.0439, "negative_cosine_max": 0.0368}, 0.0000),
        ]
        for name, raw, conf in controls:
            dec, meta = evaluate_frame_decision(conf, raw, decision_policy="relative_margin")
            assert dec is False, f"Control scene {name} falsely triggered with margin={meta['margin']}"
