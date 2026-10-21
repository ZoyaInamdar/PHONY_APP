"""Unit tests for Trigger-Aware Player shared pipeline configuration and schema validation."""

import subprocess
import sys
from pathlib import Path
import pytest
import yaml

from scripts.validate_config import (
    CANONICAL_TRIGGER_IDS,
    ALLOWED_ACTIONS,
    load_config,
    load_schema,
    validate_config,
    validate_domain_rules,
    validate_schema_compliance,
)

CONFIG_PATH = Path("config.yaml")
SCHEMA_PATH = Path("schema/config.schema.json")

EXPECTED_CONTRACT = {
    "flashing": {"default": "hide", "allowed": ["hide", "skip", "warn"]},
    "profanity": {"default": "mute", "allowed": ["mute", "skip", "warn"]},
    "gunshot": {"default": "mute", "allowed": ["mute", "reduce", "skip", "warn"]},
    "explosion": {"default": "reduce", "allowed": ["reduce", "mute", "skip", "warn"]},
    "scream": {"default": "reduce", "allowed": ["reduce", "mute", "skip", "warn"]},
    "loud_spike": {"default": "reduce", "allowed": ["reduce", "mute", "warn"]},
    "sustained_loudness": {"default": "reduce", "allowed": ["reduce", "mute", "warn", "skip"]},
    "spider": {"default": "hide", "allowed": ["hide", "blur", "skip", "warn"]},
    "insect": {"default": "hide", "allowed": ["hide", "blur", "skip", "warn"]},
    "fast_motion": {"default": "warn", "allowed": ["warn", "hide", "skip"]},
    "jump_scare": {"default": "warn", "allowed": ["warn", "hide", "mute", "skip"]},
    "visible_blood": {"default": "hide", "allowed": ["hide", "blur", "skip", "warn"]},
}


def test_config_file_exists():
    """Verify that config.yaml exists in the repository root."""
    assert CONFIG_PATH.is_file(), f"Expected {CONFIG_PATH} to exist."


def test_schema_file_exists():
    """Verify that schema/config.schema.json exists."""
    assert SCHEMA_PATH.is_file(), f"Expected {SCHEMA_PATH} to exist."


def test_config_validates_successfully():
    """Verify that config.yaml passes both schema and domain validation without errors."""
    is_valid, errors = validate_config(CONFIG_PATH, SCHEMA_PATH)
    assert is_valid, f"Validation failed with errors: {errors}"
    assert len(errors) == 0


def test_all_canonical_triggers_present():
    """Verify all 12 canonical trigger IDs are present and conform to required structure."""
    config = load_config(CONFIG_PATH)
    triggers = config.get("triggers", {})

    assert set(CANONICAL_TRIGGER_IDS) == set(EXPECTED_CONTRACT.keys())

    for trigger_id in CANONICAL_TRIGGER_IDS:
        assert trigger_id in triggers, f"Canonical trigger '{trigger_id}' missing from config.yaml"
        t_conf = triggers[trigger_id]

        # Check required fields
        assert "enabled" in t_conf
        assert "status" in t_conf
        assert t_conf["status"] in ["baseline", "experimental", "disabled"]
        assert "modality" in t_conf
        assert t_conf["modality"] in ["visual", "audio", "text", "audio_visual"]
        assert "method" in t_conf
        assert "ml_model_required" in t_conf
        assert "input" in t_conf
        assert "timing_resolution_seconds" in t_conf
        assert t_conf["timing_resolution_seconds"] > 0
        assert "source" in t_conf
        assert "threshold" in t_conf
        assert "default_action" in t_conf
        assert t_conf["default_action"] in ALLOWED_ACTIONS
        assert "allowed_actions" in t_conf
        assert t_conf["default_action"] in t_conf["allowed_actions"]
        assert "padding_before_seconds" in t_conf
        assert t_conf["padding_before_seconds"] >= 0
        assert "padding_after_seconds" in t_conf
        assert t_conf["padding_after_seconds"] >= 0
        assert "merge_gap_seconds" in t_conf
        assert t_conf["merge_gap_seconds"] >= 0
        assert "min_segment_duration_seconds" in t_conf
        assert "max_segment_duration_seconds" in t_conf
        assert t_conf["min_segment_duration_seconds"] < t_conf["max_segment_duration_seconds"]

        # Check architectural contract
        expected = EXPECTED_CONTRACT[trigger_id]
        assert t_conf["default_action"] == expected["default"], (
            f"Trigger '{trigger_id}' default_action '{t_conf['default_action']}' does not match expected '{expected['default']}'"
        )
        assert set(t_conf["allowed_actions"]) == set(expected["allowed"]), (
            f"Trigger '{trigger_id}' allowed_actions {t_conf['allowed_actions']} does not match expected {expected['allowed']}"
        )


def test_timeline_contract():
    """Verify timeline contract specifications: seconds unit, half_open intervals, fractional seconds."""
    config = load_config(CONFIG_PATH)
    timeline = config.get("timeline", {})
    assert timeline.get("timestamp_unit") == "seconds"
    assert timeline.get("interval_convention") == "half_open"
    assert timeline.get("allow_fractional_seconds") is True


def test_detection_record_contract_fields():
    """Verify shared detection record contract has start, end, trigger, score, source."""
    config = load_config(CONFIG_PATH)
    detection = config.get("detection", {})
    fields = detection.get("common_record_fields", [])
    expected = ["start", "end", "trigger", "score", "source"]
    assert fields == expected, f"Expected {expected}, got {fields}"


def test_flashing_algorithm_configuration():
    """Verify flashing trigger algorithm configuration and disclaimer."""
    config = load_config(CONFIG_PATH)
    flash = config["triggers"]["flashing"]
    algo = flash.get("algorithm", {})

    assert algo.get("frame_dimensions", {}).get("width") == 128
    assert algo.get("frame_dimensions", {}).get("height") == 72
    assert algo.get("linearize_srgb") is True
    assert algo.get("luminance_coefficients", {}).get("r") == pytest.approx(0.2126)
    assert algo.get("luminance_coefficients", {}).get("g") == pytest.approx(0.7152)
    assert algo.get("luminance_coefficients", {}).get("b") == pytest.approx(0.0722)
    assert algo.get("spatial_grid", {}).get("columns") == 16
    assert algo.get("spatial_grid", {}).get("rows") == 9
    assert algo.get("cell_luminance_change_threshold") == pytest.approx(0.10)
    assert algo.get("darker_frame_luminance_threshold") == pytest.approx(0.80)
    assert algo.get("flash_count_window_seconds") == pytest.approx(1.0)
    assert algo.get("min_opposite_transitions_threshold") > 3
    assert algo.get("simultaneous_flashing_area_threshold") <= 0.25
    assert "compliance_disclaimer" in algo


def test_loud_spike_algorithm_configuration():
    """Verify loud_spike trigger algorithm parameters."""
    config = load_config(CONFIG_PATH)
    spike = config["triggers"]["loud_spike"]
    algo = spike.get("algorithm", {})

    assert algo.get("rms_window_duration_seconds") == pytest.approx(0.1)
    assert algo.get("rolling_median_duration_seconds") == pytest.approx(10.0)
    assert algo.get("relative_spike_threshold_db") == pytest.approx(12.0)
    assert algo.get("numerical_floor_db") <= -80.0
    assert "leading_window_policy" in algo
    assert algo.get("user_facing_semantics") == "sudden loudness increase"


def test_audio_classifier_triggers_unresolved_backends():
    """Verify audio classifier triggers (gunshot, explosion, scream) do not falsely claim confirmed backend."""
    config = load_config(CONFIG_PATH)
    for tid in ["gunshot", "explosion", "scream"]:
        t = config["triggers"][tid]
        settings = t.get("classifier_settings", {})
        assert settings.get("selected_backend") is None, f"{tid} must not have falsely confirmed backend"
        assert settings.get("backend_selection_status") == "unresolved"
        assert set(settings.get("candidate_backends", [])) == {"AST", "YAMNet", "PANNs"}


def test_profanity_configuration():
    """Verify profanity trigger settings including 1.0s padding and subtitle priority."""
    config = load_config(CONFIG_PATH)
    prof = config["triggers"]["profanity"]
    assert prof.get("padding_before_seconds") == pytest.approx(1.0)
    assert prof.get("padding_after_seconds") == pytest.approx(1.0)
    matching = prof.get("matching_settings", {})
    assert matching.get("prefer_subtitles") is True
    assert matching.get("case_sensitive") is False
    assert matching.get("match_whole_words") is True


def test_spider_configuration():
    """Verify spider trigger configuration including tiling and prompt setup."""
    config = load_config(CONFIG_PATH)
    spider = config["triggers"]["spider"]
    algo = spider.get("algorithm", {})
    assert algo.get("frame_sampling_fps") == pytest.approx(1.0)
    assert algo.get("spatial_tiling", {}).get("enabled") is True
    assert algo.get("selected_backend") in (None, "YOLO-World")
    assert {"CLIP", "SigLIP"}.issubset(set(algo.get("candidate_backends", [])))
    assert len(algo.get("positive_prompts", [])) >= 1
    assert len(algo.get("negative_prompts", [])) >= 3


def test_experimental_stretch_features_disabled():
    """Verify experimental stretch features (fast_motion, jump_scare, sustained_loudness, insect, visible_blood) are disabled by default."""
    config = load_config(CONFIG_PATH)
    for tid in ["fast_motion", "jump_scare", "sustained_loudness", "insect", "visible_blood"]:
        t = config["triggers"][tid]
        assert t.get("status") == "experimental"
        assert t.get("enabled") is False


def test_merge_cross_trigger_policy():
    """Verify merge settings forbid combining different triggers into one record."""
    config = load_config(CONFIG_PATH)
    merge = config.get("merge", {})
    assert merge.get("default_merge_gap_seconds") == pytest.approx(2.5)
    assert merge.get("max_segment_duration_seconds") == pytest.approx(60.0)
    cross_policy = merge.get("cross_trigger_policy", {})
    assert cross_policy.get("keep_separate") is True
    assert cross_policy.get("allow_cross_trigger_merging") is False


def test_schema_rejects_invalid_trigger_action():
    """Verify that the JSON schema rejects an unsupported trigger action."""
    config = load_config(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)

    # Mutate with invalid action
    config["triggers"]["flashing"]["default_action"] = "explode_tv"
    errors = validate_schema_compliance(config, schema)
    assert len(errors) > 0, "Schema should have rejected invalid action 'explode_tv'"


def test_schema_rejects_missing_required_section():
    """Verify that the JSON schema rejects a config missing a required top-level section."""
    config = load_config(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)

    del config["merge"]
    errors = validate_schema_compliance(config, schema)
    assert any("merge" in err for err in errors), "Schema should have flagged missing 'merge' section"


def test_validate_config_cli_execution():
    """Verify that running scripts/validate_config.py via subprocess returns exit code 0."""
    result = subprocess.run(
        [sys.executable, "scripts/validate_config.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"CLI validation failed with stderr: {result.stderr}\nstdout: {result.stdout}"
    assert "SUCCESS" in result.stdout


def test_schema_accepts_hide_and_reduce_actions():
    """Verify that config schema accepts 'hide' and 'reduce' in action enums."""
    config = load_config(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)

    config["triggers"]["flashing"]["default_action"] = "hide"
    config["triggers"]["flashing"]["allowed_actions"] = ["hide", "warn"]
    config["triggers"]["loud_spike"]["default_action"] = "reduce"
    config["triggers"]["loud_spike"]["allowed_actions"] = ["reduce", "mute", "warn"]

    errors = validate_schema_compliance(config, schema)
    assert len(errors) == 0, f"Schema should have accepted 'hide' and 'reduce', got errors: {errors}"
    domain_errors = validate_domain_rules(config)
    assert len(domain_errors) == 0, f"Domain rules should have accepted 'hide' and 'reduce', got errors: {domain_errors}"


def test_schema_rejects_dim_and_beep_actions():
    """Verify that config schema rejects deprecated 'dim' and 'beep' actions."""
    config = load_config(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)

    config["triggers"]["flashing"]["default_action"] = "dim"
    errors = validate_schema_compliance(config, schema)
    assert len(errors) > 0, "Schema should have rejected deprecated 'dim' action"

    config["triggers"]["flashing"]["default_action"] = "beep"
    errors = validate_schema_compliance(config, schema)
    assert len(errors) > 0, "Schema should have rejected non-product 'beep' action"


def test_schema_rejects_obscure_action():
    """Verify that config schema rejects non-canonical action 'obscure'."""
    config = load_config(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)

    config["triggers"]["flashing"]["default_action"] = "obscure"
    errors = validate_schema_compliance(config, schema)
    assert len(errors) > 0, "Schema should have rejected 'obscure' action"
