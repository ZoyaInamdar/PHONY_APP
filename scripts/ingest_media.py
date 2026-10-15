#!/usr/bin/env python3
"""Media ingestion pipeline for Trigger-Aware Player (Person B).

Normalizes incoming media containers into standardized pipeline assets:
1. Video normalization: H.264 (yuv420p) at 24 fps, maximum 1920x1080 resolution,
   preserving aspect ratio, with timeline normalized to start at 0.0.
2. Audio extraction: Mono signed 16-bit PCM WAV at 44,100 Hz (pcm_s16le).
   If source contains no audio, records this explicitly without generating fake audio.
3. Low-resolution frame extraction: RGB24 raw frames at 128x72 pixels at 24 fps.
4. Snapshot sampling: Preview snapshots at <= 1.0 fps, maximum dimension 512 pixels,
   with snapshot manifest (frames_manifest.json).
5. Rich Ingestion Manifest: Creates data/ingest_manifest.json adhering to
   schema/manifest.schema.json (JSON Schema Draft 2020-12).
6. Atomic output: Emits assets into staging directory, validating before publishing.
   Never marks playback manifest (manifest.json) as 'complete' (ingestion != analysis).

Usage:
    python scripts/ingest_media.py --input "data/media/example.mp4" --output-dir "data/processed/example"
    python scripts/ingest_media.py --input "data/media/example.mp4" --output-dir "data/processed/example" --film-id example
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import jsonschema
from jsonschema import Draft202012Validator

from scripts.timeline import frame_to_seconds

DEFAULT_SCHEMA_PATH = REPO_ROOT / "schema" / "manifest.schema.json"
DEFAULT_TARGET_FPS = 24.0
DEFAULT_MAX_WIDTH = 1920
DEFAULT_MAX_HEIGHT = 1080
DEFAULT_LOW_RES_WIDTH = 128
DEFAULT_LOW_RES_HEIGHT = 72
DEFAULT_SNAPSHOT_FPS = 1.0
DEFAULT_SNAPSHOT_MAX_DIM = 512


class IngestionError(RuntimeError):
    """Base exception for media ingestion failures."""
    pass


class MissingDependencyError(IngestionError):
    """Raised when ffmpeg or ffprobe executable is not available."""
    pass


class InvalidMediaError(IngestionError):
    """Raised when the input file is missing, corrupt, or unreadable."""
    pass


class SchemaValidationError(IngestionError):
    """Raised when the generated ingestion manifest fails schema validation."""
    pass


def check_tool_availability() -> None:
    """Verify that ffmpeg and ffprobe are installed and accessible on PATH."""
    if not shutil.which("ffprobe"):
        raise MissingDependencyError(
            "ffprobe executable not found in PATH. Please install FFmpeg/FFprobe."
        )
    if not shutil.which("ffmpeg"):
        raise MissingDependencyError(
            "ffmpeg executable not found in PATH. Please install FFmpeg/FFprobe."
        )


def probe_media(media_path: Path | str) -> Dict[str, Any]:
    """Inspect source media container and stream properties using ffprobe.

    Returns structured dictionary with container format and detected streams.
    Raises FileNotFoundError if media file is missing.
    Raises InvalidMediaError if ffprobe execution fails or output is malformed.
    """
    check_tool_availability()
    path = Path(media_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Source media file not found: {path}")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size,format_name,bit_rate:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt,nb_frames,duration,sample_rate,channels,sample_fmt",
        "-of",
        "json",
        str(path),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise InvalidMediaError(
            f"ffprobe failed to read media file '{path}': {exc.stderr.strip()}"
        ) from exc

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise InvalidMediaError(
            f"Failed to parse ffprobe JSON output for '{path}': {exc}"
        ) from exc

    format_info = data.get("format", {})
    streams = data.get("streams", [])

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        raise InvalidMediaError(
            f"Media file '{path}' contains no valid video stream."
        )

    duration_val = format_info.get("duration")
    duration = float(duration_val) if duration_val is not None else None

    return {
        "path": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "container_duration_seconds": duration,
        "format_name": format_info.get("format_name"),
        "video_streams": video_streams,
        "audio_streams": audio_streams,
        "raw_probe": data,
    }


def normalize_video(
    input_path: Path,
    output_path: Path,
    max_width: int = DEFAULT_MAX_WIDTH,
    max_height: int = DEFAULT_MAX_HEIGHT,
    target_fps: float = DEFAULT_TARGET_FPS,
    has_audio: bool = False,
) -> Dict[str, Any]:
    """Normalize video stream to H.264 / yuv420p at constant frame rate.

    - Constrains resolution to max_width x max_height while preserving aspect ratio.
    - Forces even dimensions for yuv420p encoder compatibility.
    - Resets timestamps so playback starts at 0.0.
    """
    check_tool_availability()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Filter chain:
    # 1. Scale down within max bounds preserving aspect ratio
    # 2. Ensure both width and height are divisible by 2 (trunc(iw/2)*2)
    # 3. Enforce constant output frame rate (fps={target_fps})
    # 4. Reset presentation timestamps to start at 0 (setpts=PTS-STARTPTS)
    vf = (
        f"scale=min({max_width}\\,iw):min({max_height}\\,ih):force_original_aspect_ratio=decrease,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        f"fps={target_fps},"
        f"setpts=PTS-STARTPTS"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "0",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "fast",
        "-crf",
        "23",
    ]

    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2"])
    else:
        cmd.append("-an")

    cmd.append(str(output_path))

    try:
        # Keep FFmpeg progress/errors visible in the terminal instead of
        # capturing stderr until the full encode finishes.
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=None,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise IngestionError(
            f"Video normalization failed for '{input_path}' "
            f"(FFmpeg exit code {exc.returncode})."
        ) from exc

    # Probe normalized video output to capture exact verified properties
    norm_probe = probe_media(output_path)
    video_stream = norm_probe["video_streams"][0]

    # Measure exact frame count via ffprobe packet count
    count_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_packets",
        "-show_entries",
        "stream=nb_read_packets",
        "-of",
        "csv=p=0",
        str(output_path),
    ]
    try:
        # Use the ffprobe command defined above, not the FFmpeg normalization
        # command. ffprobe prints the packet count to stdout.
        count_proc = subprocess.run(
            count_cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        frame_count = int(count_proc.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as exc:
        error_detail = (
            getattr(exc, "stderr", None)
            or getattr(exc, "stdout", None)
            or str(exc)
        )
        raise IngestionError(
            f"Failed to count normalized video frames: {error_detail.strip()}"
        ) from exc
    dur = norm_probe.get("container_duration_seconds")
    if dur is None or dur <= 0:
        dur = frame_count / target_fps

    return {
        "path": output_path.name,
        "codec": "h264",
        "pixel_format": "yuv420p",
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "fps": target_fps,
        "duration_seconds": float(dur),
        "frame_count": frame_count,
        "timeline_start_seconds": 0.0,
    }


def extract_audio(
    input_path: Path,
    output_path: Path,
) -> Dict[str, Any]:
    """Extract mono PCM WAV audio at 44,100 Hz (pcm_s16le)."""
    check_tool_availability()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise IngestionError(
            f"Audio extraction failed for '{input_path}': {exc.stderr.strip()}"
        ) from exc

    # Probe extracted audio properties
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_name,sample_rate,channels,duration",
        "-of",
        "json",
        str(output_path),
    ]
    try:
        probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        probe_data = json.loads(probe_proc.stdout)
        astream = probe_data["streams"][0]
        duration_str = astream.get("duration")
        duration = float(duration_str) if duration_str is not None else None
    except Exception as exc:
        raise IngestionError(
            f"Failed to probe extracted audio WAV '{output_path}': {exc}"
        ) from exc

    return {
        "present": True,
        "extraction_status": "extracted",
        "path": output_path.name,
        "codec": "pcm_s16le",
        "sample_rate": 44100,
        "channels": 1,
        "duration_seconds": duration,
        "error": None,
    }


def extract_low_res_frames(
    normalized_video_path: Path,
    output_path: Path,
    width: int = DEFAULT_LOW_RES_WIDTH,
    height: int = DEFAULT_LOW_RES_HEIGHT,
    fps: float = DEFAULT_TARGET_FPS,
    expected_frame_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Extract raw RGB24 video frames at 128x72 pixels.

    Writes a single streaming binary file of raw RGB24 frames:
    Each frame is exactly width * height * 3 bytes (27,648 bytes for 128x72).
    This representation is memory-efficient and directly memmappable via numpy:
        np.memmap(output_path, dtype=np.uint8, mode='r', shape=(frame_count, height, width, 3))
    """
    check_tool_availability()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(normalized_video_path),
        "-vf",
        f"scale={width}:{height}",
        "-c:v",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise IngestionError(
            f"Low-resolution frame extraction failed: {exc.stderr.strip()}"
        ) from exc

    file_size = output_path.stat().st_size
    bytes_per_frame = width * height * 3
    frame_count = file_size // bytes_per_frame
    remainder = file_size % bytes_per_frame

    frame_loss_count = 0
    if remainder != 0:
        frame_loss_count += 1

    if expected_frame_count is not None and frame_count < expected_frame_count:
        frame_loss_count += (expected_frame_count - frame_count)

    return {
        "path": output_path.name,
        "width": width,
        "height": height,
        "pixel_format": "rgb24",
        "fps": fps,
        "frame_count": frame_count,
        "storage_format": "raw_rgb24",
        "bytes_per_frame": bytes_per_frame,
        "total_bytes": file_size,
        "frame_loss_count": frame_loss_count,
        "loss_policy": "strict_zero_loss",
    }


def extract_snapshots(
    normalized_video_path: Path,
    snapshots_dir: Path,
    manifest_filename: str = "frames_manifest.json",
    target_fps: float = DEFAULT_SNAPSHOT_FPS,
    max_dimension: int = DEFAULT_SNAPSHOT_MAX_DIM,
) -> Dict[str, Any]:
    """Extract preview snapshots at <= 1.0 fps with maximum dimension <= 512px.

    Writes snapshot images (snapshot_0000.jpg, ...) and frames_manifest.json.
    Handles short clips (< 1.0s) gracefully via round=up to guarantee at least 1 preview.
    """
    check_tool_availability()
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    # Use round=up to ensure short clips (< 1s) yield at least 1 snapshot at t=0.0
    vf = (
        f"fps=fps={target_fps}:round=up,"
        f"scale=min({max_dimension}\\,iw):min({max_dimension}\\,ih):force_original_aspect_ratio=decrease"
    )

    out_pattern = snapshots_dir / "snapshot_%04d.jpg"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(normalized_video_path),
        "-vf",
        vf,
        "-q:v",
        "2",
        str(out_pattern),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise IngestionError(
            f"Snapshot extraction failed: {exc.stderr.strip()}"
        ) from exc

    # Gather generated snapshot files in deterministic numerical order
    snapshot_files = sorted(
        snapshots_dir.glob("snapshot_*.jpg"),
        key=lambda p: p.name,
    )

    snapshot_records: List[Dict[str, Any]] = []
    for idx, snap_file in enumerate(snapshot_files):
        timestamp = frame_to_seconds(idx, target_fps)
        record = {
            "index": idx,
            "timestamp_seconds": timestamp,
            "filename": snap_file.name,
            "path": f"snapshots/{snap_file.name}",
        }
        snapshot_records.append(record)

    manifest_data = {
        "schema_version": 1,
        "target_fps": target_fps,
        "max_dimension": max_dimension,
        "total_snapshots": len(snapshot_records),
        "snapshots": snapshot_records,
    }

    manifest_path = snapshots_dir / manifest_filename
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    return {
        "output_dir": snapshots_dir.name,
        "manifest_path": f"{snapshots_dir.name}/{manifest_filename}",
        "snapshot_count": len(snapshot_records),
        "target_fps": target_fps,
        "max_dimension": max_dimension,
        "format": "jpeg",
    }


def validate_ingest_manifest(
    manifest_data: Dict[str, Any],
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
) -> Tuple[bool, List[str]]:
    """Validate ingestion manifest dictionary against JSON Schema Draft 2020-12."""
    schema_file = Path(schema_path)
    if not schema_file.is_file():
        raise FileNotFoundError(f"Manifest schema file not found at: {schema_file.resolve()}")

    with schema_file.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest_data), key=lambda e: e.path)

    error_messages: List[str] = []
    for err in errors:
        loc = " -> ".join(str(p) for p in err.path) if err.path else "root"
        error_messages.append(f"[{loc}] {err.message}")

    return (len(error_messages) == 0, error_messages)


def ingest_media(
    input_path: Path | str,
    output_dir: Path | str,
    film_id: Optional[str] = None,
    target_fps: float = DEFAULT_TARGET_FPS,
    max_width: int = DEFAULT_MAX_WIDTH,
    max_height: int = DEFAULT_MAX_HEIGHT,
    low_res_width: int = DEFAULT_LOW_RES_WIDTH,
    low_res_height: int = DEFAULT_LOW_RES_HEIGHT,
    snapshot_fps: float = DEFAULT_SNAPSHOT_FPS,
    snapshot_max_dim: int = DEFAULT_SNAPSHOT_MAX_DIM,
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
    skip_validation: bool = False,
) -> Dict[str, Any]:
    """Execute media ingestion pipeline with atomic staging and schema validation.

    CRITICAL INVARIANT:
    This function produces 'ingest_manifest.json'. It NEVER creates or marks
    the player playback manifest ('manifest.json') as 'complete'.
    Ingestion prepares media assets; analysis completion is determined
    solely by downstream detector and packaging orchestration.
    """
    check_tool_availability()
    src = Path(input_path).resolve()
    dst = Path(output_dir).resolve()

    if film_id is None:
        film_id = src.stem

    # 1. Inspect source media
    probe_info = probe_media(src)
    has_audio = len(probe_info["audio_streams"]) > 0

    warnings: List[str] = []
    errors: List[str] = []

    # 2. Prepare staging directory on the same filesystem/parent
    dst.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = dst.parent / f".staging_{dst.name}_{uuid.uuid4().hex[:8]}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        # A. Normalize video
        norm_video_path = staging_dir / "normalized.mp4"
        norm_video_info = normalize_video(
            input_path=src,
            output_path=norm_video_path,
            max_width=max_width,
            max_height=max_height,
            target_fps=target_fps,
            has_audio=has_audio,
        )

        # B. Extract audio if present
        if has_audio:
            audio_path = staging_dir / "audio_mono_44100.wav"
            try:
                audio_info = extract_audio(src, audio_path)
            except Exception as exc:
                audio_info = {
                    "present": True,
                    "extraction_status": "failed",
                    "path": None,
                    "codec": None,
                    "sample_rate": None,
                    "channels": None,
                    "duration_seconds": None,
                    "error": str(exc),
                }
                errors.append(f"Audio extraction failed on existing stream: {exc}")
        else:
            audio_info = {
                "present": False,
                "extraction_status": "skipped_no_stream",
                "path": None,
                "codec": None,
                "sample_rate": None,
                "channels": None,
                "duration_seconds": None,
                "error": None,
            }

        # C. Extract low-resolution frames
        low_res_path = staging_dir / "low_res_frames_128x72.rgb"
        low_res_info = extract_low_res_frames(
            normalized_video_path=norm_video_path,
            output_path=low_res_path,
            width=low_res_width,
            height=low_res_height,
            fps=target_fps,
            expected_frame_count=norm_video_info["frame_count"],
        )

        if low_res_info["frame_loss_count"] > 0:
            warnings.append(
                f"Low-res frame extraction detected {low_res_info['frame_loss_count']} potential frame discrepancy."
            )

        # D. Extract preview snapshots
        snapshots_dir = staging_dir / "snapshots"
        snapshots_info = extract_snapshots(
            normalized_video_path=norm_video_path,
            snapshots_dir=snapshots_dir,
            manifest_filename="frames_manifest.json",
            target_fps=snapshot_fps,
            max_dimension=snapshot_max_dim,
        )

        # Also write frames_manifest.json to staging root for config.yaml compatibility
        shutil.copy2(
            snapshots_dir / "frames_manifest.json",
            staging_dir / "frames_manifest.json",
        )

        # E. Determine status
        if errors:
            status = "failed"
        elif warnings:
            status = "partial"
        else:
            status = "complete"

        # F. Assemble rich ingestion manifest
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        manifest_data = {
            "schema_version": 1,
            "film_id": film_id,
            "pipeline_stage": "ingestion",
            "status": status,
            "created_at": created_at,
            "source_media": {
                "path": str(src),
                "filename": src.name,
                "size_bytes": probe_info["size_bytes"],
                "container_duration_seconds": probe_info["container_duration_seconds"],
                "format_name": probe_info["format_name"],
                "video_stream_count": len(probe_info["video_streams"]),
                "audio_stream_count": len(probe_info["audio_streams"]),
            },
            "normalized_video": norm_video_info,
            "audio": audio_info,
            "low_res_frames": low_res_info,
            "snapshots": snapshots_info,
            "warnings": warnings,
            "errors": errors,
        }

        # G. Validate manifest against JSON Schema
        if not skip_validation:
            is_valid, validation_errors = validate_ingest_manifest(
                manifest_data, schema_path=schema_path
            )
            if not is_valid:
                raise SchemaValidationError(
                    f"Generated ingestion manifest failed schema validation: {validation_errors}"
                )

        # Write manifest file inside staging directory
        manifest_output_path = staging_dir / "ingest_manifest.json"
        with manifest_output_path.open("w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)

        # H. Atomic publish to final output directory
        if dst.exists():
            backup_dir = dst.parent / f".backup_{dst.name}_{uuid.uuid4().hex[:8]}"
            dst.rename(backup_dir)
            try:
                staging_dir.rename(dst)
                shutil.rmtree(backup_dir, ignore_errors=True)
            except Exception:
                if backup_dir.exists() and not dst.exists():
                    backup_dir.rename(dst)
                raise
        else:
            staging_dir.rename(dst)

        return manifest_data

    except Exception:
        # On failure, always clean up staging directory so no partial files persist
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Media Ingestion Pipeline CLI for Trigger-Aware Player (Person B)."
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        type=Path,
        help="Path to source media file to ingest.",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        required=True,
        type=Path,
        help="Destination directory for processed media assets.",
    )
    parser.add_argument(
        "--film-id",
        type=str,
        default=None,
        help="Identifier for the media asset (defaults to input file stem).",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_TARGET_FPS,
        help=f"Target normalized video frame rate (default: {DEFAULT_TARGET_FPS}).",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=DEFAULT_MAX_WIDTH,
        help=f"Maximum normalized video width (default: {DEFAULT_MAX_WIDTH}).",
    )
    parser.add_argument(
        "--max-height",
        type=int,
        default=DEFAULT_MAX_HEIGHT,
        help=f"Maximum normalized video height (default: {DEFAULT_MAX_HEIGHT}).",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help="Path to manifest JSON schema for validation.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip JSON schema validation of ingest_manifest.json.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        print(f"Starting media ingestion for: {args.input}")
        manifest = ingest_media(
            input_path=args.input,
            output_dir=args.output_dir,
            film_id=args.film_id,
            target_fps=args.fps,
            max_width=args.max_width,
            max_height=args.max_height,
            schema_path=args.schema,
            skip_validation=args.skip_validation,
        )
        print(f"Ingestion successful! Assets published to: {args.output_dir}")
        print(f"Ingestion Manifest: {Path(args.output_dir) / 'ingest_manifest.json'}")
        print(f"Status: {manifest['status']}")
        return 0
    except IngestionError as exc:
        print(f"ERROR: Ingestion failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
