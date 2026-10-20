# Multi-Detector Same-Timeline Evaluation Report — Trigger-Aware Player

**Generated:** 2026-10-20T07:30:32.385484+00:00  
**Evaluator Role:** Person B — Media Ingestion, Packaging, and Pipeline Integration  
**Film ID:** `multi_trigger_test` (15.000 seconds)  
**Matching Policy:** One-to-one temporal matching with $\text{tIoU} \ge 0.5$  

---

## 1. Executive Summary

This report evaluates the multi-detector cue packaging integration for `multi_trigger_test` on a single, unified presentation timeline. Both the operational loudness detector (`loud_spike`) and the operational subtitle/profanity detector (`profanity`) were executed against the same media asset, packaged into a single canonical `index.json`, and scored against ground-truth labels established for that exact timeline.

### Key Results Overview

| Metric Category | Precision | Recall | F1 Score | Notes |
| :--- | :---: | :---: | :---: | :--- |
| **Overall Micro Average** | **100.0%** | **100.0%** | **100.0%** | Aggregated across 4 predictions and 4 ground-truth events |
| **Overall Macro Average** | 100.0% | 100.0% | 100.0% | Unweighted mean across 2 active triggers |

### Aggregate Temporal Accuracy

- **Mean Temporal IoU (tIoU):** 1.0000
- **Mean Onset Error:** 0.0000s (Mean Absolute: 0.0000s)
- **Mean Offset Error:** 0.0000s (Mean Absolute: 0.0000s)

---

## 2. Per-Trigger Benchmark Results

| Trigger | Component | Modality | GT Events | Pred Events | TP | FP | FN | Precision | Recall | F1 | Mean tIoU | Mean Abs Onset Err |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `loud_spike` | `loudness_detector` | audio | 2 | 2 | 2 | 0 | 0 | 100.0% | 100.0% | 100.0% | 1.0000 | 0.0000s |
| `profanity` | `profanity_detector` | subtitles | 2 | 2 | 2 | 0 | 0 | 100.0% | 100.0% | 100.0% | 1.0000 | 0.0000s |

---

## 3. Detailed Event Matches & Boundary Exactness

| Trigger | Match Status | Prediction Interval | Ground-Truth Interval | tIoU | Onset Error | Offset Error |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `loud_spike` | **Matched TP** | [5.000, 5.400) | [5.000, 5.400) | 1.0000 | +0.000s | +0.000s |
| `loud_spike` | **Matched TP** | [11.000, 11.300) | [11.000, 11.300) | 1.0000 | +0.000s | +0.000s |
| `profanity` | **Matched TP** | [1.000, 3.500) | [1.000, 3.500) | 1.0000 | +0.000s | +0.000s |
| `profanity` | **Matched TP** | [7.000, 9.500) | [7.000, 9.500) | 1.0000 | +0.000s | +0.000s |

---

## 4. Pipeline & Contract Compliance

1. **Timeline Consistency:** All packaged cues and ground-truth intervals fall strictly within `[0.0, 15.000]` seconds.
2. **Film ID Preservation:** Index and detections both identify film asset as `multi_trigger_test`.
3. **Canonical Trigger Identifiers:** Used canonical `profanity` and `loud_spike` matching `config.yaml`.
4. **Canonical Detector Provenance:** Packaging preserved `source: profanity_detector` and `source: loudness_detector`.
5. **Index Schema Conformance:** Validated with `scripts/validate_index.py` against `schema/index.schema.json`.

---

## 5. Reproducibility

Commands executed to reproduce this multi-detector evaluation:

```powershell
# 1. Generate media and ground-truth labels
python scripts/generate_test_clips.py --multi-trigger-only

# 2. Extract audio and run detectors
ffmpeg -y -i data/test_clips/multi_trigger_test.mp4 -vn -ac 1 -ar 44100 -c:a pcm_s16le data/processed/multi_trigger_eval/audio.wav
python scripts/detect_loudness.py --input data/processed/multi_trigger_eval/audio.wav --output data/processed/multi_trigger_eval/loudness_detections.json --film-id multi_trigger_test
python scripts/parse_subtitles.py data/test_clips/multi_trigger_test.srt --wordlist data/profanity_wordlist.txt --out-detections data/processed/multi_trigger_eval/profanity_detections.json --source profanity_detector

# 3. Package and validate index
python scripts/package_cues.py --input data/processed/multi_trigger_eval/loudness_detections.json data/processed/multi_trigger_eval/profanity_detections.json --output data/processed/multi_trigger_eval/index.json --film-id multi_trigger_test --duration 15.0 --fps 24.0
python scripts/validate_index.py --index data/processed/multi_trigger_eval/index.json

# 4. Score packaged index against ground-truth labels
python scripts/evaluate_detections.py --index data/processed/multi_trigger_eval/index.json --labels data/test_clips/labels.csv --film-id multi_trigger_test --output-json reports/multi_detector_evaluation_report.json --output-md reports/multi_detector_evaluation_report.md
```

