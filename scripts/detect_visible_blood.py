#!/usr/bin/env python3
"""BloodshotNet: YOLO-based Visible Blood Detector for Trigger-Aware Player.

Detects 'visible_blood' events using ultralytics YOLO with temporal smoothing:
1. Input Layer: Shared video frame generator yielding media-relative presentation
   timestamps synchronized with the shared timeline utility.
2. Inference Bridge: YOLO bounding box detection with confidence gating and
   spatial area filtering (rejects micro-artifacts < 0.5% screen area).
3. Temporal Smoothing: Hysteresis gate (>= 3 consecutive frames) to reject
   glitches, with 15-frame hang-time smoothing to bridge momentary occlusions.
4. Packaging & Contract Validation: Canonical 5-field schema output with
   downstream integration into package_cues.py and index.json.

Usage:
    python scripts/detect_visible_blood.py --video data/media/nero.mp4
    python scripts/detect_visible_blood.py --video data/media/nero.mp4 --output data/blood_cues.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Sequence, Set, Tuple, Union

import cv2
import numpy as np

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from scripts.timeline import frame_to_seconds
except ImportError:
    def frame_to_seconds(frame_index: int, fps: float) -> float:
        return float(frame_index) / float(fps)

DEFAULT_WEIGHTS = REPO_ROOT / "models" / "bloodshot_yolo_small.pt"
DEFAULT_CONF_THRESHOLD = 0.65
DEFAULT_MIN_BOX_AREA_RATIO = 0.005  # Reject detections smaller than 0.5% screen area
DEFAULT_CONSECUTIVE_FRAMES = 3
DEFAULT_HANG_TIME_FRAMES = 15
TRIGGER_ID = "visible_blood"
SOURCE_ID = "bloodshotnet_yolo_v1"

# Target class label aliases across various YOLO blood/gore datasets
TARGET_CLASSES = frozenset({
    "visible_blood",
    "visible blood",
    "blood",
    "blood_stain",
    "blood stain",
    "blood_splatter",
    "gore",
    "wound",
    "open wound",
})


def validate_record(cue: Dict[str, Any]) -> None:
    """Validate a raw detection cue against the shared contract.
    
    Mandatory contract fields:
    - start: float >= 0.0
    - end: float strictly > start
    - trigger: string matching canonical trigger registry
    - score: float in range [0.0, 1.0]
    - source: non-empty string identifier
    """
    required = ["start", "end", "trigger", "score", "source"]
    for field in required:
        if field not in cue:
            raise ValueError(f"Cue record missing mandatory field '{field}': {cue}")

    start = cue["start"]
    end = cue["end"]
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        raise TypeError(f"Timestamps must be numeric: start={start}, end={end}")

    if start < 0.0 or end <= start:
        raise ValueError(f"Invalid cue interval bounds: [{start}, {end})")

    score = cue["score"]
    if not isinstance(score, (int, float)) or not (0.0 <= score <= 1.0):
        raise ValueError(f"Invalid cue score: {score}")

    source = cue["source"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"Cue source must be a non-empty string: {source}")


def video_frame_generator(
    video_path: Union[str, Path],
) -> Generator[Tuple[float, float, np.ndarray], None, None]:
    """Input Layer: Shared video frame decoder yielding media-relative timestamps.

    Yields:
        Tuple of (pts_start_seconds, pts_end_seconds, frame_bgr_array)
    """
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    if fps <= 0.0 or not math.isfinite(fps):
        fps = 24.0
    frame_duration = 1.0 / fps

    frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            pos_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            if pos_msec is not None and pos_msec > 0.0:
                pts_start = pos_msec / 1000.0
            else:
                pts_start = frame_to_seconds(frame_idx, fps)

            pts_end = pts_start + frame_duration
            yield (pts_start, pts_end, frame)
            frame_idx += 1
    finally:
        cap.release()


def filter_frame_detections(
    model: Any,
    frame: np.ndarray,
    conf_threshold: float = DEFAULT_CONF_THRESHOLD,
    min_box_area_ratio: float = DEFAULT_MIN_BOX_AREA_RATIO,
    target_classes: Set[str] = TARGET_CLASSES,
    imgsz: int = 640,
) -> Tuple[bool, float, List[Dict[str, Any]]]:
    """Inference Bridge: Run YOLO model on frame and apply confidence and spatial area filtering.

    Returns:
        Tuple of (blood_detected: bool, max_confidence: float, list_of_box_dicts)
    """
    if model is None:
        return False, 0.0, []

    frame_h, frame_w = frame.shape[:2]
    total_screen_area = max(1.0, float(frame_h * frame_w))

    results = model(frame, verbose=False, conf=conf_threshold, imgsz=imgsz)
    blood_detected = False
    max_conf = 0.0
    filtered_boxes: List[Dict[str, Any]] = []

    for r in results:
        if not hasattr(r, "boxes") or r.boxes is None:
            continue
        for box in r.boxes:
            cls_id = int(box.cls[0])
            cls_name = model.names.get(cls_id, "").lower() if hasattr(model, "names") else ""
            if cls_name not in target_classes:
                continue

            conf = float(box.conf[0])
            if conf < conf_threshold:
                continue

            # Spatial Area Check: Reject micro-artifacts smaller than min_box_area_ratio
            if hasattr(box, "xyxy") and box.xyxy is not None:
                try:
                    coords = box.xyxy[0].tolist()
                    x1, y1, x2, y2 = coords[:4]
                    box_area = max(0.0, float((x2 - x1) * (y2 - y1)))
                    area_ratio = box_area / total_screen_area
                    if area_ratio < min_box_area_ratio:
                        continue
                except Exception:
                    pass

            blood_detected = True
            max_conf = max(max_conf, conf)
            filtered_boxes.append({
                "class": cls_name,
                "confidence": conf,
            })

    return blood_detected, max_conf, filtered_boxes


def detect_visible_blood(
    video_path: Union[str, Path],
    weights_path: Union[str, Path] = DEFAULT_WEIGHTS,
    conf_threshold: float = DEFAULT_CONF_THRESHOLD,
    min_box_area_ratio: float = DEFAULT_MIN_BOX_AREA_RATIO,
    min_consecutive_frames: int = DEFAULT_CONSECUTIVE_FRAMES,
    hang_time_frames: int = DEFAULT_HANG_TIME_FRAMES,
    imgsz: int = 640,
    frame_stride: int = 1,
) -> List[Dict[str, Any]]:
    """Execute BloodshotNet visible blood detection on a video file.

    Parameters:
        video_path: Path to the target video file.
        weights_path: Path to ultralytics YOLO model weights (.pt file).
        conf_threshold: Minimum bounding box confidence to consider a positive detection.
        min_box_area_ratio: Minimum screen area ratio (default 0.005) for positive box.
        min_consecutive_frames: Number of consecutive positive frames required to open an interval.
        hang_time_frames: Maximum number of unconfirmed frames to bridge before closing an interval.
        imgsz: Target YOLO inference resolution (e.g. 640 or 320 for CPU speedup).
        frame_stride: Process every N-th frame (default 1 = every frame; 2 = 2x CPU speedup).

    Returns:
        List of raw detection dictionaries conforming to the canonical contract.
    """
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")

    # Load YOLO model (with graceful fallback for headless CI runners)
    model = None
    weights = Path(weights_path)
    if weights.is_file():
        try:
            from ultralytics import YOLO
            model = YOLO(str(weights))
            if hasattr(model, "set_classes"):
                model.set_classes(["visible blood", "blood", "blood stain", "gore", "wound"])
        except ImportError:
            print(f"Warning: ultralytics is not installed. Running in mock CI mode.", file=sys.stderr)
    else:
        print(f"Warning: Weights not found at '{weights}'. Running in mock CI mode.", file=sys.stderr)

    trigger_cues: List[Dict[str, Any]] = []

    # Temporal Smoothing State Machine
    active_interval: Optional[Dict[str, Any]] = None
    consecutive_detects = 0
    frames_since_last_detect = 0
    candidate_start_time = 0.0
    candidate_scores: List[float] = []
    last_frame_duration = 1.0 / 24.0

    raw_frame_idx = 0
    for pts_start, pts_end, frame in video_frame_generator(path):
        raw_frame_idx += 1
        if frame_stride > 1 and (raw_frame_idx % frame_stride != 0):
            continue

        current_time = pts_start
        frame_duration = pts_end - pts_start
        last_frame_duration = frame_duration

        blood_detected, frame_max_conf, _ = filter_frame_detections(
            model=model,
            frame=frame,
            conf_threshold=conf_threshold,
            min_box_area_ratio=min_box_area_ratio,
            target_classes=TARGET_CLASSES,
            imgsz=imgsz,
        )

        # State 1: Blood detected in this frame
        if blood_detected:
            consecutive_detects += 1
            frames_since_last_detect = 0

            if active_interval is None:
                if consecutive_detects == 1:
                    candidate_start_time = current_time
                    candidate_scores = [frame_max_conf]
                else:
                    candidate_scores.append(frame_max_conf)

                # Open interval after consecutive positive confirmations
                if consecutive_detects >= min_consecutive_frames:
                    active_interval = {
                        "start": round(candidate_start_time, 3),
                        "end": round(max(pts_end, candidate_start_time + frame_duration), 3),
                        "scores": list(candidate_scores),
                    }
            else:
                active_interval["end"] = round(max(pts_end, active_interval["start"] + frame_duration), 3)
                active_interval["scores"].append(frame_max_conf)

        # State 2: No blood detected in this frame
        else:
            consecutive_detects = 0
            candidate_scores.clear()

            if active_interval is not None:
                frames_since_last_detect += 1
                # Close interval after hang-time threshold exceeded
                if frames_since_last_detect > hang_time_frames:
                    mean_score = float(np.mean(active_interval["scores"]))
                    cue = {
                        "start": active_interval["start"],
                        "end": max(active_interval["end"], round(active_interval["start"] + frame_duration, 3)),
                        "trigger": TRIGGER_ID,
                        "type": TRIGGER_ID,
                        "score": round(float(np.clip(mean_score, 0.0, 1.0)), 4),
                        "source": SOURCE_ID,
                        "status": "experimental",
                    }
                    validate_record(cue)
                    trigger_cues.append(cue)
                    active_interval = None
                    frames_since_last_detect = 0

    # Final interval clamp if video ends with an open interval
    if active_interval is not None:
        mean_score = float(np.mean(active_interval["scores"]))
        cue = {
            "start": active_interval["start"],
            "end": max(active_interval["end"], round(active_interval["start"] + last_frame_duration, 3)),
            "trigger": TRIGGER_ID,
            "type": TRIGGER_ID,
            "score": round(float(np.clip(mean_score, 0.0, 1.0)), 4),
            "source": SOURCE_ID,
            "status": "experimental",
        }
        validate_record(cue)
        trigger_cues.append(cue)

    return trigger_cues


def main() -> None:
    parser = argparse.ArgumentParser(description="BloodshotNet YOLO visible blood detector.")
    parser.add_argument("--video", required=True, help="Path to input video file.")
    parser.add_argument("--weights", default=str(DEFAULT_WEIGHTS), help="Path to YOLO weights (.pt).")
    parser.add_argument("--conf", type=float, default=DEFAULT_CONF_THRESHOLD, help="Confidence threshold.")
    parser.add_argument("--min-area", type=float, default=DEFAULT_MIN_BOX_AREA_RATIO, help="Min box area ratio.")
    parser.add_argument("--min-frames", type=int, default=DEFAULT_CONSECUTIVE_FRAMES, help="Consecutive frames required.")
    parser.add_argument("--hang-time", type=int, default=DEFAULT_HANG_TIME_FRAMES, help="Hang time frames smoothing.")
    parser.add_argument("--imgsz", type=int, default=640, help="Inference resolution (640 default, 320 for fast CPU).")
    parser.add_argument("--stride", type=int, default=1, help="Frame stride (process every N-th frame).")
    parser.add_argument("--output", default=None, help="Optional output JSON path.")
    args = parser.parse_args()

    cues = detect_visible_blood(
        video_path=args.video,
        weights_path=args.weights,
        conf_threshold=args.conf,
        min_box_area_ratio=args.min_area,
        min_consecutive_frames=args.min_frames,
        hang_time_frames=args.hang_time,
        imgsz=args.imgsz,
        frame_stride=args.stride,
    )

    if args.output:
        out_p = Path(args.output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with out_p.open("w", encoding="utf-8") as f:
            json.dump(cues, f, indent=2)
        print(f"Emitted {len(cues)} visible_blood cues to {out_p}")
    else:
        print(json.dumps(cues, indent=2))



if __name__ == "__main__":
    main()
