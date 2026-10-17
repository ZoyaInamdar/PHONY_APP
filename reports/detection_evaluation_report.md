# Detector Evaluation Report — Trigger-Aware Player (PHONY_APP)

**Generated:** 2026-10-16T17:17:13.031163+00:00  
**Evaluator Role:** Person B — Media Ingestion, Packaging, and Pipeline Integration  
**Matching Policy:** One-to-one temporal matching with $\text{tIoU} \ge 0.5$  

---

## 1. Executive Summary

This report benchmarks the offline detectors implemented in the repository against the verified ground-truth test dataset (`data/test_clips/labels.csv` and `manifest.json`).

### Key Results Overview

| Metric Category | Precision | Recall | F1 Score | Notes |
| :--- | :---: | :---: | :---: | :--- |
| **Dataset Micro-Average** | 50.0% | 40.0% | 44.4% | Pooled TP=2, FP=2, FN=3 across all evaluated triggers |
| **Dataset Macro-Average** | 50.0% | 41.7% | 45.0% | Unweighted mean across evaluated triggers |

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
| `loud_spike` | loudness_detector | audio | 7 | 2 | 2 | 1 | 1 | 1 | 50.0% | 50.0% | 50.0% | 0.6667 | 0.0000s |
| `profanity` | profanity_detector | subtitles | 6 | 3 | 2 | 1 | 1 | 2 | 50.0% | 33.3% | 40.0% | 1.0000 | 0.0000s |

---

## 4. Per-Clip Detailed Results

| Clip Name | Trigger | Role / Type | GT Count | Pred Count | TP | FP | FN | Precision | Recall | F1 | Mean IoU |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `audio_loud_spike_isolated.mp4` | `loud_spike` | test_clip | 1 | 1 | 1 | 0 | 0 | 100.0% | 100.0% | 100.0% | 0.6667 |
| `audio_loud_spike_test.mp4` | `loud_spike` | test_clip | 1 | 1 | 0 | 1 | 1 | 0.0% | 0.0% | *null* | *null* |
| `audio_negative_ambient.mp4` | `loud_spike` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `loud_spike` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `loud_spike` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `loud_spike` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `loud_spike` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `speech_triggers_test.mp4` | `profanity` | test_clip | 3 | 2 | 1 | 1 | 2 | 50.0% | 33.3% | 40.0% | 1.0000 |
| `audio_negative_ambient.mp4` | `profanity` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `profanity` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `profanity` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `profanity` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `profanity` | test_clip | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |

### Individual Event Matches & Boundary Exactness

| Clip | Trigger | Match Type | Pred Interval | GT Interval | tIoU | Onset Error | Offset Error |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `audio_loud_spike_isolated.mp4` | `loud_spike` | Matched TP | [1.500, 1.700) | [1.500, 1.800) | 0.6667 | +0.000s | -0.100s |
| `audio_loud_spike_test.mp4` | `loud_spike` | Unmatched FP | [2.000, 2.100) | — | 0.0000 | — | — |
| `audio_loud_spike_test.mp4` | `loud_spike` | Unmatched FN | — | [2.000, 2.500) | 0.0000 | — | — |
| `speech_triggers_test.mp4` | `profanity` | Matched TP | [1.000, 3.500) | [1.000, 3.500) | 1.0000 | +0.000s | +0.000s |
| `speech_triggers_test.mp4` | `profanity` | Unmatched FP | [7.000, 12.500) | — | 0.0000 | — | — |
| `speech_triggers_test.mp4` | `profanity` | Unmatched FN | — | [7.000, 9.500) | 0.0000 | — | — |
| `speech_triggers_test.mp4` | `profanity` | Unmatched FN | — | [10.500, 12.500) | 0.0000 | — | — |

---

## 5. Annotation Integrity & Unsupported Detectors

### Unsupported Detectors Registry

| Trigger | Component | Owner | Status | GT Labels in labels.csv | Reason / Blocking Dependency |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `flashing` | flash_detector | Person A | **MISSING_DETECTOR** | 16 | Detector implementation not available in repository. |
| `gunshot` | audio_event_classifier | Person A | **MISSING_DETECTOR** | 2 | Detector implementation not available in repository. |
| `explosion` | audio_event_classifier | Person A | **MISSING_DETECTOR** | 2 | Detector implementation not available in repository. |
| `scream` | audio_event_classifier | Person A | **MISSING_DETECTOR** | 2 | Detector implementation not available in repository. |
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
