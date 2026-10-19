#!/usr/bin/env python3
"""Pretrained Audio-Event Classifier for Trigger-Aware Player (Person A).

Classifies audio events using real pretrained YAMNet ONNX model for sensitive triggers:
- gunshot: firearm discharge, rifle shots, gunfire, cap gun
- explosion: detonations, blasts, concussive explosions, bursts
- scream: high-pitched vocalizations of terror, distress, or screaming

Architecture:
-------------
- Model: YAMNet (MobileNet v1 architecture on AudioSet, trained by Google Research).
- Input: 16 kHz mono audio converted to 64-band log-mel spectrogram patches [1, 1, 96, 64].
- Preprocessing: 25ms periodic Hann window (400 samples), 10ms hop (160 samples), 512-point FFT.
- Mel scale: 64 triangular filterbank bands spanning 125 Hz to 7500 Hz (HTK formula).
- Patch size: 96 frames (0.96s), hop size: 48 frames (0.48s).
- Activation: Sigmoid over 521 AudioSet class logits.
- Runtime: ONNX Runtime (CPU execution provider).
- License: Apache 2.0 / MIT.

Subprocess Fallback:
--------------------
If executed under a Python version lacking onnxruntime (such as Python 3.14),
the classifier automatically delegates to the project's dedicated .venv312 Python
environment where onnxruntime is installed and verified.

Usage:
    python scripts/classify_audio_events.py --input "data/test_clips/audio_gunshot_test.mp4" --output "cues.json"
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import yaml

DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_SOURCE = "audio_event_classifier"

# Default model directory and files
DEFAULT_MODEL_DIR = REPO_ROOT / "models" / "yamnet" / "yamnet-onnx-float"
DEFAULT_MODEL_PATH = DEFAULT_MODEL_DIR / "yamnet.onnx"
DEFAULT_LABELS_PATH = DEFAULT_MODEL_DIR / "labels.txt"

# Dedicated Python 3.12 virtual environment path
VENV_312_PYTHON = REPO_ROOT / ".venv312" / "bin" / "python"

# Candidate backends
BACKEND_YAMNET = "YAMNet"
BACKEND_AST = "AST"
BACKEND_PANNS = "PANNs"
CANDIDATE_BACKENDS = (BACKEND_YAMNET, BACKEND_AST, BACKEND_PANNS)

# AudioSet class mappings from config.yaml
DEFAULT_LABEL_MAPPINGS: Dict[str, Dict[str, List[str]]] = {
    "gunshot": {
        BACKEND_YAMNET: [
            "Gunshot, gunfire",
            "Machine gun",
            "Fusillade",
            "Cap gun",
            "Artillery fire",
        ],
        BACKEND_AST: ["Gunshot, gunfire", "Artillery fire", "Cap gun"],
        BACKEND_PANNS: ["Gunshot, gunfire", "Machine gun"],
    },
    "explosion": {
        BACKEND_YAMNET: [
            "Explosion",
            "Burst, pop",
            "Boom",
            "Eruption",
            "Bang",
            "Artillery fire",
            "Fireworks",
            "Firecracker",
        ],
        BACKEND_AST: ["Explosion", "Burst, pop", "Boom"],
        BACKEND_PANNS: ["Explosion", "Boom"],
    },
    "scream": {
        BACKEND_YAMNET: [
            "Screaming",
            "Shout",
            "Yell",
            "Children shouting",
            "Bellow",
            "Whoop",
            "Wail, moan",
        ],
        BACKEND_AST: ["Screaming", "Shout", "Yell"],
        BACKEND_PANNS: ["Screaming", "Shout"],
    },
}

# Provisional confidence thresholds from config.yaml
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "gunshot": 0.65,
    "explosion": 0.70,
    "scream": 0.60,
}


def load_classifier_config(config_path: Union[str, Path] = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load trigger detection configuration from YAML file."""
    path = Path(config_path)
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def get_class_to_trigger_mapping(
    backend: str = BACKEND_YAMNET,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Return mapping of lowercased model class label -> canonical trigger ID."""
    mapping: Dict[str, str] = {}
    cfg_triggers = (config or {}).get("triggers", {})

    for trig in ("gunshot", "explosion", "scream"):
        model_classes = []
        if trig in cfg_triggers:
            c_settings = cfg_triggers[trig].get("classifier_settings", {})
            m_mapping = c_settings.get("model_class_mapping", {})
            if backend in m_mapping:
                model_classes = m_mapping[backend]

        if not model_classes:
            model_classes = DEFAULT_LABEL_MAPPINGS.get(trig, {}).get(backend, [])

        for label in model_classes:
            mapping[label.strip().lower()] = trig

    return mapping


def get_trigger_threshold(
    trigger: str,
    config: Optional[Dict[str, Any]] = None,
) -> float:
    """Retrieve detection confidence threshold for a trigger from config."""
    if config and "triggers" in config and trigger in config["triggers"]:
        trig_cfg = config["triggers"][trigger]
        if "threshold" in trig_cfg:
            return float(trig_cfg["threshold"])
    return DEFAULT_THRESHOLDS.get(trigger, 0.65)


def map_model_predictions_to_cues(
    window_predictions: Sequence[Dict[str, Any]],
    backend: str = BACKEND_YAMNET,
    config: Optional[Dict[str, Any]] = None,
    source: str = DEFAULT_SOURCE,
) -> List[Dict[str, Any]]:
    """Convert raw window classification predictions into standard project cues."""
    class_map = get_class_to_trigger_mapping(backend=backend, config=config)
    thresholds = {
        trig: get_trigger_threshold(trig, config)
        for trig in ("gunshot", "explosion", "scream")
    }

    raw_cues: List[Dict[str, Any]] = []

    for win in window_predictions:
        w_start = float(win["start"])
        w_end = float(win["end"])
        probs = win.get("class_probabilities", {})

        trigger_scores: Dict[str, float] = collections.defaultdict(float)
        for cls_name, prob in probs.items():
            norm_cls = cls_name.strip().lower()
            if norm_cls in class_map:
                trig = class_map[norm_cls]
                if prob > trigger_scores[trig]:
                    trigger_scores[trig] = float(prob)

        for trig, score in trigger_scores.items():
            thresh = thresholds.get(trig, 0.65)
            if score >= thresh:
                raw_cues.append({
                    "start": round(w_start, 3),
                    "end": round(w_end, 3),
                    "trigger": trig,
                    "score": round(float(np.clip(score, 0.0, 1.0)), 4),
                    "source": source,
                })

    return raw_cues


def compute_htk_mel_weights(
    num_mel_bins: int = 64,
    num_spectrogram_bins: int = 257,
    sample_rate: float = 16000.0,
    lower_hz: float = 125.0,
    upper_hz: float = 7500.0,
) -> np.ndarray:
    """Create triangular Mel filterbank matrix matching Google's linear_to_mel_weight_matrix."""
    def hz_to_mel(f: float) -> float:
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel_to_hz(m: float) -> float:
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)

    linear_freqs = np.linspace(0.0, sample_rate / 2.0, num_spectrogram_bins)
    mel_edges = np.linspace(hz_to_mel(lower_hz), hz_to_mel(upper_hz), num_mel_bins + 2)
    hz_edges = mel_to_hz(mel_edges)

    weights = np.zeros((num_spectrogram_bins, num_mel_bins), dtype=np.float32)
    for i in range(num_mel_bins):
        lower = hz_edges[i]
        peak = hz_edges[i + 1]
        upper = hz_edges[i + 2]
        down = (linear_freqs - lower) / (peak - lower)
        up = (upper - linear_freqs) / (upper - peak)
        weights[:, i] = np.maximum(0.0, np.minimum(down, up))
    return weights


def extract_audio_samples_16k(
    media_path: Union[str, Path],
) -> np.ndarray:
    """Extract mono 16 kHz float32 audio samples from video or audio file using ffmpeg."""
    path = Path(media_path)
    if not path.is_file():
        raise FileNotFoundError(f"Media file not found: {path.resolve()}")

    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    cmd = [
        ffmpeg_bin,
        "-v", "error",
        "-i", str(path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-f", "f32le",
        "-",
    ]

    res = subprocess.run(cmd, capture_output=True, check=False)
    if res.returncode != 0:
        return np.array([], dtype=np.float32)

    return np.frombuffer(res.stdout, dtype=np.float32)


def compute_log_mel_spectrogram(
    audio: np.ndarray,
    sample_rate: float = 16000.0,
    window_length_samples: int = 400,
    hop_length_samples: int = 160,
    fft_length: int = 512,
    mel_bands: int = 64,
    mel_min_hz: float = 125.0,
    mel_max_hz: float = 7500.0,
    log_offset: float = 0.001,
) -> np.ndarray:
    """Compute log-mel spectrogram from 1D audio waveform conforming to YAMNet input specs."""
    if len(audio) < window_length_samples:
        return np.zeros((0, mel_bands), dtype=np.float32)

    # Periodic Hann window (400 samples)
    hann = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(window_length_samples) / window_length_samples)

    frames = []
    for start in range(0, len(audio) - window_length_samples + 1, hop_length_samples):
        chunk = audio[start : start + window_length_samples] * hann
        frames.append(chunk)

    frames_arr = np.array(frames, dtype=np.float32)
    stft = np.fft.rfft(frames_arr, n=fft_length)
    mag_spec = np.abs(stft).astype(np.float32)

    mel_weights = compute_htk_mel_weights(
        num_mel_bins=mel_bands,
        num_spectrogram_bins=(fft_length // 2 + 1),
        sample_rate=sample_rate,
        lower_hz=mel_min_hz,
        upper_hz=mel_max_hz,
    )

    mel_spec = np.dot(mag_spec, mel_weights)
    log_mel = np.log(mel_spec + log_offset)
    return log_mel.astype(np.float32)


def run_yamnet_inference(
    audio_path: Union[str, Path],
    model_path: Union[str, Path] = DEFAULT_MODEL_PATH,
    labels_path: Union[str, Path] = DEFAULT_LABELS_PATH,
    patch_window_frames: int = 96,
    patch_hop_frames: int = 48,
) -> List[Dict[str, Any]]:
    """Run real YAMNet ONNX inference on audio file and return per-window class probabilities."""
    m_path = Path(model_path)
    l_path = Path(labels_path)
    if not m_path.is_file():
        raise FileNotFoundError(f"YAMNet ONNX model not found at: {m_path.resolve()}")
    if not l_path.is_file():
        raise FileNotFoundError(f"YAMNet labels file not found at: {l_path.resolve()}")

    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("onnxruntime is not installed in the current Python environment.") from exc

    with l_path.open("r", encoding="utf-8") as f:
        labels = [line.strip() for line in f if line.strip()]

    audio = extract_audio_samples_16k(audio_path)
    if len(audio) == 0:
        return []

    log_mel = compute_log_mel_spectrogram(audio)
    if log_mel.shape[0] < patch_window_frames:
        return []

    # Configure ONNX session
    opts = ort.SessionOptions()
    opts.log_severity_level = 3  # Suppress info/warning telemetry logs
    session = ort.InferenceSession(str(m_path), sess_options=opts, providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    num_patches = (log_mel.shape[0] - patch_window_frames) // patch_hop_frames + 1
    window_predictions: List[Dict[str, Any]] = []

    for p in range(num_patches):
        p_start = p * patch_hop_frames
        p_end = p_start + patch_window_frames
        patch = log_mel[p_start:p_end, :].reshape(1, 1, patch_window_frames, 64).astype(np.float32)

        logits = session.run(["class_scores"], {input_name: patch})[0][0]
        # Sigmoid activation clamped to avoid overflow
        clipped_logits = np.clip(logits, -50.0, 50.0)
        probs = 1.0 / (1.0 + np.exp(-clipped_logits))

        t_start = (p_start * 160) / 16000.0
        t_end = t_start + (patch_window_frames * 160) / 16000.0

        # Create class probabilities dictionary
        class_probs = {labels[i]: float(probs[i]) for i in range(len(labels))}
        window_predictions.append({
            "start": round(float(t_start), 3),
            "end": round(float(t_end), 3),
            "class_probabilities": class_probs,
        })

    return window_predictions


def classify_audio_events(
    audio_path: Union[str, Path],
    backend: str = BACKEND_YAMNET,
    config: Optional[Dict[str, Any]] = None,
    config_path: Union[str, Path] = DEFAULT_CONFIG_PATH,
    model_path: Union[str, Path] = DEFAULT_MODEL_PATH,
    labels_path: Union[str, Path] = DEFAULT_LABELS_PATH,
    strict: bool = False,
) -> List[Dict[str, Any]]:
    """Classify audio events for gunshot, explosion, and scream.

    If run in an environment without onnxruntime, delegates to .venv312 if present.
    """
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path.resolve()}")

    if config is None:
        config = load_classifier_config(config_path)

    if backend != BACKEND_YAMNET:
        if strict:
            raise NotImplementedError(f"Backend '{backend}' is not supported for active execution.")
        return []

    # Check if onnxruntime is available in current process
    try:
        import onnxruntime
        HAS_ORT = True
    except ImportError:
        HAS_ORT = False

    if HAS_ORT:
        # Run in-process ONNX inference
        try:
            window_preds = run_yamnet_inference(
                audio_path=path,
                model_path=model_path,
                labels_path=labels_path,
            )
            raw_cues = map_model_predictions_to_cues(
                window_predictions=window_preds,
                backend=backend,
                config=config,
            )
            return raw_cues
        except Exception as exc:
            if strict:
                raise
            return []

    # If current environment lacks onnxruntime, check if .venv312 is available
    if VENV_312_PYTHON.is_file():
        # Delegate to Python 3.12 venv CLI
        cmd = [
            str(VENV_312_PYTHON),
            str(REPO_ROOT / "scripts" / "classify_audio_events.py"),
            "--input", str(path),
            "--backend", backend,
            "--config", str(config_path),
        ]
        if strict:
            cmd.append("--strict")
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            try:
                return json.loads(res.stdout)
            except Exception as exc:
                if strict:
                    raise RuntimeError(f"Failed to parse JSON from inference subprocess: {res.stdout}") from exc
                return []
        elif res.returncode != 0 and strict:
            raise RuntimeError(
                f"Subprocess audio classification failed with code {res.returncode}: {res.stderr.strip()}"
            )

    if strict:
        raise RuntimeError("No suitable ONNX runtime found in current environment or .venv312.")
    return []


def main() -> int:
    """CLI entrypoint for standalone audio event classifier."""
    parser = argparse.ArgumentParser(
        description="Trigger-Aware Player — Pretrained Audio Event Classifier (Person A)"
    )
    parser.add_argument("--input", "-i", required=True, type=Path, help="Input audio or video file")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output JSON path")
    parser.add_argument("--config", "-c", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to config.yaml")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH, help="Path to YAMNet ONNX model")
    parser.add_argument("--labels-path", type=Path, default=DEFAULT_LABELS_PATH, help="Path to labels.txt")
    parser.add_argument("--backend", "-b", choices=CANDIDATE_BACKENDS, default=BACKEND_YAMNET)
    parser.add_argument("--strict", action="store_true", help="Fail with error on inference failure")

    args = parser.parse_args()

    if not args.input.is_file():
        sys.stderr.write(f"[ERROR] Input file not found: {args.input}\n")
        return 1

    try:
        detections = classify_audio_events(
            args.input,
            backend=args.backend,
            config_path=args.config,
            model_path=args.model_path,
            labels_path=args.labels_path,
            strict=args.strict,
        )
    except Exception as exc:
        sys.stderr.write(f"[ERROR] Classifier execution failed: {exc}\n")
        return 1

    out_json = json.dumps(detections, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as f:
            f.write(out_json + "\n")
        print(f"Wrote {len(detections)} detections to: {args.output}")
    else:
        print(out_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
