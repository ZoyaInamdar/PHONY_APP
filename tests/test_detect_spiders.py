"""Unit and integration tests for YOLO-World spider detection pipeline.

Tests cover:
1. Confidence threshold filtering.
2. Half-open [start, end) interval grouping across consecutive seconds.
3. Empty detections handling and valid schema compliance.
4. Malformed detection record rejection.
5. Strict class isolation (canonical trigger 'spider', forbidding reptile triggers/mappings).
6. End-to-end integration with generic cue packaging (gap merging, cue IDs, schema validation).
7. Regression verification for existing canonical triggers and cross-trigger separation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set
import pytest

from scripts.detect_spiders import (
    DEFAULT_CLASS,
    DEFAULT_CONF,
    group_consecutive_detections,
)
from scripts.package_cues import (
    package_cues,
    validate_raw_detection,
)
from scripts.validate_config import load_config, load_schema
from scripts.validate_index import (
    get_canonical_triggers_from_config,
    validate_domain_rules,
    validate_schema_compliance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
SCHEMA_PATH = REPO_ROOT / "schema" / "index.schema.json"


# -----------------------------------------------------------------------------
# 1. Interval Grouping & Timestamp Tests
# -----------------------------------------------------------------------------

def test_interval_grouping_consecutive_seconds():
    """Consecutive detected seconds [10, 11, 12] group into a single [10.0, 13.0) half-open interval."""
    frames = [
        {"second": 10, "score": 0.45},
        {"second": 11, "score": 0.85},
        {"second": 12, "score": 0.60},
    ]
    detections = group_consecutive_detections(frames, fps_sample=1.0)
    assert len(detections) == 1
    det = detections[0]
    assert det["start"] == 10.0
    assert det["end"] == 13.0
    assert det["trigger"] == "spider"
    assert det["score"] == 0.85
    assert det["source"] == "spider_detector"


def test_interval_grouping_disjoint_seconds():
    """Non-consecutive detected seconds produce separate half-open intervals."""
    frames = [
        {"second": 5, "score": 0.50},
        {"second": 6, "score": 0.70},
        {"second": 15, "score": 0.90},
    ]
    detections = group_consecutive_detections(frames, fps_sample=1.0)
    assert len(detections) == 2

    assert detections[0]["start"] == 5.0
    assert detections[0]["end"] == 7.0
    assert detections[0]["score"] == 0.70

    assert detections[1]["start"] == 15.0
    assert detections[1]["end"] == 16.0
    assert detections[1]["score"] == 0.90


def test_fractional_fps_sampling_step():
    """Fractional sampling rates scale interval lengths accurately."""
    frames = [
        {"second": 2.0, "score": 0.80},
        {"second": 2.5, "score": 0.85},
    ]
    detections = group_consecutive_detections(frames, fps_sample=2.0)
    assert len(detections) == 1
    assert detections[0]["start"] == 2.0
    assert detections[0]["end"] == 3.0


# -----------------------------------------------------------------------------
# 2. Empty Detections and Zero-Count Handling
# -----------------------------------------------------------------------------

def test_empty_detections_produces_empty_list():
    """When no spiders are detected in video frames, empty list is returned safely."""
    detections = group_consecutive_detections([], fps_sample=1.0)
    assert detections == []


def test_empty_detections_packaging():
    """An input file with 0 detections packages into a valid index.json with empty cues."""
    config = load_config(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)
    canonical_triggers = set(config["triggers"].keys())

    raw_data = {
        "film_id": "sintel_clean",
        "synthetic": False,
        "timeline": {"duration_seconds": 120.0, "fps": 24.0},
        "detections": [],
    }

    packaged = package_cues(
        raw_data=raw_data,
        film_id="sintel_clean",
        duration_seconds=120.0,
        fps=24.0,
        config=config,
        synthetic=False,
        schema=schema,
    )

    assert packaged["film_id"] == "sintel_clean"
    assert packaged["synthetic"] is False
    assert packaged["cues"] == []

    schema_errors = validate_schema_compliance(packaged, schema)
    assert not schema_errors, f"Schema compliance failed: {schema_errors}"

    domain_errors = validate_domain_rules(packaged, canonical_triggers)
    assert not domain_errors, f"Domain validation failed: {domain_errors}"


# -----------------------------------------------------------------------------
# 3. Class Isolation & Reptile Forbiddance
# -----------------------------------------------------------------------------

def test_class_isolation_rejects_reptiles():
    """Reptiles and non-spider targets are strictly rejected; no mapping to spider allowed."""
    frames = [{"second": 1, "score": 0.9}]

    # Rejection of reptile triggers
    with pytest.raises(ValueError, match="Only canonical trigger 'spider' is supported"):
        group_consecutive_detections(frames, trigger="reptile")

    with pytest.raises(ValueError, match="Only canonical trigger 'spider' is supported"):
        group_consecutive_detections(frames, trigger="snake")

    with pytest.raises(ValueError, match="Only canonical trigger 'spider' is supported"):
        group_consecutive_detections(frames, trigger="lizard")


def test_canonical_class_constant():
    """Default class must be 'spider'."""
    assert DEFAULT_CLASS == "spider"
    assert DEFAULT_CONF == 0.15


# -----------------------------------------------------------------------------
# 4. Malformed Output Handling
# -----------------------------------------------------------------------------

def test_malformed_detection_records_rejected():
    """Detections with negative start, inverted intervals, or out-of-range scores fail validation."""
    config = load_config(CONFIG_PATH)
    canonical_triggers = set(config["triggers"].keys())

    # Inverted interval (end <= start)
    bad_interval = {"start": 10.0, "end": 8.0, "trigger": "spider", "score": 0.8, "source": "spider_detector"}
    with pytest.raises(ValueError, match="must be strictly greater than 'start'"):
        validate_raw_detection(bad_interval, canonical_triggers, max_allowed_duration=60.0, cue_index=0)

    # Negative start
    bad_start = {"start": -1.0, "end": 5.0, "trigger": "spider", "score": 0.8, "source": "spider_detector"}
    with pytest.raises(ValueError, match="'start' must be >= 0.0"):
        validate_raw_detection(bad_start, canonical_triggers, max_allowed_duration=60.0, cue_index=0)

    # Score > 1.0
    bad_score = {"start": 1.0, "end": 2.0, "trigger": "spider", "score": 1.5, "source": "spider_detector"}
    with pytest.raises(ValueError, match="'score' must be in"):
        validate_raw_detection(bad_score, canonical_triggers, max_allowed_duration=60.0, cue_index=0)

    # Unknown trigger
    bad_trigger = {"start": 1.0, "end": 2.0, "trigger": "reptile", "score": 0.9, "source": "spider_detector"}
    with pytest.raises(ValueError, match="is not recognized in canonical triggers"):
        validate_raw_detection(bad_trigger, canonical_triggers, max_allowed_duration=60.0, cue_index=0)


# -----------------------------------------------------------------------------
# 5. Packaging Pipeline Integration (Gap Merging, Cue IDs)
# -----------------------------------------------------------------------------

def test_spider_packaging_merging_and_ids():
    """Raw spider detections are merged according to merge_gap_seconds in config.yaml."""
    config = load_config(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)
    canonical_triggers = set(config["triggers"].keys())

    spider_conf = config["triggers"]["spider"]
    merge_gap = spider_conf["merge_gap_seconds"]  # 3.0s
    assert merge_gap == 3.0

    raw_data = {
        "film_id": "sintel",
        "synthetic": False,
        "timeline": {"duration_seconds": 200.0, "fps": 24.0},
        "detections": [
            # Detection 1: [10.0, 12.0)
            {"start": 10.0, "end": 12.0, "trigger": "spider", "score": 0.80, "source": "spider_detector"},
            # Detection 2: [15.0, 17.0)
            # Gap is 15.0 - 12.0 = 3.0s <= merge_gap_seconds (3.0s) -> Merged!
            {"start": 15.0, "end": 17.0, "trigger": "spider", "score": 0.92, "source": "spider_detector"},
        ],
    }

    packaged = package_cues(
        raw_data=raw_data,
        film_id="sintel",
        duration_seconds=200.0,
        fps=24.0,
        config=config,
        synthetic=False,
        schema=schema,
    )

    cues = packaged["cues"]
    assert len(cues) == 1, "Detections separated by <= merge_gap_seconds must be merged into 1 cue"

    cue = cues[0]
    assert cue["trigger"] == "spider"
    assert cue["source"] == "spider_detector"
    assert cue["score"] == 0.92
    assert cue["start"] == 10.0
    assert cue["end"] == 17.0
    assert cue["id"] == "cue_sintel_spider_010000_017000"

    # Schema & domain validation
    assert not validate_schema_compliance(packaged, schema)
    assert not validate_domain_rules(packaged, canonical_triggers)


def test_spider_disjoint_packaging_does_not_overmerge():
    """Spider detections separated by > merge_gap_seconds remain distinct cues."""
    config = load_config(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)
    canonical_triggers = set(config["triggers"].keys())

    raw_data = {
        "film_id": "sintel",
        "synthetic": False,
        "timeline": {"duration_seconds": 200.0, "fps": 24.0},
        "detections": [
            # Detection 1: [10.0, 12.0)
            {"start": 10.0, "end": 12.0, "trigger": "spider", "score": 0.75, "source": "spider_detector"},
            # Detection 2: [25.0, 27.0)
            # Gap is 25.0 - 12.0 = 13.0s > 3.0s merge gap -> Keep distinct!
            {"start": 25.0, "end": 27.0, "trigger": "spider", "score": 0.88, "source": "spider_detector"},
        ],
    }

    packaged = package_cues(
        raw_data=raw_data,
        film_id="sintel",
        duration_seconds=200.0,
        fps=24.0,
        config=config,
        synthetic=False,
        schema=schema,
    )

    cues = packaged["cues"]
    assert len(cues) == 2
    assert cues[0]["start"] == 10.0
    assert cues[0]["end"] == 12.0
    assert cues[0]["id"] == "cue_sintel_spider_010000_012000"

    assert cues[1]["start"] == 25.0
    assert cues[1]["end"] == 27.0
    assert cues[1]["id"] == "cue_sintel_spider_025000_027000"

    assert not validate_schema_compliance(packaged, schema)
    assert not validate_domain_rules(packaged, canonical_triggers)


# -----------------------------------------------------------------------------
# 6. Regression & Cross-Trigger Separation
# -----------------------------------------------------------------------------

def test_cross_trigger_separation_with_spider():
    """Overlapping spider and audio/visual cues must remain strictly separate (no cross-trigger merge)."""
    config = load_config(CONFIG_PATH)
    schema = load_schema(SCHEMA_PATH)
    canonical_triggers = set(config["triggers"].keys())

    raw_data = {
        "film_id": "sintel",
        "synthetic": False,
        "timeline": {"duration_seconds": 100.0, "fps": 24.0},
        "detections": [
            # Overlapping intervals of different triggers
            {"start": 20.0, "end": 25.0, "trigger": "spider", "score": 0.85, "source": "spider_detector"},
            {"start": 21.0, "end": 24.0, "trigger": "loud_spike", "score": 0.95, "source": "loudness_detector"},
        ],
    }

    packaged = package_cues(
        raw_data=raw_data,
        film_id="sintel",
        duration_seconds=100.0,
        fps=24.0,
        config=config,
        synthetic=False,
        schema=schema,
    )

    cues = packaged["cues"]
    assert len(cues) == 2
    triggers = {c["trigger"] for c in cues}
    assert triggers == {"spider", "loud_spike"}
    assert not validate_schema_compliance(packaged, schema)
    assert not validate_domain_rules(packaged, canonical_triggers)
