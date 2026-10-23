#!/usr/bin/env python3
"""Module for parsing, classifying, and exporting SRT subtitle cues for Trigger-Aware Player pipeline.

Uses `pysrt` to read and validate .srt files, classifies each subtitle cue as
either 'sound_description' or 'speech', matches profanity words against speech
cues, and exports results to JSON according to shared pipeline contracts:
- cues.json: Array of every cue with id, start, end, kind, text, and flagged_words.
- detections/subtitles.json: Flagged intervals matching shared detector contract with provenance.

Also provides optional offline speech-to-text (STT) fallback integration when
subtitles are missing, empty, or unreadable.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import pysrt

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.stt_fallback import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_STT_BACKEND,
    DEFAULT_STT_MODEL_SIZE,
    DEFAULT_WORDLIST_PATH,
    STTError,
    STTModelNotFoundError,
    STTTranscriptionError,
    STTUnavailableError,
    detect_profanity_with_fallback,
    match_profanity_in_transcript,
    transcribe_audio_faster_whisper,
)


class SubtitleParseError(Exception):
    """Raised when an SRT file cannot be parsed or contains invalid cue structures."""
    pass


def get_pysrt_version() -> str:
    """Return the installed pysrt version string."""
    try:
        return importlib.metadata.version("pysrt")
    except Exception:
        return getattr(pysrt, "__version__", "unknown")


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
    sound_desc_pattern = r"^(\s*(\[[^\]]*\]|\([^\)]*\))\s*)+$"

    if re.fullmatch(sound_desc_pattern, clean_text, flags=re.DOTALL):
        return "sound_description"

    return "speech"


def classify_cue(cue: pysrt.SubRipItem) -> pysrt.SubRipItem:
    """Process a single SubRipItem cue and attach the `kind` attribute."""
    cue.kind = classify_cue_text(cue.text or "")
    if not hasattr(cue, "flagged"):
        cue.flagged = []
    return cue


def subrip_time_to_seconds(srt_time: pysrt.SubRipTime) -> float:
    """Convert a pysrt SubRipTime object to float seconds."""
    return srt_time.ordinal / 1000.0


def load_wordlist(file_path: Union[str, Path]) -> List[str]:
    """Load an external profanity word list file.

    Format:
    - Plain text file, one term per line.
    - Lines starting with '#' and blank lines are ignored as comments.
    - All terms are lowercased and deduplicated preserving order.

    Raises:
        FileNotFoundError: If the word list file does not exist.
        ValueError: If the file path is not a regular file.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Profanity word list file not found: {path.resolve()}")
    if not path.is_file():
        raise ValueError(f"Word list path is not a regular file: {path.resolve()}")

    words: List[str] = []
    seen = set()

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lower_word = stripped.lower()
            if lower_word not in seen:
                seen.add(lower_word)
                words.append(lower_word)

    return words


def match_profanity_in_cues(
    sub_file: pysrt.SubRipFile,
    word_list: List[str],
    source: str = "profanity_detector",
) -> List[Dict[str, Any]]:
    """Match profanity terms against speech cues in a parsed SRT file.

    Requirements:
    - Uses regex word boundaries (\\b) to ensure whole-word matching
      (e.g., matching 'ass' but not 'class', 'crap' but not 'scraper').
    - Case-insensitive matching, handling punctuation and uppercase/lowercase.
    - Matches only against cues classified as 'speech' (skips sound_description).
    - For every match, appends a flagged metadata object with:
        * word: matched string
        * char_start: start offset in text
        * char_end: end offset in text
        * evidence: 'subtitle_text'
        * timing: 'estimated'
        * spoken_confirmed: null
    - Produces shared detection records for downstream merging. If a cue contains
      a flagged word, start and end match the entire cue interval (estimated timing).

    Args:
        sub_file: Parsed SubRipFile whose cues already have .kind set.
        word_list: List of profanity strings to search for.
        source: Component identifier for raw detections (default: 'profanity_detector').

    Returns:
        List of raw detection records adhering to the shared detection contract.
    """
    if not word_list:
        return []

    # Compile word boundary matchers for each term
    matchers: List[Tuple[str, str, re.Pattern[str]]] = []
    for idx, word in enumerate(word_list):
        escaped = re.escape(word)
        pattern = re.compile(rf"\b{escaped}\b", flags=re.IGNORECASE)
        matchers.append((f"w{idx + 1}", word, pattern))

    raw_detections: List[Dict[str, Any]] = []

    for cue in sub_file:
        kind = getattr(cue, "kind", None)
        if kind is None:
            classify_cue(cue)
            kind = cue.kind

        # Profanity matching only applies to speech cues
        if kind != "speech":
            continue

        raw_text = cue.text or ""
        flagged_terms: List[Dict[str, Any]] = []

        start_s = subrip_time_to_seconds(cue.start)
        end_s = subrip_time_to_seconds(cue.end)
        cue_duration = max(end_s - start_s, 0.001)
        text_length = max(len(raw_text), 1)

        for term_id, term_str, pattern in matchers:
            for match in pattern.finditer(raw_text):
                char_start = match.start()
                char_end = match.end()
                matched_word = raw_text[char_start:char_end]

                # Linear interpolation for estimated word timestamp
                word_start = round(start_s + (char_start / text_length) * cue_duration, 3)
                word_end = round(start_s + (char_end / text_length) * cue_duration, 3)

                flagged_terms.append({
                    "term_id": term_id,
                    "word": matched_word,
                    "char_start": char_start,
                    "char_end": char_end,
                    "evidence": "subtitle_text",
                    "timing": "estimated",
                    "start": word_start,
                    "end": word_end,
                    "confidence": 1.0,
                    "spoken_confirmed": None,
                })

        if flagged_terms:
            # Sort flagged terms chronologically by text offset
            flagged_terms.sort(key=lambda t: t["char_start"])
            cue.flagged = flagged_terms

            # Per pipeline spec: with estimated timing, the detection bounds
            # cover the entire cue interval.
            raw_detections.append({
                "start": round(start_s, 3),
                "end": round(end_s, 3),
                "trigger": "profanity",
                "score": 1.0,
                "source": source,
            })

    return raw_detections


def parse_vtt_timestamp(ts_str: str) -> float:
    """Parse a WebVTT timestamp string (MM:SS.mmm or HH:MM:SS.mmm) to float seconds.

    Supports:
        '00:01.000' -> 1.0
        '01:02:03.456' -> 3723.456
        '00:00:01' -> 1.0
        '00:01.5' -> 1.5
        '00:01,500' -> 1.5

    Raises:
        SubtitleParseError: If the timestamp does not conform to WebVTT specification.
    """
    ts_clean = ts_str.strip().replace(",", ".")
    match = re.match(r"^(?:(\d{2,}):)?(\d{2}):(\d{2})(?:\.(\d{1,3}))?$", ts_clean)
    if not match:
        raise SubtitleParseError(f"Invalid WebVTT timestamp format: '{ts_str}'")

    hours_g, mins_g, secs_g, millis_g = match.groups()
    hours = int(hours_g) if hours_g is not None else 0
    mins = int(mins_g)
    secs = int(secs_g)
    millis_raw = millis_g or "0"
    millis = int(millis_raw.ljust(3, "0")[:3])

    if mins >= 60 or secs >= 60:
        raise SubtitleParseError(f"Invalid WebVTT timestamp units in '{ts_str}' (minutes/seconds >= 60)")

    total_seconds = hours * 3600.0 + mins * 60.0 + secs + (millis / 1000.0)
    if not math.isfinite(total_seconds) or total_seconds < 0.0:
        raise SubtitleParseError(f"Invalid or non-positive timestamp value in '{ts_str}'")

    return round(total_seconds, 3)


def parse_vtt_file(
    file_path: Union[str, Path],
    encoding: Optional[str] = None,
    wordlist_path: Optional[Union[str, Path]] = None,
    source: str = "profanity_detector",
    media_duration: Optional[float] = None,
) -> pysrt.SubRipFile:
    """Parse a WebVTT (.vtt) file, classify cues, and optionally match profanity.

    Handles:
    - 'WEBVTT' header and optional UTF-8 BOM.
    - Optional cue identifiers.
    - 'MM:SS.mmm' and 'HH:MM:SS.mmm' timestamps.
    - Cue settings after timestamp ranges (e.g. line:90% align:middle).
    - Multiline cue text.
    - UTF-8 text and inline formatting tags.
    - Non-cue blocks ('NOTE', 'STYLE', 'REGION') without treating content as dialogue.
    - Zero-cue valid VTT files.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If file is not a regular file or does not have .vtt extension.
        SubtitleParseError: If header is missing/malformed, timestamps are invalid,
                            intervals are zero-length/reversed, or timing exceeds media duration.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Subtitle file not found: {path.resolve()}")
    if not path.is_file():
        raise ValueError(f"Path is not a regular file: {path.resolve()}")
    if path.suffix.lower() != ".vtt":
        raise ValueError(
            f"Expected a file with '.vtt' extension, got '{path.suffix}' for {path.name}"
        )

    if path.stat().st_size == 0:
        return pysrt.SubRipFile()

    encodings_to_try = [encoding] if encoding else ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
    raw_text: Optional[str] = None
    last_error: Optional[Exception] = None

    for enc in encodings_to_try:
        if not enc:
            continue
        try:
            raw_text = path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError) as e:
            last_error = e
            continue
        except Exception as e:
            raise SubtitleParseError(f"Failed to read VTT file {path.name}: {e}") from e

    if raw_text is None:
        raise SubtitleParseError(
            f"Could not decode VTT file {path.name} with tested encodings: {last_error}"
        )

    # Strip optional UTF-8 BOM
    if raw_text.startswith("\ufeff"):
        raw_text = raw_text[1:]

    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    stripped = normalized.strip()
    if not stripped:
        return pysrt.SubRipFile()

    # Validate WEBVTT header on the very first line
    lines = normalized.split("\n")
    first_line = lines[0].strip()
    header_pattern = r"^WEBVTT([ \t\-\:].*)?$"
    if not re.match(header_pattern, first_line):
        raise SubtitleParseError(
            f"Missing or invalid WEBVTT header in {path.name}: first line was '{first_line[:50]}'"
        )

    # Split into discrete blocks by blank lines
    blocks = re.split(r"\n\s*\n+", normalized)
    items: List[pysrt.SubRipItem] = []

    # Process blocks after the header block (block 0 contains WEBVTT)
    for block_idx, block in enumerate(blocks[1:], start=1):
        block_lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not block_lines:
            continue

        first_bline = block_lines[0]
        # Skip recognized non-cue metadata blocks
        if (
            first_bline == "NOTE"
            or first_bline.startswith("NOTE ")
            or first_bline.startswith("NOTE\t")
        ):
            continue
        if (
            first_bline == "STYLE"
            or first_bline.startswith("STYLE ")
            or first_bline == "REGION"
            or first_bline.startswith("REGION ")
        ):
            continue

        # Find line with timestamp separator '-->'
        timing_line_idx = -1
        for idx, line in enumerate(block_lines):
            if "-->" in line:
                timing_line_idx = idx
                break

        if timing_line_idx == -1:
            raise SubtitleParseError(
                f"Malformed cue block at block {block_idx} in {path.name}: "
                f"missing '-->' timestamp separator in '{block_lines[0]}'."
            )

        cue_id = block_lines[0] if timing_line_idx > 0 else f"c{len(items) + 1}"
        timing_line = block_lines[timing_line_idx]
        payload_lines = block_lines[timing_line_idx + 1:]

        parts = timing_line.split("-->")
        if len(parts) != 2:
            raise SubtitleParseError(
                f"Invalid timestamp line at block {block_idx} in {path.name}: '{timing_line}'"
            )

        start_token = parts[0].strip()
        right_tokens = parts[1].strip().split()
        if not right_tokens:
            raise SubtitleParseError(
                f"Missing end timestamp at block {block_idx} in {path.name}: '{timing_line}'"
            )
        end_token = right_tokens[0]

        start_s = parse_vtt_timestamp(start_token)
        end_s = parse_vtt_timestamp(end_token)

        # Zero-length or reversed interval rejection
        if end_s <= start_s:
            raise SubtitleParseError(
                f"Invalid cue interval at block {block_idx} in {path.name}: "
                f"end ({end_s:.3f}s) <= start ({start_s:.3f}s)."
            )

        if media_duration is not None and start_s >= media_duration:
            raise SubtitleParseError(
                f"Cue start ({start_s:.3f}s) at block {block_idx} in {path.name} "
                f"exceeds media duration ({media_duration:.3f}s)."
            )

        cue_text = "\n".join(payload_lines)
        start_time = pysrt.SubRipTime.from_ordinal(int(round(start_s * 1000)))
        end_time = pysrt.SubRipTime.from_ordinal(int(round(end_s * 1000)))

        item = pysrt.SubRipItem(
            index=len(items) + 1,
            start=start_time,
            end=end_time,
            text=cue_text,
        )
        item.id = cue_id
        classify_cue(item)
        items.append(item)

    sub_file = pysrt.SubRipFile(items)

    if wordlist_path is not None:
        words = load_wordlist(wordlist_path)
        match_profanity_in_cues(sub_file, words, source=source)

    return sub_file


def parse_srt_file(
    file_path: Union[str, Path],
    encoding: Optional[str] = None,
    wordlist_path: Optional[Union[str, Path]] = None,
    source: str = "profanity_detector",
    media_duration: Optional[float] = None,
) -> pysrt.SubRipFile:
    """Parse an .srt file using pysrt, classify cues, and optionally match profanity.

    Args:
        file_path: Path to the .srt file.
        encoding: Text encoding. If None, auto-detects with fallback.
        wordlist_path: Optional path to external profanity word list.
        source: Stable component identifier for detections (default: 'profanity_detector').
        media_duration: Optional media timeline duration to validate cue bounds.

    Returns:
        pysrt.SubRipFile containing SubRipItem objects, each with a .kind attribute
        and a .flagged list if profanity is detected.
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
            _ = len(sub_file)
            break
        except (UnicodeDecodeError, LookupError) as e:
            last_error = e
            continue
        except Exception as e:
            raise SubtitleParseError(f"Failed to parse SRT file {path.name}: {e}") from e

    if sub_file is None:
        raise SubtitleParseError(
            f"Could not decode SRT file {path.name} with tested encodings: {last_error}"
        )

    # 4. Validate and classify each cue
    for i, cue in enumerate(sub_file):
        if not isinstance(cue, pysrt.SubRipItem):
            raise SubtitleParseError(f"Cue index {i} in {path.name} is not a valid SubRipItem.")

        if cue.start is None or cue.end is None:
            raise SubtitleParseError(
                f"Malformed cue index {getattr(cue, 'index', i + 1)} in {path.name}: missing timestamp."
            )

        start_s = subrip_time_to_seconds(cue.start)
        end_s = subrip_time_to_seconds(cue.end)

        if end_s <= start_s:
            raise SubtitleParseError(
                f"Invalid cue interval at index {getattr(cue, 'index', i + 1)} in {path.name}: "
                f"end ({end_s:.3f}s) is before or equal to start ({start_s:.3f}s)."
            )

        if media_duration is not None and start_s >= media_duration:
            raise SubtitleParseError(
                f"Cue start ({start_s:.3f}s) at index {getattr(cue, 'index', i + 1)} in {path.name} "
                f"exceeds media duration ({media_duration:.3f}s)."
            )

        classify_cue(cue)

    # 5. Optional profanity detection if wordlist_path is supplied
    if wordlist_path is not None:
        words = load_wordlist(wordlist_path)
        match_profanity_in_cues(sub_file, words, source=source)

    return sub_file


def parse_subtitle_file(
    file_path: Union[str, Path],
    encoding: Optional[str] = None,
    wordlist_path: Optional[Union[str, Path]] = None,
    source: str = "profanity_detector",
    media_duration: Optional[float] = None,
) -> pysrt.SubRipFile:
    """Parse a subtitle file (.srt or .vtt), classify cues, and optionally match profanity.

    Dispatches by validated file extension:
    - .srt -> parse_srt_file
    - .vtt -> parse_vtt_file

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is not a regular file or has an unsupported extension.
        SubtitleParseError: If the subtitle content is corrupt, invalid, or malformed.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Subtitle file not found: {path.resolve()}")
    if not path.is_file():
        raise ValueError(f"Path is not a regular file: {path.resolve()}")

    ext = path.suffix.lower()
    if ext == ".srt":
        return parse_srt_file(
            path,
            encoding=encoding,
            wordlist_path=wordlist_path,
            source=source,
            media_duration=media_duration,
        )
    elif ext == ".vtt":
        return parse_vtt_file(
            path,
            encoding=encoding,
            wordlist_path=wordlist_path,
            source=source,
            media_duration=media_duration,
        )
    else:
        raise ValueError(
            f"Expected a file with '.srt' or '.vtt' extension, got '{ext}' for {path.name}"
        )


def cues_to_dicts(sub_file: pysrt.SubRipFile) -> List[Dict[str, Any]]:
    """Convert a classified pysrt.SubRipFile into a list of cue dictionaries.

    Suitable for export to JSON (e.g. cues.json). Each item contains:
    - id: string cue identifier
    - start: float start time in seconds
    - end: float end time in seconds
    - kind: 'speech' or 'sound_description'
    - text: full subtitle text
    - flagged_words: array of flagged word metadata
    """
    results: List[Dict[str, Any]] = []
    for i, cue in enumerate(sub_file):
        cue_id = f"c{getattr(cue, 'index', i + 1)}"
        flagged_list = getattr(cue, "flagged", []) or []

        flagged_words: List[Dict[str, Any]] = []
        for f in flagged_list:
            flagged_words.append({
                "word": f.get("word", ""),
                "char_start": f["char_start"],
                "char_end": f["char_end"],
                "evidence": f.get("evidence", "subtitle_text"),
                "timing": f.get("timing", "estimated"),
                "spoken_confirmed": f.get("spoken_confirmed", None),
            })

        item_dict: Dict[str, Any] = {
            "id": cue_id,
            "start": round(subrip_time_to_seconds(cue.start), 3),
            "end": round(subrip_time_to_seconds(cue.end), 3),
            "kind": getattr(cue, "kind", "speech"),
            "text": cue.text,
            "flagged_words": flagged_words,
        }

        # Keep legacy alias 'flagged' if flagged_words is populated
        if flagged_words:
            item_dict["flagged"] = flagged_list

        results.append(item_dict)
    return results


def export_cues_json(
    sub_file: pysrt.SubRipFile,
    output_path: Union[str, Path],
    indent: int = 2,
) -> List[Dict[str, Any]]:
    """Export every subtitle cue in the film to cues.json.

    Args:
        sub_file: Classified SubRipFile with attached kind and flagged terms.
        output_path: Destination path for cues.json.
        indent: JSON indentation level (default: 2).

    Returns:
        List of cue dictionaries written to disk.
    """
    cues_data = cues_to_dicts(sub_file)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    with out_p.open("w", encoding="utf-8") as f:
        json.dump(cues_data, f, indent=indent)

    return cues_data


def generate_subtitles_provenance(
    source_file: Optional[str] = None,
    wordlist_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Generate provenance metadata block for the subtitles detection stage."""
    pysrt_version = get_pysrt_version()
    provenance: Dict[str, Any] = {
        "stage": "subtitles",
        "status": "ok",
        "source_file": str(source_file) if source_file else None,
        "wordlist_path": str(wordlist_path) if wordlist_path else None,
        "evidence_type": "subtitle_text",
        "timing_type": "estimated",
        "libs": {
            "pysrt": pysrt_version,
            "python": platform.python_version(),
        },
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    return provenance


def export_detections_json(
    sub_file: Union[pysrt.SubRipFile, Dict[str, Any]],
    output_path: Union[str, Path],
    source: str = "profanity_detector",
    source_file: Optional[str] = None,
    wordlist_path: Optional[Union[str, Path]] = None,
    indent: int = 2,
) -> Dict[str, Any]:
    """Export flagged intervals to detections/subtitles.json with a provenance block.

    Supports either a parsed `pysrt.SubRipFile` or a result payload dictionary from
    `detect_profanity_with_fallback`.

    Args:
        sub_file: Parsed SubRipFile with flagged speech cues, OR a result dictionary.
        output_path: Destination path for detections/subtitles.json.
        source: Stable component identifier (default: 'profanity_detector').
        source_file: Optional original SRT filename for provenance.
        wordlist_path: Optional wordlist path for provenance.
        indent: JSON indentation level (default: 2).

    Returns:
        Dictionary containing the provenance block and detections list.
    """
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    # Case 1: Result dictionary from detect_profanity_with_fallback
    if isinstance(sub_file, dict) and "provenance" in sub_file:
        payload = sub_file
        with out_p.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent)
        return payload

    # Case 2: SubRipFile
    detections: List[Dict[str, Any]] = []

    for cue in sub_file:
        flagged = getattr(cue, "flagged", [])
        if flagged:
            start_s = subrip_time_to_seconds(cue.start)
            end_s = subrip_time_to_seconds(cue.end)
            detections.append({
                "start": round(start_s, 3),
                "end": round(end_s, 3),
                "trigger": "profanity",
                "score": 1.0,
                "source": source,
            })

    provenance = generate_subtitles_provenance(
        source_file=source_file,
        wordlist_path=wordlist_path,
    )

    payload = {
        "provenance": provenance,
        "detections": detections,
    }

    with out_p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent)

    return payload


def main() -> None:
    """CLI interface for parsing subtitles, detecting profanity, and exporting JSON."""
    parser = argparse.ArgumentParser(
        description="Parse subtitles, detect profanity with optional STT fallback, and export JSON."
    )
    parser.add_argument("srt_file", type=str, nargs="?", default=None, help="Path to the .srt or .vtt file to process.")
    parser.add_argument(
        "--wordlist",
        type=str,
        default=None,
        help="Optional path to external profanity wordlist file.",
    )
    parser.add_argument(
        "--encoding",
        type=str,
        default=None,
        help="Optional text encoding (default: auto-detect utf-8 / latin-1).",
    )
    parser.add_argument(
        "--out-cues",
        type=str,
        default=None,
        help="Path to export cues.json (default: None).",
    )
    parser.add_argument(
        "--out-detections",
        type=str,
        default=None,
        help="Path to export detections/subtitles.json (default: None).",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="profanity_detector",
        help="Stable component identifier for detections (default: profanity_detector).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output parsed cues as formatted JSON to stdout.",
    )
    parser.add_argument(
        "--audio",
        type=str,
        default=None,
        help="Optional path to audio file for speech-to-text (STT) fallback.",
    )
    parser.add_argument(
        "--enable-stt",
        action="store_true",
        help="Explicitly enable speech-to-text fallback when subtitles are missing or unusable.",
    )
    parser.add_argument(
        "--stt-backend",
        type=str,
        default=None,
        help="STT backend to use (default: faster-whisper).",
    )
    parser.add_argument(
        "--stt-model",
        type=str,
        default=None,
        help="Local STT model size or path (default: base.en).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml.",
    )
    parser.add_argument(
        "--film-id",
        type=str,
        default=None,
        help="Film identifier for metadata.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Media presentation duration in seconds.",
    )

    args = parser.parse_args()

    if not args.srt_file and not args.audio:
        parser.error("Either srt_file or --audio must be specified.")

    # Route through detect_profanity_with_fallback if audio/stt options are provided or srt_file is omitted
    if args.audio or args.enable_stt or not args.srt_file:
        res = detect_profanity_with_fallback(
            srt_path=args.srt_file,
            audio_path=args.audio,
            wordlist_path=args.wordlist,
            config_path=args.config or DEFAULT_CONFIG_PATH,
            enable_stt=args.enable_stt if args.enable_stt else None,
            stt_backend=args.stt_backend,
            stt_model=args.stt_model,
            source=args.source,
            film_id=args.film_id,
            media_duration=args.duration,
        )

        if args.out_detections:
            export_detections_json(
                res,
                args.out_detections,
                source=args.source,
                source_file=args.srt_file,
                wordlist_path=args.wordlist,
            )
            print(f"Exported detections to: {args.out_detections} (status: {res['provenance']['status']})")

        if args.out_cues:
            if res.get("sub_file"):
                export_cues_json(res["sub_file"], args.out_cues)
                print(f"Exported cues.json to: {args.out_cues}")
            else:
                out_p = Path(args.out_cues)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with out_p.open("w", encoding="utf-8") as f:
                    json.dump(res.get("transcript_segments", []), f, indent=2)
                print(f"Exported transcript cues to: {args.out_cues}")

        if args.json:
            print(json.dumps(res, indent=2))
        elif not args.out_cues and not args.out_detections:
            status_val = res["provenance"]["status"]
            det_count = len(res["detections"])
            print(f"Profanity analysis completed: status={status_val}, detections={det_count}")
            if res["provenance"].get("failure_reason"):
                print(f"Note: {res['provenance']['failure_reason']}", file=sys.stderr)
        return

    # Classic subtitle-only path
    try:
        sub_file = parse_subtitle_file(args.srt_file, encoding=args.encoding, source=args.source)

        if args.wordlist:
            words = load_wordlist(args.wordlist)
            match_profanity_in_cues(sub_file, words, source=args.source)

        if args.out_cues:
            export_cues_json(sub_file, args.out_cues)
            print(f"Exported cues.json to: {args.out_cues}")

        if args.out_detections:
            export_detections_json(
                sub_file,
                args.out_detections,
                source=args.source,
                source_file=args.srt_file,
                wordlist_path=args.wordlist,
            )
            print(f"Exported detections to: {args.out_detections}")

        if args.json:
            print(json.dumps(cues_to_dicts(sub_file), indent=2))
        elif not args.out_cues and not args.out_detections:
            sound_count = sum(1 for c in sub_file if getattr(c, "kind", None) == "sound_description")
            speech_count = len(sub_file) - sound_count
            flagged_count = sum(1 for c in sub_file if getattr(c, "flagged", None))
            print(f"Processed: {args.srt_file}")
            print(f"Total Cues: {len(sub_file)} (Speech: {speech_count}, Sound Descriptions: {sound_count})")
            if args.wordlist:
                print(f"Cues Flagged with Profanity: {flagged_count}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
