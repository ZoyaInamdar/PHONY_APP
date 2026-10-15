#!/usr/bin/env python3
"""Module for parsing and classifying SRT subtitle files for Trigger-Aware Player pipeline.

Uses `pysrt` to read and validate .srt files, classifying each subtitle cue as
either 'sound_description' or 'speech' by setting a `kind` attribute on each cue.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pysrt


class SubtitleParseError(Exception):
    """Raised when an SRT file cannot be parsed or contains invalid cue structures."""
    pass


def strip_formatting_tags(text: str) -> str:
    """Strip basic HTML formatting tags (e.g., <i>, </i>, <b>, </b>, <font ...>) from cue text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def classify_cue_text(text: str) -> str:
    """Classify cue text as 'sound_description' or 'speech'.

    Logic:
    - If the clean text is enclosed in brackets '[...]' or parentheses '(...)'
      (or composed entirely of bracketed/parenthesized descriptors), it is
      classified as 'sound_description'.
    - If it contains spoken dialogue or un-enclosed text, it is classified as 'speech'.

    Examples:
        '[gunshots]' -> 'sound_description'
        '(sighs)' -> 'sound_description'
        '[Music playing]' -> 'sound_description'
        '[applause] [cheering]' -> 'sound_description'
        'Look out! [gunshot]' -> 'speech'
        'Hello world' -> 'speech'
    """
    clean_text = strip_formatting_tags(text).strip()
    if not clean_text:
        return "speech"

    # Pattern matching single or multiple consecutive bracketed/parenthesized blocks
    # e.g., "[music]", "(sighs)", "[screams] (groans)"
    sound_desc_pattern = r"^(\s*(\[[^\]]*\]|\([^\)]*\))\s*)+$"

    if re.fullmatch(sound_desc_pattern, clean_text, flags=re.DOTALL):
        return "sound_description"

    return "speech"


def classify_cue(cue: pysrt.SubRipItem) -> pysrt.SubRipItem:
    """Process a single SubRipItem cue and attach the `kind` attribute."""
    cue.kind = classify_cue_text(cue.text or "")
    return cue


def subrip_time_to_seconds(srt_time: pysrt.SubRipTime) -> float:
    """Convert a pysrt SubRipTime object to float seconds."""
    return srt_time.ordinal / 1000.0


def parse_srt_file(
    file_path: Union[str, Path],
    encoding: Optional[str] = None,
) -> pysrt.SubRipFile:
    """Parse an .srt file using pysrt and classify each cue with a `kind` attribute.

    Args:
        file_path: Path to the .srt file.
        encoding: Text encoding (e.g. 'utf-8', 'latin-1'). If None, tries utf-8 with fallback.

    Returns:
        pysrt.SubRipFile containing SubRipItem objects, each with a .kind attribute.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If file path is invalid or file is not an .srt file.
        SubtitleParseError: If the file is malformed, corrupt, or contains invalid cues.
    """
    path = Path(file_path)

    # 1. Check path validity and existence
    if not path.exists():
        raise FileNotFoundError(f"Subtitle file not found: {path.resolve()}")

    if not path.is_file():
        raise ValueError(f"Path is not a regular file: {path.resolve()}")

    if path.suffix.lower() != ".srt":
        raise ValueError(
            f"Expected a file with '.srt' extension, got '{path.suffix}' for {path.name}"
        )

    # 2. Check for empty file
    if path.stat().st_size == 0:
        # Return an empty SubRipFile
        return pysrt.SubRipFile()

    # 3. Read and parse using pysrt with robust encoding handling
    sub_file: Optional[pysrt.SubRipFile] = None
    encodings_to_try = [encoding] if encoding else ["utf-8-sig", "utf-8", "latin-1", "cp1252"]

    last_error: Optional[Exception] = None
    for enc in encodings_to_try:
        if not enc:
            continue
        try:
            sub_file = pysrt.open(str(path), encoding=enc)
            # If successfully opened, verify by accessing cues
            _ = len(sub_file)
            break
        except (UnicodeDecodeError, LookupError) as e:
            last_error = e
            continue
        except Exception as e:
            # pysrt or I/O error
            raise SubtitleParseError(f"Failed to parse SRT file {path.name}: {e}") from e

    if sub_file is None:
        raise SubtitleParseError(
            f"Could not decode SRT file {path.name} with tested encodings: {last_error}"
        )

    # 4. Validate and classify each cue
    for i, cue in enumerate(sub_file):
        if not isinstance(cue, pysrt.SubRipItem):
            raise SubtitleParseError(f"Cue index {i} in {path.name} is not a valid SubRipItem.")

        # Validate start and end times
        if cue.start is None or cue.end is None:
            raise SubtitleParseError(
                f"Malformed cue index {getattr(cue, 'index', i + 1)} in {path.name}: missing start or end timestamp."
            )

        start_s = subrip_time_to_seconds(cue.start)
        end_s = subrip_time_to_seconds(cue.end)

        if end_s < start_s:
            raise SubtitleParseError(
                f"Invalid cue interval at index {getattr(cue, 'index', i + 1)} in {path.name}: "
                f"end ({end_s:.3f}s) is before start ({start_s:.3f}s)."
            )

        # Attach kind attribute
        classify_cue(cue)

    return sub_file


def cues_to_dicts(sub_file: pysrt.SubRipFile) -> List[Dict[str, Any]]:
    """Convert a classified pysrt.SubRipFile into a list of cue dictionaries.

    Suitable for export to JSON (e.g. cues.json).
    """
    results: List[Dict[str, Any]] = []
    for i, cue in enumerate(sub_file):
        cue_id = f"c{getattr(cue, 'index', i + 1)}"
        results.append({
            "id": cue_id,
            "start": round(subrip_time_to_seconds(cue.start), 3),
            "end": round(subrip_time_to_seconds(cue.end), 3),
            "kind": getattr(cue, "kind", "speech"),
            "text": cue.text,
        })
    return results


def main() -> None:
    """CLI interface for parsing and classifying SRT subtitle files."""
    parser = argparse.ArgumentParser(
        description="Parse and classify subtitle cues in an .srt file using pysrt."
    )
    parser.add_argument("srt_file", type=str, help="Path to the .srt file to process.")
    parser.add_argument(
        "--encoding",
        type=str,
        default=None,
        help="Optional text encoding (default: auto-detect utf-8 / latin-1).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output parsed cues as formatted JSON.",
    )

    args = parser.parse_args()

    try:
        sub_file = parse_srt_file(args.srt_file, encoding=args.encoding)
        if args.json:
            import json
            print(json.dumps(cues_to_dicts(sub_file), indent=2))
        else:
            sound_count = sum(1 for c in sub_file if getattr(c, "kind", None) == "sound_description")
            speech_count = len(sub_file) - sound_count
            print(f"Processed: {args.srt_file}")
            print(f"Total Cues: {len(sub_file)} (Speech: {speech_count}, Sound Descriptions: {sound_count})")
            for cue in sub_file[:5]:
                print(f"  [{cue.kind.upper()}] {cue.start} --> {cue.end}: {cue.text.replace(os.linesep, ' ')}")
            if len(sub_file) > 5:
                print(f"  ... and {len(sub_file) - 5} more cues.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
