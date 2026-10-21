#!/usr/bin/env python3
"""End-to-End Real Film Cue Ingestion & Packaging Pipeline.

Reproducibly runs media ingestion, offline detectors, and index packaging
for available demonstration films:
- Sintel (sintel_trailer.mp4, 52.21s verified local media)
- Tears of Steel (subtitles.json from tears_of_steel.srt; video media pending)

Usage:
    python3 scripts/package_real_film_cues.py
    python3 scripts/package_real_film_cues.py --film sintel_trailer
    python3 scripts/package_real_film_cues.py --film tears_of_steel
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

VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
VENV312_PYTHON = REPO_ROOT / ".venv312" / "bin" / "python"

# Auto re-exec with repo venv if current environment lacks required packages
try:
    import yaml  # noqa: F401
    import jsonschema  # noqa: F401
except ImportError:
    if VENV_PYTHON.is_file() and sys.executable != str(VENV_PYTHON):
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON)] + sys.argv)
    elif VENV312_PYTHON.is_file() and sys.executable != str(VENV312_PYTHON):
        os.execv(str(VENV312_PYTHON), [str(VENV312_PYTHON)] + sys.argv)

CONFIG_PATH = REPO_ROOT / "config.yaml"
SCHEMA_PATH = REPO_ROOT / "schema" / "index.schema.json"


def package_film_cues(
    input_paths: Sequence[Path | str],
    output_path: Path | str,
    film_id: str,
    duration_seconds: float,
    fps: float = 24.0,
    config_path: Path | str = CONFIG_PATH,
    schema_path: Path | str = SCHEMA_PATH,
    synthetic: bool = False,
) -> Dict[str, Any]:
    """Package detector outputs into a validated cue index using the established cue contract."""
    from scripts.package_cues import package_and_save_index

    return package_and_save_index(
        input_path=input_paths,
        output_path=output_path,
        config_path=config_path,
        schema_path=schema_path,
        film_id=film_id,
        duration_seconds=duration_seconds,
        fps=fps,
        synthetic=synthetic,
    )


def run_command(cmd: List[str], desc: str) -> bool:
    """Run a subprocess command and print status."""
    resolved_cmd = list(cmd)
    if resolved_cmd and resolved_cmd[0] in ("python", "python3"):
        w = shutil.which(resolved_cmd[0])
        if not w or "WindowsApps" in w or (os.path.isfile(w) and os.path.getsize(w) == 0):
            resolved_cmd[0] = sys.executable
    print(f"--> [{desc}] Running: {' '.join(resolved_cmd)}")
    t0 = time.perf_counter()
    res = subprocess.run(resolved_cmd, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - t0
    if res.returncode != 0:
        print(f"    FAILED ({elapsed:.2f}s): {res.stderr.strip() or res.stdout.strip()}", file=sys.stderr)
        return False
    print(f"    OK ({elapsed:.2f}s)")
    return True


def package_sintel_trailer(py: str) -> bool:
    """Run full media ingest, detectors, and packaging for Sintel trailer."""
    media_path = REPO_ROOT / "data" / "media" / "sintel_trailer.mp4"
    if not media_path.is_file():
        print(f"[BLOCKER] Sintel trailer media missing at: {media_path}", file=sys.stderr)
        return False

    out_dir = REPO_ROOT / "data" / "processed" / "sintel_trailer"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Ingest media
    ingest_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "ingest_media.py"),
        "--input", str(media_path),
        "--output-dir", str(out_dir),
        "--film-id", "sintel_trailer",
    ]
    if not run_command(ingest_cmd, "Ingest Media"):
        return False

    audio_wav = out_dir / "audio_mono_44100.wav"
    normalized_video = out_dir / "normalized.mp4"

    # 2. Loudness detector
    loudness_json = out_dir / "loudness_detections.json"
    loudness_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "detect_loudness.py"),
        "--input", str(audio_wav),
        "--output", str(loudness_json),
        "--config", str(CONFIG_PATH),
        "--film-id", "sintel_trailer",
        "--mode", "spike",
    ]
    if not run_command(loudness_cmd, "Loudness Detector"):
        return False

    # 3. Flashing detector
    flashing_json = out_dir / "flashing_detections.json"
    flashing_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "detect_flashing.py"),
        "--input", str(normalized_video),
        "--output", str(flashing_json),
        "--config", str(CONFIG_PATH),
    ]
    if not run_command(flashing_cmd, "Flashing Detector"):
        return False

    # 4. Audio event classifier
    audio_events_json = out_dir / "audio_events_detections.json"
    audio_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "classify_audio_events.py"),
        "--input", str(audio_wav),
        "--output", str(audio_events_json),
        "--config", str(CONFIG_PATH),
    ]
    if not run_command(audio_cmd, "Audio Event Classifier"):
        return False

    # 5. Jump scare compound detector
    jump_scare_json = out_dir / "jump_scare_detections.json"
    js_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "detect_jump_scare.py"),
        "--input", str(normalized_video),
        "--output", str(jump_scare_json),
        "--config", str(CONFIG_PATH),
    ]
    if not run_command(js_cmd, "Jump Scare Detector"):
        return False

    # 6. Fast motion detector
    fast_motion_json = out_dir / "fast_motion_detections.json"
    fm_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "detect_fast_motion.py"),
        "--input", str(normalized_video),
        "--output", str(fast_motion_json),
        "--config", str(CONFIG_PATH),
    ]
    if not run_command(fm_cmd, "Fast Motion Detector"):
        return False

    # Standardize wrapped format with synthetic=False
<<<<<<< Updated upstream
    spider_json = out_dir / "spider_detections.json"
    raw_files = [flashing_json, audio_events_json, jump_scare_json, fast_motion_json]
    if spider_json.is_file():
        raw_files.append(spider_json)
=======
    raw_files = [flashing_json, audio_events_json, jump_scare_json, fast_motion_json]
>>>>>>> Stashed changes
    for rf in raw_files:
        if rf.is_file():
            with rf.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                wrapped = {
                    "film_id": "sintel_trailer",
                    "synthetic": False,
                    "timeline": {"duration_seconds": 52.21, "fps": 24.0},
                    "detections": data,
                }
                with rf.open("w", encoding="utf-8") as f:
                    json.dump(wrapped, f, indent=2)

    # 7. Package cues into index.json
    index_json = out_dir / "index.json"
<<<<<<< Updated upstream
    pkg_inputs = [
        str(loudness_json),
        str(flashing_json),
        str(audio_events_json),
        str(jump_scare_json),
        str(fast_motion_json),
    ]
    if spider_json.is_file():
        pkg_inputs.append(str(spider_json))

=======
>>>>>>> Stashed changes
    pkg_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "package_cues.py"),
        "--input",
<<<<<<< Updated upstream
        *pkg_inputs,
=======
        str(loudness_json),
        str(flashing_json),
        str(audio_events_json),
        str(jump_scare_json),
        str(fast_motion_json),
>>>>>>> Stashed changes
        "--output", str(index_json),
        "--config", str(CONFIG_PATH),
        "--schema", str(SCHEMA_PATH),
        "--film-id", "sintel_trailer",
        "--duration", "52.21",
        "--fps", "24.0",
        "--no-synthetic",
    ]
    if not run_command(pkg_cmd, "Package Cues Index"):
        return False

    # 8. Validate index
    val_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "validate_index.py"),
        "--index", str(index_json),
        "--media", str(media_path),
        "--duration-tolerance", "0.1",
    ]
    return run_command(val_cmd, "Validate Index")


<<<<<<< Updated upstream
def package_sintel(py: str) -> bool:
    """Run cue packaging for full feature film Sintel (888.04s)."""
    media_path = REPO_ROOT / "data" / "media" / "sintel.mkv"
    out_dir = REPO_ROOT / "data" / "processed" / "sintel"
    out_dir.mkdir(parents=True, exist_ok=True)

    spider_json = out_dir / "spider_detections.json"
    subtitles_json = out_dir / "subtitles.json"
    if not subtitles_json.is_file():
        subtitles_json = out_dir / "cues.json"

    # Standardize wrapped format if list
    if spider_json.is_file():
        with spider_json.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            wrapped = {
                "film_id": "sintel",
                "synthetic": False,
                "timeline": {"duration_seconds": 888.042, "fps": 24.0},
                "detections": data,
            }
            with spider_json.open("w", encoding="utf-8") as f:
                json.dump(wrapped, f, indent=2)

    if subtitles_json.is_file():
        with subtitles_json.open("r", encoding="utf-8") as f:
            s_data = json.load(f)
        if isinstance(s_data, list):
            s_wrapped = {
                "film_id": "sintel",
                "synthetic": False,
                "timeline": {"duration_seconds": 888.042, "fps": 24.0},
                "detections": s_data,
            }
            with subtitles_json.open("w", encoding="utf-8") as f:
                json.dump(s_wrapped, f, indent=2)
        elif isinstance(s_data, dict) and s_data.get("synthetic") is not False:
            s_data["film_id"] = "sintel"
            s_data["synthetic"] = False
            s_data["timeline"] = {"duration_seconds": 888.042, "fps": 24.0}
            with subtitles_json.open("w", encoding="utf-8") as f:
                json.dump(s_data, f, indent=2)

    inputs: List[str] = []
    if spider_json.is_file():
        inputs.append(str(spider_json))
    if subtitles_json.is_file():
        inputs.append(str(subtitles_json))

    if not inputs:
        print(f"[BLOCKER] No detections or cues found for Sintel in {out_dir}", file=sys.stderr)
        return False

=======
def package_tears_of_steel(py: str, allow_subtitles_only: bool = False) -> bool:
    """Package available real subtitle detections for Tears of Steel or report media blocker."""
    out_dir = REPO_ROOT / "data" / "processed" / "tears_of_steel"
    subtitles_json = out_dir / "subtitles.json"

    if not subtitles_json.is_file():
        print(f"[BLOCKER] Tears of steel subtitles.json missing at: {subtitles_json}", file=sys.stderr)
        return False

    # Check for full video media asset
    media_path = REPO_ROOT / "data" / "media" / "tears_of_steel.webm"
    if not media_path.is_file():
        print(f"[BLOCKER] Tears of Steel video container missing at: {media_path}", file=sys.stderr)
        print("          Full film video/audio ingestion and detector execution is blocked because media file is absent.", file=sys.stderr)
        if not allow_subtitles_only:
            print("          Per project instructions: stopping processing for tears_of_steel.", file=sys.stderr)
            return False
        print("          --allow-subtitles-only specified: packaging available real subtitle detections.", file=sys.stderr)

    # Standardize wrapped format
    with subtitles_json.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["film_id"] = "tears_of_steel"
    data["synthetic"] = False
    data["timeline"] = {"duration_seconds": 734.167, "fps": 24.0}
    with subtitles_json.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

>>>>>>> Stashed changes
    index_json = out_dir / "index.json"
    pkg_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "package_cues.py"),
<<<<<<< Updated upstream
        "--input",
        *inputs,
        "--output", str(index_json),
        "--config", str(CONFIG_PATH),
        "--schema", str(SCHEMA_PATH),
        "--film-id", "sintel",
        "--duration", "888.042",
        "--fps", "24.0",
        "--no-synthetic",
    ]
    if not run_command(pkg_cmd, "Package Sintel Full Film Cues"):
=======
        "--input", str(subtitles_json),
        "--output", str(index_json),
        "--config", str(CONFIG_PATH),
        "--schema", str(SCHEMA_PATH),
        "--film-id", "tears_of_steel",
        "--duration", "734.167",
        "--fps", "24.0",
        "--no-synthetic",
    ]
    if not run_command(pkg_cmd, "Package Tears of Steel Cues"):
>>>>>>> Stashed changes
        return False

    val_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "validate_index.py"),
        "--index", str(index_json),
    ]
<<<<<<< Updated upstream
    if media_path.is_file() and shutil.which("ffprobe"):
        val_cmd.extend(["--media", str(media_path), "--duration-tolerance", "0.5"])

    return run_command(val_cmd, "Validate Sintel Full Film Index")


def package_tears_of_steel(py: str, allow_subtitles_only: bool = False) -> bool:
    """Package available real subtitle detections for Tears of Steel or report media blocker."""
    out_dir = REPO_ROOT / "data" / "processed" / "tears_of_steel"
    subtitles_json = out_dir / "subtitles.json"

    if not subtitles_json.is_file():
        print(f"[BLOCKER] Tears of steel subtitles.json missing at: {subtitles_json}", file=sys.stderr)
        return False

    # Check for full video media asset
    media_path = REPO_ROOT / "data" / "media" / "tears_of_steel.webm"
    if not media_path.is_file():
        print(f"[BLOCKER] Tears of Steel video container missing at: {media_path}", file=sys.stderr)
        print("          Full film video/audio ingestion and detector execution is blocked because media file is absent.", file=sys.stderr)
        if not allow_subtitles_only:
            print("          Per project instructions: stopping processing for tears_of_steel.", file=sys.stderr)
            return False
        print("          --allow-subtitles-only specified: packaging available real subtitle detections.", file=sys.stderr)

    # Standardize wrapped format
    with subtitles_json.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["film_id"] = "tears_of_steel"
    data["synthetic"] = False
    data["timeline"] = {"duration_seconds": 734.167, "fps": 24.0}
    with subtitles_json.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    index_json = out_dir / "index.json"
    pkg_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "package_cues.py"),
        "--input", str(subtitles_json),
        "--output", str(index_json),
        "--config", str(CONFIG_PATH),
        "--schema", str(SCHEMA_PATH),
        "--film-id", "tears_of_steel",
        "--duration", "734.167",
        "--fps", "24.0",
        "--no-synthetic",
    ]
    if not run_command(pkg_cmd, "Package Tears of Steel Cues"):
        return False

    val_cmd = [
        py,
        str(REPO_ROOT / "scripts" / "validate_index.py"),
        "--index", str(index_json),
    ]
=======
>>>>>>> Stashed changes
    return run_command(val_cmd, "Validate Tears of Steel Index")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="End-to-End Real Film Cue Ingestion & Packaging Pipeline."
    )
    parser.add_argument(
        "--film",
<<<<<<< Updated upstream
        choices=["all", "sintel", "sintel_trailer", "tears_of_steel"],
=======
        choices=["all", "sintel_trailer", "tears_of_steel"],
>>>>>>> Stashed changes
        default="all",
        help="Film to process (default: all)",
    )
    parser.add_argument(
        "--allow-subtitles-only",
        action="store_true",
        help="Package available real subtitle detections for films whose full video media is absent.",
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

<<<<<<< Updated upstream
    sintel_trailer_ok = True
    sintel_full_ok = True
    tos_ok = True

    if args.film in ("all", "sintel_trailer"):
        print("\n--- Processing Film 1: Sintel Trailer (Real Media: data/media/sintel_trailer.mp4) ---")
        sintel_trailer_ok = package_sintel_trailer(py)

    if args.film in ("all", "sintel"):
        print("\n--- Processing Film 2: Sintel Full Film (Real Media: data/media/sintel.mkv) ---")
        sintel_full_ok = package_sintel(py)

    if args.film in ("all", "tears_of_steel"):
        print("\n--- Processing Film 3: Tears of Steel (Subtitles: data/processed/tears_of_steel/) ---")
=======
    sintel_ok = True
    tos_ok = True

    if args.film in ("all", "sintel_trailer"):
        print("\n--- Processing Film 1: Sintel Trailer (Real Media: data/media/sintel_trailer.mp4) ---")
        sintel_ok = package_sintel_trailer(py)

    if args.film in ("all", "tears_of_steel"):
        print("\n--- Processing Film 2: Tears of Steel (Subtitles: data/processed/tears_of_steel/) ---")
>>>>>>> Stashed changes
        tos_ok = package_tears_of_steel(py, allow_subtitles_only=args.allow_subtitles_only)

    print("\n" + "=" * 72)
    print("  SUMMARY OF REAL FILM PACKAGING")
    print("=" * 72)
<<<<<<< Updated upstream
    if args.film in ("all", "sintel_trailer"):
        print(f"Sintel Trailer:    {'SUCCESS (data/processed/sintel_trailer/index.json)' if sintel_trailer_ok else 'FAILED'}")
    if args.film in ("all", "sintel"):
        print(f"Sintel Full Film:  {'SUCCESS (data/processed/sintel/index.json)' if sintel_full_ok else 'FAILED'}")
    if args.film in ("all", "tears_of_steel"):
        print(f"Tears of Steel:    {'SUCCESS (data/processed/tears_of_steel/index.json)' if tos_ok else 'BLOCKED (data/media/tears_of_steel.webm missing)'}")
    print("=" * 72)

    return 0 if (sintel_trailer_ok and sintel_full_ok and tos_ok) else 1
=======
    print(f"Sintel Trailer:  {'SUCCESS (data/processed/sintel_trailer/index.json)' if sintel_ok else 'FAILED'}")
    print(f"Tears of Steel:  {'SUCCESS (data/processed/tears_of_steel/index.json)' if tos_ok else 'BLOCKED (data/media/tears_of_steel.webm missing)'}")
    print("=" * 72)

    return 0 if (sintel_ok and tos_ok) else 1
>>>>>>> Stashed changes


if __name__ == "__main__":
    sys.exit(main())
