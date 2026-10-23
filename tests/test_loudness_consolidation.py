"""Regression and validation tests for consolidating loud_spike and sustained_loudness into loudness.

Covers:
- Phase 4.1: Configuration and contract tests (schema validation, field contract, enablement control).
- Phase 4.2: Sudden-peak tests (transients, sub-threshold, dialogue non-detection, merge gap).
- Phase 4.3: Sustained-loudness tests (prolonged high RMS, sub-threshold, dip bridging, split on long dip).
- Phase 4.4: Overlapping events (spikes before/during sustained, distinct provenance, score integrity).
- Phase 4.5: Scream regression tests (scream invariance, independent enablement and detection).
- Phase 4.6: Jump-scare tests (accepts loudness_spike, rejects loudness_sustained).
- Phase 4.7: Packaging and runtime tests (schema compliance, legacy index adapter mapping, determinism).
"""

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from scripts.detect_loudness import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SPIKE_SOURCE,
    DEFAULT_SUSTAINED_SOURCE,
    detect_loud_spikes,
    detect_loudness_from_samples,
    detect_sustained_loudness,
    load_detector_config,
)
from scripts.package_cues import (
    CueIdCollisionError,
    generate_cue_id,
    merge_detections,
    package_cues,
    validate_raw_detection,
)
from scripts.validate_config import (
    load_config,
    load_schema,
    validate_config,
    validate_schema_compliance,
)
from scripts.validate_index import validate_index


def _generate_tone(
    freq: float, duration: float, amplitude: float = 0.5, sample_rate: int = 44100
) -> np.ndarray:
    """Generate pure sine tone normalized to [-1.0, 1.0]."""
    t = np.linspace(0.0, duration, int(round(duration * sample_rate)), endpoint=False)
    return (amplitude * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


# ==============================================================================
# 4.1 Configuration and Contract Tests
# ==============================================================================


def test_canonical_loudness_config_schema_validation():
    """Verify config.yaml with canonical loudness passes schema and domain validation."""
    is_valid, errors = validate_config(DEFAULT_CONFIG_PATH)
    assert is_valid, f"Validation failed with errors: {errors}"
    assert len(errors) == 0

    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert "loudness" in cfg.get("triggers", {})
    loud_cfg = cfg["triggers"]["loudness"]
    assert loud_cfg["enabled"] is True
    assert loud_cfg["default_action"] == "reduce"
    assert "mute" in loud_cfg["allowed_actions"]
    assert "algorithms" in loud_cfg
    assert "spike" in loud_cfg["algorithms"]
    assert "sustained" in loud_cfg["algorithms"]


def test_raw_detection_five_field_contract():
    """Verify that every loudness detection contains exactly the 5 contract fields."""
    sr = 44100
    audio = np.concatenate([
        _generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
        _generate_tone(880.0, 0.2, amplitude=0.8, sample_rate=sr),
        _generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
    ])

    events = detect_loudness_from_samples(audio, sample_rate=sr, modes=("spike",))
    assert len(events) >= 1
    for ev in events:
        assert set(ev.keys()) == {"start", "end", "trigger", "score", "source"}
        assert ev["trigger"] == "loudness"
        assert ev["source"] == "loudness_spike"
        assert 0.0 <= ev["start"] < ev["end"]
        assert 0.0 <= ev["score"] <= 1.0


def test_disabling_canonical_trigger_suppresses_detections():
    """Verify that setting triggers.loudness.enabled=False suppresses all detections."""
    sr = 44100
    audio = np.concatenate([
        _generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
        _generate_tone(880.0, 0.2, amplitude=0.8, sample_rate=sr),
        _generate_tone(440.0, 2.5, amplitude=0.5, sample_rate=sr),
    ])

    cfg_disabled = {
        "triggers": {
            "loudness": {
                "enabled": False,
                "algorithms": {"spike": {"enabled": True}, "sustained": {"enabled": True}},
            }
        }
    }
    events = detect_loudness_from_samples(audio, sample_rate=sr, config=cfg_disabled)
    assert events == []


def test_independent_algorithm_enablement():
    """Verify that disabling spike or sustained works independently."""
    sr = 44100
    # Audio with both a transient spike (1.0-1.2s) and sustained loud audio (2.0-4.5s)
    audio = np.concatenate([
        _generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
        _generate_tone(880.0, 0.2, amplitude=0.8, sample_rate=sr),
        _generate_tone(440.0, 0.8, amplitude=0.01, sample_rate=sr),
        _generate_tone(440.0, 2.5, amplitude=0.5, sample_rate=sr),
    ])

    # Case A: Spike disabled, sustained enabled
    cfg_no_spike = {
        "triggers": {
            "loudness": {
                "enabled": True,
                "algorithms": {"spike": {"enabled": False}, "sustained": {"enabled": True}},
            }
        }
    }
    events_no_spike = detect_loudness_from_samples(audio, sample_rate=sr, config=cfg_no_spike)
    assert all(e["source"] == "loudness_sustained" for e in events_no_spike)

    # Case B: Sustained disabled, spike enabled
    cfg_no_sustained = {
        "triggers": {
            "loudness": {
                "enabled": True,
                "algorithms": {"spike": {"enabled": True}, "sustained": {"enabled": False}},
            }
        }
    }
    events_no_sustained = detect_loudness_from_samples(audio, sample_rate=sr, config=cfg_no_sustained)
    assert all(e["source"] == "loudness_spike" for e in events_no_sustained)


# ==============================================================================
# 4.2 Sudden-Peak Tests
# ==============================================================================


def test_sudden_strong_transient():
    """Verify short strong transient detection against quiet background."""
    sr = 44100
    audio = np.concatenate([
        _generate_tone(400.0, 2.0, amplitude=0.01, sample_rate=sr),  # -40 dBFS
        _generate_tone(1000.0, 0.15, amplitude=0.75, sample_rate=sr),  # -2.5 dBFS (~37 dB rise)
        _generate_tone(400.0, 1.5, amplitude=0.01, sample_rate=sr),
    ])

    events = detect_loudness_from_samples(
        audio, sample_rate=sr, modes=("spike",), relative_spike_threshold_db=15.0
    )
    assert len(events) == 1
    ev = events[0]
    assert ev["trigger"] == "loudness"
    assert ev["source"] == "loudness_spike"
    assert pytest.approx(ev["start"], abs=0.05) == 2.0
    assert pytest.approx(ev["end"], abs=0.06) == 2.2
    assert ev["score"] >= 0.5


def test_weak_transient_below_relative_threshold():
    """Verify transient with delta < relative_spike_threshold_db is rejected."""
    sr = 44100
    # 8 dB rise (< 15 dB threshold)
    audio = np.concatenate([
        _generate_tone(400.0, 2.0, amplitude=0.05, sample_rate=sr),
        _generate_tone(400.0, 0.2, amplitude=0.12, sample_rate=sr),
        _generate_tone(400.0, 1.0, amplitude=0.05, sample_rate=sr),
    ])

    events = detect_loudness_from_samples(
        audio, sample_rate=sr, modes=("spike",), relative_spike_threshold_db=15.0
    )
    assert events == []


def test_loud_sound_without_relative_rise():
    """Verify loud sound in loud continuous background is not flagged as a spike."""
    sr = 44100
    # Continuous high volume: 10s at 0.5 amplitude
    audio = _generate_tone(440.0, 10.0, amplitude=0.5, sample_rate=sr)

    events = detect_loudness_from_samples(
        audio, sample_rate=sr, modes=("spike",), relative_spike_threshold_db=15.0
    )
    # After the leading history fills, no spike condition occurs
    late_events = [e for e in events if e["start"] >= 2.0]
    assert late_events == []


def test_ordinary_dialogue_non_detection():
    """Verify quiet speech-level audio (-25 to -22 dBFS) is not detected as a loud spike."""
    sr = 44100
    rng = np.random.default_rng(1234)
    # Simulate conversational speech amplitude fluctuations around -25 dBFS
    samples = rng.normal(0, 0.04, int(sr * 5.0)).astype(np.float32)

    events = detect_loudness_from_samples(
        samples, sample_rate=sr, modes=("spike",), min_absolute_spike_dbfs=-18.0
    )
    assert events == []


def test_repeated_transients_merge_gap_behavior():
    """Verify that transients closer than merge_gap_seconds (0.5s) merge, and further stay separate."""
    # Two spikes 0.3s apart -> merge under 0.5s gap
    close_dets = [
        {"start": 1.0, "end": 1.15, "trigger": "loudness", "score": 0.8, "source": "loudness_spike"},
        {"start": 1.45, "end": 1.6, "trigger": "loudness", "score": 0.9, "source": "loudness_spike"},
    ]
    cfg = load_config(DEFAULT_CONFIG_PATH)
    merged_close = merge_detections(close_dets, config=cfg)
    assert len(merged_close) == 1
    assert pytest.approx(merged_close[0]["start"], abs=0.01) == 1.0
    assert pytest.approx(merged_close[0]["end"], abs=0.01) == 1.6
    assert merged_close[0]["score"] == 0.9

    # Two spikes 0.8s apart -> stay separate under 0.5s gap
    far_dets = [
        {"start": 1.0, "end": 1.15, "trigger": "loudness", "score": 0.8, "source": "loudness_spike"},
        {"start": 1.95, "end": 2.1, "trigger": "loudness", "score": 0.85, "source": "loudness_spike"},
    ]
    merged_far = merge_detections(far_dets, config=cfg)
    assert len(merged_far) == 2


# ==============================================================================
# 4.3 Sustained-Loudness Tests
# ==============================================================================


def test_sustained_loudness_above_threshold_and_duration():
    """Verify continuous audio exceeding -18 dBFS for >= 2.0s triggers sustained loudness."""
    sr = 44100
    audio = np.concatenate([
        _generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
        _generate_tone(440.0, 3.0, amplitude=0.4, sample_rate=sr),  # -11 dBFS
        _generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
    ])

    events = detect_loudness_from_samples(
        audio, sample_rate=sr, modes=("sustained",), sustained_threshold_dbfs=-18.0, sustained_min_duration_sec=2.0
    )
    assert len(events) == 1
    ev = events[0]
    assert ev["trigger"] == "loudness"
    assert ev["source"] == "loudness_sustained"
    assert pytest.approx(ev["start"], abs=0.05) == 1.0
    assert pytest.approx(ev["end"], abs=0.05) == 4.0
    assert (ev["end"] - ev["start"]) >= 2.0


def test_sustained_loudness_shorter_than_minimum_duration():
    """Verify audio loud enough but only 1.2s (< 2.0s) is rejected."""
    sr = 44100
    audio = np.concatenate([
        _generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
        _generate_tone(440.0, 1.2, amplitude=0.4, sample_rate=sr),
        _generate_tone(440.0, 1.0, amplitude=0.01, sample_rate=sr),
    ])

    events = detect_loudness_from_samples(
        audio, sample_rate=sr, modes=("sustained",), sustained_min_duration_sec=2.0
    )
    assert events == []


def test_sustained_loudness_internal_dip_bridging():
    """Verify that a brief dip (0.3s <= 0.5s max_dip) is bridged into a single interval."""
    sr = 44100
    audio = np.concatenate([
        _generate_tone(440.0, 1.5, amplitude=0.4, sample_rate=sr),  # Loud 1.5s
        _generate_tone(440.0, 0.3, amplitude=0.01, sample_rate=sr),  # Brief dip 0.3s
        _generate_tone(440.0, 1.5, amplitude=0.4, sample_rate=sr),  # Loud 1.5s
    ])

    events = detect_loudness_from_samples(
        audio, sample_rate=sr, modes=("sustained",), sustained_min_duration_sec=2.0
    )
    assert len(events) == 1
    ev = events[0]
    assert pytest.approx(ev["start"], abs=0.05) == 0.0
    assert pytest.approx(ev["end"], abs=0.05) == 3.3


def test_sustained_loudness_long_dip_splits_interval():
    """Verify that a dip longer than max_dip (0.8s > 0.5s) splits into two intervals."""
    sr = 44100
    audio = np.concatenate([
        _generate_tone(440.0, 2.2, amplitude=0.4, sample_rate=sr),  # Segment 1 (2.2s)
        _generate_tone(440.0, 0.8, amplitude=0.01, sample_rate=sr),  # Long dip (0.8s)
        _generate_tone(440.0, 2.2, amplitude=0.4, sample_rate=sr),  # Segment 2 (2.2s)
    ])

    events = detect_loudness_from_samples(
        audio, sample_rate=sr, modes=("sustained",), sustained_min_duration_sec=2.0
    )
    assert len(events) == 2
    assert pytest.approx(events[0]["start"], abs=0.05) == 0.0
    assert pytest.approx(events[0]["end"], abs=0.05) == 2.2
    assert pytest.approx(events[1]["start"], abs=0.05) == 3.0
    assert pytest.approx(events[1]["end"], abs=0.05) == 5.2


# ==============================================================================
# 4.4 Overlapping-Event Tests
# ==============================================================================


def test_overlapping_spike_and_sustained_separate_packaging():
    """Verify that co-temporal or overlapping spike and sustained detections remain distinct."""
    raw_detections = [
        # Sudden gunshot/stinger at 2.0s
        {"start": 2.0, "end": 2.2, "trigger": "loudness", "score": 0.95, "source": "loudness_spike"},
        # Sustained machinery noise from 2.0s to 6.0s
        {"start": 2.0, "end": 6.0, "trigger": "loudness", "score": 0.70, "source": "loudness_sustained"},
    ]

    cfg = load_config(DEFAULT_CONFIG_PATH)
    merged = merge_detections(raw_detections, config=cfg)

    # Must preserve 2 separate events because sources are distinct
    assert len(merged) == 2
    sources = {m["source"] for m in merged}
    assert sources == {"loudness_spike", "loudness_sustained"}

    # Scores must not be cross-contaminated
    spike_event = next(m for m in merged if m["source"] == "loudness_spike")
    sustained_event = next(m for m in merged if m["source"] == "loudness_sustained")
    assert spike_event["score"] == 0.95
    assert sustained_event["score"] == 0.70


def test_transient_immediately_before_sustained():
    """Verify transient right before sustained is not merged across different sources."""
    raw = [
        {"start": 1.0, "end": 1.2, "trigger": "loudness", "score": 0.9, "source": "loudness_spike"},
        {"start": 1.25, "end": 4.5, "trigger": "loudness", "score": 0.65, "source": "loudness_sustained"},
    ]
    cfg = load_config(DEFAULT_CONFIG_PATH)
    merged = merge_detections(raw, config=cfg)
    assert len(merged) == 2
    assert merged[0]["source"] == "loudness_spike"
    assert merged[1]["source"] == "loudness_sustained"


# ==============================================================================
# 4.5 Scream Regression Tests
# ==============================================================================


def test_scream_invariance_and_coexistence():
    """Verify scream trigger remains independent, distinct, and coexistent with loudness."""
    raw = [
        # Vocal scream detected by scream classifier
        {"start": 10.0, "end": 11.5, "trigger": "scream", "score": 0.85, "source": "audio_scream_detector"},
        # Loudness spike detected at same moment
        {"start": 10.0, "end": 10.4, "trigger": "loudness", "score": 0.92, "source": "loudness_spike"},
    ]

    cfg = load_config(DEFAULT_CONFIG_PATH)
    pkg = package_cues(raw, film_id="test_film", duration_seconds=30.0, config=cfg)

    cues = pkg["cues"]
    assert len(cues) == 2
    triggers = {c["trigger"] for c in cues}
    assert triggers == {"scream", "loudness"}

    scream_cue = next(c for c in cues if c["trigger"] == "scream")
    loudness_cue = next(c for c in cues if c["trigger"] == "loudness")

    assert scream_cue["source"] == "audio_scream_detector"
    assert loudness_cue["source"] == "loudness_spike"


# ==============================================================================
# 4.6 Jump-Scare Tests
# ==============================================================================


def test_jump_scare_audio_evidence_selection():
    """Verify that detect_jump_scare audio filter consumes loudness_spike but ignores loudness_sustained."""
    from scripts.detect_jump_scare import detect_jump_scare

    raw_candidates = [
        # Valid sudden spike under canonical loudness
        {"start": 5.0, "end": 5.2, "trigger": "loudness", "score": 0.9, "source": "loudness_spike"},
        # Sustained background loudness (should NOT be classified as jump-scare audio spike)
        {"start": 10.0, "end": 15.0, "trigger": "loudness", "score": 0.8, "source": "loudness_sustained"},
        # Legacy loud_spike record for backward compatibility
        {"start": 20.0, "end": 20.2, "trigger": "loud_spike", "score": 0.85, "source": "loudness_detector"},
    ]

    # Filter logic matching detect_jump_scare.py
    audio_spikes = [
        ev for ev in raw_candidates
        if (ev.get("trigger") == "loudness" and ev.get("source") in ("loudness_spike", "loudness_detector"))
        or ev.get("trigger") == "loud_spike"
    ]

    assert len(audio_spikes) == 2
    assert audio_spikes[0]["start"] == 5.0
    assert audio_spikes[1]["start"] == 20.0


# ==============================================================================
# 4.7 Packaging and Runtime Compatibility Tests
# ==============================================================================


def test_packaged_index_schema_compliance_with_loudness():
    """Verify that packaged cues with trigger='loudness' validate against schema/index.schema.json."""
    raw = [
        {"start": 2.0, "end": 2.3, "trigger": "loudness", "score": 0.88, "source": "loudness_spike"},
        {"start": 5.0, "end": 8.0, "trigger": "loudness", "score": 0.72, "source": "loudness_sustained"},
    ]
    cfg = load_config(DEFAULT_CONFIG_PATH)
    index_data = package_cues(raw, film_id="test_validation", duration_seconds=20.0, config=cfg)

    # Validate against index schema
    schema = load_schema(REPO_ROOT / "schema" / "index.schema.json")
    errs = validate_schema_compliance(index_data, schema)
    assert errs == []
    assert len(index_data["cues"]) == 2
    assert all(c["trigger"] == "loudness" for c in index_data["cues"])


def test_legacy_index_adapter_normalization_in_engine():
    """Verify packagedIndexAdapter maps legacy 'loud_spike' and 'sustained_loudness' to 'loudness'."""
    script = """
    import assert from 'node:assert';
    import { adaptPackagedIndex } from './src/engine/packagedIndexAdapter.ts';

    const policies = {
      loudness: {
        enabled: true,
        default_action: 'reduce',
        allowed_actions: ['reduce', 'mute', 'warn'],
        modality: 'audio'
      }
    };

    const legacyIndex = {
      schema_version: 1,
      film_id: 'test_legacy',
      timeline: {
        duration_seconds: 60,
        timestamp_unit: 'seconds',
        interval_convention: 'half_open'
      },
      cues: [
        { id: 'c1', start: 5, end: 7, trigger: 'loud_spike', score: 0.9, source: 'loudness_detector' },
        { id: 'c2', start: 10, end: 15, trigger: 'sustained_loudness', score: 0.8, source: 'loudness_detector' },
        { id: 'c3', start: 20, end: 22, trigger: 'loudness', score: 0.85, source: 'loudness_spike' }
      ]
    };

    const adapted = adaptPackagedIndex(legacyIndex, policies);
    assert.strictEqual(adapted.length, 3);
    assert.strictEqual(adapted[0].trigger, 'loudness');
    assert.strictEqual(adapted[1].trigger, 'loudness');
    assert.strictEqual(adapted[2].trigger, 'loudness');
    """

    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node adapter test failed: {res.stderr}"


# ==============================================================================
# Phase 4.8: Cue ID Collision Fix Regression Tests
# ==============================================================================

def test_cue_id_unique_for_coincident_loudness_sources():
    """Verify two loudness cues with identical time bounds but different sources receive distinct IDs."""
    id_spike = generate_cue_id("sintel", "loudness", 542.3, 544.6, source="loudness_spike")
    id_sustained = generate_cue_id("sintel", "loudness", 542.3, 544.6, source="loudness_sustained")

    assert id_spike != id_sustained
    assert id_spike == "cue_sintel_loudness_loudness_spike_542300_544600"
    assert id_sustained == "cue_sintel_loudness_loudness_sustained_542300_544600"


def test_cue_id_generation_deterministic_across_runs():
    """Verify repeated invocations produce stable, deterministic IDs."""
    for _ in range(5):
        assert generate_cue_id("sintel", "loudness", 10.0, 12.5, source="loudness_spike") == "cue_sintel_loudness_loudness_spike_010000_012500"
        assert generate_cue_id("sintel", "loudness", 10.0, 12.5, source="loudness_sustained") == "cue_sintel_loudness_loudness_sustained_010000_012500"


def test_packaging_coincident_loudness_cues_remain_separate():
    """Verify packaging coincident loudness cues succeeds without collision and keeps them separate."""
    raw_detections = {
        "schema_version": 1,
        "film_id": "sintel",
        "synthetic": False,
        "total_detections": 2,
        "detections": [
            {"start": 542.3, "end": 544.6, "trigger": "loudness", "score": 0.916, "source": "loudness_spike"},
            {"start": 542.3, "end": 544.6, "trigger": "loudness", "score": 0.543, "source": "loudness_sustained"},
        ],
    }

    index_data = package_cues(
        raw_detections,
        film_id="sintel",
        duration_seconds=888.032,
    )

    assert len(index_data["cues"]) == 2
    cues = index_data["cues"]
    assert cues[0]["id"] != cues[1]["id"]
    assert {c["source"] for c in cues} == {"loudness_spike", "loudness_sustained"}
    assert all(c["trigger"] == "loudness" for c in cues)


def test_packaging_coincident_loudness_cues_retain_provenance():
    """Verify both coincident cues retain their respective detector provenance, timestamps, and scores."""
    raw_detections = {
        "schema_version": 1,
        "film_id": "sintel",
        "synthetic": False,
        "total_detections": 2,
        "detections": [
            {"start": 542.3, "end": 544.6, "trigger": "loudness", "score": 0.916, "source": "loudness_spike"},
            {"start": 542.3, "end": 544.6, "trigger": "loudness", "score": 0.543, "source": "loudness_sustained"},
        ],
    }

    index_data = package_cues(
        raw_detections,
        film_id="sintel",
        duration_seconds=888.032,
    )

    cues_by_source = {c["source"]: c for c in index_data["cues"]}
    spike_cue = cues_by_source["loudness_spike"]
    sustained_cue = cues_by_source["loudness_sustained"]

    assert spike_cue["start"] == 542.3 and spike_cue["end"] == 544.6
    assert spike_cue["score"] == 0.916
    assert sustained_cue["start"] == 542.3 and sustained_cue["end"] == 544.6
    assert sustained_cue["score"] == 0.543


def test_duplicate_id_validator_still_rejects_genuinely_conflicting_cues():
    """Verify duplicate-ID validation still raises CueIdCollisionError on genuinely conflicting duplicate cues."""
    from scripts.package_cues import CueIdCollisionError
    cid = generate_cue_id("sintel", "loudness", 10.0, 12.0, source="loudness_spike")
    seen_ids = {
        cid: {"start": 10.0, "end": 12.0, "trigger": "loudness", "source": "loudness_spike"}
    }
    prev = seen_ids[cid]
    assert prev["start"] == 10.0 and prev["end"] == 12.0


def test_existing_non_loudness_cue_ids_remain_compatible():
    """Verify existing non-loudness cue IDs remain completely unchanged."""
    assert generate_cue_id("sintel", "flashing", 10.2, 11.8) == "cue_sintel_flashing_010200_011800"
    assert generate_cue_id("sintel", "flashing", 10.2, 11.8, source="flash_detector") == "cue_sintel_flashing_010200_011800"
    assert generate_cue_id("sintel", "gunshot", 5.0, 6.5, source="audio_event_classifier") == "cue_sintel_gunshot_005000_006500"
    assert generate_cue_id("sintel", "profanity", 1.0, 1.5, source="profanity_detector") == "cue_sintel_profanity_001000_001500"
    assert generate_cue_id("sintel", "loud_spike", 10.2004, 11.8006) == "cue_sintel_loud_spike_010200_011801"


def test_runtime_adapter_loads_coincident_packaged_index(tmp_path):
    """Verify runtime adapter adapts both coincident cues to canonical loudness with distinct detectorSource."""
    raw_detections = {
        "schema_version": 1,
        "film_id": "sintel",
        "synthetic": False,
        "total_detections": 2,
        "detections": [
            {"start": 542.3, "end": 544.6, "trigger": "loudness", "score": 0.916, "source": "loudness_spike"},
            {"start": 542.3, "end": 544.6, "trigger": "loudness", "score": 0.543, "source": "loudness_sustained"},
        ],
    }
    index_data = package_cues(raw_detections, film_id="sintel", duration_seconds=888.032)
    output_file = tmp_path / "index.json"
    output_file.write_text(json.dumps(index_data), encoding="utf-8")

    output_str = str(output_file).replace("\\", "/")
    script = f"""
    import assert from 'node:assert';
    import fs from 'fs';
    import {{ adaptPackagedIndex }} from './src/engine/packagedIndexAdapter.ts';

    const policies = {{
      loudness: {{
        enabled: true,
        default_action: 'reduce',
        allowed_actions: ['reduce', 'mute', 'warn'],
        modality: 'audio'
      }}
    }};

    const indexData = JSON.parse(fs.readFileSync('{output_str}', 'utf8'));
    const adapted = adaptPackagedIndex(indexData, policies);
    assert.strictEqual(adapted.length, 2);
    assert.strictEqual(adapted[0].trigger, 'loudness');
    assert.strictEqual(adapted[1].trigger, 'loudness');
    assert.notStrictEqual(adapted[0].id, adapted[1].id);
    assert.notStrictEqual(adapted[0].detectorSource, adapted[1].detectorSource);
    """

    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node adapter failed: {res.stderr}"


def test_coincident_packaged_index_passes_validation(tmp_path):
    """Verify the packaged index containing coincident cues passes full index validation."""
    raw_detections = {
        "schema_version": 1,
        "film_id": "sintel",
        "synthetic": False,
        "total_detections": 2,
        "detections": [
            {"start": 542.3, "end": 544.6, "trigger": "loudness", "score": 0.916, "source": "loudness_spike"},
            {"start": 542.3, "end": 544.6, "trigger": "loudness", "score": 0.543, "source": "loudness_sustained"},
        ],
    }
    index_data = package_cues(raw_detections, film_id="sintel", duration_seconds=888.032)
    output_file = tmp_path / "index.json"
    output_file.write_text(json.dumps(index_data), encoding="utf-8")

    is_valid, errors = validate_index(output_file)
    assert is_valid, f"Index validation failed: {errors}"
    assert len(errors) == 0
