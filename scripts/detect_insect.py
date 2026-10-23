#!/usr/bin/env python3
"""Offline Visual Insect Detector Prototype.

Provides:
1. Configurable video frame sampling (cv2 VideoCapture streaming without saving frames).
2. Vision-language prompt configuration (positive insect anchors vs. negative distractors).
3. Pluggable backend architecture:
   - SigLIP2OnnxBackend: Real ONNX Runtime inference using Google SigLIP 2 vision encoder.
   - MockInsectDetectionBackend: Deterministic mock backend for testing and offline verification.
4. Temporal interval merging: merges frame-level decisions into half-open [start, end) cues.
5. Schema-compliant prediction export conforming to schema/insect_prediction.schema.json.
6. CLI commands: inspect-model, inspect-media, predict, validate-predictions.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
STAGING_DIR = Path("/Users/zoya/Desktop/insect_benchmark_staging")
DEFAULT_PREDICTIONS_DIR = STAGING_DIR / "predictions"
DEFAULT_PREDICTION_SCHEMA = REPO_ROOT / "schema" / "insect_prediction.schema.json"

DEFAULT_MODEL_STAGING_DIR = Path("/Users/zoya/Desktop/insect_model_staging")
DEFAULT_MODEL_DIR = DEFAULT_MODEL_STAGING_DIR
DEFAULT_VISION_MODEL_FP16 = DEFAULT_MODEL_DIR / "vision_model_fp16.onnx"
DEFAULT_VISION_MODEL_FP32 = DEFAULT_MODEL_DIR / "vision_model.onnx"
DEFAULT_PROMPT_EMBEDDINGS = DEFAULT_MODEL_DIR / "prompt_embeddings.json"

# Upstream SigLIP 2 Export Constants (verified from export_meta.json & tokenizer_config.json)
DEFAULT_LOGIT_SCALE = 112.66890226978423
DEFAULT_LOGIT_BIAS = -16.771724700927734
DEFAULT_EMBED_DIM = 768
DEFAULT_TEXT_MAX_LENGTH = 64
DEFAULT_PAD_TOKEN_ID = 0
DEFAULT_EOS_TOKEN_ID = 1
DEFAULT_BOS_TOKEN_ID = 2
DEFAULT_UNK_TOKEN_ID = 3

DEFAULT_SAMPLING_FPS = 1.0
DEFAULT_CONFIDENCE_THRESHOLD = 0.50
DEFAULT_MERGE_GAP_SECONDS = 2.0
DEFAULT_MIN_DURATION_SECONDS = 0.5
DEFAULT_PADDING_BEFORE_SECONDS = 1.0
DEFAULT_PADDING_AFTER_SECONDS = 1.5

# Configurable Decision Policy Constants
# 'relative_margin' is the default operating policy for the insect detector:
# Gated by contrastive margin (positive cosine - negative distractor cosine >= 0.03)
# and minimum confidence floor (>= 0.10). 'production' policy remains available for legacy comparison.
DEFAULT_DECISION_POLICY = "relative_margin"
SUPPORTED_DECISION_POLICIES = ("production", "relative_margin", "calibrated_score")
DEFAULT_MARGIN_THRESHOLD = 0.03
DEFAULT_MIN_CONFIDENCE = 0.10
DEFAULT_MIN_CONSECUTIVE_FRAMES = 1


# ==============================================================================
# 1. Prompt Configuration
# ==============================================================================

DEFAULT_PROMPT_CONFIG: Dict[str, Any] = {
    "version": "2.1.0",
    "model_family": "SigLIP-2",
    "scoring_method": "siglip2_independent_sigmoid",
    "logit_scale": DEFAULT_LOGIT_SCALE,
    "logit_bias": DEFAULT_LOGIT_BIAS,
    "apply_distractor_suppression": False,
    "description": "Zero-shot visual insect classification using independent SigLIP 2 sigmoid scoring over multi-anchor positive and negative text embeddings",
    "positive_prompts": [
        "a photo of an insect",
        "a close-up photo of a crawling bug or beetle",
        "a photo of a flying wasp or bee",
        "a dense swarm of insects flying in the air",
        "a photo of a mosquito, midge, or fly",
        "a photo of a cockroach on the floor",
        "a macro photo of an insect",
        "a small insect",
        "a small crawling insect on a surface",
        "a bug crawling",
        "a photo of an insect on a green leaf or plant",
        "a bug resting on foliage or vegetation",
        "a small insect on a garden plant",
        "a close-up of an insect on tree bark",
        "a bug on rough natural stone or wood",
        "a detailed macro view of an insect showing legs and antennae",
        "a close-up photo of insect antennae and head",
        "a close-up of a segmented insect body",
        "a photo of a dragonfly or damselfly perched",
        "a true bug perched on vegetation",
    ],
    "negative_prompts": [
        "a photo of an empty indoor room with clean walls",
        "a photo of people talking in a cinematic movie",
        "a photo of a spider on a web",
        "a close-up photo of rough tree bark and wood grain",
        "a photo of green tree leaves and branches",
        "a photo of autumn leaves falling in the wind",
        "a photo of atmospheric dust particles and sparks floating in the dark",
    ],
}


# ==============================================================================
# 2. Frame Sampler (Streaming Video Decoder)
# ==============================================================================

def probe_video_metadata(video_path: Path) -> Dict[str, Any]:
    """Probe video duration, dimensions, and FPS using cv2."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV (cv2) is required for video probing.") from exc

    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video file with OpenCV: {video_path}")

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        duration = (total_frames / fps) if (fps > 0 and total_frames > 0) else 0.0

        return {
            "path": str(video_path),
            "filename": video_path.name,
            "width": width,
            "height": height,
            "fps": round(fps, 3),
            "total_frames": total_frames,
            "duration_seconds": round(duration, 3),
        }
    finally:
        cap.release()


def sample_video_frames(
    video_path: Path,
    target_fps: float = DEFAULT_SAMPLING_FPS,
) -> Generator[Tuple[float, Any], None, None]:
    """Streamingly sample frames at target_fps from video without saving to disk.

    Yields:
        (timestamp_seconds, frame_bgr_numpy_array)

    Guarantees:
        - Memory safe: yields one frame at a time and releases references immediately.
        - VideoCapture is released upon normal termination or exception.
    """
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV (cv2) is required for video frame sampling.") from exc

    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {video_path}")

    try:
        native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
        if native_fps <= 0.0 or not math.isfinite(native_fps):
            native_fps = 24.0

        sample_step = 1.0 / target_fps if (target_fps > 0 and math.isfinite(target_fps)) else 1.0
        frame_interval = max(1, int(round(native_fps * sample_step)))

        last_ts = -1.0
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_interval == 0:
                # Use presentation timestamp from OpenCV if available, else derive from frame index
                pos_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
                calc_ts = round(float(frame_idx) / native_fps, 3)
                if pos_msec is not None and math.isfinite(pos_msec) and pos_msec > 0.0:
                    pts_ts = round(float(pos_msec) / 1000.0, 3)
                    ts = pts_ts if pts_ts >= last_ts else calc_ts
                else:
                    ts = calc_ts

                ts = max(0.0, ts)
                last_ts = ts
                yield (ts, frame)

            frame_idx += 1
    finally:
        cap.release()


# ==============================================================================
# 3. Vision-Language Detection Backends & Preprocessing
# ==============================================================================

def preprocess_frame_siglip(frame_bgr: Any) -> Any:
    """Preprocess a video frame for SigLIP 2 vision tower according to official specifications.

    Steps:
    1. Validate non-empty 3-channel BGR image.
    2. Convert BGR to RGB.
    3. Resize to 224x224 using antialiased area decimation (cv2.INTER_AREA) when downscaling
       to preserve high-frequency details (delicate insect legs/antennae), matching upstream
       SigLIP 2 reference specification (bilinear/antialiased when downscaling; resample=2),
       and cv2.INTER_LINEAR when upscaling.
    4. Rescale pixel values: (x / 255.0 - 0.5) / 0.5 -> [-1.0, 1.0].
    5. Transpose from HWC to CHW and add batch dimension -> shape (1, 3, 224, 224), float32.
    """
    if frame_bgr is None:
        raise ValueError("Cannot preprocess None frame.")

    import numpy as np

    if not isinstance(frame_bgr, np.ndarray) or frame_bgr.size == 0:
        raise ValueError("Invalid or empty frame array provided for preprocessing.")

    if len(frame_bgr.shape) != 3 or frame_bgr.shape[2] != 3:
        raise ValueError(
            f"Expected 3-channel BGR frame of shape (H, W, 3), got shape: {frame_bgr.shape}"
        )

    if not np.isfinite(frame_bgr).all():
        raise ValueError("Input frame contains non-finite values (NaN or Inf).")

    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV (cv2) is required for image preprocessing.") from exc

    # Convert BGR -> RGB
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

    # Resize to 224x224 matching SigLIP 2 specification (antialiased decimation when downscaling)
    h, w = rgb.shape[:2]
    interp = cv2.INTER_AREA if (h > 224 or w > 224) else cv2.INTER_LINEAR
    resized = cv2.resize(rgb, (224, 224), interpolation=interp)

    # Rescale to [-1.0, 1.0]: (x / 127.5) - 1.0
    norm_img = ((resized.astype(np.float32) / 255.0) - 0.5) / 0.5

    # Channels-first layout: (1, 3, 224, 224)
    tensor = np.ascontiguousarray(
        np.transpose(norm_img, (2, 0, 1))[np.newaxis, ...],
        dtype=np.float32,
    )
    return tensor


def format_prompt_tokens(
    token_ids: List[int],
    eos_token_id: int = DEFAULT_EOS_TOKEN_ID,
    pad_token_id: int = DEFAULT_PAD_TOKEN_ID,
    max_length: int = DEFAULT_TEXT_MAX_LENGTH,
) -> List[int]:
    """Format token sequence according to SigLIP 2 Gemma text contract.

    Steps:
    1. Append <eos> token (token_id=eos_token_id) if not already the final token.
    2. Truncate to max_length (64) if sequence exceeds length.
    3. Pad with <pad> (token_id=pad_token_id, 0) up to max_length (64).
    """
    tokens = [int(t) for t in token_ids]
    if not tokens or tokens[-1] != eos_token_id:
        tokens.append(eos_token_id)

    if len(tokens) > max_length:
        tokens = tokens[:max_length]
    elif len(tokens) < max_length:
        tokens.extend([pad_token_id] * (max_length - len(tokens)))

    return tokens


def compute_siglip2_scores(
    img_embed: Any,
    pos_embeds: Any,
    neg_embeds: Any,
    positive_prompts: Optional[List[str]] = None,
    negative_prompts: Optional[List[str]] = None,
    logit_scale: float = DEFAULT_LOGIT_SCALE,
    logit_bias: float = DEFAULT_LOGIT_BIAS,
    apply_distractor_suppression: bool = False,
) -> Dict[str, Any]:
    """Compute independent SigLIP 2 sigmoid probabilities and raw cosine similarities per label.

    Upstream SigLIP 2 scoring contract:
        cosine_i = dot(normalize(image), normalize(text_i))
        logit_i = cosine_i * logit_scale + logit_bias
        prob_i = sigmoid(logit_i) = 1 / (1 + exp(-logit_i))

    Properties:
    - Labels are evaluated independently; probabilities do NOT sum to 1.
    - Softmax is NOT applied across labels.
    - Native sigmoid score is strictly distinguished from any optional heuristic.

    Returns:
        Dict containing:
        - confidence: float in [0.0, 1.0] (unmodified native SigLIP 2 score by default)
        - raw_scores: complete breakdown of cosines, logits, sigmoids, and distractor heuristic
    """
    import numpy as np

    if img_embed is None or pos_embeds is None or neg_embeds is None:
        raise ValueError("Embeddings cannot be None.")

    # Ensure 2D arrays (B, D)
    if img_embed.ndim == 1:
        img_embed = img_embed[np.newaxis, :]
    if pos_embeds.ndim == 1:
        pos_embeds = pos_embeds[np.newaxis, :]
    if neg_embeds.ndim == 1:
        neg_embeds = neg_embeds[np.newaxis, :]

    if not np.isfinite(img_embed).all():
        raise ValueError("Image embedding contains non-finite values (NaN or Inf).")
    if not np.isfinite(pos_embeds).all() or not np.isfinite(neg_embeds).all():
        raise ValueError("Prompt embeddings contain non-finite values (NaN or Inf).")

    # L2 normalize image embedding
    img_norm = np.linalg.norm(img_embed, axis=-1, keepdims=True)
    if float(img_norm[0, 0]) == 0.0:
        raise ValueError("Image embedding has zero Euclidean norm.")
    img_embed = img_embed / img_norm

    # L2 normalize prompt embeddings
    pos_norm = np.linalg.norm(pos_embeds, axis=-1, keepdims=True)
    neg_norm = np.linalg.norm(neg_embeds, axis=-1, keepdims=True)
    if (pos_norm == 0.0).any() or (neg_norm == 0.0).any():
        raise ValueError("Prompt embedding contains zero-norm vector.")
    pos_embeds = pos_embeds / pos_norm
    neg_embeds = neg_embeds / neg_norm

    # Compute raw cosine similarities: dot product of normalized vectors
    pos_cosines = np.dot(img_embed, pos_embeds.T)[0]
    neg_cosines = np.dot(img_embed, neg_embeds.T)[0]

    # Compute native SigLIP 2 logits and sigmoid probabilities per label independently
    pos_logits = pos_cosines * logit_scale + logit_bias
    neg_logits = neg_cosines * logit_scale + logit_bias

    # Clamp logits to prevent numerical overflow in exp
    pos_logits_clamped = np.clip(pos_logits, -80.0, 80.0)
    neg_logits_clamped = np.clip(neg_logits, -80.0, 80.0)

    pos_sigmoids = 1.0 / (1.0 + np.exp(-pos_logits_clamped))
    neg_sigmoids = 1.0 / (1.0 + np.exp(-neg_logits_clamped))

    max_pos_cos = float(np.max(pos_cosines))
    max_neg_cos = float(np.max(neg_cosines))
    max_pos_logit = float(np.max(pos_logits))
    max_neg_logit = float(np.max(neg_logits))
    native_sigmoid_max = float(np.max(pos_sigmoids))
    negative_sigmoid_max = float(np.max(neg_sigmoids))

    # Optional, clearly-separated distractor-suppression heuristic
    if negative_sigmoid_max > native_sigmoid_max:
        excess = negative_sigmoid_max - native_sigmoid_max
        heuristic_score = max(0.0, native_sigmoid_max * (1.0 - min(1.0, excess)))
    else:
        heuristic_score = native_sigmoid_max

    # Confidence: by default, native_sigmoid_max (unmodified SigLIP score)
    confidence = heuristic_score if apply_distractor_suppression else native_sigmoid_max
    confidence = max(0.0, min(1.0, round(confidence, 4)))

    per_prompt_scores: Dict[str, Any] = {}
    if positive_prompts and len(positive_prompts) == len(pos_cosines):
        for p_text, c_val, l_val, s_val in zip(positive_prompts, pos_cosines, pos_logits, pos_sigmoids):
            per_prompt_scores[p_text] = {
                "cosine": round(float(c_val), 4),
                "logit": round(float(l_val), 4),
                "sigmoid": round(float(s_val), 4),
                "type": "positive",
            }
    if negative_prompts and len(negative_prompts) == len(neg_cosines):
        for n_text, c_val, l_val, s_val in zip(negative_prompts, neg_cosines, neg_logits, neg_sigmoids):
            per_prompt_scores[n_text] = {
                "cosine": round(float(c_val), 4),
                "logit": round(float(l_val), 4),
                "sigmoid": round(float(s_val), 4),
                "type": "negative",
            }

    return {
        "confidence": confidence,
        "raw_scores": {
            "positive_cosine_max": round(max_pos_cos, 4),
            "negative_cosine_max": round(max_neg_cos, 4),
            "positive_logit_max": round(max_pos_logit, 4),
            "negative_logit_max": round(max_neg_logit, 4),
            "native_sigmoid_max": round(native_sigmoid_max, 4),
            "negative_sigmoid_max": round(negative_sigmoid_max, 4),
            "heuristic_distractor_suppressed_score": round(heuristic_score, 4),
            "distractor_suppression_applied": apply_distractor_suppression,
            "logit_scale": round(logit_scale, 4),
            "logit_bias": round(logit_bias, 4),
            "per_prompt_scores": per_prompt_scores,
        },
    }


def validate_and_load_prompt_embeddings(
    embeddings_path: Path,
    expected_positive_prompts: Optional[List[str]] = None,
    expected_negative_prompts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Validate and load prompt_embeddings.json, rejecting invalid or stale artifacts."""
    import numpy as np

    if not embeddings_path.is_file():
        raise FileNotFoundError(f"Prompt embeddings file not found: {embeddings_path}")

    with embeddings_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Prompt embeddings artifact at {embeddings_path} must be a JSON object.")

    if data.get("schema_version") != 1:
        raise ValueError(
            f"Unsupported prompt embeddings schema_version: {data.get('schema_version')} (expected 1)"
        )

    if data.get("embed_dim") != DEFAULT_EMBED_DIM:
        raise ValueError(
            f"Incompatible embed_dim: {data.get('embed_dim')} (expected {DEFAULT_EMBED_DIM})"
        )

    if "positive_embeddings" not in data or "negative_embeddings" not in data:
        raise ValueError("Prompt embeddings artifact must contain 'positive_embeddings' and 'negative_embeddings'.")

    pos_embeds = np.array(data["positive_embeddings"], dtype=np.float32)
    neg_embeds = np.array(data["negative_embeddings"], dtype=np.float32)

    if pos_embeds.ndim != 2 or pos_embeds.shape[1] != DEFAULT_EMBED_DIM:
        raise ValueError(f"Expected positive_embeddings shape (*, {DEFAULT_EMBED_DIM}), got {pos_embeds.shape}")
    if neg_embeds.ndim != 2 or neg_embeds.shape[1] != DEFAULT_EMBED_DIM:
        raise ValueError(f"Expected negative_embeddings shape (*, {DEFAULT_EMBED_DIM}), got {neg_embeds.shape}")

    if not np.isfinite(pos_embeds).all() or not np.isfinite(neg_embeds).all():
        raise ValueError("Prompt embeddings contain non-finite values (NaN or Inf).")

    # Stale artifact detection: check if prompt strings match expected prompt configuration
    if expected_positive_prompts is not None:
        saved_pos = data.get("positive_prompts")
        if saved_pos != expected_positive_prompts:
            raise ValueError(
                f"Stale prompt embeddings artifact: positive prompts in file do not match configured prompts.\n"
                f"File prompts ({len(saved_pos) if saved_pos else 0}): {saved_pos}\n"
                f"Configured prompts ({len(expected_positive_prompts)}): {expected_positive_prompts}"
            )

    if expected_negative_prompts is not None:
        saved_neg = data.get("negative_prompts")
        if saved_neg != expected_negative_prompts:
            raise ValueError(
                f"Stale prompt embeddings artifact: negative prompts in file do not match configured prompts.\n"
                f"File prompts ({len(saved_neg) if saved_neg else 0}): {saved_neg}\n"
                f"Configured prompts ({len(expected_negative_prompts)}): {expected_negative_prompts}"
            )

    return {
        "positive_embeddings": pos_embeds,
        "negative_embeddings": neg_embeds,
        "positive_prompts": data.get("positive_prompts", []),
        "negative_prompts": data.get("negative_prompts", []),
        "prompt_to_embedding": data.get("prompt_to_embedding", {}),
        "logit_scale": float(data.get("logit_scale", DEFAULT_LOGIT_SCALE)),
        "logit_bias": float(data.get("logit_bias", DEFAULT_LOGIT_BIAS)),
        "metadata": data,
    }


class SigLIP2TokenizerAdapter:
    """Tokenizer adapter for SigLIP 2 (Gemma BPE vocabulary).

    Enforces the upstream Gemma tokenizer contract:
    - Lowercase normalization: text.lower().strip()
    - Vocabulary: 256,000 Gemma BPE tokens
    - Sequence formatting: append <eos> (id=1), pad with <pad> (id=0) to fixed length 64
    - Output: int64 numpy array of shape (batch_size, 64)
    """

    def __init__(
        self,
        tokenizer_path: Optional[Path] = None,
        tokenizer_instance: Optional[Any] = None,
    ) -> None:
        self.tokenizer_path = Path(tokenizer_path or (DEFAULT_MODEL_DIR / "tokenizer.json"))
        self._tokenizer: Any = tokenizer_instance

        if self._tokenizer is None:
            self._init_tokenizer()

    def _init_tokenizer(self) -> None:
        if not self.tokenizer_path.is_file():
            raise FileNotFoundError(
                f"SigLIP 2 tokenizer file not found at: {self.tokenizer_path}\n"
                f"Prerequisite: tokenizer.json (~34.4 MB) must be downloaded from "
                f"'upshift/siglip2-base-patch16-224-onnx' into staging directory: {self.tokenizer_path.parent}."
            )

        try:
            from tokenizers import Tokenizer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "The 'tokenizers' Python package is required to execute local tokenization against 'tokenizer.json'. "
                "Install tokenizers in .venv_eval or provide a pre-instantiated tokenizer."
            ) from exc

        self._tokenizer = Tokenizer.from_file(str(self.tokenizer_path))

    def encode(self, text: str) -> List[int]:
        """Normalize, tokenize, and format a single prompt string to exactly 64 token IDs."""
        if not isinstance(text, str):
            raise TypeError(f"Prompt must be a string, got {type(text).__name__}: {text!r}")

        norm_text = text.lower().strip()
        if hasattr(self._tokenizer, "encode"):
            raw_ids = self._tokenizer.encode(norm_text).ids
        elif callable(self._tokenizer):
            raw_ids = self._tokenizer(norm_text)
        else:
            raise RuntimeError("Underlying tokenizer instance does not support .encode() or __call__().")

        return format_prompt_tokens(
            token_ids=raw_ids,
            eos_token_id=DEFAULT_EOS_TOKEN_ID,
            pad_token_id=DEFAULT_PAD_TOKEN_ID,
            max_length=DEFAULT_TEXT_MAX_LENGTH,
        )

    def batch_encode(self, texts: List[str]) -> Any:
        """Tokenize a list of prompt strings to an int64 numpy array of shape (len(texts), 64)."""
        import numpy as np

        if not texts:
            return np.empty((0, DEFAULT_TEXT_MAX_LENGTH), dtype=np.int64)

        sequences = [self.encode(t) for t in texts]
        return np.array(sequences, dtype=np.int64)


class SigLIP2TextEncoder:
    """ONNX Runtime text encoder for SigLIP 2 text tower.

    Accepts tokenized input_ids (B, 64) int64 and outputs L2-normalized text embeddings (B, 768) float32.
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        session: Optional[Any] = None,
    ) -> None:
        self.model_path = Path(model_path or (DEFAULT_MODEL_DIR / "text_model_fp16.onnx"))
        self.session: Any = session
        self.input_name: str = "input_ids"

        if self.session is None:
            self._init_session()
        else:
            inputs = self.session.get_inputs()
            if inputs:
                self.input_name = inputs[0].name

    def _init_session(self) -> None:
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"SigLIP 2 text model not found at: {self.model_path}\n"
                f"Prerequisite: text_model_fp16.onnx (~189 KB) and text_model_fp16.onnx_data (~564.8 MB) "
                f"must be downloaded from 'upshift/siglip2-base-patch16-224-onnx' into staging directory: {self.model_path.parent}."
            )

        data_sibling = self.model_path.parent / f"{self.model_path.name}_data"
        if not data_sibling.is_file():
            raise FileNotFoundError(
                f"SigLIP 2 external text tensor data missing: {data_sibling}\n"
                f"The text model requires '{self.model_path.name}_data' to initialize."
            )

        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "onnxruntime is not installed in the current environment. Use .venv_eval to execute inference."
            ) from exc

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(self.model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def encode(self, input_ids: Any) -> Any:
        """Run text ONNX graph on input_ids (B, 64) and return L2-normalized embeddings (B, 768)."""
        import numpy as np

        if not isinstance(input_ids, np.ndarray):
            input_ids = np.array(input_ids, dtype=np.int64)

        if input_ids.ndim != 2 or input_ids.shape[1] != DEFAULT_TEXT_MAX_LENGTH:
            raise ValueError(
                f"Expected input_ids shape (B, {DEFAULT_TEXT_MAX_LENGTH}), got {input_ids.shape}"
            )

        if input_ids.shape[0] == 0:
            return np.empty((0, DEFAULT_EMBED_DIM), dtype=np.float32)

        outputs = self.session.run(None, {self.input_name: input_ids.astype(np.int64)})
        embeds = outputs[0]

        if embeds.ndim != 2 or embeds.shape[1] != DEFAULT_EMBED_DIM:
            raise ValueError(
                f"Expected text encoder output shape (*, {DEFAULT_EMBED_DIM}), got {embeds.shape}"
            )

        if not np.isfinite(embeds).all():
            raise ValueError("Text encoder produced non-finite embedding values (NaN or Inf).")

        norms = np.linalg.norm(embeds, axis=-1, keepdims=True)
        if (norms == 0.0).any():
            raise ValueError("Text encoder produced zero-norm embedding vector.")

        # L2-normalize
        embeds_norm = (embeds / norms).astype(np.float32)
        return embeds_norm

    def encode_prompts(
        self,
        prompts: List[str],
        tokenizer: SigLIP2TokenizerAdapter,
    ) -> Tuple[Any, Dict[str, List[float]]]:
        """Tokenize and encode prompt strings, returning normalized embeddings and prompt-to-vector mapping."""
        input_ids = tokenizer.batch_encode(prompts)
        embeds = self.encode(input_ids)
        prompt_map = {p: embeds[i].tolist() for i, p in enumerate(prompts)}
        return embeds, prompt_map


def generate_prompt_embeddings(
    positive_prompts: List[str],
    negative_prompts: List[str],
    output_path: Path,
    text_model_path: Optional[Path] = None,
    tokenizer_path: Optional[Path] = None,
    tokenizer_fn: Optional[Any] = None,
    tokenizer_adapter: Optional[SigLIP2TokenizerAdapter] = None,
    text_encoder: Optional[SigLIP2TextEncoder] = None,
) -> Dict[str, Any]:
    """Generate static prompt embeddings for SigLIP 2 zero-shot inference.

    Requirements when weights are available:
    - Tokenizer: Gemma tokenizer (tokenizer.json, ~34.4 MB), lowercased, <eos> appended,
      padded with token ID 0 up to 64 tokens.
    - Text Model: text_model_fp16.onnx + text_model_fp16.onnx_data (~564.8 MB) producing (B, 768) float32 embeddings.

    Associates each embedding with its exact prompt text in the output JSON.
    Fails with actionable error if model files or tokenizer are not yet downloaded.
    """
    import numpy as np

    # Setup text encoder
    if text_encoder is not None:
        encoder = text_encoder
    else:
        mdl_path = text_model_path or (DEFAULT_MODEL_DIR / "text_model_fp16.onnx")
        encoder = SigLIP2TextEncoder(model_path=mdl_path)

    # Setup tokenizer adapter
    if tokenizer_adapter is not None:
        tok_adapter = tokenizer_adapter
    elif tokenizer_fn is not None:
        tok_adapter = SigLIP2TokenizerAdapter(tokenizer_instance=tokenizer_fn)
    else:
        tok_path = tokenizer_path or (DEFAULT_MODEL_DIR / "tokenizer.json")
        tok_adapter = SigLIP2TokenizerAdapter(tokenizer_path=tok_path)

    pos_embeds, pos_map = encoder.encode_prompts(positive_prompts, tok_adapter)
    neg_embeds, neg_map = encoder.encode_prompts(negative_prompts, tok_adapter)

    prompt_to_embedding = {**pos_map, **neg_map}

    payload = {
        "schema_version": 1,
        "model_family": "SigLIP-2",
        "model_id": "google/siglip2-base-patch16-224",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "embed_dim": DEFAULT_EMBED_DIM,
        "text_max_length": DEFAULT_TEXT_MAX_LENGTH,
        "pad_token_id": DEFAULT_PAD_TOKEN_ID,
        "eos_token_id": DEFAULT_EOS_TOKEN_ID,
        "logit_scale": DEFAULT_LOGIT_SCALE,
        "logit_bias": DEFAULT_LOGIT_BIAS,
        "normalized": True,
        "positive_prompts": list(positive_prompts),
        "negative_prompts": list(negative_prompts),
        "positive_embeddings": pos_embeds.tolist(),
        "negative_embeddings": neg_embeds.tolist(),
        "prompt_to_embedding": prompt_to_embedding,
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return {
        "status": "success",
        "output_path": str(output_path),
        "positive_count": len(positive_prompts),
        "negative_count": len(negative_prompts),
        "total_prompts": len(prompt_to_embedding),
    }


class BaseVisionLanguageBackend:
    """Abstract base class for insect vision-language backends."""

    def predict_frame(self, frame_bgr: Any) -> Dict[str, Any]:
        """Compute visual insect affinity for a single BGR frame.

        Returns:
            Dict containing:
            - confidence: float in [0.0, 1.0]
            - raw_scores: Dict with positive and negative scores
        """
        raise NotImplementedError


class SigLIP2OnnxBackend(BaseVisionLanguageBackend):
    """Real ONNX Runtime vision-language backend using SigLIP 2 ONNX.

    Requires:
    - Pretrained vision encoder: vision_model_fp16.onnx (or vision_model.onnx)
      PLUS its external tensor data: vision_model_fp16.onnx_data
    - Precomputed text embeddings: prompt_embeddings.json (768-dim)
    """

    def __init__(
        self,
        model_path: Path,
        prompt_embeddings_path: Path,
        logit_scale: float = DEFAULT_LOGIT_SCALE,
        logit_bias: float = DEFAULT_LOGIT_BIAS,
        apply_distractor_suppression: bool = False,
        expected_positive_prompts: Optional[List[str]] = None,
        expected_negative_prompts: Optional[List[str]] = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.prompt_embeddings_path = Path(prompt_embeddings_path)
        self.logit_scale = logit_scale
        self.logit_bias = logit_bias
        self.apply_distractor_suppression = apply_distractor_suppression
        self.expected_positive_prompts = expected_positive_prompts
        self.expected_negative_prompts = expected_negative_prompts
        self.session: Any = None
        self.pos_embeddings: Any = None
        self.neg_embeddings: Any = None
        self.positive_prompts: List[str] = []
        self.negative_prompts: List[str] = []

        self._check_and_load_assets()

    def _check_and_load_assets(self) -> None:
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"SigLIP 2 ONNX vision model not found at: {self.model_path}\n"
                f"Prerequisite: Download candidate model from 'upshift/siglip2-base-patch16-224-onnx' on Hugging Face "
                f"into external staging directory: {DEFAULT_MODEL_STAGING_DIR}."
            )

        # External tensor data check: vision_model_fp16.onnx_data / vision_model.onnx_data
        data_sibling = self.model_path.parent / f"{self.model_path.name}_data"
        if not data_sibling.is_file():
            raise FileNotFoundError(
                f"Required external tensor data file missing: {data_sibling}\n"
                f"The ONNX model '{self.model_path.name}' requires external tensor weights ({self.model_path.name}_data) "
                f"located alongside the graph file to initialize."
            )

        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "onnxruntime is not installed in the current environment. "
                "Use .venv_eval to execute inference."
            ) from exc

        # Validate and load prompt embeddings artifact
        if not self.prompt_embeddings_path.is_file():
            text_model = self.model_path.parent / "text_model_fp16.onnx"
            text_data = self.model_path.parent / "text_model_fp16.onnx_data"
            tokenizer_file = self.model_path.parent / "tokenizer.json"
            if text_model.is_file() and text_data.is_file() and tokenizer_file.is_file():
                generate_prompt_embeddings(
                    positive_prompts=self.expected_positive_prompts or DEFAULT_PROMPT_CONFIG["positive_prompts"],
                    negative_prompts=self.expected_negative_prompts or DEFAULT_PROMPT_CONFIG["negative_prompts"],
                    output_path=self.prompt_embeddings_path,
                    text_model_path=text_model,
                    tokenizer_path=tokenizer_file,
                )

        emb_data = validate_and_load_prompt_embeddings(
            self.prompt_embeddings_path,
            expected_positive_prompts=self.expected_positive_prompts,
            expected_negative_prompts=self.expected_negative_prompts,
        )

        self.pos_embeddings = emb_data["positive_embeddings"]
        self.neg_embeddings = emb_data["negative_embeddings"]
        self.positive_prompts = emb_data["positive_prompts"]
        self.negative_prompts = emb_data["negative_prompts"]
        self.logit_scale = emb_data.get("logit_scale", self.logit_scale)
        self.logit_bias = emb_data.get("logit_bias", self.logit_bias)

        # Initialize ONNX inference session
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(
            str(self.model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )

    def predict_frame(self, frame_bgr: Any) -> Dict[str, Any]:
        """Preprocess frame to 224x224 RGB, run ONNX vision tower, and score using SigLIP 2 semantics."""
        input_tensor = preprocess_frame_siglip(frame_bgr)

        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {input_name: input_tensor})
        img_embed = outputs[0]  # shape: (1, 768)

        return compute_siglip2_scores(
            img_embed=img_embed,
            pos_embeds=self.pos_embeddings,
            neg_embeds=self.neg_embeddings,
            positive_prompts=self.positive_prompts,
            negative_prompts=self.negative_prompts,
            logit_scale=self.logit_scale,
            logit_bias=self.logit_bias,
            apply_distractor_suppression=self.apply_distractor_suppression,
        )


class MockInsectDetectionBackend(BaseVisionLanguageBackend):
    """Deterministic mock backend for offline unit testing and pipeline verification."""

    def __init__(self, forced_confidence: Optional[float] = None) -> None:
        self.forced_confidence = forced_confidence

    def predict_frame(self, frame_bgr: Any) -> Dict[str, Any]:
        """Return deterministic score based on frame brightness or forced confidence."""
        if self.forced_confidence is not None:
            conf = self.forced_confidence
        else:
            # Deterministic pseudo-affinity derived from mean frame pixel intensity
            import numpy as np
            mean_val = float(np.mean(frame_bgr)) if hasattr(frame_bgr, "mean") else 128.0
            # Normalize to [0.1, 0.9]
            conf = round(0.1 + (mean_val / 255.0) * 0.8, 4)

        pos_sim = round(conf * 0.8, 4)
        neg_sim = round((1.0 - conf) * 0.8, 4)
        margin = round(pos_sim - neg_sim, 4)

        return {
            "confidence": conf,
            "raw_scores": {
                "positive_score": pos_sim,
                "negative_score": neg_sim,
                "margin": margin,
            },
        }


# ==============================================================================
# 4. Configurable Decision Policies & Temporal Interval Merging
# ==============================================================================

def evaluate_frame_decision(
    confidence: float,
    raw_scores: Dict[str, Any],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    decision_policy: str = DEFAULT_DECISION_POLICY,
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> Tuple[bool, Dict[str, Any]]:
    """Determine binary insect decision and compute policy diagnostics for a single frame.

    Supported Policies:
    - 'production' (default): Original uncalibrated threshold policy. Positive if confidence >= threshold (default 0.50).
    - 'relative_margin': Candidate contrastive policy. Positive if (positive_cosine_max - negative_cosine_max) >= margin_threshold
      AND confidence >= min_confidence floor.
    - 'calibrated_score': Calibrated sigmoid policy. Positive if confidence >= threshold.
    """
    if decision_policy not in SUPPORTED_DECISION_POLICIES:
        raise ValueError(
            f"Unsupported decision_policy: {decision_policy!r}. "
            f"Supported policies: {SUPPORTED_DECISION_POLICIES}"
        )

    pos_cos = float(raw_scores.get("positive_cosine_max", 0.0))
    neg_cos = float(raw_scores.get("negative_cosine_max", 0.0))
    margin = round(pos_cos - neg_cos, 4)

    policy_meta: Dict[str, Any] = {
        "policy": decision_policy,
        "margin": margin,
        "margin_threshold": margin_threshold,
        "min_confidence": min_confidence,
        "threshold": threshold,
    }

    if decision_policy == "production":
        decision = bool(confidence >= threshold)
    elif decision_policy == "relative_margin":
        decision = bool(margin >= margin_threshold and confidence >= min_confidence)
    elif decision_policy == "calibrated_score":
        decision = bool(confidence >= threshold)
    else:
        decision = bool(confidence >= threshold)

    policy_meta["decision"] = decision
    return decision, policy_meta


def merge_frame_detections_to_intervals(
    frame_detections: List[Dict[str, Any]],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    sample_interval: float = 1.0,
    merge_gap_seconds: float = DEFAULT_MERGE_GAP_SECONDS,
    min_duration_seconds: float = DEFAULT_MIN_DURATION_SECONDS,
    padding_before_seconds: float = DEFAULT_PADDING_BEFORE_SECONDS,
    padding_after_seconds: float = DEFAULT_PADDING_AFTER_SECONDS,
    media_duration_seconds: Optional[float] = None,
    min_consecutive_frames: int = DEFAULT_MIN_CONSECUTIVE_FRAMES,
) -> List[Dict[str, Any]]:
    """Merge positive sampled frames into contiguous half-open [start, end) intervals.

    Interval boundary semantics:
    - Sample t_k represents interval [t_k, t_k + sample_interval).
    - If distance between end of interval A and start of interval B is <= merge_gap_seconds, merge.
    - Applies padding before and after, clamped to [0.0, media_duration].
    - Filters intervals containing fewer than min_consecutive_frames (temporal confirmation).
    """
    if not frame_detections:
        return []

    # Filter frames meeting decision criteria and validate finiteness
    positive_frames = []
    for fr in frame_detections:
        ts = float(fr.get("timestamp", 0.0))
        conf = float(fr.get("confidence", 0.0))
        if not (math.isfinite(ts) and math.isfinite(conf)):
            continue
        is_positive = bool(fr["decision"]) if "decision" in fr else bool(conf >= threshold)
        if is_positive:
            positive_frames.append(fr)
    if not positive_frames:
        return []

    # Sort chronologically
    positive_frames.sort(key=lambda x: float(x["timestamp"]))

    raw_intervals: List[Dict[str, Any]] = []
    current_start = float(positive_frames[0]["timestamp"])
    current_end = current_start + sample_interval
    confs = [float(positive_frames[0]["confidence"])]

    for fr in positive_frames[1:]:
        ts = float(fr["timestamp"])
        fr_end = ts + sample_interval
        gap = ts - current_end

        if gap <= merge_gap_seconds:
            current_end = max(current_end, fr_end)
            confs.append(float(fr["confidence"]))
        else:
            raw_intervals.append({
                "start": current_start,
                "end": current_end,
                "confidence": round(sum(confs) / len(confs), 4),
                "frame_count": len(confs),
            })
            current_start = ts
            current_end = fr_end
            confs = [float(fr["confidence"])]

    # Append trailing interval
    raw_intervals.append({
        "start": current_start,
        "end": current_end,
        "confidence": round(sum(confs) / len(confs), 4),
        "frame_count": len(confs),
    })

    # Apply padding, min_duration filtering, temporal confirmation, and bounds clamping
    packaged_cues: List[Dict[str, Any]] = []
    max_dur = media_duration_seconds if media_duration_seconds and media_duration_seconds > 0 else float("inf")

    for inv in raw_intervals:
        if inv.get("frame_count", 1) < min_consecutive_frames:
            continue
        dur = inv["end"] - inv["start"]
        if dur < min_duration_seconds:
            continue

        padded_start = max(0.0, round(inv["start"] - padding_before_seconds, 3))
        padded_end = min(max_dur, round(inv["end"] + padding_after_seconds, 3))

        if padded_end > padded_start:
            packaged_cues.append({
                "start": padded_start,
                "end": padded_end,
                "confidence": inv["confidence"],
                "trigger": "insect",
                "action": "hide",
            })

    # Final gap merge after padding expansion
    if len(packaged_cues) <= 1:
        return packaged_cues

    merged: List[Dict[str, Any]] = [packaged_cues[0]]
    for nxt in packaged_cues[1:]:
        prev = merged[-1]
        if nxt["start"] <= prev["end"]:
            prev["end"] = max(prev["end"], nxt["end"])
            prev["confidence"] = max(prev["confidence"], nxt["confidence"])
        else:
            merged.append(nxt)

    return merged


# ==============================================================================
# 5. Core Video Inference Pipeline
# ==============================================================================

def run_insect_detection_on_video(
    video_path: Path,
    backend: BaseVisionLanguageBackend,
    target_fps: float = DEFAULT_SAMPLING_FPS,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    merge_gap_seconds: float = DEFAULT_MERGE_GAP_SECONDS,
    padding_before: float = DEFAULT_PADDING_BEFORE_SECONDS,
    padding_after: float = DEFAULT_PADDING_AFTER_SECONDS,
    decision_policy: str = DEFAULT_DECISION_POLICY,
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_consecutive_frames: int = DEFAULT_MIN_CONSECUTIVE_FRAMES,
) -> Dict[str, Any]:
    """Run incremental frame sampling, backend prediction, and interval packaging on one video.

    Parameters:
        decision_policy: 'production' (default), 'relative_margin', or 'calibrated_score'.
        margin_threshold: Minimum (pos_cos - neg_cos) margin for relative_margin policy.
        min_confidence: Minimum native sigmoid floor for relative_margin policy.
        min_consecutive_frames: Minimum consecutive frames required for temporal confirmation.
    """
    t0 = time.perf_counter()
    meta = probe_video_metadata(video_path)
    sample_dt = 1.0 / target_fps if target_fps > 0 else 1.0

    frame_detections: List[Dict[str, Any]] = []
    sampled_count = 0

    for ts, frame in sample_video_frames(video_path, target_fps=target_fps):
        pred_res = backend.predict_frame(frame)
        conf = float(pred_res.get("confidence", 0.0))
        if not math.isfinite(conf) or conf < 0.0 or conf > 1.0:
            raise ValueError(
                f"Invalid confidence score from detector backend at timestamp {ts}s: {conf}. "
                f"Confidence must be a finite float in [0.0, 1.0]."
            )

        raw_scores = dict(pred_res.get("raw_scores", {}))
        decision, policy_meta = evaluate_frame_decision(
            confidence=conf,
            raw_scores=raw_scores,
            threshold=threshold,
            decision_policy=decision_policy,
            margin_threshold=margin_threshold,
            min_confidence=min_confidence,
        )
        raw_scores["decision_policy"] = policy_meta

        frame_detections.append({
            "timestamp": ts,
            "confidence": conf,
            "decision": decision,
            "raw_scores": raw_scores,
        })
        sampled_count += 1

    # Merge into interval events
    interval_events = merge_frame_detections_to_intervals(
        frame_detections=frame_detections,
        threshold=threshold,
        sample_interval=sample_dt,
        merge_gap_seconds=merge_gap_seconds,
        padding_before_seconds=padding_before,
        padding_after_seconds=padding_after,
        media_duration_seconds=meta["duration_seconds"],
        min_consecutive_frames=min_consecutive_frames,
    )

    elapsed = round(time.perf_counter() - t0, 3)

    return {
        "clip_id": video_path.stem,
        "media_path": str(video_path),
        "media_duration_seconds": meta["duration_seconds"],
        "sampled_frames_count": sampled_count,
        "processing_time_seconds": elapsed,
        "threshold": threshold,
        "interval_events": interval_events,
        "frame_detections": frame_detections,
    }


# ==============================================================================
# 6. CLI Commands
# ==============================================================================

def cmd_inspect_model(args: argparse.Namespace) -> int:
    """Inspect model files, weight existence, and dependencies."""
    print("=== SigLIP 2 ONNX Model Inspection ===")
    model_dir = Path(args.model_dir or DEFAULT_MODEL_DIR)
    fp16_path = model_dir / "vision_model_fp16.onnx"
    fp16_data = model_dir / "vision_model_fp16.onnx_data"
    fp32_path = model_dir / "vision_model.onnx"
    fp32_data = model_dir / "vision_model.onnx_data"
    text_fp16 = model_dir / "text_model_fp16.onnx"
    text_fp16_data = model_dir / "text_model_fp16.onnx_data"
    tokenizer_path = model_dir / "tokenizer.json"
    prompts_path = model_dir / "prompt_embeddings.json"

    print(f"- Target Directory: {model_dir}")
    print(f"- Vision FP16 Model: {'FOUND' if fp16_path.is_file() else 'MISSING'} ({fp16_path.name})")
    print(f"- Vision FP16 Data:  {'FOUND' if fp16_data.is_file() else 'MISSING'} ({fp16_data.name})")
    print(f"- Vision FP32 Model: {'FOUND' if fp32_path.is_file() else 'MISSING'} ({fp32_path.name})")
    print(f"- Vision FP32 Data:  {'FOUND' if fp32_data.is_file() else 'MISSING'} ({fp32_data.name})")
    print(f"- Text FP16 Model:   {'FOUND' if text_fp16.is_file() else 'MISSING'} ({text_fp16.name})")
    print(f"- Text FP16 Data:    {'FOUND' if text_fp16_data.is_file() else 'MISSING'} ({text_fp16_data.name})")
    print(f"- Tokenizer:         {'FOUND' if tokenizer_path.is_file() else 'MISSING'} ({tokenizer_path.name})")
    print(f"- Text Embeddings:   {'FOUND' if prompts_path.is_file() else 'MISSING'} ({prompts_path.name})")

    # Check dependencies
    print("\n=== Dependency Inspection ===")
    try:
        import cv2
        print(f"[+] OpenCV: Available (v{cv2.__version__})")
    except ImportError:
        print("[-] OpenCV: NOT AVAILABLE")

    try:
        import onnxruntime as ort
        print(f"[+] ONNX Runtime: Available (v{ort.__version__}, providers: {ort.get_available_providers()})")
    except ImportError:
        print("[-] ONNX Runtime: NOT AVAILABLE (Available in .venv_eval)")

    try:
        import tokenizers
        print(f"[+] Tokenizers:   Available (v{tokenizers.__version__})")
    except ImportError:
        print("[-] Tokenizers:   NOT AVAILABLE (Available in .venv_eval)")

    has_vision = (fp16_path.is_file() and fp16_data.is_file()) or (fp32_path.is_file() and fp32_data.is_file())
    has_embeddings = prompts_path.is_file()

    if not has_vision or not has_embeddings:
        print("\n[!] STATUS: MODEL ASSETS INCOMPLETE FOR REAL INFERENCE.")
        print("    Exact Candidate: upshift/siglip2-base-patch16-224-onnx on Hugging Face")
        print("    External Staging Directory: /Users/zoya/Desktop/insect_model_staging/")
        if not has_vision:
            print("    Required Vision Files: vision_model_fp16.onnx (~221 KB) + vision_model_fp16.onnx_data (~201.4 MB)")
        if not has_embeddings:
            print("    Required Embeddings: prompt_embeddings.json (~32 KB, 768-dim static vectors)")
            print("                         (Alternatively require text_model_fp16.onnx + data (~564.8 MB) + tokenizer.json (~34.4 MB) to generate locally)")
        print("    Action: Awaiting user download authorization.")
        return 1
    return 0


def cmd_generate_prompt_embeddings(args: argparse.Namespace) -> int:
    """Generate prompt embeddings using text model or check requirements."""
    output_path = Path(args.output or DEFAULT_PROMPT_EMBEDDINGS)
    try:
        res = generate_prompt_embeddings(
            positive_prompts=DEFAULT_PROMPT_CONFIG["positive_prompts"],
            negative_prompts=DEFAULT_PROMPT_CONFIG["negative_prompts"],
            output_path=output_path,
            text_model_path=args.text_model,
            tokenizer_path=args.tokenizer,
        )
        print(f"[+] Embeddings generation status: {res['status']}")
        return 0
    except Exception as e:
        print(f"[-] Cannot generate prompt embeddings: {e}")
        return 1


def cmd_inspect_media(args: argparse.Namespace) -> int:
    """Probe staged video files and display technical parameters."""
    print("=== Media Probing ===")
    input_paths = []
    if args.input:
        p = Path(args.input)
        if p.is_dir():
            input_paths = sorted(p.glob("*.webm")) + sorted(p.glob("*.mp4"))
        elif p.is_file():
            input_paths = [p]
    else:
        input_paths = sorted(STAGING_DIR.glob("*.webm"))

    if not input_paths:
        print(f"[-] No video files found in: {args.input or STAGING_DIR}")
        return 1

    print(f"Found {len(input_paths)} video(s):\n")
    print(f"{'Filename':<55} | {'Dimensions':<10} | {'FPS':<6} | {'Duration':<8} | {'1fps Samples'}")
    print("-" * 95)

    for vp in input_paths:
        try:
            m = probe_video_metadata(vp)
            est_samples = int(math.ceil(m["duration_seconds"] * args.fps))
            print(f"{m['filename']:<55} | {m['width']}x{m['height']:<6} | {m['fps']:<6} | {m['duration_seconds']:<6.1f}s | {est_samples}")
        except Exception as e:
            print(f"{vp.name:<55} | ERROR: {e}")

    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    """Run detector prediction on staged video(s) and export JSON."""
    output_path = Path(args.output or (DEFAULT_PREDICTIONS_DIR / "insect_predictions.json"))

    if output_path.exists() and not args.overwrite:
        print(f"[-] Error: Output file already exists: {output_path}")
        print("    Use --overwrite flag to replace existing predictions.")
        return 1

    # Select backend
    backend: BaseVisionLanguageBackend
    model_id: str

    if args.mock:
        backend = MockInsectDetectionBackend(forced_confidence=args.mock_confidence)
        model_id = "mock_insect_detector_v1"
        print("[*] Running in MOCK mode (synthetic/test scores)")
    else:
        model_path = Path(args.model_path or DEFAULT_VISION_MODEL_FP16)
        prompt_path = Path(args.prompt_embeddings or DEFAULT_PROMPT_EMBEDDINGS)
        suppress = getattr(args, "apply_distractor_suppression", False)
        try:
            backend = SigLIP2OnnxBackend(
                model_path=model_path,
                prompt_embeddings_path=prompt_path,
                logit_scale=DEFAULT_LOGIT_SCALE,
                logit_bias=DEFAULT_LOGIT_BIAS,
                apply_distractor_suppression=suppress,
                expected_positive_prompts=DEFAULT_PROMPT_CONFIG["positive_prompts"],
                expected_negative_prompts=DEFAULT_PROMPT_CONFIG["negative_prompts"],
            )
        except Exception as e:
            print(f"[-] Error initializing real ONNX backend: {e}")
            print("    Run 'inspect-model' for prerequisites or specify --mock for testing.")
            return 1
        model_id = "siglip2_base_patch16_224_onnx"

    # Gather inputs
    input_paths: List[Path] = []
    if args.input:
        p = Path(args.input)
        if p.is_dir():
            input_paths = sorted(p.glob("*.webm")) + sorted(p.glob("*.mp4"))
        elif p.is_file():
            input_paths = [p]
    else:
        input_paths = sorted(STAGING_DIR.glob("*.webm"))

    if not input_paths:
        print(f"[-] No video files found to process.")
        return 1

    print(f"[*] Processing {len(input_paths)} video(s) at {args.fps} fps (threshold: {args.threshold})...")
    clip_predictions: List[Dict[str, Any]] = []
    failed_clips: List[Tuple[str, str]] = []

    for idx, vp in enumerate(input_paths):
        print(f"    [{idx+1}/{len(input_paths)}] {vp.name} ... ", end="", flush=True)
        try:
            res = run_insect_detection_on_video(
                video_path=vp,
                backend=backend,
                target_fps=args.fps,
                threshold=args.threshold,
                merge_gap_seconds=args.merge_gap,
                decision_policy=getattr(args, "decision_policy", DEFAULT_DECISION_POLICY),
                margin_threshold=getattr(args, "margin_threshold", DEFAULT_MARGIN_THRESHOLD),
                min_confidence=getattr(args, "min_confidence", DEFAULT_MIN_CONFIDENCE),
                min_consecutive_frames=getattr(args, "min_consecutive_frames", DEFAULT_MIN_CONSECUTIVE_FRAMES),
            )
            clip_predictions.append(res)
            print(f"Done ({res['sampled_frames_count']} frames, {len(res['interval_events'])} events in {res['processing_time_seconds']}s)")
        except Exception as e:
            print(f"FAILED: {e}")
            failed_clips.append((vp.name, str(e)))

    if failed_clips:
        print(f"\n[-] Error: {len(failed_clips)} video(s) failed during prediction processing.")
        return 1

    # Build schema-compliant payload
    payload = {
        "schema_version": 1,
        "model_id": model_id,
        "model_version": "2.0.0-prototype",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "default_threshold": args.threshold,
        "ground_truth_status": "unreviewed",
        "prompt_configuration": DEFAULT_PROMPT_CONFIG,
        "notes": "Automated prediction prototype output. Ground truth status is unreviewed.",
        "predictions": clip_predictions,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"\n[+] Successfully exported predictions for {len(clip_predictions)} video(s) to: {output_path}")
    return 0


def cmd_validate_predictions(args: argparse.Namespace) -> int:
    """Validate prediction JSON file against schema."""
    from scripts.evaluate_insect import validate_prediction_data
    pred_path = Path(args.file)
    if not pred_path.is_file():
        print(f"[-] Prediction file not found: {pred_path}")
        return 1

    with pred_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    try:
        validate_prediction_data(data)
        print(f"[+] Prediction file is valid: {pred_path} ({len(data.get('predictions', []))} clips)")
        return 0
    except Exception as e:
        print(f"[-] Validation error: {e}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Visual Insect Detection Prototype")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # inspect-model
    p_im = subparsers.add_parser("inspect-model", help="Inspect model files, weights, and dependencies")
    p_im.add_argument("--model-dir", type=Path, help="Path to model directory")

    # inspect-media
    p_md = subparsers.add_parser("inspect-media", help="Probe staged video media files")
    p_md.add_argument("--input", type=Path, help="Path to video or directory")
    p_md.add_argument("--fps", type=float, default=DEFAULT_SAMPLING_FPS, help="Target sampling rate")

    # generate-prompt-embeddings
    p_ge = subparsers.add_parser("generate-prompt-embeddings", help="Generate or verify text prompt embeddings")
    p_ge.add_argument("--text-model", type=Path, help="Path to text_model_fp16.onnx")
    p_ge.add_argument("--tokenizer", type=Path, help="Path to tokenizer.json")
    p_ge.add_argument("--output", "-o", type=Path, help="Output path for prompt_embeddings.json")

    # predict
    p_pr = subparsers.add_parser("predict", help="Run insect detection on video(s)")
    p_pr.add_argument("--input", "-i", type=Path, help="Path to video file or directory")
    p_pr.add_argument("--output", "-o", type=Path, help="Path to output predictions JSON")
    p_pr.add_argument("--model-path", type=Path, help="Path to ONNX model")
    p_pr.add_argument("--prompt-embeddings", type=Path, help="Path to prompt embeddings JSON")
    p_pr.add_argument("--mock", action="store_true", help="Use deterministic mock backend for pipeline testing")
    p_pr.add_argument("--mock-confidence", type=float, help="Forced mock confidence score")
    p_pr.add_argument("--fps", type=float, default=DEFAULT_SAMPLING_FPS, help="Sampling rate in frames per second")
    p_pr.add_argument("--threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD, help="Confidence threshold")
    p_pr.add_argument("--merge-gap", type=float, default=DEFAULT_MERGE_GAP_SECONDS, help="Merge gap in seconds")
    p_pr.add_argument("--decision-policy", choices=SUPPORTED_DECISION_POLICIES, default=DEFAULT_DECISION_POLICY, help=f"Decision policy for binary event emission (default: {DEFAULT_DECISION_POLICY})")
    p_pr.add_argument("--margin-threshold", type=float, default=DEFAULT_MARGIN_THRESHOLD, help=f"Margin threshold for relative_margin policy (default: {DEFAULT_MARGIN_THRESHOLD})")
    p_pr.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE, help=f"Minimum confidence floor for relative_margin policy (default: {DEFAULT_MIN_CONFIDENCE})")
    p_pr.add_argument("--min-consecutive-frames", type=int, default=DEFAULT_MIN_CONSECUTIVE_FRAMES, help=f"Minimum consecutive positive frames for temporal confirmation (default: {DEFAULT_MIN_CONSECUTIVE_FRAMES})")
    p_pr.add_argument("--apply-distractor-suppression", action="store_true", help="Apply optional distractor-suppression heuristic to dampen confidence when negative prompt exceeds insect affinity")
    p_pr.add_argument("--overwrite", action="store_true", help="Overwrite existing output file")

    # validate-predictions
    p_vp = subparsers.add_parser("validate-predictions", help="Validate prediction JSON against schema")
    p_vp.add_argument("file", type=Path, help="Path to predictions JSON")

    args = parser.parse_args()

    if args.command == "inspect-model":
        return cmd_inspect_model(args)
    elif args.command == "inspect-media":
        return cmd_inspect_media(args)
    elif args.command == "generate-prompt-embeddings":
        return cmd_generate_prompt_embeddings(args)
    elif args.command == "predict":
        return cmd_predict(args)
    elif args.command == "validate-predictions":
        return cmd_validate_predictions(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
