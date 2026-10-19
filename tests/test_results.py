"""Unit tests for scripts/results.py (Person A).

Verifies calculation of false alarms per minute, confidence labeling,
pure data assembly, markdown formatting with required disclaimers,
and safe handling of missing files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from scripts.results import (
    CANONICAL_TRIGGERS,
    FLASH_DISCLAIMER,
    build_results_data,
    calculate_false_alarms_per_minute,
    determine_confidence_label,
    extract_trigger_config,
    format_markdown_results,
    get_library_versions,
    main,
    run_evaluation_stage,
)


def test_calculate_false_alarms_per_minute():
    """Verify rate calculations across positive, zero, and boundary durations."""
    # 3 FPs over 60 seconds = 3.0 / min
    assert calculate_false_alarms_per_minute(3, 60.0) == 3.0
    # 1 FP over 30 seconds = 2.0 / min
    assert calculate_false_alarms_per_minute(1, 30.0) == 2.0
    # 0 FPs over 120 seconds = 0.0 / min
    assert calculate_false_alarms_per_minute(0, 120.0) == 0.0
    # 0 or negative duration returns None
    assert calculate_false_alarms_per_minute(1, 0.0) is None
    assert calculate_false_alarms_per_minute(1, -5.0) is None


def test_determine_confidence_label():
    """Verify confidence tier assignment reflects availability and F1."""
    # Unimplemented detector
    assert "dropped" in determine_confidence_label("spider", "baseline", False, None)
    # Configured experimental
    assert "experimental" in determine_confidence_label("jump_scare", "experimental", True, None)
    # High F1 baseline
    assert "high confidence" in determine_confidence_label("flashing", "baseline", True, 0.89)
    # Low F1 baseline
    assert "experimental" in determine_confidence_label("profanity", "baseline", True, 0.40)
    # Calibrating (no F1 yet)
    assert "calibrating" in determine_confidence_label("loud_spike", "baseline", True, None)


def test_extract_trigger_config():
    """Verify extraction of canonical trigger thresholds and statuses from config data."""
    mock_config = {
        "triggers": {
            "flashing": {"threshold": 3.0, "status": "baseline", "enabled": True},
            "loud_spike": {"threshold": 0.7, "status": "baseline", "enabled": True},
        }
    }
    extracted = extract_trigger_config(mock_config)
    assert "flashing" in extracted
    assert extracted["flashing"]["threshold"] == 3.0
    assert extracted["flashing"]["status"] == "baseline"
    assert "spider" in extracted
    assert extracted["spider"]["status"] == "UNVERIFIED"


def test_get_library_versions():
    """Verify that key library versions are successfully retrieved without crash."""
    versions = get_library_versions(["pytest", "yaml", "non_existent_fake_package"])
    assert "pytest" in versions
    assert versions["pytest"] != "not installed"
    assert "yaml" in versions
    assert versions["non_existent_fake_package"] == "not installed"


def test_format_markdown_results_requirements():
    """Verify results.md includes required phrases, synthetic labels, and tables."""
    mock_data = {
        "benchmark_type": "synthetic benchmark",
        "generated_timestamp_utc": "2026-10-19T12:00:00Z",
        "run_date": "2026-10-19",
        "runtimes": {
            "harness_evaluation_seconds": 1.23,
            "harness_status": "Executed successfully",
        },
        "environment": {
            "python_version": "3.14.4",
            "ffmpeg_version": "9.0.2",
            "libraries": {"numpy": "2.5.4", "pytest": "8.4.2"},
            "yamnet_model": {"model_id": "yamnet", "runtime": "onnx", "precision": "float"},
        },
        "flash_detector_specification": FLASH_DISCLAIMER,
        "dataset_summary": {
            "total_clips_evaluated": 10,
            "counts": {"true_positives": 5, "false_positives": 1, "false_negatives": 2},
            "micro_metrics": {"precision": 0.8333, "recall": 0.7143, "f1": 0.7692},
            "macro_metrics": {"precision": 0.80, "recall": 0.70, "f1": 0.75},
        },
        "per_trigger": {
            "flashing": {
                "display_name": "Rapid Flashing",
                "status": "evaluated",
                "confidence_label": "high confidence",
                "threshold_in_config": 0.1,
                "true_segments": 10,
                "true_positives": 8,
                "false_positives": 0,
                "false_negatives": 2,
                "precision": 1.0,
                "recall": 0.8,
                "f1": 0.8889,
                "signed_start_error_s": 0.0,
                "signed_end_error_s": -0.01,
                "false_alarms_per_minute": 0.0,
            }
        },
        "real_film_validation": {
            "status": "no_hand_labelled_full_film_clips",
            "description": "None exist for real full films.",
        },
        "notes": ["Sample note"],
    }

    md = format_markdown_results(mock_data)

    # Rule 5: Exact flash detector phrasing
    assert (
        "detects rapid luminance flashing over screen regions, approximating the WCAG general-flash definition. Not a compliance check."
        in md
    )
    # Rule 5: Scores are not probabilities
    assert "detector scores are not probabilities" in md.lower()
    # Rule 6: Synthetic benchmark label
    assert "synthetic benchmark" in md
    # Real film section present
    assert "Real-Film Validation Status" in md
    assert "no_hand_labelled_full_film_clips" in md


def test_build_results_data_missing_file_raises(tmp_path: Path):
    """Verify build_results_data safely raises FileNotFoundError if report missing."""
    missing_json = tmp_path / "non_existent_report.json"
    dummy_config = tmp_path / "config.yaml"
    dummy_config.write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        build_results_data(
            eval_json_path=missing_json,
            config_path=dummy_config,
            harness_runtime_s=0.0,
            harness_status_msg="N/A",
        )


def test_run_evaluation_stage_missing_config_or_labels(tmp_path: Path):
    """Verify run_evaluation_stage returns False immediately if config or labels missing."""
    dummy_eval = tmp_path / "dummy_eval.py"
    dummy_eval.write_text("print('hello')", encoding="utf-8")
    missing_config = tmp_path / "nonexistent_config.yaml"
    missing_labels = tmp_path / "nonexistent_labels.csv"
    existing_file = tmp_path / "existing.txt"
    existing_file.write_text("ok", encoding="utf-8")

    # Missing config
    success, elapsed, msg = run_evaluation_stage(
        python_exe="python3",
        config_path=missing_config,
        manifest_path=existing_file,
        labels_path=existing_file,
        clips_dir=tmp_path,
        output_json=tmp_path / "out.json",
        output_md=tmp_path / "out.md",
        force=True,
    )
    assert not success
    assert "Config file missing" in msg

    # Missing labels
    success, elapsed, msg = run_evaluation_stage(
        python_exe="python3",
        config_path=existing_file,
        manifest_path=existing_file,
        labels_path=missing_labels,
        clips_dir=tmp_path,
        output_json=tmp_path / "out.json",
        output_md=tmp_path / "out.md",
        force=True,
    )
    assert not success
    assert "Labels file missing" in msg


def test_main_cli_missing_file_exit_code(tmp_path: Path):
    """Verify CLI main returns exit code 1 when required input files do not exist."""
    missing_config = str(tmp_path / "missing_config.yaml")
    missing_labels = str(tmp_path / "missing_labels.csv")

    code = main([
        "--config", missing_config,
        "--labels", missing_labels,
        "--quiet",
    ])
    assert code == 1

