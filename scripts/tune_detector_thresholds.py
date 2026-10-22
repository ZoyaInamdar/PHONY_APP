#!/usr/bin/env python3
"""Detector Threshold Tuning Pipeline (Validation Split Only).

Performs bounded hyperparameter search on the validation (or train_dev) split,
strictly protecting the locked_test split and production defaults.

Key safety rules:
1. Prevents data leakage: refuses to tune on 'locked_test'.
2. Keeps synthetic and natural-media evaluation separated.
3. If natural validation media lacks verified ground truth (e.g., zero verified natural video events),
   records status INSUFFICIENT_VALIDATION_DATA and identifies required annotations.
4. Does NOT modify production config.yaml or production film indexes.
5. Saves candidate configurations and experiment comparisons in:
   data/evaluation/tuning/<detector>/
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml
from scripts.evaluate_detections import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_IOU_THRESHOLD,
    load_detector_config,
)
from scripts.evaluate_detector_independent import (
    DEFAULT_MANIFEST_PATH,
    compute_file_sha256,
    evaluate_detector_run,
    match_predictions_to_ground_truth,
)

DEFAULT_TUNING_OUT = REPO_ROOT / "data" / "evaluation" / "tuning"

# Bounded candidate search grids for eligible rule-based detectors
SEARCH_GRIDS: Dict[str, List[Dict[str, Any]]] = {
    "flashing": [
        {"contrast_threshold": 0.03, "min_flash_count": 3, "name": "candidate_sensitive"},
        {"contrast_threshold": 0.05, "min_flash_count": 3, "name": "candidate_balanced"},
        {"contrast_threshold": 0.08, "min_flash_count": 4, "name": "candidate_conservative"},
    ],
    "loud_spike": [
        {"spike_threshold_db": 10.0, "spike_min_silence_s": 0.1, "name": "candidate_sensitive"},
        {"spike_threshold_db": 14.0, "spike_min_silence_s": 0.15, "name": "candidate_balanced"},
        {"spike_threshold_db": 18.0, "spike_min_silence_s": 0.2, "name": "candidate_conservative"},
    ],
    "fast_motion": [
        {"diff_threshold": 25.0, "motion_ratio": 0.15, "name": "candidate_sensitive"},
        {"diff_threshold": 30.0, "motion_ratio": 0.20, "name": "candidate_balanced"},
        {"diff_threshold": 40.0, "motion_ratio": 0.30, "name": "candidate_conservative"},
    ],
}


def tune_detector(
    detector_name: str,
    split: str = "validation",
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    base_config_path: Path | str = DEFAULT_CONFIG_PATH,
    output_dir: Path | str = DEFAULT_TUNING_OUT,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> Dict[str, Any]:
    """Execute threshold tuning experiment on validation or train_dev split."""
    if split == "locked_test":
        raise ValueError(
            "CRITICAL SAFETY VIOLATION: Threshold tuning against 'locked_test' split is strictly prohibited "
            "to prevent evaluation data leakage. Use 'validation' or 'train_dev'."
        )

    out_base = Path(output_dir) / detector_name
    out_base.mkdir(parents=True, exist_ok=True)

    base_cfg = load_detector_config(base_config_path)

    # 1. Baseline Run
    print(f"[{detector_name}] Running baseline evaluation on '{split}' split...")
    baseline_run = evaluate_detector_run(
        detector_name=detector_name,
        manifest_path=manifest_path,
        split_filter=split,
        config_path=base_config_path,
        output_base_dir=out_base / "baseline",
        iou_threshold=iou_threshold,
    )

    grid = SEARCH_GRIDS.get(detector_name, [])
    if not grid:
        print(f"[{detector_name}] No hyperparameter search grid defined for this detector.")
        return {"detector": detector_name, "status": "NO_SEARCH_GRID", "baseline": baseline_run}

    candidate_results: List[Dict[str, Any]] = []

    for cand_params in grid:
        cand_name = cand_params["name"]
        print(f"[{detector_name}] Evaluating candidate: {cand_name} ({cand_params})...")

        cand_cfg = copy.deepcopy(base_cfg)
        # Update parameters in candidate config under appropriate trigger section
        trigger_section = cand_cfg.get(detector_name, {})
        for k, v in cand_params.items():
            if k != "name":
                trigger_section[k] = v
        cand_cfg[detector_name] = trigger_section

        # Save temporary candidate config file
        cand_cfg_file = out_base / f"config_{cand_name}.yaml"
        with cand_cfg_file.open("w", encoding="utf-8") as f:
            yaml.safe_dump(cand_cfg, f)

        # Run independent evaluation with candidate config
        cand_eval = evaluate_detector_run(
            detector_name=detector_name,
            manifest_path=manifest_path,
            split_filter=split,
            config_path=cand_cfg_file,
            output_base_dir=out_base / "candidates" / cand_name,
            iou_threshold=iou_threshold,
        )

        candidate_results.append({
            "candidate_name": cand_name,
            "parameters": {k: v for k, v in cand_params.items() if k != "name"},
            "summary": cand_eval.get("summary", {}),
        })

    # Prepare experiment report
    report = {
        "detector": detector_name,
        "split": split,
        "tuning_timestamp": datetime.now(timezone.utc).isoformat(),
        "baseline": baseline_run.get("summary", {}),
        "candidates": candidate_results,
        "natural_validation_status": baseline_run.get("summary", {}).get("natural", {}).get("status", "UNKNOWN"),
        "notes": (
            "Production defaults remain untouched. If natural-media ground truth is INSUFFICIENT_VALIDATION_DATA, "
            "no natural accuracy claims are made and production config.yaml is not altered."
        ),
    }

    report_file = out_base / "tuning_report.json"
    with report_file.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"[{detector_name}] Tuning report saved to {report_file}")
    return report


def main():
    parser = argparse.ArgumentParser(description="Tune detector thresholds on validation split.")
    parser.add_argument("--detector", required=True, help="Detector name (flashing, loud_spike, fast_motion)")
    parser.add_argument("--split", default="validation", help="Evaluation split ('validation' or 'train_dev')")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Path to manifest.json")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to base config.yaml")
    parser.add_argument("--output-dir", default=str(DEFAULT_TUNING_OUT), help="Output tuning directory")

    args = parser.parse_args()
    tune_detector(
        detector_name=args.detector,
        split=args.split,
        manifest_path=args.manifest,
        base_config_path=args.config,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
