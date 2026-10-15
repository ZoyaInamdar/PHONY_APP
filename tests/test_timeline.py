#!/usr/bin/env python3
"""Unit tests for shared timeline time-conversion helper (scripts/timeline.py).

Verifies:
- Frame index to seconds conversion (at 24 FPS, non-integer rates like 23.976)
- Sample index to seconds conversion (at 44,100 Hz, 48,000 Hz)
- Reverse conversions with round, floor, and ceil policies
- Python built-in round ties-to-even behavior
- Half-open frame interval conversions [start_frame, end_frame)
- Roundtrip integrity
- Strict rejection of negative indices, negative/zero rates, NaN/inf, invalid types,
  booleans masquerading as ints/floats, and empty/reversed intervals.
"""

from __future__ import annotations

import math
import pytest

from scripts.timeline import (
    SUPPORTED_ROUNDING_POLICIES,
    TimelineError,
    TimelineTypeError,
    TimelineValueError,
    frame_interval_to_seconds,
    frame_to_seconds,
    sample_to_seconds,
    seconds_to_frame,
    seconds_to_sample,
)


# ==============================================================================
# 1. Frame conversions at 24 FPS
# ==============================================================================

def test_frame_to_seconds_24fps_cardinal():
    """Verify standard 24 FPS reference points (0 -> 0.0, 24 -> 1.0, 48 -> 2.0)."""
    assert frame_to_seconds(0, 24.0) == 0.0
    assert frame_to_seconds(24, 24.0) == 1.0
    assert frame_to_seconds(48, 24.0) == 2.0
    assert frame_to_seconds(12, 24.0) == 0.5
    assert frame_to_seconds(72, 24.0) == 3.0


# ==============================================================================
# 2. Audio conversions at 44,100 Hz
# ==============================================================================

def test_sample_to_seconds_44100hz_cardinal():
    """Verify standard 44,100 Hz reference points (0 -> 0.0, 44100 -> 1.0)."""
    assert sample_to_seconds(0, 44100) == 0.0
    assert sample_to_seconds(44100, 44100) == 1.0
    assert sample_to_seconds(88200, 44100) == 2.0
    assert sample_to_seconds(22050, 44100) == 0.5


def test_sample_to_seconds_48000hz():
    """Verify 48,000 Hz conversions."""
    assert sample_to_seconds(0, 48000) == 0.0
    assert sample_to_seconds(48000, 48000) == 1.0
    assert sample_to_seconds(24000, 48000) == 0.5


# ==============================================================================
# 3. Non-integer FPS (e.g. 23.976, 29.97)
# ==============================================================================

def test_frame_to_seconds_non_integer_fps():
    """Verify valid non-integer frame rates calculate index / fps accurately."""
    fps = 23.976
    expected_sec = 24.0 / 23.976
    actual_sec = frame_to_seconds(24, fps)
    assert pytest.approx(actual_sec, rel=1e-9) == expected_sec
    assert pytest.approx(frame_to_seconds(0, fps), abs=1e-9) == 0.0

    fps_ntsc = 29.97
    assert pytest.approx(frame_to_seconds(30, fps_ntsc), rel=1e-9) == (30.0 / 29.97)


# ==============================================================================
# 4. seconds_to_frame for round, floor, and ceil
# ==============================================================================

def test_seconds_to_frame_policies():
    """Verify round, floor, and ceil rounding policies on fractional frame timestamps."""
    fps = 24.0
    # 0.5 frames at 24 fps is 0.5 / 24 = 0.020833333333333332
    # 0.48 frames: 0.02s * 24 = 0.48
    assert seconds_to_frame(0.02, fps, "round") == 0
    assert seconds_to_frame(0.02, fps, "floor") == 0
    assert seconds_to_frame(0.02, fps, "ceil") == 1

    # 0.72 frames: 0.03s * 24 = 0.72
    assert seconds_to_frame(0.03, fps, "round") == 1
    assert seconds_to_frame(0.03, fps, "floor") == 0
    assert seconds_to_frame(0.03, fps, "ceil") == 1

    # Exact frame boundaries: 1.0s * 24 = 24.0
    assert seconds_to_frame(1.0, fps, "round") == 24
    assert seconds_to_frame(1.0, fps, "floor") == 24
    assert seconds_to_frame(1.0, fps, "ceil") == 24


def test_seconds_to_frame_ties_to_even():
    """Verify that 'round' strictly follows Python ties-to-even (banker's rounding)."""
    # At fps = 2.0:
    # 0.25s * 2.0 = 0.5  -> round ties to 0 (even)
    # 0.75s * 2.0 = 1.5  -> round ties to 2 (even)
    # 1.25s * 2.0 = 2.5  -> round ties to 2 (even)
    # 1.75s * 2.0 = 3.5  -> round ties to 4 (even)
    fps = 2.0
    assert seconds_to_frame(0.25, fps, "round") == 0
    assert seconds_to_frame(0.75, fps, "round") == 2
    assert seconds_to_frame(1.25, fps, "round") == 2
    assert seconds_to_frame(1.75, fps, "round") == 4


# ==============================================================================
# 5. seconds_to_sample for round, floor, and ceil
# ==============================================================================

def test_seconds_to_sample_policies():
    """Verify round, floor, and ceil policies for audio sample conversion."""
    sample_rate = 44100
    # Exact integer sample offsets
    assert seconds_to_sample(0.0, sample_rate, "round") == 0
    assert seconds_to_sample(1.0, sample_rate, "round") == 44100
    assert seconds_to_sample(0.5, sample_rate, "floor") == 22050
    assert seconds_to_sample(0.5, sample_rate, "ceil") == 22050

    # Test fractional sample behavior with sample_rate = 10
    sr = 10
    # 0.04s * 10 = 0.4 samples
    assert seconds_to_sample(0.04, sr, "round") == 0
    assert seconds_to_sample(0.04, sr, "floor") == 0
    assert seconds_to_sample(0.04, sr, "ceil") == 1

    # 0.07s * 10 = 0.7 samples
    assert seconds_to_sample(0.07, sr, "round") == 1
    assert seconds_to_sample(0.07, sr, "floor") == 0
    assert seconds_to_sample(0.07, sr, "ceil") == 1


def test_seconds_to_sample_ties_to_even():
    """Verify ties-to-even behavior for seconds_to_sample."""
    sr = 4  # 0.125 * 4 = 0.5; 0.375 * 4 = 1.5; 0.625 * 4 = 2.5
    assert seconds_to_sample(0.125, sr, "round") == 0
    assert seconds_to_sample(0.375, sr, "round") == 2
    assert seconds_to_sample(0.625, sr, "round") == 2


# ==============================================================================
# 6. Frame intervals and half-open boundaries
# ==============================================================================

def test_frame_interval_to_seconds_cardinal():
    """Verify [start_frame, end_frame) maps accurately to [start_sec, end_sec)."""
    # 24 FPS: [24, 48) -> [1.0, 2.0)
    start_sec, end_sec = frame_interval_to_seconds(24, 48, 24.0)
    assert start_sec == 1.0
    assert end_sec == 2.0

    # [0, 24) -> [0.0, 1.0)
    start_sec, end_sec = frame_interval_to_seconds(0, 24, 24.0)
    assert start_sec == 0.0
    assert end_sec == 1.0

    # Detection covering frames 10..14 inclusive must be represented as [10, 15)
    start_sec, end_sec = frame_interval_to_seconds(10, 15, 24.0)
    assert pytest.approx(start_sec, rel=1e-9) == (10.0 / 24.0)
    assert pytest.approx(end_sec, rel=1e-9) == (15.0 / 24.0)
    assert pytest.approx(end_sec - start_sec, rel=1e-9) == (5.0 / 24.0)


# ==============================================================================
# 7. Conversion round-trips
# ==============================================================================

def test_frame_roundtrip_constant_fps():
    """Verify that frame_to_seconds and seconds_to_frame round-trip cleanly."""
    fps_list = [24.0, 25.0, 30.0, 60.0, 23.976]
    for fps in fps_list:
        for frame_idx in [0, 1, 2, 23, 24, 25, 100, 500, 1440]:
            sec = frame_to_seconds(frame_idx, fps)
            recovered = seconds_to_frame(sec, fps, "round")
            assert recovered == frame_idx, f"Failed roundtrip at fps={fps}, frame={frame_idx}"


def test_sample_roundtrip():
    """Verify that sample_to_seconds and seconds_to_sample round-trip cleanly."""
    for sr in [44100, 48000]:
        for sample_idx in [0, 1, 100, 22050, 44100, 48000, 96000, 441000]:
            sec = sample_to_seconds(sample_idx, sr)
            recovered = seconds_to_sample(sec, sr, "round")
            assert recovered == sample_idx, f"Failed roundtrip at sr={sr}, sample={sample_idx}"


# ==============================================================================
# 8. Negative indices and timestamps
# ==============================================================================

def test_negative_inputs_rejected():
    """Verify negative indices, timestamps, and interval endpoints raise ValueError."""
    with pytest.raises(ValueError):
        frame_to_seconds(-1, 24.0)

    with pytest.raises(ValueError):
        seconds_to_frame(-0.1, 24.0)

    with pytest.raises(ValueError):
        sample_to_seconds(-1, 44100)

    with pytest.raises(ValueError):
        seconds_to_sample(-0.001, 44100)

    with pytest.raises(ValueError):
        frame_interval_to_seconds(-1, 10, 24.0)


# ==============================================================================
# 9. Zero and negative rates
# ==============================================================================

def test_zero_and_negative_rates_rejected():
    """Verify non-positive rates raise ValueError."""
    # Zero rates
    with pytest.raises(ValueError):
        frame_to_seconds(10, 0.0)

    with pytest.raises(ValueError):
        seconds_to_frame(1.0, 0.0)

    with pytest.raises(ValueError):
        sample_to_seconds(100, 0)

    with pytest.raises(ValueError):
        seconds_to_sample(1.0, 0)

    with pytest.raises(ValueError):
        frame_interval_to_seconds(0, 10, 0.0)

    # Negative rates
    with pytest.raises(ValueError):
        frame_to_seconds(10, -24.0)

    with pytest.raises(ValueError):
        seconds_to_frame(1.0, -24.0)

    with pytest.raises(ValueError):
        sample_to_seconds(100, -44100)

    with pytest.raises(ValueError):
        seconds_to_sample(1.0, -44100)

    with pytest.raises(ValueError):
        frame_interval_to_seconds(0, 10, -24.0)


# ==============================================================================
# 10. NaN and positive/negative infinity
# ==============================================================================

def test_nan_and_infinity_rejected():
    """Verify NaN and infinity values are strictly rejected."""
    nan = float("nan")
    inf = float("inf")
    ninf = float("-inf")

    # For fps
    for bad_fps in (nan, inf, ninf):
        with pytest.raises(ValueError):
            frame_to_seconds(10, bad_fps)
        with pytest.raises(ValueError):
            seconds_to_frame(1.0, bad_fps)
        with pytest.raises(ValueError):
            frame_interval_to_seconds(0, 10, bad_fps)

    # For seconds
    for bad_sec in (nan, inf, ninf):
        with pytest.raises(ValueError):
            seconds_to_frame(bad_sec, 24.0)
        with pytest.raises(ValueError):
            seconds_to_sample(bad_sec, 44100)


# ==============================================================================
# 11. Invalid types (booleans, strings, floats where ints required)
# ==============================================================================

def test_invalid_types_rejected():
    """Verify invalid types (especially booleans as ints) are rejected."""
    # frame_to_seconds rejects booleans and non-ints
    with pytest.raises((TypeError, ValueError)):
        frame_to_seconds(True, 24.0)
    with pytest.raises((TypeError, ValueError)):
        frame_to_seconds(False, 24.0)
    with pytest.raises((TypeError, ValueError)):
        frame_to_seconds("24", 24.0)
    with pytest.raises((TypeError, ValueError)):
        frame_to_seconds(24.5, 24.0)
    with pytest.raises((TypeError, ValueError)):
        frame_to_seconds(24, "24.0")
    with pytest.raises((TypeError, ValueError)):
        frame_to_seconds(24, True)

    # seconds_to_frame rejects booleans and non-numeric
    with pytest.raises((TypeError, ValueError)):
        seconds_to_frame(True, 24.0)
    with pytest.raises((TypeError, ValueError)):
        seconds_to_frame("1.0", 24.0)
    with pytest.raises((TypeError, ValueError)):
        seconds_to_frame(1.0, "24.0")

    # sample_to_seconds rejects booleans and non-ints
    with pytest.raises((TypeError, ValueError)):
        sample_to_seconds(True, 44100)
    with pytest.raises((TypeError, ValueError)):
        sample_to_seconds(100, True)
    with pytest.raises((TypeError, ValueError)):
        sample_to_seconds(100.5, 44100)
    with pytest.raises((TypeError, ValueError)):
        sample_to_seconds(100, 44100.0)  # sample_rate must be an int

    # seconds_to_sample rejects booleans and non-int sample_rate
    with pytest.raises((TypeError, ValueError)):
        seconds_to_sample(True, 44100)
    with pytest.raises((TypeError, ValueError)):
        seconds_to_sample(1.0, True)
    with pytest.raises((TypeError, ValueError)):
        seconds_to_sample(1.0, 44100.0)

    # frame_interval_to_seconds rejects booleans
    with pytest.raises((TypeError, ValueError)):
        frame_interval_to_seconds(True, 24, 24.0)
    with pytest.raises((TypeError, ValueError)):
        frame_interval_to_seconds(0, True, 24.0)
    with pytest.raises((TypeError, ValueError)):
        frame_interval_to_seconds(0, False, 24.0)


# ==============================================================================
# 12. Invalid rounding policies
# ==============================================================================

def test_invalid_rounding_policies_rejected():
    """Verify unsupported rounding policies raise ValueError."""
    for bad_policy in ("truncate", "nearest", "banker", "", "ROUND", "Floor"):
        with pytest.raises(ValueError):
            seconds_to_frame(1.0, 24.0, rounding=bad_policy)
        with pytest.raises(ValueError):
            seconds_to_sample(1.0, 44100, rounding=bad_policy)

    # Non-string rounding policy
    with pytest.raises((TypeError, ValueError)):
        seconds_to_frame(1.0, 24.0, rounding=123)
    with pytest.raises((TypeError, ValueError)):
        seconds_to_sample(1.0, 44100, rounding=None)


# ==============================================================================
# 13. Empty or reversed frame intervals
# ==============================================================================

def test_empty_or_reversed_frame_intervals_rejected():
    """Verify start_frame >= end_frame raises ValueError."""
    # Empty interval: start_frame == end_frame
    with pytest.raises(ValueError):
        frame_interval_to_seconds(10, 10, 24.0)
    with pytest.raises(ValueError):
        frame_interval_to_seconds(0, 0, 24.0)

    # Reversed interval: start_frame > end_frame
    with pytest.raises(ValueError):
        frame_interval_to_seconds(20, 10, 24.0)
    with pytest.raises(ValueError):
        frame_interval_to_seconds(1, 0, 24.0)


# ==============================================================================
# 14. Boundary cases close to frame and sample transitions
# ==============================================================================

def test_boundary_cases_near_transitions():
    """Verify boundary behavior slightly below, exactly at, and slightly above frame transitions."""
    fps = 24.0
    epsilon = 1e-9

    # Frame 24 is at 1.0s exactly
    assert seconds_to_frame(1.0, fps, "floor") == 24
    assert seconds_to_frame(1.0, fps, "ceil") == 24
    assert seconds_to_frame(1.0, fps, "round") == 24

    # Slightly below 1.0s (23.999999976 frames)
    t_below = 1.0 - epsilon
    assert seconds_to_frame(t_below, fps, "floor") == 23
    assert seconds_to_frame(t_below, fps, "ceil") == 24
    assert seconds_to_frame(t_below, fps, "round") == 24

    # Slightly above 1.0s (24.000000024 frames)
    t_above = 1.0 + epsilon
    assert seconds_to_frame(t_above, fps, "floor") == 24
    assert seconds_to_frame(t_above, fps, "ceil") == 25
    assert seconds_to_frame(t_above, fps, "round") == 24


# ==============================================================================
# 15. Ingestion Pipeline Snapshot Sampling Integration
# ==============================================================================

def test_timeline_integration_snapshot_sampling():
    """Verify frame_to_seconds produces accurate timeline points for snapshot sampling rates."""
    # Standard 1.0 FPS snapshot sampling
    assert frame_to_seconds(0, 1.0) == 0.0
    assert frame_to_seconds(1, 1.0) == 1.0
    assert frame_to_seconds(2, 1.0) == 2.0

    # Sub-second 0.5 FPS snapshot sampling (one snapshot every 2 seconds)
    assert frame_to_seconds(0, 0.5) == 0.0
    assert frame_to_seconds(1, 0.5) == 2.0
    assert frame_to_seconds(2, 0.5) == 4.0
