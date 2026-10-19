"""Unit and integration tests for synthetic test-clip generator (Person C).

Verifies:
1. Whole-number frame phase constraints for flash rates.
2. Area geometries and contrast step levels.
3. Synchronized flash+beep calibration clip and separate sync_labels.csv.
4. Generated MP4 media files have valid video and audio streams.
5. Primary ground-truth labels in labels.csv strictly match generated clip durations:
   0.0 <= start < end <= clip_duration.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict

import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.generate_test_clips import (
    CANONICAL_TRIGGERS,
    ClipSpec,
    build_all_clip_specs,
    compute_flash_timing,
    find_tool,
    generate_audio_clip,
    generate_audio_test_suite,
    generate_detail_pattern_clip,
    generate_flash_clip,
    generate_negative_video_clips,
    generate_speech_test_clip,
    generate_sync_clip,
    get_area_geometry,
    load_canonical_triggers,
    read_labels_csv,
    reexport_full_manifest,
    synthesize_audio_event,
    verify_subtitle_parser_on_speech_clip,
    write_labels_csv,
    write_manifest_json,
    write_srt_file,
    write_sync_labels_csv,
)






def probe_clip(clip_path: Path) -> Dict[str, Any]:
    """Probe an MP4 file with ffprobe to extract duration and stream properties."""
    ffprobe_bin = find_tool("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,duration",
        "-of", "json",
        str(clip_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def test_compute_flash_timing_design_constraints():
    """Verify each phase is a whole number of frames and records actual rate."""
    # 24 fps at 4 flashes/sec -> 3 frames bright, 3 dark -> actual 4.0 Hz
    b, d, rate = compute_flash_timing(fps=24.0, target_flash_rate=4.0)
    assert b == 3
    assert d == 3
    assert rate == 4.0

    # 24 fps at 2 flashes/sec -> 6 frames bright, 6 dark -> actual 2.0 Hz
    b, d, rate = compute_flash_timing(fps=24.0, target_flash_rate=2.0)
    assert b == 6
    assert d == 6
    assert rate == 2.0

    # 24 fps at 3 flashes/sec -> 4 frames bright, 4 dark -> actual 3.0 Hz
    b, d, rate = compute_flash_timing(fps=24.0, target_flash_rate=3.0)
    assert b == 4
    assert d == 4
    assert rate == 3.0

    # 24 fps at 6 flashes/sec -> 2 frames bright, 2 dark -> actual 6.0 Hz
    b, d, rate = compute_flash_timing(fps=24.0, target_flash_rate=6.0)
    assert b == 2
    assert d == 2
    assert rate == 6.0

    # 25 fps at 4 flashes/sec -> 3 frames bright, 3 dark -> cycle 6 frames -> actual rate 4.167 Hz
    b, d, rate = compute_flash_timing(fps=25.0, target_flash_rate=4.0)
    assert b == 3
    assert d == 3
    assert rate == pytest.approx(25.0 / 6.0, 0.001)

    # 30 fps at 4 flashes/sec -> 4 frames bright, 4 dark -> cycle 8 frames -> actual rate 3.75 Hz
    b, d, rate = compute_flash_timing(fps=30.0, target_flash_rate=4.0)
    assert b == 4
    assert d == 4
    assert rate == pytest.approx(30.0 / 8.0, 0.001)

    # 60 fps at 4 flashes/sec -> 8 frames bright, 8 dark -> cycle 16 frames -> actual rate 3.75 Hz
    b, d, rate = compute_flash_timing(fps=60.0, target_flash_rate=4.0)
    assert b == 8
    assert d == 8
    assert rate == pytest.approx(60.0 / 16.0, 0.001)


def test_area_geometries():
    """Verify area geometry coordinates and surface fractions."""
    w, h = 640, 360
    # Full
    x, y, box_w, box_h, frac = get_area_geometry("full", w, h)
    assert (x, y, box_w, box_h) == (0, 0, 640, 360)
    assert frac == 1.0

    # Half (50%)
    x, y, box_w, box_h, frac = get_area_geometry("half", w, h)
    assert box_w * box_h == 640 * 180
    assert frac == pytest.approx(0.5, 0.01)

    # Quarter (25% - WCAG boundary)
    x, y, box_w, box_h, frac = get_area_geometry("quarter", w, h)
    assert (box_w, box_h) == (320, 180)
    assert frac == pytest.approx(0.25, 0.01)

    # Small patch (6.25% - sub-threshold)
    x, y, box_w, box_h, frac = get_area_geometry("small_patch", w, h)
    assert (box_w, box_h) == (160, 90)
    assert frac == pytest.approx(0.0625, 0.01)

    with pytest.raises(ValueError):
        get_area_geometry("invalid_area", w, h)


def test_write_and_read_labels_csv(tmp_path: Path):
    """Verify labels.csv serialization, sorting, and validation."""
    labels_file = tmp_path / "labels.csv"
    records = [
        {"clip": "clip_b.mp4", "trigger": "flashing", "start": 2.0, "end": 4.5, "labeller": "synthetic_generator"},
        {"clip": "clip_a.mp4", "trigger": "flashing", "start": 1.0, "end": 3.0, "labeller": "synthetic_generator", "actual_rate": 4.0},
    ]

    out_path = write_labels_csv(records, labels_file)
    assert out_path.is_file()

    loaded = read_labels_csv(labels_file)
    assert len(loaded) == 2
    # Verify sorting by clip
    assert loaded[0]["clip"] == "clip_a.mp4"
    assert loaded[1]["clip"] == "clip_b.mp4"

    # Invalid trigger rejection
    with pytest.raises(ValueError, match="Invalid trigger"):
        write_labels_csv([{"clip": "c.mp4", "trigger": "non_canonical", "start": 1.0, "end": 2.0}], labels_file)

    # Invalid interval rejection
    with pytest.raises(ValueError, match="Invalid interval"):
        write_labels_csv([{"clip": "c.mp4", "trigger": "flashing", "start": 2.0, "end": 1.0}], labels_file)


def test_generate_sync_clip_and_duration_check(tmp_path: Path):
    """Verify sync clip generation, streams presence, and sync_labels.csv."""
    mp4_path, specs = generate_sync_clip(
        output_dir=tmp_path,
        filename="test_sync.mp4",
        duration=3.0,
        fps=24.0,
        marker_intervals=[(1.0, 1.2), (2.0, 2.2)],
    )

    assert mp4_path.is_file()
    assert len(specs) == 2

    # Probe file with ffprobe
    probe_data = probe_clip(mp4_path)
    streams = probe_data.get("streams", [])
    codec_types = {s["codec_type"] for s in streams}
    assert "video" in codec_types
    assert "audio" in codec_types

    duration_s = float(probe_data["format"]["duration"])
    assert duration_s == pytest.approx(3.0, abs=0.1)

    # Verify sync_labels.csv is written and isolated from primary labels.csv
    sync_csv = tmp_path / "sync_labels.csv"
    assert sync_csv.is_file()
    with sync_csv.open("r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2
        for row in reader:
            assert 0.0 <= float(row["start"]) < float(row["end"]) <= duration_s
            assert row["clip"] == "test_sync.mp4"


def test_labels_match_generated_clip_durations(tmp_path: Path):
    """Core Delivery 1 Test: Ensure intervals in labels.csv match generated clip durations."""
    test_cases = [
        ("flash_24fps_4hz_full.mp4", 24.0, 4.0, "full", 0.50),
        ("flash_25fps_3hz_quarter.mp4", 25.0, 3.0, "quarter", 0.20),
        ("flash_30fps_2hz_half.mp4", 30.0, 2.0, "half", 0.10),
        ("flash_60fps_6hz_patch.mp4", 60.0, 6.0, "small_patch", 0.05),
    ]

    all_specs: list[ClipSpec] = []
    for fname, fps, rate, area, contrast in test_cases:
        p, spec = generate_flash_clip(
            output_dir=tmp_path,
            filename=fname,
            fps=fps,
            target_flash_rate=rate,
            area=area,
            contrast_step=contrast,
            lead_in_seconds=0.5,
            num_cycles=4,
            lead_out_seconds=0.5,
        )
        assert p.is_file()
        all_specs.append(spec)

    labels_csv_path = tmp_path / "labels.csv"
    write_labels_csv(
        [s.to_label_dict() for s in all_specs if s.to_label_dict()],
        labels_csv_path,
    )

    # Read back and probe media to verify truth intervals against real video streams
    labels = read_labels_csv(labels_csv_path)
    assert len(labels) == len(test_cases)

    for row in labels:
        clip_file = tmp_path / row["clip"]
        assert clip_file.is_file(), f"Clip file missing: {clip_file}"

        probe_data = probe_clip(clip_file)
        streams = probe_data.get("streams", [])
        codec_types = {s["codec_type"] for s in streams}
        assert "video" in codec_types, f"Clip {row['clip']} must contain a video track"
        assert "audio" in codec_types, f"Clip {row['clip']} must contain an audio track"

        media_duration = float(probe_data["format"]["duration"])
        start = float(row["start"])
        end = float(row["end"])

        # Label constraint 1: Start must be non-negative
        assert start >= 0.0, f"Start time {start} is negative in {row['clip']}"

        # Label constraint 2: End must be strictly greater than start
        assert end > start, f"End time {end} <= start {start} in {row['clip']}"

        # Label constraint 3: End must not exceed clip duration
        assert end <= media_duration + 0.05, (
            f"Label interval [{start}, {end}) exceeds clip duration {media_duration} in {row['clip']}"
        )

        # Label constraint 4: Trigger must be canonical 'flashing'
        assert row["trigger"] in CANONICAL_TRIGGERS
        assert row["trigger"] == "flashing"


def test_generate_detail_pattern_clip_no_label(tmp_path: Path):
    """Verify detail pattern clip has valid media streams and is NEVER written to labels.csv."""
    mp4_path, spec = generate_detail_pattern_clip(
        output_dir=tmp_path,
        filename="test_detail.mp4",
        duration=2.0,
        fps=24.0,
    )
    assert mp4_path.is_file()

    # Verify stream properties
    probe_data = probe_clip(mp4_path)
    streams = probe_data.get("streams", [])
    codec_types = {s["codec_type"] for s in streams}
    assert "video" in codec_types
    assert "audio" in codec_types

    duration_s = float(probe_data["format"]["duration"])
    assert duration_s == pytest.approx(2.0, abs=0.1)

    # Constraint: Must NOT have a trigger label and must NOT convert to a label row
    assert spec.trigger is None
    assert spec.start is None
    assert spec.end is None
    assert spec.to_label_dict() is None

    # Write labels with spec included and confirm it's omitted
    labels_csv = tmp_path / "labels.csv"
    specs_to_write = [s for s in [spec] if s.to_label_dict() is not None]
    assert len(specs_to_write) == 0
    write_labels_csv(specs_to_write, labels_csv)

    loaded = read_labels_csv(labels_csv)
    assert len(loaded) == 0


def test_synthesize_audio_event():
    """Verify waveform synthesis for all positive and negative audio categories."""
    for event_type in ["gunshot", "explosion", "scream", "ambient_noise", "calm_dialogue"]:
        wave = synthesize_audio_event(event_type, duration=0.5, sample_rate=44100)
        assert len(wave) == int(0.5 * 44100)
        assert abs(float(wave.max())) <= 1.0
        assert abs(float(wave.min())) <= 1.0

    with pytest.raises(ValueError):
        synthesize_audio_event("invalid_sound", duration=0.5)


def test_generate_audio_clip_positive(tmp_path: Path):
    """Verify gunshot, explosion, and scream clips are wrapped in MP4 and logged to labels.csv."""
    test_events = [
        ("audio_gunshot.mp4", [("gunshot", 1.0, 1.5)], 3.0),
        ("audio_explosion.mp4", [("explosion", 0.5, 2.5)], 4.0),
        ("audio_scream.mp4", [("scream", 1.0, 2.2)], 3.0),
    ]

    all_specs: list[ClipSpec] = []
    for fname, evts, dur in test_events:
        mp4_path, specs = generate_audio_clip(
            output_dir=tmp_path,
            filename=fname,
            duration=dur,
            events=evts,
        )
        assert mp4_path.is_file()
        all_specs.extend(specs)

        # Verify video and audio tracks
        probe_data = probe_clip(mp4_path)
        streams = probe_data.get("streams", [])
        codec_types = {s["codec_type"] for s in streams}
        assert "video" in codec_types
        assert "audio" in codec_types
        media_dur = float(probe_data["format"]["duration"])
        assert media_dur == pytest.approx(dur, abs=0.1)

    labels_csv = tmp_path / "labels.csv"
    write_labels_csv([s.to_label_dict() for s in all_specs if s.to_label_dict()], labels_csv)

    loaded_labels = read_labels_csv(labels_csv)
    assert len(loaded_labels) == 3

    triggers = {r["trigger"] for r in loaded_labels}
    assert triggers == {"gunshot", "explosion", "scream"}

    for row in loaded_labels:
        assert 0.0 <= row["start"] < row["end"]
        assert row["trigger"] in CANONICAL_TRIGGERS


def test_generate_negative_audio_clips(tmp_path: Path):
    """Verify negative audio clips (ambient noise, calm dialogue) are NOT written to labels.csv."""
    negative_events = [
        ("audio_ambient.mp4", [("ambient_noise", 0.0, 3.0)], 3.0),
        ("audio_dialogue.mp4", [("calm_dialogue", 0.0, 3.0)], 3.0),
    ]

    all_specs: list[ClipSpec] = []
    for fname, evts, dur in negative_events:
        mp4_path, specs = generate_audio_clip(
            output_dir=tmp_path,
            filename=fname,
            duration=dur,
            events=evts,
        )
        assert mp4_path.is_file()
        all_specs.extend(specs)

    # All negative specs must have trigger=None and to_label_dict()=None
    for s in all_specs:
        assert s.trigger is None
        assert s.to_label_dict() is None

    labels_csv = tmp_path / "labels.csv"
    valid_rows = [s.to_label_dict() for s in all_specs if s.to_label_dict() is not None]
    assert len(valid_rows) == 0
    write_labels_csv(valid_rows, labels_csv)

    loaded = read_labels_csv(labels_csv)
    assert len(loaded) == 0


def test_write_srt_file(tmp_path: Path):
    """Verify that write_srt_file produces valid SubRip formatting."""
    cues = [
        {"start": 1.0, "end": 2.5, "text": "First line"},
        {"start": 3.0, "end": 5.25, "text": "Second line"},
    ]
    srt_file = tmp_path / "test.srt"
    write_srt_file(cues, srt_file)

    assert srt_file.is_file()
    content = srt_file.read_text(encoding="utf-8")
    assert "1\n00:00:01,000 --> 00:00:02,500\nFirst line" in content
    assert "2\n00:00:03,000 --> 00:00:05,250\nSecond line" in content


def test_generate_speech_test_clip_and_parser_integration(tmp_path: Path):
    """Verify speech clip generation, matching .srt, and subtitle parser integration."""
    mp4_path, srt_path, specs = generate_speech_test_clip(
        output_dir=tmp_path,
        mp4_filename="test_speech.mp4",
        srt_filename="test_speech.srt",
    )

    assert mp4_path.is_file()
    assert srt_path.is_file()

    # 1. Verify subtitle parser caught the expected profanities
    audit = verify_subtitle_parser_on_speech_clip(srt_path)
    assert audit["total_cues"] >= 5
    assert audit["speech_cues"] >= 4
    assert audit["sound_descriptions"] >= 1
    assert audit["flagged_cues"] >= 3

    # 2. Verify media streams
    probe_data = probe_clip(mp4_path)
    streams = probe_data.get("streams", [])
    codec_types = {s["codec_type"] for s in streams}
    assert "video" in codec_types
    assert "audio" in codec_types

    duration_s = float(probe_data["format"]["duration"])
    assert duration_s >= 16.0

    # 3. Verify labels.csv contains the profanity entries
    labels_csv = tmp_path / "labels.csv"
    assert labels_csv.is_file()
    records = read_labels_csv(labels_csv)
    assert len(records) == 3

    for row in records:
        assert row["clip"] == "test_speech.mp4"
        assert row["trigger"] == "profanity"
        assert row["trigger"] in CANONICAL_TRIGGERS
        assert 0.0 <= float(row["start"]) < float(row["end"]) <= duration_s


def test_generate_negative_video_clips(tmp_path: Path):
    """Verify negative video clips (gradients, slow fades, static) have valid streams and no labels."""
    paths, specs = generate_negative_video_clips(output_dir=tmp_path)
    assert len(paths) == 3
    assert len(specs) == 3

    for p in paths:
        assert p.is_file()
        probe_data = probe_clip(p)
        streams = probe_data.get("streams", [])
        codec_types = {s["codec_type"] for s in streams}
        assert "video" in codec_types
        assert "audio" in codec_types
        assert float(probe_data["format"]["duration"]) >= 4.9

    for s in specs:
        assert s.trigger is None
        assert s.start is None
        assert s.end is None
        assert s.to_label_dict() is None

    labels_csv = tmp_path / "labels.csv"
    valid_rows = [s.to_label_dict() for s in specs if s.to_label_dict() is not None]
    assert len(valid_rows) == 0
    write_labels_csv(valid_rows, labels_csv)

    loaded = read_labels_csv(labels_csv)
    assert len(loaded) == 0


def test_canonical_triggers_loaded_from_config(tmp_path: Path):
    """Verify triggers are loaded dynamically from config.yaml and invalid triggers fail."""
    # 1. Triggers loaded from config.yaml match the 9 canonical triggers
    expected_triggers = {
        "flashing",
        "loud_spike",
        "gunshot",
        "explosion",
        "scream",
        "profanity",
        "spider",
        "fast_motion",
        "jump_scare",
    }
    assert CANONICAL_TRIGGERS == expected_triggers

    # Explicitly verify legacy/obsolete triggers are NOT present
    assert "violence" not in CANONICAL_TRIGGERS
    assert "gore" not in CANONICAL_TRIGGERS

    # 2. ClipSpec.to_label_dict fails if trigger is not in CANONICAL_TRIGGERS
    spec_violence = ClipSpec(
        clip_name="test_violence.mp4",
        duration=5.0,
        fps=24.0,
        trigger="violence",
        start=1.0,
        end=3.0,
    )
    with pytest.raises(ValueError, match="Invalid trigger 'violence'"):
        spec_violence.to_label_dict()

    spec_gore = ClipSpec(
        clip_name="test_gore.mp4",
        duration=5.0,
        fps=24.0,
        trigger="gore",
        start=1.0,
        end=3.0,
    )
    with pytest.raises(ValueError, match="Invalid trigger 'gore'"):
        spec_gore.to_label_dict()

    # 3. write_labels_csv fails if an invalid trigger is passed
    dummy_csv = tmp_path / "test_labels.csv"
    with pytest.raises(ValueError, match="Invalid trigger 'violence'"):
        write_labels_csv([{"clip": "v.mp4", "trigger": "violence", "start": 1.0, "end": 2.0}], dummy_csv)

    # 4. load_canonical_triggers falls back to official 9 + 4 addiction triggers if config file cannot be read
    fallback_triggers = load_canonical_triggers(tmp_path / "non_existent.yaml")
    expected_fallback = {
        "flashing",
        "loud_spike",
        "gunshot",
        "explosion",
        "scream",
        "profanity",
        "spider",
        "fast_motion",
        "jump_scare",
        "alcohol",
        "narcotics",
        "gambling",
        "tobacco",
    }
    assert fallback_triggers == expected_fallback
    assert "violence" not in fallback_triggers
    assert "gore" not in fallback_triggers

    # Also falls back on invalid config
    bad_config = tmp_path / "bad_config.yaml"
    bad_config.write_text("other_field: 123\n", encoding="utf-8")
    assert load_canonical_triggers(bad_config) == expected_fallback


def test_manifest_reexport_and_deterministic_audio(tmp_path: Path):
    """Verify manifest export covers all 30 clips with metadata, and audio synthesis is deterministic."""
    import numpy as np

    # 1. Deterministic audio generation with fixed seed
    wave1 = synthesize_audio_event("gunshot", duration=1.0, seed=42)
    wave2 = synthesize_audio_event("gunshot", duration=1.0, seed=42)
    wave3 = synthesize_audio_event("gunshot", duration=1.0, seed=99)
    assert np.array_equal(wave1, wave2)
    assert not np.array_equal(wave1, wave3)

    # 2. Build all specs returns 30 distinct clips (28 base suite + 2 loud_spike clips)
    specs = build_all_clip_specs()
    assert len(specs) == 30
    clip_names = {s.clip_name for s in specs}
    assert len(clip_names) == 30
    assert "audio_loud_spike_test.mp4" in clip_names
    assert "audio_loud_spike_isolated.mp4" in clip_names

    # 3. Export manifest to tmp_path
    manifest_p = tmp_path / "manifest.json"
    write_manifest_json(specs, manifest_p)
    assert manifest_p.is_file()

    with manifest_p.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["clip_count"] == 30
    assert len(data["clips"]) == 30

    # Check that each clip contains required metadata fields
    for c in data["clips"]:
        assert "clip_name" in c
        assert "duration" in c and c["duration"] > 0
        assert "fps" in c and c["fps"] > 0
        assert "width" in c and c["width"] == 640
        assert "height" in c and c["height"] == 360
        assert "metadata" in c


def test_actual_rate_and_loud_spike_labels(tmp_path: Path):
    """Verify actual_rate is correctly populated for flash clips and loud_spike labels are written."""
    # 1. Flash clip spec populates actual_rate in to_label_dict
    flash_spec = ClipSpec(
        clip_name="flash_test.mp4",
        duration=5.0,
        fps=24.0,
        trigger="flashing",
        start=1.0,
        end=3.0,
        metadata={"target_flash_rate": 4.0, "actual_flash_rate": 4.167},
    )
    label_dict = flash_spec.to_label_dict()
    assert label_dict is not None
    assert label_dict["trigger"] == "flashing"
    assert label_dict["actual_rate"] == 4.167

    # 2. loud_spike clip spec produces proper label
    loud_spec = ClipSpec(
        clip_name="audio_loud_spike_test.mp4",
        duration=6.0,
        fps=24.0,
        trigger="loud_spike",
        start=2.0,
        end=2.5,
        metadata={"event_type": "loud_spike"},
    )
    loud_dict = loud_spec.to_label_dict()
    assert loud_dict is not None
    assert loud_dict["trigger"] == "loud_spike"
    assert loud_dict["start"] == 2.0
    assert loud_dict["end"] == 2.5

    # 3. write_labels_csv and read_labels_csv preserve actual_rate
    csv_file = tmp_path / "test_labels.csv"
    write_labels_csv([label_dict, loud_dict], csv_file)

    loaded = read_labels_csv(csv_file)
    assert len(loaded) == 2
    flash_row = next(r for r in loaded if r["trigger"] == "flashing")
    loud_row = next(r for r in loaded if r["trigger"] == "loud_spike")
    assert flash_row["actual_rate"] == 4.167
    assert "actual_rate" not in loud_row or loud_row["actual_rate"] is None or loud_row["actual_rate"] == ""


def test_synthetic_dataset_overall_integrity():
    """Verify that the live data/test_clips dataset passes all comprehensive integrity checks."""
    from scripts.validate_synthetic_dataset import validate_dataset

    report = validate_dataset()
    assert report["is_valid"] is True
    assert len(report["errors"]) == 0
    assert report["total_mp4_files"] in (30, 31, 34, 35)
    assert report["unique_manifest_clips"] in (30, 34)
    assert report["total_label_rows"] in (31, 35)
    assert report["flashing_actual_rates_valid"] is True
    assert report["loudness_clips_valid"] is True


def test_multi_trigger_fixture_labels():
    """Verify that multi_trigger_test.mp4 in labels.csv contains expected triggers and intervals."""
    from scripts.generate_test_clips import read_labels_csv, DEFAULT_LABELS_PATH

    records = read_labels_csv(DEFAULT_LABELS_PATH)
    multi_records = [r for r in records if r["clip"] == "multi_trigger_test.mp4"]
    assert len(multi_records) == 4

    triggers = [r["trigger"] for r in multi_records]
    assert triggers == ["profanity", "loud_spike", "profanity", "loud_spike"]

    # Verify chronological intervals
    intervals = [(float(r["start"]), float(r["end"])) for r in multi_records]
    assert intervals == [(1.0, 3.5), (5.0, 5.4), (7.0, 9.5), (11.0, 11.3)]


def test_experimental_test_suite_specs_and_labels(tmp_path: Path):
    """Verify experimental suite generates fast_motion and jump_scare specs with exact intervals."""
    from scripts.generate_test_clips import (
        build_all_clip_specs,
        generate_experimental_test_suite,
    )

    specs = build_all_clip_specs(include_experimental=True)
    assert len(specs) == 34
    exp_clip_names = {s.clip_name for s in specs}
    assert "fast_motion_positive.mp4" in exp_clip_names
    assert "fast_motion_negative.mp4" in exp_clip_names
    assert "jump_scare_positive.mp4" in exp_clip_names
    assert "jump_scare_negative.mp4" in exp_clip_names

    paths, exp_specs = generate_experimental_test_suite(output_dir=tmp_path)
    assert len(paths) == 6
    assert (tmp_path / "fast_motion_positive.mp4").is_file()
    assert (tmp_path / "fast_motion_negative.mp4").is_file()
    assert (tmp_path / "jump_scare_positive.mp4").is_file()
    assert (tmp_path / "jump_scare_positive.wav").is_file()
    assert (tmp_path / "jump_scare_negative.mp4").is_file()
    assert (tmp_path / "jump_scare_negative.wav").is_file()

    # Verify manifest
    manifest_file = tmp_path / "manifest.json"
    assert manifest_file.is_file()
    with manifest_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["clip_count"] == 34

    # Verify labels.csv
    labels_file = tmp_path / "labels.csv"
    assert labels_file.is_file()
    with labels_file.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fm_pos = next(r for r in rows if r["clip"] == "fast_motion_positive.mp4")
    assert fm_pos["trigger"] == "fast_motion"
    assert float(fm_pos["start"]) == 0.0
    assert float(fm_pos["end"]) == 3.0

    fm_neg = next(r for r in rows if r["clip"] == "fast_motion_negative.mp4")
    assert fm_neg["trigger"] == "fast_motion"
    assert fm_neg["start"] == ""
    assert fm_neg["end"] == ""

    js_pos = next(r for r in rows if r["clip"] == "jump_scare_positive.mp4")
    assert js_pos["trigger"] == "jump_scare"
    assert float(js_pos["start"]) == 1.5
    assert float(js_pos["end"]) == 1.6

    js_neg = next(r for r in rows if r["clip"] == "jump_scare_negative.mp4")
    assert js_neg["trigger"] == "jump_scare"
    assert js_neg["start"] == ""
    assert js_neg["end"] == ""



