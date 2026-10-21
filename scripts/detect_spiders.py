#!/usr/bin/env python3
"""Open-Vocabulary Spider Detection Pipeline for Trigger-Aware Player.

Scans video content (such as Sintel) using Ultralytics YOLO-World
(yolov8s-world.pt) and OpenCV for the open-vocabulary class "spider".

Key characteristics & optimizations:
1. Strict 1 frame-per-second (FPS) sampling to optimize for CPU execution.
2. Open-vocabulary prompt assignment via model.set_classes(["spider"]).
3. Groups consecutive detected seconds into [start, end) half-open intervals.
4. Outputs a JSON manifest adhering to schema_version: 1.
5. Auto-delegates to Python 3.13 if executed under Python >= 3.14.

Usage:
    python scripts/detect_spiders.py
    python scripts/detect_spiders.py --input data/media/sintel.mkv --output data/processed/sintel/spider_cues.json
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "media" / "sintel.mkv"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "processed" / "sintel" / "spider_detections.json"
DEFAULT_MODEL = REPO_ROOT / "yolov8s-world.pt"
DEFAULT_CLASS = "spider"
DEFAULT_CONF = 0.15


def check_and_delegate_python() -> bool:
    """If running under Python >= 3.14 or without ultralytics, delegate to Python 3.13."""
    needs_delegation = False
    try:
        import ultralytics  # noqa: F401
        import cv2  # noqa: F401
    except ImportError:
        needs_delegation = True

    if sys.version_info >= (3, 14):
        needs_delegation = True

    if not needs_delegation:
        return False

    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python313" / "python.exe",
        REPO_ROOT / ".venv313" / "Scripts" / "python.exe",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
    ]

    target_py: Optional[str] = None
    for cand in candidates:
        if cand.is_file():
            target_py = str(cand)
            break

    if not target_py:
        target_py = "py -3.13"

    print(f"[INFO] Delegating spider detection from Python {sys.version.split()[0]} to: {target_py}")
    cmd = target_py.split() + [str(Path(__file__).resolve())] + sys.argv[1:]
    ret = subprocess.run(cmd)
    sys.exit(ret.returncode)


def group_consecutive_detections(
    detected_frames: List[Dict[str, Any]],
    fps_sample: float = 1.0,
    source: str = "spider_detector",
    trigger: str = "spider",
) -> List[Dict[str, Any]]:
    """Group consecutive detected seconds into [start, end) half-open intervals.

    Each detection at second `t` represents the 1-second interval [t, t + (1.0 / fps_sample)).
    Consecutive seconds are merged into a single continuous [start, end) interval.
    Enforces canonical trigger ID 'spider'.
    """
    if trigger != "spider":
        raise ValueError(f"Invalid trigger '{trigger}'. Only canonical trigger 'spider' is supported.")

    if not detected_frames:
        return []

    sorted_dets = sorted(detected_frames, key=lambda d: d["second"])

    groups: List[List[Dict[str, Any]]] = []
    current_group: List[Dict[str, Any]] = [sorted_dets[0]]
    step = (1.0 / fps_sample) if fps_sample > 0 else 1.0

    for det in sorted_dets[1:]:
        prev = current_group[-1]
        # Consecutive if the next detected second immediately follows previous
        if abs(det["second"] - (prev["second"] + step)) < (step * 0.5):
            current_group.append(det)
        else:
            groups.append(current_group)
            current_group = [det]

    if current_group:
        groups.append(current_group)

    detections: List[Dict[str, Any]] = []
    for grp in groups:
        start_sec = float(grp[0]["second"])
        end_sec = float(grp[-1]["second"] + step)
        max_score = max(float(d.get("score", 1.0)) for d in grp)

        detections.append({
            "start": round(start_sec, 3),
            "end": round(end_sec, 3),
            "trigger": trigger,
            "score": round(max_score, 4),
            "source": source,
        })

    return detections


def detect_spiders_in_video(
    video_path: Path,
    model_name: str = str(DEFAULT_MODEL),
    target_class: str = DEFAULT_CLASS,
    confidence_threshold: float = DEFAULT_CONF,
    sample_fps: float = 1.0,
    max_duration_seconds: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Scan a video at 1 frame per second using YOLO-World for spiders."""
    import cv2
    from ultralytics import YOLO

    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    print(f"[INFO] Opening video: {video_path}")
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video file: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_duration = total_frames / fps if fps > 0 else 0.0

    print(f"[INFO] Video loaded: {total_frames} frames, {fps:.2f} FPS, {video_duration:.2f}s duration.")
    print(f"[INFO] CPU Optimization: Strictly scanning at {sample_fps} frame/sec.")

    if target_class != "spider":
        raise ValueError(f"Target class '{target_class}' is invalid. Only canonical trigger 'spider' is supported.")

    # Initialize YOLO-World model
    print(f"[INFO] Loading YOLO-World model: {model_name}...")
    model = YOLO(model_name)

    print(f"[INFO] Setting open-vocabulary class to: ['{target_class}']")
    model.set_classes([target_class])

    detected_frames: List[Dict[str, Any]] = []
    current_sec = 0
    effective_duration = video_duration
    if max_duration_seconds is not None:
        effective_duration = min(video_duration, max_duration_seconds)

    total_seconds_to_scan = int(effective_duration)
    print(f"[INFO] Starting scan of {total_seconds_to_scan} frames on CPU...")

    start_perf = datetime.now()

    while current_sec < total_seconds_to_scan:
        target_frame_idx = int(round(current_sec * fps))
        if target_frame_idx >= total_frames:
            break

        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame_idx)
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        # Inference on CPU
        results = model.predict(
            source=frame,
            conf=confidence_threshold,
            device="cpu",
            verbose=False,
        )

        boxes = results[0].boxes if len(results) > 0 else None
        if boxes is not None and len(boxes) > 0:
            scores = [float(b.conf.item()) for b in boxes]
            max_score = max(scores)
            print(
                f"[TRIGGER] Spider detected at {current_sec:4d}s "
                f"({len(boxes)} boxes, max conf: {max_score:.3f})"
            )
            detected_frames.append({
                "second": current_sec,
                "score": max_score,
                "box_count": len(boxes),
            })

        current_sec += 1
        if current_sec % 30 == 0 or current_sec == total_seconds_to_scan:
            elapsed = (datetime.now() - start_perf).total_seconds()
            rate = current_sec / elapsed if elapsed > 0 else 0.0
            print(
                f"  -> Scanned {current_sec}/{total_seconds_to_scan}s "
                f"({rate:.1f} fps) | Detections: {len(detected_frames)}"
            )

    cap.release()

    meta = {
        "duration_seconds": round(video_duration, 3),
        "fps": round(fps, 3),
        "total_frames": total_frames,
        "scanned_seconds": current_sec,
        "interval_convention": "half_open",
    }
    return detected_frames, meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description="YOLO-World Spider Detector for Trigger-Aware Player"
    )
    parser.add_argument(
        "--input", "-i",
        default=str(DEFAULT_INPUT),
        help=f"Input video file path (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output", "-o",
        default=str(DEFAULT_OUTPUT),
        help=f"Output JSON manifest path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--model", "-m",
        default=str(DEFAULT_MODEL),
        help=f"Ultralytics YOLO-World model weight (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--class-name",
        default=DEFAULT_CLASS,
        help=f"Open-vocabulary target class (default: {DEFAULT_CLASS})",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=DEFAULT_CONF,
        help=f"Confidence threshold (default: {DEFAULT_CONF})",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=None,
        help="Optional max duration in seconds to scan (e.g. for testing)",
    )
    args = parser.parse_args()

    # Automatically delegate to Python 3.13 if invoked under Python >= 3.14
    check_and_delegate_python()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.is_file():
        print(f"[ERROR] Input video not found at: {input_path}", file=sys.stderr)
        return 1

    film_id = input_path.stem

    print("=" * 70)
    print("TRIGGER-AWARE PLAYER: SPIDER DETECTION PIPELINE")
    print(f"Input:       {input_path}")
    print(f"Output:      {output_path}")
    print(f"Model:       {args.model}")
    print(f"Class:       {args.class_name}")
    print(f"Threshold:   {args.conf}")
    print("=" * 70)

    raw_detections, meta = detect_spiders_in_video(
        video_path=input_path,
        model_name=args.model,
        target_class=args.class_name,
        confidence_threshold=args.conf,
        sample_fps=1.0,
        max_duration_seconds=args.max_duration,
    )

    detections = group_consecutive_detections(
        detected_frames=raw_detections,
        fps_sample=1.0,
        source="spider_detector",
        trigger="spider",
    )

    manifest = {
        "film_id": film_id,
        "synthetic": False,
        "timeline": {
            "duration_seconds": meta["duration_seconds"],
            "fps": meta["fps"],
        },
        "model": "yolov8s-world.pt",
        "target_class": "spider",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sampling_rate_fps": 1.0,
        "confidence_threshold": args.conf,
        "detections": detections,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("\n" + "=" * 70)
    print(f"[SUCCESS] Wrote {len(detections)} spider detection intervals to {output_path}")
    print("=" * 70)
    for d in detections:
        print(f"  - [{d['start']:.2f}s -> {d['end']:.2f}s) | Score: {d['score']:.4f} | Source: {d['source']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
