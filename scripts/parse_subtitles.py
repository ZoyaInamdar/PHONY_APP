#!/usr/bin/env python3
"""Module for parsing and classifying SRT subtitle files for Trigger-Aware Player pipeline.

Uses `pysrt` to read and validate .srt files, classifying each subtitle cue as
either 'sound_description' or 'speech', and matches profanity words against
speech cues using an external word list.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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
        * evidence: 'subtitle_text'
        * timing: 'estimated'
        * spoken_confirmed: null
    - Produces shared detection records for downstream merging.

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

                # Linear interpolation for estimated word timestamp
                word_start = round(start_s + (char_start / text_length) * cue_duration, 3)
                word_end = round(start_s + (char_end / text_length) * cue_duration, 3)

                flagged_terms.append({
                    "term_id": term_id,
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

            # Add raw detection record on shared timeline
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

    Suitable for export to JSON (e.g. cues.json). Includes `flagged` list if present.
    """
    results: List[Dict[str, Any]] = []
    for i, cue in enumerate(sub_file):
        cue_id = f"c{getattr(cue, 'index', i + 1)}"
        item_dict: Dict[str, Any] = {
            "id": cue_id,
            "start": round(subrip_time_to_seconds(cue.start), 3),
            "end": round(subrip_time_to_seconds(cue.end), 3),
            "kind": getattr(cue, "kind", "speech"),
            "text": cue.text,
        }

        flagged = getattr(cue, "flagged", None)
        if flagged:
            item_dict["flagged"] = flagged

        results.append(item_dict)
    return results


def main() -> None:
    """CLI interface for parsing subtitles and detecting profanity."""
    parser = argparse.ArgumentParser(
        description="Parse and classify subtitle cues in an .srt file and match profanity."
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
        "--json",
        action="store_true",
        help="Output parsed cues as formatted JSON.",
    )
    parser.add_argument(
        "--out-cues",
        type=str,
        default=None,
        help="Path to write cues.json output.",
    )
    parser.add_argument(
        "--out-detections",
        type=str,
        default=None,
        help="Path to write detections/subtitles.json output.",
    )

    args = parser.parse_args()

    try:
        sub_file = parse_srt_file(args.srt_file, encoding=args.encoding)

        raw_detections: List[Dict[str, Any]] = []
        if args.wordlist:
            words = load_wordlist(args.wordlist)
            raw_detections = match_profanity_in_cues(sub_file, words)

        cues_data = cues_to_dicts(sub_file)

        if args.out_cues:
            out_p = Path(args.out_cues)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            with out_p.open("w", encoding="utf-8") as f:
                json.dump(cues_data, f, indent=2)
            print(f"Wrote cues to: {out_p}")

        if args.out_detections:
            det_p = Path(args.out_detections)
            det_p.parent.mkdir(parents=True, exist_ok=True)
            with det_p.open("w", encoding="utf-8") as f:
                json.dump(raw_detections, f, indent=2)
            print(f"Wrote detections to: {det_p}")

        if args.json:
            print(json.dumps(cues_data, indent=2))
        elif not args.out_cues and not args.out_detections:
            sound_count = sum(1 for c in sub_file if getattr(c, "kind", None) == "sound_description")
            speech_count = len(sub_file) - sound_count
            flagged_count = sum(1 for c in sub_file if getattr(c, "flagged", None))
            print(f"Processed: {args.srt_file}")
            print(f"Total Cues: {len(sub_file)} (Speech: {speech_count}, Sound Descriptions: {sound_count})")
            if args.wordlist:
                print(f"Cues Flagged with Profanity: {flagged_count}")
            for cue in sub_file[:5]:
                flag_info = f" [FLAGGED: {len(cue.flagged)}]" if getattr(cue, "flagged", None) else ""
                print(f"  [{cue.kind.upper()}]{flag_info} {cue.start} --> {cue.end}: {cue.text.replace(os.linesep, ' ')}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
