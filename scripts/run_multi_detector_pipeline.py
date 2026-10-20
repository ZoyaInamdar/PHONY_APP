#!/usr/bin/env python3
"""Reproducible Multi-Detector Pipeline Runner for Trigger-Aware Player (Person B).

Executes the complete operational multi-detector demonstration pipeline in a single
reproducible command:
1. Audio extraction via FFmpeg (44.1kHz mono 16-bit PCM WAV).
2. Audio loudness detection (`scripts/detect_loudness.py`).
3. Subtitle parsing & profanity detection (`scripts/parse_subtitles.py`).
4. Multi-detector cue packaging into unified index (`scripts/package_cues.py`).
5. Schema and domain validation of packaged index (`scripts/validate_index.py`).
6. Same-timeline multi-detector evaluation report generation (`scripts/evaluate_detections.py`).
7. Dataset-wide detector benchmark report generation (`scripts/evaluate_detections.py`).

Usage:
    python scripts/run_multi_detector_pipeline.py
    python scripts/run_multi_detector_pipeline.py --help
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

# Ensure repository root is on sys.path and resolve paths relative to root
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True)
class PipelinePaths:
    """Explicit filesystem paths for all pipeline inputs, intermediates, and outputs."""
    repo_root: Path
    input_media: Path
    input_srt: Path
    wordlist: Path
    labels_csv: Path
    config_yaml: Path
    index_schema: Path
    eval_dir: Path
    audio_wav: Path
    loudness_detections: Path
    profanity_detections: Path
    index_json: Path
    multi_report_json: Path
    multi_report_md: Path
    dataset_report_json: Path
    dataset_report_md: Path
    fast_motion_detections: Optional[Path] = None
    jump_scare_detections: Optional[Path] = None

    @classmethod
    def default(cls, repo_root: Optional[Path] = None) -> PipelinePaths:
        root = repo_root or REPO_ROOT
        eval_dir = root / "data" / "processed" / "multi_trigger_eval"
        reports_dir = root / "reports"
        return cls(
            repo_root=root,
            input_media=root / "data" / "test_clips" / "multi_trigger_test.mp4",
            input_srt=root / "data" / "test_clips" / "multi_trigger_test.srt",
            wordlist=root / "data" / "profanity_wordlist.txt",
            labels_csv=root / "data" / "test_clips" / "labels.csv",
            config_yaml=root / "config.yaml",
            index_schema=root / "schema" / "index.schema.json",
            eval_dir=eval_dir,
            audio_wav=eval_dir / "audio.wav",
            loudness_detections=eval_dir / "loudness_detections.json",
            profanity_detections=eval_dir / "profanity_detections.json",
            index_json=eval_dir / "index.json",
            multi_report_json=reports_dir / "multi_detector_evaluation_report.json",
            multi_report_md=reports_dir / "multi_detector_evaluation_report.md",
            dataset_report_json=reports_dir / "detection_evaluation_report.json",
            dataset_report_md=reports_dir / "detection_evaluation_report.md",
            fast_motion_detections=eval_dir / "fast_motion_detections.json",
            jump_scare_detections=eval_dir / "jump_scare_detections.json",
        )

    @property
    def fast_motion_path(self) -> Path:
        return self.fast_motion_detections if self.fast_motion_detections is not None else (self.eval_dir / "fast_motion_detections.json")

    @property
    def jump_scare_path(self) -> Path:
        return self.jump_scare_detections if self.jump_scare_detections is not None else (self.eval_dir / "jump_scare_detections.json")


@dataclass(frozen=True)
class PipelineOptions:
    """Operational parameters for the pipeline run."""
    film_id: str = "multi_trigger_test"
    duration: float = 15.0
    fps: float = 24.0
    skip_dataset_eval: bool = False
    generate_media: bool = False
    dry_run: bool = False
    include_experimental: bool = False


@dataclass(frozen=True)
class PipelineStage:
    """Definition of a single discrete pipeline stage."""
    stage_id: int
    name: str
    command: List[str]
    description: str
    expected_output: Optional[Path] = None


def check_prerequisites(
    paths: PipelinePaths,
    check_ffmpeg: bool = True,
    check_experimental: bool = False,
) -> List[str]:
    """Check required input files and binaries. Returns list of error messages (empty if OK)."""
    errors: List[str] = []

    if check_ffmpeg and shutil.which("ffmpeg") is None:
        errors.append("FFmpeg executable not found in system PATH. Required for audio extraction.")

    if not paths.input_media.is_file():
        errors.append(f"Input media clip not found: {paths.input_media}")

    if not paths.input_srt.is_file():
        errors.append(f"Input subtitle file not found: {paths.input_srt}")

    if not paths.wordlist.is_file():
        errors.append(f"Profanity wordlist not found: {paths.wordlist}")

    if not paths.labels_csv.is_file():
        errors.append(f"Ground-truth labels CSV not found: {paths.labels_csv}")

    if not paths.config_yaml.is_file():
        errors.append(f"Pipeline config.yaml not found: {paths.config_yaml}")

    if not paths.index_schema.is_file():
        errors.append(f"Index schema JSON not found: {paths.index_schema}")

    if check_experimental:
        fast_motion_script = paths.repo_root / "scripts" / "detect_fast_motion.py"
        jump_scare_script = paths.repo_root / "scripts" / "detect_jump_scare.py"
        if not fast_motion_script.is_file():
            errors.append(f"Fast motion detector script not found: {fast_motion_script}")
        if not jump_scare_script.is_file():
            errors.append(f"Jump scare detector script not found: {jump_scare_script}")

    return errors


def build_pipeline_stages(
    paths: PipelinePaths,
    options: PipelineOptions,
    python_exe: Optional[str] = None,
) -> List[PipelineStage]:
    """Build the ordered list of pipeline stage commands with explicit paths."""
    py = python_exe or sys.executable
    stages: List[PipelineStage] = []
    current_id = 1

    # Optional Stage 0: Media Generation
    if options.generate_media:
        stages.append(
            PipelineStage(
                stage_id=current_id,
                name="Generate Synthetic Media & Subtitles",
                command=[
                    py,
                    str(paths.repo_root / "scripts" / "generate_test_clips.py"),
                    "--multi-trigger-only",
                ],
                description="Generates or refreshes multi_trigger_test.mp4, subtitles, and labels.",
            )
        )
        current_id += 1

    # Stage 1: Audio extraction with FFmpeg
    stages.append(
        PipelineStage(
            stage_id=current_id,
            name="Extract Audio (FFmpeg)",
            command=[
                "ffmpeg",
                "-y",
                "-i",
                str(paths.input_media),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-c:a",
                "pcm_s16le",
                str(paths.audio_wav),
            ],
            description="Extracts 44.1kHz mono 16-bit PCM WAV from media container.",
            expected_output=paths.audio_wav,
        )
    )
    current_id += 1

    # Stage 2: Loudness Detection
    stages.append(
        PipelineStage(
            stage_id=current_id,
            name="Detect Loud Spikes (loudness_detector)",
            command=[
                py,
                str(paths.repo_root / "scripts" / "detect_loudness.py"),
                "--input",
                str(paths.audio_wav),
                "--output",
                str(paths.loudness_detections),
                "--config",
                str(paths.config_yaml),
                "--film-id",
                options.film_id,
            ],
            description="Detects acoustic spikes exceeding loudness threshold.",
            expected_output=paths.loudness_detections,
        )
    )
    current_id += 1

    # Stage 3: Subtitle Parsing / Profanity Detection
    stages.append(
        PipelineStage(
            stage_id=current_id,
            name="Parse Subtitles (profanity_detector)",
            command=[
                py,
                str(paths.repo_root / "scripts" / "parse_subtitles.py"),
                str(paths.input_srt),
                "--wordlist",
                str(paths.wordlist),
                "--out-detections",
                str(paths.profanity_detections),
                "--source",
                "profanity_detector",
            ],
            description="Matches subtitle cues against profanity wordlist.",
            expected_output=paths.profanity_detections,
        )
    )
    current_id += 1

    # Optional Stages: Experimental Detectors
    if options.include_experimental:
        stages.append(
            PipelineStage(
                stage_id=current_id,
                name="Detect Fast Motion (motion_detector)",
                command=[
                    py,
                    str(paths.repo_root / "scripts" / "detect_fast_motion.py"),
                    "--input",
                    str(paths.input_media),
                    "--output",
                    str(paths.fast_motion_path),
                    "--config",
                    str(paths.config_yaml),
                ],
                description="Detects visual fast motion and camera shake from video luminance differences.",
                expected_output=paths.fast_motion_path,
            )
        )
        current_id += 1

        stages.append(
            PipelineStage(
                stage_id=current_id,
                name="Detect Jump Scares (jump_scare_detector)",
                command=[
                    py,
                    str(paths.repo_root / "scripts" / "detect_jump_scare.py"),
                    "--input",
                    str(paths.input_media),
                    "--output",
                    str(paths.jump_scare_path),
                    "--config",
                    str(paths.config_yaml),
                ],
                description="Detects multimodal compound jump scares fusing audio spikes and rapid visual change.",
                expected_output=paths.jump_scare_path,
            )
        )
        current_id += 1

    # Stage 4: Package Cues into Index
    package_inputs = [
        str(paths.loudness_detections),
        str(paths.profanity_detections),
    ]
    if options.include_experimental:
        package_inputs.extend([
            str(paths.fast_motion_path),
            str(paths.jump_scare_path),
        ])

    stages.append(
        PipelineStage(
            stage_id=current_id,
            name="Package Multi-Detector Cues into Index",
            command=[
                py,
                str(paths.repo_root / "scripts" / "package_cues.py"),
                "--input",
                *package_inputs,
                "--output",
                str(paths.index_json),
                "--config",
                str(paths.config_yaml),
                "--schema",
                str(paths.index_schema),
                "--film-id",
                options.film_id,
                "--duration",
                str(options.duration),
                "--fps",
                str(options.fps),
            ],
            description="Combines detections into a single chronological cue index.",
            expected_output=paths.index_json,
        )
    )
    current_id += 1

    # Stage 5: Validate Index
    stages.append(
        PipelineStage(
            stage_id=current_id,
            name="Validate Packaged Index",
            command=[
                py,
                str(paths.repo_root / "scripts" / "validate_index.py"),
                "--index",
                str(paths.index_json),
                "--schema",
                str(paths.index_schema),
                "--config",
                str(paths.config_yaml),
            ],
            description="Validates index schema, interval boundaries, and policy compliance.",
        )
    )
    current_id += 1

    # Stage 6: Score Packaged Index Against Ground-Truth
    stages.append(
        PipelineStage(
            stage_id=current_id,
            name="Score Packaged Index (multi_trigger_test)",
            command=[
                py,
                str(paths.repo_root / "scripts" / "evaluate_detections.py"),
                "--index",
                str(paths.index_json),
                "--labels",
                str(paths.labels_csv),
                "--film-id",
                options.film_id,
                "--output-json",
                str(paths.multi_report_json),
                "--output-md",
                str(paths.multi_report_md),
            ],
            description="Evaluates same-timeline cues against ground-truth and outputs reports.",
            expected_output=paths.multi_report_json,
        )
    )
    current_id += 1

    # Stage 7: Dataset-Wide Evaluation (optional)
    if not options.skip_dataset_eval:
        stages.append(
            PipelineStage(
                stage_id=current_id,
                name="Score Complete Dataset Across Available Detectors",
                command=[
                    py,
                    str(paths.repo_root / "scripts" / "evaluate_detections.py"),
                    "--output-json",
                    str(paths.dataset_report_json),
                    "--output-md",
                    str(paths.dataset_report_md),
                ],
                description="Runs full dataset evaluation across positive and negative controls.",
                expected_output=paths.dataset_report_json,
            )
        )

    return stages


def execute_pipeline(
    paths: PipelinePaths,
    options: PipelineOptions,
    check_env: bool = True,
) -> int:
    """Execute pipeline stages in order. Stops immediately on failure and returns exit code."""
    # Ensure working output directories exist
    paths.eval_dir.mkdir(parents=True, exist_ok=True)
    paths.multi_report_json.parent.mkdir(parents=True, exist_ok=True)
    paths.dataset_report_json.parent.mkdir(parents=True, exist_ok=True)

    # Validate prerequisites
    if check_env:
        errors = check_prerequisites(paths, check_ffmpeg=True, check_experimental=options.include_experimental)
        if errors:
            print("=================================================================", file=sys.stderr)
            print("[ERROR] Pipeline prerequisites check failed:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            print("=================================================================", file=sys.stderr)
            return 1

    stages = build_pipeline_stages(paths, options)
    total_stages = len(stages)

    print("=================================================================")
    print("  TRIGGER-AWARE PLAYER — MULTI-DETECTOR PIPELINE RUNNER")
    print(f"  Film ID: {options.film_id} | Media Duration: {options.duration}s | Total Stages: {total_stages}")
    print(f"  Working Dir: {paths.eval_dir}")
    print("=================================================================\n")

    for stage in stages:
        cmd_str = " ".join(stage.command)
        print(f"[{stage.stage_id}/{total_stages}] {stage.name}")
        print(f"  Desc: {stage.description}")
        print(f"  Cmd:  {cmd_str}\n")

        if options.dry_run:
            print(f"  [DRY-RUN] Skipping execution for stage {stage.stage_id}.\n")
            continue

        try:
            res = subprocess.run(
                stage.command,
                cwd=str(paths.repo_root),
                check=False,
            )
            if res.returncode != 0:
                print(
                    f"\n[FATAL] Stage {stage.stage_id} ({stage.name}) failed with exit code {res.returncode}.",
                    file=sys.stderr,
                )
                print("Pipeline execution halted immediately. Subsequent stages cancelled.", file=sys.stderr)
                return res.returncode

            if stage.expected_output and not stage.expected_output.is_file():
                print(
                    f"\n[FATAL] Stage {stage.stage_id} ({stage.name}) exited with code 0 but expected output file was not created: {stage.expected_output}",
                    file=sys.stderr,
                )
                print("Pipeline execution halted immediately. Subsequent stages cancelled.", file=sys.stderr)
                return 1
        except Exception as exc:
            print(f"\n[FATAL] Stage {stage.stage_id} ({stage.name}) raised unexpected error: {exc}", file=sys.stderr)
            return 1

        print(f"  -> Stage {stage.stage_id} completed successfully.\n")

    print("=================================================================")
    print("  PIPELINE EXECUTION COMPLETE — ALL STAGES PASSED")
    print("=================================================================")
    print(f"  Packaged Index:       {paths.index_json}")
    print(f"  Multi-Trigger Report: {paths.multi_report_md}")
    if not options.skip_dataset_eval:
        print(f"  Dataset Report:       {paths.dataset_report_md}")
    print("=================================================================\n")

    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Reproducible multi-detector evaluation pipeline runner (Person B).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--media",
        type=Path,
        default=None,
        help="Path to input media file (default: data/test_clips/multi_trigger_test.mp4).",
    )
    parser.add_argument(
        "--srt",
        type=Path,
        default=None,
        help="Path to input subtitle file (default: data/test_clips/multi_trigger_test.srt).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory for processed artifacts (default: data/processed/multi_trigger_eval).",
    )
    parser.add_argument(
        "--film-id",
        type=str,
        default="multi_trigger_test",
        help="Film identifier matching cues and ground truth.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=15.0,
        help="Declared media timeline duration in seconds.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=24.0,
        help="Video frame rate in frames per second.",
    )
    parser.add_argument(
        "--generate-media",
        action="store_true",
        help="Run media generation prior to audio extraction.",
    )
    parser.add_argument(
        "--include-experimental",
        action="store_true",
        help="Include experimental detectors (fast_motion, jump_scare) in pipeline execution and packaging.",
    )
    parser.add_argument(
        "--skip-dataset-eval",
        action="store_true",
        help="Skip the dataset-wide evaluation stage and run only same-timeline evaluation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print stages and commands without executing them.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)

    paths = PipelinePaths.default()
    if args.media:
        paths = dataclass_replace(paths, input_media=args.media.resolve())
    if args.srt:
        paths = dataclass_replace(paths, input_srt=args.srt.resolve())
    if args.out_dir:
        out_dir = args.out_dir.resolve()
        reports_dir = out_dir / "reports"
        paths = dataclass_replace(
            paths,
            eval_dir=out_dir,
            audio_wav=out_dir / "audio.wav",
            loudness_detections=out_dir / "loudness_detections.json",
            profanity_detections=out_dir / "profanity_detections.json",
            fast_motion_detections=out_dir / "fast_motion_detections.json",
            jump_scare_detections=out_dir / "jump_scare_detections.json",
            index_json=out_dir / "index.json",
            multi_report_json=reports_dir / "multi_detector_evaluation_report.json",
            multi_report_md=reports_dir / "multi_detector_evaluation_report.md",
            dataset_report_json=reports_dir / "detection_evaluation_report.json",
            dataset_report_md=reports_dir / "detection_evaluation_report.md",
        )

    options = PipelineOptions(
        film_id=args.film_id,
        duration=args.duration,
        fps=args.fps,
        skip_dataset_eval=args.skip_dataset_eval,
        generate_media=args.generate_media,
        dry_run=args.dry_run,
        include_experimental=args.include_experimental,
    )

    return execute_pipeline(paths, options)


def dataclass_replace(obj: PipelinePaths, **kwargs) -> PipelinePaths:
    """Helper to shallow-replace fields in frozen dataclass."""
    fields = {k: getattr(obj, k) for k in obj.__dataclass_fields__}
    fields.update(kwargs)
    return PipelinePaths(**fields)


if __name__ == "__main__":
    sys.exit(main())
