"""Unit tests for Trigger-Aware Player cue packaging pipeline (scripts/package_cues.py).

Verifies:
- Deterministic, collision-safe cue IDs
- Maximum segment duration policy and boundary conditions
- Raw detection validation and duration limits
- Grouping by (trigger, source) and no cross-trigger merging
- Byte-for-byte reproducibility and stability under input shuffling
- Atomic file writing and error handling
- CLI execution and exit codes
"""

from __future__ import annotations

import copy
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from scripts.package_cues import (
    CueIdCollisionError,
    adapt_subtitle_cues_to_raw_detections,
    atomic_write_json,
    generate_cue_id,
    merge_detections,
    package_and_save_index,
    package_cues,
)
from scripts.validate_config import CANONICAL_TRIGGER_IDS
from scripts.validate_index import load_index, validate_index

CUES_PATH = Path("data/cues.json")
INDEX_PATH = Path("data/index.json")
CONFIG_PATH = Path("config.yaml")
SCHEMA_PATH = Path("schema/index.schema.json")


# ------------------------------------------------------------------------------
# 1. Cue ID Determinism, Rounding, and Collision Safety (Concern 1)
# ------------------------------------------------------------------------------

def test_generate_cue_id_deterministic():
    """Verify identical input always generates the exact same cue ID."""
    id1 = generate_cue_id("sintel", "flashing", 10.2, 11.8)
    id2 = generate_cue_id("sintel", "flashing", 10.2, 11.8)
    assert id1 == "cue_sintel_flashing_010200_011800"
    assert id1 == id2


def test_generate_cue_id_millisecond_rounding():
    """Verify rounding rule from seconds to milliseconds."""
    # 10.2004s -> 10200ms
    # 11.8006s -> 11801ms
    cid = generate_cue_id("sintel", "loud_spike", 10.2004, 11.8006)
    assert cid == "cue_sintel_loud_spike_010200_011801"


def test_generate_cue_id_submillisecond_duration_safety():
    """Verify that when end > start but rounding would yield end_ms <= start_ms, end_ms is bumped."""
    # 1.0001s -> 1000ms; 1.0003s -> 1000ms. end_ms bumped to 1001ms.
    cid = generate_cue_id("sintel", "spider", 1.0001, 1.0003)
    assert cid == "cue_sintel_spider_001000_001001"


def test_generate_cue_id_rejects_invalid_timestamps():
    """Verify generate_cue_id rejects negative, non-finite, or inverted timestamps."""
    with pytest.raises(ValueError, match="non-negative"):
        generate_cue_id("sintel", "flashing", -1.0, 2.0)

    with pytest.raises(ValueError, match="strictly greater than start"):
        generate_cue_id("sintel", "flashing", 5.0, 5.0)

    with pytest.raises(ValueError, match="strictly greater than start"):
        generate_cue_id("sintel", "flashing", 5.0, 4.0)

    with pytest.raises(ValueError, match="finite"):
        generate_cue_id("sintel", "flashing", float("nan"), 10.0)

    with pytest.raises(ValueError, match="finite"):
        generate_cue_id("sintel", "flashing", 1.0, float("inf"))


def test_cue_id_collision_on_distinct_submillisecond_intervals():
    """Verify packaging distinct cues that round to the same millisecond raises CueIdCollisionError."""
    # Two distinct detections with sub-millisecond differences that round to the same millisecond:
    # 10.0001s and 10.0004s both round to 10000ms.
    raw_data = {
        "film_id": "sintel",
        "detections": [
            {"start": 10.0001, "end": 12.0001, "trigger": "gunshot", "score": 0.8, "source": "src1"},
            {"start": 10.0004, "end": 12.0004, "trigger": "gunshot", "score": 0.9, "source": "src2"},
        ],
    }
    # Because sources are different ("src1" vs "src2"), they do not merge.
    # Therefore, both reach ID generation and collide on cue_sintel_gunshot_010000_012000.
    with pytest.raises(CueIdCollisionError, match="Cue ID collision detected for ID"):
        package_cues(raw_data, film_id="sintel", duration_seconds=52.0)


# ------------------------------------------------------------------------------
# 2. Maximum Segment Duration and Merge Policy (Concern 2)
# ------------------------------------------------------------------------------

def test_merge_gap_exact_boundary():
    """Verify that detections separated by a gap exactly equal to merge_gap are merged."""
    dets = [
        {"start": 10.0, "end": 15.0, "trigger": "gunshot", "score": 0.7, "source": "audio_clf"},
        {"start": 17.5, "end": 20.0, "trigger": "gunshot", "score": 0.9, "source": "audio_clf"},
    ]
    # Gap is 17.5 - 15.0 = 2.5s == default merge_gap (2.5s)
    merged = merge_detections(dets, default_merge_gap=2.5, max_segment_duration=60.0)
    assert len(merged) == 1
    assert merged[0]["start"] == 10.0
    assert merged[0]["end"] == 20.0
    assert merged[0]["score"] == 0.9


def test_merge_gap_exceeded_not_merged():
    """Verify that detections separated by a gap > merge_gap are NOT merged."""
    dets = [
        {"start": 10.0, "end": 15.0, "trigger": "gunshot", "score": 0.7, "source": "audio_clf"},
        {"start": 17.51, "end": 20.0, "trigger": "gunshot", "score": 0.9, "source": "audio_clf"},
    ]
    # Gap is 17.51 - 15.0 = 2.51s > 2.5s
    merged = merge_detections(dets, default_merge_gap=2.5, max_segment_duration=60.0)
    assert len(merged) == 2
    assert merged[0]["start"] == 10.0
    assert merged[0]["end"] == 15.0
    assert merged[1]["start"] == 17.51
    assert merged[1]["end"] == 20.0


def test_merge_duration_exact_maximum():
    """Verify that a merged segment whose duration exactly equals max_segment_duration succeeds."""
    dets = [
        {"start": 0.0, "end": 30.0, "trigger": "flashing", "score": 0.8, "source": "flash_detector"},
        {"start": 32.0, "end": 60.0, "trigger": "flashing", "score": 0.85, "source": "flash_detector"},
    ]
    # Total span: 60.0 - 0.0 = 60.0s == max_segment_duration
    merged = merge_detections(dets, default_merge_gap=2.5, max_segment_duration=60.0)
    assert len(merged) == 1
    assert merged[0]["start"] == 0.0
    assert merged[0]["end"] == 60.0


def test_merge_duration_exceeded_splits_cleanly():
    """Verify that when merging would exceed max_segment_duration, the current segment is finalized."""
    dets = [
        {"start": 0.0, "end": 30.0, "trigger": "flashing", "score": 0.8, "source": "flash_detector"},
        {"start": 32.0, "end": 60.1, "trigger": "flashing", "score": 0.85, "source": "flash_detector"},
    ]
    # Total span would be 60.1 - 0.0 = 60.1s > 60.0s
    merged = merge_detections(dets, default_merge_gap=2.5, max_segment_duration=60.0)
    assert len(merged) == 2
    assert merged[0]["start"] == 0.0
    assert merged[0]["end"] == 30.0
    assert merged[1]["start"] == 32.0
    assert merged[1]["end"] == 60.1


def test_single_raw_detection_longer_than_maximum_rejected():
    """Verify that a single raw detection exceeding max_segment_duration is rejected with a validation error."""
    dets = [
        {"start": 0.0, "end": 65.0, "trigger": "spider", "score": 0.75, "source": "spider_detector"}
    ]
    with pytest.raises(ValueError, match="exceeds configured maximum allowable segment duration"):
        merge_detections(dets, max_segment_duration=60.0)


def test_overlapping_detections_merged():
    """Verify overlapping detections of the same trigger and source are merged into one span."""
    dets = [
        {"start": 10.0, "end": 15.0, "trigger": "explosion", "score": 0.6, "source": "audio_clf"},
        {"start": 12.0, "end": 18.0, "trigger": "explosion", "score": 0.95, "source": "audio_clf"},
    ]
    merged = merge_detections(dets, default_merge_gap=2.5, max_segment_duration=60.0)
    assert len(merged) == 1
    assert merged[0]["start"] == 10.0
    assert merged[0]["end"] == 18.0
    assert merged[0]["score"] == 0.95


def test_same_trigger_different_sources_not_merged():
    """Verify detections of the same trigger but different sources are NEVER merged."""
    dets = [
        {"start": 10.0, "end": 12.0, "trigger": "gunshot", "score": 0.8, "source": "detector_a"},
        {"start": 11.0, "end": 13.0, "trigger": "gunshot", "score": 0.9, "source": "detector_b"},
    ]
    merged = merge_detections(dets, default_merge_gap=2.5, max_segment_duration=60.0)
    assert len(merged) == 2
    sources = {m["source"] for m in merged}
    assert sources == {"detector_a", "detector_b"}


def test_different_triggers_same_source_not_merged():
    """Verify detections of different triggers from the same source are NEVER merged."""
    dets = [
        {"start": 10.0, "end": 15.0, "trigger": "gunshot", "score": 0.8, "source": "audio_clf"},
        {"start": 12.0, "end": 16.0, "trigger": "explosion", "score": 0.9, "source": "audio_clf"},
    ]
    merged = merge_detections(dets, default_merge_gap=2.5, max_segment_duration=60.0)
    assert len(merged) == 2
    triggers = {m["trigger"] for m in merged}
    assert triggers == {"gunshot", "explosion"}


def test_no_detections_lost_during_segmentation():
    """Verify all intervals are accounted for across chain of segments."""
    # Create 5 contiguous detections of 15 seconds each = 75 seconds total.
    # With max_duration=30s, this should split into 3 segments: [0-30], [30-60], [60-75]
    dets = [
        {"start": float(i * 15), "end": float((i + 1) * 15), "trigger": "scream", "score": 0.7, "source": "audio_clf"}
        for i in range(5)
    ]
    merged = merge_detections(dets, default_merge_gap=2.5, max_segment_duration=30.0)
    assert len(merged) == 3
    assert merged[0]["start"] == 0.0 and merged[0]["end"] == 30.0
    assert merged[1]["start"] == 30.0 and merged[1]["end"] == 60.0
    assert merged[2]["start"] == 60.0 and merged[2]["end"] == 75.0


# ------------------------------------------------------------------------------
# 3. Packaging Pipeline End-to-End Tests (Concern 6)
# ------------------------------------------------------------------------------

def test_package_cues_valid_fixture():
    """Verify packaging data/cues.json produces a valid index with all canonical triggers."""
    with CUES_PATH.open("r", encoding="utf-8") as f:
        raw_cues = json.load(f)

    index_data = package_cues(raw_cues, film_id="sintel", duration_seconds=52.0, fps=24.0)

    assert index_data["schema_version"] == 1
    assert index_data["film_id"] == "sintel"
    assert index_data["timeline"]["duration_seconds"] == 52.0
    assert index_data["timeline"]["fps"] == 24.0

    cues = index_data["cues"]
    assert len(cues) == 9
    represented_triggers = {c["trigger"] for c in cues}
    assert represented_triggers == set(CANONICAL_TRIGGER_IDS)


def test_package_cues_empty_list():
    """Verify that an empty raw detection list produces a valid index with empty cues array."""
    index_data = package_cues([], film_id="sintel", duration_seconds=52.0)
    assert index_data["cues"] == []
    assert index_data["film_id"] == "sintel"


def test_package_cues_output_stable_under_input_shuffling():
    """Verify that shuffling the raw input detections yields the exact same packaged result."""
    with CUES_PATH.open("r", encoding="utf-8") as f:
        raw_cues = json.load(f)

    detections = raw_cues["detections"]

    # Package standard order
    res1 = package_cues(detections, film_id="sintel", duration_seconds=52.0, fps=24.0)

    # Shuffle detections
    shuffled = copy.deepcopy(detections)
    random.seed(42)
    random.shuffle(shuffled)

    res2 = package_cues(shuffled, film_id="sintel", duration_seconds=52.0, fps=24.0)

    assert res1 == res2
    assert json.dumps(res1, sort_keys=True) == json.dumps(res2, sort_keys=True)


def test_package_cues_repeated_runs_identical_bytes():
    """Verify that repeated packaging runs produce byte-for-byte identical JSON serialization."""
    with CUES_PATH.open("r", encoding="utf-8") as f:
        raw_cues = json.load(f)

    res1 = package_cues(raw_cues, film_id="sintel", duration_seconds=52.0, fps=24.0)
    res2 = package_cues(raw_cues, film_id="sintel", duration_seconds=52.0, fps=24.0)

    bytes1 = json.dumps(res1, indent=2, allow_nan=False) + "\n"
    bytes2 = json.dumps(res2, indent=2, allow_nan=False) + "\n"

    assert bytes1 == bytes2


def test_package_cues_outside_timeline_rejected():
    """Verify that raw detections extending beyond declared duration fail packaging."""
    raw_data = {
        "film_id": "sintel",
        "detections": [
            {"start": 50.0, "end": 55.0, "trigger": "gunshot", "score": 0.8, "source": "audio_clf"}
        ],
    }
    with pytest.raises(ValueError, match="exceeds declared film duration"):
        package_cues(raw_data, film_id="sintel", duration_seconds=52.0)


def test_package_cues_unknown_trigger_rejected():
    """Verify that an unknown trigger in raw detections fails packaging."""
    raw_data = {
        "film_id": "sintel",
        "detections": [
            {"start": 10.0, "end": 12.0, "trigger": "alien_roar", "score": 0.8, "source": "audio_clf"}
        ],
    }
    with pytest.raises(ValueError, match="not recognized in canonical triggers"):
        package_cues(raw_data, film_id="sintel", duration_seconds=52.0)


def test_package_cues_invalid_score_rejected():
    """Verify that an invalid score in raw detections fails packaging."""
    for bad_score in [-0.5, 1.5, float("nan")]:
        raw_data = {
            "film_id": "sintel",
            "detections": [
                {"start": 10.0, "end": 12.0, "trigger": "scream", "score": bad_score, "source": "audio_clf"}
            ],
        }
        with pytest.raises(ValueError, match="score"):
            package_cues(raw_data, film_id="sintel", duration_seconds=52.0)


def test_atomic_write_does_not_corrupt_on_failure(tmp_path):
    """Verify that a failure during packaging does not overwrite or corrupt an existing target file."""
    target_file = tmp_path / "index.json"
    target_file.write_text('{"existing_valid": true}\n', encoding="utf-8")

    bad_raw_file = tmp_path / "bad_cues.json"
    bad_raw_file.write_text(
        json.dumps({
            "detections": [
                {"start": 10.0, "end": 5.0, "trigger": "flashing", "score": 0.5, "source": "src"}  # Invalid end < start
            ]
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        package_and_save_index(input_path=bad_raw_file, output_path=target_file)

    # Existing file must remain completely untouched!
    assert target_file.read_text(encoding="utf-8") == '{"existing_valid": true}\n'


def test_package_cues_cli_success(tmp_path):
    """Verify scripts/package_cues.py CLI execution succeeds and outputs valid index."""
    output_file = tmp_path / "cli_packaged_index.json"

    res = subprocess.run(
        [
            sys.executable,
            "scripts/package_cues.py",
            "--input",
            str(CUES_PATH),
            "--output",
            str(output_file),
            "--film-id",
            "sintel",
            "--duration",
            "52.0",
            "--fps",
            "24.0",
        ],
        capture_output=True,
        text=True,
    )

    assert res.returncode == 0, f"CLI packaging failed: {res.stderr}\n{res.stdout}"
    assert "SUCCESS" in res.stdout
    assert output_file.is_file()

    # Validate output with validate_index
    is_valid, errors = validate_index(output_file, SCHEMA_PATH, CONFIG_PATH)
    assert is_valid, f"Packaged output index failed validation: {errors}"


def test_package_cues_cli_failure_on_invalid_input(tmp_path):
    """Verify scripts/package_cues.py CLI returns exit code 1 on invalid input."""
    bad_cues = tmp_path / "bad_cues.json"
    bad_cues.write_text(json.dumps({"detections": "not_a_list"}), encoding="utf-8")
    output_file = tmp_path / "failed_index.json"

    res = subprocess.run(
        [
            sys.executable,
            "scripts/package_cues.py",
            "--input",
            str(bad_cues),
            "--output",
            str(output_file),
        ],
        capture_output=True,
        text=True,
    )

    assert res.returncode == 1
    assert "FAILURE" in res.stderr or "FAILURE" in res.stdout


# ------------------------------------------------------------------------------
# 7. Exact Adjacency, Score Aggregation, and Subtitle Adaptation Regression Tests
# ------------------------------------------------------------------------------

def test_merge_exact_adjacency():
    """Verify that exactly adjacent intervals [10, 15) and [15, 20) merge cleanly into [10, 20)."""
    detections = [
        {"start": 10.0, "end": 15.0, "trigger": "loud_spike", "score": 0.8, "source": "loudness_detector"},
        {"start": 15.0, "end": 20.0, "trigger": "loud_spike", "score": 0.9, "source": "loudness_detector"},
    ]
    merged = merge_detections(detections, max_segment_duration=30.0, default_merge_gap=2.5)
    assert len(merged) == 1
    assert merged[0]["start"] == 10.0
    assert merged[0]["end"] == 20.0
    assert merged[0]["score"] == 0.9
    assert merged[0]["trigger"] == "loud_spike"
    assert merged[0]["source"] == "loudness_detector"


def test_merge_score_aggregation_max_score():
    """Verify that score aggregation retains the maximum score across merged intervals."""
    detections = [
        {"start": 1.0, "end": 3.0, "trigger": "profanity", "score": 0.65, "source": "subtitles"},
        {"start": 4.0, "end": 6.0, "trigger": "profanity", "score": 0.98, "source": "subtitles"},
        {"start": 7.0, "end": 8.0, "trigger": "profanity", "score": 0.40, "source": "subtitles"},
    ]
    # merge gap 2.0 covers gap 1.0 (between 3 and 4) and gap 1.0 (between 6 and 7)
    merged = merge_detections(detections, max_segment_duration=30.0, default_merge_gap=2.0)
    assert len(merged) == 1
    assert merged[0]["start"] == 1.0
    assert merged[0]["end"] == 8.0
    assert merged[0]["score"] == 0.98


def test_adapt_subtitle_cues_with_flagged_words():
    """Verify adapt_subtitle_cues_to_raw_detections converts speech cues with flagged words and ignores clean dialogue."""
    cues_data = [
        {
            "id": "c1",
            "start": 1.0,
            "end": 3.5,
            "kind": "speech",
            "text": "What the fuck is happening?",
            "flagged_words": [
                {"word": "fuck", "char_start": 9, "char_end": 13, "evidence": "subtitle_text", "timing": "estimated"}
            ],
        },
        {
            "id": "c2",
            "start": 4.0,
            "end": 6.0,
            "kind": "sound_description",
            "text": "[door slams]",
            "flagged_words": [],
        },
        {
            "id": "c3",
            "start": 7.0,
            "end": 9.5,
            "kind": "speech",
            "text": "Clean dialogue here with no bad words.",
            "flagged_words": [],
        },
        {
            "id": "c4",
            "start": 10.5,
            "end": 12.0,
            "kind": "speech",
            "text": "Damn it!",
            "flagged_words": [
                {"word": "damn", "char_start": 0, "char_end": 4, "evidence": "subtitle_text", "timing": "estimated"}
            ],
        },
    ]

    adapted = adapt_subtitle_cues_to_raw_detections(cues_data, default_source="subtitles")
    assert len(adapted) == 2

    assert adapted[0]["start"] == 1.0
    assert adapted[0]["end"] == 3.5
    assert adapted[0]["trigger"] == "profanity"
    assert adapted[0]["score"] == 1.0
    assert adapted[0]["source"] == "subtitles"

    assert adapted[1]["start"] == 10.5
    assert adapted[1]["end"] == 12.0
    assert adapted[1]["trigger"] == "profanity"
    assert adapted[1]["score"] == 1.0
    assert adapted[1]["source"] == "subtitles"


def test_adapt_subtitle_cues_detections_json_format():
    """Verify adapt_subtitle_cues_to_raw_detections handles export_detections_json structure."""
    detections_payload = {
        "provenance": {"stage": "subtitles", "status": "ok"},
        "detections": [
            {"start": 2.0, "end": 4.5, "trigger": "profanity", "score": 1.0, "source": "subtitles"},
            {"start": 8.0, "end": 10.0, "trigger": "profanity", "score": 0.95, "source": "subtitles"},
        ],
    }
    adapted = adapt_subtitle_cues_to_raw_detections(detections_payload)
    assert len(adapted) == 2
    assert adapted[0]["start"] == 2.0
    assert adapted[0]["end"] == 4.5
    assert adapted[0]["trigger"] == "profanity"
    assert adapted[0]["score"] == 1.0
    assert adapted[1]["score"] == 0.95


def test_adapt_subtitle_cues_non_destructive():
    """Verify adapt_subtitle_cues_to_raw_detections does not mutate input dictionaries or lists."""
    cues_data = [
        {
            "id": "c1",
            "start": 1.5,
            "end": 3.0,
            "kind": "speech",
            "text": "Shit!",
            "flagged_words": [{"word": "shit", "char_start": 0, "char_end": 4}],
        }
    ]
    original_copy = copy.deepcopy(cues_data)

    adapted = adapt_subtitle_cues_to_raw_detections(cues_data)
    assert len(adapted) == 1

    # Verify input is unchanged
    assert cues_data == original_copy


def test_adapt_subtitle_cues_invalid_input_rejected():
    """Verify adapt_subtitle_cues_to_raw_detections rejects non-numeric or inverted timestamps."""
    with pytest.raises(ValueError, match="non-numeric"):
        adapt_subtitle_cues_to_raw_detections([
            {"start": "not_a_number", "end": 5.0, "trigger": "profanity"}
        ])

    with pytest.raises(ValueError, match="invalid interval"):
        adapt_subtitle_cues_to_raw_detections([
            {"start": 5.0, "end": 3.0, "trigger": "profanity"}
        ])

    with pytest.raises(ValueError, match="must be a dictionary or list"):
        adapt_subtitle_cues_to_raw_detections("invalid_string_input")  # type: ignore


def test_package_cues_with_subtitles_integration(tmp_path):
    """Verify package_cues cleanly integrates audio spikes and subtitle profanity cues."""
    raw_audio_detections = {
        "film_id": "speech_test",
        "timeline": {"duration_seconds": 20.0, "fps": 24.0},
        "detections": [
            {"start": 4.0, "end": 5.0, "trigger": "loud_spike", "score": 0.85, "source": "loudness_detector"},
        ],
    }

    subtitles_cues = [
        {
            "id": "c1",
            "start": 1.0,
            "end": 3.0,
            "kind": "speech",
            "text": "Watch your mouth!",
            "flagged_words": [{"word": "mouth", "char_start": 11, "char_end": 16}],
        },
        {
            "id": "c2",
            "start": 8.0,
            "end": 10.0,
            "kind": "speech",
            "text": "Get out of here!",
            "flagged_words": [{"word": "hell", "char_start": 0, "char_end": 4}],
        },
    ]

    index_data = package_cues(
        raw_data=raw_audio_detections,
        subtitles=subtitles_cues,
        film_id="speech_test",
        duration_seconds=20.0,
        fps=24.0,
    )

    cues = index_data["cues"]
    assert len(cues) == 3

    # Verify chronological ordering
    assert cues[0]["start"] == 1.0
    assert cues[0]["trigger"] == "profanity"
    assert cues[0]["id"] == "cue_speech_test_profanity_001000_003000"

    assert cues[1]["start"] == 4.0
    assert cues[1]["trigger"] == "loud_spike"
    assert cues[1]["id"] == "cue_speech_test_loud_spike_004000_005000"

    assert cues[2]["start"] == 8.0
    assert cues[2]["trigger"] == "profanity"
    assert cues[2]["id"] == "cue_speech_test_profanity_008000_010000"

    # Validate output against schema
    index_file = tmp_path / "index.json"
    atomic_write_json(index_data, index_file)
    is_valid, errors = validate_index(index_file, SCHEMA_PATH, CONFIG_PATH)
    assert is_valid, f"Validation errors: {errors}"


def test_package_cues_cli_with_subtitles(tmp_path):
    """Verify package_cues CLI accepts --subtitles and packages both sources."""
    audio_cues_file = tmp_path / "audio_cues.json"
    audio_cues_file.write_text(
        json.dumps({
            "film_id": "cli_test",
            "detections": [
                {"start": 5.0, "end": 7.0, "trigger": "loud_spike", "score": 0.9, "source": "loudness_detector"}
            ],
        }),
        encoding="utf-8",
    )

    subtitles_file = tmp_path / "subtitles_cues.json"
    subtitles_file.write_text(
        json.dumps({
            "detections": [
                {"start": 1.0, "end": 3.0, "trigger": "profanity", "score": 1.0, "source": "subtitles"}
            ]
        }),
        encoding="utf-8",
    )

    output_file = tmp_path / "packaged_index.json"

    res = subprocess.run(
        [
            sys.executable,
            "scripts/package_cues.py",
            "--input",
            str(audio_cues_file),
            "--subtitles",
            str(subtitles_file),
            "--output",
            str(output_file),
            "--film-id",
            "cli_test",
            "--duration",
            "15.0",
            "--fps",
            "24.0",
        ],
        capture_output=True,
        text=True,
    )

    assert res.returncode == 0, f"CLI error: {res.stderr}\n{res.stdout}"
    assert "SUCCESS: Packaged 2 cue(s)" in res.stdout
    assert output_file.is_file()

    with output_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["cues"]) == 2
    triggers = [c["trigger"] for c in data["cues"]]
    assert triggers == ["profanity", "loud_spike"]
