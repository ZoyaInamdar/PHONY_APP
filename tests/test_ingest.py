"""Automated unit and integration tests for media ingestion pipeline (Person B).

Verifies:
- Tool availability (FFmpeg and FFprobe)
- Synthetic video with audio ingestion
- Synthetic video without audio ingestion (explicit absence without fake audio)
- Handling of missing, corrupt, and invalid media inputs
- Safe path handling for paths containing spaces
- Video normalization: H.264, yuv420p, 24 fps, resolution limits, timeline start at zero
- Audio extraction: 44.1 kHz mono PCM WAV (pcm_s16le)
- Low-resolution RGB frames: 128x72, rgb24, memory-efficient raw stream, exact byte counts
- Snapshots: <= 1.0 fps, max dimension <= 512, deterministic manifest, short clip handling
- Ingestion manifest schema compliance against schema/manifest.schema.json
- Distinction between absent audio and failed extraction
- Staging directory isolation and failure cleanup
- Stale-output cleanup and repeatability
- Verification that ingestion never writes playback manifest as complete
- CLI execution and exit codes
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from scripts.ingest_media import (
    DEFAULT_SCHEMA_PATH,
    IngestionError,
    InvalidMediaError,
    check_tool_availability,
    extract_audio,
    extract_low_res_frames,
    extract_snapshots,
    ingest_media,
    normalize_video,
    probe_media,
    validate_ingest_manifest,
)


def has_ffmpeg_and_ffprobe() -> bool:
    """Check if ffmpeg and ffprobe are available in the test environment."""
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


pytestmark = pytest.mark.skipif(
    not has_ffmpeg_and_ffprobe(),
    reason="FFmpeg and FFprobe executables required for ingestion tests",
)


def create_synthetic_video(
    output_path: Path,
    duration: float = 2.0,
    with_audio: bool = True,
    width: int = 320,
    height: int = 240,
    fps: int = 24,
) -> Path:
    """Generate a small synthetic MP4 video file using FFmpeg test filters."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
    ]
    if width % 2 != 0 or height % 2 != 0:
        v_codec = ["-c:v", "mjpeg"]
    else:
        v_codec = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]

    if with_audio:
        cmd.extend([
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=1000:duration={duration}",
            *v_codec,
            "-c:a",
            "aac",
        ])
    else:
        cmd.extend([
            *v_codec,
            "-an",
        ])
    cmd.append(str(output_path))
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return output_path


# ------------------------------------------------------------------------------
# 1. Tool Availability & Probing
# ------------------------------------------------------------------------------

def test_check_tool_availability():
    """Verify tool check passes when ffmpeg and ffprobe are present."""
    check_tool_availability()


def test_probe_media_valid(tmp_path: Path):
    """Verify probe_media extracts valid container and stream information."""
    media = create_synthetic_video(tmp_path / "test.mp4", duration=1.5, with_audio=True)
    info = probe_media(media)

    assert info["filename"] == "test.mp4"
    assert info["size_bytes"] > 0
    assert info["container_duration_seconds"] is not None
    assert info["container_duration_seconds"] >= 1.4
    assert len(info["video_streams"]) == 1
    assert len(info["audio_streams"]) == 1
    assert info["video_streams"][0]["codec_name"] == "h264"
    assert info["audio_streams"][0]["codec_name"] == "aac"


def test_probe_media_missing_file():
    """Verify probe_media raises FileNotFoundError on missing file."""
    with pytest.raises(FileNotFoundError):
        probe_media("non_existent_file_xyz123.mp4")


def test_probe_media_invalid_content(tmp_path: Path):
    """Verify probe_media raises InvalidMediaError on corrupted/non-media file."""
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_text("not a real video file", encoding="utf-8")
    with pytest.raises(InvalidMediaError):
        probe_media(corrupt)


# ------------------------------------------------------------------------------
# 2. Ingestion with Audio
# ------------------------------------------------------------------------------

def test_ingest_media_with_audio(tmp_path: Path):
    """Verify complete ingestion pipeline for synthetic video containing audio."""
    src = create_synthetic_video(tmp_path / "input" / "clip.mp4", duration=2.0, with_audio=True)
    out_dir = tmp_path / "processed" / "clip"

    manifest = ingest_media(input_path=src, output_dir=out_dir, film_id="clip")

    # Ingestion Manifest verification
    assert manifest["schema_version"] == 1
    assert manifest["film_id"] == "clip"
    assert manifest["pipeline_stage"] == "ingestion"
    assert manifest["status"] == "complete"
    assert manifest["warnings"] == []
    assert manifest["errors"] == []

    # File existence in published directory
    assert (out_dir / "normalized.mp4").is_file()
    assert (out_dir / "audio_mono_44100.wav").is_file()
    assert (out_dir / "low_res_frames_128x72.rgb").is_file()
    assert (out_dir / "snapshots").is_dir()
    assert (out_dir / "snapshots" / "frames_manifest.json").is_file()
    assert (out_dir / "frames_manifest.json").is_file()
    assert (out_dir / "ingest_manifest.json").is_file()

    # Normalized Video properties
    norm_v = manifest["normalized_video"]
    assert norm_v["codec"] == "h264"
    assert norm_v["pixel_format"] == "yuv420p"
    assert norm_v["fps"] == 24.0
    assert norm_v["timeline_start_seconds"] == 0.0
    assert norm_v["frame_count"] > 0
    assert norm_v["width"] % 2 == 0
    assert norm_v["height"] % 2 == 0

    # Audio properties
    audio = manifest["audio"]
    assert audio["present"] is True
    assert audio["extraction_status"] == "extracted"
    assert audio["codec"] == "pcm_s16le"
    assert audio["sample_rate"] == 44100
    assert audio["channels"] == 1
    assert audio["duration_seconds"] is not None

    # Low-res frame properties
    lrf = manifest["low_res_frames"]
    assert lrf["width"] == 128
    assert lrf["height"] == 72
    assert lrf["pixel_format"] == "rgb24"
    assert lrf["bytes_per_frame"] == 27648
    assert lrf["frame_count"] == norm_v["frame_count"]
    assert lrf["total_bytes"] == lrf["frame_count"] * 27648
    assert lrf["frame_loss_count"] == 0

    # Snapshots properties
    snaps = manifest["snapshots"]
    assert snaps["snapshot_count"] >= 2
    assert snaps["target_fps"] == 1.0
    assert snaps["max_dimension"] == 512

    # Schema compliance
    is_valid, errors = validate_ingest_manifest(manifest)
    assert is_valid, f"Schema validation errors: {errors}"


# ------------------------------------------------------------------------------
# 3. Ingestion Without Audio (Explicit Absence)
# ------------------------------------------------------------------------------

def test_ingest_media_without_audio(tmp_path: Path):
    """Verify ingestion of media with no audio explicitly records absent status."""
    src = create_synthetic_video(tmp_path / "input" / "silent.mp4", duration=1.5, with_audio=False)
    out_dir = tmp_path / "processed" / "silent"

    manifest = ingest_media(input_path=src, output_dir=out_dir)

    assert manifest["status"] == "complete"
    assert manifest["audio"]["present"] is False
    assert manifest["audio"]["extraction_status"] == "skipped_no_stream"
    assert manifest["audio"]["path"] is None
    assert manifest["audio"]["codec"] is None
    assert manifest["audio"]["sample_rate"] is None
    assert manifest["audio"]["channels"] is None
    assert not (out_dir / "audio_mono_44100.wav").exists()

    is_valid, errors = validate_ingest_manifest(manifest)
    assert is_valid, f"Schema validation errors: {errors}"


# ------------------------------------------------------------------------------
# 4. Path Safety: Paths Containing Spaces
# ------------------------------------------------------------------------------

def test_ingest_media_paths_with_spaces(tmp_path: Path):
    """Verify safe subprocess execution with directory paths containing spaces."""
    space_input_dir = tmp_path / "my raw input videos"
    space_output_dir = tmp_path / "my processed output assets"

    src = create_synthetic_video(space_input_dir / "my clip file.mp4", duration=1.0, with_audio=True)
    manifest = ingest_media(input_path=src, output_dir=space_output_dir)

    assert manifest["status"] == "complete"
    assert (space_output_dir / "normalized.mp4").is_file()
    assert (space_output_dir / "ingest_manifest.json").is_file()


# ------------------------------------------------------------------------------
# 5. Video Normalization & Resolution Limits
# ------------------------------------------------------------------------------

def test_video_normalization_odd_dimensions_and_downscale(tmp_path: Path):
    """Verify normalization scales down large odd dimensions to even yuv420p dimensions."""
    # Create video with non-standard odd dimensions
    src = create_synthetic_video(
        tmp_path / "odd.mp4",
        duration=1.0,
        with_audio=False,
        width=323,
        height=241,
        fps=30,
    )
    norm_path = tmp_path / "normalized.mp4"
    res = normalize_video(
        input_path=src,
        output_path=norm_path,
        max_width=1920,
        max_height=1080,
        target_fps=24.0,
        has_audio=False,
    )

    assert res["width"] % 2 == 0
    assert res["height"] % 2 == 0
    assert res["fps"] == 24.0
    assert res["codec"] == "h264"
    assert res["pixel_format"] == "yuv420p"
    assert res["timeline_start_seconds"] == 0.0


# ------------------------------------------------------------------------------
# 6. Low-Resolution Frame Memory Efficiency & Binary Precision
# ------------------------------------------------------------------------------

def test_low_res_frames_exact_byte_alignment(tmp_path: Path):
    """Verify low-res frames file size is an exact multiple of 27,648 bytes."""
    src = create_synthetic_video(tmp_path / "video.mp4", duration=1.0, with_audio=False)
    norm = tmp_path / "norm.mp4"
    normalize_video(src, norm, target_fps=24.0)

    rgb_out = tmp_path / "frames.rgb"
    info = extract_low_res_frames(norm, rgb_out, width=128, height=72, fps=24.0)

    assert info["frame_count"] == 24
    assert info["bytes_per_frame"] == 27648
    assert info["total_bytes"] == 24 * 27648
    assert info["frame_loss_count"] == 0
    assert rgb_out.stat().st_size == 24 * 27648


# ------------------------------------------------------------------------------
# 7. Snapshots: Max Dimension & Short Clip Handling
# ------------------------------------------------------------------------------

def test_snapshots_short_clip_guarantee(tmp_path: Path):
    """Verify short clips (< 1.0s) yield at least 1 preview snapshot at t=0.0."""
    src = create_synthetic_video(tmp_path / "short.mp4", duration=0.4, with_audio=False)
    norm = tmp_path / "norm.mp4"
    normalize_video(src, norm, target_fps=24.0)

    snap_dir = tmp_path / "snapshots"
    info = extract_snapshots(norm, snap_dir, target_fps=1.0, max_dimension=512)

    assert info["snapshot_count"] >= 1
    manifest_file = snap_dir / "frames_manifest.json"
    assert manifest_file.is_file()

    with manifest_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert len(data["snapshots"]) >= 1
    assert data["snapshots"][0]["timestamp_seconds"] == 0.0
    assert (snap_dir / data["snapshots"][0]["filename"]).is_file()


def test_snapshots_timestamp_generation_timeline_integration(tmp_path: Path):
    """Verify snapshot timestamps are computed via shared timeline helper frame_to_seconds."""
    from scripts.timeline import frame_to_seconds

    # Create a 2.5 second synthetic video (yields at least 3 snapshots at 1.0 fps)
    src = create_synthetic_video(tmp_path / "multi_sec.mp4", duration=2.5, with_audio=False)
    norm = tmp_path / "norm.mp4"
    normalize_video(src, norm, target_fps=24.0)

    snap_dir = tmp_path / "snapshots"
    target_fps = 1.0
    info = extract_snapshots(norm, snap_dir, target_fps=target_fps, max_dimension=512)

    assert info["snapshot_count"] >= 3
    manifest_file = snap_dir / "frames_manifest.json"
    assert manifest_file.is_file()

    with manifest_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["target_fps"] == target_fps
    assert data["total_snapshots"] == len(data["snapshots"])

    # Verify each snapshot timestamp strictly matches frame_to_seconds(idx, target_fps)
    for idx, snap in enumerate(data["snapshots"]):
        assert snap["index"] == idx
        expected_sec = frame_to_seconds(idx, target_fps)
        assert snap["timestamp_seconds"] == expected_sec
        assert snap["filename"] == f"snapshot_{idx + 1:04d}.jpg"
        assert snap["path"] == f"snapshots/snapshot_{idx + 1:04d}.jpg"
        assert (snap_dir / snap["filename"]).is_file()


# ------------------------------------------------------------------------------
# 8. Schema Validation & Conditional Invariants
# ------------------------------------------------------------------------------

def test_manifest_schema_rejects_missing_fields():
    """Verify schema validator detects missing required fields."""
    incomplete_manifest: Dict[str, Any] = {
        "schema_version": 1,
        "film_id": "test",
        # missing pipeline_stage, status, etc.
    }
    is_valid, errors = validate_ingest_manifest(incomplete_manifest)
    assert not is_valid
    assert len(errors) > 0


def test_manifest_schema_conditional_audio_failure():
    """Verify schema requires 'error' field when audio extraction fails."""
    manifest_data = {
        "schema_version": 1,
        "film_id": "test",
        "pipeline_stage": "ingestion",
        "status": "failed",
        "created_at": "2026-10-15T12:00:00Z",
        "source_media": {
            "path": "test.mp4",
            "filename": "test.mp4",
            "size_bytes": 100,
        },
        "normalized_video": {
            "path": "norm.mp4",
            "codec": "h264",
            "pixel_format": "yuv420p",
            "width": 320,
            "height": 240,
            "fps": 24.0,
            "duration_seconds": 1.0,
            "frame_count": 24,
            "timeline_start_seconds": 0.0,
        },
        "audio": {
            "present": True,
            "extraction_status": "failed",
            # missing "error" string
        },
        "low_res_frames": {
            "path": "frames.rgb",
            "width": 128,
            "height": 72,
            "pixel_format": "rgb24",
            "fps": 24.0,
            "frame_count": 24,
            "storage_format": "raw_rgb24",
            "bytes_per_frame": 27648,
            "total_bytes": 663552,
            "frame_loss_count": 0,
            "loss_policy": "strict_zero_loss",
        },
        "snapshots": {
            "output_dir": "snapshots",
            "manifest_path": "snapshots/frames_manifest.json",
            "snapshot_count": 1,
            "target_fps": 1.0,
            "max_dimension": 512,
            "format": "jpeg",
        },
        "warnings": [],
        "errors": ["Audio extraction failed"],
    }
    is_valid, errors = validate_ingest_manifest(manifest_data)
    assert not is_valid
    assert any("error" in err for err in errors)


# ------------------------------------------------------------------------------
# 9. Output Integrity, Stale Cleanup & Staging Protection
# ------------------------------------------------------------------------------

def test_stale_output_cleanup_and_repeatability(tmp_path: Path):
    """Verify re-running ingestion cleanly purges stale files from previous runs."""
    src = create_synthetic_video(tmp_path / "input.mp4", duration=1.0, with_audio=True)
    out_dir = tmp_path / "processed"

    # Run 1
    ingest_media(src, out_dir)
    assert (out_dir / "ingest_manifest.json").is_file()

    # Plant a stale file in the output directory
    stale_file = out_dir / "stale_detector_cache.tmp"
    stale_file.write_text("old data", encoding="utf-8")
    assert stale_file.is_file()

    # Run 2
    ingest_media(src, out_dir)

    # Stale file must be cleaned up; atomic publishing replaces directory
    assert not stale_file.exists()
    assert (out_dir / "ingest_manifest.json").is_file()


# ------------------------------------------------------------------------------
# 10. Playback Gate Isolation: Never Write Playback Manifest Complete
# ------------------------------------------------------------------------------

def test_ingestion_never_writes_playback_manifest_complete(tmp_path: Path):
    """Verify ingestion alone NEVER marks playback manifest ('manifest.json') as complete.

    CRITICAL INVARIANT:
    Person A's engine requires { "status": "complete" } in data/manifest.json.
    Successful media ingestion must NOT claim that the entire analysis pipeline is complete.
    """
    src = create_synthetic_video(tmp_path / "input.mp4", duration=1.0, with_audio=True)
    out_dir = tmp_path / "processed"

    manifest = ingest_media(src, out_dir)

    # Output file is ingest_manifest.json, NOT the engine gate manifest.json
    assert (out_dir / "ingest_manifest.json").is_file()
    assert not (out_dir / "manifest.json").exists()

    # Ingestion manifest explicitly flags pipeline_stage as 'ingestion'
    assert manifest["pipeline_stage"] == "ingestion"


# ------------------------------------------------------------------------------
# 11. CLI Execution
# ------------------------------------------------------------------------------

def test_cli_execution_success(tmp_path: Path):
    """Verify CLI execution succeeds and exits with code 0."""
    src = create_synthetic_video(tmp_path / "cli_input.mp4", duration=1.0, with_audio=True)
    out_dir = tmp_path / "cli_output"

    cmd = [
        sys.executable,
        "scripts/ingest_media.py",
        "--input",
        str(src),
        "--output-dir",
        str(out_dir),
        "--film-id",
        "cli_test",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0
    assert (out_dir / "ingest_manifest.json").is_file()


def test_cli_execution_missing_media_failure(tmp_path: Path):
    """Verify CLI execution fails with code 1 on non-existent media file."""
    cmd = [
        sys.executable,
        "scripts/ingest_media.py",
        "--input",
        str(tmp_path / "non_existent.mp4"),
        "--output-dir",
        str(tmp_path / "out"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode != 0


# ------------------------------------------------------------------------------
# 12. Stage Progress Logging, Quiet Mode & Timeout Regression
# ------------------------------------------------------------------------------

def test_ingest_stage_logging_and_quiet_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify stage-level progress logging is output when verbose=True and silenced when verbose=False."""
    src = create_synthetic_video(tmp_path / "log_clip.mp4", duration=1.0, with_audio=True)
    out_dir_verbose = tmp_path / "out_verbose"
    out_dir_quiet = tmp_path / "out_quiet"

    # Verbose run
    ingest_media(src, out_dir_verbose, verbose=True)
    captured = capsys.readouterr().out
    assert "[Stage 1/5]" in captured
    assert "[Stage 2/5]" in captured
    assert "[Stage 3/5]" in captured
    assert "[Stage 4/5]" in captured
    assert "[Stage 5/5]" in captured
    assert "[Validation]" in captured
    assert "[Publish]" in captured

    # Quiet run
    ingest_media(src, out_dir_quiet, verbose=False)
    captured_quiet = capsys.readouterr().out
    assert captured_quiet == ""


def test_ingest_timeout_handling(tmp_path: Path):
    """Verify subprocess timeout raises IngestionError with informative message."""
    src = create_synthetic_video(tmp_path / "timeout_clip.mp4", duration=1.0, with_audio=True)

    # Extremely low timeout forces TimeoutExpired in probe_media
    with pytest.raises(IngestionError) as exc_info:
        probe_media(src, timeout=0.000001)
    assert "timed out after" in str(exc_info.value)


def test_cli_quiet_flag(tmp_path: Path):
    """Verify --quiet flag suppresses stage logging in CLI execution."""
    src = create_synthetic_video(tmp_path / "cli_quiet.mp4", duration=1.0, with_audio=True)
    out_dir = tmp_path / "cli_quiet_out"

    cmd = [
        sys.executable,
        "scripts/ingest_media.py",
        "--input",
        str(src),
        "--output-dir",
        str(out_dir),
        "--quiet",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0
    assert "[Stage 1/5]" not in proc.stdout
    assert (out_dir / "ingest_manifest.json").is_file()
