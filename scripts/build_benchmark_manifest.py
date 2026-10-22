#!/usr/bin/env python3
"""Build canonical benchmark_manifest.json consolidating synthetic, natural, and candidate clips."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_CLIPS_MANIFEST = REPO_ROOT / "data" / "test_clips" / "manifest.json"
TEST_CLIPS_LABELS = REPO_ROOT / "data" / "test_clips" / "labels.csv"
NATURAL_VALIDATION_MANIFEST = REPO_ROOT / "data" / "natural_validation" / "manifest.json"
BENCHMARK_DIR = REPO_ROOT / "data" / "benchmark"
BENCHMARK_MANIFEST = BENCHMARK_DIR / "manifest.json"


def build_benchmark_manifest():
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

    # Load synthetic test clips manifest
    with TEST_CLIPS_MANIFEST.open("r", encoding="utf-8") as f:
        syn_manifest = json.load(f)

    # Load synthetic labels.csv
    syn_labels_by_clip = {}
    with TEST_CLIPS_LABELS.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            syn_labels_by_clip.setdefault(row["clip"], []).append(row)

    clips = []

    # 1. Process 34 synthetic test clips -> split: train_dev
    for syn_clip in syn_manifest["clips"]:
        clip_name = syn_clip["clip_name"]
        duration = float(syn_clip["duration"])
        fps = float(syn_clip.get("fps", 24.0))
        width = int(syn_clip.get("width", 640))
        height = int(syn_clip.get("height", 360))
        meta = syn_clip.get("metadata", {})
        raw_mod = meta.get("modality")
        if raw_mod == "speech_subtitles":
            modality = "subtitles"
        elif raw_mod == "audio":
            modality = "audio"
        elif fps > 0:
            modality = "video"
        else:
            modality = "audio"

        # Determine if it's a negative control and specific negative triggers
        is_negative = (
            syn_clip.get("trigger") is None
            or "negative" in clip_name.lower()
            or "ambient" in meta.get("event_type", "")
            or "dialogue" in meta.get("event_type", "")
            or "static" in meta.get("event_type", "")
        )

        neg_triggers = None
        if is_negative:
            if "fast_motion" in clip_name:
                neg_triggers = ["fast_motion"]
            elif "jump_scare" in clip_name:
                neg_triggers = ["jump_scare"]
            elif any(k in clip_name for k in ["flash", "fade", "gradient", "static", "pattern"]):
                neg_triggers = ["flashing"]
            elif "audio" in clip_name:
                neg_triggers = ["loud_spike", "gunshot", "explosion", "scream", "sustained_loudness"]
            elif clip_name == "sync_test.mp4":
                neg_triggers = ["gunshot", "explosion", "scream", "fast_motion", "jump_scare", "spider"]

        events = []
        if not is_negative:
            # Look up in labels.csv first, then fallback to clip definition
            label_rows = syn_labels_by_clip.get(clip_name, [])
            if label_rows:
                for idx, row in enumerate(label_rows):
                    start_str = row.get("start", "").strip()
                    end_str = row.get("end", "").strip()
                    if start_str and end_str:
                        start_f = float(start_str)
                        end_f = float(end_str)
                        events.append({
                            "event_id": f"evt_syn_{clip_name.replace('.', '_')}_{idx:02d}",
                            "trigger": row["trigger"],
                            "start": round(start_f, 3),
                            "end": round(end_f, 3),
                            "boundary_uncertainty_seconds": 0.0,
                            "annotator_id": row.get("labeller") or "synthetic_generator",
                            "review_status": "verified",
                            "ambiguity_notes": f"Programmatically synthesized {row['trigger']} ground truth.",
                        })
            elif syn_clip.get("trigger") and syn_clip.get("start") is not None and syn_clip.get("end") is not None:
                events.append({
                    "event_id": f"evt_syn_{clip_name.replace('.', '_')}_00",
                    "trigger": syn_clip["trigger"],
                    "start": round(float(syn_clip["start"]), 3),
                    "end": round(float(syn_clip["end"]), 3),
                    "boundary_uncertainty_seconds": 0.0,
                    "annotator_id": syn_clip.get("labeller", "synthetic_generator"),
                    "review_status": "verified",
                    "ambiguity_notes": f"Synthesized fixture {syn_clip['trigger']} ground truth.",
                })

        clip_record = {
            "clip_id": clip_name,
            "film_id": "synthetic_fixtures",
            "media_relpath": f"data/test_clips/{clip_name}",
            "modality": modality,
            "duration_seconds": duration,
            "fps": fps,
            "width": width,
            "height": height,
            "dataset_type": "synthetic",
            "split": "train_dev",
            "scene_id": f"scene_syn_{clip_name.replace('.', '_')}",
            "is_negative_control": is_negative,
            "license": "Synthetic Test Generator (Internal MIT-equivalent fixture)",
            "events": events,
        }
        if neg_triggers:
            clip_record["negative_triggers"] = neg_triggers
        clips.append(clip_record)

    # 2. Process Natural Validation Audio Clips -> split: validation
    with NATURAL_VALIDATION_MANIFEST.open("r", encoding="utf-8") as f:
        nat_manifest = json.load(f)

    for nat_clip in nat_manifest["clips"]:
        clip_name = nat_clip["clip_name"]
        duration = float(nat_clip["duration"])
        fps = float(nat_clip.get("fps", 0.0))
        meta = nat_clip.get("metadata", {})
        trigger = nat_clip["trigger"]
        start_f = float(nat_clip["start"])
        end_f = float(nat_clip["end"])

        clip_id = clip_name
        rel_media_path = f"data/natural_validation/audio/{clip_name}"
        scene_id = f"scene_nat_{clip_name.replace('.', '_')}"

        clips.append({
            "clip_id": clip_id,
            "film_id": f"freesound_{clip_name.split('.')[0]}",
            "media_relpath": rel_media_path,
            "modality": "audio",
            "duration_seconds": duration,
            "fps": fps,
            "width": 0,
            "height": 0,
            "dataset_type": "natural",
            "split": "validation",
            "scene_id": scene_id,
            "is_negative_control": False,
            "license": meta.get("source", "Creative Commons Attribution"),
            "events": [
                {
                    "event_id": f"evt_nat_{clip_name.replace('.', '_')}_01",
                    "trigger": trigger,
                    "start": round(start_f, 3),
                    "end": round(end_f, 3),
                    "boundary_uncertainty_seconds": 0.05,
                    "annotator_id": nat_clip.get("labeller", "independent_acoustic_annotation"),
                    "review_status": "verified",
                    "ambiguity_notes": f"Verified natural audio event: {trigger}.",
                }
            ],
        })

    # 3. Natural Film Candidate Scenes (Sintel Trailer) -> split: validation
    clips.append({
        "clip_id": "sintel_trailer_action_scene",
        "film_id": "sintel_trailer",
        "media_relpath": "data/media/sintel_trailer.mp4",
        "modality": "audiovisual",
        "duration_seconds": 52.208,
        "fps": 24.0,
        "width": 1280,
        "height": 720,
        "dataset_type": "natural",
        "split": "validation",
        "scene_id": "scene_sintel_trailer_combat",
        "is_negative_control": False,
        "license": "Creative Commons Attribution 3.0 Blender Foundation (durian.blender.org)",
        "events": [
            {
                "event_id": "evt_cand_sintel_combat_01",
                "trigger": "fast_motion",
                "start": 20.000,
                "end": 25.000,
                "boundary_uncertainty_seconds": 0.25,
                "annotator_id": "candidate_pipeline_detection",
                "review_status": "pending_review",
                "ambiguity_notes": "High-velocity combat montage; awaiting human review to establish gold boundary.",
            }
        ],
    })

    clips.append({
        "clip_id": "sintel_trailer_calm_intro",
        "film_id": "sintel_trailer",
        "media_relpath": "data/media/sintel_trailer.mp4",
        "modality": "audiovisual",
        "duration_seconds": 52.208,
        "fps": 24.0,
        "width": 1280,
        "height": 720,
        "dataset_type": "natural",
        "split": "validation",
        "scene_id": "scene_sintel_trailer_opening",
        "is_negative_control": True,
        "negative_triggers": ["fast_motion", "flashing", "explosion", "gunshot", "scream", "loud_spike", "jump_scare", "spider"],
        "license": "Creative Commons Attribution 3.0 Blender Foundation (durian.blender.org)",
        "events": [],
    })

    # 4. Natural Film Candidate Scenes (Elephants Dream) -> split: locked_test
    clips.append({
        "clip_id": "elephants_dream_flashing_room",
        "film_id": "elephants_dream",
        "media_relpath": "data/media/elephants_dream.mp4",
        "modality": "audiovisual",
        "duration_seconds": 653.827,
        "fps": 24.0,
        "width": 1920,
        "height": 1080,
        "dataset_type": "natural",
        "split": "locked_test",
        "scene_id": "scene_ed_flashing_strobe",
        "is_negative_control": False,
        "license": "Creative Commons Attribution 2.5 Blender Foundation (orange.blender.org)",
        "events": [
            {
                "event_id": "evt_cand_ed_flash_01",
                "trigger": "flashing",
                "start": 27.292,
                "end": 32.917,
                "boundary_uncertainty_seconds": 0.10,
                "annotator_id": "candidate_pipeline_detection",
                "review_status": "pending_review",
                "ambiguity_notes": "Machine room electrical strobe sequence; pending photosensitivity panel review.",
            }
        ],
    })

    clips.append({
        "clip_id": "elephants_dream_cable_burst",
        "film_id": "elephants_dream",
        "media_relpath": "data/media/elephants_dream.mp4",
        "modality": "audiovisual",
        "duration_seconds": 653.827,
        "fps": 24.0,
        "width": 1920,
        "height": 1080,
        "dataset_type": "natural",
        "split": "locked_test",
        "scene_id": "scene_ed_cable_explosion",
        "is_negative_control": False,
        "license": "Creative Commons Attribution 2.5 Blender Foundation (orange.blender.org)",
        "events": [
            {
                "event_id": "evt_cand_ed_exp_01",
                "trigger": "explosion",
                "start": 307.680,
                "end": 308.640,
                "boundary_uncertainty_seconds": 0.20,
                "annotator_id": "candidate_pipeline_detection",
                "review_status": "pending_review",
                "ambiguity_notes": "Acoustic cable rupture transient; pending human consensus review.",
            }
        ],
    })

    clips.append({
        "clip_id": "elephants_dream_quiet_walk",
        "film_id": "elephants_dream",
        "media_relpath": "data/media/elephants_dream.mp4",
        "modality": "audiovisual",
        "duration_seconds": 653.827,
        "fps": 24.0,
        "width": 1920,
        "height": 1080,
        "dataset_type": "natural",
        "split": "locked_test",
        "scene_id": "scene_ed_corridor_dialogue",
        "is_negative_control": True,
        "negative_triggers": ["fast_motion", "flashing", "explosion", "gunshot", "scream", "loud_spike", "jump_scare", "spider"],
        "license": "Creative Commons Attribution 2.5 Blender Foundation (orange.blender.org)",
        "events": [],
    })

    benchmark_manifest = {
        "schema_version": 1,
        "benchmark_id": "trigger_aware_player_benchmark_v1",
        "version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": (
            "Reproducible ground-truth benchmark foundation for Trigger-Aware Player detectors. "
            "Enforces strict separation of synthetic fixtures (train_dev), natural validation media, "
            "and locked test film scenes (Elephants Dream) with anti-leakage scene grouping. "
            "Pending-review annotations are strictly excluded from verified scoring."
        ),
        "splits": {
            "train_dev": {
                "description": "Synthetic unit fixtures with known physical parameters for detector development and regression testing.",
                "usage": "Internal development, unit testing, and parameter tuning.",
            },
            "validation": {
                "description": "Verified natural audio clips (Freesound/OpenGameArt) and candidate natural film scenes (pending review; not verified ground truth).",
                "usage": "Hyperparameter tuning and cross-validation prior to final test.",
            },
            "locked_test": {
                "description": "Held-out candidate natural film scenes from Elephants Dream (pending review; zero verified ground-truth events).",
                "usage": "Locked evaluation only once human verification is completed; currently excluded from verified ground truth.",
            },
        },
        "clips": clips,
    }

    with BENCHMARK_MANIFEST.open("w", encoding="utf-8") as f:
        json.dump(benchmark_manifest, f, indent=2)

    print(f"Created benchmark manifest with {len(clips)} clips at {BENCHMARK_MANIFEST}")


if __name__ == "__main__":
    build_benchmark_manifest()
