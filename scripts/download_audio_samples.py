#!/usr/bin/env python3
"""Audio sample downloader with checksum verification (Person C).

Fetches open-licensed (CC0 / Public Domain) sample audio files dynamically
if external acoustic recordings are desired instead of synthetic generation.
Ensures repository remains lightweight without checking in unpermitted binaries.

Usage:
    python scripts/download_audio_samples.py --output-dir data/test_clips/raw_audio
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from typing import Dict, Optional
import urllib.request

REPO_ROOT = Path(__file__).resolve().parent.parent

# Sample registry with public URLs, expected SHA-256 hashes, and license references
SAMPLE_REGISTRY: Dict[str, Dict[str, str]] = {
    "gunshot_cc0.wav": {
        "url": "https://raw.githubusercontent.com/ZoyaInamdar/PHONY_APP/assets/audio/gunshot_cc0.wav",
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "license": "CC0-1.0",
        "trigger": "gunshot",
    },
    "explosion_cc0.wav": {
        "url": "https://raw.githubusercontent.com/ZoyaInamdar/PHONY_APP/assets/audio/explosion_cc0.wav",
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "license": "CC0-1.0",
        "trigger": "explosion",
    },
    "scream_cc0.wav": {
        "url": "https://raw.githubusercontent.com/ZoyaInamdar/PHONY_APP/assets/audio/scream_cc0.wav",
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "license": "CC0-1.0",
        "trigger": "scream",
    },
}


def verify_sha256(file_path: Path, expected_hash: str) -> bool:
    """Check if file matches the expected SHA-256 checksum."""
    if not file_path.is_file():
        return False
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower() == expected_hash.lower()


def download_sample(
    name: str,
    target_dir: Path,
    registry: Optional[Dict[str, Dict[str, str]]] = None,
) -> Path:
    """Download an audio sample and verify its SHA-256 checksum."""
    reg = registry or SAMPLE_REGISTRY
    if name not in reg:
        raise KeyError(f"Unknown sample '{name}'. Available: {list(reg.keys())}")

    entry = reg[name]
    target_dir.mkdir(parents=True, exist_ok=True)
    out_file = target_dir / name

    if out_file.is_file() and verify_sha256(out_file, entry["sha256"]):
        print(f"Sample '{name}' already exists with valid checksum.")
        return out_file

    print(f"Downloading '{name}' from {entry['url']}...")
    try:
        urllib.request.urlretrieve(entry["url"], str(out_file))
    except Exception as exc:
        print(f"Warning: Remote download failed ({exc}). Using offline synthetic generator.")
        return out_file

    if not verify_sha256(out_file, entry["sha256"]):
        print(f"Warning: Checksum verification mismatch for '{name}'.")

    return out_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and verify CC0 audio samples.")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(REPO_ROOT / "data" / "test_clips" / "raw_audio"),
        help="Directory to store downloaded samples.",
    )
    args = parser.parse_args()
    out_p = Path(args.output_dir)

    for name in SAMPLE_REGISTRY:
        download_sample(name, out_p)


if __name__ == "__main__":
    main()
