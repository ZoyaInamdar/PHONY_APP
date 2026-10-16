#!/usr/bin/env python3
"""Comprehensive unit and integration tests for audio loudness and spike detector.

Verifies:
1. Digital silence — no loudness events.
2. A short loud spike — detected as 'loud_spike' when configured thresholds are met.
3. Sustained loud audio — detected as 'sustained_loudness' when configured duration is met.
4. Audio below the threshold — no event.
5. An event immediately below and immediately above the threshold (boundary threshold sensitivity).
6. Events at the beginning and end of a clip.
7. A spike crossing a processing-window boundary (proper consecutive window merging).
8. A sustained event interrupted by a short dip (bridging behavior vs unbridged long dip).
9. Accurate start/end timestamps using sample_to_seconds.
10. Correct behavior for empty audio, very short audio, and partial final windows.
11. Sample-rate handling (44.1 kHz, 48 kHz) and invalid input handling.
12. Correct output fields, serialization, and contract compliance with downstream package_cues.py.
13. Streaming real-world audio processing on smoke_test assets.
14. CLI execution for scripts/detect_loudness.py and scripts/loudness_detector.py.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

from scripts.detect_loudness import (
    calculate_window_dbfs,
    detect_loud_spikes,
    detect_loudness,
    detect_loudness_from_samples,
    detect_loudness_from_wav,
    detect_sustained_loudness,
    stream_wav_windows,
)
from scripts.package_cues import merge_detections, validate_raw_detection
from scripts.timeline import sample_to_seconds, seconds_to_sample


# ==============================================================================
# Helper Fixtures & Signal Generators
# ==============================================================================

def generate_tone(
    freq_hz: float,
    duration_sec: float,
    amplitude: float = 0.5,
    sample_rate: int = 44100,
) -> np.ndarray:
    """Generate a pure sine tone normalized in [-1.0, 1.0]."""
    num_samples = int(round(duration_sec * sample_rate))
    if num_samples <= 0:
        return np.empty(0, dtype=np.float32)
    t = np.linspace(0.0, duration_sec, num_samples, endpoint=False, dtype=np.float32)
    tone = amplitude * np.sin(2.0 * np.pi * freq_hz * t)
    return tone.astype(np.float32)


def generate_silence(duration_sec: float, sample_rate: int = 44100) -> np.ndarray:
    """Generate digital silence (zeros)."""
    num_samples = int(round(duration_sec * sample_rate))
    return np.zeros(num_samples, dtype=np.float32)


def write_test_wav(path: Path, samples: np.ndarray, sample_rate: int = 44100) -> Path:
    """Write float32 samples in [-1.0, 1.0] to a 16-bit mono PCM WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clamped = np.clip(samples, -1.0, 1.0)
    int16_samples = (clamped * 32767.0).astype(np.int16)

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(int16_samples.tobytes())
    return path


# ==============================================================================
# 1. Digital Silence
# ==============================================================================

def test_digital_silence():
    """Verify digital silence produces zero events and avoids log10(0) math errors."""
    silence = generate_silence(3.0, sample_rate=44100)
    events = detect_loudness_from_samples(silence, sample_rate=44100, modes=("spike", "sustained"))
    assert events == []

    # Also test window dbfs floor calculation directly
    db = calculate_window_dbfs(silence[:4410])
    assert db == -90.0


# ==============================================================================
# 2. Short Loud Spike
# ==============================================================================

def test_short_loud_spike():
    """Verify a short loud spike against a quiet background is detected as 'loud_spike'."""
    sample_rate = 44100
    # 2.0s quiet baseline at amplitude 0.01 (~ -43 dBFS)
    baseline_quiet = generate_tone(440.0, 2.0, amplitude=0.01, sample_rate=sample_rate)
    # 0.2s loud spike at amplitude 0.8 (~ -5 dBFS, a ~38 dB increase)
    spike = generate_tone(880.0, 0.2, amplitude=0.8, sample_rate=sample_rate)
    # 2.0s quiet baseline at amplitude 0.01
    baseline_quiet_after = generate_tone(440.0, 2.0, amplitude=0.01, sample_rate=sample_rate)

    audio = np.concatenate([baseline_quiet, spike, baseline_quiet_after])
    events = detect_loudness_from_samples(
        audio,
        sample_rate=sample_rate,
        modes=("spike",),
        relative_spike_threshold_db=12.0,
    )

    assert len(events) == 1
    ev = events[0]
    assert ev["trigger"] == "loud_spike"
    assert ev["source"] == "loudness_detector"
    assert pytest.approx(ev["start"], abs=0.05) == 2.0
    assert pytest.approx(ev["end"], abs=0.05) == 2.2
    assert 0.0 <= ev["score"] <= 1.0
    assert ev["score"] > 0.5  # High confidence spike


# ==============================================================================
# 3. Sustained Loud Audio
# ==============================================================================

def test_sustained_loud_audio():
    """Verify audio remaining loud for >= 2.0s is detected as 'sustained_loudness'."""
    sample_rate = 44100
    # 1.0s quiet baseline
    quiet = generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sample_rate)
    # 2.5s continuous loud audio at amplitude 0.4 (~ -11 dBFS, above -18 dBFS threshold)
    loud = generate_tone(440.0, 2.5, amplitude=0.4, sample_rate=sample_rate)
    # 1.0s quiet after
    quiet_after = generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sample_rate)

    audio = np.concatenate([quiet, loud, quiet_after])
    events = detect_loudness_from_samples(
        audio,
        sample_rate=sample_rate,
        modes=("sustained",),
        sustained_threshold_dbfs=-18.0,
        sustained_min_duration_sec=2.0,
    )

    assert len(events) == 1
    ev = events[0]
    assert ev["trigger"] == "sustained_loudness"
    assert ev["source"] == "loudness_detector"
    assert pytest.approx(ev["start"], abs=0.05) == 1.0
    assert pytest.approx(ev["end"], abs=0.05) == 3.5
    assert (ev["end"] - ev["start"]) >= 2.0


# ==============================================================================
# 4. Audio Below Threshold
# ==============================================================================

def test_audio_below_threshold():
    """Verify audio that is consistently below threshold triggers no events."""
    sample_rate = 44100
    # Continuous moderate tone at amplitude 0.05 (~ -29 dBFS), below -18 dBFS and no relative spike
    audio = generate_tone(440.0, 3.0, amplitude=0.05, sample_rate=sample_rate)
    events = detect_loudness_from_samples(
        audio,
        sample_rate=sample_rate,
        modes=("spike", "sustained"),
        relative_spike_threshold_db=12.0,
        sustained_threshold_dbfs=-18.0,
    )
    assert events == []


# ==============================================================================
# 5. Boundary Threshold Sensitivity (Immediately Below vs Immediately Above)
# ==============================================================================

def test_spike_boundary_threshold_sensitivity():
    """Verify spike threshold: below 12.0 dB delta is rejected, >= 12.0 dB is accepted."""
    sample_rate = 44100
    baseline_amp = 0.01  # ~ -43 dBFS

    # Compute exact amplitude for 10 dB increase vs 14 dB increase
    # 20 * log10(amp / baseline_amp) = delta_db  =>  amp = baseline_amp * 10^(delta_db / 20)
    amp_sub_threshold = baseline_amp * (10.0 ** (10.0 / 20.0))  # 10.0 dB increase (< 12 dB)
    amp_super_threshold = baseline_amp * (10.0 ** (14.0 / 20.0))  # 14.0 dB increase (> 12 dB)

    # Sub-threshold: baseline + 10 dB spike + baseline
    audio_sub = np.concatenate([
        generate_tone(440.0, 2.0, amplitude=baseline_amp, sample_rate=sample_rate),
        generate_tone(440.0, 0.2, amplitude=amp_sub_threshold, sample_rate=sample_rate),
        generate_tone(440.0, 1.0, amplitude=baseline_amp, sample_rate=sample_rate),
    ])
    events_sub = detect_loudness_from_samples(
        audio_sub,
        sample_rate=sample_rate,
        modes=("spike",),
        relative_spike_threshold_db=12.0,
    )
    assert events_sub == []

    # Super-threshold: baseline + 14 dB spike + baseline
    audio_super = np.concatenate([
        generate_tone(440.0, 2.0, amplitude=baseline_amp, sample_rate=sample_rate),
        generate_tone(440.0, 0.2, amplitude=amp_super_threshold, sample_rate=sample_rate),
        generate_tone(440.0, 1.0, amplitude=baseline_amp, sample_rate=sample_rate),
    ])
    events_super = detect_loudness_from_samples(
        audio_super,
        sample_rate=sample_rate,
        modes=("spike",),
        relative_spike_threshold_db=12.0,
    )
    assert len(events_super) == 1
    assert events_super[0]["trigger"] == "loud_spike"


def test_sustained_boundary_threshold_sensitivity():
    """Verify sustained loudness threshold sensitivity: below -18 dBFS rejected, above accepted."""
    sample_rate = 44100
    # Amplitude for -20 dBFS: RMS = 0.1, sine amp = 0.1 * sqrt(2) ≈ 0.1414
    # Amplitude for -16 dBFS: RMS = 10^(-16/20) ≈ 0.1585, sine amp = 0.1585 * sqrt(2) ≈ 0.2241
    audio_sub = generate_tone(440.0, 3.0, amplitude=0.14, sample_rate=sample_rate)  # ~ -20 dBFS
    events_sub = detect_loudness_from_samples(
        audio_sub,
        sample_rate=sample_rate,
        modes=("sustained",),
        sustained_threshold_dbfs=-18.0,
        sustained_min_duration_sec=2.0,
    )
    assert events_sub == []

    audio_super = generate_tone(440.0, 3.0, amplitude=0.25, sample_rate=sample_rate)  # ~ -15 dBFS
    events_super = detect_loudness_from_samples(
        audio_super,
        sample_rate=sample_rate,
        modes=("sustained",),
        sustained_threshold_dbfs=-18.0,
        sustained_min_duration_sec=2.0,
    )
    assert len(events_super) == 1
    assert events_super[0]["trigger"] == "sustained_loudness"


# ==============================================================================
# 6. Events at Beginning and End of Clip
# ==============================================================================

def test_events_at_beginning_and_end_of_clip():
    """Verify events beginning at sample 0 and ending at clip end are bounded accurately."""
    sample_rate = 44100

    # Sustained loudness starting at t=0.0
    audio_start = np.concatenate([
        generate_tone(440.0, 2.5, amplitude=0.4, sample_rate=sample_rate),
        generate_silence(2.0, sample_rate=sample_rate),
    ])
    events_start = detect_loudness_from_samples(
        audio_start,
        sample_rate=sample_rate,
        modes=("sustained",),
        sustained_threshold_dbfs=-18.0,
        sustained_min_duration_sec=2.0,
    )
    assert len(events_start) == 1
    assert events_start[0]["start"] == 0.0
    assert pytest.approx(events_start[0]["end"], abs=0.05) == 2.5

    # Sustained loudness ending at clip end
    audio_end = np.concatenate([
        generate_silence(2.0, sample_rate=sample_rate),
        generate_tone(440.0, 2.5, amplitude=0.4, sample_rate=sample_rate),
    ])
    total_sec = len(audio_end) / sample_rate
    events_end = detect_loudness_from_samples(
        audio_end,
        sample_rate=sample_rate,
        modes=("sustained",),
        sustained_threshold_dbfs=-18.0,
        sustained_min_duration_sec=2.0,
    )
    assert len(events_end) == 1
    assert pytest.approx(events_end[0]["start"], abs=0.05) == 2.0
    assert pytest.approx(events_end[0]["end"], abs=0.05) == total_sec


# ==============================================================================
# 7. Spike Crossing a Window Boundary (Continuous Segment Merging)
# ==============================================================================

def test_spike_crossing_window_boundary():
    """Verify a spike spanning across consecutive analysis windows merges into one event."""
    sample_rate = 44100
    # Window duration = 0.1s (4410 samples).
    # Place a 0.15s loud burst starting at 0.95s, ending at 1.10s.
    # It covers part of window [0.9s, 1.0s), all of [1.0s, 1.1s), and part of [1.1s, 1.2s).
    pre = generate_tone(440.0, 0.95, amplitude=0.01, sample_rate=sample_rate)
    spike = generate_tone(880.0, 0.15, amplitude=0.8, sample_rate=sample_rate)
    post = generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sample_rate)

    audio = np.concatenate([pre, spike, post])
    events = detect_loudness_from_samples(
        audio,
        sample_rate=sample_rate,
        modes=("spike",),
        relative_spike_threshold_db=12.0,
    )

    # Must be merged into a single event, NOT separate fragmented events
    assert len(events) == 1
    ev = events[0]
    assert ev["trigger"] == "loud_spike"
    assert ev["start"] <= 1.0
    assert ev["end"] >= 1.1
    assert (ev["end"] - ev["start"]) >= 0.15


# ==============================================================================
# 8. Sustained Event Interrupted by Brief Dip (Bridged vs Unbridged)
# ==============================================================================

def test_sustained_event_dip_bridging():
    """Verify brief dips (<= 0.5s) are bridged, while longer dips (> 0.5s) partition runs."""
    sample_rate = 44100

    # 1. Brief dip (0.3s dip <= 0.5s max_dip)
    part1 = generate_tone(440.0, 1.5, amplitude=0.4, sample_rate=sample_rate)
    brief_dip = generate_silence(0.3, sample_rate=sample_rate)
    part2 = generate_tone(440.0, 1.5, amplitude=0.4, sample_rate=sample_rate)
    audio_bridged = np.concatenate([part1, brief_dip, part2])

    events_bridged = detect_loudness_from_samples(
        audio_bridged,
        sample_rate=sample_rate,
        modes=("sustained",),
        sustained_threshold_dbfs=-18.0,
        sustained_min_duration_sec=2.0,
    )
    # The dip was bridged: 1.5 + 0.3 + 1.5 = 3.3s >= 2.0s min duration
    assert len(events_bridged) == 1
    assert pytest.approx(events_bridged[0]["start"], abs=0.05) == 0.0
    assert pytest.approx(events_bridged[0]["end"], abs=0.05) == 3.3

    # 2. Long dip (1.0s dip > 0.5s max_dip)
    long_dip = generate_silence(1.0, sample_rate=sample_rate)
    audio_unbridged = np.concatenate([part1, long_dip, part2])

    events_unbridged = detect_loudness_from_samples(
        audio_unbridged,
        sample_rate=sample_rate,
        modes=("sustained",),
        sustained_threshold_dbfs=-18.0,
        sustained_min_duration_sec=2.0,
    )
    # Because the dip is 1.0s, the runs are split into two 1.5s segments.
    # Since neither segment reaches the 2.0s minimum duration, neither qualifies!
    assert events_unbridged == []


# ==============================================================================
# 9. Accurate Timestamps with Timeline Helper
# ==============================================================================

def test_accurate_timestamps_using_sample_to_seconds():
    """Verify timestamps strictly match sample_to_seconds boundaries."""
    sample_rate = 44100
    baseline = generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sample_rate)
    spike = generate_tone(880.0, 0.3, amplitude=0.8, sample_rate=sample_rate)
    after = generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sample_rate)

    audio = np.concatenate([baseline, spike, after])
    events = detect_loudness_from_samples(
        audio,
        sample_rate=sample_rate,
        modes=("spike",),
    )

    assert len(events) == 1
    ev = events[0]
    # At 44.1 kHz, 1.0s is exactly sample 44100
    expected_start = sample_to_seconds(44100, sample_rate)
    assert pytest.approx(ev["start"], abs=0.05) == expected_start
    # End must be strictly greater than start
    assert ev["end"] > ev["start"]


# ==============================================================================
# 10. Empty Audio, Very Short Audio, and Partial Final Windows
# ==============================================================================

def test_empty_audio():
    """Verify empty array/file returns empty detection list without crashing."""
    empty = np.empty(0, dtype=np.float32)
    assert detect_loudness_from_samples(empty, sample_rate=44100) == []


def test_very_short_audio():
    """Verify audio shorter than 1 window duration (e.g. 50 samples) is handled safely."""
    short = np.array([0.1, -0.2, 0.05] * 20, dtype=np.float32)
    events = detect_loudness_from_samples(short, sample_rate=44100)
    assert isinstance(events, list)


def test_partial_final_window():
    """Verify audio with non-integer window sample count processes partial window safely."""
    sample_rate = 44100
    # 1.25 seconds: 12 windows of 0.1s + 1 partial window of 0.05s
    audio = generate_tone(440.0, 1.25, amplitude=0.01, sample_rate=sample_rate)
    events = detect_loudness_from_samples(audio, sample_rate=sample_rate)
    assert isinstance(events, list)


# ==============================================================================
# 11. Sample Rate Handling & Invalid Input
# ==============================================================================

def test_sample_rate_handling(tmp_path: Path):
    """Verify correct time calculation on 48,000 Hz and 44,100 Hz WAV files."""
    for sr in [44100, 48000]:
        base = generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr)
        spk = generate_tone(880.0, 0.2, amplitude=0.8, sample_rate=sr)
        post = generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr)
        wav_file = write_test_wav(tmp_path / f"test_{sr}.wav", np.concatenate([base, spk, post]), sample_rate=sr)

        events = detect_loudness_from_wav(wav_file, modes=("spike",))
        assert len(events) == 1
        assert pytest.approx(events[0]["start"], abs=0.05) == 1.0
        assert pytest.approx(events[0]["end"], abs=0.05) == 1.2


def test_invalid_input_handling(tmp_path: Path):
    """Verify proper exceptions on missing files and invalid types."""
    with pytest.raises(FileNotFoundError):
        detect_loudness_from_wav(tmp_path / "does_not_exist.wav")

    with pytest.raises(TypeError):
        detect_loudness(12345)


# ==============================================================================
# 12. Contract Compliance & Downstream Cue Packaging Integration
# ==============================================================================

def test_contract_compliance_and_packaging_integration():
    """Verify raw detections comply with shared contract and package cleanly via package_cues."""
    sample_rate = 44100
    base = generate_tone(440.0, 2.0, amplitude=0.01, sample_rate=sample_rate)
    spk = generate_tone(880.0, 0.3, amplitude=0.8, sample_rate=sample_rate)
    post = generate_tone(440.0, 2.0, amplitude=0.01, sample_rate=sample_rate)
    audio = np.concatenate([base, spk, post])

    events = detect_loudness_from_samples(audio, sample_rate=sample_rate, modes=("spike",))
    assert len(events) == 1
    det = events[0]

    # Validate against package_cues shared contract
    canonical_triggers = {"flashing", "loud_spike", "gunshot", "explosion", "scream", "profanity", "spider"}
    validate_raw_detection(det, canonical_triggers=canonical_triggers)

    # Test merge_detections can consume it cleanly
    merged = merge_detections([det], canonical_triggers=canonical_triggers)
    assert len(merged) == 1
    assert merged[0]["trigger"] == "loud_spike"
    assert merged[0]["source"] == "loudness_detector"


# ==============================================================================
# 13. Real Audio Streaming Smoke Test
# ==============================================================================

def test_real_audio_streaming_smoke_test():
    """Verify streaming execution on actual smoke_test media without OOM."""
    wav_path = Path("data/processed/smoke_test/audio_mono_44100.wav")
    if not wav_path.is_file():
        pytest.skip("smoke_test WAV not found; skipping real media test")

    events = detect_loudness_from_wav(wav_path, modes=("spike",))
    assert isinstance(events, list)
    assert len(events) > 0

    # Ensure all timestamps are finite, non-negative, and bounded by file duration
    for ev in events:
        assert 0.0 <= ev["start"] < ev["end"] <= 635.0
        assert 0.0 <= ev["score"] <= 1.0
        assert ev["trigger"] == "loud_spike"
        assert ev["source"] == "loudness_detector"


# ==============================================================================
# 14. CLI Execution
# ==============================================================================

def test_cli_execution(tmp_path: Path):
    """Verify CLI execution runs and writes valid JSON raw detections."""
    sr = 44100
    audio = np.concatenate([
        generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
        generate_tone(880.0, 0.2, amplitude=0.8, sample_rate=sr),
        generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
    ])
    wav_p = write_test_wav(tmp_path / "cli_test.wav", audio, sample_rate=sr)
    out_json = tmp_path / "out_detections.json"

    # Test scripts/detect_loudness.py
    cmd = [
        sys.executable,
        "scripts/detect_loudness.py",
        "--input",
        str(wav_p),
        "--output",
        str(out_json),
        "--mode",
        "spike",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0
    assert out_json.is_file()

    with out_json.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["schema_version"] == 1
    assert data["record_type"] == "raw_detections"
    assert len(data["detections"]) == 1
    assert data["detections"][0]["trigger"] == "loud_spike"

    # Test alias scripts/loudness_detector.py
    cmd_alias = [
        sys.executable,
        "scripts/loudness_detector.py",
        "--input",
        str(wav_p),
        "--mode",
        "spike",
    ]
    proc_alias = subprocess.run(cmd_alias, capture_output=True, text=True)
    assert proc_alias.returncode == 0


# ==============================================================================
# 15. Ground Truth Benchmark & Exponential Decay Onset Regression
# ==============================================================================

def test_exponential_decay_spike_exact_onset_and_duration_regression():
    """Verify that transient spikes with exponential decay yield exact onset timing and zero false positives.

    Acoustic model matches synthesize_audio_event('loud_spike'):
    - +15 dB peak with exponential decay transient exp(-t / 0.12).
    - Ground-truth onset at t=1.500s.
    - Detector detects onset with zero onset error (<= 0.05s tolerance).
    """
    sr = 44100
    total_dur = 4.0
    total_samples = int(total_dur * sr)

    # Ambient baseline at -30 dBFS
    rng = np.random.default_rng(42)
    ambient = rng.normal(0, 0.03, total_samples).astype(np.float32)

    # Place exponential decay spike at 1.50s - 2.00s
    spike_start = 1.5
    spike_dur = 0.5
    spike_samples = int(spike_dur * sr)
    t = np.linspace(0.0, spike_dur, spike_samples, endpoint=False, dtype=np.float32)
    decay = np.exp(-t / 0.12).astype(np.float32)
    tone = 0.65 * np.sin(2 * np.pi * 380 * t) * decay
    noise = 0.35 * rng.normal(0, 1, spike_samples).astype(np.float32) * decay
    spike_audio = tone + noise
    spike_audio = (spike_audio / np.max(np.abs(spike_audio))) * 0.90

    start_idx = int(spike_start * sr)
    ambient[start_idx : start_idx + spike_samples] += spike_audio

    detections = detect_loudness_from_samples(ambient, sample_rate=sr, modes=("spike",))
    assert len(detections) == 1
    det = detections[0]

    # Onset must match 1.50s within one analysis window (0.1s)
    assert pytest.approx(det["start"], abs=0.05) == spike_start
    assert det["trigger"] == "loud_spike"
    assert det["score"] > 0.5
    # The detected interval must stay within the spike window
    assert det["end"] <= (spike_start + spike_dur + 0.1)


def test_ground_truth_test_clips_evaluation():
    """Evaluate detector on positive test clips and negative controls if present in data/test_clips."""
    clips_dir = Path("data/test_clips")
    pos_isolated = clips_dir / "audio_loud_spike_isolated.mp4"
    pos_test = clips_dir / "audio_loud_spike_test.mp4"
    neg_ambient = clips_dir / "audio_negative_ambient.mp4"
    neg_dialogue = clips_dir / "audio_negative_dialogue.mp4"

    if not (pos_isolated.is_file() and pos_test.is_file() and neg_ambient.is_file() and neg_dialogue.is_file()):
        pytest.skip("Synthetic test clips not present; skipping dataset benchmark test")

    import tempfile
    from scripts.ingest_media import extract_audio

    with tempfile.TemporaryDirectory() as td:
        tmp_p = Path(td)

        # 1. Positive isolated: GT [1.500, 1.800)
        wav_iso = tmp_p / "iso.wav"
        extract_audio(pos_isolated, wav_iso)
        dets_iso = detect_loudness_from_wav(wav_iso, modes=("spike",))
        assert len(dets_iso) == 1
        assert pytest.approx(dets_iso[0]["start"], abs=0.05) == 1.500
        assert dets_iso[0]["end"] <= 1.850

        # 2. Positive test: GT [2.000, 2.500)
        wav_test = tmp_p / "test.wav"
        extract_audio(pos_test, wav_test)
        dets_test = detect_loudness_from_wav(wav_test, modes=("spike",))
        assert len(dets_test) == 1
        assert pytest.approx(dets_test[0]["start"], abs=0.05) == 2.000
        assert dets_test[0]["end"] <= 2.550

        # 3. Negative controls: 0 detections
        wav_amb = tmp_p / "amb.wav"
        extract_audio(neg_ambient, wav_amb)
        assert detect_loudness_from_wav(wav_amb, modes=("spike",)) == []

        wav_dia = tmp_p / "dia.wav"
        extract_audio(neg_dialogue, wav_dia)
        assert detect_loudness_from_wav(wav_dia, modes=("spike",)) == []
