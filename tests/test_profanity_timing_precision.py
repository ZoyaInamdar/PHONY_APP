"""Comprehensive test suite for profanity timing precision, provenance resolution,
word-level vs cue-level mute boundaries, and shared detector contract compliance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from scripts.package_cues import (
    load_and_aggregate_detector_inputs,
    merge_detections,
    package_and_save_index,
    package_cues,
    validate_raw_detection,
)
from scripts.parse_subtitles import (
    cues_to_dicts,
    match_profanity_in_cues,
    parse_subtitle_file,
)
from scripts.validate_index import validate_index


@pytest.fixture
def test_config() -> Dict[str, Any]:
    """Test config with explicit word-level precision settings."""
    return {
        "project_metadata": {"default_film_id": "test_film"},
        "triggers": {
            "profanity": {
                "merge_gap_seconds": 2.0,
                "word_level_merge_gap_seconds": 0.0,
                "preserve_word_precision": True,
                "max_segment_duration_seconds": 10.0,
            },
            "loud_spike": {
                "merge_gap_seconds": 2.0,
                "max_segment_duration_seconds": 15.0,
            },
        },
    }


# -----------------------------------------------------------------------------
# Scenario 1: Short SRT cue with phrase remains cue-level
# -----------------------------------------------------------------------------
def test_short_srt_cues_merge_at_cue_level(tmp_path: Path, test_config: Dict[str, Any]):
    """Verify short SRT cues (<= 1.2s) are NOT falsely classified as word-level

    and merge using standard merge_gap_seconds (2.0s).
    """
    srt_file = tmp_path / "short_dialogue.srt"
    srt_file.write_text(
        "1\n00:00:01,000 --> 00:00:01,800\nDamn it!\n\n"
        "2\n00:00:02,400 --> 00:00:03,100\nWhat the hell!\n",
        encoding="utf-8",
    )
    # Cue 1: [1.0, 1.8] (duration 0.8s <= 1.2s)
    # Cue 2: [2.4, 3.1] (duration 0.7s <= 1.2s)
    # Gap: 0.6s (< 2.0s)
    wordlist = tmp_path / "words.txt"
    wordlist.write_text("damn\nhell\n", encoding="utf-8")

    sub_file = parse_subtitle_file(srt_file, wordlist_path=wordlist)
    cue_dicts = cues_to_dicts(sub_file)

    raw_input = {
        "film_id": "test_film",
        "provenance": {
            "stage": "subtitles",
            "evidence_type": "subtitle_text",
            "timing_type": "estimated",
            "status": "ok",
        },
        "cues": cue_dicts,
    }

    index_data = package_cues(
        raw_data=raw_input,
        config=test_config,
        duration_seconds=10.0,
        synthetic=True,
    )

    cues = index_data["cues"]
    # With 2.0s merge gap, [1.0, 1.8] and [2.4, 3.1] MUST merge into [1.0, 3.1]
    assert len(cues) == 1
    assert cues[0]["start"] == 1.0
    assert cues[0]["end"] == 3.1
    assert cues[0]["trigger"] == "profanity"


# -----------------------------------------------------------------------------
# Scenario 2: Short WebVTT cue with phrase remains cue-level
# -----------------------------------------------------------------------------
def test_short_vtt_cues_merge_at_cue_level(tmp_path: Path, test_config: Dict[str, Any]):
    """Verify short WebVTT cues (<= 1.2s) merge using standard merge_gap_seconds."""
    vtt_file = tmp_path / "short_vtt.vtt"
    vtt_file.write_text(
        "WEBVTT\n\n"
        "00:05.000 --> 00:05.700\nDamn.\n\n"
        "00:06.200 --> 00:06.900\nCrap.\n",
        encoding="utf-8",
    )
    # Cue 1: [5.0, 5.7] (0.7s)
    # Cue 2: [6.2, 6.9] (0.7s)
    # Gap: 0.5s (< 2.0s)
    wordlist = tmp_path / "words.txt"
    wordlist.write_text("damn\ncrap\n", encoding="utf-8")

    sub_file = parse_subtitle_file(vtt_file, wordlist_path=wordlist)
    cue_dicts = cues_to_dicts(sub_file)

    raw_input = {
        "film_id": "test_film",
        "provenance": {
            "stage": "subtitles",
            "evidence_type": "subtitle_text",
            "timing_type": "estimated",
            "status": "ok",
        },
        "cues": cue_dicts,
    }

    index_data = package_cues(
        raw_data=raw_input,
        config=test_config,
        duration_seconds=10.0,
        synthetic=True,
    )

    cues = index_data["cues"]
    assert len(cues) == 1
    assert cues[0]["start"] == 5.0
    assert cues[0]["end"] == 6.9


# -----------------------------------------------------------------------------
# Scenario 3: Long STT word detection retains word precision
# -----------------------------------------------------------------------------
def test_long_stt_word_detection_preserves_precision(test_config: Dict[str, Any]):
    """Verify STT word detections lasting > 1.2s retain word-level precision

    and do NOT get merged across gaps because duration exceeds 1.2s.
    """
    raw_stt_data = {
        "film_id": "test_film",
        "provenance": {
            "stage": "profanity_detector",
            "evidence_type": "stt_word",
            "timing_type": "exact",
            "status": "fallback_used",
        },
        "detections": [
            # Drawn-out profanity lasting 1.5s (> 1.2s)
            {"start": 10.0, "end": 11.5, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
            # Another word 0.6s later (within 2.0s standard gap, but outside 0.0s word gap)
            {"start": 12.1, "end": 12.6, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
        ],
    }

    index_data = package_cues(
        raw_data=raw_stt_data,
        config=test_config,
        duration_seconds=20.0,
        synthetic=True,
    )

    cues = index_data["cues"]
    # Because evidence_type is stt_word and preserve_word_precision is True with gap 0.0,
    # these two detections MUST NOT merge despite the first having duration 1.5s!
    assert len(cues) == 2
    assert cues[0]["start"] == 10.0
    assert cues[0]["end"] == 11.5
    assert cues[1]["start"] == 12.1
    assert cues[1]["end"] == 12.6


# -----------------------------------------------------------------------------
# Scenario 4: Adjacent word detections obey word_level_merge_gap_seconds
# -----------------------------------------------------------------------------
def test_adjacent_word_detections_obey_word_level_merge_gap():
    """Verify adjacent word detections obey configured word_level_merge_gap_seconds."""
    words = [
        {"start": 1.0, "end": 1.4, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
        {"start": 1.6, "end": 2.0, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
    ]
    # Gap is 0.2s

    # Case A: word_level_merge_gap_seconds: 0.0 -> keep separate
    cfg_zero = {
        "triggers": {
            "profanity": {
                "merge_gap_seconds": 2.0,
                "word_level_merge_gap_seconds": 0.0,
                "preserve_word_precision": True,
                "max_segment_duration_seconds": 10.0,
            }
        }
    }
    merged_zero = merge_detections(words, config=cfg_zero, profanity_granularity="word")
    assert len(merged_zero) == 2
    assert merged_zero[0]["start"] == 1.0 and merged_zero[0]["end"] == 1.4
    assert merged_zero[1]["start"] == 1.6 and merged_zero[1]["end"] == 2.0

    # Case B: word_level_merge_gap_seconds: 0.3 -> merges across 0.2s gap
    cfg_point3 = {
        "triggers": {
            "profanity": {
                "merge_gap_seconds": 2.0,
                "word_level_merge_gap_seconds": 0.3,
                "preserve_word_precision": True,
                "max_segment_duration_seconds": 10.0,
            }
        }
    }
    merged_point3 = merge_detections(words, config=cfg_point3, profanity_granularity="word")
    assert len(merged_point3) == 1
    assert merged_point3[0]["start"] == 1.0
    assert merged_point3[0]["end"] == 2.0


# -----------------------------------------------------------------------------
# Scenario 5: preserve_word_precision toggle behavior
# -----------------------------------------------------------------------------
def test_preserve_word_precision_toggle():
    """Verify preserve_word_precision: false falls back to standard merge_gap_seconds."""
    words = [
        {"start": 1.0, "end": 1.4, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
        {"start": 2.2, "end": 2.6, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
    ]
    # Gap is 0.8s (<= 2.0s)

    cfg_disabled = {
        "triggers": {
            "profanity": {
                "merge_gap_seconds": 2.0,
                "word_level_merge_gap_seconds": 0.0,
                "preserve_word_precision": False,  # DISABLED
                "max_segment_duration_seconds": 10.0,
            }
        }
    }
    # With preserve_word_precision: False, word-level detections merge using 2.0s gap
    merged = merge_detections(words, config=cfg_disabled, profanity_granularity="word")
    assert len(merged) == 1
    assert merged[0]["start"] == 1.0
    assert merged[0]["end"] == 2.6


# -----------------------------------------------------------------------------
# Scenario 6: Mixed input sources and provenance propagation
# -----------------------------------------------------------------------------
def test_mixed_inputs_provenance_resolution(tmp_path: Path, test_config: Dict[str, Any]):
    """Verify load_and_aggregate_detector_inputs properly extracts profanity granularity from provenance."""
    # File A: Subtitles with estimated timing
    sub_file = tmp_path / "sub_dets.json"
    sub_file.write_text(
        json.dumps({
            "film_id": "test_film",
            "timeline": {"duration_seconds": 20.0, "fps": 24.0},
            "synthetic": True,
            "provenance": {
                "stage": "subtitles",
                "evidence_type": "subtitle_text",
                "timing_type": "estimated",
                "status": "ok",
            },
            "detections": [
                {"start": 1.0, "end": 1.8, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"}
            ],
        }),
        encoding="utf-8",
    )

    dets_sub, meta_sub = load_and_aggregate_detector_inputs(input_paths=[sub_file])
    assert meta_sub.get("profanity_granularity") == "cue"
    assert meta_sub["trigger_coverage"].get("profanity") == "ok"

    # File B: STT with exact word timing
    stt_file = tmp_path / "stt_dets.json"
    stt_file.write_text(
        json.dumps({
            "film_id": "test_film",
            "timeline": {"duration_seconds": 20.0, "fps": 24.0},
            "synthetic": True,
            "provenance": {
                "stage": "profanity_detector",
                "evidence_type": "stt_word",
                "timing_type": "exact",
                "status": "fallback_used",
            },
            "detections": [
                {"start": 3.0, "end": 3.4, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"}
            ],
        }),
        encoding="utf-8",
    )

    dets_stt, meta_stt = load_and_aggregate_detector_inputs(input_paths=[stt_file])
    assert meta_stt.get("profanity_granularity") == "word"
    assert meta_stt["trigger_coverage"].get("profanity") == "fallback_used"


# -----------------------------------------------------------------------------
# Scenario 7: Shared 5-field raw detection contract compliance
# -----------------------------------------------------------------------------
def test_shared_five_field_raw_detection_contract():
    """Verify raw detections conform strictly to the 5-field contract."""
    det = {
        "start": 1.5,
        "end": 2.0,
        "trigger": "profanity",
        "score": 1.0,
        "source": "profanity_detector",
    }
    # Validates without raising
    validate_raw_detection(det, canonical_triggers={"profanity"})
    assert set(det.keys()) == {"start", "end", "trigger", "score", "source"}

    # Invalid contract: missing source
    invalid_det = {
        "start": 1.5,
        "end": 2.0,
        "trigger": "profanity",
        "score": 1.0,
    }
    with pytest.raises(ValueError, match="missing required field 'source'"):
        validate_raw_detection(invalid_det, canonical_triggers={"profanity"})


# -----------------------------------------------------------------------------
# Scenario 8: End-to-end packaging and index validation
# -----------------------------------------------------------------------------
def test_end_to_end_packaging_and_validation(tmp_path: Path):
    """Verify packaged index passes schema and domain validation."""
    raw_stt_file = tmp_path / "stt_input.json"
    raw_stt_file.write_text(
        json.dumps({
            "film_id": "precision_test_film",
            "timeline": {"duration_seconds": 30.0, "fps": 24.0},
            "synthetic": True,
            "provenance": {
                "stage": "profanity_detector",
                "evidence_type": "stt_word",
                "timing_type": "exact",
                "status": "fallback_used",
            },
            "detections": [
                {"start": 2.0, "end": 2.4, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
                {"start": 5.0, "end": 6.5, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
            ],
        }),
        encoding="utf-8",
    )

    out_index = tmp_path / "index.json"
    index_data = package_and_save_index(
        input_path=raw_stt_file,
        output_path=out_index,
        film_id="precision_test_film",
        duration_seconds=30.0,
        fps=24.0,
        synthetic=True,
    )

    assert out_index.is_file()
    assert index_data["film_id"] == "precision_test_film"
    assert len(index_data["cues"]) == 2
    assert index_data["cues"][0]["id"] == "cue_precision_test_film_profanity_002000_002400"
    assert index_data["cues"][1]["id"] == "cue_precision_test_film_profanity_005000_006500"

    # Validate with domain validator
    validate_index(index_path=out_index)
