# Trigger-Aware Player — Detector Benchmark & Evaluation Results

**Run Date:** 2026-10-19 (2026-10-19T18:29:33.180545+00:00)  
**Benchmark Type:** synthetic benchmark  
**Harness Runtime:** 0.0s (Skipped (report is up to date; use --force to rerun))  

---

## 1. Specification & Compliance Disclaimers

> **Flash Detector Specification:** detects rapid luminance flashing over screen regions, approximating the WCAG general-flash definition. Not a compliance check.
> 
> **Score Semantics:** Detector scores represent relative confidence or signal energy; detector scores are not probabilities.

---

## 2. Per-Trigger Detection Performance (synthetic benchmark)

All metrics below are computed against generated benchmark clips with temporal IoU $\ge 0.5$ one-to-one matching.

| Trigger | Status & Tier | Config Thresh | True Segs | Prec | Recall | F1 | Signed Start Err | Signed End Err | FP / Min |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `flashing` | high confidence (F1 >= 0.70) | 0.10 | 16 | 100.0% | 81.2% | 89.7% | +0.000s | -0.010s | 0.00 |
| `loud_spike` | experimental (calibrating) | 12.00 | 2 | 0.0% | 0.0% | null | null | null | 3.33 |
| `gunshot` | experimental (config.yaml) | 0.65 | 2 | 100.0% | 50.0% | 66.7% | -0.060s | +0.400s | 0.00 |
| `explosion` | experimental (config.yaml) | 0.70 | 2 | null | 0.0% | null | null | null | 0.00 |
| `scream` | experimental (config.yaml) | 0.60 | 2 | null | 0.0% | null | null | null | 0.00 |
| `profanity` | experimental (F1 < 0.70) | 1.00 | 3 | 50.0% | 33.3% | 40.0% | +0.000s | +0.000s | 1.43 |
| `spider` | dropped (unimplemented) | 0.70 | 0 | null | null | null | null | null | null |
| `fast_motion` | experimental (config.yaml) | 0.20 | 1 | null | 0.0% | null | null | null | 0.00 |
| `jump_scare` | experimental (config.yaml) | 0.75 | 1 | null | 0.0% | null | null | null | 0.00 |

**Dataset Micro Metrics (synthetic benchmark):** Precision: **83.3%** | Recall: **51.7%** | F1: **63.8%**  
**Total Clips Evaluated:** 67  

---

## 3. Real-Film Validation Status

- **Status:** `no_hand_labelled_full_film_clips`
- **Finding:** Ground truth in data/test_clips/labels.csv consists exclusively of synthetic generated clips (labeller: synthetic_generator). data/natural_validation/labels.csv contains 2 external natural audio clips (explosion and scream from Freesound/OpenGameArt), but no hand-labelled ground truth intervals currently exist for full demo films (Sintel or Tears of Steel).

---

## 4. Execution Environment & Dependencies

- **Python Version:** `3.14.4`
- **FFmpeg Version:** `9.0.2`

### Key Library Versions

| Package | Installed Version |
| :--- | :--- |
| `numpy` | `2.5.4` |
| `scipy` | `1.18.1` |
| `librosa` | `0.11.0` |
| `soundfile` | `0.14.0` |
| `cv2` | `4.14.0.94` |
| `pysrt` | `1.1.2` |
| `pandas` | `3.0.6` |
| `pytest` | `8.4.2` |
| `yaml` | `6.0.3` |
| `jsonschema` | `4.26.0` |

### Acoustic Classifier Model

- **Model ID:** `yamnet` (YamNet)
- **Runtime / Precision:** `onnx` / `float`
- **ONNX Runtime Target:** `1.30.0`
- **Metadata Path:** [`models/yamnet/yamnet-onnx-float/metadata.json`](models/yamnet/yamnet-onnx-float/metadata.json)

---

## 5. Methodological Notes

- Detector scores are detector-specific metrics and are not calibrated probabilities.
- Flash detector approximates WCAG general-flash rule; it is not a medical safeguard or compliance check.
- All clips in data/test_clips/ are synthetic generator outputs ('synthetic benchmark').
- docs/evaluation-rules.md is absent from the repository; confidence tiers are determined from config.yaml status and empirical F1 scores.
