# Detector Evaluation Report — Trigger-Aware Player (PHONY_APP)

**Generated:** 2026-10-19T06:48:02.670972+00:00  
**Evaluator Role:** Person B — Media Ingestion, Packaging, and Pipeline Integration  
**Matching Policy:** One-to-one temporal matching with $\text{tIoU} \ge 0.5$  

---

## 1. Executive Summary

This report benchmarks the offline detectors implemented in the repository against the verified ground-truth test dataset (`data/test_clips/labels.csv` and `manifest.json`).

### Key Results Overview

| Metric Category | Precision | Recall | F1 Score | Notes |
| :--- | :---: | :---: | :---: | :--- |
| **Dataset Micro-Average** | *null* | 0.0% | *null* | Pooled TP=0, FP=0, FN=2 across all evaluated triggers |
| **Dataset Macro-Average** | *null* | 0.0% | *null* | Unweighted mean across evaluated triggers |

---

## 2. Methodology & Mathematical Definitions

### One-to-One Deterministic Matching
1. **Temporal IoU:** For prediction $[p_{start}, p_{end})$ and ground-truth $[g_{start}, g_{end})$, $\text{tIoU} = \frac{\text{Intersection}}{\text{Union}}$. A valid candidate match requires same trigger and $\text{tIoU} \ge 0.5$.
2. **Bipartite Constraint:** A prediction matches at most one ground-truth event. A ground-truth event matches at most one prediction.
3. **Deterministic Tie-Breaking:** Candidate pairs are ranked by: highest tIoU descending $\to$ prediction start ascending $\to$ ground truth start ascending. Greedy selection is applied.

### Undefined Metric Representation
Where a metric has a zero denominator (e.g., negative controls with $TP + FP = 0$ or $TP + FN = 0$), the metric is represented as **`null`** rather than a misleading `0.0` or `1.0`.

### Averaging Strategy
- **Micro-average:** Sums counts ($TP, FP, FN$) across clips/events, then evaluates $\frac{\sum TP}{\sum TP + \sum FP}$ and $\frac{\sum TP}{\sum TP + \sum FN}$. Correctly reflects aggregate performance.
- **Macro-average:** Computes metric per clip (or per trigger) and takes the arithmetic mean across defined values.

---

## 3. Per-Trigger Benchmark Results

| Trigger | Component | Modality | Clips | GT Events | Preds | TP | FP | FN | Micro Precision | Micro Recall | Micro F1 | Mean IoU | Mean \|Onset Err\| |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `explosion` | audio_event_classifier | audio | 1 | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* | *null*s |
| `scream` | audio_event_classifier | audio | 1 | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* | *null*s |

---

## 4. Per-Clip Detailed Results

| Clip Name | Trigger | Role / Type | GT Count | Pred Count | TP | FP | FN | Precision | Recall | F1 | Mean IoU |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `explosion_freesound_156031.wav` | `explosion` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `scream_dan_knoflicek_3.wav` | `scream` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |

### Individual Event Matches & Boundary Exactness

| Clip | Trigger | Match Type | Pred Interval | GT Interval | tIoU | Onset Error | Offset Error |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `explosion_freesound_156031.wav` | `explosion` | Unmatched FN | — | [0.050, 3.200) | 0.0000 | — | — |
| `scream_dan_knoflicek_3.wav` | `scream` | Unmatched FN | — | [0.000, 1.650) | 0.0000 | — | — |
| — | — | No matches | — | — | — | — | — |

---

## 5. Annotation Integrity & Unsupported Detectors

### Unsupported Detectors Registry

| Trigger | Component | Owner | Status | GT Labels in labels.csv | Reason / Blocking Dependency |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `loud_spike` | loudness_detector | Person B | **AVAILABLE** | 0 | Detector available but excluded from target evaluation triggers. |
| `profanity` | profanity_detector | Person C | **AVAILABLE** | 0 | Detector available but excluded from target evaluation triggers. |
| `flashing` | flash_detector | Person A | **AVAILABLE** | 0 | Detector available but excluded from target evaluation triggers. |
| `gunshot` | audio_event_classifier | Person A | **AVAILABLE** | 0 | Detector available but excluded from target evaluation triggers. |
| `spider` | spider_detector | Person A | **MISSING_DETECTOR** | 0 | Detector implementation not available in repository. |
| `fast_motion` | motion_detector | Person C | **MISSING_DETECTOR** | 0 | Detector implementation not available in repository. |
| `jump_scare` | compound_detector | Person B and Person C | **MISSING_DETECTOR** | 0 | Detector implementation not available in repository. |

### Annotation Integrity Rules Enforced
1. **No False Negativity Assumption:** Clips not labeled for a trigger were NOT assumed to be negative controls unless explicitly specified as such in `manifest.json`.
2. **No Fabricated Labels or Scores:** Metrics are only reported for detectors that actually executed.
3. **Preservation of Truth:** `data/test_clips/labels.csv` was preserved read-only without modifications.

---

## 6. Reproducibility

To regenerate this report and the corresponding machine-readable JSON artifact, run:

```powershell
python scripts/evaluate_detections.py --output-json reports/detection_evaluation_report.json --output-md reports/detection_evaluation_report.md
```

To execute the full evaluation harness test suite:
```powershell
pytest tests/test_evaluate_detections.py -v
```
