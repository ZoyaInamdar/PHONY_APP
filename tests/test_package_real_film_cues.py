"""Unit tests for scripts/package_real_film_cues.py (Person A/B).

Verifies that packaged real-film indexes:
- Have synthetic == False
- Comply with schema/index.schema.json
- Pass domain validation rules (timestamps within duration, unique IDs)
- Correctly represent real detector outputs without cue fabrication
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jsonschema.exceptions import ValidationError

from scripts.package_real_film_cues import (
    package_film_cues,
    package_tears_of_steel,
)
from scripts.validate_index import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SCHEMA_PATH,
    get_canonical_triggers_from_config,
    load_config_data,
    load_index,
    load_schema,
    validate_domain_rules,
    validate_schema_compliance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_sintel_trailer_packaged_index_validity():
    """Verify Sintel trailer packaged index is non-synthetic and passes all validations."""
    index_path = REPO_ROOT / "data" / "processed" / "sintel_trailer" / "index.json"
    assert index_path.is_file(), f"Index file missing: {index_path}"

    with index_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # Core non-synthetic contract
    assert data.get("synthetic") is False, "Sintel trailer index must be non-synthetic (synthetic=False)"
    assert data.get("film_id") == "sintel_trailer"
    assert "timeline" in data
    dur = float(data["timeline"]["duration_seconds"])
    assert dur > 50.0 and dur < 55.0

    # Ensure cues are non-empty and bounded by media duration
    cues = data.get("cues", [])
    assert len(cues) > 0, "Real detector pipeline on sintel_trailer must produce at least one detection"
    for cue in cues:
        assert float(cue["start"]) >= 0.0
        assert float(cue["end"]) > float(cue["start"])
        assert float(cue["end"]) <= dur, f"Cue {cue['id']} exceeds film duration {dur}"
        assert 0.0 <= float(cue["score"]) <= 1.0

    # Full schema & domain compliance
    schema = load_schema(DEFAULT_SCHEMA_PATH)
    canonical_triggers = get_canonical_triggers_from_config(DEFAULT_CONFIG_PATH)

    validate_schema_compliance(data, schema)
    validate_domain_rules(data, canonical_triggers=canonical_triggers)


def test_tears_of_steel_packaged_index_validity():
    """Verify Tears of Steel packaged index is non-synthetic and passes all validations."""
    index_path = REPO_ROOT / "data" / "processed" / "tears_of_steel" / "index.json"
    assert index_path.is_file(), f"Index file missing: {index_path}"

    with index_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # Core non-synthetic contract
    assert data.get("synthetic") is False, "Tears of Steel index must be non-synthetic (synthetic=False)"
    assert data.get("film_id") == "tears_of_steel"
    assert "timeline" in data
    dur = float(data["timeline"]["duration_seconds"])
    assert abs(dur - 734.167) < 0.1

    # Cues bounded by media duration
    cues = data.get("cues", [])
    assert len(cues) > 0, "Tears of Steel must contain at least one subtitle cue"
    for cue in cues:
        assert float(cue["start"]) >= 0.0
        assert float(cue["end"]) > float(cue["start"])
        assert float(cue["end"]) <= dur, f"Cue {cue['id']} exceeds film duration {dur}"
        assert 0.0 <= float(cue["score"]) <= 1.0

    # Full schema & domain compliance
    schema = load_schema(DEFAULT_SCHEMA_PATH)
    canonical_triggers = get_canonical_triggers_from_config(DEFAULT_CONFIG_PATH)

    validate_schema_compliance(data, schema)
    validate_domain_rules(data, canonical_triggers=canonical_triggers)


def test_valid_cue_packaging(tmp_path: Path):
    """Verify package_film_cues produces a valid index with correct half-open intervals and IDs."""
    det1 = tmp_path / "det1.json"
    det1.write_text(
        json.dumps({
            "film_id": "test_film",
            "synthetic": False,
            "timeline": {"duration_seconds": 60.0, "fps": 24.0},
            "detections": [
                {"start": 1.5, "end": 3.0, "trigger": "flashing", "score": 0.85, "source": "flash_detector"},
            ],
        }),
        encoding="utf-8",
    )
    det2 = tmp_path / "det2.json"
    det2.write_text(
        json.dumps({
            "film_id": "test_film",
            "synthetic": False,
            "timeline": {"duration_seconds": 60.0, "fps": 24.0},
            "detections": [
                {"start": 10.0, "end": 12.5, "trigger": "loud_spike", "score": 0.92, "source": "loudness_detector"},
            ],
        }),
        encoding="utf-8",
    )

    out_index = tmp_path / "out_index.json"
    res = package_film_cues(
        input_paths=[det1, det2],
        output_path=out_index,
        film_id="test_film",
        duration_seconds=60.0,
        fps=24.0,
        synthetic=False,
    )

    assert out_index.is_file()
    assert res["film_id"] == "test_film"
    assert res["synthetic"] is False
    assert len(res["cues"]) == 2
    assert res["cues"][0]["id"] == "cue_test_film_flashing_001500_003000"
    assert res["cues"][1]["id"] == "cue_test_film_loud_spike_010000_012500"
    assert res["timeline"]["interval_convention"] == "half_open"


def test_empty_detector_output(tmp_path: Path):
    """Verify packager handles empty detector outputs (0 detections) without error."""
    empty_det = tmp_path / "empty_det.json"
    empty_det.write_text(
        json.dumps({
            "film_id": "test_film",
            "synthetic": False,
            "timeline": {"duration_seconds": 30.0, "fps": 24.0},
            "detections": [],
        }),
        encoding="utf-8",
    )
    active_det = tmp_path / "active_det.json"
    active_det.write_text(
        json.dumps({
            "film_id": "test_film",
            "synthetic": False,
            "timeline": {"duration_seconds": 30.0, "fps": 24.0},
            "detections": [
                {"start": 5.0, "end": 6.0, "trigger": "gunshot", "score": 0.77, "source": "audio_event_classifier"}
            ],
        }),
        encoding="utf-8",
    )

    out_index = tmp_path / "empty_test_index.json"
    res = package_film_cues(
        input_paths=[empty_det, active_det],
        output_path=out_index,
        film_id="test_film",
        duration_seconds=30.0,
        synthetic=False,
    )

    assert len(res["cues"]) == 1
    assert res["cues"][0]["trigger"] == "gunshot"


def test_missing_detector_output_raises(tmp_path: Path):
    """Verify packager raises FileNotFoundError on missing input detector file."""
    missing_file = tmp_path / "does_not_exist.json"
    out_index = tmp_path / "index.json"

    with pytest.raises(FileNotFoundError):
        package_film_cues(
            input_paths=[missing_file],
            output_path=out_index,
            film_id="test_film",
            duration_seconds=30.0,
            synthetic=False,
        )


def test_invalid_timestamps_and_malformed_cues(tmp_path: Path):
    """Verify packager rejects negative starts, end <= start, non-numeric values, or end > duration."""
    out_index = tmp_path / "index.json"

    # 1. start < 0
    bad1 = tmp_path / "bad1.json"
    bad1.write_text(
        json.dumps({
            "film_id": "test",
            "synthetic": True,
            "detections": [{"start": -1.0, "end": 5.0, "trigger": "flashing", "score": 0.8, "source": "flash_detector"}],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=">= 0.0"):
        package_film_cues(input_paths=[bad1], output_path=out_index, film_id="test", duration_seconds=30.0, synthetic=True)

    # 2. end <= start
    bad2 = tmp_path / "bad2.json"
    bad2.write_text(
        json.dumps({
            "film_id": "test",
            "synthetic": True,
            "detections": [{"start": 5.0, "end": 5.0, "trigger": "flashing", "score": 0.8, "source": "flash_detector"}],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strictly greater"):
        package_film_cues(input_paths=[bad2], output_path=out_index, film_id="test", duration_seconds=30.0, synthetic=True)

    # 3. end > duration_seconds
    bad3 = tmp_path / "bad3.json"
    bad3.write_text(
        json.dumps({
            "film_id": "test",
            "synthetic": True,
            "detections": [{"start": 10.0, "end": 35.0, "trigger": "flashing", "score": 0.8, "source": "flash_detector"}],
        }),
        encoding="utf-8",
    )
    with pytest.raises((ValueError, ValidationError)):
        package_film_cues(input_paths=[bad3], output_path=out_index, film_id="test", duration_seconds=30.0, synthetic=True)


def test_unsupported_triggers_rejected(tmp_path: Path):
    """Verify packager rejects unsupported trigger identifiers."""
    bad_trig = tmp_path / "unsupported_trigger.json"
    bad_trig.write_text(
        json.dumps({
            "film_id": "test",
            "synthetic": True,
            "detections": [{"start": 1.0, "end": 2.0, "trigger": "fake_hazard_trigger", "score": 0.8, "source": "test"}],
        }),
        encoding="utf-8",
    )
    out_index = tmp_path / "index.json"

    with pytest.raises((ValueError, ValidationError)):
        package_film_cues(input_paths=[bad_trig], output_path=out_index, film_id="test", duration_seconds=30.0, synthetic=True)


def test_mismatched_film_identity_rejected(tmp_path: Path):
    """Verify packager raises ValueError when merging detector outputs from conflicting films."""
    f1 = tmp_path / "f1.json"
    f1.write_text(
        json.dumps({
            "film_id": "sintel_trailer",
            "synthetic": False,
            "detections": [{"start": 1.0, "end": 2.0, "trigger": "flashing", "score": 0.8, "source": "flash_detector"}],
        }),
        encoding="utf-8",
    )
    f2 = tmp_path / "f2.json"
    f2.write_text(
        json.dumps({
            "film_id": "tears_of_steel",
            "synthetic": False,
            "detections": [{"start": 3.0, "end": 4.0, "trigger": "loud_spike", "score": 0.8, "source": "loudness_detector"}],
        }),
        encoding="utf-8",
    )

    out_index = tmp_path / "index.json"
    with pytest.raises(ValueError, match="Incompatible film metadata"):
        package_film_cues(
            input_paths=[f1, f2],
            output_path=out_index,
            film_id="sintel_trailer",
            duration_seconds=50.0,
        )


def test_missing_source_media_blocker():
    """Verify package_tears_of_steel halts and returns False when full media asset is missing."""
    # When tears_of_steel.webm is missing, package_tears_of_steel without allow_subtitles_only returns False
    res = package_tears_of_steel("python3", allow_subtitles_only=False)
    assert res is False, "Missing tears_of_steel.webm must report blocker and return False"


def test_synthetic_versus_real_provenance(tmp_path: Path):
    """Verify synthetic flag propagation and guardrails preventing fake real data."""
    # 1. Real input marked as real produces synthetic=False
    real_input = tmp_path / "real_input.json"
    real_input.write_text(
        json.dumps({
            "film_id": "film",
            "synthetic": False,
            "detections": [{"start": 1.0, "end": 2.0, "trigger": "flashing", "score": 0.5, "source": "flash_detector"}],
        }),
        encoding="utf-8",
    )
    real_index = tmp_path / "real_index.json"
    r1 = package_film_cues(input_paths=[real_input], output_path=real_index, film_id="film", duration_seconds=10.0, synthetic=False)
    assert r1["synthetic"] is False

    # 2. Synthetic input marked as synthetic produces synthetic=True
    synth_input = tmp_path / "synth_input.json"
    synth_input.write_text(
        json.dumps({
            "film_id": "film",
            "synthetic": True,
            "detections": [{"start": 1.0, "end": 2.0, "trigger": "flashing", "score": 0.5, "source": "flash_detector"}],
        }),
        encoding="utf-8",
    )
    synth_index = tmp_path / "synth_index.json"
    r2 = package_film_cues(input_paths=[synth_input], output_path=synth_index, film_id="film", duration_seconds=10.0, synthetic=True)
    assert r2["synthetic"] is True

    # 3. Guardrail: Attempting to mark synthetic input as real must raise ValueError
    fraud_index = tmp_path / "fraud_index.json"
    with pytest.raises(ValueError, match="Cannot mark index as real"):
        package_film_cues(input_paths=[synth_input], output_path=fraud_index, film_id="film", duration_seconds=10.0, synthetic=False)


def test_schema_validation(tmp_path: Path):
    """Verify packaged index strictly satisfies schema/index.schema.json."""
    det = tmp_path / "det.json"
    det.write_text(
        json.dumps({
            "film_id": "film_a",
            "synthetic": False,
            "detections": [{"start": 2.0, "end": 4.5, "trigger": "scream", "score": 0.65, "source": "audio_event_classifier"}],
        }),
        encoding="utf-8",
    )
    out_index = tmp_path / "schema_test_index.json"
    index_data = package_film_cues(input_paths=[det], output_path=out_index, film_id="film_a", duration_seconds=10.0, synthetic=False)

    schema = load_schema(DEFAULT_SCHEMA_PATH)
    validate_schema_compliance(index_data, schema)


def test_deterministic_output(tmp_path: Path):
    """Verify packager produces bit-for-bit identical JSON and cue IDs on repeated runs."""
    d1 = tmp_path / "d1.json"
    d1.write_text(
        json.dumps({
            "film_id": "film_det",
            "synthetic": False,
            "detections": [
                {"start": 1.0, "end": 2.0, "trigger": "flashing", "score": 0.8, "source": "flash_detector"},
                {"start": 4.0, "end": 6.0, "trigger": "loud_spike", "score": 0.9, "source": "loudness_detector"},
            ],
        }),
        encoding="utf-8",
    )

    out1 = tmp_path / "run1.json"
    out2 = tmp_path / "run2.json"

    package_film_cues(input_paths=[d1], output_path=out1, film_id="film_det", duration_seconds=10.0, synthetic=False)
    package_film_cues(input_paths=[d1], output_path=out2, film_id="film_det", duration_seconds=10.0, synthetic=False)

    assert out1.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")


