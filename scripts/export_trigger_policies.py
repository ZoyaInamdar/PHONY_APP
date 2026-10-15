#!/usr/bin/env python3
"""Export trigger policies from config.yaml to a JSON fixture for TypeScript integration tests.

This script reads the triggers section of config.yaml and writes a JSON file
containing only the fields used by the TypeScript adapter
(packagedIndexAdapter.ts): enabled, modality, default_action, allowed_actions.

Usage:
    python scripts/export_trigger_policies.py
    python scripts/export_trigger_policies.py --config config.yaml --output test/fixtures/triggerPolicies.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # type: ignore[import]

DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "test" / "fixtures" / "triggerPolicies.json"


def extract_trigger_policies(config_path: Path) -> Dict[str, Any]:
    """Load config.yaml and return a dict of trigger policies keyed by trigger ID."""
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    triggers: Dict[str, Any] = config.get("triggers", {})
    if not isinstance(triggers, dict):
        raise ValueError("config.yaml 'triggers' section must be a mapping.")

    policies: Dict[str, Any] = {}
    for trigger_id, trigger_data in triggers.items():
        if not isinstance(trigger_data, dict):
            raise ValueError(f"Trigger '{trigger_id}' must be a mapping in config.yaml.")

        policies[trigger_id] = {
            "enabled": bool(trigger_data.get("enabled", False)),
            "modality": str(trigger_data.get("modality", "")),
            "default_action": str(trigger_data.get("default_action", "")),
            "allowed_actions": list(trigger_data.get("allowed_actions", [])),
        }

    return policies


def write_fixture(policies: Dict[str, Any], output_path: Path) -> None:
    """Write policies to a JSON file atomically."""
    output = {
        "_note": (
            "Auto-generated from config.yaml by scripts/export_trigger_policies.py. "
            "Do not edit manually. Re-run this script when config.yaml changes."
        ),
        "_source": "config.yaml triggers section",
        "policies": policies,
    }

    json_text = json.dumps(output, indent=2) + "\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(output_path.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json_text)
        os.replace(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export trigger policies from config.yaml to a JSON fixture."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to config.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT_PATH})",
    )
    args = parser.parse_args()

    try:
        policies = extract_trigger_policies(args.config)
        write_fixture(policies, args.output)
        print(f"SUCCESS: Wrote {len(policies)} trigger policies to {args.output}")
        return 0
    except Exception as exc:
        print(f"FAILURE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
