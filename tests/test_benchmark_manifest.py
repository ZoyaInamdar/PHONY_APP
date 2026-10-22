"""Automated verification tests for Ground-Truth Benchmark Foundation.

Verifies:
1. JSON schema conformity of schema/benchmark_manifest.schema.json.
2. Mathematical half-open interval integrity [start, end) where end > start >= 0 and end <= duration.
3. Canonical trigger nomenclature matching project specifications.
4. Anti-leakage scene isolation: zero scene_id overlap across splits (train_dev, validation, locked_test).
5. Negative control integrity: negative controls have no positive target events.
6. Provenance separation: synthetic fixtures and natural media are clearly distinguished.
7. Review-status gating: pending_review events are excluded from verified ground-truth exports.
8. Evaluation compatibility: exported labels.csv and manifest load cleanly into scripts/evaluate_detections.py.
9. Validator failure modes: error detection on corrupt intervals, leaked scenes, and invalid triggers.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from scripts.evaluate_detections import load_clips_manifest, load_ground_truth_labels
from scripts.export_benchmark_labels import export_benchmark_labels
from scripts.validate_benchmark_manifest import (
    CANONICAL_TRIGGERS,
    validate_manifest_dict,
    validate_manifest_file,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "data" / "benchmark" / "manifest.json"
SCHEMA_PATH = REPO_ROOT / "schema" / "benchmark_manifest.schema.json"


@pytest.fixture
def manifest_data() -> Dict[str, Any]:
    assert MANIFEST_PATH.is_file(), f"Benchmark manifest not found: {MANIFEST_PATH}"
    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_benchmark_manifest_exists_and_schema_valid():
    """Verify that benchmark manifest passes full schema and integrity validation."""
    is_valid, errors = validate_manifest_file(
        manifest_path=MANIFEST_PATH,
        schema_path=SCHEMA_PATH,
        check_media=True,
    )
    assert is_valid, f"Benchmark manifest validation failed:\n" + "\n".join(errors)


def test_temporal_intervals_and_bounds(manifest_data: Dict[str, Any]):
    """Verify mathematical half-open interval rules for every event across all clips."""
    for clip in manifest_data["clips"]:
        clip_id = clip["clip_id"]
        duration = clip["duration_seconds"]
        assert duration > 0.0, f"Clip {clip_id} duration must be positive."

        for event in clip.get("events", []):
            event_id = event["event_id"]
            start = event["start"]
            end = event["end"]

            assert start >= 0.0, f"Event {event_id} in {clip_id} has negative start: {start}"
            assert end > start, f"Event {event_id} in {clip_id} has non-positive duration: [{start}, {end}]"
            assert end <= (duration + 1e-3), (
                f"Event {event_id} in {clip_id} ends at {end}s, which exceeds clip duration {duration}s"
            )
            if "boundary_uncertainty_seconds" in event and event["boundary_uncertainty_seconds"] is not None:
                assert event["boundary_uncertainty_seconds"] >= 0.0


def test_canonical_triggers(manifest_data: Dict[str, Any]):
    """Verify that all event triggers belong to the canonical trigger set."""
    for clip in manifest_data["clips"]:
        for event in clip.get("events", []):
            trigger = event["trigger"]
            assert trigger in CANONICAL_TRIGGERS, (
                f"Clip {clip['clip_id']} contains unknown trigger '{trigger}'"
            )


def test_anti_leakage_scene_isolation(manifest_data: Dict[str, Any]):
    """Verify zero scene overlap across splits (anti-leakage guarantee)."""
    splits = {"train_dev": set(), "validation": set(), "locked_test": set()}

    for clip in manifest_data["clips"]:
        split = clip["split"]
        scene_id = clip["scene_id"]
        assert split in splits, f"Unknown split '{split}' in clip {clip['clip_id']}"
        splits[split].add(scene_id)

    # Check pair-wise intersections
    train_val_overlap = splits["train_dev"] & splits["validation"]
    train_test_overlap = splits["train_dev"] & splits["locked_test"]
    val_test_overlap = splits["validation"] & splits["locked_test"]

    assert not train_val_overlap, f"Scene leakage between train_dev and validation: {train_val_overlap}"
    assert not train_test_overlap, f"Scene leakage between train_dev and locked_test: {train_test_overlap}"
    assert not val_test_overlap, f"Scene leakage between validation and locked_test: {val_test_overlap}"

    # Specifically check that locked_test scenes are isolated
    assert len(splits["locked_test"]) > 0, "locked_test split must contain scenes."
    for scene in splits["locked_test"]:
        assert scene.startswith("scene_ed_"), f"Unexpected scene in locked_test: {scene}"


def test_negative_control_integrity(manifest_data: Dict[str, Any]):
    """Verify negative control clips have no positive target events."""
    neg_count = 0
    for clip in manifest_data["clips"]:
        if clip.get("is_negative_control", False):
            neg_count += 1
            events = [e for e in clip.get("events", []) if e.get("review_status") != "rejected"]
            assert len(events) == 0, (
                f"Negative control clip {clip['clip_id']} has {len(events)} positive events."
            )
    assert neg_count >= 8, f"Expected at least 8 negative controls, got {neg_count}"


def test_dataset_provenance_separation(manifest_data: Dict[str, Any]):
    """Verify clear distinction between synthetic fixtures and natural media."""
    synthetic_clips = [c for c in manifest_data["clips"] if c["dataset_type"] == "synthetic"]
    natural_clips = [c for c in manifest_data["clips"] if c["dataset_type"] == "natural"]

    assert len(synthetic_clips) == 34, f"Expected 34 synthetic fixtures, got {len(synthetic_clips)}"
    assert len(natural_clips) >= 7, f"Expected at least 7 natural clips/scenes, got {len(natural_clips)}"

    for clip in synthetic_clips:
        assert clip["split"] == "train_dev", "Synthetic unit fixtures must belong to train_dev"
        assert clip["media_relpath"].startswith("data/test_clips/"), (
            f"Synthetic media must reside in data/test_clips/: {clip['media_relpath']}"
        )

    for clip in natural_clips:
        assert clip["split"] in {"validation", "locked_test"}, (
            f"Natural clip {clip['clip_id']} must belong to validation or locked_test split"
        )
        assert not clip["media_relpath"].startswith("data/test_clips/"), (
            f"Natural media must not reside in data/test_clips/: {clip['media_relpath']}"
        )


def test_review_status_gating_and_export(tmp_path: Path, manifest_data: Dict[str, Any]):
    """Verify that pending_review annotations are excluded from verified ground-truth exports."""
    # Find candidate scenes that should be marked pending_review
    pending_events = []
    verified_events = []
    for clip in manifest_data["clips"]:
        for evt in clip.get("events", []):
            if evt["review_status"] == "pending_review":
                pending_events.append(evt)
            elif evt["review_status"] == "verified":
                verified_events.append(evt)

    assert len(pending_events) > 0, "Benchmark must include pending_review candidate events."
    assert len(verified_events) > 0, "Benchmark must include verified events."

    # Export with default include_pending=False
    out_labels_verified = tmp_path / "verified_labels.csv"
    out_manifest_verified = tmp_path / "verified_manifest.json"
    rows_verified, mf_verified = export_benchmark_labels(
        benchmark_manifest_path=MANIFEST_PATH,
        include_pending=False,
        output_labels_csv=out_labels_verified,
        output_manifest_json=out_manifest_verified,
    )

    # Export with include_pending=True
    out_labels_all = tmp_path / "all_labels.csv"
    out_manifest_all = tmp_path / "all_manifest.json"
    rows_all, mf_all = export_benchmark_labels(
        benchmark_manifest_path=MANIFEST_PATH,
        include_pending=True,
        output_labels_csv=out_labels_all,
        output_manifest_json=out_manifest_all,
    )

    # Verified export must have strictly fewer positive events than include_pending=True
    assert len(rows_verified) < len(rows_all)

    # Build exact lookup keys for pending events: (clip_id, trigger, start, end)
    pending_event_keys = set()
    for clip in manifest_data["clips"]:
        for evt in clip.get("events", []):
            if evt["review_status"] == "pending_review":
                pending_event_keys.add(
                    (clip["clip_id"], evt["trigger"], f"{evt['start']:.3f}", f"{evt['end']:.3f}")
                )

    for row in rows_verified:
        row_key = (row["clip"], row["trigger"], row["start"], row["end"])
        assert row_key not in pending_event_keys, f"Pending event {row_key} leaked into verified export!"

    # Verify that exported files load cleanly into evaluate_detections parser
    loaded_labels = load_ground_truth_labels(out_labels_verified)
    loaded_manifest = load_clips_manifest(out_manifest_verified)
    assert len(loaded_labels) > 0
    assert len(loaded_manifest) > 0


def test_pending_only_and_mixed_clip_gating(tmp_path: Path):
    """Verify export gating for pending-only clips, mixed verified/pending clips, and negative controls."""
    mock_manifest = {
        "schema_version": 1,
        "benchmark_id": "test_gating_manifest",
        "version": "1.0.0",
        "created_at": "2026-10-22T00:00:00Z",
        "description": "Test manifest for gating logic",
        "splits": {
            "train_dev": {"description": "dev", "usage": "dev"},
            "validation": {"description": "val", "usage": "val"},
            "locked_test": {"description": "test", "usage": "test"},
        },
        "clips": [
            # Clip 1: Pending-only -> Must be excluded in verified export
            {
                "clip_id": "clip_pending_only.mp4",
                "film_id": "film_test",
                "media_relpath": "data/test_clips/audio_gunshot_test.mp4",
                "modality": "audio",
                "duration_seconds": 4.0,
                "fps": 24.0,
                "dataset_type": "natural",
                "split": "validation",
                "scene_id": "scene_gating_1",
                "is_negative_control": False,
                "license": "CC-BY",
                "events": [
                    {
                        "event_id": "evt_pending_1",
                        "trigger": "gunshot",
                        "start": 1.0,
                        "end": 2.0,
                        "annotator_id": "pipeline",
                        "review_status": "pending_review",
                    }
                ],
            },
            # Clip 2: Mixed verified and pending -> Only verified event exported
            {
                "clip_id": "clip_mixed.mp4",
                "film_id": "film_test",
                "media_relpath": "data/test_clips/audio_explosion_test.mp4",
                "modality": "audio",
                "duration_seconds": 5.0,
                "fps": 24.0,
                "dataset_type": "natural",
                "split": "validation",
                "scene_id": "scene_gating_2",
                "is_negative_control": False,
                "license": "CC-BY",
                "events": [
                    {
                        "event_id": "evt_mixed_verified",
                        "trigger": "explosion",
                        "start": 1.0,
                        "end": 3.0,
                        "annotator_id": "human_reviewer",
                        "review_status": "verified",
                    },
                    {
                        "event_id": "evt_mixed_pending",
                        "trigger": "scream",
                        "start": 3.5,
                        "end": 4.5,
                        "annotator_id": "pipeline",
                        "review_status": "pending_review",
                    },
                ],
            },
            # Clip 3: Negative control -> Retained with is_negative_control flag
            {
                "clip_id": "clip_negative.mp4",
                "film_id": "film_test",
                "media_relpath": "data/test_clips/video_negative_static.mp4",
                "modality": "video",
                "duration_seconds": 3.0,
                "fps": 24.0,
                "dataset_type": "synthetic",
                "split": "train_dev",
                "scene_id": "scene_gating_3",
                "is_negative_control": True,
                "license": "Internal",
                "events": [],
            },
        ],
    }

    mock_manifest_path = tmp_path / "mock_manifest.json"
    mock_manifest_path.write_text(json.dumps(mock_manifest), encoding="utf-8")

    # 1. Export verified only (include_pending=False)
    v_rows, v_mf = export_benchmark_labels(
        benchmark_manifest_path=mock_manifest_path,
        include_pending=False,
    )
    v_clips = [c["clip_name"] for c in v_mf["clips"]]

    # Pending-only clip must be completely excluded
    assert "clip_pending_only.mp4" not in v_clips
    # Mixed clip must be included
    assert "clip_mixed.mp4" in v_clips
    # Negative control must be included
    assert "clip_negative.mp4" in v_clips

    # For mixed clip, only the verified event (explosion) is exported, NOT scream
    mixed_row_triggers = [r["trigger"] for r in v_rows if r["clip"] == "clip_mixed.mp4"]
    assert "explosion" in mixed_row_triggers
    assert "scream" not in mixed_row_triggers

    # 2. Export all (include_pending=True)
    all_rows, all_mf = export_benchmark_labels(
        benchmark_manifest_path=mock_manifest_path,
        include_pending=True,
    )
    all_clips = [c["clip_name"] for c in all_mf["clips"]]
    assert "clip_pending_only.mp4" in all_clips
    assert "clip_mixed.mp4" in all_clips


def test_explicit_negative_control_evaluation():
    """Verify that negative controls contribute correctly: zero false positives when quiet, and penalize false positives when detector fires."""
    from scripts.evaluate_detections import audit_annotation_coverage, evaluate_clip

    # 1. Audit coverage recognizes is_negative_control without brittle substring matching
    mock_manifest = {
        "calm_quiet_scene.mp4": {
            "clip_name": "calm_quiet_scene.mp4",
            "duration": 10.0,
            "trigger": None,
            "metadata": {
                "is_negative_control": True,
                "event_type": "negative_control",
                "contains_trigger": False,
            },
        }
    }
    mock_labels = {}  # No labels for negative control

    audit = audit_annotation_coverage(mock_manifest, mock_labels, {"flashing"})
    neg_clips = audit["per_trigger_coverage"]["flashing"]["negative_control_clips"]
    assert "calm_quiet_scene.mp4" in neg_clips, "Explicit negative control must be audited as negative_control_clip"

    # 2. Quiet negative control (0 predictions) -> TP=0, FP=0, FN=0
    eval_clean = evaluate_clip(
        clip_name="calm_quiet_scene.mp4",
        trigger="flashing",
        predictions=[],
        ground_truth=[],
    )
    assert eval_clean["true_positives"] == 0
    assert eval_clean["false_positives"] == 0
    assert eval_clean["false_negatives"] == 0

    # 3. Spurious detector alarm on negative control -> TP=0, FP=1, FN=0
    false_pred = [{"start": 2.0, "end": 4.0, "trigger": "flashing", "score": 0.85, "source": "detector"}]
    eval_fp = evaluate_clip(
        clip_name="calm_quiet_scene.mp4",
        trigger="flashing",
        predictions=false_pred,
        ground_truth=[],
    )
    assert eval_fp["true_positives"] == 0
    assert eval_fp["false_positives"] == 1
    assert eval_fp["false_negatives"] == 0
    assert eval_fp["precision"] == 0.0


def test_missing_media_produces_explicit_error(tmp_path: Path):
    """Verify that evaluate_dataset raises explicit FileNotFoundError when a clip file is absent."""
    from scripts.evaluate_detections import evaluate_dataset

    # Create dummy manifest with non-existent clip
    missing_manifest = {
        "generator_version": 1,
        "clip_count": 1,
        "clips": [
            {
                "clip_name": "definitely_non_existent_clip_12345.mp4",
                "duration": 5.0,
                "fps": 24.0,
                "trigger": "loud_spike",
                "start": 1.0,
                "end": 2.0,
                "metadata": {"event_type": "loud_spike", "contains_trigger": True},
            }
        ],
    }
    mf_path = tmp_path / "missing_manifest.json"
    mf_path.write_text(json.dumps(missing_manifest), encoding="utf-8")

    labels_csv_content = (
        "clip,trigger,start,end,labeller,actual_rate\n"
        "definitely_non_existent_clip_12345.mp4,loud_spike,1.000,2.000,annotator,\n"
    )
    lbl_path = tmp_path / "missing_labels.csv"
    lbl_path.write_text(labels_csv_content, encoding="utf-8")

    # Calling evaluate_dataset must raise FileNotFoundError rather than silently returning empty predictions
    with pytest.raises(FileNotFoundError) as exc_info:
        evaluate_dataset(
            manifest_path=mf_path,
            labels_path=lbl_path,
            clips_dir=tmp_path,
            target_triggers={"loud_spike"},
        )
    assert "missing media file for clip 'definitely_non_existent_clip_12345.mp4'" in str(exc_info.value)


def test_validator_detects_corruptions(manifest_data: Dict[str, Any]):
    """Verify that validate_manifest_dict detects invalid intervals, leakage, and corrupt triggers."""
    # 1. Inverted interval
    corrupted_interval = copy.deepcopy(manifest_data)
    corrupted_interval["clips"][0]["events"][0]["start"] = 4.0
    corrupted_interval["clips"][0]["events"][0]["end"] = 2.0
    is_valid, errs = validate_manifest_dict(corrupted_interval, schema_path=SCHEMA_PATH, check_media=False)
    assert not is_valid
    assert any("End must be strictly greater than start" in e for e in errs)

    # 2. Exceeded duration
    corrupted_duration = copy.deepcopy(manifest_data)
    clip_dur = corrupted_duration["clips"][0]["duration_seconds"]
    corrupted_duration["clips"][0]["events"][0]["end"] = clip_dur + 50.0
    is_valid, errs = validate_manifest_dict(corrupted_duration, schema_path=SCHEMA_PATH, check_media=False)
    assert not is_valid
    assert any("exceeds clip duration" in e for e in errs)

    # 3. Scene leakage
    corrupted_leakage = copy.deepcopy(manifest_data)
    # Move a scene_id from train_dev into validation as well
    first_scene = corrupted_leakage["clips"][0]["scene_id"]
    corrupted_leakage["clips"][-1]["scene_id"] = first_scene  # clip -1 is in locked_test
    is_valid, errs = validate_manifest_dict(corrupted_leakage, schema_path=SCHEMA_PATH, check_media=False)
    assert not is_valid
    assert any("Scene leakage detected" in e for e in errs)

    # 4. Unknown trigger
    corrupted_trigger = copy.deepcopy(manifest_data)
    corrupted_trigger["clips"][0]["events"][0]["trigger"] = "flying_saucers"
    is_valid, errs = validate_manifest_dict(corrupted_trigger, schema_path=SCHEMA_PATH, check_media=False)
    assert not is_valid
    assert any("flying_saucers" in e for e in errs)

    # 5. Negative control with positive events
    corrupted_neg = copy.deepcopy(manifest_data)
    for c in corrupted_neg["clips"]:
        if c["is_negative_control"]:
            c["events"].append({
                "event_id": "bogus_event",
                "trigger": "flashing",
                "start": 1.0,
                "end": 2.0,
                "annotator_id": "test",
                "review_status": "verified",
            })
            break
    is_valid, errs = validate_manifest_dict(corrupted_neg, schema_path=SCHEMA_PATH, check_media=False)
    assert not is_valid
    assert any("Negative control clip" in e for e in errs)


def test_negative_control_trigger_specificity():
    """Verify that a clip can be a negative control for one trigger without being negative for every trigger."""
    from scripts.evaluate_detections import audit_annotation_coverage

    manifest = {
        "fast_motion_neg_only.mp4": {
            "clip_name": "fast_motion_neg_only.mp4",
            "duration": 5.0,
            "modality": "video",
            "is_negative_control": True,
            "negative_triggers": ["fast_motion"],
            "metadata": {
                "is_negative_control": True,
                "negative_triggers": ["fast_motion"],
                "modality": "video",
            },
        },
        "pure_audio_ambient.mp4": {
            "clip_name": "pure_audio_ambient.mp4",
            "duration": 5.0,
            "modality": "audio",
            "is_negative_control": True,
            "metadata": {
                "is_negative_control": True,
                "modality": "audio",
            },
        },
    }
    labels: Dict[str, List[Dict[str, Any]]] = {}

    audit = audit_annotation_coverage(
        manifest=manifest,
        labels_by_clip=labels,
        supported_triggers={"fast_motion", "flashing", "loud_spike"},
    )

    # 1. fast_motion_neg_only is negative for fast_motion, but UNCOVERED for flashing
    fm_cov = audit["per_trigger_coverage"]["fast_motion"]
    flash_cov = audit["per_trigger_coverage"]["flashing"]
    assert "fast_motion_neg_only.mp4" in fm_cov["negative_control_clips"]
    assert "fast_motion_neg_only.mp4" not in flash_cov["negative_control_clips"]
    assert "fast_motion_neg_only.mp4" in flash_cov["uncovered_clips"]

    # 2. pure_audio_ambient is negative for loud_spike, but NOT for visual flashing
    loud_cov = audit["per_trigger_coverage"]["loud_spike"]
    assert "pure_audio_ambient.mp4" in loud_cov["negative_control_clips"]
    assert "pure_audio_ambient.mp4" not in flash_cov["negative_control_clips"]
    assert "pure_audio_ambient.mp4" in flash_cov["uncovered_clips"]


def test_empty_timestamp_marker_rows_not_counted_as_positive(tmp_path: Path):
    """Verify that a negative-control marker row with blank timestamps is never counted as a positive event."""
    from scripts.evaluate_detections import load_ground_truth_labels, load_negative_control_markers

    csv_path = tmp_path / "test_labels_with_markers.csv"
    csv_path.write_text(
        "clip,trigger,start,end,labeller,actual_rate\n"
        "video_negative_static.mp4,flashing,,,benchmark_negative_control,\n"
        "audio_positive.mp4,loud_spike,1.000,2.500,annotator_1,24.0\n"
        "fast_motion_neg.mp4,fast_motion,,,benchmark_negative_control,\n",
        encoding="utf-8",
    )

    # load_ground_truth_labels must NOT load negative marker rows as positive intervals
    loaded_gt = load_ground_truth_labels(csv_path)
    assert "video_negative_static.mp4" not in loaded_gt
    assert "fast_motion_neg.mp4" not in loaded_gt
    assert "audio_positive.mp4" in loaded_gt
    assert len(loaded_gt["audio_positive.mp4"]) == 1

    # load_negative_control_markers retrieves them as explicit negative triggers
    markers = load_negative_control_markers(csv_path)
    assert markers.get("video_negative_static.mp4") == {"flashing"}
    assert markers.get("fast_motion_neg.mp4") == {"fast_motion"}
    assert "audio_positive.mp4" not in markers


def test_human_review_workflow_lifecycle(tmp_path: Path):
    """Verify export and import of human reviews without auto-promoting unreviewed candidates."""
    from scripts.benchmark_review_workflow import export_review_sheet, import_review_sheet

    mock_manifest = {
        "schema_version": 1,
        "benchmark_id": "test_bm",
        "version": "1.0.0",
        "created_at": "2026-10-22T00:00:00Z",
        "description": "Test",
        "splits": {
            "train_dev": {"description": "t", "usage": "u"},
            "validation": {"description": "v", "usage": "u"},
            "locked_test": {"description": "l", "usage": "u"},
        },
        "clips": [
            {
                "clip_id": "clip_cand_01.mp4",
                "film_id": "test_film",
                "media_relpath": "data/media/test.mp4",
                "modality": "video",
                "duration_seconds": 10.0,
                "dataset_type": "natural",
                "split": "validation",
                "scene_id": "scene_01",
                "is_negative_control": False,
                "license": "CC-BY",
                "events": [
                    {
                        "event_id": "evt_cand_01",
                        "trigger": "flashing",
                        "start": 1.0,
                        "end": 3.0,
                        "annotator_id": "detector",
                        "review_status": "pending_review",
                    },
                    {
                        "event_id": "evt_cand_02",
                        "trigger": "fast_motion",
                        "start": 4.0,
                        "end": 6.0,
                        "annotator_id": "detector",
                        "review_status": "pending_review",
                    },
                ],
            }
        ],
    }

    m_file = tmp_path / "manifest.json"
    m_file.write_text(json.dumps(mock_manifest, indent=2), encoding="utf-8")

    sheet_file = tmp_path / "review_sheet.csv"
    count = export_review_sheet(manifest_path=m_file, output_csv_path=sheet_file)
    assert count == 2

    # Simulate reviewer completing the sheet:
    # evt_cand_01: VERIFIED_PRESENT by reviewer_jane with adjusted timestamps
    # evt_cand_02: left blank (still pending)
    reviewed_csv_content = (
        "clip_id,film_id,scene_id,event_id,trigger,candidate_start,candidate_end,candidate_uncertainty,candidate_notes,review_decision,reviewed_start,reviewed_end,reviewed_uncertainty,reviewer_id,review_notes\n"
        "clip_cand_01.mp4,test_film,scene_01,evt_cand_01,flashing,1.000,3.000,0.100,,VERIFIED_PRESENT,1.200,2.800,0.050,reviewer_jane,Confirmed sharp strobe\n"
        "clip_cand_01.mp4,test_film,scene_01,evt_cand_02,fast_motion,4.000,6.000,0.100,,,,,,,\n"
    )
    reviewed_file = tmp_path / "completed_reviews.csv"
    reviewed_file.write_text(reviewed_csv_content, encoding="utf-8")

    verified, rejected, pending = import_review_sheet(
        manifest_path=m_file,
        review_csv_path=reviewed_file,
    )
    assert verified == 1
    assert rejected == 0
    assert pending == 1

    # Reload manifest and verify statuses
    updated = json.loads(m_file.read_text(encoding="utf-8"))
    events = updated["clips"][0]["events"]
    evt1 = next(e for e in events if e["event_id"] == "evt_cand_01")
    evt2 = next(e for e in events if e["event_id"] == "evt_cand_02")

    assert evt1["review_status"] == "verified"
    assert evt1["start"] == 1.2
    assert evt1["end"] == 2.8
    assert evt1["annotator_id"] == "reviewer_jane"

    # evt2 must NOT have been auto-promoted; it remains pending_review
    assert evt2["review_status"] == "pending_review"
    assert evt2["start"] == 4.0

