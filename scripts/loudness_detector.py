#!/usr/bin/env python3
"""Loudness detector alias module for Trigger-Aware Player.

Re-exports detection functions and CLI from scripts/detect_loudness.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.detect_loudness import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SOURCE,
    calculate_window_dbfs,
    detect_loud_spikes,
    detect_loudness,
    detect_loudness_from_samples,
    detect_loudness_from_wav,
    detect_sustained_loudness,
    load_detector_config,
    main,
    stream_wav_windows,
)

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_SOURCE",
    "calculate_window_dbfs",
    "detect_loud_spikes",
    "detect_loudness",
    "detect_loudness_from_samples",
    "detect_loudness_from_wav",
    "detect_sustained_loudness",
    "load_detector_config",
    "main",
    "stream_wav_windows",
]

if __name__ == "__main__":
    sys.exit(main())
