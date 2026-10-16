"""Unit tests for the SRT subtitle parser, cue classifier, and profanity matching module."""

import tempfile
from pathlib import Path
import pytest
import pysrt

from scripts.parse_subtitles import (
    SubtitleParseError,
    classify_cue_text,
    classify_cue,
    cues_to_dicts,
    export_cues_json,
    export_detections_json,
    generate_subtitles_provenance,
    get_pysrt_version,
    load_wordlist,
    match_profanity_in_cues,
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
        assert sub_file[2].kind == "speech"
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
            "flagged_words": [],
        }
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_load_wordlist_success():
    """Verify loading wordlist ignores comments and blanks and normalizes terms."""
    content = """# Header comment
# License: CC BY 4.0

damn
HELL
  crap
# inline comment
bastard
"""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(content)
        temp_path = f.name

    try:
        words = load_wordlist(temp_path)
        assert words == ["damn", "hell", "crap", "bastard"]
    finally:
        Path(temp_path).unlink(missing_ok=True)


def test_load_wordlist_file_not_found():
    """Verify FileNotFoundError is raised when wordlist file is missing."""
    with pytest.raises(FileNotFoundError, match="Profanity word list file not found"):
        load_wordlist("non_existent_wordlist.txt")


def test_match_profanity_whole_word_boundaries():
    """Verify regex word boundaries match whole words only (ass vs class, crap vs scraper)."""
    sub_file = pysrt.SubRipFile([
        pysrt.SubRipItem(
            index=1,
            start=pysrt.SubRipTime(seconds=1),
            end=pysrt.SubRipTime(seconds=3),
            text="He is an ass, but in class he shows classic skills.",
        ),
        pysrt.SubRipItem(
            index=2,
            start=pysrt.SubRipTime(seconds=4),
            end=pysrt.SubRipTime(seconds=6),
            text="Use the scraper to clean this crap.",
        ),
    ])

    word_list = ["ass", "crap"]
    detections = match_profanity_in_cues(sub_file, word_list)

    # Cue 1: should match 'ass' once, not 'class' or 'classic'
    assert len(sub_file[0].flagged) == 1
    flagged1 = sub_file[0].flagged[0]
    assert flagged1["term_id"] == "w1"
    matched_word1 = sub_file[0].text[flagged1["char_start"]:flagged1["char_end"]]
    assert matched_word1 == "ass"

    # Cue 2: should match 'crap' once, not 'scraper'
    assert len(sub_file[1].flagged) == 1
    flagged2 = sub_file[1].flagged[0]
    matched_word2 = sub_file[1].text[flagged2["char_start"]:flagged2["char_end"]]
    assert matched_word2 == "crap"

    assert len(detections) == 2


def test_match_profanity_case_and_punctuation():
    """Verify matching handles uppercase, lowercase, mixed case, and punctuation."""
    sub_file = pysrt.SubRipFile([
        pysrt.SubRipItem(
            index=1,
            start=pysrt.SubRipTime(seconds=1),
            end=pysrt.SubRipTime(seconds=4),
            text='What the "HELL"?! (Damn)... That was DAMN close!',
        )
    ])

    word_list = ["hell", "damn"]
    match_profanity_in_cues(sub_file, word_list)

    assert len(sub_file[0].flagged) == 3
    matched_terms = [
        sub_file[0].text[f["char_start"]:f["char_end"]]
        for f in sub_file[0].flagged
    ]
    assert matched_terms == ["HELL", "Damn", "DAMN"]


def test_match_profanity_required_metadata_labels():
    """Verify evidence='subtitle_text', timing='estimated', spoken_confirmed=None."""
    sub_file = pysrt.SubRipFile([
        pysrt.SubRipItem(
            index=1,
            start=pysrt.SubRipTime(seconds=10),
            end=pysrt.SubRipTime(seconds=12),
            text="Get down, damn it!",
        )
    ])

    word_list = ["damn"]
    match_profanity_in_cues(sub_file, word_list)

    flagged = sub_file[0].flagged[0]
    assert flagged["evidence"] == "subtitle_text"
    assert flagged["timing"] == "estimated"
    assert flagged["spoken_confirmed"] is None
    assert flagged["confidence"] == 1.0
    assert 10.0 <= flagged["start"] <= flagged["end"] <= 12.0


def test_match_profanity_skips_sound_descriptions():
    """Verify sound description cues are NOT flagged as profanity."""
    sub_file = pysrt.SubRipFile([
        pysrt.SubRipItem(
            index=1,
            start=pysrt.SubRipTime(seconds=1),
            end=pysrt.SubRipTime(seconds=3),
            text="[damn loud music]",
        )
    ])

    classify_cue(sub_file[0])
    assert sub_file[0].kind == "sound_description"

    word_list = ["damn"]
    detections = match_profanity_in_cues(sub_file, word_list)

    assert len(sub_file[0].flagged) == 0
    assert len(detections) == 0


def test_match_profanity_integration_with_default_wordlist():
    """Verify end-to-end integration with data/profanity_wordlist.txt."""
    default_wordlist_path = Path("data/profanity_wordlist.txt")
    assert default_wordlist_path.is_file(), "data/profanity_wordlist.txt must exist"

    content = """1
00:00:01,000 --> 00:00:03,000
[music playing]

2
00:00:04,000 --> 00:00:06,000
This is total bullshit!
"""
    with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False, encoding="utf-8") as f:
        f.write(content)
        temp_srt = f.name

    try:
        sub_file = parse_srt_file(temp_srt, wordlist_path=default_wordlist_path)
        assert len(sub_file) == 2
        assert len(sub_file[0].flagged) == 0

        assert len(sub_file[1].flagged) == 1
        assert sub_file[1].flagged[0]["evidence"] == "subtitle_text"
        assert sub_file[1].flagged[0]["timing"] == "estimated"
        assert sub_file[1].flagged[0]["spoken_confirmed"] is None

        dicts = cues_to_dicts(sub_file)
        assert "flagged" not in dicts[0]
        assert "flagged" in dicts[1]
    finally:
        Path(temp_srt).unlink(missing_ok=True)


def test_export_cues_json():
    """Verify export_cues_json creates formatted JSON with required cue fields and flagged_words."""
    sub_file = pysrt.SubRipFile([
        pysrt.SubRipItem(
            index=1,
            start=pysrt.SubRipTime(seconds=5),
            end=pysrt.SubRipTime(seconds=8),
            text="Get down, damn it!",
        ),
        pysrt.SubRipItem(
            index=2,
            start=pysrt.SubRipTime(seconds=9),
            end=pysrt.SubRipTime(seconds=11),
            text="[gunfire in distance]",
        ),
    ])

    word_list = ["damn"]
    match_profanity_in_cues(sub_file, word_list)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        out_json = f.name

    try:
        exported = export_cues_json(sub_file, out_json)
        assert len(exported) == 2

        # Cue 1 (speech with profanity)
        c1 = exported[0]
        assert c1["id"] == "c1"
        assert c1["start"] == 5.0
        assert c1["end"] == 8.0
        assert c1["kind"] == "speech"
        assert c1["text"] == "Get down, damn it!"
        assert len(c1["flagged_words"]) == 1

        fw = c1["flagged_words"][0]
        assert fw["word"] == "damn"
        assert fw["char_start"] == 10
        assert fw["char_end"] == 14
        assert fw["evidence"] == "subtitle_text"
        assert fw["timing"] == "estimated"
        assert fw["spoken_confirmed"] is None

        # Cue 2 (sound description, clean)
        c2 = exported[1]
        assert c2["id"] == "c2"
        assert c2["kind"] == "sound_description"
        assert c2["flagged_words"] == []
    finally:
        Path(out_json).unlink(missing_ok=True)


def test_export_detections_json():
    """Verify export_detections_json produces shared detector contract with provenance."""
    sub_file = pysrt.SubRipFile([
        pysrt.SubRipItem(
            index=1,
            start=pysrt.SubRipTime(seconds=12),
            end=pysrt.SubRipTime(seconds=15),
            text="This is complete bullshit.",
        ),
        pysrt.SubRipItem(
            index=2,
            start=pysrt.SubRipTime(seconds=16),
            end=pysrt.SubRipTime(seconds=18),
            text="Everything is fine.",
        ),
    ])

    word_list = ["bullshit"]
    match_profanity_in_cues(sub_file, word_list)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        out_json = f.name

    try:
        payload = export_detections_json(
            sub_file,
            out_json,
            source="subtitles",
            source_file="sample.srt",
            wordlist_path="data/profanity_wordlist.txt",
        )

        assert "provenance" in payload
        assert "detections" in payload

        # Provenance check
        prov = payload["provenance"]
        assert prov["stage"] == "subtitles"
        assert prov["status"] == "ok"
        assert "libs" in prov
        assert prov["libs"]["pysrt"] == get_pysrt_version()

        # Detections check: only flagged cues, timing covers full cue duration
        dets = payload["detections"]
        assert len(dets) == 1
        d = dets[0]
        assert d["start"] == 12.0
        assert d["end"] == 15.0
        assert d["trigger"] == "profanity"
        assert d["score"] == 1.0
        assert d["source"] == "subtitles"
    finally:
        Path(out_json).unlink(missing_ok=True)
