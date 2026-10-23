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

    def test_stage_sequence_with_include_experimental(self):
        """With include_experimental=True, fast motion and jump scare detectors are inserted before packaging."""
        paths = PipelinePaths.default()
        options = PipelineOptions(include_experimental=True)
        stages = build_pipeline_stages(paths, options, python_exe="test_python")

        assert len(stages) == 8
        stage_names = [s.name for s in stages]
        assert "Extract Audio" in stage_names[0]
        assert "Detect Loud Spikes" in stage_names[1]
        assert "Parse Subtitles" in stage_names[2]
        assert "Detect Jump Scares" in stage_names[3]
        assert "Package Multi-Detector Cues" in stage_names[4]
        assert "Validate Packaged Index" in stage_names[5]
        assert "Score Packaged Index" in stage_names[6]
        assert "Score Complete Dataset" in stage_names[7]

        # Verify jump scare detector stage arguments
        jump_stage = stages[3]
        assert "detect_jump_scare.py" in jump_stage.command[1]
        assert "--input" in jump_stage.command
        assert str(paths.input_media) in jump_stage.command
        assert "--output" in jump_stage.command
        assert str(paths.jump_scare_path) in jump_stage.command
        assert "--config" in jump_stage.command
        assert str(paths.config_yaml) in jump_stage.command

        # Verify packaging stage includes all 3 detector outputs
        pkg_stage = stages[4]
        input_idx = pkg_stage.command.index("--input")
        pkg_inputs = pkg_stage.command[input_idx + 1: input_idx + 4]
        assert str(paths.loudness_detections) in pkg_inputs
        assert str(paths.profanity_detections) in pkg_inputs
        assert str(paths.jump_scare_path) in pkg_inputs


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

    def test_prerequisites_check_experimental_scripts(self, tmp_path: Path):
        """When check_experimental=True, missing detector scripts are reported."""
        paths = PipelinePaths(
            repo_root=tmp_path,
            input_media=tmp_path / "clip.mp4",
            input_srt=tmp_path / "clip.srt",
            wordlist=tmp_path / "wordlist.txt",
            labels_csv=tmp_path / "labels.csv",
            config_yaml=tmp_path / "config.yaml",
            index_schema=tmp_path / "schema.json",
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
        paths.input_media.touch()
        paths.input_srt.touch()
        paths.wordlist.touch()
        paths.labels_csv.touch()
        paths.config_yaml.touch()
        paths.index_schema.touch()

        errors = check_prerequisites(paths, check_ffmpeg=False, check_experimental=True)
        assert len(errors) == 1
        assert any("detect_jump_scare.py" in e for e in errors)


class TestPipelineExecution:
    @patch("subprocess.run")
    def test_execute_pipeline_success(self, mock_run: MagicMock, tmp_path: Path):
        """Successful execution runs all stages in sequence and returns code 0."""
        mock_run.return_value = MagicMock(returncode=0)

        eval_dir = tmp_path / "eval"
        reports_dir = tmp_path / "reports"
        eval_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        paths = PipelinePaths.default()
        paths = PipelinePaths(
            repo_root=paths.repo_root,
            input_media=paths.input_media,
            input_srt=paths.input_srt,
            wordlist=paths.wordlist,
            labels_csv=paths.labels_csv,
            config_yaml=paths.config_yaml,
            index_schema=paths.index_schema,
            eval_dir=eval_dir,
            audio_wav=eval_dir / "audio.wav",
            loudness_detections=eval_dir / "loudness.json",
            profanity_detections=eval_dir / "profanity.json",
            index_json=eval_dir / "index.json",
            multi_report_json=reports_dir / "multi.json",
            multi_report_md=reports_dir / "multi.md",
            dataset_report_json=reports_dir / "dataset.json",
            dataset_report_md=reports_dir / "dataset.md",
        )
        options = PipelineOptions(skip_dataset_eval=True)

        # Ensure expected output files exist so expected_output check passes
        paths.audio_wav.touch()
        paths.loudness_detections.touch()
        paths.profanity_detections.touch()
        paths.index_json.touch()
        paths.multi_report_json.touch()

        code = execute_pipeline(paths, options, check_env=False)
        assert code == 0
        assert mock_run.call_count == 6

    @patch("subprocess.run")
    def test_execute_pipeline_failure_halts_immediately(self, mock_run: MagicMock, tmp_path: Path):
        """When a stage fails, execution halts immediately and preserves the exit code."""
        # Stage 1 succeeds, Stage 2 fails with exit code 42
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=42),
        ]

        eval_dir = tmp_path / "eval"
        eval_dir.mkdir(parents=True, exist_ok=True)
        paths = PipelinePaths.default()
        paths = PipelinePaths(
            repo_root=paths.repo_root,
            input_media=paths.input_media,
            input_srt=paths.input_srt,
            wordlist=paths.wordlist,
            labels_csv=paths.labels_csv,
            config_yaml=paths.config_yaml,
            index_schema=paths.index_schema,
            eval_dir=eval_dir,
            audio_wav=eval_dir / "audio.wav",
            loudness_detections=eval_dir / "loudness.json",
            profanity_detections=eval_dir / "profanity.json",
            index_json=eval_dir / "index.json",
            multi_report_json=tmp_path / "multi.json",
            multi_report_md=tmp_path / "multi.md",
            dataset_report_json=tmp_path / "dataset.json",
            dataset_report_md=tmp_path / "dataset.md",
        )
        paths.audio_wav.touch()
        options = PipelineOptions()

        code = execute_pipeline(paths, options, check_env=False)
        assert code == 42
        # Only 2 subprocess calls were made; stages 3-7 were NEVER executed
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_execute_pipeline_missing_expected_output_fails(self, mock_run: MagicMock, tmp_path: Path):
        """When a stage returns 0 but fails to produce its expected output file, pipeline halts."""
        mock_run.return_value = MagicMock(returncode=0)

        eval_dir = tmp_path / "eval"
        eval_dir.mkdir(parents=True, exist_ok=True)
        paths = PipelinePaths.default()
        paths = PipelinePaths(
            repo_root=paths.repo_root,
            input_media=paths.input_media,
            input_srt=paths.input_srt,
            wordlist=paths.wordlist,
            labels_csv=paths.labels_csv,
            config_yaml=paths.config_yaml,
            index_schema=paths.index_schema,
            eval_dir=eval_dir,
            audio_wav=eval_dir / "audio.wav",
            loudness_detections=eval_dir / "loudness.json",
            profanity_detections=eval_dir / "profanity.json",
            index_json=eval_dir / "index.json",
            multi_report_json=tmp_path / "multi.json",
            multi_report_md=tmp_path / "multi.md",
            dataset_report_json=tmp_path / "dataset.json",
            dataset_report_md=tmp_path / "dataset.md",
        )
        options = PipelineOptions(skip_dataset_eval=True)

        # Do NOT create audio.wav -> stage 1 should fail expected_output check
        code = execute_pipeline(paths, options, check_env=False)
        assert code == 1
        assert mock_run.call_count == 1

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
        assert args.include_experimental is False
        assert args.skip_dataset_eval is False
        assert args.dry_run is False

    def test_parse_args_include_experimental(self):
        """CLI --include-experimental flag enables experimental detectors."""
        args_default = parse_args([])
        assert args_default.include_experimental is False

        args_exp = parse_args(["--include-experimental"])
        assert args_exp.include_experimental is True

    def test_default_paths_remain_unchanged_when_out_dir_absent(self):
        """When --out-dir is absent, paths point to default locations."""
        paths = PipelinePaths.default()
        assert paths.eval_dir == paths.repo_root / "data" / "processed" / "multi_trigger_eval"
        assert paths.multi_report_json == paths.repo_root / "reports" / "multi_detector_evaluation_report.json"
        assert paths.multi_report_md == paths.repo_root / "reports" / "multi_detector_evaluation_report.md"
        assert paths.dataset_report_json == paths.repo_root / "reports" / "detection_evaluation_report.json"
        assert paths.dataset_report_md == paths.repo_root / "reports" / "detection_evaluation_report.md"

    def test_cli_out_dir_isolates_all_reports_and_intermediates(self, tmp_path: Path):
        """When --out-dir is passed, all intermediates and all 4 reports are under out_dir."""
        with patch("scripts.run_multi_detector_pipeline.execute_pipeline") as mock_exec:
            mock_exec.return_value = 0
            code = main(["--out-dir", str(tmp_path), "--include-experimental"])
            assert code == 0
            assert mock_exec.call_count == 1
            captured_paths, captured_options = mock_exec.call_args[0]

            # Intermediates
            assert captured_paths.eval_dir == tmp_path
            assert captured_paths.audio_wav == tmp_path / "audio.wav"
            assert captured_paths.loudness_detections == tmp_path / "loudness_detections.json"
            assert captured_paths.profanity_detections == tmp_path / "profanity_detections.json"
            assert captured_paths.jump_scare_detections == tmp_path / "jump_scare_detections.json"
            assert captured_paths.index_json == tmp_path / "index.json"

            # All 4 reports must be in <tmp_path>/reports/
            assert captured_paths.multi_report_json == tmp_path / "reports" / "multi_detector_evaluation_report.json"
            assert captured_paths.multi_report_md == tmp_path / "reports" / "multi_detector_evaluation_report.md"
            assert captured_paths.dataset_report_json == tmp_path / "reports" / "detection_evaluation_report.json"
            assert captured_paths.dataset_report_md == tmp_path / "reports" / "detection_evaluation_report.md"

            # Verify build_pipeline_stages commands also point inside tmp_path
            stages = build_pipeline_stages(captured_paths, captured_options, python_exe="python")
            for stage in stages:
                cmd_str = " ".join(stage.command)
                # No stage command should reference root / reports
                assert str(captured_paths.repo_root / "reports") not in cmd_str
                # No stage command should reference default processed directory
                assert "data/processed/multi_trigger_eval" not in cmd_str.replace("\\", "/")



class TestAudioEventsIntegration:
    """Focused integration tests for the --include-audio-events pipeline flag."""

    def test_parse_args_include_audio_events_flag(self):
        """CLI recognizes --include-audio-events and defaults to False when omitted."""
        args_default = parse_args([])
        assert args_default.include_audio_events is False

        args_enabled = parse_args(["--include-audio-events"])
        assert args_enabled.include_audio_events is True

    def test_classifier_stage_absent_when_flag_not_supplied(self):
        """When --include-audio-events is omitted, classifier stage is absent and baseline remains intact."""
        paths = PipelinePaths.default()
        options = PipelineOptions(include_audio_events=False)
        stages = build_pipeline_stages(paths, options, python_exe="test_python")

        assert len(stages) == 7
        stage_names = [s.name for s in stages]
        assert not any("Classify Audio Events" in name for name in stage_names)

        # Packaging stage does not include audio_events output
        pkg_stage = stages[3]
        input_idx = pkg_stage.command.index("--input")
        pkg_inputs = pkg_stage.command[input_idx + 1: input_idx + 3]
        assert str(paths.audio_events_path) not in pkg_inputs
        assert str(paths.loudness_detections) in pkg_inputs
        assert str(paths.profanity_detections) in pkg_inputs

    def test_classifier_stage_included_when_flag_supplied(self):
        """When --include-audio-events is supplied, classifier stage is included with correct paths and args."""
        paths = PipelinePaths.default()
        options = PipelineOptions(include_audio_events=True)
        stages = build_pipeline_stages(paths, options, python_exe="test_python")

        assert len(stages) == 8
        stage_names = [s.name for s in stages]
        assert "Classify Audio Events (audio_event_classifier)" in stage_names[3]

        # Verify exact classifier stage configuration
        audio_stage = stages[3]
        assert audio_stage.stage_id == 4
        assert "classify_audio_events.py" in audio_stage.command[1]
        assert "--input" in audio_stage.command
        assert str(paths.audio_wav) in audio_stage.command
        assert "--output" in audio_stage.command
        assert str(paths.audio_events_path) in audio_stage.command
        assert "--config" in audio_stage.command
        assert str(paths.config_yaml) in audio_stage.command
        assert "--strict" in audio_stage.command
        assert audio_stage.expected_output == paths.audio_events_path

        # Verify packaging stage includes audio_events output
        pkg_stage = stages[4]
        input_idx = pkg_stage.command.index("--input")
        pkg_inputs = pkg_stage.command[input_idx + 1: input_idx + 4]
        assert str(paths.loudness_detections) in pkg_inputs
        assert str(paths.profanity_detections) in pkg_inputs
        assert str(paths.audio_events_path) in pkg_inputs

    def test_stage_sequence_with_both_audio_events_and_experimental(self):
        """With both --include-audio-events and --include-experimental, all 4 detector outputs are packaged."""
        paths = PipelinePaths.default()
        options = PipelineOptions(include_audio_events=True, include_experimental=True)
        stages = build_pipeline_stages(paths, options, python_exe="test_python")

        assert len(stages) == 9
        stage_names = [s.name for s in stages]
        assert "Classify Audio Events" in stage_names[3]
        assert "Detect Jump Scares" in stage_names[4]
        assert "Package Multi-Detector Cues" in stage_names[5]

        pkg_stage = stages[5]
        input_idx = pkg_stage.command.index("--input")
        pkg_inputs = pkg_stage.command[input_idx + 1: input_idx + 5]
        assert str(paths.loudness_detections) in pkg_inputs
        assert str(paths.profanity_detections) in pkg_inputs
        assert str(paths.audio_events_path) in pkg_inputs
        assert str(paths.jump_scare_path) in pkg_inputs

    def test_prerequisites_check_audio_events(self, tmp_path: Path):
        """When check_audio_events=True, missing classify_audio_events.py is reported."""
        paths = PipelinePaths(
            repo_root=tmp_path,
            input_media=tmp_path / "clip.mp4",
            input_srt=tmp_path / "clip.srt",
            wordlist=tmp_path / "wordlist.txt",
            labels_csv=tmp_path / "labels.csv",
            config_yaml=tmp_path / "config.yaml",
            index_schema=tmp_path / "schema.json",
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
        paths.input_media.touch()
        paths.input_srt.touch()
        paths.wordlist.touch()
        paths.labels_csv.touch()
        paths.config_yaml.touch()
        paths.index_schema.touch()

        errors = check_prerequisites(paths, check_ffmpeg=False, check_audio_events=True)
        assert len(errors) == 1
        assert any("classify_audio_events.py" in e for e in errors)

    @patch("subprocess.run")
    def test_failed_classifier_stage_halts_pipeline(self, mock_run: MagicMock, tmp_path: Path):
        """If classifier fails (non-zero exit), pipeline halts immediately and returns error code."""
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir(parents=True, exist_ok=True)
        (tmp_path / "reports").mkdir(parents=True, exist_ok=True)

        paths = PipelinePaths.default()
        paths = PipelinePaths(
            repo_root=paths.repo_root,
            input_media=paths.input_media,
            input_srt=paths.input_srt,
            wordlist=paths.wordlist,
            labels_csv=paths.labels_csv,
            config_yaml=paths.config_yaml,
            index_schema=paths.index_schema,
            eval_dir=eval_dir,
            audio_wav=eval_dir / "audio.wav",
            loudness_detections=eval_dir / "loudness.json",
            profanity_detections=eval_dir / "profanity.json",
            audio_events_detections=eval_dir / "audio_events.json",
            index_json=eval_dir / "index.json",
            multi_report_json=tmp_path / "reports" / "multi.json",
            multi_report_md=tmp_path / "reports" / "multi.md",
            dataset_report_json=tmp_path / "reports" / "dataset.json",
            dataset_report_md=tmp_path / "reports" / "dataset.md",
        )

        # Mock side effects: stages 1-3 succeed, stage 4 (classifier) fails with code 2
        def run_side_effect(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "classify_audio_events.py" in cmd_str:
                return MagicMock(returncode=2)
            # Create expected output for earlier stages
            if "ffmpeg" in cmd_str:
                paths.audio_wav.touch()
            elif "detect_loudness.py" in cmd_str:
                paths.loudness_detections.touch()
            elif "parse_subtitles.py" in cmd_str:
                paths.profanity_detections.touch()
            return MagicMock(returncode=0)

        mock_run.side_effect = run_side_effect

        options = PipelineOptions(include_audio_events=True, skip_dataset_eval=True)
        ret = execute_pipeline(paths, options, check_env=False)
        assert ret == 2, f"Expected pipeline to halt with exit code 2, got {ret}"

        # Verify that subsequent packaging stage was never executed
        for call_args in mock_run.call_args_list:
            called_cmd = " ".join(str(c) for c in call_args[0][0])
            assert "package_cues.py" not in called_cmd

    def test_audio_events_packaging_contract_and_runtime_adaptation(self, tmp_path: Path):
        """Audio event classifier scream detection packages cleanly and adapts to runtime Cue with reduce action."""
        import json
        from scripts.package_cues import package_and_save_index

        audio_events_file = tmp_path / "audio_events_detections.json"
        scream_raw = [
            {
                "start": 10.0,
                "end": 12.0,
                "trigger": "scream",
                "score": 0.58,
                "source": "audio_event_classifier",
            }
        ]
        with open(audio_events_file, "w", encoding="utf-8") as f:
            json.dump(scream_raw, f, indent=2)

        out_index = tmp_path / "index.json"
        paths = PipelinePaths.default()

        index_data = package_and_save_index(
            input_path=[audio_events_file],
            output_path=out_index,
            config_path=paths.config_yaml,
            schema_path=paths.index_schema,
            film_id="scream_pipeline_test",
            duration_seconds=30.0,
            fps=24.0,
        )

        assert len(index_data["cues"]) == 1
        c = index_data["cues"][0]
        assert c["trigger"] == "scream"
        assert c["start"] == 10.0
        assert c["end"] == 12.0
        assert c["score"] == 0.58
        assert c["source"] == "audio_event_classifier"

        # Verify runtime adapter converts to runtime Cue with reduce action
        index_posix = out_index.as_posix()
        node_script = f"""
import {{ adaptPackagedIndex }} from './src/engine/packagedIndexAdapter.ts';
import {{ DEFAULT_TRIGGER_POLICIES }} from './src/data/defaultTriggerPolicies.ts';
import fs from 'fs';
const raw = JSON.parse(fs.readFileSync('{index_posix}', 'utf-8'));
const cues = adaptPackagedIndex(raw, DEFAULT_TRIGGER_POLICIES);
console.log(JSON.stringify(cues[0]));
"""
        res = subprocess.run(
            ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
            capture_output=True,
            text=True,
            cwd=str(paths.repo_root),
        )
        assert res.returncode == 0, f"Node adapter failed: {res.stderr}"
        for line in res.stdout.strip().split("\n"):
            if line.startswith("{") and "cue_" in line:
                cue_obj = json.loads(line)
                assert cue_obj["trigger"] == "scream"
                assert cue_obj["action"] == "reduce"
                assert cue_obj["source"] == "audio"
                assert cue_obj["score"] == 0.58
                assert cue_obj["start"] == 10.0
                assert cue_obj["end"] == 12.0
