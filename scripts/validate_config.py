#!/usr/bin/env python3
"""Validation script for Trigger-Aware Player pipeline configuration.

Validates config.yaml against schema/config.schema.json using JSON Schema Draft 2020-12
and executes domain-level semantic consistency checks.

Usage:
    python scripts/validate_config.py
    python scripts/validate_config.py --config config.yaml --schema schema/config.schema.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import jsonschema
import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_SCHEMA_PATH = Path("schema/config.schema.json")

CANONICAL_TRIGGER_IDS = [
    "flashing",
    "profanity",
    "gunshot",
    "explosion",
    "scream",
    "loudness",
    "loud_spike",
    "sustained_loudness",
    "insect",
    "jump_scare",
    "visible_blood",
]

ACTIVE_CANONICAL_TRIGGER_IDS = [
    "flashing",
    "profanity",
    "gunshot",
    "explosion",
    "scream",
    "loudness",
    "insect",
    "jump_scare",
    "visible_blood",
]

LEGACY_TRIGGER_IDS = ["loud_spike", "sustained_loudness"]

ALLOWED_ACTIONS = ["hide", "blur", "mute", "reduce", "skip", "warn"]

DEFAULT_HEADS_UP_PROMPT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "lead_time_seconds": 5.0,
}


def get_heads_up_prompt_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and resolve heads_up_prompts configuration with backward-compatible defaults.

    Returns:
        Dict with 'enabled' (bool) and 'lead_time_seconds' (float).
    """
    prompts = config.get("heads_up_prompts")
    if not isinstance(prompts, dict):
        return dict(DEFAULT_HEADS_UP_PROMPT_CONFIG)

    enabled = prompts.get("enabled", DEFAULT_HEADS_UP_PROMPT_CONFIG["enabled"])
    if not isinstance(enabled, bool):
        enabled = DEFAULT_HEADS_UP_PROMPT_CONFIG["enabled"]

    lead_time = prompts.get("lead_time_seconds", DEFAULT_HEADS_UP_PROMPT_CONFIG["lead_time_seconds"])
    if isinstance(lead_time, bool) or not isinstance(lead_time, (int, float)):
        lead_time = DEFAULT_HEADS_UP_PROMPT_CONFIG["lead_time_seconds"]
    else:
        lead_time = float(lead_time)
        if not math.isfinite(lead_time) or lead_time <= 0.0:
            lead_time = DEFAULT_HEADS_UP_PROMPT_CONFIG["lead_time_seconds"]

    return {
        "enabled": enabled,
        "lead_time_seconds": lead_time,
    }


def load_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load and parse a YAML configuration file."""
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path.resolve()}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Configuration file at {path} did not parse into a dictionary.")
    return data


def load_schema(schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> Dict[str, Any]:
    """Load and parse a JSON Schema file."""
    path = Path(schema_path)
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {path.resolve()}")
    with path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    if not isinstance(schema, dict):
        raise ValueError(f"Schema file at {path} did not parse into a dictionary.")
    return schema


def validate_schema_compliance(
    config: Dict[str, Any], schema: Dict[str, Any]
) -> List[str]:
    """Validate config dict against the JSON Schema Draft 2020-12."""
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    errors: List[str] = []
    for err in validator.iter_errors(config):
        path_str = " -> ".join(str(p) for p in err.path) if err.path else "root"
        errors.append(f"Schema error at [{path_str}]: {err.message}")
    return errors


def validate_domain_rules(config: Dict[str, Any]) -> List[str]:
    """Perform semantic and domain-specific validation checks beyond JSON schema."""
    errors: List[str] = []

    # 1. Timeline contract
    timeline = config.get("timeline", {})
    if timeline.get("timestamp_unit") != "seconds":
        errors.append("timeline.timestamp_unit must be 'seconds'.")
    if timeline.get("interval_convention") != "half_open":
        errors.append("timeline.interval_convention must be 'half_open'.")
    if timeline.get("allow_fractional_seconds") is not True:
        errors.append("timeline.allow_fractional_seconds must be true.")

    # 2. Shared detection record contract
    detection = config.get("detection", {})
    common_fields = detection.get("common_record_fields", [])
    expected_fields = ["start", "end", "trigger", "score", "source"]
    for field in expected_fields:
        if field not in common_fields:
            errors.append(
                f"detection.common_record_fields missing mandatory field '{field}'."
            )

    # 3. Canonical triggers
    triggers = config.get("triggers", {})
    for tid in CANONICAL_TRIGGER_IDS:
        if tid not in triggers:
            errors.append(f"Missing required canonical trigger definition: '{tid}'.")
            continue

        t_conf = triggers[tid]
        min_dur = t_conf.get("min_segment_duration_seconds", 0)
        max_dur = t_conf.get("max_segment_duration_seconds", 0)
        if min_dur >= max_dur:
            errors.append(
                f"Trigger '{tid}' min_segment_duration_seconds ({min_dur}) must be strictly less than "
                f"max_segment_duration_seconds ({max_dur})."
            )

        def_action = t_conf.get("default_action")
        allowed = t_conf.get("allowed_actions", [])
        if def_action not in allowed:
            errors.append(
                f"Trigger '{tid}' default_action '{def_action}' must be present in allowed_actions {allowed}."
            )

    # 4. Cross-trigger merge policy
    merge = config.get("merge", {})
    cross_policy = merge.get("cross_trigger_policy", {})
    if cross_policy.get("allow_cross_trigger_merging") is not False:
        errors.append("merge.cross_trigger_policy.allow_cross_trigger_merging must be false.")
    if cross_policy.get("keep_separate") is not True:
        errors.append("merge.cross_trigger_policy.keep_separate must be true.")

    # 5. Experimental features policy
    exp = config.get("experimental_features", {})
    stretch = exp.get("stretch_features", {})
    for s_name in ["jump_scare"]:
        if s_name in stretch:
            if stretch[s_name].get("enabled") is True:
                errors.append(
                    f"Experimental stretch feature '{s_name}' should not be enabled by default for baseline demo."
                )

    # 6. Heads-up advance warning prompt contract
    if "heads_up_prompts" in config:
        prompts = config["heads_up_prompts"]
        if not isinstance(prompts, dict):
            errors.append("heads_up_prompts must be a mapping/dictionary.")
        else:
            if "enabled" in prompts and not isinstance(prompts["enabled"], bool):
                errors.append(f"heads_up_prompts.enabled must be a boolean, got {type(prompts['enabled']).__name__}.")
            if "lead_time_seconds" in prompts:
                lt = prompts["lead_time_seconds"]
                if isinstance(lt, bool) or not isinstance(lt, (int, float)):
                    errors.append(f"heads_up_prompts.lead_time_seconds must be a numeric value, got {type(lt).__name__}.")
                elif not math.isfinite(float(lt)):
                    errors.append("heads_up_prompts.lead_time_seconds must be a finite number.")
                elif float(lt) <= 0.0:
                    errors.append(f"heads_up_prompts.lead_time_seconds must be strictly greater than 0.0, got {lt}.")

    return errors


def validate_config(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
) -> Tuple[bool, List[str]]:
    """Validate configuration file against schema and domain rules.

    Returns:
        (is_valid, list_of_error_strings)
    """
    try:
        config = load_config(config_path)
    except Exception as exc:
        return False, [f"Failed to load config: {exc}"]

    try:
        schema = load_schema(schema_path)
    except Exception as exc:
        return False, [f"Failed to load schema: {exc}"]

    all_errors = []
    all_errors.extend(validate_schema_compliance(config, schema))
    all_errors.extend(validate_domain_rules(config))

    return len(all_errors) == 0, all_errors


def main() -> int:
    """CLI entrypoint for config validation."""
    parser = argparse.ArgumentParser(
        description="Validate Trigger-Aware Player pipeline configuration."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help="Path to schema/config.schema.json (default: schema/config.schema.json)",
    )
    args = parser.parse_args()

    print(f"Validating configuration: {args.config.resolve()}")
    print(f"Using JSON schema:        {args.schema.resolve()}")

    is_valid, errors = validate_config(args.config, args.schema)

    if is_valid:
        print("\nSUCCESS: config.yaml passed all schema and domain validation checks.")
        return 0
    else:
        print(f"\nFAILURE: Found {len(errors)} validation error(s):")
        for idx, err in enumerate(errors, 1):
            print(f"  {idx}. {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
