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
from scripts.validate_index import (
    load_config_data,
    load_index,
    load_schema,
    validate_index,
)

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
    assert represented_triggers.issubset(set(CANONICAL_TRIGGER_IDS))


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


# ------------------------------------------------------------------------------
# 8. Multi-Detector Packaging, Provenance, & Synthetic Guardrails (Person B)
# ------------------------------------------------------------------------------

def test_multi_detector_packaging_preserves_provenance_and_intervals(tmp_path: Path):
    """Verify packaging multiple detector outputs preserves canonical triggers, seconds, half-open intervals, scores, and source provenance."""
    multi_detector_raw = {
        "film_id": "synthetic_multi_film",
        "synthetic": True,
        "description": "Explicitly synthetic multi-detector test fixture.",
        "timeline": {"duration_seconds": 30.0, "fps": 24.0},
        "detections": [
            {"start": 1.0, "end": 2.5, "trigger": "loud_spike", "score": 0.85, "source": "loudness_detector"},
            {"start": 2.0, "end": 4.0, "trigger": "flashing", "score": 0.95, "source": "flash_detector"},
            {"start": 5.0, "end": 6.0, "trigger": "gunshot", "score": 0.88, "source": "audio_event_classifier"},
            {"start": 5.5, "end": 7.0, "trigger": "scream", "score": 0.75, "source": "audio_event_classifier"},
        ],
    }

    subtitles_raw = [
        {
            "id": "c1",
            "start": 10.0,
            "end": 12.0,
            "kind": "speech",
            "text": "What the hell!",
            "flagged_words": [{"word": "hell", "char_start": 9, "char_end": 13}],
        }
    ]

    index_data = package_cues(
        raw_data=multi_detector_raw,
        subtitles=subtitles_raw,
        film_id="synthetic_multi_film",
        duration_seconds=30.0,
        fps=24.0,
        synthetic=True,
    )

    # 1. Structural properties
    assert index_data["synthetic"] is True
    assert index_data["film_id"] == "synthetic_multi_film"
    assert index_data["timeline"]["duration_seconds"] == 30.0
    assert index_data["timeline"]["timestamp_unit"] == "seconds"
    assert index_data["timeline"]["interval_convention"] == "half_open"

    cues = index_data["cues"]
    assert len(cues) == 5

    # 2. Check each cue adheres to the contract and preserves provenance
    expected_cues = [
        {"start": 1.0, "end": 2.5, "trigger": "loud_spike", "source": "loudness_detector", "score": 0.85},
        {"start": 2.0, "end": 4.0, "trigger": "flashing", "source": "flash_detector", "score": 0.95},
        {"start": 5.0, "end": 6.0, "trigger": "gunshot", "source": "audio_event_classifier", "score": 0.88},
        {"start": 5.5, "end": 7.0, "trigger": "scream", "source": "audio_event_classifier", "score": 0.75},
        {"start": 10.0, "end": 12.0, "trigger": "profanity", "source": "profanity_detector", "score": 1.0},
    ]

    for cue, exp in zip(cues, expected_cues):
        assert cue["start"] == exp["start"]
        assert cue["end"] == exp["end"]
        assert cue["end"] > cue["start"]
        assert cue["trigger"] == exp["trigger"]
        assert cue["trigger"] in CANONICAL_TRIGGER_IDS
        assert cue["source"] == exp["source"]
        assert cue["score"] == pytest.approx(exp["score"])
        expected_id = generate_cue_id("synthetic_multi_film", exp["trigger"], exp["start"], exp["end"])
        assert cue["id"] == expected_id

    # 3. Chronological sorting verified
    starts = [c["start"] for c in cues]
    assert starts == sorted(starts)

    # 4. Overlapping intervals of different triggers/sources were NOT cross-merged
    # loud_spike [1.0, 2.5) overlaps with flashing [2.0, 4.0), both remain distinct
    assert cues[0]["trigger"] == "loud_spike"
    assert cues[1]["trigger"] == "flashing"

    # gunshot [5.0, 6.0) overlaps with scream [5.5, 7.0), both remain distinct
    assert cues[2]["trigger"] == "gunshot"
    assert cues[3]["trigger"] == "scream"

    # 5. Schema and domain validation
    out_file = tmp_path / "multi_detector_index.json"
    atomic_write_json(index_data, out_file)
    is_valid, errors = validate_index(out_file, SCHEMA_PATH, CONFIG_PATH)
    assert is_valid, f"Validation errors: {errors}"


def test_multi_detector_same_trigger_different_sources_preserves_both(tmp_path: Path):
    """Verify when multiple detectors produce the same trigger with different sources, both are preserved with distinct source provenance."""
    raw_detections = {
        "film_id": "source_provenance_test",
        "synthetic": True,
        "timeline": {"duration_seconds": 20.0},
        "detections": [
            {"start": 2.0, "end": 4.0, "trigger": "loud_spike", "score": 1.0, "source": "loudness_detector"},
            {"start": 2.5, "end": 4.5, "trigger": "loud_spike", "score": 0.9, "source": "external_sensor"},
        ],
    }

    index_data = package_cues(
        raw_data=raw_detections,
        film_id="source_provenance_test",
        duration_seconds=20.0,
        synthetic=True,
    )

    cues = index_data["cues"]
    assert len(cues) == 2
    sources = {c["source"] for c in cues}
    assert sources == {"loudness_detector", "external_sensor"}

    # Verify unique deterministic IDs despite overlapping time
    ids = [c["id"] for c in cues]
    assert len(set(ids)) == 2


def test_legacy_subtitles_source_normalized_to_canonical_profanity_detector():
    """Verify legacy 'subtitles' source is explicitly normalized to canonical 'profanity_detector'."""
    raw_detections = {
        "film_id": "norm_test",
        "synthetic": True,
        "timeline": {"duration_seconds": 10.0},
        "detections": [
            {"start": 1.0, "end": 3.0, "trigger": "profanity", "score": 0.95, "source": "subtitles"},
        ],
    }
    index_data = package_cues(
        raw_data=raw_detections,
        film_id="norm_test",
        duration_seconds=10.0,
        synthetic=True,
    )
    assert len(index_data["cues"]) == 1
    assert index_data["cues"][0]["source"] == "profanity_detector"


def test_multi_detector_synthetic_flag_explicit():
    """Verify synthetic fixtures are explicitly marked synthetic=True, avoiding mislabeling as real detections."""
    raw_synthetic = {
        "film_id": "test_synth",
        "synthetic": True,
        "timeline": {"duration_seconds": 10.0},
        "detections": [
            {"start": 1.0, "end": 2.0, "trigger": "loud_spike", "score": 0.8, "source": "loudness_detector"}
        ],
    }
    index_data = package_cues(raw_synthetic, film_id="test_synth", duration_seconds=10.0, synthetic=True)
    assert index_data["synthetic"] is True

    # Real smoke test raw detections have synthetic: false
    smoke_raw_path = Path("data/processed/smoke_test/raw_detections.json")
    if smoke_raw_path.is_file():
        with smoke_raw_path.open("r", encoding="utf-8") as f:
            smoke_raw = json.load(f)
        assert smoke_raw.get("synthetic") is False


def test_multi_detector_timeline_duration_bounds_rejected():
    """Verify detector cues extending beyond the media duration are strictly rejected."""
    raw_out_of_bounds = {
        "film_id": "out_of_bounds_test",
        "synthetic": True,
        "timeline": {"duration_seconds": 10.0},
        "detections": [
            {"start": 8.0, "end": 12.0, "trigger": "loud_spike", "score": 0.9, "source": "loudness_detector"}
        ],
    }
    with pytest.raises(ValueError, match="Packaged index failed domain business rules"):
        package_cues(raw_out_of_bounds, film_id="out_of_bounds_test", duration_seconds=10.0, synthetic=True)


def test_multi_detector_cli_multiple_inputs_combines_loudness_and_profanity(tmp_path: Path):
    """Verify CLI accepts multiple raw detector JSON files via --input and packages both loudness and profanity."""
    loudness_file = tmp_path / "loudness_detections.json"
    loudness_file.write_text(
        json.dumps({
            "film_id": "unified_test_film",
            "synthetic": True,
            "timeline": {"duration_seconds": 25.0, "fps": 24.0},
            "detections": [
                {"start": 3.0, "end": 5.0, "trigger": "loud_spike", "score": 0.88, "source": "loudness_detector"},
                {"start": 12.0, "end": 14.0, "trigger": "loud_spike", "score": 0.92, "source": "loudness_detector"},
            ],
        }),
        encoding="utf-8",
    )

    profanity_file = tmp_path / "profanity_detections.json"
    profanity_file.write_text(
        json.dumps({
            "film_id": "unified_test_film",
            "synthetic": True,
            "timeline": {"duration_seconds": 25.0, "fps": 24.0},
            "detections": [
                {"start": 4.0, "end": 6.5, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
            ],
        }),
        encoding="utf-8",
    )

    output_file = tmp_path / "combined_index.json"

    res = subprocess.run(
        [
            sys.executable,
            "scripts/package_cues.py",
            "--input",
            str(loudness_file),
            str(profanity_file),
            "--output",
            str(output_file),
            "--film-id",
            "unified_test_film",
            "--duration",
            "25.0",
        ],
        capture_output=True,
        text=True,
    )

    assert res.returncode == 0, f"CLI error: {res.stderr}\n{res.stdout}"
    assert "SUCCESS" in res.stdout
    assert output_file.is_file()

    with output_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    cues = data["cues"]
    assert len(cues) == 3
    # Check distinct triggers and sources preserved
    triggers = [c["trigger"] for c in cues]
    assert "loud_spike" in triggers
    assert "profanity" in triggers
    sources = {c["source"] for c in cues}
    assert sources == {"loudness_detector", "profanity_detector"}

    # Validate schema and domain rules
    is_valid, errors = validate_index(output_file, SCHEMA_PATH, CONFIG_PATH)
    assert is_valid, f"Combined index validation failed: {errors}"


def test_multi_detector_incompatible_film_id_rejected(tmp_path: Path):
    """Verify input files identifying different films are rejected to prevent cross-film contamination."""
    file_a = tmp_path / "film_a.json"
    file_a.write_text(
        json.dumps({
            "film_id": "film_alpha",
            "detections": [{"start": 1.0, "end": 2.0, "trigger": "loud_spike", "score": 0.9, "source": "loudness_detector"}],
        }),
        encoding="utf-8",
    )
    file_b = tmp_path / "film_b.json"
    file_b.write_text(
        json.dumps({
            "film_id": "film_beta",
            "detections": [{"start": 3.0, "end": 4.0, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"}],
        }),
        encoding="utf-8",
    )
    output_file = tmp_path / "index.json"

    with pytest.raises(ValueError, match="Incompatible film metadata"):
        package_and_save_index(input_path=[file_a, file_b], output_path=output_file)


def test_multi_detector_incompatible_timeline_duration_rejected(tmp_path: Path):
    """Verify input files declaring conflicting timeline durations are rejected."""
    file_a = tmp_path / "dur_10.json"
    file_a.write_text(
        json.dumps({
            "film_id": "timeline_test",
            "timeline": {"duration_seconds": 10.0},
            "detections": [{"start": 1.0, "end": 2.0, "trigger": "loud_spike", "score": 0.9, "source": "loudness_detector"}],
        }),
        encoding="utf-8",
    )
    file_b = tmp_path / "dur_25.json"
    file_b.write_text(
        json.dumps({
            "film_id": "timeline_test",
            "timeline": {"duration_seconds": 25.0},
            "detections": [{"start": 3.0, "end": 4.0, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"}],
        }),
        encoding="utf-8",
    )
    output_file = tmp_path / "index.json"

    with pytest.raises(ValueError, match="Incompatible timeline metadata"):
        package_and_save_index(input_path=[file_a, file_b], output_path=output_file)


def test_multi_detector_synthetic_guardrail_rejects_real_flag_on_synthetic_data(tmp_path: Path):
    """Verify package_cues rejects marking output as real (synthetic=False) when input data contains synthetic cues."""
    synthetic_file = tmp_path / "synthetic_cues.json"
    synthetic_file.write_text(
        json.dumps({
            "film_id": "synth_guard_film",
            "synthetic": True,
            "timeline": {"duration_seconds": 15.0},
            "detections": [{"start": 1.0, "end": 2.0, "trigger": "loud_spike", "score": 0.8, "source": "loudness_detector"}],
        }),
        encoding="utf-8",
    )
    output_file = tmp_path / "index.json"

    with pytest.raises(ValueError, match="Cannot mark index as real"):
        package_and_save_index(
            input_path=synthetic_file,
            output_path=output_file,
            synthetic=False,
        )


def test_multi_detector_duplicate_subtitle_input_deduplicated(tmp_path: Path):
    """Verify supplying subtitle detections through both --input and --subtitles does not duplicate cues."""
    sub_file = tmp_path / "subtitles.json"
    sub_file.write_text(
        json.dumps({
            "film_id": "dedup_test",
            "synthetic": True,
            "timeline": {"duration_seconds": 20.0},
            "detections": [
                {"start": 2.0, "end": 4.0, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"}
            ],
        }),
        encoding="utf-8",
    )
    output_file = tmp_path / "index.json"

    index_data = package_and_save_index(
        input_path=[sub_file],
        subtitles_path=sub_file,
        output_path=output_file,
        film_id="dedup_test",
        duration_seconds=20.0,
        synthetic=True,
    )
    assert len(index_data["cues"]) == 1
    assert index_data["cues"][0]["id"] == "cue_dedup_test_profanity_002000_004000"


def test_multi_detector_deterministic_byte_identical_output(tmp_path: Path):
    """Verify repeated packaging runs on identical multi-detector inputs produce byte-identical output files."""
    det1 = tmp_path / "det1.json"
    det1.write_text(
        json.dumps({
            "film_id": "determ_film",
            "synthetic": True,
            "timeline": {"duration_seconds": 30.0},
            "detections": [
                {"start": 5.0, "end": 7.0, "trigger": "loud_spike", "score": 0.85, "source": "loudness_detector"},
                {"start": 1.0, "end": 3.0, "trigger": "gunshot", "score": 0.90, "source": "audio_event_classifier"},
            ],
        }),
        encoding="utf-8",
    )
    det2 = tmp_path / "det2.json"
    det2.write_text(
        json.dumps({
            "film_id": "determ_film",
            "synthetic": True,
            "timeline": {"duration_seconds": 30.0},
            "detections": [
                {"start": 2.0, "end": 4.0, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
            ],
        }),
        encoding="utf-8",
    )

    out1 = tmp_path / "out1.json"
    out2 = tmp_path / "out2.json"

    package_and_save_index(input_path=[det1, det2], output_path=out1, film_id="determ_film", duration_seconds=30.0, synthetic=True)
    package_and_save_index(input_path=[det1, det2], output_path=out2, film_id="determ_film", duration_seconds=30.0, synthetic=True)

    bytes1 = out1.read_bytes()
    bytes2 = out2.read_bytes()
    assert bytes1 == bytes2, "Repeated packaging runs produced non-identical output bytes."


def test_multi_detector_invalid_intervals_and_non_finite_scores_rejected():
    """Verify invalid intervals (start >= end, negative) and non-finite scores are rejected."""
    # start >= end
    with pytest.raises(ValueError, match="strictly greater than 'start'"):
        package_cues(
            raw_data={"detections": [{"start": 5.0, "end": 5.0, "trigger": "loud_spike", "score": 0.8, "source": "src"}]},
            film_id="inv_test",
            duration_seconds=10.0,
            synthetic=True,
        )

    # negative start
    with pytest.raises(ValueError, match="must be >= 0.0"):
        package_cues(
            raw_data={"detections": [{"start": -1.0, "end": 3.0, "trigger": "loud_spike", "score": 0.8, "source": "src"}]},
            film_id="inv_test",
            duration_seconds=10.0,
            synthetic=True,
        )

    # non-finite score (inf)
    with pytest.raises(ValueError, match="must be a finite number"):
        package_cues(
            raw_data={"detections": [{"start": 1.0, "end": 3.0, "trigger": "loud_spike", "score": float("inf"), "source": "src"}]},
            film_id="inv_test",
            duration_seconds=10.0,
            synthetic=True,
        )


def test_multi_detector_cue_id_collision_detected():
    """Verify distinct cues producing identical cue IDs are detected and raise CueIdCollisionError."""
    # Two distinct cues (different source) with same start, end, trigger will collide on ID format cue_{film}_{trigger}_{start}_{end}
    detections = [
        {"start": 1.0, "end": 3.0, "trigger": "loud_spike", "score": 0.8, "source": "loudness_detector"},
        {"start": 1.0, "end": 3.0, "trigger": "loud_spike", "score": 0.9, "source": "external_sensor"},
    ]
    with pytest.raises(CueIdCollisionError, match="Cue ID collision detected"):
        package_cues(
            raw_data={"detections": detections},
            film_id="collision_test",
            duration_seconds=10.0,
            synthetic=True,
        )


def test_subtitle_parser_cli_compatibility_and_canonical_source(tmp_path: Path):
    """Verify scripts/parse_subtitles.py CLI runs and emits detections with canonical 'profanity_detector' source."""
    srt_file = Path("data/test_clips/speech_triggers_truth.srt")
    wordlist_file = Path("data/profanity_wordlist.txt")
    out_dir = tmp_path / "sub_out"

    res = subprocess.run(
        [
            sys.executable,
            "scripts/parse_subtitles.py",
            str(srt_file),
            "--wordlist",
            str(wordlist_file),
            "--out-detections",
            str(out_dir / "detections.json"),
        ],
        capture_output=True,
        text=True,
    )

    assert res.returncode == 0, f"Subtitle parser CLI failed: {res.stderr}\n{res.stdout}"
    detections_file = out_dir / "detections.json"
    assert detections_file.is_file()

    with detections_file.open("r", encoding="utf-8") as f:
        det_data = json.load(f)

    assert len(det_data["detections"]) == 3
    for d in det_data["detections"]:
        assert d["trigger"] == "profanity"
        assert d["source"] == "profanity_detector"


# ------------------------------------------------------------------------------
# 9. True CLI Integration Tests – Multi-Detector Packaging (Person B regression)
# ------------------------------------------------------------------------------

def test_cli_multi_detector_end_to_end(tmp_path: Path):
    """End-to-end CLI regression: two separate detector JSON files in one invocation.

    Exercises CLI wiring, argument parsing, file loading, aggregation, source
    preservation, and output generation. Uses the actual subprocess interface:

        python scripts/package_cues.py \\
            --input <detector_a.json> <detector_b.json> \\
            --output <combined_index.json> \\
            --film-id cli_e2e_film \\
            --duration 30.0 \\
            --no-synthetic

    Input A: loud_spike  from loudness_detector
    Input B: profanity   from profanity_detector
    """
    # --- Build input A: loudness detector ---
    detector_a = tmp_path / "loudness_detections.json"
    detector_a_start = 5.0
    detector_a_end = 8.0
    detector_a_score = 0.87
    detector_a.write_text(
        json.dumps({
            "film_id": "cli_e2e_film",
            "synthetic": False,
            "timeline": {"duration_seconds": 30.0, "fps": 24.0},
            "detections": [
                {
                    "start": detector_a_start,
                    "end": detector_a_end,
                    "trigger": "loud_spike",
                    "score": detector_a_score,
                    "source": "loudness_detector",
                },
            ],
        }),
        encoding="utf-8",
    )

    # --- Build input B: profanity detector ---
    detector_b = tmp_path / "profanity_detections.json"
    detector_b_start = 15.0
    detector_b_end = 17.5
    detector_b_score = 1.0
    detector_b.write_text(
        json.dumps({
            "film_id": "cli_e2e_film",
            "synthetic": False,
            "timeline": {"duration_seconds": 30.0, "fps": 24.0},
            "detections": [
                {
                    "start": detector_b_start,
                    "end": detector_b_end,
                    "trigger": "profanity",
                    "score": detector_b_score,
                    "source": "profanity_detector",
                },
            ],
        }),
        encoding="utf-8",
    )

    output_file = tmp_path / "combined_index.json"

    # --- Invoke real CLI ---
    res = subprocess.run(
        [
            sys.executable,
            "scripts/package_cues.py",
            "--input",
            str(detector_a),
            str(detector_b),
            "--output",
            str(output_file),
            "--film-id",
            "cli_e2e_film",
            "--duration",
            "30.0",
            "--no-synthetic",
        ],
        capture_output=True,
        text=True,
    )

    # 1. CLI must succeed; include captured output for diagnosis on failure
    assert res.returncode == 0, (
        f"CLI packaging failed (exit {res.returncode}).\n"
        f"STDOUT:\n{res.stdout}\n"
        f"STDERR:\n{res.stderr}"
    )
    assert "SUCCESS" in res.stdout, (
        f"Expected 'SUCCESS' in stdout, got:\n{res.stdout}"
    )

    # 2. Output file must exist
    assert output_file.is_file(), "CLI did not create the output file."

    # 3. Load and inspect the generated index
    with output_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # 4. Top-level metadata
    assert data["film_id"] == "cli_e2e_film"
    assert data["synthetic"] is False, "Expected synthetic=false for real detector inputs."
    assert data["schema_version"] == 1
    assert data["timeline"]["duration_seconds"] == 30.0

    cues = data["cues"]

    # 5. Both triggers must be present
    triggers = {c["trigger"] for c in cues}
    assert "loud_spike" in triggers, f"loud_spike missing from triggers: {triggers}"
    assert "profanity" in triggers, f"profanity missing from triggers: {triggers}"

    # 6. Both sources must be preserved
    sources = {c["source"] for c in cues}
    assert "loudness_detector" in sources, f"loudness_detector missing from sources: {sources}"
    assert "profanity_detector" in sources, f"profanity_detector missing from sources: {sources}"

    # 7. Verify cues from both input files appear in output
    loud_cues = [c for c in cues if c["trigger"] == "loud_spike"]
    prof_cues = [c for c in cues if c["trigger"] == "profanity"]
    assert len(loud_cues) >= 1, "No loud_spike cues in combined output."
    assert len(prof_cues) >= 1, "No profanity cues in combined output."

    # 8. Timestamps and scores are preserved (no value corruption)
    loud_cue = loud_cues[0]
    assert loud_cue["start"] == pytest.approx(detector_a_start)
    assert loud_cue["end"] == pytest.approx(detector_a_end)
    assert loud_cue["score"] == pytest.approx(detector_a_score)
    assert loud_cue["source"] == "loudness_detector"

    prof_cue = prof_cues[0]
    assert prof_cue["start"] == pytest.approx(detector_b_start)
    assert prof_cue["end"] == pytest.approx(detector_b_end)
    assert prof_cue["score"] == pytest.approx(detector_b_score)
    assert prof_cue["source"] == "profanity_detector"

    # 9. Every cue is within the shared timeline bounds [0, 30.0)
    duration = data["timeline"]["duration_seconds"]
    for cue in cues:
        assert cue["start"] >= 0.0, f"Cue starts before 0: {cue}"
        assert cue["end"] <= duration, f"Cue end {cue['end']} exceeds duration {duration}: {cue}"
        assert cue["end"] > cue["start"], f"Cue end not > start: {cue}"

    # 10. Cue IDs are unique
    ids = [c["id"] for c in cues]
    assert len(ids) == len(set(ids)), f"Duplicate cue IDs detected: {ids}"

    # 11. Cue IDs match the deterministic generation rule
    expected_loud_id = generate_cue_id("cli_e2e_film", "loud_spike", detector_a_start, detector_a_end)
    assert loud_cue["id"] == expected_loud_id, (
        f"loud_spike cue ID mismatch: expected {expected_loud_id!r}, got {loud_cue['id']!r}"
    )
    expected_prof_id = generate_cue_id("cli_e2e_film", "profanity", detector_b_start, detector_b_end)
    assert prof_cue["id"] == expected_prof_id, (
        f"profanity cue ID mismatch: expected {expected_prof_id!r}, got {prof_cue['id']!r}"
    )

    # 12. Validate generated index using the existing schema/domain validation mechanism
    is_valid, errors = validate_index(output_file, SCHEMA_PATH, CONFIG_PATH)
    assert is_valid, f"Schema/domain validation failed for CLI-generated index: {errors}"


def test_cli_multi_detector_conflicting_film_id_rejected(tmp_path: Path):
    """Negative CLI test: conflicting film IDs across two input files must produce a non-zero exit code.

    Verifies that the CLI rejects inputs from different films and emits a
    diagnostic error message identifying the conflicting film_id.
    """
    file_alpha = tmp_path / "film_alpha.json"
    file_alpha.write_text(
        json.dumps({
            "film_id": "film_alpha",
            "synthetic": True,
            "timeline": {"duration_seconds": 20.0},
            "detections": [
                {"start": 1.0, "end": 2.0, "trigger": "loud_spike", "score": 0.9, "source": "loudness_detector"},
            ],
        }),
        encoding="utf-8",
    )

    file_beta = tmp_path / "film_beta.json"
    file_beta.write_text(
        json.dumps({
            "film_id": "film_beta",
            "synthetic": True,
            "timeline": {"duration_seconds": 20.0},
            "detections": [
                {"start": 3.0, "end": 4.0, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
            ],
        }),
        encoding="utf-8",
    )

    output_file = tmp_path / "should_not_be_created.json"

    res = subprocess.run(
        [
            sys.executable,
            "scripts/package_cues.py",
            "--input",
            str(file_alpha),
            str(file_beta),
            "--output",
            str(output_file),
            "--duration",
            "20.0",
        ],
        capture_output=True,
        text=True,
    )

    # Must exit non-zero
    assert res.returncode != 0, (
        f"Expected non-zero exit for conflicting film IDs, but got exit code 0.\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )

    # Error output must identify the conflict clearly enough to diagnose
    combined_output = res.stdout + res.stderr
    assert "film" in combined_output.lower(), (
        f"Expected 'film' in CLI error output to identify the conflict, got:\n{combined_output}"
    )
    # The implementation emits "film_alpha" or "film_beta" or "Incompatible film"
    assert any(token in combined_output for token in ("film_alpha", "film_beta", "Incompatible", "incompatible")), (
        f"Expected conflicting film ID name or 'Incompatible' in error output, got:\n{combined_output}"
    )

    # Output file must not have been created
    assert not output_file.exists(), "CLI created an output file despite conflicting film IDs."


# ==============================================================================
# 9. Packaging Edge-Case Hardening (Person B Audit)
# ==============================================================================

def test_overlapping_cues_different_triggers_co_temporal_and_containment():
    """Case 1: Overlapping cues with different triggers (co-temporal, partial, and containment).

    Verifies:
    - Cues with identical time intervals but different triggers are both preserved.
    - Cues with one completely contained inside another of a different trigger are preserved.
    - Cues are never cross-merged or assigned the wrong trigger.
    - Deterministic sorting by (start, end, trigger, source) is maintained.
    - Unique cue IDs are generated without collision.
    """
    raw_data = {
        "film_id": "overlap_audit",
        "timeline": {"duration_seconds": 30.0},
        "detections": [
            # Co-temporal: exact same interval [5.0, 10.0), two different canonical triggers
            {"start": 5.0, "end": 10.0, "trigger": "flashing", "score": 0.9, "source": "flash_detector"},
            {"start": 5.0, "end": 10.0, "trigger": "loud_spike", "score": 0.85, "source": "loudness_detector"},
            # Containment: spider [2.0, 15.0) completely contains scream [6.0, 8.0)
            {"start": 2.0, "end": 15.0, "trigger": "spider", "score": 0.92, "source": "spider_detector"},
            {"start": 6.0, "end": 8.0, "trigger": "scream", "score": 0.78, "source": "audio_clf"},
            # Partial overlap: gunshot [12.0, 18.0) overlaps explosion [14.0, 20.0)
            {"start": 12.0, "end": 18.0, "trigger": "gunshot", "score": 0.95, "source": "audio_clf"},
            {"start": 14.0, "end": 20.0, "trigger": "explosion", "score": 0.88, "source": "audio_clf"},
        ],
    }

    index_data = package_cues(raw_data, film_id="overlap_audit", duration_seconds=30.0)

    cues = index_data["cues"]
    assert len(cues) == 6

    # Verify triggers are preserved exactly
    triggers = [c["trigger"] for c in cues]
    assert sorted(triggers) == sorted(["flashing", "loud_spike", "spider", "scream", "gunshot", "explosion"])

    # Verify chronological ordering by (start, end, trigger, source)
    # 1. spider [2.0, 15.0)
    assert cues[0]["trigger"] == "spider" and cues[0]["start"] == 2.0 and cues[0]["end"] == 15.0
    # 2 & 3. flashing and loud_spike both start at 5.0 and end at 10.0; sorted alphabetically by trigger
    assert cues[1]["start"] == 5.0 and cues[1]["end"] == 10.0 and cues[1]["trigger"] == "flashing"
    assert cues[2]["start"] == 5.0 and cues[2]["end"] == 10.0 and cues[2]["trigger"] == "loud_spike"
    # 4. scream [6.0, 8.0)
    assert cues[3]["trigger"] == "scream" and cues[3]["start"] == 6.0 and cues[3]["end"] == 8.0
    # 5. gunshot [12.0, 18.0)
    assert cues[4]["trigger"] == "gunshot" and cues[4]["start"] == 12.0 and cues[4]["end"] == 18.0
    # 6. explosion [14.0, 20.0)
    assert cues[5]["trigger"] == "explosion" and cues[5]["start"] == 14.0 and cues[5]["end"] == 20.0

    # Verify distinct collision-safe IDs
    cue_ids = [c["id"] for c in cues]
    assert len(cue_ids) == len(set(cue_ids))
    assert "cue_overlap_audit_flashing_005000_010000" in cue_ids
    assert "cue_overlap_audit_loud_spike_005000_010000" in cue_ids


def test_deterministic_cue_ids_stability_under_permutations():
    """Case 2: Stable deterministic IDs under arbitrary input permutations and key ordering.

    Verifies:
    - Packaging the exact same detections in different permutations yields identical cue IDs.
    - Cue ID generation is purely deterministic and free from random or timestamp drift.
    - Film ID whitespace/casing normalization in IDs behaves consistently.
    """
    dets = [
        {"start": 1.0, "end": 3.0, "trigger": "loud_spike", "score": 0.8, "source": "src1"},
        {"start": 4.5, "end": 7.25, "trigger": "flashing", "score": 0.9, "source": "src2"},
        {"start": 8.0, "end": 12.0, "trigger": "profanity", "score": 1.0, "source": "src3"},
        {"start": 15.0, "end": 20.0, "trigger": "spider", "score": 0.7, "source": "src4"},
    ]

    expected_ids = [
        "cue_determinism_test_loud_spike_001000_003000",
        "cue_determinism_test_flashing_004500_007250",
        "cue_determinism_test_profanity_008000_012000",
        "cue_determinism_test_spider_015000_020000",
    ]

    # Run in forward, reverse, and multiple random permutations
    permutations = [
        dets,
        list(reversed(dets)),
        [dets[2], dets[0], dets[3], dets[1]],
        [dets[3], dets[1], dets[2], dets[0]],
    ]

    for perm in permutations:
        res = package_cues(
            {"detections": perm},
            film_id="Determinism Test",
            duration_seconds=25.0,
        )
        actual_ids = [c["id"] for c in res["cues"]]
        assert actual_ids == expected_ids


def test_invalid_intervals_rejection_matrix():
    """Case 3: Comprehensive rejection matrix for invalid intervals and malformed timestamps.

    Verifies:
    - start > end (inverted interval)
    - start == end (zero duration)
    - negative start or negative end
    - non-numeric string, boolean, None, NaN, and Inf timestamps
    - missing start or end fields
    - invalid scores and empty sources
    - Packager NEVER silently clamps or repairs invalid data.
    """
    base_valid = {"start": 1.0, "end": 2.0, "trigger": "loud_spike", "score": 0.8, "source": "src"}

    invalid_cases = [
        # Inverted intervals
        ({"start": 5.0, "end": 2.0}, "strictly greater than 'start'"),
        ({"start": 10.0, "end": 9.999}, "strictly greater than 'start'"),
        # Zero duration
        ({"start": 3.0, "end": 3.0}, "strictly greater than 'start'"),
        # Negative timestamps
        ({"start": -0.001, "end": 2.0}, "must be >= 0.0"),
        ({"start": -5.0, "end": -1.0}, "must be >= 0.0"),
        # Non-numeric types
        ({"start": "1.0", "end": 2.0}, "must be a finite number"),
        ({"start": 1.0, "end": "2.0"}, "must be a finite number"),
        ({"start": None, "end": 2.0}, "must be a finite number"),
        ({"start": 1.0, "end": None}, "must be a finite number"),
        # Booleans (subclass of int in Python, must be explicitly rejected)
        ({"start": True, "end": 2.0}, "must be a finite number"),
        ({"start": 1.0, "end": False}, "must be a finite number"),
        # Non-finite values
        ({"start": float("nan"), "end": 2.0}, "must be a finite number"),
        ({"start": 1.0, "end": float("nan")}, "must be a finite number"),
        ({"start": float("-inf"), "end": 2.0}, "must be a finite number"),
        ({"start": 1.0, "end": float("inf")}, "must be a finite number"),
        # Missing required fields
        ({"_drop": "start"}, "missing required field 'start'"),
        ({"_drop": "end"}, "missing required field 'end'"),
        ({"_drop": "source"}, "missing required field 'source'"),
        ({"_drop": "trigger"}, "missing required field 'trigger'"),
        ({"_drop": "score"}, "missing required field 'score'"),
        # Invalid scores
        ({"score": -0.1}, r"must be in \[0\.0, 1\.0\]"),
        ({"score": 1.05}, r"must be in \[0\.0, 1\.0\]"),
        ({"score": "high"}, "must be a finite number"),
        ({"score": True}, "must be a finite number"),
        # Empty or invalid sources
        ({"source": ""}, "must be a non-empty string"),
        ({"source": "   "}, "must be a non-empty string"),
        ({"source": 123}, "must be a non-empty string"),
    ]

    for override, match_pattern in invalid_cases:
        det = dict(base_valid)
        if "_drop" in override:
            det.pop(override["_drop"], None)
        else:
            det.update(override)

        with pytest.raises(ValueError, match=match_pattern):
            package_cues(
                raw_data={"detections": [det]},
                film_id="inv_test",
                duration_seconds=10.0,
            )


def test_cues_extending_beyond_media_duration_boundaries():
    """Case 4: Cues extending beyond media duration and exact boundary behavior.

    Verifies:
    - Cue ending exactly at duration_seconds (half-open [start, duration)) is ACCEPTED.
    - Cue with end > duration_seconds is REJECTED.
    - Cue with start >= duration_seconds is REJECTED.
    - Authoritative media duration precedence: explicit parameter vs input timeline.
    """
    film_duration = 20.0

    # 1. Exact boundary: end == duration_seconds passes
    exact_cue = {"start": 15.0, "end": film_duration, "trigger": "loud_spike", "score": 0.8, "source": "src"}
    res_exact = package_cues(
        raw_data={"detections": [exact_cue]},
        film_id="duration_boundary",
        duration_seconds=film_duration,
    )
    assert len(res_exact["cues"]) == 1
    assert res_exact["cues"][0]["end"] == film_duration

    # 2. End strictly exceeding duration: rejected
    over_end_cue = {"start": 15.0, "end": 20.001, "trigger": "loud_spike", "score": 0.8, "source": "src"}
    with pytest.raises(ValueError, match="exceeds declared film duration"):
        package_cues(
            raw_data={"detections": [over_end_cue]},
            film_id="duration_boundary",
            duration_seconds=film_duration,
        )

    # 3. Start at duration (zero length in-bounds, ends beyond): rejected
    start_at_dur_cue = {"start": 20.0, "end": 22.0, "trigger": "loud_spike", "score": 0.8, "source": "src"}
    with pytest.raises(ValueError, match="exceeds declared film duration"):
        package_cues(
            raw_data={"detections": [start_at_dur_cue]},
            film_id="duration_boundary",
            duration_seconds=film_duration,
        )

    # 4. Start strictly beyond duration: rejected
    start_past_dur_cue = {"start": 25.0, "end": 30.0, "trigger": "loud_spike", "score": 0.8, "source": "src"}
    with pytest.raises(ValueError, match="exceeds declared film duration"):
        package_cues(
            raw_data={"detections": [start_past_dur_cue]},
            film_id="duration_boundary",
            duration_seconds=film_duration,
        )

    # 5. Authoritative parameter overrides input dict duration
    # Input claims duration 50.0, but explicit parameter is 20.0 -> cue ending at 25.0 must be rejected
    with pytest.raises(ValueError, match="exceeds declared film duration"):
        package_cues(
            raw_data={
                "timeline": {"duration_seconds": 50.0},
                "detections": [{"start": 10.0, "end": 25.0, "trigger": "loud_spike", "score": 0.8, "source": "src"}],
            },
            film_id="duration_boundary",
            duration_seconds=20.0,  # Authoritative override
        )


def test_provenance_preservation_across_distinct_sources_and_merges():
    """Case 5: Provenance (source) preservation across detectors and merge operations.

    Verifies:
    - Packaged cues strictly retain their original detector 'source'.
    - Provenance is not replaced with generic 'detector' or confused with 'trigger'.
    - Merged contiguous detections retain their common 'source'.
    - Adjacent/overlapping detections with different sources never merge and retain respective sources.
    - Output conforms to index schema requiring non-empty string 'source'.
    """
    raw_detections = [
        # Two mergeable cues from source 'audio_pipeline_v1'
        {"start": 1.0, "end": 2.0, "trigger": "loud_spike", "score": 0.8, "source": "audio_pipeline_v1"},
        {"start": 2.5, "end": 3.5, "trigger": "loud_spike", "score": 0.9, "source": "audio_pipeline_v1"},
        # Overlapping cue with same trigger 'loud_spike' but different source 'hardware_mic_sensor' -> MUST NOT MERGE
        {"start": 2.0, "end": 4.0, "trigger": "loud_spike", "score": 0.95, "source": "hardware_mic_sensor"},
        # Visual detector cue
        {"start": 5.0, "end": 7.0, "trigger": "spider", "score": 0.85, "source": "visual_yolo_detector"},
        # Profanity detector cue
        {"start": 8.0, "end": 10.0, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
    ]

    res = package_cues(
        raw_data={"detections": raw_detections},
        film_id="provenance_test",
        duration_seconds=15.0,
    )

    cues = res["cues"]
    # We expect:
    # 1. Merged loud_spike from audio_pipeline_v1: [1.0, 3.5) score=0.9, source='audio_pipeline_v1'
    # 2. Distinct loud_spike from hardware_mic_sensor: [2.0, 4.0) score=0.95, source='hardware_mic_sensor'
    # 3. spider from visual_yolo_detector: [5.0, 7.0) score=0.85, source='visual_yolo_detector'
    # 4. profanity from profanity_detector: [8.0, 10.0) score=1.0, source='profanity_detector'
    assert len(cues) == 4

    # Verify every cue has 'source' and NOT 'detector'
    for cue in cues:
        assert "source" in cue
        assert "detector" not in cue
        assert isinstance(cue["source"], str) and cue["source"]

    # Cue 1: Merged audio_pipeline_v1
    assert cues[0]["start"] == 1.0 and cues[0]["end"] == 3.5
    assert cues[0]["trigger"] == "loud_spike"
    assert cues[0]["source"] == "audio_pipeline_v1"
    assert cues[0]["score"] == 0.9

    # Cue 2: hardware_mic_sensor
    assert cues[1]["start"] == 2.0 and cues[1]["end"] == 4.0
    assert cues[1]["trigger"] == "loud_spike"
    assert cues[1]["source"] == "hardware_mic_sensor"
    assert cues[1]["score"] == 0.95

    # Cue 3: visual_yolo_detector
    assert cues[2]["trigger"] == "spider"
    assert cues[2]["source"] == "visual_yolo_detector"

    # Cue 4: profanity_detector
    assert cues[3]["trigger"] == "profanity"
    assert cues[3]["source"] == "profanity_detector"


# ------------------------------------------------------------------------------
# 11. Experimental Detectors (jump_scare & fast_motion) Packaging Integration
# ------------------------------------------------------------------------------

def test_package_synthetic_jump_scare_cue():
    """Verify a valid synthetic jump_scare cue is accepted, formatted, and packaged."""
    raw_jump_scare = {
        "start": 2.123,
        "end": 3.789,
        "trigger": "jump_scare",
        "score": 0.8542,
        "source": "jump_scare_detector",
    }
    config = load_config_data(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)

    res = package_cues(
        raw_data={"detections": [raw_jump_scare]},
        film_id="sintel",
        duration_seconds=52.0,
        config=config,
        schema=schema,
    )

    cues = res["cues"]
    assert len(cues) == 1
    cue = cues[0]
    assert cue["trigger"] == "jump_scare"
    assert cue["score"] == 0.8542
    assert cue["source"] == "jump_scare_detector"
    assert cue["start"] == 2.123
    assert cue["end"] == 3.789
    assert cue["id"] == "cue_sintel_jump_scare_002123_003789"


def test_package_synthetic_fast_motion_cue():
    """Verify a valid synthetic fast_motion cue is accepted, formatted, and packaged."""
    raw_fast_motion = {
        "start": 5.0,
        "end": 7.5,
        "trigger": "fast_motion",
        "score": 0.65,
        "source": "motion_detector",
    }
    config = load_config_data(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)

    res = package_cues(
        raw_data={"detections": [raw_fast_motion]},
        film_id="tears_of_steel",
        duration_seconds=100.0,
        config=config,
        schema=schema,
    )

    cues = res["cues"]
    assert len(cues) == 1
    cue = cues[0]
    assert cue["trigger"] == "fast_motion"
    assert cue["score"] == 0.65
    assert cue["source"] == "motion_detector"
    assert cue["start"] == 5.0
    assert cue["end"] == 7.5
    assert cue["id"] == "cue_tears_of_steel_fast_motion_005000_007500"


def test_packaged_index_with_experimental_cues_validates_against_schema(tmp_path: Path):
    """Verify packaged index containing experimental cues passes full schema and domain validation."""
    raw_detections = [
        {"start": 1.0, "end": 2.0, "trigger": "loud_spike", "score": 0.9, "source": "loudness_detector"},
        {"start": 3.0, "end": 4.5, "trigger": "fast_motion", "score": 0.75, "source": "motion_detector"},
        {"start": 6.0, "end": 7.2, "trigger": "jump_scare", "score": 0.88, "source": "jump_scare_detector"},
    ]

    out_file = tmp_path / "experimental_index.json"
    raw_file = tmp_path / "raw_detections.json"
    raw_file.write_text(json.dumps(raw_detections), encoding="utf-8")

    package_and_save_index(
        input_path=raw_file,
        output_path=out_file,
        config_path=CONFIG_PATH,
        schema_path=SCHEMA_PATH,
        film_id="multi_trigger_test",
        duration_seconds=15.0,
        fps=24.0,
    )

    is_valid, errors = validate_index(
        index_path=out_file,
        schema_path=SCHEMA_PATH,
        config_path=CONFIG_PATH,
    )
    assert is_valid is True, f"Validation errors:\n{errors}"
    assert errors == []


def test_experimental_cue_invariants_and_merge_preservation():
    """Verify trigger IDs, score fields, sources, and half-open intervals are preserved correctly."""
    raw_detections = [
        # Two contiguous fast_motion cues from motion_detector with gap 0.5s <= merge_gap 2.0s -> MUST MERGE
        {"start": 1.0, "end": 2.5, "trigger": "fast_motion", "score": 0.6, "source": "motion_detector"},
        {"start": 3.0, "end": 5.0, "trigger": "fast_motion", "score": 0.85, "source": "motion_detector"},
        # Overlapping jump_scare cue on [2.0, 4.0) with different trigger & source -> MUST NOT MERGE
        {"start": 2.0, "end": 4.0, "trigger": "jump_scare", "score": 0.92, "source": "jump_scare_detector"},
    ]

    res = package_cues(
        raw_data={"detections": raw_detections},
        film_id="invariants_test",
        duration_seconds=10.0,
        config=load_config_data(CONFIG_PATH),
        schema=load_schema(SCHEMA_PATH),
    )

    cues = res["cues"]
    assert len(cues) == 2

    # Chronologically sorted by (start, end)
    # Cue 1: Merged fast_motion [1.0, 5.0) score=0.85
    assert cues[0]["trigger"] == "fast_motion"
    assert cues[0]["source"] == "motion_detector"
    assert cues[0]["start"] == 1.0
    assert cues[0]["end"] == 5.0
    assert cues[0]["score"] == 0.85
    assert cues[0]["id"] == "cue_invariants_test_fast_motion_001000_005000"

    # Cue 2: jump_scare [2.0, 4.0) score=0.92
    assert cues[1]["trigger"] == "jump_scare"
    assert cues[1]["source"] == "jump_scare_detector"
    assert cues[1]["start"] == 2.0
    assert cues[1]["end"] == 4.0
    assert cues[1]["score"] == 0.92
    assert cues[1]["id"] == "cue_invariants_test_jump_scare_002000_004000"


def test_existing_packaging_unchanged_without_experimental_detectors():
    """Verify standard packaging behavior is 100% identical when experimental cues are not present."""
    standard_detections = [
        {"start": 1.0, "end": 3.0, "trigger": "loud_spike", "score": 0.8, "source": "loudness_detector"},
        {"start": 4.0, "end": 5.5, "trigger": "profanity", "score": 1.0, "source": "profanity_detector"},
    ]

    res = package_cues(
        raw_data={"detections": standard_detections},
        film_id="standard_test",
        duration_seconds=10.0,
        config=load_config_data(CONFIG_PATH),
        schema=load_schema(SCHEMA_PATH),
    )

    cues = res["cues"]
    assert len(cues) == 2
    assert [c["trigger"] for c in cues] == ["loud_spike", "profanity"]
    assert all(c["trigger"] not in ("fast_motion", "jump_scare") for c in cues)


def test_disabled_experimental_viewer_policies_prevent_playback_application():
    """Verify that disabled experimental viewer policies prevent those cues from being applied in standard playback."""
    node_test_script = """
    import assert from 'node:assert';
    import { adaptPackagedIndex } from './src/engine/packagedIndexAdapter.ts';
    import { DEFAULT_TRIGGER_POLICIES } from './src/data/defaultTriggerPolicies.ts';

    const testIndex = {
      schema_version: 1,
      film_id: 'policy_test',
      timeline: {
        duration_seconds: 20.0,
        timestamp_unit: 'seconds',
        interval_convention: 'half_open',
        fps: 24.0,
      },
      cues: [
        {
          id: 'cue_loud_spike_01',
          start: 2.0,
          end: 4.0,
          trigger: 'loud_spike',
          score: 0.9,
          source: 'loudness_detector',
        },
        {
          id: 'cue_jump_scare_01',
          start: 6.0,
          end: 8.0,
          trigger: 'jump_scare',
          score: 0.85,
          source: 'jump_scare_detector',
        },
        {
          id: 'cue_fast_motion_01',
          start: 10.0,
          end: 12.0,
          trigger: 'fast_motion',
          score: 0.7,
          source: 'motion_detector',
        },
      ],
    };

    // 1. With standard production default policies (both experimental triggers are disabled):
    assert.strictEqual(DEFAULT_TRIGGER_POLICIES.jump_scare.enabled, false);
    assert.strictEqual(DEFAULT_TRIGGER_POLICIES.fast_motion.enabled, false);

    const standardCues = adaptPackagedIndex(testIndex, DEFAULT_TRIGGER_POLICIES);
    assert.strictEqual(standardCues.length, 1, 'Only enabled cues should be adapted');
    assert.strictEqual(standardCues[0].trigger, 'loud_spike');
    assert.strictEqual(standardCues[0].action, 'reduce');

    // 2. Explicitly enable jump_scare via custom policy:
    const customJumpPolicies = {
      ...DEFAULT_TRIGGER_POLICIES,
      jump_scare: { ...DEFAULT_TRIGGER_POLICIES.jump_scare, enabled: true },
    };
    const jumpCues = adaptPackagedIndex(testIndex, customJumpPolicies);
    assert.strictEqual(jumpCues.length, 2);
    const jumpScareCue = jumpCues.find(c => c.trigger === 'jump_scare');
    assert.ok(jumpScareCue);
    assert.strictEqual(jumpScareCue.action, 'warn');
    assert.strictEqual(jumpScareCue.source, 'audio_visual');

    // 3. Explicitly enable fast_motion via custom policy:
    const customMotionPolicies = {
      ...DEFAULT_TRIGGER_POLICIES,
      fast_motion: { ...DEFAULT_TRIGGER_POLICIES.fast_motion, enabled: true },
    };
    const motionCues = adaptPackagedIndex(testIndex, customMotionPolicies);
    assert.strictEqual(motionCues.length, 2);
    const motionCue = motionCues.find(c => c.trigger === 'fast_motion');
    assert.ok(motionCue);
    assert.strictEqual(motionCue.action, 'warn');
    assert.strictEqual(motionCue.source, 'visual');

    console.log('Experimental policies playback prevention verified successfully.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_test_script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Experimental policies playback prevention verified successfully." in res.stdout

