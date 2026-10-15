#!/usr/bin/env python3
"""Shared time-conversion helper for Trigger-Aware Player (PHONY_APP).

Provides conversion functions between media timeline units (video frame indices,
audio sample indices) and timestamps in seconds.

Core Rules & Contracts:
- All frame and sample indices are zero-based integers.
- All timestamps returned are decimal float seconds on the normalized,
  zero-based media timeline.
- Frame intervals strictly follow half-open semantics: [start_frame, end_frame),
  where start_frame is inclusive and end_frame is exclusive.
- Conversions use direct division (index / rate) rather than multiplication
  by a reciprocal to preserve precision.
- Constant-rate media contract: This helper is designed for known-rate
  normalized media (e.g., constant 24 FPS video or fixed 44,100 Hz audio).
  For variable-frame-rate (VFR) media, frame index divided by FPS is not a
  substitute for container presentation timestamps (PTS); VFR media must be
  normalized prior to timeline indexing.
- Rounding policies for reverse conversion:
    * "round": Python built-in round() ties-to-even (banker's rounding).
    * "floor": math.floor() towards negative infinity.
    * "ceil":  math.ceil() towards positive infinity.
"""

from __future__ import annotations

import math
from typing import Tuple

SUPPORTED_ROUNDING_POLICIES = ("round", "floor", "ceil")


class TimelineError(ValueError):
    """Base exception for timeline conversion and validation errors."""
    pass


class TimelineTypeError(TimelineError, TypeError):
    """Raised when an argument has an invalid type.

    Inherits from both TypeError and ValueError so callers catching either
    will handle it appropriately.
    """
    pass


class TimelineValueError(TimelineError):
    """Raised when an argument has an invalid value."""
    pass


def frame_to_seconds(frame_index: int, fps: float) -> float:
    """Convert a zero-based video frame index to seconds on a constant-rate timeline.

    Parameters:
        frame_index: Nonnegative zero-based integer frame index.
        fps: Strictly positive, finite frames-per-second (e.g. 24.0 or 23.976).

    Returns:
        Timestamp in seconds as a float: frame_index / fps.

    Raises:
        TimelineTypeError (TypeError, ValueError): If frame_index is not an int
            (or is a bool) or fps is not numeric.
        TimelineValueError (ValueError): If frame_index < 0, fps <= 0, or fps
            is not finite (NaN/inf).
    """
    if isinstance(frame_index, bool) or not isinstance(frame_index, int):
        raise TimelineTypeError(
            f"frame_index must be an integer, got {type(frame_index).__name__} ({frame_index!r})"
        )
    if frame_index < 0:
        raise TimelineValueError(f"frame_index must be non-negative, got {frame_index}")

    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        raise TimelineTypeError(
            f"fps must be a numeric float or int, got {type(fps).__name__} ({fps!r})"
        )
    fps_f = float(fps)
    if not math.isfinite(fps_f):
        raise TimelineValueError(f"fps must be finite, got {fps_f}")
    if fps_f <= 0.0:
        raise TimelineValueError(f"fps must be strictly positive, got {fps_f}")

    return float(frame_index) / fps_f


def seconds_to_frame(seconds: float, fps: float, rounding: str = "round") -> int:
    """Convert seconds timestamp to a video frame index using the specified rounding policy.

    Parameters:
        seconds: Finite, non-negative seconds timestamp.
        fps: Strictly positive, finite frames-per-second.
        rounding: Rounding policy to apply:
            - 'round': Nearest integer with round-half-to-even (Python built-in round).
            - 'floor': math.floor (round towards negative infinity).
            - 'ceil':  math.ceil (round towards positive infinity).

    Returns:
        Zero-based frame index as an integer.

    Raises:
        TimelineTypeError (TypeError, ValueError): If seconds or fps is not
            numeric or is a bool, or if rounding is not a string.
        TimelineValueError (ValueError): If seconds < 0, seconds is non-finite,
            fps <= 0, fps is non-finite, or rounding policy is unsupported.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise TimelineTypeError(
            f"seconds must be a numeric float or int, got {type(seconds).__name__} ({seconds!r})"
        )
    seconds_f = float(seconds)
    if not math.isfinite(seconds_f):
        raise TimelineValueError(f"seconds must be finite, got {seconds_f}")
    if seconds_f < 0.0:
        raise TimelineValueError(f"seconds must be non-negative, got {seconds_f}")

    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        raise TimelineTypeError(
            f"fps must be a numeric float or int, got {type(fps).__name__} ({fps!r})"
        )
    fps_f = float(fps)
    if not math.isfinite(fps_f):
        raise TimelineValueError(f"fps must be finite, got {fps_f}")
    if fps_f <= 0.0:
        raise TimelineValueError(f"fps must be strictly positive, got {fps_f}")

    if not isinstance(rounding, str):
        raise TimelineTypeError(
            f"rounding must be a string, got {type(rounding).__name__} ({rounding!r})"
        )
    if rounding not in SUPPORTED_ROUNDING_POLICIES:
        raise TimelineValueError(
            f"Unsupported rounding policy: {rounding!r}. Must be one of {SUPPORTED_ROUNDING_POLICIES}"
        )

    raw = seconds_f * fps_f
    if rounding == "round":
        return int(round(raw))
    elif rounding == "floor":
        return math.floor(raw)
    else:  # "ceil"
        return math.ceil(raw)


def sample_to_seconds(sample_index: int, sample_rate: int) -> float:
    """Convert a zero-based audio sample index to seconds on a fixed sample rate timeline.

    Parameters:
        sample_index: Nonnegative zero-based integer audio sample index.
        sample_rate: Strictly positive integer sample rate (e.g. 44100 or 48000).

    Returns:
        Timestamp in seconds as a float: sample_index / sample_rate.

    Raises:
        TimelineTypeError (TypeError, ValueError): If sample_index or sample_rate
            is not an int, or is a bool.
        TimelineValueError (ValueError): If sample_index < 0 or sample_rate <= 0.
    """
    if isinstance(sample_index, bool) or not isinstance(sample_index, int):
        raise TimelineTypeError(
            f"sample_index must be an integer, got {type(sample_index).__name__} ({sample_index!r})"
        )
    if sample_index < 0:
        raise TimelineValueError(f"sample_index must be non-negative, got {sample_index}")

    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
        raise TimelineTypeError(
            f"sample_rate must be an integer, got {type(sample_rate).__name__} ({sample_rate!r})"
        )
    if sample_rate <= 0:
        raise TimelineValueError(f"sample_rate must be strictly positive, got {sample_rate}")

    return float(sample_index) / float(sample_rate)


def seconds_to_sample(seconds: float, sample_rate: int, rounding: str = "round") -> int:
    """Convert seconds timestamp to an audio sample index using the specified rounding policy.

    Parameters:
        seconds: Finite, non-negative seconds timestamp.
        sample_rate: Strictly positive integer sample rate (e.g. 44100).
        rounding: Rounding policy to apply:
            - 'round': Nearest integer with round-half-to-even (Python built-in round).
            - 'floor': math.floor (round towards negative infinity).
            - 'ceil':  math.ceil (round towards positive infinity).

    Returns:
        Zero-based audio sample index as an integer.

    Raises:
        TimelineTypeError (TypeError, ValueError): If seconds is not numeric or is a bool,
            sample_rate is not an int or is a bool, or rounding is not a string.
        TimelineValueError (ValueError): If seconds < 0, seconds is non-finite,
            sample_rate <= 0, or rounding policy is unsupported.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise TimelineTypeError(
            f"seconds must be a numeric float or int, got {type(seconds).__name__} ({seconds!r})"
        )
    seconds_f = float(seconds)
    if not math.isfinite(seconds_f):
        raise TimelineValueError(f"seconds must be finite, got {seconds_f}")
    if seconds_f < 0.0:
        raise TimelineValueError(f"seconds must be non-negative, got {seconds_f}")

    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
        raise TimelineTypeError(
            f"sample_rate must be an integer, got {type(sample_rate).__name__} ({sample_rate!r})"
        )
    if sample_rate <= 0:
        raise TimelineValueError(f"sample_rate must be strictly positive, got {sample_rate}")

    if not isinstance(rounding, str):
        raise TimelineTypeError(
            f"rounding must be a string, got {type(rounding).__name__} ({rounding!r})"
        )
    if rounding not in SUPPORTED_ROUNDING_POLICIES:
        raise TimelineValueError(
            f"Unsupported rounding policy: {rounding!r}. Must be one of {SUPPORTED_ROUNDING_POLICIES}"
        )

    raw = seconds_f * float(sample_rate)
    if rounding == "round":
        return int(round(raw))
    elif rounding == "floor":
        return math.floor(raw)
    else:  # "ceil"
        return math.ceil(raw)


def frame_interval_to_seconds(
    start_frame: int,
    end_frame: int,
    fps: float,
) -> Tuple[float, float]:
    """Convert a half-open frame index interval [start_frame, end_frame) to seconds [start, end).

    The interval is strictly half-open: start_frame is inclusive, end_frame is exclusive.
    The caller supplies the exclusive end index directly; this helper does not add
    hidden offsets. For example, a detection covering frames 10..14 inclusive must be
    passed as [10, 15). At 24 FPS, frame interval [24, 48) maps to [1.0, 2.0) seconds.

    Parameters:
        start_frame: Nonnegative integer, inclusive start frame index.
        end_frame: Strictly greater integer, exclusive end frame index.
        fps: Strictly positive, finite frames-per-second.

    Returns:
        A tuple of (start_seconds, end_seconds) as floats.

    Raises:
        TimelineTypeError (TypeError, ValueError): If start_frame or end_frame is
            not an int (or is a bool), or fps is not numeric.
        TimelineValueError (ValueError): If start_frame < 0, end_frame <= start_frame,
            or fps <= 0 / non-finite.
    """
    if isinstance(start_frame, bool) or not isinstance(start_frame, int):
        raise TimelineTypeError(
            f"start_frame must be an integer, got {type(start_frame).__name__} ({start_frame!r})"
        )
    if start_frame < 0:
        raise TimelineValueError(f"start_frame must be non-negative, got {start_frame}")

    if isinstance(end_frame, bool) or not isinstance(end_frame, int):
        raise TimelineTypeError(
            f"end_frame must be an integer, got {type(end_frame).__name__} ({end_frame!r})"
        )
    if end_frame <= start_frame:
        raise TimelineValueError(
            f"end_frame ({end_frame}) must be strictly greater than start_frame ({start_frame}) "
            "for half-open interval [start_frame, end_frame)"
        )

    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        raise TimelineTypeError(
            f"fps must be a numeric float or int, got {type(fps).__name__} ({fps!r})"
        )
    fps_f = float(fps)
    if not math.isfinite(fps_f):
        raise TimelineValueError(f"fps must be finite, got {fps_f}")
    if fps_f <= 0.0:
        raise TimelineValueError(f"fps must be strictly positive, got {fps_f}")

    start_sec = float(start_frame) / fps_f
    end_sec = float(end_frame) / fps_f
    return (start_sec, end_sec)


__all__ = [
    "frame_to_seconds",
    "seconds_to_frame",
    "sample_to_seconds",
    "seconds_to_sample",
    "frame_interval_to_seconds",
    "TimelineError",
    "TimelineTypeError",
    "TimelineValueError",
    "SUPPORTED_ROUNDING_POLICIES",
]
