"""Unit tests for the SRT subtitle parser and cue classifier module."""

import tempfile
from pathlib import Path
import pytest
import pysrt

from scripts.parse_subtitles import (
    SubtitleParseError,
    classify_cue_text,
    classify_cue,
    cues_to_dicts,
    parse_srt_file,
    subrip_time_to_seconds,
)


def test_classify_cue_text_sound_descriptions():
    """Verify bracketed and parenthesized texts are classified as sound_description."""
    assert classify_cue_text("[gunshots]") == "sound_description"
    assert classify_cue_text("(sighs)") == "sound_description"
    assert classify_cue_text("[Music playing]") == "sound_description"
    assert classify_cue_text("  [applause]  ") == "sound_description"
    assert classify_cue_text("<i>[dramatic music]</i>") == "sound_description"
    assert classify_cue_text("[screams] (groans)") == "sound_description"
    assert classify_cue_text("(CROWD CHEERING)") == "sound_description"


def test_classify_cue_text_speech():
    """Verify spoken dialogue is classified as speech."""
    assert classify_cue_text("Hello, how are you?") == "speech"
    assert classify_cue_text("Watch out! [gunshot]") == "speech"
    assert classify_cue_text("(Sighs) I didn't mean that.") == "speech"
    assert classify_cue_text("Are you sure? (whispering)") == "speech"
    assert classify_cue_text("Yes, definitely.") == "speech"
    assert classify_cue_text("") == "speech"


def test_classify_cue_attaches_kind_attribute():
    """Verify that classify_cue sets the kind attribute directly on SubRipItem."""
    item = pysrt.SubRipItem(
        index=1,
        start=pysrt.SubRipTime(seconds=1),
        end=pysrt.SubRipTime(seconds=3),
        text="[explosion]",
    )
    result = classify_cue(item)
    assert hasattr(result, "kind")
    assert result.kind == "sound_description"

    speech_item = pysrt.SubRipItem(
        index=2,
        start=pysrt.SubRipTime(seconds=4),
        end=pysrt.SubRipTime(seconds=6),
        text="Run for your lives!",
    )
    classify_cue(speech_item)
    assert speech_item.kind == "speech"


def test_parse_valid_srt_file():
    """Verify parsing a valid SRT file produces cues with kind attribute."""
    content = """1
00:00:01,000 --> 00:00:03,500
[Dramatic music starts]

2
00:00:04,000 --> 00:00:06,200
Did you hear that?

3
00:00:06,500 --> 00:00:08,000
(whispers) Yes, be quiet.

4
00:00:08,500 --> 00:00:10,000
[gunshots]
"""
    with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False, encoding="utf-8") as f:
        f.write(content)
        temp_path = f.name

    try:
        sub_file = parse_srt_file(temp_path)
        assert len(sub_file) == 4

        assert sub_file[0].kind == "sound_description"
        assert sub_file[1].kind == "speech"
        assert sub_file[2].kind == "speech"  # Contains spoken dialogue alongside parenthesis
        assert sub_file[3].kind == "sound_description"

        # Verify time conversion
        assert subrip_time_to_seconds(sub_file[0].start) == 1.0
        assert subrip_time_to_seconds(sub_file[0].end) == 3.5

        # Verify dict export
        dicts = cues_to_dicts(sub_file)
        assert len(dicts) == 4
        assert dicts[0] == {
            "id": "c1",
            "start": 1.0,
            "end": 3.5,
            "kind": "sound_description",
            "text": "[Dramatic music starts]",
        }
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_parse_empty_srt_file():
    """Verify empty SRT file returns empty SubRipFile without errors."""
    with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False, encoding="utf-8") as f:
        f.write("")
        temp_path = f.name

    try:
        sub_file = parse_srt_file(temp_path)
        assert len(sub_file) == 0
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_error_missing_file():
    """Verify FileNotFoundError is raised for non-existent files."""
    with pytest.raises(FileNotFoundError, match="Subtitle file not found"):
        parse_srt_file("non_existent_file_xyz.srt")


def test_error_invalid_extension():
    """Verify ValueError is raised if file is not an .srt file."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("Some text")
        temp_path = f.name

    try:
        with pytest.raises(ValueError, match="Expected a file with '.srt' extension"):
            parse_srt_file(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_error_invalid_time_interval():
    """Verify SubtitleParseError is raised when cue end is before start."""
    corrupt_content = """1
00:00:10,000 --> 00:00:05,000
Backwards in time
"""
    with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False, encoding="utf-8") as f:
        f.write(corrupt_content)
        temp_path = f.name

    try:
        with pytest.raises(SubtitleParseError, match="end .* is before start"):
            parse_srt_file(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)
