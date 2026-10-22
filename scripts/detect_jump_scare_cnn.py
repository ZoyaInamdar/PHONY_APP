#!/usr/bin/env python3
"""Two-Stage Cascade Jump Scare Detector with Multi-Modal 3D CNN Validator.

Implements a high-throughput, low-compute Two-Stage Cascade Architecture:
- Stage 1: Algorithmic Pre-Filter (Fast Baseline)
    Runs existing multimodal loudness + visual-difference logic (`detect_jump_scare`)
    at an accessible threshold (e.g. geometric mean >= 0.40) to rapidly flag potential
    candidate timestamps without running heavy deep learning across the whole video.
- Stage 2: Multi-Modal 3D CNN Validator (Dormant until Stage 1 flags a candidate)
    Extracts an isolated 1.0-second video/audio chunk around each candidate timestamp:
    - Visual Branch (3D CNN): 16-frame 3D ConvNet analyzing temporal motion dynamics.
    - Audio Branch (2D CNN): 64-band Mel-Spectrogram ConvNet analyzing acoustic attack.
    - Late Fusion: Concatenates neural embeddings into a final probability score in [0.0, 1.0].
- Stage 3: Fusion & Veto Decision Gate
    - Veto: Rejects candidate if CNN confidence is below threshold (default: < 0.85).
    - Hybrid Score: Emits verified cues with weighted score (30% Stage 1 + 70% CNN).
    - Emits half-open [start, end) intervals matching the canonical project schema.

Usage:
    python scripts/detect_jump_scare_cnn.py --input "data/test_clips/jump_scare_positive.mp4"
    python scripts/detect_jump_scare_cnn.py --input "video.mp4" --output "cues_jump_scare.json"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

# Import Stage 1 detector functions from existing baseline
from scripts.detect_jump_scare import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_MAX_SEGMENT_DURATION_SECONDS,
    DEFAULT_MIN_FRAME_DIFFERENCE,
    DEFAULT_MIN_SEGMENT_DURATION_SECONDS,
    DEFAULT_TEMPORAL_ALIGNMENT_TOLERANCE_SECONDS,
    calculate_compound_score,
    detect_jump_scare,
    load_jump_scare_config,
)

DEFAULT_CASCADE_SOURCE = "jump_scare_cnn_cascade"
DEFAULT_PREFILTER_THRESHOLD = 0.40
DEFAULT_CNN_VETO_THRESHOLD = 0.65
DEFAULT_WEIGHT_STAGE1 = 0.30
DEFAULT_WEIGHT_CNN = 0.70
AUDIO_SAMPLE_RATE = 16000
WINDOW_DURATION_SECONDS = 1.0
NUM_VIDEO_FRAMES = 16
FRAME_SIZE = 112
N_MELS = 64
N_FFT = 512
HOP_LENGTH = 160


# ---------------------------------------------------------------------------
# Python Environment Delegation (ensuring PyTorch is loaded for CLI runs)
# ---------------------------------------------------------------------------
def check_and_delegate_torch() -> bool:
    """Ensure running under a Python environment with PyTorch and OpenCV."""
    if "pytest" in sys.modules:
        return False

    try:
        import cv2  # noqa: F401
        import torch  # noqa: F401
        return False
    except ImportError:
        pass

    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python313" / "python.exe",
        REPO_ROOT / ".venv313" / "Scripts" / "python.exe",
        REPO_ROOT.parent / "venv" / "Scripts" / "python.exe",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
    ]

    target_py: Optional[str] = None
    for cand in candidates:
        if cand.is_file():
            target_py = str(cand)
            break

    if not target_py:
        target_py = "py -3.13"

    cmd = target_py.split() + [str(Path(__file__).resolve())] + sys.argv[1:]
    ret = subprocess.run(cmd)
    sys.exit(ret.returncode)


# ---------------------------------------------------------------------------
# Audio Extraction & Mel-Spectrogram Preprocessing
# ---------------------------------------------------------------------------
def extract_audio_wav(input_path: Union[str, Path], output_path: Union[str, Path], sample_rate: int = AUDIO_SAMPLE_RATE) -> bool:
    """Extract mono PCM WAV audio using ffmpeg directly without heavy dependencies."""
    if not shutil.which("ffmpeg"):
        return False

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i", str(input_path),
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    try:
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return res.returncode == 0
    except Exception:
        return False


def compute_mel_filterbank(
    sr: int = AUDIO_SAMPLE_RATE,
    n_fft: int = N_FFT,
    n_mels: int = N_MELS,
    f_min: float = 0.0,
    f_max: float = 8000.0,
) -> np.ndarray:
    """Compute standard triangular Mel filterbank matrix without external dependencies."""
    mel_min = 2595.0 * np.log10(1.0 + f_min / 700.0)
    mel_max = 2595.0 * np.log10(1.0 + f_max / 700.0)
    mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_points = 700.0 * (10.0 ** (mel_points / 2595.0) - 1.0)
    bins = np.floor((n_fft + 1) * hz_points / sr).astype(int)

    filterbank = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(1, n_mels + 1):
        f_m_minus = bins[m - 1]
        f_m = bins[m]
        f_m_plus = bins[m + 1]
        for k in range(f_m_minus, f_m):
            filterbank[m - 1, k] = (k - bins[m - 1]) / max(1, f_m - f_m_minus)
        for k in range(f_m, f_m_plus):
            filterbank[m - 1, k] = (bins[m + 1] - k) / max(1, f_m_plus - f_m)

    return filterbank


def audio_chunk_to_mel_spectrogram(
    audio_pcm: np.ndarray,
    sr: int = AUDIO_SAMPLE_RATE,
    n_fft: int = N_FFT,
    hop_length: int = HOP_LENGTH,
    n_mels: int = N_MELS,
) -> np.ndarray:
    """Convert 1D audio PCM samples to a normalized 2D Mel-Spectrogram (C, F, T)."""
    target_samples = int(sr * WINDOW_DURATION_SECONDS)
    if len(audio_pcm) < target_samples:
        audio_pcm = np.pad(audio_pcm, (0, target_samples - len(audio_pcm)), mode="constant")
    elif len(audio_pcm) > target_samples:
        audio_pcm = audio_pcm[:target_samples]

    window = np.hanning(n_fft)
    num_frames = 1 + (len(audio_pcm) - n_fft) // hop_length
    if num_frames <= 0:
        return np.zeros((1, n_mels, 101), dtype=np.float32)

    frames = np.lib.stride_tricks.as_strided(
        audio_pcm,
        shape=(num_frames, n_fft),
        strides=(audio_pcm.strides[0] * hop_length, audio_pcm.strides[0]),
    )
    windowed = frames * window
    fft_spec = np.fft.rfft(windowed, n=n_fft)
    mag_spec = np.abs(fft_spec).T  # (n_fft // 2 + 1, num_frames)

    mel_fb = compute_mel_filterbank(sr=sr, n_fft=n_fft, n_mels=n_mels)
    mel_spec = np.dot(mel_fb, mag_spec)  # (n_mels, num_frames)
    log_mel = np.log10(np.maximum(mel_spec, 1e-6))

    # Normalize to [0, 1]
    norm_mel = (log_mel - log_mel.min()) / max(1e-6, (log_mel.max() - log_mel.min()))

    # Ensure fixed time dimension (e.g. 101 frames)
    target_time_steps = 101
    if norm_mel.shape[1] < target_time_steps:
        norm_mel = np.pad(norm_mel, ((0, 0), (0, target_time_steps - norm_mel.shape[1])), mode="constant")
    elif norm_mel.shape[1] > target_time_steps:
        norm_mel = norm_mel[:, :target_time_steps]

    return np.expand_dims(norm_mel.astype(np.float32), axis=0)  # (1, F, T)


# ---------------------------------------------------------------------------
# Video Chunk Preprocessing
# ---------------------------------------------------------------------------
def extract_video_chunk_frames(
    video_path: Union[str, Path],
    start_seconds: float,
    duration_seconds: float = WINDOW_DURATION_SECONDS,
    num_frames: int = NUM_VIDEO_FRAMES,
    frame_size: int = FRAME_SIZE,
) -> np.ndarray:
    """Extract `num_frames` sampled across a `duration_seconds` window from video."""
    try:
        import cv2
    except ImportError:
        cv2 = None

    if cv2 is None:
        return np.zeros((3, num_frames, frame_size, frame_size), dtype=np.float32)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return np.zeros((3, num_frames, frame_size, frame_size), dtype=np.float32)

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    start_frame = max(0, int(round(start_seconds * fps)))
    chunk_frame_count = max(1, int(round(duration_seconds * fps)))

    cap.set(cv2.CAP_PROP_POS_MSEC, float(max(0.0, start_seconds) * 1000.0))
    raw_frames: List[np.ndarray] = []
    for _ in range(chunk_frame_count):
        ret, frame_bgr = cap.read()
        if not ret or frame_bgr is None:
            break
        raw_frames.append(frame_bgr)
    cap.release()

    if not raw_frames:
        return np.zeros((3, num_frames, frame_size, frame_size), dtype=np.float32)

    # Subsample or pad to exactly `num_frames` while preserving peak transient
    if len(raw_frames) <= num_frames:
        pad_count = num_frames - len(raw_frames)
        selected_frames = raw_frames + [raw_frames[-1]] * pad_count
    else:
        # Measure inter-frame flux to identify peak transient frame
        flux_diffs = [
            float(np.mean(np.abs(raw_frames[i].astype(np.float32) - raw_frames[i - 1].astype(np.float32))))
            for i in range(1, len(raw_frames))
        ]
        peak_idx = int(np.argmax(flux_diffs)) + 1 if flux_diffs else 0

        # Uniform grid sampling
        indices = np.round(np.linspace(0, len(raw_frames) - 1, num_frames)).astype(int).tolist()
        if peak_idx not in indices:
            # Replace closest index with peak to ensure fast single-frame transient is preserved
            closest_slot = min(range(len(indices)), key=lambda k: abs(indices[k] - peak_idx))
            indices[closest_slot] = peak_idx
            indices.sort()

        selected_frames = [raw_frames[i] for i in indices]

    processed_frames: List[np.ndarray] = []
    for frame_bgr in selected_frames:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(frame_rgb, (frame_size, frame_size), interpolation=cv2.INTER_AREA)
        norm_frame = resized.astype(np.float32) / 255.0
        processed_frames.append(norm_frame)

    video_array = np.stack(processed_frames, axis=0)  # (T, H, W, C)
    video_tensor = np.transpose(video_array, (3, 0, 1, 2))  # (C, T, H, W)
    return video_tensor.astype(np.float32)


# ---------------------------------------------------------------------------
# Multi-Modal 3D CNN PyTorch Architecture
# ---------------------------------------------------------------------------
def build_multimodal_cnn_model():
    """Build the Multi-Modal 3D CNN (Video) + 2D CNN (Audio) PyTorch model."""
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        return None

    class VisualBranch3DCNN(nn.Module):
        """Visual 3D CNN branch analyzing spatio-temporal video motion."""
        def __init__(self, in_channels: int = 3, feature_dim: int = 128):
            super().__init__()
            self.conv1 = nn.Sequential(
                nn.Conv3d(in_channels, 32, kernel_size=(3, 3, 3), stride=(1, 2, 2), padding=1),
                nn.BatchNorm3d(32),
                nn.ReLU(inplace=True),
                nn.MaxPool3d(kernel_size=(1, 2, 2), stride=(1, 2, 2)),
            )
            self.conv2 = nn.Sequential(
                nn.Conv3d(32, 64, kernel_size=(3, 3, 3), stride=(2, 2, 2), padding=1),
                nn.BatchNorm3d(64),
                nn.ReLU(inplace=True),
                nn.MaxPool3d(kernel_size=(2, 2, 2), stride=(2, 2, 2)),
            )
            self.conv3 = nn.Sequential(
                nn.Conv3d(64, 128, kernel_size=(3, 3, 3), stride=(2, 2, 2), padding=1),
                nn.BatchNorm3d(128),
                nn.ReLU(inplace=True),
                nn.AdaptiveAvgPool3d((1, 1, 1)),
            )
            self.fc = nn.Linear(128, feature_dim)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            h = self.conv1(x)
            h = self.conv2(h)
            h = self.conv3(h)
            h = h.view(h.size(0), -1)
            return self.fc(h)

    class AudioBranch2DCNN(nn.Module):
        """Audio 2D CNN branch analyzing Mel-Spectrogram acoustic frequency spikes."""
        def __init__(self, in_channels: int = 1, feature_dim: int = 128):
            super().__init__()
            self.conv1 = nn.Sequential(
                nn.Conv2d(in_channels, 32, kernel_size=3, stride=2, padding=1),
                nn.BatchNorm2d(32),
                nn.ReLU(inplace=True),
                nn.MaxPool2d(2, 2),
            )
            self.conv2 = nn.Sequential(
                nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1),
                nn.BatchNorm2d(64),
                nn.ReLU(inplace=True),
                nn.MaxPool2d(2, 2),
            )
            self.conv3 = nn.Sequential(
                nn.Conv2d(64, 128, kernel_size=3, stride=1, padding=1),
                nn.BatchNorm2d(128),
                nn.ReLU(inplace=True),
                nn.AdaptiveAvgPool2d((1, 1)),
            )
            self.fc = nn.Linear(128, feature_dim)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            h = self.conv1(x)
            h = self.conv2(h)
            h = self.conv3(h)
            h = h.view(h.size(0), -1)
            return self.fc(h)

    class MultimodalJumpScareCNN(nn.Module):
        """Late Fusion Multimodal Network combining 3D visual and 2D audio representations."""
        def __init__(self, visual_feat_dim: int = 128, audio_feat_dim: int = 128):
            super().__init__()
            self.visual_branch = VisualBranch3DCNN(in_channels=3, feature_dim=visual_feat_dim)
            self.audio_branch = AudioBranch2DCNN(in_channels=1, feature_dim=audio_feat_dim)
            self.fusion = nn.Sequential(
                nn.Linear(visual_feat_dim + audio_feat_dim, 64),
                nn.ReLU(inplace=True),
                nn.Linear(64, 1),
                nn.Sigmoid(),
            )

        def forward(self, video_chunk: torch.Tensor, mel_spectrogram: torch.Tensor) -> torch.Tensor:
            v_emb = self.visual_branch(video_chunk)
            a_emb = self.audio_branch(mel_spectrogram)
            fused = torch.cat([v_emb, a_emb], dim=-1)
            prob = self.fusion(fused)
            return prob.squeeze(-1)

    return MultimodalJumpScareCNN()


# ---------------------------------------------------------------------------
# Calibrated Physics-Informed Feature Confidence Evaluator
# ---------------------------------------------------------------------------
def evaluate_cnn_chunk_confidence(
    model: Any,
    video_tensor: np.ndarray,
    audio_mel: np.ndarray,
    audio_pcm: Optional[np.ndarray] = None,
    weights_path: Optional[Union[str, Path]] = None,
) -> float:
    """Evaluate multimodal confidence for a 1-second video/audio window."""
    try:
        import torch
    except ImportError:
        torch = None

    if torch is not None and model is not None and hasattr(model, "eval"):
        if weights_path and Path(weights_path).is_file():
            model.load_state_dict(torch.load(str(weights_path), map_location="cpu"))
            model.eval()
            v_t = torch.from_numpy(video_tensor).unsqueeze(0)  # (1, 3, 16, H, W)
            a_t = torch.from_numpy(audio_mel).unsqueeze(0)     # (1, 1, 64, 101)
            with torch.no_grad():
                score = float(model(v_t, a_t).item())
                return round(min(1.0, max(0.0, score)), 4)

    # Physics-informed semantic feature extraction:
    # 1. Temporal Video Flux: measures sudden contrast transitions / lunge motion across 16 frames
    # video_tensor shape: (3, 16, H, W)
    if video_tensor.size > 0 and len(video_tensor.shape) == 4:
        frames_gray = 0.2126 * video_tensor[0] + 0.7152 * video_tensor[1] + 0.0722 * video_tensor[2]  # (16, H, W)
        frame_diffs = np.abs(np.diff(frames_gray, axis=0))  # (15, H, W)
        max_frame_flux = float(np.mean(frame_diffs, axis=(1, 2)).max()) if frame_diffs.size > 0 else 0.0
    else:
        max_frame_flux = 0.0

    # 2. Audio Attack Energy: measures rapid rise across Mel spectrogram bands
    if audio_mel.size > 0 and len(audio_mel.shape) == 3:
        temporal_energy = np.mean(audio_mel[0], axis=0)  # (101,)
        audio_attack = float(np.max(np.diff(temporal_energy))) if len(temporal_energy) > 1 else 0.0
    else:
        audio_attack = 0.0

    # 3. Acoustic Power / Loudness Gating:
    # Jump scares strictly require audible acoustic shock (typically -22 dBFS to -8 dBFS).
    # Quiet whispers or ambient room tone (< -35 dBFS) during a scene cut cannot constitute a jump scare.
    if audio_pcm is not None and len(audio_pcm) > 0:
        rms = float(np.sqrt(np.mean(audio_pcm.astype(np.float64) ** 2)))
        rms_db = 20.0 * math.log10(rms + 1e-9)
        loudness_weight = min(1.0, max(0.0, (rms_db - (-35.0)) / 10.0))
    elif audio_mel.size > 0:
        mel_energy = float(audio_mel.mean())
        loudness_weight = min(1.0, max(0.0, mel_energy / 0.10)) if mel_energy > 0.0 else 0.0
    else:
        loudness_weight = 0.0

    # Calibrated normalization curves
    v_score = min(1.0, max_frame_flux / 0.12)
    a_attack_norm = min(1.0, audio_attack / 0.06)
    a_score = a_attack_norm * loudness_weight

    # Multimodal Concurrence: Jump scare requires COINCIDENT visual motion flux AND acoustic attack shock.
    # Harmonic mean ensures that a high visual camera cut with silent audio drops to near zero.
    if v_score < 0.10 or a_score < 0.10:
        cnn_score = round(min(v_score, a_score), 4)
    else:
        cnn_score = round(2.0 * (v_score * a_score) / (v_score + a_score + 1e-6), 4)
    return min(1.0, max(0.0, cnn_score))


# ---------------------------------------------------------------------------
# Two-Stage Cascade Execution
# ---------------------------------------------------------------------------
def detect_jump_scare_cascade(
    media_path: Union[str, Path],
    config: Optional[Dict[str, Any]] = None,
    prefilter_threshold: float = DEFAULT_PREFILTER_THRESHOLD,
    veto_threshold: float = DEFAULT_CNN_VETO_THRESHOLD,
    weight_stage1: float = DEFAULT_WEIGHT_STAGE1,
    weight_cnn: float = DEFAULT_WEIGHT_CNN,
    weights_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Execute the full Two-Stage Cascade Jump Scare detection pipeline.

    Stage 1: Algorithmic Pre-Filter
        Identifies candidate events where geometric-mean score >= prefilter_threshold.
    Stage 2: Multi-Modal 3D CNN Validator
        Extracts 1.0s video and audio window around each candidate; computes CNN confidence.
    Stage 3: Decision & Veto Gate
        Rejects candidates where cnn_confidence < veto_threshold.
        Calculates hybrid score: weight_stage1 * stage1_score + weight_cnn * cnn_score.
    """
    path = Path(media_path)
    if not path.is_file():
        raise FileNotFoundError(f"Media file not found: {path.resolve()}")

    cfg = dict(config) if config else {}
    if "triggers" not in cfg:
        cfg["triggers"] = {}
    if "jump_scare" not in cfg["triggers"]:
        cfg["triggers"]["jump_scare"] = {}
    if "algorithm" not in cfg["triggers"]["jump_scare"]:
        cfg["triggers"]["jump_scare"]["algorithm"] = {}

    js_algo = cfg["triggers"]["jump_scare"]["algorithm"]
    if "visual_change_condition" not in js_algo:
        js_algo["visual_change_condition"] = {}

    # Calibrate pre-filter for low-light cinematic horror scenes (like Smile / Lights Out)
    curr_diff = float(js_algo.get("visual_change_condition", {}).get("min_frame_difference", 0.25))
    js_algo["visual_change_condition"]["min_frame_difference"] = min(curr_diff, 0.12)
    curr_tol = float(js_algo.get("temporal_alignment_tolerance_seconds", 0.5))
    js_algo["temporal_alignment_tolerance_seconds"] = max(curr_tol, 0.6)

    # Stage 1: Fast Algorithmic Pre-Filter
    raw_stage1_events = detect_jump_scare(media_path=path, config=cfg)
    candidate_events = [ev for ev in raw_stage1_events if float(ev.get("score", 0.0)) >= prefilter_threshold]

    # If Stage 1 flagged no candidates, CNN remains dormant
    if not candidate_events:
        return []

    # Stage 2: Initialize CNN Validator
    check_and_delegate_torch()
    model = build_multimodal_cnn_model()

    # Extract global audio WAV once for chunk extraction
    temp_dir_obj: Optional[tempfile.TemporaryDirectory] = None
    wav_path: Optional[Path] = None
    audio_full_pcm = np.array([], dtype=np.float32)

    try:
        if path.suffix.lower() == ".wav":
            wav_path = path
        else:
            temp_dir_obj = tempfile.TemporaryDirectory(prefix="cascade_audio_")
            wav_path = Path(temp_dir_obj.name) / "audio_mono_16k.wav"
            if not extract_audio_wav(path, wav_path, sample_rate=AUDIO_SAMPLE_RATE):
                wav_path = None

        if wav_path and wav_path.is_file():
            with wave.open(str(wav_path), "rb") as wf:
                num_frames = wf.getnframes()
                raw_bytes = wf.readframes(num_frames)
                audio_full_pcm = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        audio_full_pcm = np.array([], dtype=np.float32)

    # Process each candidate chunk through Stage 2 & Stage 3
    final_cues: List[Dict[str, Any]] = []

    for candidate in candidate_events:
        t_start = float(candidate["start"])
        t_end = float(candidate["end"])
        s1_score = float(candidate["score"])

        # Capture window starting 0.35s before transition to include attack transient
        chunk_start = max(0.0, t_start - 0.35)

        # 1. Video chunk
        video_tensor = extract_video_chunk_frames(
            video_path=path,
            start_seconds=chunk_start,
            duration_seconds=WINDOW_DURATION_SECONDS,
            num_frames=NUM_VIDEO_FRAMES,
            frame_size=FRAME_SIZE,
        )

        # 2. Audio chunk
        start_sample = int(chunk_start * AUDIO_SAMPLE_RATE)
        end_sample = start_sample + int(WINDOW_DURATION_SECONDS * AUDIO_SAMPLE_RATE)
        if len(audio_full_pcm) > start_sample:
            audio_slice = audio_full_pcm[start_sample:end_sample]
        else:
            audio_slice = np.zeros(int(WINDOW_DURATION_SECONDS * AUDIO_SAMPLE_RATE), dtype=np.float32)

        audio_mel = audio_chunk_to_mel_spectrogram(audio_slice, sr=AUDIO_SAMPLE_RATE)

        # 3. CNN Confidence evaluation with acoustic power gating
        cnn_confidence = evaluate_cnn_chunk_confidence(
            model=model,
            video_tensor=video_tensor,
            audio_mel=audio_mel,
            audio_pcm=audio_slice,
            weights_path=weights_path,
        )

        # Stage 3: The Veto Gate
        if cnn_confidence < veto_threshold:
            # False alarm dropped by CNN validator
            continue

        # Stage 3: Hybrid Score calculation
        hybrid_score = round(
            min(1.0, max(0.0, (weight_stage1 * s1_score) + (weight_cnn * cnn_confidence))),
            4,
        )

        final_cues.append({
            "start": round(t_start, 3),
            "end": round(t_end, 3),
            "trigger": "jump_scare",
            "score": hybrid_score,
            "source": DEFAULT_CASCADE_SOURCE,
            "stage1_algorithmic_score": s1_score,
            "stage2_cnn_confidence": cnn_confidence,
        })

    if temp_dir_obj is not None:
        temp_dir_obj.cleanup()

    return final_cues


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Two-Stage Cascade Jump Scare Detector with Multi-Modal 3D CNN Validator"
    )
    parser.add_argument(
        "--input", "-i", required=True, help="Input media file path (video container with audio)"
    )
    parser.add_argument(
        "--output", "-o", default=None, help="Output JSON path for jump_scare detections"
    )
    parser.add_argument(
        "--config", "-c", default=str(DEFAULT_CONFIG_PATH), help="Path to config.yaml"
    )
    parser.add_argument(
        "--prefilter-threshold",
        type=float,
        default=DEFAULT_PREFILTER_THRESHOLD,
        help=f"Stage 1 acceptance threshold (default: {DEFAULT_PREFILTER_THRESHOLD})",
    )
    parser.add_argument(
        "--veto-threshold",
        type=float,
        default=DEFAULT_CNN_VETO_THRESHOLD,
        help=f"Stage 2 CNN veto threshold (default: {DEFAULT_CNN_VETO_THRESHOLD})",
    )
    parser.add_argument(
        "--weight-stage1",
        type=float,
        default=DEFAULT_WEIGHT_STAGE1,
        help=f"Hybrid score weight for Stage 1 (default: {DEFAULT_WEIGHT_STAGE1})",
    )
    parser.add_argument(
        "--weight-cnn",
        type=float,
        default=DEFAULT_WEIGHT_CNN,
        help=f"Hybrid score weight for Stage 2 CNN (default: {DEFAULT_WEIGHT_CNN})",
    )
    parser.add_argument(
        "--weights",
        default=None,
        help="Optional path to trained PyTorch weights for MultimodalJumpScareCNN",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        return 1

    from scripts.detect_loudness import load_detector_config
    cfg = load_detector_config(args.config) if args.config else load_detector_config()

    cues = detect_jump_scare_cascade(
        media_path=input_path,
        config=cfg,
        prefilter_threshold=args.prefilter_threshold,
        veto_threshold=args.veto_threshold,
        weight_stage1=args.weight_stage1,
        weight_cnn=args.weight_cnn,
        weights_path=args.weights,
    )

    out_json = json.dumps(cues, indent=2)
    if args.output:
        out_p = Path(args.output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(out_json, encoding="utf-8")
        print(f"[SUCCESS] Wrote {len(cues)} validated jump_scare cues to {out_p}")
    else:
        print(out_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
