"""Comprehensive test suite for WebVTT subtitle parsing, STT fallback integration,
precise mute interval preservation, and pipeline coverage propagation.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from scripts.package_cues import (
    adapt_subtitle_cues_to_raw_detections,
    merge_detections,
    package_cues,
)
from scripts.parse_subtitles import (
    SubtitleParseError,
    cues_to_dicts,
    export_cues_json,
    export_detections_json,
    load_wordlist,
    match_profanity_in_cues,
    parse_srt_file,
    parse_subtitle_file,
    parse_vtt_file,
    parse_vtt_timestamp,
)
from scripts.run_multi_detector_pipeline import (
    PipelineOptions,
    PipelinePaths,
    build_pipeline_stages,
    check_prerequisites,
)
from scripts.stt_fallback import (
    detect_profanity_with_fallback,
    match_profanity_in_transcript,
)


@pytest.fixture
def wordlist_file(tmp_path: Path) -> Path:
    p = tmp_path / "profanity_wordlist.txt"
    p.write_text("damn\nhell\nshit\ncrap\n", encoding="utf-8")
    return p


@pytest.fixture
def dummy_audio_file(tmp_path: Path) -> Path:
    p = tmp_path / "audio.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 40)
    return p


# -----------------------------------------------------------------------------
# Scenario 1: VTT with a profanity match
# -----------------------------------------------------------------------------
def test_scenario_1_vtt_with_profanity_match(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test1.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:01.000 --> 00:03.000\nWhat the hell is going on?\n",
        encoding="utf-8",
    )
    res = detect_profanity_with_fallback(
        srt_path=vtt,
        wordlist_path=wordlist_file,
        enable_stt=False,
    )
    assert res["provenance"]["status"] == "ok"
    assert res["provenance"]["input_method"] == "subtitles"
    assert len(res["detections"]) == 1
    det = res["detections"][0]
    assert det["trigger"] == "profanity"
    assert det["start"] == 1.0
    assert det["end"] == 3.0
    assert det["score"] == 1.0
    assert det["source"] == "profanity_detector"


# -----------------------------------------------------------------------------
# Scenario 2: VTT with zero matches and status ok
# -----------------------------------------------------------------------------
def test_scenario_2_vtt_zero_matches_status_ok(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test2.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:01.000 --> 00:03.000\nGood morning to everyone.\n",
        encoding="utf-8",
    )
    res = detect_profanity_with_fallback(
        srt_path=vtt,
        wordlist_path=wordlist_file,
        enable_stt=False,
    )
    assert res["provenance"]["status"] == "ok"
    assert res["provenance"]["input_method"] == "subtitles"
    assert len(res["detections"]) == 0


# -----------------------------------------------------------------------------
# Scenario 3: Multiple cues and multiple matches in VTT
# -----------------------------------------------------------------------------
def test_scenario_3_multiple_cues_and_matches(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test3.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "00:01.000 --> 00:02.500\nDamn it all.\n\n"
        "00:03.000 --> 00:04.500\nEverything is fine here.\n\n"
        "00:05.000 --> 00:06.500\nThis is pure crap.\n",
        encoding="utf-8",
    )
    sub_file = parse_subtitle_file(vtt, wordlist_path=wordlist_file)
    assert len(sub_file) == 3
    dets = match_profanity_in_cues(sub_file, load_wordlist(wordlist_file))
    assert len(dets) == 2
    assert dets[0]["start"] == 1.0
    assert dets[0]["end"] == 2.5
    assert dets[1]["start"] == 5.0
    assert dets[1]["end"] == 6.5


# -----------------------------------------------------------------------------
# Scenario 4: Optional cue identifiers in VTT
# -----------------------------------------------------------------------------
def test_scenario_4_optional_cue_identifiers(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test4.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "cue-header-01\n"
        "00:01.000 --> 00:03.000\n"
        "Damn cue id.\n\n"
        "cue-header-02\n"
        "00:04.000 --> 00:06.000\n"
        "Normal clean line.\n",
        encoding="utf-8",
    )
    sub_file = parse_subtitle_file(vtt, wordlist_path=wordlist_file)
    assert len(sub_file) == 2
    assert getattr(sub_file[0], "id", None) == "cue-header-01"
    assert getattr(sub_file[1], "id", None) == "cue-header-02"


# -----------------------------------------------------------------------------
# Scenario 5: Multiline cue text in VTT
# -----------------------------------------------------------------------------
def test_scenario_5_multiline_text(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test5.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "00:01.000 --> 00:04.000\n"
        "First line of dialogue.\n"
        "Second line with damn word.\n",
        encoding="utf-8",
    )
    sub_file = parse_subtitle_file(vtt, wordlist_path=wordlist_file)
    assert len(sub_file) == 1
    assert "First line" in sub_file[0].text
    assert "Second line" in sub_file[0].text
    dets = match_profanity_in_cues(sub_file, load_wordlist(wordlist_file))
    assert len(dets) == 1


# -----------------------------------------------------------------------------
# Scenario 6: Cue settings after timestamp ranges
# -----------------------------------------------------------------------------
def test_scenario_6_cue_settings_handling(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test6.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "00:01.000 --> 00:03.500 line:90% align:middle position:50% size:80%\n"
        "Damn with settings.\n",
        encoding="utf-8",
    )
    sub_file = parse_subtitle_file(vtt, wordlist_path=wordlist_file)
    assert len(sub_file) == 1
    assert round(sub_file[0].start.ordinal / 1000.0, 3) == 1.0
    assert round(sub_file[0].end.ordinal / 1000.0, 3) == 3.5


# -----------------------------------------------------------------------------
# Scenario 7: Inline formatting tags stripped for matching
# -----------------------------------------------------------------------------
def test_scenario_7_inline_formatting_tags(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test7.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "00:01.000 --> 00:03.000\n"
        "<v John><b>Damn</b> it, <i>look</i> out!</v>\n",
        encoding="utf-8",
    )
    sub_file = parse_subtitle_file(vtt, wordlist_path=wordlist_file)
    assert len(sub_file) == 1
    dets = match_profanity_in_cues(sub_file, load_wordlist(wordlist_file))
    assert len(dets) == 1
    assert dets[0]["trigger"] == "profanity"


# -----------------------------------------------------------------------------
# Scenario 8: NOTE blocks containing profanity terms are ignored
# -----------------------------------------------------------------------------
def test_scenario_8_note_blocks_containing_profanity_ignored(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test8.vtt"
    vtt.write_text(
        "WEBVTT\n\n"
        "NOTE This is a translator note containing damn and hell words\n\n"
        "00:02.000 --> 00:04.000\n"
        "Clean actual speech.\n\n"
        "NOTE\n"
        "Another multiline note with crap.\n\n",
        encoding="utf-8",
    )
    sub_file = parse_subtitle_file(vtt, wordlist_path=wordlist_file)
    assert len(sub_file) == 1
    assert sub_file[0].text == "Clean actual speech."
    dets = match_profanity_in_cues(sub_file, load_wordlist(wordlist_file))
    assert len(dets) == 0


# -----------------------------------------------------------------------------
# Scenario 9: UTF-8 BOM support
# -----------------------------------------------------------------------------
def test_scenario_9_utf8_bom(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test9.vtt"
    content = "\ufeffWEBVTT\n\n00:01.000 --> 00:03.000\nDamn with BOM.\n"
    vtt.write_bytes(content.encode("utf-8"))
    sub_file = parse_subtitle_file(vtt, wordlist_path=wordlist_file)
    assert len(sub_file) == 1


# -----------------------------------------------------------------------------
# Scenario 10: Empty valid VTT produces 0 cues
# -----------------------------------------------------------------------------
def test_scenario_10_empty_valid_vtt(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test10.vtt"
    vtt.write_text("WEBVTT\n\n", encoding="utf-8")
    sub_file = parse_subtitle_file(vtt, wordlist_path=wordlist_file)
    assert len(sub_file) == 0


# -----------------------------------------------------------------------------
# Scenario 11: Missing file raises FileNotFoundError
# -----------------------------------------------------------------------------
def test_scenario_11_missing_file_raises(tmp_path: Path):
    missing = tmp_path / "missing.vtt"
    with pytest.raises(FileNotFoundError):
        parse_subtitle_file(missing)


# -----------------------------------------------------------------------------
# Scenario 12: Invalid or missing WEBVTT header raises SubtitleParseError
# -----------------------------------------------------------------------------
def test_scenario_12_invalid_header_raises(tmp_path: Path):
    bad = tmp_path / "bad_header.vtt"
    bad.write_text("NOTWEBVTT\n\n00:01.000 --> 00:03.000\nHello\n", encoding="utf-8")
    with pytest.raises(SubtitleParseError) as exc:
        parse_subtitle_file(bad)
    assert "Missing or invalid WEBVTT header" in str(exc.value)


# -----------------------------------------------------------------------------
# Scenario 13: Invalid, zero-length, and reversed timestamps rejected
# -----------------------------------------------------------------------------
def test_scenario_13_invalid_intervals_rejected(tmp_path: Path):
    # Zero-length
    vtt_zero = tmp_path / "zero.vtt"
    vtt_zero.write_text("WEBVTT\n\n00:02.000 --> 00:02.000\nZero length\n", encoding="utf-8")
    with pytest.raises(SubtitleParseError):
        parse_subtitle_file(vtt_zero)

    # Reversed
    vtt_rev = tmp_path / "reversed.vtt"
    vtt_rev.write_text("WEBVTT\n\n00:05.000 --> 00:02.000\nReversed\n", encoding="utf-8")
    with pytest.raises(SubtitleParseError):
        parse_subtitle_file(vtt_rev)

    # Malformed timestamp string
    vtt_bad_ts = tmp_path / "bad_ts.vtt"
    vtt_bad_ts.write_text("WEBVTT\n\n00:99.000 --> 00:02.000\nBad minutes\n", encoding="utf-8")
    with pytest.raises(SubtitleParseError):
        parse_subtitle_file(vtt_bad_ts)


# -----------------------------------------------------------------------------
# Scenario 14: Out-of-range cues rejected when duration is known
# -----------------------------------------------------------------------------
def test_scenario_14_out_of_range_cues_rejected(tmp_path: Path):
    vtt = tmp_path / "out_of_range.vtt"
    vtt.write_text("WEBVTT\n\n00:20.000 --> 00:25.000\nExceeds 15s duration\n", encoding="utf-8")
    with pytest.raises(SubtitleParseError):
        parse_subtitle_file(vtt, media_duration=15.0)


# -----------------------------------------------------------------------------
# Scenario 15: Unsupported file extension rejected
# -----------------------------------------------------------------------------
def test_scenario_15_unsupported_extension_rejected(tmp_path: Path):
    txt_file = tmp_path / "subs.txt"
    txt_file.write_text("Some text", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        parse_subtitle_file(txt_file)
    assert "Expected a file with '.srt' or '.vtt' extension" in str(exc.value)


# -----------------------------------------------------------------------------
# Scenario 16: Exact raw five-field detection contract
# -----------------------------------------------------------------------------
def test_scenario_16_exact_five_field_contract(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test16.vtt"
    vtt.write_text("WEBVTT\n\n00:01.000 --> 00:03.000\nDamn word.\n", encoding="utf-8")
    res = detect_profanity_with_fallback(
        srt_path=vtt,
        wordlist_path=wordlist_file,
        enable_stt=False,
    )
    assert len(res["detections"]) == 1
    det = res["detections"][0]
    expected_keys = {"start", "end", "trigger", "score", "source"}
    assert set(det.keys()) == expected_keys
    assert isinstance(det["start"], float)
    assert isinstance(det["end"], float)
    assert det["trigger"] == "profanity"
    assert det["score"] == 1.0
    assert det["source"] == "profanity_detector"


# -----------------------------------------------------------------------------
# Scenario 17: Correct timestamp conversion to seconds
# -----------------------------------------------------------------------------
def test_scenario_17_timestamp_conversion_to_seconds():
    assert parse_vtt_timestamp("00:01.500") == 1.5
    assert parse_vtt_timestamp("01:00.000") == 60.0
    assert parse_vtt_timestamp("01:02:03.456") == 3723.456
    assert parse_vtt_timestamp("00:00:05") == 5.0


# -----------------------------------------------------------------------------
# Scenario 18: VTT packaging and index validation
# -----------------------------------------------------------------------------
def test_scenario_18_vtt_packaging_and_validation(tmp_path: Path, wordlist_file: Path):
    vtt = tmp_path / "test18.vtt"
    vtt.write_text("WEBVTT\n\n00:01.000 --> 00:03.000\nDamn it.\n", encoding="utf-8")
    sub_file = parse_subtitle_file(vtt, wordlist_path=wordlist_file)
    cues = cues_to_dicts(sub_file)

    index_data = package_cues(
        raw_data=[],
        subtitles=cues,
        film_id="vtt_test_film",
        duration_seconds=10.0,
        fps=24.0,
        synthetic=True,
    )
    assert index_data["film_id"] == "vtt_test_film"
    assert len(index_data["cues"]) == 1
    assert index_data["cues"][0]["trigger"] == "profanity"
    assert index_data["cues"][0]["start"] == 1.0
    assert index_data["cues"][0]["end"] == 3.0


# -----------------------------------------------------------------------------
# Scenario 19: SRT regression behavior preserved
# -----------------------------------------------------------------------------
def test_scenario_19_srt_regression_behavior(tmp_path: Path, wordlist_file: Path):
    srt = tmp_path / "test19.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nDamn SRT cue.\n", encoding="utf-8")
    sub_file = parse_subtitle_file(srt, wordlist_path=wordlist_file)
    assert len(sub_file) == 1
    assert sub_file[0].text == "Damn SRT cue."
    dets = match_profanity_in_cues(sub_file, load_wordlist(wordlist_file))
    assert len(dets) == 1


# -----------------------------------------------------------------------------
# Scenario 20: STT fallback with missing or unusable VTT/SRT subtitles
# -----------------------------------------------------------------------------
def test_scenario_20_stt_fallback_with_unusable_vtt(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    corrupt_vtt = tmp_path / "corrupt.vtt"
    corrupt_vtt.write_text("NOT VALID VTT AT ALL", encoding="utf-8")
    mock_segments = [
        {"id": 1, "start": 2.0, "end": 4.0, "text": "What the hell happened?"}
    ]
    res = detect_profanity_with_fallback(
        srt_path=corrupt_vtt,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: mock_segments,
    )
    assert res["provenance"]["status"] == "fallback_used"
    assert res["provenance"]["input_method"] == "speech_to_text"
    assert len(res["detections"]) == 1
    assert res["detections"][0]["trigger"] == "profanity"


# -----------------------------------------------------------------------------
# Scenario 21: Coverage propagation through pipeline
# -----------------------------------------------------------------------------
def test_scenario_21_coverage_propagation(tmp_path: Path):
    det_input = {
        "film_id": "test_film",
        "provenance": {
            "stage": "profanity_detector",
            "status": "fallback_used",
        },
        "detections": [
            {"start": 1.0, "end": 1.5, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"}
        ],
    }
    idx = package_cues(
        raw_data=det_input,
        film_id="test_film",
        duration_seconds=10.0,
        synthetic=True,
    )
    assert "trigger_coverage" in idx
    assert idx["trigger_coverage"].get("profanity") == "fallback_used"


# -----------------------------------------------------------------------------
# Scenario 22: Other triggers preserved when profanity analysis is unavailable
# -----------------------------------------------------------------------------
def test_scenario_22_other_triggers_preserved_on_profanity_failure(tmp_path: Path):
    prof_input = {
        "film_id": "multi_film",
        "provenance": {
            "stage": "profanity_detector",
            "status": "unavailable",
            "failure_reason": "No subtitles and STT disabled",
        },
        "detections": [],
    }
    loudness_input = {
        "film_id": "multi_film",
        "detections": [
            {"start": 3.0, "end": 4.5, "trigger": "loud_spike", "score": 0.95, "source": "loudness_detector"}
        ],
    }
    idx = package_cues(
        raw_data=[loudness_input, prof_input],
        film_id="multi_film",
        duration_seconds=10.0,
        synthetic=True,
    )
    assert len(idx["cues"]) == 1
    assert idx["cues"][0]["trigger"] == "loud_spike"
    assert idx["trigger_coverage"].get("profanity") == "unavailable"


# -----------------------------------------------------------------------------
# Section 7 tests: Precise profanity word-level timing preservation
# -----------------------------------------------------------------------------
def test_word_level_timestamps_preserve_precision_without_overmerging(tmp_path: Path):
    # Two word-level detections separated by 1.2s
    # With standard 2.0s merge gap, they would merge into [2.0, 3.8].
    # With word-level precision preservation, they remain two distinct cues!
    word_dets = [
        {"start": 2.0, "end": 2.3, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
        {"start": 3.5, "end": 3.8, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
    ]
    cfg = {
        "triggers": {
            "profanity": {
                "merge_gap_seconds": 2.0,
                "word_level_merge_gap_seconds": 0.0,
                "preserve_word_precision": True,
                "max_segment_duration_seconds": 10.0,
            }
        }
    }
    merged = merge_detections(word_dets, config=cfg, profanity_granularity="word")
    assert len(merged) == 2
    assert merged[0]["start"] == 2.0 and merged[0]["end"] == 2.3
    assert merged[1]["start"] == 3.5 and merged[1]["end"] == 3.8


def test_srt_cue_level_merging_remains_compatible(tmp_path: Path):
    # Two standard SRT cue-level detections (duration > 1.2s) separated by 0.5s
    # They should merge according to standard merge_gap_seconds: 2.0
    srt_cues = [
        {"start": 2.0, "end": 4.5, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
        {"start": 5.0, "end": 7.5, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
    ]
    cfg = {
        "triggers": {
            "profanity": {
                "merge_gap_seconds": 2.0,
                "word_level_merge_gap_seconds": 0.0,
                "preserve_word_precision": True,
                "max_segment_duration_seconds": 10.0,
            }
        }
    }
    merged = merge_detections(srt_cues, config=cfg)
    assert len(merged) == 1
    assert merged[0]["start"] == 2.0 and merged[0]["end"] == 7.5


def test_other_triggers_merge_behavior_unchanged(tmp_path: Path):
    # Loudness detections separated by 1.0s with merge_gap: 2.0s
    # Must merge normally
    loud_dets = [
        {"start": 2.0, "end": 2.5, "trigger": "loud_spike", "score": 0.9, "source": "loudness_detector"},
        {"start": 3.5, "end": 4.0, "trigger": "loud_spike", "score": 0.8, "source": "loudness_detector"},
    ]
    cfg = {
        "triggers": {
            "loud_spike": {
                "merge_gap_seconds": 2.0,
                "max_segment_duration_seconds": 15.0,
            }
        }
    }
    merged = merge_detections(loud_dets, config=cfg)
    assert len(merged) == 1
    assert merged[0]["start"] == 2.0 and merged[0]["end"] == 4.0


def test_pipeline_runner_wires_stt_flags_and_vtt():
    paths = PipelinePaths.default()
    options = PipelineOptions(
        enable_stt=True,
        stt_backend="faster-whisper",
        stt_model="base.en",
    )
    stages = build_pipeline_stages(paths, options)
    stage3 = next(s for s in stages if "profanity_detector" in s.name)
    cmd = stage3.command
    assert "--enable-stt" in cmd
    assert "--stt-backend" in cmd
    assert "faster-whisper" in cmd
    assert "--stt-model" in cmd
    assert "base.en" in cmd
    assert "--audio" in cmd
