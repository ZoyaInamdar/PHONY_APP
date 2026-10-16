#!/usr/bin/env python3
"""Module for parsing, classifying, and exporting SRT subtitle cues for Trigger-Aware Player pipeline.

Uses `pysrt` to read and validate .srt files, classifies each subtitle cue as
either 'sound_description' or 'speech', matches profanity words against speech
cues, and exports results to JSON according to shared pipeline contracts:
- cues.json: Array of every cue with id, start, end, kind, text, and flagged_words.
- detections/subtitles.json: Flagged intervals matching shared detector contract with provenance.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pysrt


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
    source: str = "subtitles",
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
        source: Component identifier for raw detections (default: 'subtitles').

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


def parse_srt_file(
    file_path: Union[str, Path],
    encoding: Optional[str] = None,
    wordlist_path: Optional[Union[str, Path]] = None,
) -> pysrt.SubRipFile:
    """Parse an .srt file using pysrt, classify cues, and optionally match profanity.

    Args:
        file_path: Path to the .srt file.
        encoding: Text encoding. If None, auto-detects with fallback.
        wordlist_path: Optional path to external profanity word list.

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

        if end_s < start_s:
            raise SubtitleParseError(
                f"Invalid cue interval at index {getattr(cue, 'index', i + 1)} in {path.name}: "
                f"end ({end_s:.3f}s) is before start ({start_s:.3f}s)."
            )

        classify_cue(cue)

    # 5. Optional profanity detection if wordlist_path is supplied
    if wordlist_path is not None:
        words = load_wordlist(wordlist_path)
        match_profanity_in_cues(sub_file, words)

    return sub_file


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
    sub_file: pysrt.SubRipFile,
    output_path: Union[str, Path],
    source: str = "subtitles",
    source_file: Optional[str] = None,
    wordlist_path: Optional[Union[str, Path]] = None,
    indent: int = 2,
) -> Dict[str, Any]:
    """Export flagged intervals to detections/subtitles.json with a provenance block.

    Each detection adheres to the shared detector contract:
    { "start": float, "end": float, "trigger": "profanity", "score": 1.0, "source": "subtitles" }
    Because timing is estimated, start/end cover the entire cue duration.

    Args:
        sub_file: Parsed SubRipFile with flagged speech cues.
        output_path: Destination path for detections/subtitles.json.
        source: Stable component identifier (default: 'subtitles').
        source_file: Optional original SRT filename for provenance.
        wordlist_path: Optional wordlist path for provenance.
        indent: JSON indentation level (default: 2).

    Returns:
        Dictionary containing the provenance block and detections list.
    """
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

    payload: Dict[str, Any] = {
        "provenance": provenance,
        "detections": detections,
    }

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    with out_p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent)

    return payload


def main() -> None:
    """CLI interface for parsing subtitles, detecting profanity, and exporting JSON."""
    parser = argparse.ArgumentParser(
        description="Parse subtitles, detect profanity, and export cues.json and detections/subtitles.json."
    )
    parser.add_argument("srt_file", type=str, help="Path to the .srt file to process.")
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
        "--json",
        action="store_true",
        help="Output parsed cues as formatted JSON to stdout.",
    )

    args = parser.parse_args()

    try:
        sub_file = parse_srt_file(args.srt_file, encoding=args.encoding)

        if args.wordlist:
            words = load_wordlist(args.wordlist)
            match_profanity_in_cues(sub_file, words)

        if args.out_cues:
            export_cues_json(sub_file, args.out_cues)
            print(f"Exported cues.json to: {args.out_cues}")

        if args.out_detections:
            export_detections_json(
                sub_file,
                args.out_detections,
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
