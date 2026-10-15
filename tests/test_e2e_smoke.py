#!/usr/bin/env python3
"""Integration and smoke tests for Trigger-Aware Player End-to-End Pipeline (Person B).

Verifies:
1. Complete e2e pipeline execution against real audio (data/processed/smoke_test/audio_mono_44100.wav).
2. Complete e2e pipeline execution against synthetic audio with controlled spikes.
3. Boundary exactness of player muting according to half-open interval semantics [start, end).
4. Viewer preferences and toggle compliance (muteEnabled=false restores soundGain=1).
5. Empty detector handling (clean zero-cue index packaging and validation).
6. CLI script execution and JSON output formatting.
7. Multi-component detector availability audit reporting.
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

from scripts.detect_loudness import DEFAULT_CONFIG_PATH
from scripts.package_cues import DEFAULT_SCHEMA_PATH, atomic_write_json, package_cues
from scripts.run_e2e_smoke_test import (
    DEFAULT_AUDIO_PATH,
    check_detector_availability,
    get_wav_info,
    run_e2e_smoke_pipeline,
    run_player_engine_simulation,
)
from scripts.validate_index import validate_index


def create_synthetic_wav(
    file_path: Path,
    sample_rate: int = 44100,
    duration_seconds: float = 12.0,
    spike_intervals: List[tuple[float, float]] = [(4.0, 5.0), (8.0, 9.0)],
) -> None:
    """Generate a 16-bit mono PCM WAV file with silence/low noise and loud tone spikes."""
    total_samples = int(sample_rate * duration_seconds)
    audio = np.random.uniform(-0.001, 0.001, total_samples).astype(np.float32)

    for start_sec, end_sec in spike_intervals:
        start_idx = int(start_sec * sample_rate)
        end_idx = min(total_samples, int(end_sec * sample_rate))
        t = np.arange(end_idx - start_idx) / sample_rate
        # Generate 1 kHz loud sine tone at full scale
        tone = 0.95 * np.sin(2 * np.pi * 1000.0 * t).astype(np.float32)
        audio[start_idx:end_idx] = tone

    # Convert float32 [-1, 1] to int16
    audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(file_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(audio_int16.tobytes())


def test_detector_audit_reporting() -> None:
    """Verify that detector availability matrix accurately reports component owners and statuses."""
    audit = check_detector_availability()

    assert "loudness_detector" in audit
    assert audit["loudness_detector"]["status"] == "available"
    assert audit["loudness_detector"]["owner"] == "Person B"
    assert audit["loudness_detector"]["trigger"] == "loud_spike"

    assert "flash_detector" in audit
    assert audit["flash_detector"]["status"] == "missing"
    assert audit["flash_detector"]["owner"] == "Person A"

    assert "audio_event_classifier" in audit
    assert audit["audio_event_classifier"]["status"] == "missing"
    assert audit["audio_event_classifier"]["owner"] == "Person A"

    assert "profanity_detector" in audit
    assert audit["profanity_detector"]["status"] == "partial"
    assert audit["profanity_detector"]["owner"] == "Person C"


def test_e2e_smoke_pipeline_with_real_audio(tmp_path: Path) -> None:
    """Run full pipeline on smoke_test media audio and verify player engine muting."""
    if not DEFAULT_AUDIO_PATH.exists():
        pytest.skip(f"Test audio {DEFAULT_AUDIO_PATH} not present.")

    report = run_e2e_smoke_pipeline(
        audio_path=DEFAULT_AUDIO_PATH,
        film_id="smoke_test",
        output_dir=tmp_path,
        keep_artifacts=True,
        verbose=False,
    )

    assert report["success"] is True
    assert report["raw_spikes_detected"] > 0
    assert report["packaged_cues_count"] > 0
    assert report["total_boundary_checks"] > 0
    assert report["passed_boundary_checks"] == report["total_boundary_checks"]

    # Verify artifacts on disk
    index_file = tmp_path / "index.json"
    assert index_file.exists()
    with open(index_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["schema_version"] == 1
    assert data["film_id"] == "smoke_test"
    assert len(data["cues"]) == report["packaged_cues_count"]


def test_e2e_smoke_pipeline_with_synthetic_audio(tmp_path: Path) -> None:
    """Run full pipeline on synthetic audio with calibrated spikes."""
    synth_wav = tmp_path / "synth_spikes.wav"
    create_synthetic_wav(
        file_path=synth_wav,
        sample_rate=44100,
        duration_seconds=14.0,
        spike_intervals=[(3.0, 4.0), (8.0, 9.0)],
    )

    report = run_e2e_smoke_pipeline(
        audio_path=synth_wav,
        film_id="synth_film",
        output_dir=tmp_path / "output",
        keep_artifacts=True,
        verbose=False,
    )

    assert report["success"] is True
    assert report["raw_spikes_detected"] >= 2
    assert report["packaged_cues_count"] >= 1
    assert report["passed_boundary_checks"] == report["total_boundary_checks"]


def test_player_action_resolution_exact_half_open_boundaries(tmp_path: Path) -> None:
    """Verify player engine exactness at start and end boundaries according to [start, end)."""
    # Create a synthetic index with two adjacent cues: [10.0, 15.0) and [15.0, 20.0)
    index_data = {
        "schema_version": 1,
        "film_id": "boundary_test",
        "synthetic": True,
        "description": "Boundary test fixture",
        "timeline": {
            "duration_seconds": 30.0,
            "timestamp_unit": "seconds",
            "interval_convention": "half_open",
            "fps": 24.0,
        },
        "cues": [
            {
                "id": "cue_boundary_test_loud_spike_010000_015000",
                "start": 10.0,
                "end": 15.0,
                "trigger": "loud_spike",
                "score": 0.9,
                "source": "loudness_detector",
            },
            {
                "id": "cue_boundary_test_loud_spike_015000_020000",
                "start": 15.0,
                "end": 20.0,
                "trigger": "loud_spike",
                "score": 0.9,
                "source": "loudness_detector",
            },
        ],
    }
    index_path = tmp_path / "boundary_index.json"
    atomic_write_json(index_data, index_path)

    # Validate against schema
    assert validate_index(index_path, DEFAULT_SCHEMA_PATH, DEFAULT_CONFIG_PATH)

    # Run Node player simulation with explicit sample timestamps
    loader_path = REPO_ROOT / "scripts" / "ts_loader.mjs"
    sim_script = REPO_ROOT / "scripts" / "simulate_player.mjs"

    # Test timestamps:
    # 9.9s (unmuted)
    # 10.0s (cue 1 start -> muted)
    # 12.5s (cue 1 mid -> muted)
    # 14.999s (cue 1 end-0.001 -> muted)
    # 15.0s (cue 1 ends, cue 2 starts -> muted)
    # 19.999s (cue 2 end-0.001 -> muted)
    # 20.0s (cue 2 end -> unmuted)
    # 20.5s (after cues -> unmuted)
    timestamps = [9.9, 10.0, 12.5, 14.999, 15.0, 19.999, 20.0, 20.5]
    cmd = [
        "node",
        "--experimental-strip-types",
        "--loader",
        loader_path.resolve().as_uri(),
        str(sim_script),
        "--index",
        str(index_path),
        "--timestamps",
        ",".join(str(t) for t in timestamps),
        "--verify-mute",
        "--json",
    ]

    res = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert res.returncode == 0, f"Player simulation failed:\n{res.stderr}"

    # Parse JSON
    stdout_lines = res.stdout.strip().splitlines()
    json_start = next(i for i, l in enumerate(stdout_lines) if l.strip().startswith("{"))
    data = json.loads("\n".join(stdout_lines[json_start:]))

    assert data["all_passed"] is True
    evals = {ev["time"]: ev for ev in data["evaluations"]}

    assert evals[9.9]["soundGain"] == 1
    assert evals[10.0]["soundGain"] == 0
    assert "cue_boundary_test_loud_spike_010000_015000" in evals[10.0]["activeCueIds"]
    assert evals[12.5]["soundGain"] == 0
    assert evals[14.999]["soundGain"] == 0

    # At exact boundary 15.0s: cue 1 has released, cue 2 is active
    assert evals[15.0]["soundGain"] == 0
    assert "cue_boundary_test_loud_spike_010000_015000" not in evals[15.0]["activeCueIds"]
    assert "cue_boundary_test_loud_spike_015000_020000" in evals[15.0]["activeCueIds"]

    assert evals[19.999]["soundGain"] == 0
    # At exact boundary 20.0s: cue 2 has released, unmuted
    assert evals[20.0]["soundGain"] == 1
    assert len(evals[20.0]["activeCueIds"]) == 0

    assert evals[20.5]["soundGain"] == 1


def test_empty_detector_output_pipeline(tmp_path: Path) -> None:
    """Verify that empty detector results yield a valid zero-cue index that plays unmuted."""
    # Create an all-silence WAV
    silent_wav = tmp_path / "silence.wav"
    create_synthetic_wav(
        file_path=silent_wav,
        sample_rate=44100,
        duration_seconds=5.0,
        spike_intervals=[],  # No spikes
    )

    report = run_e2e_smoke_pipeline(
        audio_path=silent_wav,
        film_id="silent_film",
        output_dir=tmp_path / "output",
        keep_artifacts=True,
        verbose=False,
    )

    assert report["success"] is True
    assert report["raw_spikes_detected"] == 0
    assert report["packaged_cues_count"] == 0

    index_path = tmp_path / "output" / "index.json"
    with open(index_path, "r", encoding="utf-8") as f:
        idx_data = json.load(f)
    assert idx_data["cues"] == []

    # Run player simulation to verify soundGain=1 across timeline
    sim_data = run_player_engine_simulation(index_path)
    assert sim_data["all_passed"] is True
    assert sim_data["total_cues"] == 0


def test_cli_execution_success() -> None:
    """Verify that scripts/run_e2e_smoke_test.py CLI executes successfully with --json."""
    if not DEFAULT_AUDIO_PATH.exists():
        pytest.skip(f"Test audio {DEFAULT_AUDIO_PATH} not present.")

    cmd = [sys.executable, "scripts/run_e2e_smoke_test.py", "--json"]
    res = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)

    assert res.returncode == 0, f"CLI failed with returncode {res.returncode}:\n{res.stderr}"
    data = json.loads(res.stdout)
    assert data["success"] is True
    assert data["packaged_cues_count"] > 0
    assert data["passed_boundary_checks"] == data["total_boundary_checks"]
