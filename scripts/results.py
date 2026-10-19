#!/usr/bin/env python3
"""Unified Results Runner and Benchmark Summary Generator.

Person A — Trigger-Aware Player (Amazon Developer Hackathon 2026, Fire TV / Vega OS).

Runs the multi-detector evaluation harness, reads empirical benchmark data,
gathers exact environment and configuration parameters, and compiles both
human-readable results.md and machine-readable results.json.

Usage:
    python3 scripts/results.py
    python3 scripts/results.py --force
    python3 scripts/results.py --output-md results.md --output-json results.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Prefer repo venv python if current interpreter lacks required modules
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
VENV312_PYTHON = REPO_ROOT / ".venv312" / "bin" / "python"

# Auto re-exec with repo venv if current environment lacks PyYAML or jsonschema
try:
    import yaml  # noqa: F401
    import jsonschema  # noqa: F401
except ImportError:
    if VENV_PYTHON.is_file() and sys.executable != str(VENV_PYTHON):
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON)] + sys.argv)
    elif VENV312_PYTHON.is_file() and sys.executable != str(VENV312_PYTHON):
        os.execv(str(VENV312_PYTHON), [str(VENV312_PYTHON)] + sys.argv)

DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_MANIFEST_PATH = REPO_ROOT / "data" / "test_clips" / "manifest.json"
DEFAULT_LABELS_PATH = REPO_ROOT / "data" / "test_clips" / "labels.csv"
DEFAULT_NATURAL_LABELS_PATH = REPO_ROOT / "data" / "natural_validation" / "labels.csv"
DEFAULT_CLIPS_DIR = REPO_ROOT / "data" / "test_clips"
DEFAULT_EVAL_JSON = REPO_ROOT / "reports" / "detection_evaluation_report.json"
DEFAULT_EVAL_MD = REPO_ROOT / "reports" / "detection_evaluation_report.md"
DEFAULT_OUTPUT_MD = REPO_ROOT / "results.md"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "results.json"

FLASH_DISCLAIMER = (
    "detects rapid luminance flashing over screen regions, approximating the "
    "WCAG general-flash definition. Not a compliance check."
)

CANONICAL_TRIGGERS: List[str] = [
    "flashing",
    "loud_spike",
    "gunshot",
    "explosion",
    "scream",
    "profanity",
    "spider",
    "fast_motion",
    "jump_scare",
]

KEY_LIBRARIES: List[str] = [
    "numpy",
    "scipy",
    "librosa",
    "soundfile",
    "cv2",
    "pysrt",
    "pandas",
    "pytest",
    "yaml",
    "jsonschema",
]


def resolve_python_executable() -> str:
    """Select the best Python interpreter with installed scientific packages."""
    try:
        import yaml  # noqa: F401
        import jsonschema  # noqa: F401
        return sys.executable
    except ImportError:
        if VENV_PYTHON.is_file():
            return str(VENV_PYTHON)
        if VENV312_PYTHON.is_file():
            return str(VENV312_PYTHON)
        return sys.executable


def get_ffmpeg_version() -> str:
    """Retrieve FFmpeg version string from local system, or UNVERIFIED."""
    try:
        res = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            first_line = res.stdout.splitlines()[0]
            # e.g. "ffmpeg version 9.0.2 Copyright (c) ..."
            parts = first_line.split()
            if len(parts) >= 3 and parts[1] == "version":
                return parts[2]
            return first_line.strip()
    except Exception:
        pass
    return "UNVERIFIED (ffmpeg not in system PATH or invocation failed)"


def get_library_versions(libraries: Optional[List[str]] = None) -> Dict[str, str]:
    """Retrieve runtime versions for specified key packages."""
    libs = libraries or KEY_LIBRARIES
    versions: Dict[str, str] = {}

    try:
        from importlib import metadata
    except ImportError:
        metadata = None

    package_distribution_names = {
        "cv2": "opencv-python-headless",
        "yaml": "PyYAML",
    }

    for lib in libs:
        dist_name = package_distribution_names.get(lib, lib)
        ver = None
        if metadata:
            try:
                ver = metadata.version(dist_name)
            except Exception:
                pass
        if not ver:
            try:
                mod = importlib.import_module(lib)
                ver = getattr(mod, "__version__", None)
            except ImportError:
                versions[lib] = "not installed"
                continue
            except Exception as exc:
                versions[lib] = f"error: {exc}"
                continue
        versions[lib] = str(ver) if ver else "present (no __version__)"
    return versions


def get_yamnet_model_metadata() -> Dict[str, Any]:
    """Read YAMNet ONNX model metadata from disk if available."""
    metadata_path = REPO_ROOT / "models" / "yamnet" / "yamnet-onnx-float" / "metadata.json"
    if metadata_path.is_file():
        try:
            with metadata_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "model_id": data.get("model_id", "yamnet"),
                    "model_name": data.get("model_name", "YamNet"),
                    "runtime": data.get("runtime", "onnx"),
                    "precision": data.get("precision", "float"),
                    "onnx_runtime_version": data.get("tool_versions", {}).get("onnx_runtime", "1.30.0"),
                    "metadata_file": str(metadata_path.relative_to(REPO_ROOT)),
                }
        except Exception as exc:
            return {"error": f"Failed to parse metadata: {exc}"}
    return {
        "model_id": "yamnet",
        "runtime": "onnx",
        "precision": "float",
        "status": "UNVERIFIED (metadata.json not found)",
    }


def load_config_data(config_path: Path) -> Dict[str, Any]:
    """Load configuration YAML file."""
    try:
        import yaml
    except ImportError:
        # Fallback minimal parser or raise
        raise RuntimeError("PyYAML must be installed to load configuration")

    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def extract_trigger_config(config_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Extract per-trigger thresholds, status, and actions from config.yaml."""
    triggers = config_data.get("triggers", {})
    extracted: Dict[str, Dict[str, Any]] = {}

    for trig in CANONICAL_TRIGGERS:
        t_data = triggers.get(trig, {})
        extracted[trig] = {
            "display_name": t_data.get("display_name", trig),
            "status": t_data.get("status", "UNVERIFIED"),
            "enabled": t_data.get("enabled", False),
            "threshold": t_data.get("threshold"),
            "default_action": t_data.get("default_action", "warn"),
            "padding_before_seconds": t_data.get("padding_before_seconds"),
            "padding_after_seconds": t_data.get("padding_after_seconds"),
            "merge_gap_seconds": t_data.get("merge_gap_seconds"),
            "source": t_data.get("source", "unknown"),
        }
    return extracted


def calculate_false_alarms_per_minute(
    false_positives: int,
    duration_seconds: float,
) -> Optional[float]:
    """Calculate false alarms per minute from FP count and total clip duration."""
    if duration_seconds <= 0.0:
        return None
    minutes = duration_seconds / 60.0
    return round(float(false_positives) / minutes, 3)


def determine_confidence_label(
    trigger: str,
    status_in_config: str,
    is_available: bool,
    f1_score: Optional[float],
) -> str:
    """Determine confidence tier based on config.yaml and availability.

    Note: docs/evaluation-rules.md is absent in this codebase; hence
    classification is explicitly derived from config.yaml status and verified execution.
    """
    if not is_available:
        return "dropped (unimplemented)"
    if status_in_config == "experimental":
        return "experimental (config.yaml)"
    if f1_score is not None and f1_score >= 0.70:
        return "high confidence (F1 >= 0.70)"
    if f1_score is not None and f1_score > 0.0:
        return "experimental (F1 < 0.70)"
    return "experimental (calibrating)"


def run_evaluation_stage(
    python_exe: str,
    config_path: Path,
    manifest_path: Path,
    labels_path: Path,
    clips_dir: Path,
    output_json: Path,
    output_md: Path,
    force: bool = False,
) -> Tuple[bool, float, str]:
    """Execute scripts/evaluate_detections.py if output is stale or force is requested.

    Returns:
        (success, elapsed_seconds, message)
    """
    eval_script = REPO_ROOT / "scripts" / "evaluate_detections.py"
    if not eval_script.is_file():
        return False, 0.0, f"Evaluation script missing: {eval_script}"
    if not config_path.is_file():
        return False, 0.0, f"Config file missing: {config_path}"
    if not labels_path.is_file():
        return False, 0.0, f"Labels file missing: {labels_path}"

    # Check whether inputs are newer than existing output
    if not force and output_json.is_file():
        out_mtime = output_json.stat().st_mtime
        inputs = [eval_script, config_path, manifest_path, labels_path]
        detector_scripts = [
            REPO_ROOT / "scripts" / "detect_flashing.py",
            REPO_ROOT / "scripts" / "classify_audio_events.py",
            REPO_ROOT / "scripts" / "detect_loudness.py",
            REPO_ROOT / "scripts" / "detect_jump_scare.py",
            REPO_ROOT / "scripts" / "detect_fast_motion.py",
        ]
        inputs.extend([s for s in detector_scripts if s.is_file()])

        is_stale = False
        for inp in inputs:
            if inp.is_file() and inp.stat().st_mtime > out_mtime:
                is_stale = True
                break

        if not is_stale:
            return True, 0.0, "Skipped (report is up to date; use --force to rerun)"

    cmd = [
        python_exe,
        str(eval_script),
        "--manifest", str(manifest_path),
        "--labels", str(labels_path),
        "--clips-dir", str(clips_dir),
        "--config", str(config_path),
        "--output-json", str(output_json),
        "--output-md", str(output_md),
    ]

    t0 = time.perf_counter()
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - t0

    if res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip() or f"exit code {res.returncode}"
        return False, elapsed, f"evaluate_detections.py failed: {err_msg}"

    return True, elapsed, "Executed successfully"


def build_results_data(
    eval_json_path: Path,
    config_path: Path,
    harness_runtime_s: float,
    harness_status_msg: str,
) -> Dict[str, Any]:
    """Assemble all verified empirical data, configuration, and environment facts."""
    if not eval_json_path.is_file():
        raise FileNotFoundError(f"Evaluation report JSON missing: {eval_json_path}")

    with eval_json_path.open("r", encoding="utf-8") as f:
        eval_data = json.load(f)

    config_data = load_config_data(config_path)
    trigger_configs = extract_trigger_config(config_data)
    yamnet_meta = get_yamnet_model_metadata()

    env_info = {
        "python_version": sys.version.split()[0],
        "ffmpeg_version": get_ffmpeg_version(),
        "libraries": get_library_versions(),
        "yamnet_model": yamnet_meta,
    }

    per_trigger_eval = eval_data.get("per_trigger", {})
    per_clip_list = eval_data.get("per_clip", [])

    # Calculate total duration per trigger across evaluated clips
    trigger_durations: Dict[str, float] = {}
    for clip_record in per_clip_list:
        trig = clip_record.get("trigger")
        dur = float(clip_record.get("metadata", {}).get("duration", 0.0))
        if trig:
            trigger_durations[trig] = trigger_durations.get(trig, 0.0) + dur

    per_trigger_results: Dict[str, Any] = {}

    for trig in CANONICAL_TRIGGERS:
        t_cfg = trigger_configs.get(trig, {})
        t_eval = per_trigger_eval.get(trig)

        if t_eval:
            counts = t_eval.get("counts", {})
            micro_m = t_eval.get("micro_metrics", {})
            temp_m = t_eval.get("temporal_metrics", {})

            tp = counts.get("true_positives", 0)
            fp = counts.get("false_positives", 0)
            fn = counts.get("false_negatives", 0)
            gt_events = counts.get("ground_truth_events", 0)

            dur_s = trigger_durations.get(trig, 0.0)
            fa_per_min = calculate_false_alarms_per_minute(fp, dur_s)

            prec = micro_m.get("precision")
            rec = micro_m.get("recall")
            f1 = micro_m.get("f1")

            conf_label = determine_confidence_label(
                trigger=trig,
                status_in_config=t_cfg.get("status", ""),
                is_available=True,
                f1_score=f1,
            )

            per_trigger_results[trig] = {
                "display_name": t_cfg.get("display_name", trig),
                "status": "evaluated",
                "confidence_label": conf_label,
                "threshold_in_config": t_cfg.get("threshold"),
                "true_segments": gt_events,
                "true_positives": tp,
                "false_positives": fp,
                "false_negatives": fn,
                "precision": prec,
                "recall": rec,
                "f1": f1,
                "mean_iou": temp_m.get("mean_iou"),
                "signed_start_error_s": temp_m.get("mean_onset_error"),
                "signed_end_error_s": temp_m.get("mean_offset_error"),
                "abs_start_error_s": temp_m.get("mean_abs_onset_error"),
                "abs_end_error_s": temp_m.get("mean_abs_offset_error"),
                "evaluable_duration_seconds": round(dur_s, 2),
                "false_alarms_per_minute": fa_per_min,
            }
        else:
            # Unimplemented or un-evaluated trigger
            conf_label = determine_confidence_label(
                trigger=trig,
                status_in_config=t_cfg.get("status", ""),
                is_available=False,
                f1_score=None,
            )
            per_trigger_results[trig] = {
                "display_name": t_cfg.get("display_name", trig),
                "status": "unimplemented",
                "confidence_label": conf_label,
                "threshold_in_config": t_cfg.get("threshold"),
                "true_segments": 0,
                "true_positives": 0,
                "false_positives": 0,
                "false_negatives": 0,
                "precision": None,
                "recall": None,
                "f1": None,
                "mean_iou": None,
                "signed_start_error_s": None,
                "signed_end_error_s": None,
                "abs_start_error_s": None,
                "abs_end_error_s": None,
                "evaluable_duration_seconds": 0.0,
                "false_alarms_per_minute": None,
            }

    summary_counts = eval_data.get("summary", {}).get("counts", {})
    summary_micro = eval_data.get("summary", {}).get("dataset_micro_metrics", {})
    summary_macro = eval_data.get("summary", {}).get("dataset_macro_metrics", {})

    return {
        "benchmark_type": "synthetic benchmark",
        "generated_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "run_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "runtimes": {
            "harness_evaluation_seconds": round(harness_runtime_s, 3),
            "harness_status": harness_status_msg,
        },
        "environment": env_info,
        "flash_detector_specification": FLASH_DISCLAIMER,
        "dataset_summary": {
            "total_clips_evaluated": eval_data.get("summary", {}).get("total_clips_evaluated", 0),
            "counts": summary_counts,
            "micro_metrics": summary_micro,
            "macro_metrics": summary_macro,
        },
        "per_trigger": per_trigger_results,
        "real_film_validation": {
            "status": "no_hand_labelled_full_film_clips",
            "description": (
                "Ground truth in data/test_clips/labels.csv consists exclusively of synthetic "
                "generated clips (labeller: synthetic_generator). data/natural_validation/labels.csv "
                "contains 2 external natural audio clips (explosion and scream from Freesound/OpenGameArt), "
                "but no hand-labelled ground truth intervals currently exist for full demo films (Sintel or Tears of Steel)."
            ),
        },
        "notes": [
            "Detector scores are detector-specific metrics and are not calibrated probabilities.",
            "Flash detector approximates WCAG general-flash rule; it is not a medical safeguard or compliance check.",
            "All clips in data/test_clips/ are synthetic generator outputs ('synthetic benchmark').",
            "docs/evaluation-rules.md is absent from the repository; confidence tiers are determined from config.yaml status and empirical F1 scores.",
        ],
    }


def format_markdown_results(data: Dict[str, Any]) -> str:
    """Format the results dictionary into human-readable results.md."""
    lines: List[str] = []
    lines.append("# Trigger-Aware Player — Detector Benchmark & Evaluation Results")
    lines.append("")
    lines.append(f"**Run Date:** {data['run_date']} ({data['generated_timestamp_utc']})  ")
    lines.append(f"**Benchmark Type:** {data['benchmark_type']}  ")
    lines.append(f"**Harness Runtime:** {data['runtimes']['harness_evaluation_seconds']}s ({data['runtimes']['harness_status']})  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. Specification & Compliance Disclaimers")
    lines.append("")
    lines.append(f"> **Flash Detector Specification:** {data['flash_detector_specification']}")
    lines.append("> ")
    lines.append("> **Score Semantics:** Detector scores represent relative confidence or signal energy; detector scores are not probabilities.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. Per-Trigger Detection Performance (synthetic benchmark)")
    lines.append("")
    lines.append("All metrics below are computed against generated benchmark clips with temporal IoU $\\ge 0.5$ one-to-one matching.")
    lines.append("")
    lines.append("| Trigger | Status & Tier | Config Thresh | True Segs | Prec | Recall | F1 | Signed Start Err | Signed End Err | FP / Min |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    for trig in CANONICAL_TRIGGERS:
        res = data["per_trigger"].get(trig, {})
        tier = res.get("confidence_label", "UNVERIFIED")
        thresh = res.get("threshold_in_config")
        thresh_str = f"{thresh:.2f}" if isinstance(thresh, (int, float)) else "null"
        segs = res.get("true_segments", 0)

        p = res.get("precision")
        r = res.get("recall")
        f = res.get("f1")
        p_str = f"{p * 100:.1f}%" if p is not None else "null"
        r_str = f"{r * 100:.1f}%" if r is not None else "null"
        f_str = f"{f * 100:.1f}%" if f is not None else "null"

        start_err = res.get("signed_start_error_s")
        end_err = res.get("signed_end_error_s")
        start_str = f"{start_err:+.3f}s" if start_err is not None else "null"
        end_str = f"{end_err:+.3f}s" if end_err is not None else "null"

        fa = res.get("false_alarms_per_minute")
        fa_str = f"{fa:.2f}" if fa is not None else "null"

        lines.append(
            f"| `{trig}` | {tier} | {thresh_str} | {segs} | {p_str} | {r_str} | {f_str} | {start_str} | {end_str} | {fa_str} |"
        )

    lines.append("")
    summary = data.get("dataset_summary", {})
    micro = summary.get("micro_metrics", {})
    lines.append(f"**Dataset Micro Metrics (synthetic benchmark):** Precision: **{micro.get('precision', 0) * 100:.1f}%** | Recall: **{micro.get('recall', 0) * 100:.1f}%** | F1: **{micro.get('f1', 0) * 100:.1f}%**  ")
    lines.append(f"**Total Clips Evaluated:** {summary.get('total_clips_evaluated', 0)}  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. Real-Film Validation Status")
    lines.append("")
    rf = data.get("real_film_validation", {})
    lines.append(f"- **Status:** `{rf.get('status')}`")
    lines.append(f"- **Finding:** {rf.get('description')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. Execution Environment & Dependencies")
    lines.append("")
    env = data.get("environment", {})
    lines.append(f"- **Python Version:** `{env.get('python_version')}`")
    lines.append(f"- **FFmpeg Version:** `{env.get('ffmpeg_version')}`")
    lines.append("")
    lines.append("### Key Library Versions")
    lines.append("")
    lines.append("| Package | Installed Version |")
    lines.append("| :--- | :--- |")
    for pkg, ver in env.get("libraries", {}).items():
        lines.append(f"| `{pkg}` | `{ver}` |")
    lines.append("")
    lines.append("### Acoustic Classifier Model")
    lines.append("")
    ym = env.get("yamnet_model", {})
    lines.append(f"- **Model ID:** `{ym.get('model_id')}` ({ym.get('model_name', 'YamNet')})")
    lines.append(f"- **Runtime / Precision:** `{ym.get('runtime')}` / `{ym.get('precision')}`")
    lines.append(f"- **ONNX Runtime Target:** `{ym.get('onnx_runtime_version')}`")
    lines.append(f"- **Metadata Path:** [`{ym.get('metadata_file', 'models/yamnet')}`]({ym.get('metadata_file', 'models/yamnet')})")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. Methodological Notes")
    lines.append("")
    for n in data.get("notes", []):
        lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Unified Results Runner & Benchmark Summary Generator (Person A)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-running detector evaluation harness even if cached output is up to date.",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_OUTPUT_MD,
        help=f"Path for human-readable Markdown results (default: {DEFAULT_OUTPUT_MD})",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
        help=f"Path for machine-readable JSON results (default: {DEFAULT_OUTPUT_JSON})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to pipeline config.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help=f"Path to test clips manifest.json (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=DEFAULT_LABELS_PATH,
        help=f"Path to ground truth labels.csv (default: {DEFAULT_LABELS_PATH})",
    )
    parser.add_argument(
        "--clips-dir",
        type=Path,
        default=DEFAULT_CLIPS_DIR,
        help=f"Directory containing test clips (default: {DEFAULT_CLIPS_DIR})",
    )
    parser.add_argument(
        "--eval-json",
        type=Path,
        default=DEFAULT_EVAL_JSON,
        help=f"Path for evaluation harness JSON output (default: {DEFAULT_EVAL_JSON})",
    )
    parser.add_argument(
        "--eval-md",
        type=Path,
        default=DEFAULT_EVAL_MD,
        help=f"Path for evaluation harness Markdown output (default: {DEFAULT_EVAL_MD})",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console output.",
    )

    args = parser.parse_args(argv)

    py_exe = resolve_python_executable()

    if not args.quiet:
        print("=" * 72)
        print("  TRIGGER-AWARE PLAYER — RESULTS HARNESS EXECUTION")
        print("=" * 72)
        print(f"Python interpreter: {py_exe}")
        print(f"Config path:        {args.config.resolve()}")
        print(f"Labels path:        {args.labels.resolve()}")

    # Stage 1: Run evaluation harness
    t0 = time.perf_counter()
    success, elapsed, msg = run_evaluation_stage(
        python_exe=py_exe,
        config_path=args.config,
        manifest_path=args.manifest,
        labels_path=args.labels,
        clips_dir=args.clips_dir,
        output_json=args.eval_json,
        output_md=args.eval_md,
        force=args.force,
    )

    if not success:
        print(f"[ERROR] Harness stage failed: {msg}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"[Stage 1] Evaluation Harness: {msg} ({elapsed:.2f}s)")

    # Stage 2: Build results
    try:
        results_data = build_results_data(
            eval_json_path=args.eval_json,
            config_path=args.config,
            harness_runtime_s=elapsed,
            harness_status_msg=msg,
        )
    except Exception as exc:
        print(f"[ERROR] Failed to compile results data: {exc}", file=sys.stderr)
        return 1

    # Stage 3: Write outputs
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("w", encoding="utf-8") as f:
        json.dump(results_data, f, indent=2)

    md_content = format_markdown_results(results_data)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    with args.output_md.open("w", encoding="utf-8") as f:
        f.write(md_content)

    total_time = time.perf_counter() - t0

    if not args.quiet:
        print(f"[Stage 2] Wrote JSON results: {args.output_json.resolve()}")
        print(f"[Stage 3] Wrote Markdown results: {args.output_md.resolve()}")
        print("=" * 72)
        print(f"  ALL BENCHMARK RESULTS READY in {total_time:.2f}s")
        print("=" * 72)
        micro = results_data["dataset_summary"]["micro_metrics"]
        print(
            f"Dataset Micro (synthetic benchmark): "
            f"P={micro.get('precision', 0)*100:.1f}% | "
            f"R={micro.get('recall', 0)*100:.1f}% | "
            f"F1={micro.get('f1', 0)*100:.1f}%"
        )
        print("=" * 72)

    return 0


if __name__ == "__main__":
    sys.exit(main())
