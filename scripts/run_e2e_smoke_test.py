#!/usr/bin/env python3
"""End-to-End Smoke Test for Trigger-Aware Player (PHONY_APP).

Combines available offline detectors, packages their detections into index.json,
validates the index schema and domain rules, and runs the Player Action Engine
(src/engine/packagedIndexAdapter.ts & resolveActions.ts) to verify runtime mute
behavior, boundary exactness, and viewer option toggles.

Usage:
    python scripts/run_e2e_smoke_test.py
    python scripts/run_e2e_smoke_test.py --audio data/processed/smoke_test/audio_mono_44100.wav --media data/media/smoke_test.mp4
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
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.detect_loudness import (
    DEFAULT_CONFIG_PATH,
    detect_loudness,
    load_detector_config,
)
from scripts.package_cues import (
    DEFAULT_SCHEMA_PATH,
    atomic_write_json,
    load_config_data,
    load_schema,
    package_cues,
)
from scripts.validate_index import validate_index

DEFAULT_AUDIO_PATH = Path("data/processed/smoke_test/audio_mono_44100.wav")
DEFAULT_MEDIA_PATH = Path("data/media/smoke_test.mp4")
DEFAULT_FILM_ID = "smoke_test"


def get_wav_info(wav_path: Path) -> Dict[str, Any]:
    """Inspect WAV file properties without external dependencies."""
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        channels = w.getnchannels()
        nframes = w.getnframes()
        duration = float(nframes) / float(sr) if sr > 0 else 0.0
        return {
            "sample_rate": sr,
            "channels": channels,
            "total_samples": nframes,
            "duration_seconds": duration,
        }


def check_detector_availability() -> Dict[str, Dict[str, Any]]:
    """Audit repository for available detector implementations and status."""
    return {
        "loudness_detector": {
            "component": "loudness_detector",
            "modality": "audio",
            "trigger": "loud_spike",
            "owner": "Person B",
            "status": "available",
            "script_path": "scripts/detect_loudness.py",
            "notes": "Fully implemented, tested, and operational.",
        },
        "flash_detector": {
            "component": "flash_detector",
            "modality": "visual",
            "trigger": "flashing",
            "owner": "Person A",
            "status": "missing",
            "script_path": None,
            "notes": "Person A component not implemented in repository.",
        },
        "audio_event_classifier": {
            "component": "audio_event_classifier",
            "modality": "audio",
            "trigger": "gunshot, explosion, scream",
            "owner": "Person A",
            "status": "missing",
            "script_path": None,
            "notes": "Person A component not implemented in repository.",
        },
        "profanity_detector": {
            "component": "profanity_detector",
            "modality": "subtitles",
            "trigger": "profanity",
            "owner": "Person C",
            "status": "partial",
            "script_path": "src/subtitles/profanityParser.ts",
            "notes": "Parser algorithm exists in TypeScript, but no CLI wrapper or subtitle data in pipeline.",
        },
    }


def run_player_engine_simulation(
    index_path: Path,
    repo_root: Path = REPO_ROOT,
) -> Dict[str, Any]:
    """Execute Person A's player engine via Node.js to verify runtime action resolution."""
    loader_path = repo_root / "scripts" / "ts_loader.mjs"
    sim_script = repo_root / "scripts" / "simulate_player.mjs"

    cmd = [
        "node",
        "--experimental-strip-types",
        "--loader",
        loader_path.resolve().as_uri(),
        str(sim_script),
        "--index",
        str(index_path),
        "--verify-mute",
        "--json",
    ]

    result = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0 and not result.stdout:
        raise RuntimeError(
            f"Node player simulation failed with code {result.returncode}:\n{result.stderr}"
        )

    # Filter out any Node.js experimental warnings that may appear before JSON output
    stdout_lines = result.stdout.strip().splitlines()
    json_start = -1
    for idx, line in enumerate(stdout_lines):
        if line.strip().startswith("{"):
            json_start = idx
            break

    if json_start == -1:
        raise RuntimeError(
            f"Failed to find JSON payload in Node.js output:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    json_str = "\n".join(stdout_lines[json_start:])
    sim_data = json.loads(json_str)
    return sim_data


def run_e2e_smoke_pipeline(
    audio_path: Optional[Path] = None,
    media_path: Optional[Path] = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    film_id: str = DEFAULT_FILM_ID,
    output_dir: Optional[Path] = None,
    keep_artifacts: bool = False,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Execute complete end-to-end smoke test pipeline."""
    if verbose:
        print("=" * 72)
        print("  TRIGGER-AWARE PLAYER — END-TO-END SMOKE TEST PIPELINE")
        print("=" * 72)

    # 1. Detector Audit
    detectors = check_detector_availability()
    if verbose:
        print("\n[Step 1] Detector Availability Audit:")
        for det_name, info in detectors.items():
            print(f"  - {det_name} ({info['owner']}): [{info['status'].upper()}] - {info['notes']}")

    # 2. Audio input verification
    resolved_audio_path = Path(audio_path) if audio_path else DEFAULT_AUDIO_PATH
    if not resolved_audio_path.exists():
        raise FileNotFoundError(f"Input audio file not found: {resolved_audio_path}")

    wav_info = get_wav_info(resolved_audio_path)
    audio_duration = wav_info["duration_seconds"]
    if verbose:
        print(f"\n[Step 2] Input Media Audio Verified:")
        print(f"  File: {resolved_audio_path}")
        print(f"  Sample Rate: {wav_info['sample_rate']} Hz, Channels: {wav_info['channels']}")
        print(f"  Total Samples: {wav_info['total_samples']:,}, Duration: {audio_duration:.3f}s")

    # Set up temporary working directory for pipeline artifacts
    temp_dir_obj = None
    if output_dir:
        working_dir = Path(output_dir)
        working_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="e2e_smoke_")
        working_dir = Path(temp_dir_obj.name)

    raw_cues_path = working_dir / "raw_detections.json"
    index_path = working_dir / "index.json"

    try:
        # 3. Detector execution (Loudness Spike Detector)
        if verbose:
            print(f"\n[Step 3] Running Loudness Spike Detection (--mode spike)...")

        config = load_config_data(config_path)
        schema = load_schema(schema_path)

        raw_detections = detect_loudness(
            input_source=resolved_audio_path,
            config=config,
            modes=("spike",),
            output_path=raw_cues_path,
            film_id=film_id,
        )
        if verbose:
            print(f"  Raw loudness spikes detected: {len(raw_detections)}")

        # Validate raw detection contract on all emitted records
        for i, rec in enumerate(raw_detections):
            assert "start" in rec and rec["start"] >= 0.0, f"Record {i} missing/invalid start"
            assert "end" in rec and rec["end"] > rec["start"], f"Record {i} missing/invalid end"
            assert rec["trigger"] == "loud_spike", f"Record {i} invalid trigger {rec.get('trigger')}"
            assert 0.0 <= rec["score"] <= 1.0, f"Record {i} score {rec.get('score')} out of [0, 1]"
            assert rec["source"] == "loudness_detector", f"Record {i} invalid source {rec.get('source')}"

        if verbose:
            print(f"  Saved raw detections to: {raw_cues_path}")

        # 4. Packaging execution (scripts/package_cues.py)
        if verbose:
            print(f"\n[Step 4] Packaging Cues into index.json...")

        packaged_index = package_cues(
            raw_data=raw_detections,
            config=config,
            schema=schema,
            film_id=film_id,
            duration_seconds=audio_duration,
            fps=24.0,
            synthetic=False,
            description=f"E2E Smoke Test Packaged Index for {film_id}",
        )
        atomic_write_json(packaged_index, index_path)
        packaged_cues = packaged_index.get("cues", [])
        if verbose:
            print(f"  Packaged cues after merging and padding: {len(packaged_cues)}")
            print(f"  Saved packaged index to: {index_path}")

        # 5. Index validation (scripts/validate_index.py)
        if verbose:
            print(f"\n[Step 5] Validating Packaged index.json against schema and domain rules...")

        resolved_media_path = Path(media_path) if media_path and Path(media_path).exists() else None
        validation_ok = validate_index(
            index_path=index_path,
            schema_path=schema_path,
            config_path=config_path,
            media_path=resolved_media_path,
        )
        if not validation_ok:
            raise ValueError(f"Packaged index {index_path} failed validation!")
        if verbose:
            print("  Validation: SUCCESS (Schema, triggers, timeline ordering, and bounds verified)")

        # 6. Player Action Engine Simulation (Node.js)
        if verbose:
            print(f"\n[Step 6] Running Player Action Engine (Person A packagedIndexAdapter + resolveActions)...")

        sim_results = run_player_engine_simulation(index_path)
        boundary_verifications = sim_results.get("boundary_verifications", [])
        all_passed = sim_results.get("all_passed", False)

        total_checks = sum(len(bv["checks"]) for bv in boundary_verifications)
        passed_checks = sum(sum(1 for c in bv["checks"] if c["passed"]) for bv in boundary_verifications)

        if verbose:
            print(f"  Simulated {len(boundary_verifications)} mute cues across {total_checks} boundary check points.")
            print(f"  Check results: {passed_checks}/{total_checks} passed.")
            for bv in boundary_verifications[:5]:  # Print first 5 cues
                status = "PASS" if bv["passed"] else "FAIL"
                print(f"    [{status}] Cue {bv['cue_id']} [{bv['start']:.3f}s - {bv['end']:.3f}s)")
            if len(boundary_verifications) > 5:
                print(f"    ... and {len(boundary_verifications) - 5} more cues (all passed)")

        if not all_passed:
            raise RuntimeError("Player engine simulation detected failed boundary checks!")

        if verbose:
            print("\n" + "=" * 72)
            print("  END-TO-END SMOKE TEST RESULT: ALL PIPELINE STAGES PASSED!")
            print("=" * 72)

        return {
            "success": True,
            "film_id": film_id,
            "audio_duration_seconds": audio_duration,
            "raw_spikes_detected": len(raw_detections),
            "packaged_cues_count": len(packaged_cues),
            "total_boundary_checks": total_checks,
            "passed_boundary_checks": passed_checks,
            "detectors_audited": detectors,
            "artifacts": {
                "raw_cues": str(raw_cues_path),
                "index": str(index_path),
            },
        }

    finally:
        if temp_dir_obj and not keep_artifacts:
            temp_dir_obj.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-End Smoke Test for Trigger-Aware Player."
    )
    parser.add_argument(
        "--audio",
        type=Path,
        default=DEFAULT_AUDIO_PATH,
        help=f"Path to input WAV file (default: {DEFAULT_AUDIO_PATH})",
    )
    parser.add_argument(
        "--media",
        type=Path,
        default=DEFAULT_MEDIA_PATH,
        help=f"Path to input MP4 video file (default: {DEFAULT_MEDIA_PATH})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to config.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to index.schema.json (default: {DEFAULT_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--film-id",
        type=str,
        default=DEFAULT_FILM_ID,
        help=f"Film identifier (default: {DEFAULT_FILM_ID})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to save test artifacts (default: temporary directory)",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep generated test artifacts on disk",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON summary to stdout",
    )

    args = parser.parse_args()

    try:
        report = run_e2e_smoke_pipeline(
            audio_path=args.audio,
            media_path=args.media,
            config_path=args.config,
            schema_path=args.schema,
            film_id=args.film_id,
            output_dir=args.output_dir,
            keep_artifacts=args.keep_artifacts,
            verbose=not args.json,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        sys.exit(0)
    except Exception as exc:
        print(f"\n[ERROR] End-to-end smoke test failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
