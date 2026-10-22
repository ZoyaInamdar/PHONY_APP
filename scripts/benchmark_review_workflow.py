#!/usr/bin/env python3
"""Human Review Workflow for Benchmark Candidate Annotations.

Provides tools to:
1. Export unreviewed / pending natural film candidate events to a structured review sheet (CSV).
2. Validate and import completed human review decisions back into benchmark_manifest.json
   without corrupting existing verified ground-truth or unrelated annotations.
3. Strictly prevent unreviewed candidates from automatically being promoted to verified status.

Usage:
    # Export pending candidates for review:
    python3 scripts/benchmark_review_workflow.py export --output data/benchmark/review_sheet.csv

    # Import completed review sheet:
    python3 scripts/benchmark_review_workflow.py import --input data/benchmark/completed_reviews.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_PATH = REPO_ROOT / "data" / "benchmark" / "manifest.json"
DEFAULT_REVIEW_SHEET = REPO_ROOT / "data" / "benchmark" / "pending_review_sheet.csv"

REVIEW_COLUMNS = [
    "clip_id",
    "film_id",
    "scene_id",
    "event_id",
    "trigger",
    "candidate_start",
    "candidate_end",
    "candidate_uncertainty",
    "candidate_notes",
    "review_decision",  # VERIFIED_PRESENT, VERIFIED_ABSENT, REJECTED, AMBIGUOUS
    "reviewed_start",
    "reviewed_end",
    "reviewed_uncertainty",
    "reviewer_id",
    "review_notes",
]

VALID_DECISIONS = {"VERIFIED_PRESENT", "VERIFIED_ABSENT", "REJECTED", "AMBIGUOUS", ""}


def export_review_sheet(
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    output_csv_path: Path | str = DEFAULT_REVIEW_SHEET,
    split_filter: Optional[str] = None,
    status_filter: str = "pending_review",
) -> int:
    """Export pending candidate annotations to a human review CSV sheet."""
    m_path = Path(manifest_path)
    if not m_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {m_path.resolve()}")

    with m_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    out_path = Path(output_csv_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []

    for clip in data.get("clips", []):
        if split_filter and clip.get("split") != split_filter:
            continue

        clip_id = clip["clip_id"]
        film_id = clip.get("film_id", "")
        scene_id = clip.get("scene_id", "")

        for evt in clip.get("events", []):
            if evt.get("review_status") == status_filter:
                rows.append({
                    "clip_id": clip_id,
                    "film_id": film_id,
                    "scene_id": scene_id,
                    "event_id": evt["event_id"],
                    "trigger": evt["trigger"],
                    "candidate_start": f"{evt['start']:.3f}",
                    "candidate_end": f"{evt['end']:.3f}",
                    "candidate_uncertainty": f"{evt.get('boundary_uncertainty_seconds', 0.1):.3f}",
                    "candidate_notes": evt.get("ambiguity_notes", ""),
                    "review_decision": "",
                    "reviewed_start": "",
                    "reviewed_end": "",
                    "reviewed_uncertainty": "",
                    "reviewer_id": "",
                    "review_notes": "",
                })

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Exported {len(rows)} pending candidates to {out_path}")
    return len(rows)


def import_review_sheet(
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    review_csv_path: Path | str = DEFAULT_REVIEW_SHEET,
    output_manifest_path: Optional[Path | str] = None,
) -> Tuple[int, int, int]:
    """Import reviewed annotations into the benchmark manifest.

    Returns:
        (verified_count, rejected_count, pending_count)
    """
    m_path = Path(manifest_path)
    if not m_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {m_path.resolve()}")

    r_path = Path(review_csv_path)
    if not r_path.is_file():
        raise FileNotFoundError(f"Review sheet not found: {r_path.resolve()}")

    with m_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Read review decisions indexed by event_id
    decisions: Dict[str, Dict[str, str]] = {}
    with r_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            evt_id = row.get("event_id", "").strip()
            if evt_id:
                decisions[evt_id] = row

    verified_count = 0
    rejected_count = 0
    pending_count = 0

    for clip in manifest.get("clips", []):
        updated_events = []
        for evt in clip.get("events", []):
            evt_id = evt["event_id"]
            if evt_id in decisions:
                review = decisions[evt_id]
                dec = review.get("review_decision", "").strip().upper()

                if dec not in VALID_DECISIONS:
                    raise ValueError(f"Invalid review decision '{dec}' for event {evt_id}. Must be one of: {VALID_DECISIONS}")

                if dec == "VERIFIED_PRESENT":
                    reviewer = review.get("reviewer_id", "").strip()
                    if not reviewer:
                        raise ValueError(f"Review decision VERIFIED_PRESENT for event {evt_id} requires reviewer_id.")

                    start_str = review.get("reviewed_start", "").strip()
                    end_str = review.get("reviewed_end", "").strip()
                    unc_str = review.get("reviewed_uncertainty", "").strip()

                    start_val = float(start_str) if start_str else float(evt["start"])
                    end_val = float(end_str) if end_str else float(evt["end"])
                    unc_val = float(unc_str) if unc_str else float(evt.get("boundary_uncertainty_seconds", 0.05))

                    if end_val <= start_val:
                        raise ValueError(f"Invalid timestamp interval [{start_val}, {end_val}] for {evt_id}")

                    evt["start"] = round(start_val, 3)
                    evt["end"] = round(end_val, 3)
                    evt["boundary_uncertainty_seconds"] = round(unc_val, 3)
                    evt["review_status"] = "verified"
                    evt["annotator_id"] = reviewer
                    evt["ambiguity_notes"] = review.get("review_notes", "").strip() or f"Verified by {reviewer}"
                    verified_count += 1
                    updated_events.append(evt)

                elif dec == "VERIFIED_ABSENT":
                    # Mark trigger as verified absent in this clip
                    reviewer = review.get("reviewer_id", "").strip() or "reviewer"
                    evt["review_status"] = "rejected"
                    evt["ambiguity_notes"] = f"Trigger verified absent by {reviewer}: {review.get('review_notes', '')}"
                    rejected_count += 1
                    updated_events.append(evt)

                elif dec == "REJECTED":
                    evt["review_status"] = "rejected"
                    evt["ambiguity_notes"] = f"Rejected candidate annotation: {review.get('review_notes', '')}"
                    rejected_count += 1
                    updated_events.append(evt)

                else:
                    # Unreviewed, AMBIGUOUS, or blank -> stays pending_review
                    pending_count += 1
                    updated_events.append(evt)
            else:
                updated_events.append(evt)

        clip["events"] = updated_events

    out_path = Path(output_manifest_path) if output_manifest_path else m_path
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Imported reviews: {verified_count} verified, {rejected_count} rejected, {pending_count} pending -> {out_path}")
    return verified_count, rejected_count, pending_count


def main():
    parser = argparse.ArgumentParser(description="Benchmark candidate human review workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    exp_parser = subparsers.add_parser("export", help="Export pending candidates to CSV review sheet.")
    exp_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Path to manifest.json")
    exp_parser.add_argument("--output", default=str(DEFAULT_REVIEW_SHEET), help="Output review sheet CSV")
    exp_parser.add_argument("--split", default=None, help="Filter by split (validation, locked_test)")

    imp_parser = subparsers.add_parser("import", help="Import reviewed decisions into manifest.")
    imp_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH), help="Path to input manifest.json")
    imp_parser.add_argument("--input", required=True, help="Path to completed review sheet CSV")
    imp_parser.add_argument("--output", default=None, help="Output manifest.json (defaults to input manifest)")

    args = parser.parse_args()
    if args.command == "export":
        export_review_sheet(args.manifest, args.output, args.split)
    elif args.command == "import":
        import_review_sheet(args.manifest, args.input, args.output)


if __name__ == "__main__":
    main()
