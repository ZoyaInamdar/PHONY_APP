"""Unit tests for the Reproducible Multi-Detector Pipeline Runner (Person B).

Tests cover:
- Pipeline path construction and isolation
- Command construction and stage ordering
- Prerequisite checking (FFmpeg, media, subtitles, schemas)
- Failure propagation and immediate pipeline halt
- Mocked end-to-end execution
- Output path safety (preventing overwrite of data/index.json)
- CLI argument parsing and flags
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from scripts.run_multi_detector_pipeline import (
    PipelineOptions,
    PipelinePaths,
    PipelineStage,
    build_pipeline_stages,
    check_prerequisites,
    execute_pipeline,
    main,
    parse_args,
)


class TestPipelinePathsAndOptions:
    def test_default_paths_structure(self):
        """Default paths must all be anchored to repository root."""
        paths = PipelinePaths.default()
        assert paths.repo_root.is_dir()
        assert paths.input_media.name == "multi_trigger_test.mp4"
        assert paths.input_srt.name == "multi_trigger_test.srt"
        assert paths.wordlist.name == "profanity_wordlist.txt"
        assert paths.labels_csv.name == "labels.csv"
        assert paths.config_yaml.name == "config.yaml"
        assert paths.index_schema.name == "index.schema.json"
        assert paths.eval_dir == paths.repo_root / "data" / "processed" / "multi_trigger_eval"
        assert paths.index_json == paths.eval_dir / "index.json"
        assert paths.multi_report_json.name == "multi_detector_evaluation_report.json"
        assert paths.multi_report_md.name == "multi_detector_evaluation_report.md"
        assert paths.dataset_report_json.name == "detection_evaluation_report.json"
        assert paths.dataset_report_md.name == "detection_evaluation_report.md"

    def test_output_path_safety_does_not_target_root_index_json(self):
        """Pipeline must NEVER write to top-level data/index.json."""
        paths = PipelinePaths.default()
        root_index = paths.repo_root / "data" / "index.json"
        assert paths.index_json != root_index
        assert paths.index_json.parent == paths.eval_dir


class TestStageBuilding:
    def test_stage_sequence_and_order(self):
        """Stages must execute in strict sequence: extract -> detect -> parse -> package -> validate -> score."""
        paths = PipelinePaths.default()
        options = PipelineOptions()
        stages = build_pipeline_stages(paths, options, python_exe="test_python")

        assert len(stages) == 7
        stage_names = [s.name for s in stages]
        assert "Extract Audio" in stage_names[0]
        assert "Detect Loud Spikes" in stage_names[1]
        assert "Parse Subtitles" in stage_names[2]
        assert "Package Multi-Detector Cues" in stage_names[3]
        assert "Validate Packaged Index" in stage_names[4]
        assert "Score Packaged Index" in stage_names[5]
        assert "Score Complete Dataset" in stage_names[6]

        # Verify Stage 5 (validation) runs BEFORE Stage 6 (evaluation)
        assert stages[4].stage_id == 5
        assert "validate_index.py" in stages[4].command[1]
        assert stages[5].stage_id == 6
        assert "evaluate_detections.py" in stages[5].command[1]

    def test_skip_dataset_eval_option(self):
        """With skip_dataset_eval=True, only 6 stages are generated."""
        paths = PipelinePaths.default()
        options = PipelineOptions(skip_dataset_eval=True)
        stages = build_pipeline_stages(paths, options, python_exe="test_python")

        assert len(stages) == 6
        stage_names = [s.name for s in stages]
        assert not any("Complete Dataset" in name for name in stage_names)

    def test_generate_media_option(self):
        """With generate_media=True, media generation is prepended as Stage 1."""
        paths = PipelinePaths.default()
        options = PipelineOptions(generate_media=True)
        stages = build_pipeline_stages(paths, options, python_exe="test_python")

        assert len(stages) == 8
        assert "generate_test_clips.py" in stages[0].command[1]
        assert "--multi-trigger-only" in stages[0].command

    def test_command_explicit_paths(self):
        """Commands must use explicit path arguments matching PipelinePaths."""
        paths = PipelinePaths.default()
        options = PipelineOptions(film_id="demo_film", duration=12.5, fps=30.0)
        stages = build_pipeline_stages(paths, options, python_exe="test_python")

        # Package cues stage check
        pkg_stage = stages[3]
        assert "--input" in pkg_stage.command
        assert str(paths.loudness_detections) in pkg_stage.command
        assert str(paths.profanity_detections) in pkg_stage.command
        assert "--output" in pkg_stage.command
        assert str(paths.index_json) in pkg_stage.command
        assert "--film-id" in pkg_stage.command
        assert "demo_film" in pkg_stage.command
        assert "--duration" in pkg_stage.command
        assert "12.5" in pkg_stage.command
        assert "--fps" in pkg_stage.command
        assert "30.0" in pkg_stage.command


class TestPrerequisitesCheck:
    def test_prerequisites_check_in_repo(self):
        """In the actual repository, existing test fixtures and schemas satisfy prerequisites."""
        paths = PipelinePaths.default()
        errors = check_prerequisites(paths, check_ffmpeg=False)
        assert errors == []

    def test_prerequisites_missing_file_reporting(self, tmp_path: Path):
        """Missing input files are explicitly reported in error list."""
        paths = PipelinePaths(
            repo_root=tmp_path,
            input_media=tmp_path / "missing.mp4",
            input_srt=tmp_path / "missing.srt",
            wordlist=tmp_path / "missing.txt",
            labels_csv=tmp_path / "missing.csv",
            config_yaml=tmp_path / "missing.yaml",
            index_schema=tmp_path / "missing.json",
            eval_dir=tmp_path / "eval",
            audio_wav=tmp_path / "eval" / "audio.wav",
            loudness_detections=tmp_path / "eval" / "loudness.json",
            profanity_detections=tmp_path / "eval" / "profanity.json",
            index_json=tmp_path / "eval" / "index.json",
            multi_report_json=tmp_path / "reports" / "multi.json",
            multi_report_md=tmp_path / "reports" / "multi.md",
            dataset_report_json=tmp_path / "reports" / "dataset.json",
            dataset_report_md=tmp_path / "reports" / "dataset.md",
        )
        errors = check_prerequisites(paths, check_ffmpeg=False)
        assert len(errors) == 6
        assert any("missing.mp4" in e for e in errors)
        assert any("missing.srt" in e for e in errors)

    def test_prerequisites_missing_ffmpeg(self):
        """Missing ffmpeg on system PATH is reported."""
        paths = PipelinePaths.default()
        with patch("shutil.which", return_value=None):
            errors = check_prerequisites(paths, check_ffmpeg=True)
            assert any("FFmpeg" in e for e in errors)


class TestPipelineExecution:
    @patch("subprocess.run")
    def test_execute_pipeline_success(self, mock_run: MagicMock):
        """Successful execution runs all stages in sequence and returns code 0."""
        mock_run.return_value = MagicMock(returncode=0)

        paths = PipelinePaths.default()
        options = PipelineOptions(skip_dataset_eval=True)

        code = execute_pipeline(paths, options, check_env=False)
        assert code == 0
        assert mock_run.call_count == 6

    @patch("subprocess.run")
    def test_execute_pipeline_failure_halts_immediately(self, mock_run: MagicMock):
        """When a stage fails, execution halts immediately and preserves the exit code."""
        # Stage 1 succeeds, Stage 2 fails with exit code 42
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=42),
        ]

        paths = PipelinePaths.default()
        options = PipelineOptions()

        code = execute_pipeline(paths, options, check_env=False)
        assert code == 42
        # Only 2 subprocess calls were made; stages 3-7 were NEVER executed
        assert mock_run.call_count == 2

    def test_execute_pipeline_prerequisites_failure_stops_before_any_command(self, tmp_path: Path):
        """Prerequisite failure returns 1 without running any commands."""
        paths = PipelinePaths.default()
        bad_paths = PipelinePaths(
            repo_root=paths.repo_root,
            input_media=tmp_path / "non_existent.mp4",
            input_srt=paths.input_srt,
            wordlist=paths.wordlist,
            labels_csv=paths.labels_csv,
            config_yaml=paths.config_yaml,
            index_schema=paths.index_schema,
            eval_dir=paths.eval_dir,
            audio_wav=paths.audio_wav,
            loudness_detections=paths.loudness_detections,
            profanity_detections=paths.profanity_detections,
            index_json=paths.index_json,
            multi_report_json=paths.multi_report_json,
            multi_report_md=paths.multi_report_md,
            dataset_report_json=paths.dataset_report_json,
            dataset_report_md=paths.dataset_report_md,
        )

        with patch("subprocess.run") as mock_run:
            code = execute_pipeline(bad_paths, PipelineOptions(), check_env=True)
            assert code == 1
            assert mock_run.call_count == 0


class TestCLIInvocation:
    def test_cli_dry_run(self):
        """CLI with --dry-run prints plan without subprocess execution."""
        with patch("subprocess.run") as mock_run:
            code = main(["--dry-run", "--skip-dataset-eval"])
            assert code == 0
            assert mock_run.call_count == 0

    def test_parse_args_defaults(self):
        """CLI arguments default to expected standard pipeline parameters."""
        args = parse_args([])
        assert args.film_id == "multi_trigger_test"
        assert args.duration == 15.0
        assert args.fps == 24.0
        assert args.generate_media is False
        assert args.skip_dataset_eval is False
        assert args.dry_run is False
