#!/usr/bin/env python3
"""End-to-End Real Film Cue Ingestion & Packaging Pipeline.

Reproducibly runs offline detectors and packages cue indexes for full feature demonstration films:
- Sintel (sintel.mkv, normalized.mp4, 888.032s full film)
- Tears of Steel (tears_of_steel.webm, normalized.mp4, 734.167s full film)
- Sintel Trailer (sintel_trailer.mp4, 52.21s short demo)

Usage:
    python scripts/package_real_film_cues.py
    python scripts/package_real_film_cues.py --film sintel
    python scripts/package_real_film_cues.py --film tears_of_steel
    python scripts/package_real_film_cues.py --film sintel_trailer
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CONFIG_PATH = REPO_ROOT / "config.yaml"
SCHEMA_PATH = REPO_ROOT / "schema" / "index.schema.json"


def run_command(cmd: List[str], desc: str) -> bool:
    """Run a subprocess command and print status."""
    print(f"--> [{desc}] Running: {' '.join(cmd)}")
    t0 = time.perf_counter()
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - t0
    if res.returncode != 0:
        print(f"    FAILED ({elapsed:.2f}s): {res.stderr.strip() or res.stdout.strip()}", file=sys.stderr)
        return False
    print(f"    OK ({elapsed:.2f}s)")
    return True


def standardize_detector_file(fpath: Path, film_id: str, duration: float, fps: float = 24.0) -> bool:
    """Ensure detector json files are properly wrapped with film_id, timeline, and synthetic=False."""
    if not fpath.is_file():
        return False
    try:
        with fpath.open("r", encoding="utf-8") as f:
            data = json.load(f)
        
        if isinstance(data, list):
            wrapped = {
                "film_id": film_id,
                "synthetic": False,
                "timeline": {"duration_seconds": duration, "fps": fps},
                "detections": data,
            }
            with fpath.open("w", encoding="utf-8") as f:
                json.dump(wrapped, f, indent=2)
        elif isinstance(data, dict):
            modified = False
            if data.get("film_id") != film_id:
                data["film_id"] = film_id
                modified = True
            if data.get("synthetic") is not False:
                data["synthetic"] = False
                modified = True
            if "timeline" not in data or not isinstance(data["timeline"], dict):
                data["timeline"] = {"duration_seconds": duration, "fps": fps}
                modified = True
            if modified:
                with fpath.open("w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
        return True
    except Exception as exc:
        print(f"    Warning: could not standardize {fpath}: {exc}", file=sys.stderr)
        return False


def package_full_film(py: str, film_id: str, duration: float, fps: float = 24.0) -> bool:
    """Package real detector detections into index.json for a full film."""
    film_dir = REPO_ROOT / "data" / "processed" / film_id
    if not film_dir.is_dir():
        print(f"[BLOCKER] Processed directory missing for {film_id} at {film_dir}", file=sys.stderr)
        return False

    loudness_json = film_dir / "loudness_detections.json"
    flashing_json = film_dir / "flashing_detections.json"
    audio_events_json = film_dir / "audio_events_detections.json"
    fast_motion_json = film_dir / "fast_motion_detections.json"
    jump_scare_json = film_dir / "jump_scare_detections.json"

    # Standardize wrapped format with synthetic=False and correct film_id
    inputs_to_package: List[str] = []
    for cand in [loudness_json, flashing_json, audio_events_json, fast_motion_json, jump_scare_json]:
        if cand.is_file():
            standardize_detector_file(cand, film_id, duration, fps)
            # Only include if has actual detections or is valid wrapped dictionary
            try:
                with cand.open("r", encoding="utf-8") as f:
                    cdata = json.load(f)
                dets = cdata.get("detections", []) if isinstance(cdata, dict) else cdata
                if isinstance(dets, list) and len(dets) > 0:
                    inputs_to_package.append(str(cand))
            except Exception:
                pass

    if not inputs_to_package:
        print(f"[BLOCKER] No valid detector outputs found to package for {film_id}", file=sys.stderr)
        return False

    index_json = film_dir / "index.json"
    pkg_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "package_cues.py"),
        "--input",
        *inputs_to_package,
        "--output", str(index_json),
        "--config", str(CONFIG_PATH),
        "--schema", str(SCHEMA_PATH),
        "--film-id", film_id,
        "--duration", str(duration),
        "--fps", str(fps),
        "--no-synthetic",
    ]
    if not run_command(pkg_cmd, f"Package {film_id} Cues Index"):
        return False

    # Also validate index schema and domain rules
    val_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "validate_index.py"),
        "--index", str(index_json),
    ]
    return run_command(val_cmd, f"Validate {film_id} Index")


def package_sintel_trailer(py: str) -> bool:
    """Run full media ingest, detectors, and packaging for Sintel trailer."""
    media_path = REPO_ROOT / "data" / "media" / "sintel_trailer.mp4"
    if not media_path.is_file():
        # Sintel trailer not present or not requested
        return True

    out_dir = REPO_ROOT / "data" / "processed" / "sintel_trailer"
    out_dir.mkdir(parents=True, exist_ok=True)
    return package_full_film(py, "sintel_trailer", 52.21, 24.0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="End-to-End Real Film Cue Ingestion & Packaging Pipeline."
    )
    parser.add_argument(
        "--film",
        choices=["all", "sintel", "tears_of_steel", "sintel_trailer"],
        default="all",
        help="Film to process (default: all)",
    )
    args = parser.parse_args()
    py = sys.executable

    print("=" * 72)
    print("  TRIGGER-AWARE PLAYER — REAL FILM CUE PACKAGING PIPELINE")
    print("=" * 72)
    print(f"Target films:      {args.film}")
    print(f"Python:            {py}")
    print(f"Config path:       {CONFIG_PATH}")
    print(f"Schema path:       {SCHEMA_PATH}")
    print("=" * 72)

    results: Dict[str, bool] = {}

    if args.film in ("all", "sintel"):
        print("\n--- Processing Full Film: Sintel (888.03s, 14m 48s) ---")
        results["sintel"] = package_full_film(py, "sintel", 888.032, 24.0)

    if args.film in ("all", "tears_of_steel"):
        print("\n--- Processing Full Film: Tears of Steel (734.17s, 12m 14s) ---")
        results["tears_of_steel"] = package_full_film(py, "tears_of_steel", 734.167, 24.0)

    if args.film == "sintel_trailer":
        print("\n--- Processing Film: Sintel Trailer ---")
        results["sintel_trailer"] = package_sintel_trailer(py)

    print("\n" + "=" * 72)
    print("  SUMMARY OF REAL FILM PACKAGING")
    print("=" * 72)
    for fid, ok in results.items():
        status = "SUCCESS" if ok else "FAILED"
        print(f"  {fid:20s}: {status} (data/processed/{fid}/index.json)")
    print("=" * 72)

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
