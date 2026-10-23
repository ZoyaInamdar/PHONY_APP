#!/usr/bin/env python3
"""Audio loudness and spike detection pipeline for Trigger-Aware Player (Person B).

Detects audio loudness triggers:
1. 'loud_spike': Sudden, sharp increases in root-mean-square (RMS) loudness
   relative to a local background rolling median (configured in config.yaml under
   triggers.loud_spike).
2. 'sustained_loudness': Audio remaining above a calibrated loudness threshold
   for a sustained duration, with tolerance for brief dips.

Contracts & Invariants:
- Shared Timeline Helper: All timestamp conversions use scripts/timeline.py
  (sample_to_seconds, seconds_to_sample) for exact sample-to-seconds alignment.
- Zero-Based Presentation Timestamps: All timestamps are seconds on the media timeline.
- Half-Open Intervals: Intervals strictly follow [start, end) where end is exclusive.
- Chunked Audio Streaming: Reads WAV files in manageable streaming buffers to avoid
  loading large movie audio into memory.
- Output Format: Every emitted detection record complies with the shared detector contract:
    {
        "start": float,     # seconds, >= 0.0
        "end": float,       # seconds, > start
        "trigger": str,     # "loud_spike" or "sustained_loudness"
        "score": float,     # [0.0, 1.0] normalized detector confidence/intensity
        "source": "loudness_detector"
    }

Usage:
    python scripts/detect_loudness.py --input "data/processed/smoke_test/audio_mono_44100.wav"
    python scripts/detect_loudness.py --input "audio.wav" --output "cues_loudness.json" --mode all
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
import wave
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import yaml

from scripts.timeline import sample_to_seconds, seconds_to_sample

DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_SOURCE = "loudness_detector"

# Baseline defaults aligned with config.yaml (triggers.loud_spike.algorithm)
DEFAULT_RMS_WINDOW_SEC = 0.1             # 100 ms RMS analysis window
DEFAULT_ROLLING_MEDIAN_SEC = 10.0        # 10.0 s background history buffer
DEFAULT_RELATIVE_SPIKE_THRESHOLD_DB = 15.0 # 15.0 dB increase above rolling median (approved Run 10)
DEFAULT_NUMERICAL_FLOOR_DB = -90.0       # Floor to prevent log10(0)
DEFAULT_EPSILON = 1e-9
DEFAULT_MIN_ABSOLUTE_SPIKE_DBFS = -18.0  # Floor below which sounds are too quiet to be loud spikes (-18.0 dBFS approved Run 10)
DEFAULT_LEADING_WINDOW_POLICY = "use_available_history"

# Baseline defaults for sustained loudness detection
DEFAULT_SUSTAINED_THRESHOLD_DBFS = -18.0 # -18.0 dBFS threshold
DEFAULT_SUSTAINED_MIN_DURATION_SEC = 2.0 # 2.0 s minimum duration
DEFAULT_SUSTAINED_MAX_DIP_SEC = 0.5      # 0.5 s maximum allowable brief dip to bridge


def load_detector_config(config_path: Union[str, Path] = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load configuration from config.yaml, returning safe defaults if absent."""
    p = Path(config_path)
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def calculate_window_dbfs(
    pcm_slice: np.ndarray,
    numerical_floor_db: float = DEFAULT_NUMERICAL_FLOOR_DB,
    epsilon: float = DEFAULT_EPSILON,
) -> float:
    """Calculate RMS amplitude in dBFS for a slice of normalized audio samples in [-1.0, 1.0].

    Parameters:
        pcm_slice: 1D numpy array of float32/float64 samples normalized to [-1.0, 1.0].
        numerical_floor_db: Minimum decibel floor (default -90.0 dBFS).
        epsilon: Small positive constant to prevent log10(0).

    Returns:
        Loudness in dBFS (clamped between numerical_floor_db and 0.0).
    """
    if len(pcm_slice) == 0:
        return float(numerical_floor_db)

    mean_sq = float(np.mean(pcm_slice.astype(np.float64) ** 2))
    if mean_sq <= 0.0 or not math.isfinite(mean_sq):
        return float(numerical_floor_db)

    rms = math.sqrt(mean_sq)
    if rms <= epsilon:
        return float(numerical_floor_db)

    db = 20.0 * math.log10(rms)
    if not math.isfinite(db) or db < numerical_floor_db:
        return float(numerical_floor_db)
    return min(0.0, db)


def stream_wav_windows(
    wav_path: Union[str, Path],
    window_duration_sec: float = DEFAULT_RMS_WINDOW_SEC,
    chunk_seconds: float = 1.0,
) -> Generator[Tuple[int, int, float], None, Tuple[int, int]]:
    """Stream audio from a 16-bit PCM WAV file yielding (start_sample, end_sample, dbfs).

    Processes audio in streaming chunks without loading the entire movie into memory.

    Parameters:
        wav_path: Path to mono 16-bit PCM WAV file.
        window_duration_sec: Duration of each analysis window in seconds (default 0.1s).
        chunk_seconds: Size of chunk to read from disk at once in seconds (default 1.0s).

    Yields:
        (window_start_sample, window_end_sample, dbfs)

    Returns:
        (total_samples, sample_rate)
    """
    path = Path(wav_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio WAV file not found: {path}")

    with wave.open(str(path), "rb") as w:
        sample_rate = w.getframerate()
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        total_samples = w.getnframes()

        if sampwidth != 2:
            raise ValueError(f"Expected 16-bit PCM WAV (sampwidth=2), got sampwidth={sampwidth}")
        if channels != 1:
            raise ValueError(f"Expected mono WAV (channels=1), got channels={channels}")

        window_samples = max(1, seconds_to_sample(window_duration_sec, sample_rate, rounding="round"))
        chunk_samples = max(window_samples, seconds_to_sample(chunk_seconds, sample_rate, rounding="round"))

        current_sample = 0
        remainder = np.empty(0, dtype=np.float32)

        while True:
            raw_bytes = w.readframes(chunk_samples)
            if not raw_bytes:
                break

            chunk_int16 = np.frombuffer(raw_bytes, dtype=np.int16)
            chunk_float = chunk_int16.astype(np.float32) / 32768.0

            if len(remainder) > 0:
                combined = np.concatenate((remainder, chunk_float))
            else:
                combined = chunk_float

            num_windows = len(combined) // window_samples
            for w_idx in range(num_windows):
                w_start = current_sample
                w_end = current_sample + window_samples
                w_slice = combined[w_idx * window_samples : (w_idx + 1) * window_samples]
                db = calculate_window_dbfs(w_slice)
                yield (w_start, w_end, db)
                current_sample += window_samples

            remainder_start = num_windows * window_samples
            remainder = combined[remainder_start:]

        # Handle final partial window if any audio samples remain
        if len(remainder) > 0:
            w_start = current_sample
            w_end = current_sample + len(remainder)
            db = calculate_window_dbfs(remainder)
            yield (w_start, w_end, db)

        return (total_samples, sample_rate)


def detect_loud_spikes(
    window_data: List[Tuple[int, int, float]],
    sample_rate: int,
    relative_spike_threshold_db: float = DEFAULT_RELATIVE_SPIKE_THRESHOLD_DB,
    rolling_median_duration_sec: float = DEFAULT_ROLLING_MEDIAN_SEC,
    window_duration_sec: float = DEFAULT_RMS_WINDOW_SEC,
    min_absolute_spike_dbfs: float = DEFAULT_MIN_ABSOLUTE_SPIKE_DBFS,
    leading_window_policy: str = DEFAULT_LEADING_WINDOW_POLICY,
    initial_median_db: Optional[float] = None,
    source: str = DEFAULT_SOURCE,
) -> List[Dict[str, Any]]:
    """Detect sudden loudness spikes relative to rolling median background.

    Parameters:
        window_data: List of (start_sample, end_sample, dbfs).
        sample_rate: Audio sample rate in Hz.
        relative_spike_threshold_db: Decibels above rolling median to trigger spike (default 12.0).
        rolling_median_duration_sec: History buffer duration in seconds (default 10.0).
        window_duration_sec: Analysis window duration in seconds (default 0.1).
        min_absolute_spike_dbfs: Minimum absolute dBFS required to qualify as a loud spike.
        leading_window_policy: "use_available_history" or "pad_with_initial_median".
        initial_median_db: Optional initial baseline dB to pre-seed history.
        source: Detector component identifier (default "loudness_detector").

    Returns:
        List of detection dictionaries adhering to the shared contract.
    """
    if not window_data:
        return []

    if relative_spike_threshold_db is None:
        relative_spike_threshold_db = DEFAULT_RELATIVE_SPIKE_THRESHOLD_DB
    if min_absolute_spike_dbfs is None:
        min_absolute_spike_dbfs = DEFAULT_MIN_ABSOLUTE_SPIKE_DBFS

    history_len = max(1, int(round(rolling_median_duration_sec / window_duration_sec)))
    history: collections.deque[float] = collections.deque(maxlen=history_len)

    if initial_median_db is not None:
        history.append(float(initial_median_db))

    spike_runs: List[Dict[str, Any]] = []
    current_run: Optional[Dict[str, Any]] = None

    for start_samp, end_samp, db in window_data:
        # Determine reference median
        if len(history) == 0:
            if leading_window_policy == "use_available_history":
                ref_median = db
            else:
                ref_median = initial_median_db if initial_median_db is not None else db
        else:
            ref_median = float(np.median(history))

        # Check for spike condition
        delta_db = db - ref_median
        is_spike = (delta_db >= relative_spike_threshold_db) and (db >= min_absolute_spike_dbfs)

        if is_spike:
            # Normalized score based on spike magnitude above threshold
            # Clamped into [0.10, 1.00]
            score = min(1.0, max(0.10, delta_db / (relative_spike_threshold_db * 2.5)))
            score = round(score, 3)

            if current_run is None:
                current_run = {
                    "start_sample": start_samp,
                    "end_sample": end_samp,
                    "scores": [score],
                    "deltas": [delta_db],
                }
            else:
                # Merge consecutive loud window into current run
                current_run["end_sample"] = end_samp
                current_run["scores"].append(score)
                current_run["deltas"].append(delta_db)
        else:
            # Finalize run if active
            if current_run is not None:
                spike_runs.append(current_run)
                current_run = None

        # Update history with current window dB
        history.append(db)

    if current_run is not None:
        spike_runs.append(current_run)

    # Convert sample boundaries to seconds using scripts/timeline.py
    detections: List[Dict[str, Any]] = []
    for run in spike_runs:
        start_sec = sample_to_seconds(run["start_sample"], sample_rate)
        end_sec = sample_to_seconds(run["end_sample"], sample_rate)
        best_score = max(run["scores"])

        detections.append({
            "start": round(start_sec, 4),
            "end": round(end_sec, 4),
            "trigger": "loud_spike",
            "score": best_score,
            "source": source,
        })

    return detections


def detect_sustained_loudness(
    window_data: List[Tuple[int, int, float]],
    sample_rate: int,
    threshold_dbfs: float = DEFAULT_SUSTAINED_THRESHOLD_DBFS,
    min_duration_sec: float = DEFAULT_SUSTAINED_MIN_DURATION_SEC,
    max_dip_sec: float = DEFAULT_SUSTAINED_MAX_DIP_SEC,
    window_duration_sec: float = DEFAULT_RMS_WINDOW_SEC,
    source: str = DEFAULT_SOURCE,
) -> List[Dict[str, Any]]:
    """Detect sustained loudness events above a threshold with tolerance for brief dips.

    Parameters:
        window_data: List of (start_sample, end_sample, dbfs).
        sample_rate: Audio sample rate in Hz.
        threshold_dbfs: Minimum dBFS to qualify as loud (default -18.0).
        min_duration_sec: Minimum duration in seconds to trigger sustained loudness (default 2.0).
        max_dip_sec: Maximum duration in seconds of brief dips to bridge (default 0.5).
        window_duration_sec: Analysis window duration in seconds (default 0.1).
        source: Detector component identifier (default "loudness_detector").

    Returns:
        List of detection dictionaries adhering to the shared contract.
    """
    if not window_data:
        return []

    max_dip_windows = max(0, int(round(max_dip_sec / window_duration_sec)))
    min_windows = max(1, int(round(min_duration_sec / window_duration_sec)))

    runs: List[Dict[str, Any]] = []
    in_run = False
    run_start_samp = 0
    run_end_samp = 0
    run_scores: List[float] = []
    dip_count = 0

    for start_samp, end_samp, db in window_data:
        is_loud = (db >= threshold_dbfs)

        if is_loud:
            # Score scaled by how much level exceeds threshold
            score = min(1.0, max(0.5, 0.5 + (db - threshold_dbfs) / 30.0 * 0.5))
            score = round(score, 3)

            if not in_run:
                in_run = True
                run_start_samp = start_samp
                run_scores = [score]
            else:
                run_scores.append(score)

            run_end_samp = end_samp
            dip_count = 0
        else:
            if in_run:
                dip_count += 1
                if dip_count > max_dip_windows:
                    # Dip exceeded bridge threshold; finalize previous run
                    span_sec = sample_to_seconds(run_end_samp - run_start_samp, sample_rate)
                    if span_sec >= min_duration_sec:
                        runs.append({
                            "start_sample": run_start_samp,
                            "end_sample": run_end_samp,
                            "score": max(run_scores) if run_scores else 0.5,
                        })
                    in_run = False
                    dip_count = 0
                    run_scores = []

    if in_run:
        span_sec = sample_to_seconds(run_end_samp - run_start_samp, sample_rate)
        if span_sec >= min_duration_sec:
            runs.append({
                "start_sample": run_start_samp,
                "end_sample": run_end_samp,
                "score": max(run_scores) if run_scores else 0.5,
            })

    # Convert to contract records
    detections: List[Dict[str, Any]] = []
    for r in runs:
        start_sec = sample_to_seconds(r["start_sample"], sample_rate)
        end_sec = sample_to_seconds(r["end_sample"], sample_rate)
        detections.append({
            "start": round(start_sec, 4),
            "end": round(end_sec, 4),
            "trigger": "sustained_loudness",
            "score": r["score"],
            "source": source,
        })

    return detections


def detect_loudness_from_samples(
    samples: np.ndarray,
    sample_rate: int = 44100,
    config: Optional[Dict[str, Any]] = None,
    modes: Tuple[str, ...] = ("spike", "sustained"),
    initial_median_db: Optional[float] = None,
    relative_spike_threshold_db: Optional[float] = None,
    min_absolute_spike_dbfs: Optional[float] = None,
    sustained_threshold_dbfs: Optional[float] = None,
    sustained_min_duration_sec: Optional[float] = None,
    source: str = DEFAULT_SOURCE,
) -> List[Dict[str, Any]]:
    """Detect loudness triggers directly from an in-memory numpy float array in [-1.0, 1.0].

    Ideal for fast, deterministic unit testing without file I/O.
    """
    if config is None:
        config = load_detector_config()

    spike_algo = config.get("triggers", {}).get("loud_spike", {}).get("algorithm", {})
    window_sec = float(spike_algo.get("rms_window_duration_seconds", DEFAULT_RMS_WINDOW_SEC))
    rolling_med_sec = float(spike_algo.get("rolling_median_duration_seconds", DEFAULT_ROLLING_MEDIAN_SEC))

    if relative_spike_threshold_db is None:
        relative_spike_threshold_db = float(
            spike_algo.get("relative_spike_threshold_db", DEFAULT_RELATIVE_SPIKE_THRESHOLD_DB)
        )

    if min_absolute_spike_dbfs is None:
        min_absolute_spike_dbfs = float(
            spike_algo.get("min_absolute_spike_dbfs", DEFAULT_MIN_ABSOLUTE_SPIKE_DBFS)
        )

    if sustained_threshold_dbfs is None:
        sustained_threshold_dbfs = DEFAULT_SUSTAINED_THRESHOLD_DBFS
    if sustained_min_duration_sec is None:
        sustained_min_duration_sec = DEFAULT_SUSTAINED_MIN_DURATION_SEC

    window_samples = max(1, seconds_to_sample(window_sec, sample_rate, rounding="round"))
    total_samples = len(samples)

    # Slice into windows
    window_data: List[Tuple[int, int, float]] = []
    num_full_windows = total_samples // window_samples
    for w_idx in range(num_full_windows):
        w_start = w_idx * window_samples
        w_end = w_start + window_samples
        db = calculate_window_dbfs(samples[w_start:w_end])
        window_data.append((w_start, w_end, db))

    rem = total_samples % window_samples
    if rem > 0:
        w_start = num_full_windows * window_samples
        w_end = total_samples
        db = calculate_window_dbfs(samples[w_start:w_end])
        window_data.append((w_start, w_end, db))

    detections: List[Dict[str, Any]] = []

    if "spike" in modes:
        detections.extend(
            detect_loud_spikes(
                window_data=window_data,
                sample_rate=sample_rate,
                relative_spike_threshold_db=relative_spike_threshold_db,
                rolling_median_duration_sec=rolling_med_sec,
                window_duration_sec=window_sec,
                min_absolute_spike_dbfs=min_absolute_spike_dbfs,
                initial_median_db=initial_median_db,
                source=source,
            )
        )

    if "sustained" in modes:
        detections.extend(
            detect_sustained_loudness(
                window_data=window_data,
                sample_rate=sample_rate,
                threshold_dbfs=sustained_threshold_dbfs,
                min_duration_sec=sustained_min_duration_sec,
                window_duration_sec=window_sec,
                source=source,
            )
        )

    # Chronological sort
    detections.sort(key=lambda d: (d["start"], d["end"], d["trigger"]))
    return detections


def detect_loudness_from_wav(
    wav_path: Union[str, Path],
    config: Optional[Dict[str, Any]] = None,
    modes: Tuple[str, ...] = ("spike", "sustained"),
    initial_median_db: Optional[float] = None,
    relative_spike_threshold_db: Optional[float] = None,
    min_absolute_spike_dbfs: Optional[float] = None,
    sustained_threshold_dbfs: Optional[float] = None,
    sustained_min_duration_sec: Optional[float] = None,
    source: str = DEFAULT_SOURCE,
) -> List[Dict[str, Any]]:
    """Stream audio from a WAV file and detect loudness triggers."""
    if config is None:
        config = load_detector_config()

    spike_algo = config.get("triggers", {}).get("loud_spike", {}).get("algorithm", {})
    window_sec = float(spike_algo.get("rms_window_duration_seconds", DEFAULT_RMS_WINDOW_SEC))
    rolling_med_sec = float(spike_algo.get("rolling_median_duration_seconds", DEFAULT_ROLLING_MEDIAN_SEC))

    if relative_spike_threshold_db is None:
        relative_spike_threshold_db = float(
            spike_algo.get("relative_spike_threshold_db", DEFAULT_RELATIVE_SPIKE_THRESHOLD_DB)
        )

    if sustained_threshold_dbfs is None:
        sustained_threshold_dbfs = DEFAULT_SUSTAINED_THRESHOLD_DBFS
    if sustained_min_duration_sec is None:
        sustained_min_duration_sec = DEFAULT_SUSTAINED_MIN_DURATION_SEC

    # Stream windows directly
    window_data: List[Tuple[int, int, float]] = []
    sample_rate = 44100
    gen = stream_wav_windows(wav_path, window_duration_sec=window_sec)

    try:
        while True:
            item = next(gen)
            window_data.append(item)
    except StopIteration as stop:
        if stop.value:
            _, sample_rate = stop.value

    detections: List[Dict[str, Any]] = []

    if "spike" in modes:
        detections.extend(
            detect_loud_spikes(
                window_data=window_data,
                sample_rate=sample_rate,
                relative_spike_threshold_db=relative_spike_threshold_db,
                rolling_median_duration_sec=rolling_med_sec,
                window_duration_sec=window_sec,
                min_absolute_spike_dbfs=min_absolute_spike_dbfs,
                initial_median_db=initial_median_db,
                source=source,
            )
        )

    if "sustained" in modes:
        detections.extend(
            detect_sustained_loudness(
                window_data=window_data,
                sample_rate=sample_rate,
                threshold_dbfs=sustained_threshold_dbfs,
                min_duration_sec=sustained_min_duration_sec,
                window_duration_sec=window_sec,
                source=source,
            )
        )

    detections.sort(key=lambda d: (d["start"], d["end"], d["trigger"]))
    return detections


def detect_loudness(
    input_source: Union[str, Path, np.ndarray],
    sample_rate: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
    output_path: Optional[Union[str, Path]] = None,
    modes: Tuple[str, ...] = ("spike", "sustained"),
    film_id: str = "smoke_test",
    relative_spike_threshold_db: Optional[float] = None,
    min_absolute_spike_dbfs: Optional[float] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """Unified entry point for loudness detection from WAV file path or numpy array."""
    if isinstance(input_source, (str, Path)):
        detections = detect_loudness_from_wav(
            wav_path=input_source,
            config=config,
            modes=modes,
            relative_spike_threshold_db=relative_spike_threshold_db,
            min_absolute_spike_dbfs=min_absolute_spike_dbfs,
            **kwargs,
        )
    elif isinstance(input_source, np.ndarray):
        sr = sample_rate if sample_rate is not None else 44100
        detections = detect_loudness_from_samples(
            samples=input_source,
            sample_rate=sr,
            config=config,
            modes=modes,
            relative_spike_threshold_db=relative_spike_threshold_db,
            min_absolute_spike_dbfs=min_absolute_spike_dbfs,
            **kwargs,
        )
    else:
        raise TypeError(f"input_source must be str, Path, or np.ndarray, got {type(input_source)}")

    if output_path is not None:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "record_type": "raw_detections",
            "film_id": film_id,
            "synthetic": False,
            "total_detections": len(detections),
            "detections": detections,
        }
        with out_p.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    return detections


def main() -> int:
    """CLI entry point for audio loudness and spike detection."""
    parser = argparse.ArgumentParser(
        description="Audio loudness and spike detection for Trigger-Aware Player."
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to input mono 16-bit PCM WAV file.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to output JSON file to store raw detections (optional).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to config.yaml (default: config.yaml).",
    )
    parser.add_argument(
        "--mode",
        choices=["all", "spike", "sustained"],
        default="all",
        help="Detection mode: 'all', 'spike', or 'sustained' (default: all).",
    )
    parser.add_argument(
        "--film-id",
        type=str,
        default="smoke_test",
        help="Film identifier for raw detection payload.",
    )
    parser.add_argument(
        "--relative-spike-threshold-db",
        type=float,
        default=None,
        help="Relative dB increase above rolling median to trigger spike (default: from config or 15.0).",
    )
    parser.add_argument(
        "--min-absolute-spike-dbfs",
        type=float,
        default=None,
        help="Minimum absolute dBFS to qualify as a loud spike (default: from config or -18.0).",
    )

    args = parser.parse_args()

    mode_map = {
        "all": ("spike", "sustained"),
        "spike": ("spike",),
        "sustained": ("sustained",),
    }
    selected_modes = mode_map[args.mode]

    try:
        detections = detect_loudness(
            input_source=args.input,
            config=load_detector_config(args.config),
            output_path=args.output,
            modes=selected_modes,
            film_id=args.film_id,
            relative_spike_threshold_db=args.relative_spike_threshold_db,
            min_absolute_spike_dbfs=args.min_absolute_spike_dbfs,
        )
        print(f"Loudness detection complete: found {len(detections)} events.")
        for d in detections[:10]:
            print(f"  [{d['start']:.2f}s - {d['end']:.2f}s] {d['trigger']} (score: {d['score']:.3f})")
        if len(detections) > 10:
            print(f"  ... and {len(detections) - 10} more events.")
        return 0
    except Exception as exc:
        print(f"Error during loudness detection: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
