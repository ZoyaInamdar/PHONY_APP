"""Unit tests for Trigger-Aware Player packaged index (index.json) and raw cues (cues.json).

Verifies schema compliance, business rule validations (intervals, duration bounds,
chronological ordering, uniqueness, non-finite values), CLI entry points, true
trigger synchronization with config.yaml, and media timeline duration verification.
"""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import yaml

from scripts.validate_config import CANONICAL_TRIGGER_IDS
from scripts.validate_index import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_INDEX_PATH,
    DEFAULT_SCHEMA_PATH,
    check_trigger_synchronization,
    get_canonical_triggers_from_config,
    load_config_data,
    load_index,
    load_schema,
    probe_media_duration,
    validate_domain_rules,
    validate_index,
    validate_schema_compliance,
    verify_media_duration,
)

INDEX_PATH = Path("data/index.json")
CUES_PATH = Path("data/cues.json")
SCHEMA_PATH = Path("schema/index.schema.json")
CONFIG_PATH = Path("config.yaml")


@pytest.fixture
def valid_index_dict() -> Dict[str, Any]:
    """Return a pristine copy of the valid synthetic index dictionary."""
    return load_index(INDEX_PATH)


@pytest.fixture
def index_schema_dict() -> Dict[str, Any]:
    """Return the loaded index schema dictionary."""
    return load_schema(SCHEMA_PATH)


@pytest.fixture
def config_dict() -> Dict[str, Any]:
    """Return the loaded configuration dictionary."""
    return load_config_data(CONFIG_PATH) or {}


# ------------------------------------------------------------------------------
# 1. Basic Existence and Structure
# ------------------------------------------------------------------------------

def test_index_and_cues_files_exist():
    """Verify that both data/index.json and data/cues.json exist in repository."""
    assert INDEX_PATH.is_file(), f"Expected {INDEX_PATH} to exist."
    assert CUES_PATH.is_file(), f"Expected {CUES_PATH} to exist."
    assert SCHEMA_PATH.is_file(), f"Expected {SCHEMA_PATH} to exist."


def test_cues_raw_detections_contract():
    """Verify that data/cues.json adheres to the shared raw detection record contract."""
    with CUES_PATH.open("r", encoding="utf-8") as f:
        cues_data = json.load(f)

    assert cues_data.get("record_type") == "raw_detections"
    assert cues_data.get("synthetic") is True
    detections = cues_data.get("detections", [])
    assert len(detections) >= 9, "Expected synthetic fixture to cover all canonical triggers"

    triggers_found = set()
    for det in detections:
        for field in ["start", "end", "trigger", "score", "source"]:
            assert field in det, f"Detection missing contract field '{field}': {det}"

        start = det["start"]
        end = det["end"]
        trigger = det["trigger"]
        score = det["score"]
        source = det["source"]

        assert isinstance(start, (int, float)) and start >= 0.0
        assert isinstance(end, (int, float)) and end > start
        assert trigger in CANONICAL_TRIGGER_IDS
        triggers_found.add(trigger)
        assert isinstance(score, (int, float)) and 0.0 <= score <= 1.0
        assert isinstance(source, str) and len(source.strip()) > 0

    assert triggers_found.issubset(set(CANONICAL_TRIGGER_IDS)), (
        f"Missing triggers in raw detections fixture: {set(CANONICAL_TRIGGER_IDS) - triggers_found}"
    )


def test_valid_synthetic_index():
    """Verify that the synthetic data/index.json passes all validations cleanly."""
    is_valid, errors = validate_index(INDEX_PATH, SCHEMA_PATH, CONFIG_PATH)
    assert is_valid, f"Validation failed with errors: {errors}"
    assert len(errors) == 0


def test_missing_required_top_level_fields(valid_index_dict, index_schema_dict):
    """Verify that missing top-level required fields are rejected by schema."""
    for required_field in ["schema_version", "film_id", "timeline", "cues"]:
        mutated = copy.deepcopy(valid_index_dict)
        del mutated[required_field]
        errors = validate_schema_compliance(mutated, index_schema_dict)
        assert any(required_field in err for err in errors), (
            f"Expected schema to reject missing top-level field '{required_field}'."
        )


def test_missing_cue_required_fields(valid_index_dict, index_schema_dict):
    """Verify that a cue missing any required contract field is rejected."""
    for field in ["id", "start", "end", "trigger", "score", "source"]:
        mutated = copy.deepcopy(valid_index_dict)
        del mutated["cues"][0][field]
        errors = validate_schema_compliance(mutated, index_schema_dict)
        assert len(errors) > 0, f"Expected rejection when cue is missing '{field}'."


def test_unknown_trigger_id(valid_index_dict, index_schema_dict):
    """Verify that an unknown/unsupported trigger identifier is rejected."""
    mutated = copy.deepcopy(valid_index_dict)
    mutated["cues"][0]["trigger"] = "unsupported_alien_attack"

    canonical = get_canonical_triggers_from_config(CONFIG_PATH)
    schema_errors = validate_schema_compliance(mutated, index_schema_dict)
    domain_errors = validate_domain_rules(mutated, canonical)

    assert len(schema_errors) > 0 or len(domain_errors) > 0
    assert any("unsupported_alien_attack" in err for err in schema_errors + domain_errors)


# ------------------------------------------------------------------------------
# 2. Score Semantics and Boundary Constraints
# ------------------------------------------------------------------------------

def test_score_boundaries_zero_and_one_valid(valid_index_dict, index_schema_dict):
    """Verify that scores at boundary values 0.0 and 1.0 are valid."""
    mutated = copy.deepcopy(valid_index_dict)
    mutated["cues"][0]["score"] = 0.0
    mutated["cues"][1]["score"] = 1.0

    schema_errors = validate_schema_compliance(mutated, index_schema_dict)
    domain_errors = validate_domain_rules(mutated, set(CANONICAL_TRIGGER_IDS))
    assert len(schema_errors) == 0
    assert len(domain_errors) == 0


def test_invalid_score_range(valid_index_dict, index_schema_dict):
    """Verify that scores outside [0.0, 1.0] are rejected."""
    for invalid_score in [-0.01, 1.01]:
        mutated = copy.deepcopy(valid_index_dict)
        mutated["cues"][0]["score"] = invalid_score

        schema_errors = validate_schema_compliance(mutated, index_schema_dict)
        domain_errors = validate_domain_rules(mutated, set(CANONICAL_TRIGGER_IDS))
        assert len(schema_errors) > 0 or len(domain_errors) > 0


def test_negative_timestamps_rejected(valid_index_dict, index_schema_dict):
    """Verify that negative timestamps are rejected."""
    mutated = copy.deepcopy(valid_index_dict)
    mutated["cues"][0]["start"] = -1.0
    mutated["cues"][0]["end"] = 2.0

    schema_errors = validate_schema_compliance(mutated, index_schema_dict)
    domain_errors = validate_domain_rules(mutated, set(CANONICAL_TRIGGER_IDS))
    assert len(schema_errors) > 0 or len(domain_errors) > 0


def test_end_less_than_or_equal_to_start(valid_index_dict):
    """Verify that intervals where end <= start are rejected."""
    canonical = get_canonical_triggers_from_config(CONFIG_PATH)

    # Case 1: end == start
    mutated_equal = copy.deepcopy(valid_index_dict)
    mutated_equal["cues"][0]["start"] = 12.0
    mutated_equal["cues"][0]["end"] = 12.0
    errors_equal = validate_domain_rules(mutated_equal, canonical)
    assert any("strictly greater than 'start'" in err for err in errors_equal)

    # Case 2: end < start
    mutated_reversed = copy.deepcopy(valid_index_dict)
    mutated_reversed["cues"][0]["start"] = 15.0
    mutated_reversed["cues"][0]["end"] = 12.0
    errors_reversed = validate_domain_rules(mutated_reversed, canonical)
    assert any("strictly greater than 'start'" in err for err in errors_reversed)


# ------------------------------------------------------------------------------
# 3. Timeline Bounds and Duration Checks
# ------------------------------------------------------------------------------

def test_cue_ending_exactly_at_declared_duration_passes(valid_index_dict):
    """Verify that a cue whose end timestamp is exactly the timeline duration passes."""
    canonical = get_canonical_triggers_from_config(CONFIG_PATH)
    duration = valid_index_dict["timeline"]["duration_seconds"]

    mutated = copy.deepcopy(valid_index_dict)
    mutated["cues"][-1]["start"] = duration - 1.0
    mutated["cues"][-1]["end"] = duration

    errors = validate_domain_rules(mutated, canonical)
    assert len(errors) == 0


def test_timestamp_beyond_known_duration(valid_index_dict):
    """Verify that cue intervals exceeding timeline.duration_seconds are rejected."""
    canonical = get_canonical_triggers_from_config(CONFIG_PATH)
    duration = valid_index_dict["timeline"]["duration_seconds"]

    mutated = copy.deepcopy(valid_index_dict)
    mutated["cues"][-1]["start"] = duration + 5.0
    mutated["cues"][-1]["end"] = duration + 10.0

    errors = validate_domain_rules(mutated, canonical)
    assert any("exceeds declared film duration" in err for err in errors)


def test_zero_or_negative_timeline_duration_fails(valid_index_dict, index_schema_dict):
    """Verify that timeline.duration_seconds <= 0 is rejected."""
    canonical = get_canonical_triggers_from_config(CONFIG_PATH)
    for bad_dur in [0.0, -10.0]:
        mutated = copy.deepcopy(valid_index_dict)
        mutated["timeline"]["duration_seconds"] = bad_dur

        schema_errors = validate_schema_compliance(mutated, index_schema_dict)
        domain_errors = validate_domain_rules(mutated, canonical)
        assert len(schema_errors) > 0 or len(domain_errors) > 0


def test_invalid_fps_fails(valid_index_dict, index_schema_dict):
    """Verify that invalid timeline.fps values are rejected."""
    canonical = get_canonical_triggers_from_config(CONFIG_PATH)
    for bad_fps in [0.0, -24.0, float("nan"), float("inf")]:
        mutated = copy.deepcopy(valid_index_dict)
        mutated["timeline"]["fps"] = bad_fps

        schema_errors = validate_schema_compliance(mutated, index_schema_dict)
        domain_errors = validate_domain_rules(mutated, canonical)
        assert len(schema_errors) > 0 or len(domain_errors) > 0


def test_duplicate_cue_ids_rejected(valid_index_dict):
    """Verify that duplicate cue IDs are detected and rejected."""
    canonical = get_canonical_triggers_from_config(CONFIG_PATH)
    mutated = copy.deepcopy(valid_index_dict)
    mutated["cues"][1]["id"] = mutated["cues"][0]["id"]

    errors = validate_domain_rules(mutated, canonical)
    assert any("Duplicate cue id" in err for err in errors)


def test_chronological_sorting_violation(valid_index_dict):
    """Verify that cues not ordered chronologically are rejected."""
    canonical = get_canonical_triggers_from_config(CONFIG_PATH)
    mutated = copy.deepcopy(valid_index_dict)
    mutated["cues"][0], mutated["cues"][1] = mutated["cues"][1], mutated["cues"][0]

    errors = validate_domain_rules(mutated, canonical)
    assert any("Chronological sorting violation" in err for err in errors)


def test_non_finite_numbers_rejected(valid_index_dict):
    """Verify that NaN and Infinity are rejected."""
    canonical = get_canonical_triggers_from_config(CONFIG_PATH)

    # NaN in start
    mutated_nan = copy.deepcopy(valid_index_dict)
    mutated_nan["cues"][0]["start"] = float("nan")
    errors_nan = validate_domain_rules(mutated_nan, canonical)
    assert any("NaN" in err for err in errors_nan)

    # Infinity in end
    mutated_inf = copy.deepcopy(valid_index_dict)
    mutated_inf["cues"][0]["end"] = float("inf")
    errors_inf = validate_domain_rules(mutated_inf, canonical)
    assert any("Infinity" in err for err in errors_inf)


def test_invalid_json_syntax(tmp_path):
    """Verify that an index file with malformed JSON syntax is rejected gracefully."""
    bad_json_file = tmp_path / "bad_index.json"
    bad_json_file.write_text("{ unquoted_key: 123, }", encoding="utf-8")

    is_valid, errors = validate_index(bad_json_file, SCHEMA_PATH, CONFIG_PATH)
    assert not is_valid
    assert any("Failed to load index file" in err for err in errors)


# ------------------------------------------------------------------------------
# 4. Configuration-to-Schema Trigger Synchronization Tests (Concern 3)
# ------------------------------------------------------------------------------

def test_schema_trigger_ids_match_config(config_dict, index_schema_dict):
    """Verify zero drift between canonical triggers in config.yaml and schema/index.schema.json."""
    errors = check_trigger_synchronization(config_dict, index_schema_dict)
    assert len(errors) == 0, f"Trigger synchronization failed: {errors}"


def test_trigger_sync_fails_when_schema_missing_trigger(config_dict, index_schema_dict):
    """Verify synchronization failure when a trigger is removed from the schema enum."""
    mutated_schema = copy.deepcopy(index_schema_dict)
    enum_list = mutated_schema["$defs"]["packaged_cue"]["properties"]["trigger"]["enum"]
    enum_list.remove("gunshot")

    errors = check_trigger_synchronization(config_dict, mutated_schema)
    assert len(errors) > 0
    assert any("missing from schema enum" in err and "gunshot" in err for err in errors)


def test_trigger_sync_fails_when_schema_has_unknown_trigger(config_dict, index_schema_dict):
    """Verify synchronization failure when an unexpected trigger is added to schema enum."""
    mutated_schema = copy.deepcopy(index_schema_dict)
    enum_list = mutated_schema["$defs"]["packaged_cue"]["properties"]["trigger"]["enum"]
    enum_list.append("alien_encounter")

    errors = check_trigger_synchronization(config_dict, mutated_schema)
    assert len(errors) > 0
    assert any("Unexpected trigger(s) in schema enum" in err and "alien_encounter" in err for err in errors)


def test_trigger_sync_fails_when_schema_has_misspelled_trigger(config_dict, index_schema_dict):
    """Verify synchronization failure when a trigger is misspelled in the schema enum."""
    mutated_schema = copy.deepcopy(index_schema_dict)
    enum_list = mutated_schema["$defs"]["packaged_cue"]["properties"]["trigger"]["enum"]
    idx = enum_list.index("loud_spike")
    enum_list[idx] = "loudspike"  # Missing underscore

    errors = check_trigger_synchronization(config_dict, mutated_schema)
    assert len(errors) >= 2  # Missing 'loud_spike' AND unexpected 'loudspike'
    assert any("loud_spike" in err for err in errors)
    assert any("loudspike" in err for err in errors)


def test_trigger_sync_fails_when_schema_has_duplicate_trigger(config_dict, index_schema_dict):
    """Verify synchronization failure when a duplicate trigger exists in schema enum."""
    mutated_schema = copy.deepcopy(index_schema_dict)
    enum_list = mutated_schema["$defs"]["packaged_cue"]["properties"]["trigger"]["enum"]
    enum_list.append("flashing")  # Duplicate

    errors = check_trigger_synchronization(config_dict, mutated_schema)
    assert any("Duplicate trigger ID" in err and "flashing" in err for err in errors)


def test_trigger_sync_fails_when_config_triggers_change(config_dict, index_schema_dict):
    """Verify synchronization failure when config.yaml trigger set is mutated."""
    mutated_config = copy.deepcopy(config_dict)
    del mutated_config["triggers"]["spider"]

    errors = check_trigger_synchronization(mutated_config, index_schema_dict)
    assert any("Unexpected trigger(s) in schema enum" in err and "spider" in err for err in errors)


# ------------------------------------------------------------------------------
# 5. Media Timeline Verification Tests (Concern 4)
# ------------------------------------------------------------------------------

def test_media_verification_matching_duration_passes(tmp_path):
    """Verify media verification passes when declared duration matches probed duration."""
    dummy_media = tmp_path / "test_video.mp4"
    dummy_media.write_bytes(b"dummy video data")

    with patch("scripts.validate_index.shutil.which", return_value="C:\\fake\\ffprobe.exe"), \
         patch("scripts.validate_index.probe_media_duration", return_value=52.0):
        is_match, err = verify_media_duration(52.0, dummy_media, tolerance=0.1)
        assert is_match is True
        assert err is None


def test_media_verification_within_tolerance_passes(tmp_path):
    """Verify media verification passes when difference is within tolerance (0.1s)."""
    dummy_media = tmp_path / "test_video.mp4"
    dummy_media.write_bytes(b"dummy video data")

    with patch("scripts.validate_index.probe_media_duration", return_value=52.05):
        is_match, err = verify_media_duration(52.0, dummy_media, tolerance=0.1)
        assert is_match is True
        assert err is None


def test_media_verification_exceeding_tolerance_fails(tmp_path):
    """Verify media verification fails when duration difference exceeds tolerance."""
    dummy_media = tmp_path / "test_video.mp4"
    dummy_media.write_bytes(b"dummy video data")

    with patch("scripts.validate_index.probe_media_duration", return_value=55.5):
        is_match, err = verify_media_duration(52.0, dummy_media, tolerance=0.1)
        assert is_match is False
        assert "exceeds tolerance 0.100s" in err


def test_media_verification_missing_media_file_fails():
    """Verify media verification fails clearly when media file does not exist."""
    missing_file = Path("non_existent_movie.mp4")
    is_match, err = verify_media_duration(52.0, missing_file, tolerance=0.1)
    assert is_match is False
    assert "Media file not found" in err


def test_media_verification_missing_ffprobe_fails(tmp_path):
    """Verify media verification fails clearly when ffprobe is not installed."""
    dummy_media = tmp_path / "test_video.mp4"
    dummy_media.write_bytes(b"dummy video data")

    with patch("scripts.validate_index.shutil.which", return_value=None):
        is_match, err = verify_media_duration(52.0, dummy_media, tolerance=0.1)
        assert is_match is False
        assert "ffprobe executable not found in PATH" in err


# ------------------------------------------------------------------------------
# 6. CLI Execution Tests
# ------------------------------------------------------------------------------

def test_cli_execution_success():
    """Verify scripts/validate_index.py CLI returns exit code 0 on valid index."""
    res = subprocess.run(
        [sys.executable, "scripts/validate_index.py"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"CLI validation failed: {res.stderr}\n{res.stdout}"
    assert "SUCCESS" in res.stdout


def test_cli_execution_failure(tmp_path):
    """Verify scripts/validate_index.py CLI returns non-zero exit code on invalid index."""
    bad_file = tmp_path / "invalid_index.json"
    bad_file.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "scripts/validate_index.py", "--index", str(bad_file)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "FAILURE" in res.stdout


def test_timeline_requires_timestamp_unit_and_interval_convention(valid_index_dict, index_schema_dict):
    """Verify that schema/index.schema.json requires timestamp_unit and interval_convention."""
    # Test missing timestamp_unit
    index_missing_unit = copy.deepcopy(valid_index_dict)
    del index_missing_unit["timeline"]["timestamp_unit"]
    errors = validate_schema_compliance(index_missing_unit, index_schema_dict)
    assert any("timestamp_unit" in err for err in errors), "Schema must require timestamp_unit in timeline"

    # Test missing interval_convention
    index_missing_conv = copy.deepcopy(valid_index_dict)
    del index_missing_conv["timeline"]["interval_convention"]
    errors = validate_schema_compliance(index_missing_conv, index_schema_dict)
    assert any("interval_convention" in err for err in errors), "Schema must require interval_convention in timeline"


def test_packaged_cue_status_validation(valid_index_dict, index_schema_dict):
    """Verify that packaged cues accept optional status ('ready', 'needs_review') and reject invalid values."""
    # Valid status: ready
    index_ready = copy.deepcopy(valid_index_dict)
    index_ready["cues"][0]["status"] = "ready"
    errors = validate_schema_compliance(index_ready, index_schema_dict)
    assert len(errors) == 0, f"Expected status 'ready' to be valid, got errors: {errors}"

    # Valid status: needs_review
    index_review = copy.deepcopy(valid_index_dict)
    index_review["cues"][0]["status"] = "needs_review"
    errors = validate_schema_compliance(index_review, index_schema_dict)
    assert len(errors) == 0, f"Expected status 'needs_review' to be valid, got errors: {errors}"

    # Invalid status
    index_invalid = copy.deepcopy(valid_index_dict)
    index_invalid["cues"][0]["status"] = "pending_approval"
    errors = validate_schema_compliance(index_invalid, index_schema_dict)
    assert any("pending_approval" in err or "status" in err for err in errors), "Schema must reject invalid status"
