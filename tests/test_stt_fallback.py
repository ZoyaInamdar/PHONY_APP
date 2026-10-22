"""Unit and integration tests for optional speech-to-text (STT) fallback for profanity detection.

Covers all 20 required verification criteria:
1. Valid SRT with profanity: existing parser is used; STT is not called.
2. Valid SRT without profanity: successful zero-match analysis; STT is not called.
3. Missing SRT with fallback disabled: STT is not called; status is not falsely marked successful.
4. Missing SRT with fallback enabled: mocked STT output is matched by the existing profanity matcher.
5. Empty or unusable SRT with fallback enabled: fallback is attempted.
6. Malformed SRT with fallback disabled: detection status is clear and no false clean result is reported.
7. Malformed SRT with fallback enabled: fallback succeeds or produces a clear failure status.
8. STT dependency missing.
9. Model weights unavailable.
10. Transcription failure.
11. Empty or whitespace-only transcript.
12. Transcript containing no profanity.
13. Transcript containing one profane word.
14. Transcript containing multiple profane words.
15. Segment and word timestamp handling.
16. Invalid, negative, non-finite, zero-length, or out-of-range intervals.
17. No duplicate processing of the same dialogue by both SRT and STT.
18. Raw detection output passes through the existing packager and validator.
19. Other trigger detections remain intact when profanity detection is unavailable.
20. Existing player action resolution remains unchanged.
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from scripts.package_cues import package_cues
from scripts.parse_subtitles import (
    SubtitleParseError,
    detect_profanity_with_fallback,
    match_profanity_in_transcript,
)
from scripts.stt_fallback import (
    STTError,
    STTModelNotFoundError,
    STTTranscriptionError,
    STTUnavailableError,
    transcribe_audio_faster_whisper,
)
from scripts.validate_index import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SCHEMA_PATH,
    load_schema,
    validate_domain_rules,
    validate_schema_compliance,
)

SAMPLE_WORDLIST = ["ass", "bullshit", "crap", "damn", "fuck", "hell", "shit"]


@pytest.fixture
def wordlist_file(tmp_path: Path) -> Path:
    p = tmp_path / "profanity_wordlist.txt"
    p.write_text("\n".join(SAMPLE_WORDLIST) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def dummy_audio_file(tmp_path: Path) -> Path:
    p = tmp_path / "audio.wav"
    p.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
    return p


@pytest.fixture
def valid_profane_srt(tmp_path: Path) -> Path:
    p = tmp_path / "profane.srt"
    p.write_text(
        "1\n00:00:01,000 --> 00:00:03,500\nThis is damn good.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nClean dialogue here.\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def valid_clean_srt(tmp_path: Path) -> Path:
    p = tmp_path / "clean.srt"
    p.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nHello world.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nHave a wonderful day.\n",
        encoding="utf-8",
    )
    return p


# -----------------------------------------------------------------------------
# Test 1: Valid SRT with profanity -> subtitles used, STT is not called
# -----------------------------------------------------------------------------
def test_1_valid_srt_with_profanity_uses_subtitles(valid_profane_srt: Path, wordlist_file: Path, dummy_audio_file: Path):
    mock_stt = MagicMock()
    res = detect_profanity_with_fallback(
        srt_path=valid_profane_srt,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=mock_stt,
    )
    assert res["provenance"]["status"] == "ok"
    assert res["provenance"]["input_method"] == "subtitles"
    assert res["provenance"]["evidence_type"] == "subtitle_text"
    assert len(res["detections"]) == 1
    assert res["detections"][0]["trigger"] == "profanity"
    assert res["detections"][0]["start"] == 1.0
    assert res["detections"][0]["end"] == 3.5
    mock_stt.assert_not_called()


# -----------------------------------------------------------------------------
# Test 2: Valid SRT without profanity -> status 'ok', 0 detections, STT not called
# -----------------------------------------------------------------------------
def test_2_valid_srt_without_profanity_returns_zero_detections(valid_clean_srt: Path, wordlist_file: Path, dummy_audio_file: Path):
    mock_stt = MagicMock()
    res = detect_profanity_with_fallback(
        srt_path=valid_clean_srt,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=mock_stt,
    )
    assert res["provenance"]["status"] == "ok"
    assert res["provenance"]["input_method"] == "subtitles"
    assert len(res["detections"]) == 0
    mock_stt.assert_not_called()


# -----------------------------------------------------------------------------
# Test 3: Missing SRT with fallback disabled -> status 'skipped', STT not called
# -----------------------------------------------------------------------------
def test_3_missing_srt_fallback_disabled_records_skipped(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    mock_stt = MagicMock()
    missing_srt = tmp_path / "non_existent.srt"
    res = detect_profanity_with_fallback(
        srt_path=missing_srt,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=False,
        transcriber_fn=mock_stt,
    )
    assert res["provenance"]["status"] in ("skipped", "unavailable")
    assert res["provenance"]["input_method"] == "none"
    assert "fallback is disabled" in res["provenance"]["failure_reason"]
    assert len(res["detections"]) == 0
    mock_stt.assert_not_called()


# -----------------------------------------------------------------------------
# Test 4: Missing SRT with fallback enabled -> mocked STT is matched
# -----------------------------------------------------------------------------
def test_4_missing_srt_fallback_enabled_uses_stt(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    missing_srt = tmp_path / "non_existent.srt"
    mock_segments = [
        {"id": 1, "start": 2.0, "end": 4.5, "text": "What the hell is that?"}
    ]
    res = detect_profanity_with_fallback(
        srt_path=missing_srt,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: mock_segments,
    )
    assert res["provenance"]["status"] == "fallback_used"
    assert res["provenance"]["input_method"] == "speech_to_text"
    assert len(res["detections"]) == 1
    assert res["detections"][0]["trigger"] == "profanity"
    assert res["detections"][0]["start"] == 2.0
    assert res["detections"][0]["end"] == 4.5
    assert res["detections"][0]["source"] == "profanity_detector"


# -----------------------------------------------------------------------------
# Test 5: Empty (0-byte) SRT with fallback enabled -> STT fallback is attempted
# -----------------------------------------------------------------------------
def test_5_empty_srt_attempts_stt_fallback(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    empty_srt = tmp_path / "empty.srt"
    empty_srt.write_text("", encoding="utf-8")
    mock_segments = [
        {"id": 1, "start": 5.0, "end": 7.0, "text": "Shut up, damn it."}
    ]
    res = detect_profanity_with_fallback(
        srt_path=empty_srt,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: mock_segments,
    )
    assert res["provenance"]["status"] == "fallback_used"
    assert res["provenance"]["input_method"] == "speech_to_text"
    assert "empty" in res["provenance"]["subtitle_unusable_reason"].lower()
    assert len(res["detections"]) == 1


# -----------------------------------------------------------------------------
# Test 6: Malformed SRT with fallback disabled -> records unavailable/failed
# -----------------------------------------------------------------------------
def test_6_malformed_srt_fallback_disabled_reports_error(tmp_path: Path, wordlist_file: Path):
    malformed_srt = tmp_path / "corrupt.srt"
    malformed_srt.write_text("Not valid SRT format at all", encoding="utf-8")
    res = detect_profanity_with_fallback(
        srt_path=malformed_srt,
        audio_path=None,
        wordlist_path=wordlist_file,
        enable_stt=False,
    )
    assert res["provenance"]["status"] == "unavailable"
    assert res["provenance"]["input_method"] == "none"
    assert "disabled" in res["provenance"]["failure_reason"]
    assert len(res["detections"]) == 0


# -----------------------------------------------------------------------------
# Test 7: Malformed SRT with fallback enabled -> STT fallback succeeds
# -----------------------------------------------------------------------------
def test_7_malformed_srt_fallback_enabled_succeeds_via_stt(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    malformed_srt = tmp_path / "corrupt.srt"
    malformed_srt.write_text("Not valid SRT format", encoding="utf-8")
    mock_segments = [
        {"id": 1, "start": 10.0, "end": 12.0, "text": "This is bullshit."}
    ]
    res = detect_profanity_with_fallback(
        srt_path=malformed_srt,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: mock_segments,
    )
    assert res["provenance"]["status"] == "fallback_used"
    assert res["provenance"]["input_method"] == "speech_to_text"
    assert len(res["detections"]) == 1
    assert res["detections"][0]["start"] == 10.0


# -----------------------------------------------------------------------------
# Test 8: STT dependency missing -> records unavailable status
# -----------------------------------------------------------------------------
def test_8_stt_dependency_missing_records_unavailable(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    with patch("scripts.stt_fallback.transcribe_audio_faster_whisper", side_effect=STTUnavailableError("faster-whisper not installed")):
        res = detect_profanity_with_fallback(
            srt_path=None,
            audio_path=dummy_audio_file,
            wordlist_path=wordlist_file,
            enable_stt=True,
        )
        assert res["provenance"]["status"] == "unavailable"
        assert "not installed" in res["provenance"]["failure_reason"]
        assert len(res["detections"]) == 0


# -----------------------------------------------------------------------------
# Test 9: Model weights unavailable -> records unavailable status
# -----------------------------------------------------------------------------
def test_9_stt_model_weights_unavailable_records_unavailable(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    with patch("scripts.stt_fallback.transcribe_audio_faster_whisper", side_effect=STTModelNotFoundError("model base.en not found locally")):
        res = detect_profanity_with_fallback(
            srt_path=None,
            audio_path=dummy_audio_file,
            wordlist_path=wordlist_file,
            enable_stt=True,
        )
        assert res["provenance"]["status"] == "unavailable"
        assert "not found" in res["provenance"]["failure_reason"]
        assert len(res["detections"]) == 0


# -----------------------------------------------------------------------------
# Test 10: Transcription failure -> records failed status
# -----------------------------------------------------------------------------
def test_10_transcription_failure_records_failed(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    def failing_transcriber(_):
        raise STTTranscriptionError("CUDA out of memory / corrupted audio")

    res = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=failing_transcriber,
    )
    assert res["provenance"]["status"] == "failed"
    assert "corrupted audio" in res["provenance"]["failure_reason"]
    assert len(res["detections"]) == 0


# -----------------------------------------------------------------------------
# Test 11: Empty or blank transcript -> records status 'failed', not clean scan
# -----------------------------------------------------------------------------
def test_11_empty_transcript_produces_zero_detections(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    # Case A: 0 segments returned
    res_empty = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: [],
    )
    assert res_empty["provenance"]["status"] == "failed"
    assert "0 segments" in res_empty["provenance"]["failure_reason"]
    assert len(res_empty["detections"]) == 0

    # Case B: Blank/whitespace-only text returned
    blank_segments = [
        {"id": 1, "start": 1.0, "end": 2.0, "text": "   "},
        {"id": 2, "start": 3.0, "end": 4.0, "text": ""},
    ]
    res_blank = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: blank_segments,
    )
    assert res_blank["provenance"]["status"] == "failed"
    assert "blank text" in res_blank["provenance"]["failure_reason"]
    assert len(res_blank["detections"]) == 0

    # Case C: Invalid timestamps only
    invalid_timing_segments = [
        {"id": 1, "start": 5.0, "end": 2.0, "text": "Valid text but inverted timing"},
    ]
    res_timing = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: invalid_timing_segments,
    )
    assert res_timing["provenance"]["status"] == "failed"
    assert "invalid" in res_timing["provenance"]["failure_reason"]
    assert len(res_timing["detections"]) == 0

    # Case D: Valid quiet/short speech without profanity -> correctly records fallback_used
    quiet_segments = [
        {"id": 1, "start": 1.0, "end": 1.5, "text": "Yes."},
    ]
    res_quiet = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: quiet_segments,
    )
    assert res_quiet["provenance"]["status"] == "fallback_used"
    assert len(res_quiet["detections"]) == 0


# -----------------------------------------------------------------------------
# Test 12: Transcript containing no profanity -> status fallback_used, 0 detections
# -----------------------------------------------------------------------------
def test_12_clean_transcript_produces_zero_detections(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    clean_segments = [
        {"id": 1, "start": 0.5, "end": 2.0, "text": "Good morning everyone."},
        {"id": 2, "start": 2.5, "end": 4.0, "text": "The weather is lovely today."},
    ]
    res = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: clean_segments,
    )
    assert res["provenance"]["status"] == "fallback_used"
    assert len(res["detections"]) == 0


# -----------------------------------------------------------------------------
# Test 13: Transcript containing one profane word
# -----------------------------------------------------------------------------
def test_13_single_profane_word_detected(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    segments = [
        {"id": 1, "start": 1.0, "end": 3.0, "text": "Be careful, damn it."}
    ]
    res = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: segments,
    )
    assert len(res["detections"]) == 1
    assert res["detections"][0]["trigger"] == "profanity"
    assert res["detections"][0]["start"] == 1.0
    assert res["detections"][0]["end"] == 3.0


# -----------------------------------------------------------------------------
# Test 14: Transcript containing multiple profane words
# -----------------------------------------------------------------------------
def test_14_multiple_profane_words_detected(tmp_path: Path, wordlist_file: Path, dummy_audio_file: Path):
    segments = [
        {"id": 1, "start": 1.0, "end": 3.0, "text": "Holy shit, look at that."},
        {"id": 2, "start": 4.0, "end": 6.0, "text": "What the hell were you thinking?"},
    ]
    res = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: segments,
    )
    assert len(res["detections"]) == 2
    assert res["detections"][0]["start"] == 1.0
    assert res["detections"][1]["start"] == 4.0


# -----------------------------------------------------------------------------
# Test 15: Segment and word-level timestamp handling
# -----------------------------------------------------------------------------
def test_15_word_level_timestamps_used_when_available(wordlist_file: Path):
    segments_with_words = [
        {
            "id": 1,
            "start": 1.0,
            "end": 4.0,
            "text": "Don't be an ass here.",
            "words": [
                {"word": "Don't", "start": 1.0, "end": 1.5, "probability": 0.98},
                {"word": "be", "start": 1.6, "end": 1.8, "probability": 0.99},
                {"word": "an", "start": 1.9, "end": 2.1, "probability": 0.97},
                {"word": "ass", "start": 2.2, "end": 2.7, "probability": 0.95},
                {"word": "here.", "start": 2.8, "end": 3.5, "probability": 0.96},
            ],
        }
    ]
    dets = match_profanity_in_transcript(
        segments=segments_with_words,
        word_list=SAMPLE_WORDLIST,
        use_word_timestamps=True,
    )
    assert len(dets) == 1
    # Confirms word-level precision is used rather than full segment interval [1.0, 4.0]
    assert dets[0]["start"] == 2.2
    assert dets[0]["end"] == 2.7


# -----------------------------------------------------------------------------
# Test 16: Invalid, negative, non-finite, zero-length intervals safely handled
# -----------------------------------------------------------------------------
def test_16_invalid_and_out_of_range_intervals_rejected(wordlist_file: Path):
    corrupt_segments = [
        {"id": 1, "start": -2.0, "end": 2.0, "text": "bad start damn"},
        {"id": 2, "start": 5.0, "end": 4.0, "text": "inverted end damn"},
        {"id": 3, "start": float("nan"), "end": 6.0, "text": "nan start damn"},
        {"id": 4, "start": 10.0, "end": float("inf"), "text": "inf end damn"},
        {"id": 5, "start": 25.0, "end": 30.0, "text": "beyond duration damn"},
    ]
    dets = match_profanity_in_transcript(
        segments=corrupt_segments,
        word_list=SAMPLE_WORDLIST,
        media_duration=20.0,  # Limits media duration to 20s
    )
    # Segments 1-4 are mathematically invalid; segment 5 is beyond media duration
    assert len(dets) == 0


# -----------------------------------------------------------------------------
# Test 17: No duplicate processing by both SRT and STT
# -----------------------------------------------------------------------------
def test_17_no_duplicate_processing_when_both_present(valid_profane_srt: Path, dummy_audio_file: Path, wordlist_file: Path):
    mock_stt = MagicMock()
    res = detect_profanity_with_fallback(
        srt_path=valid_profane_srt,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=mock_stt,
    )
    # Verifies only 1 detection from SRT was produced and STT was never called
    assert len(res["detections"]) == 1
    assert res["provenance"]["input_method"] == "subtitles"
    mock_stt.assert_not_called()


# -----------------------------------------------------------------------------
# Test 18: Raw detection output passes through existing packager and validator
# -----------------------------------------------------------------------------
def test_18_raw_stt_detections_package_and_validate_cleanly(dummy_audio_file: Path, wordlist_file: Path):
    segments = [
        {"id": 1, "start": 10.0, "end": 12.0, "text": "Complete bullshit."},
        {"id": 2, "start": 12.5, "end": 14.0, "text": "More crap."},
    ]
    res = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: segments,
        film_id="stt_test_film",
        media_duration=50.0,
    )

    schema = load_schema(DEFAULT_SCHEMA_PATH)
    index_data = package_cues(
        raw_data=res,
        film_id="stt_test_film",
        duration_seconds=50.0,
        fps=24.0,
        schema=schema,
    )

    # Verifies schema compliance and domain business rules
    schema_errs = validate_schema_compliance(index_data, schema)
    assert schema_errs == []
    domain_errs = validate_domain_rules(index_data, {"flashing", "profanity", "gunshot", "explosion", "scream", "loud_spike", "sustained_loudness", "insect", "jump_scare", "visible_blood"})
    assert domain_errs == []
    assert index_data["trigger_coverage"]["profanity"] == "fallback_used"


# -----------------------------------------------------------------------------
# Test 19: Other trigger detections remain intact when profanity is unavailable
# -----------------------------------------------------------------------------
def test_19_other_trigger_detections_intact_when_profanity_unavailable():
    loudness_cue = {
        "start": 5.0,
        "end": 6.5,
        "trigger": "loud_spike",
        "score": 0.85,
        "source": "loudness_detector",
    }
    # Profanity result with status 'skipped' and 0 detections
    profanity_skipped_payload = {
        "provenance": {"stage": "profanity_detector", "status": "skipped", "failure_reason": "No subtitles and STT disabled"},
        "detections": [],
    }

    schema = load_schema(DEFAULT_SCHEMA_PATH)
    index_data = package_cues(
        raw_data=[loudness_cue],
        film_id="multi_test",
        duration_seconds=30.0,
        fps=24.0,
        schema=schema,
        trigger_coverage={"profanity": "skipped"},
    )

    assert len(index_data["cues"]) == 1
    assert index_data["cues"][0]["trigger"] == "loud_spike"
    assert index_data["trigger_coverage"]["profanity"] == "skipped"


# -----------------------------------------------------------------------------
# Test 20: Existing player action resolution remains unchanged
# -----------------------------------------------------------------------------
def test_20_existing_player_action_resolution_unchanged(dummy_audio_file: Path, wordlist_file: Path):
    segments = [
        {"id": 1, "start": 3.0, "end": 5.0, "text": "What the hell."}
    ]
    res = detect_profanity_with_fallback(
        srt_path=None,
        audio_path=dummy_audio_file,
        wordlist_path=wordlist_file,
        enable_stt=True,
        transcriber_fn=lambda _: segments,
    )
    schema = load_schema(DEFAULT_SCHEMA_PATH)
    index_data = package_cues(
        raw_data=res["detections"],
        film_id="action_test",
        duration_seconds=20.0,
        fps=24.0,
        schema=schema,
    )
    cue = index_data["cues"][0]
    assert cue["trigger"] == "profanity"
    assert cue["score"] == 1.0
    assert cue["source"] == "profanity_detector"
    assert cue["start"] == 3.0
    assert cue["end"] == 5.0
    # Verifies standard deterministic cue ID convention
    assert cue["id"] == "cue_action_test_profanity_003000_005000"
