# Detector Evaluation Report — Trigger-Aware Player (PHONY_APP)

**Generated:** 2026-10-20T07:30:40.303583+00:00  
**Evaluator Role:** Person B — Media Ingestion, Packaging, and Pipeline Integration  
**Matching Policy:** One-to-one temporal matching with $\text{tIoU} \ge 0.5$  

---

## 1. Executive Summary

This report benchmarks the offline detectors implemented in the repository against the verified ground-truth test dataset (`data/test_clips/labels.csv` and `manifest.json`).

### Key Results Overview

| Metric Category | Precision | Recall | F1 Score | Notes |
| :--- | :---: | :---: | :---: | :--- |
| **Dataset Micro-Average** | 25.0% | 3.5% | 6.1% | Pooled TP=1, FP=3, FN=28 across all evaluated triggers |
| **Dataset Macro-Average** | 25.0% | 4.2% | 40.0% | Unweighted mean across evaluated triggers |

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
| `explosion` | audio_event_classifier | audio | 9 | 2 | 0 | 0 | 0 | 2 | *null* | 0.0% | *null* | *null* | *null*s |
| `fast_motion` | motion_detector | visual | 8 | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* | *null*s |
| `flashing` | flash_detector | visual | 23 | 16 | 0 | 0 | 0 | 16 | *null* | 0.0% | *null* | *null* | *null*s |
| `gunshot` | audio_event_classifier | audio | 9 | 2 | 0 | 0 | 0 | 2 | *null* | 0.0% | *null* | *null* | *null*s |
| `jump_scare` | compound_detector | multimodal | 8 | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* | *null*s |
| `loud_spike` | loudness_detector | audio | 9 | 2 | 2 | 0 | 2 | 2 | 0.0% | 0.0% | *null* | *null* | *null*s |
| `profanity` | profanity_detector | subtitles | 8 | 3 | 2 | 1 | 1 | 2 | 50.0% | 33.3% | 40.0% | 1.0000 | 0.0000s |
| `scream` | audio_event_classifier | audio | 9 | 2 | 0 | 0 | 0 | 2 | *null* | 0.0% | *null* | *null* | *null*s |

---

## 4. Per-Clip Detailed Results

| Clip Name | Trigger | Role / Type | GT Count | Pred Count | TP | FP | FN | Precision | Recall | F1 | Mean IoU |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `audio_explosion_test.mp4` | `explosion` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `audio_multi_event_mix.mp4` | `explosion` | Composite multi-event au | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `audio_negative_ambient.mp4` | `explosion` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `explosion` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `fast_motion_negative.mp4` | `explosion` | Gentle/slow-panning spat | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `jump_scare_negative.mp4` | `explosion` | Gradual fade from black  | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `explosion` | Slow continuous fade (0. | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `explosion` | Smooth spatial sinusoida | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `explosion` | Completely static 50% ne | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `fast_motion_positive.mp4` | `fast_motion` | High-velocity spatial tr | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `audio_negative_ambient.mp4` | `fast_motion` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `fast_motion` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `fast_motion_negative.mp4` | `fast_motion` | Gentle/slow-panning spat | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `jump_scare_negative.mp4` | `fast_motion` | Gradual fade from black  | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `fast_motion` | Slow continuous fade (0. | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `fast_motion` | Smooth spatial sinusoida | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `fast_motion` | Completely static 50% ne | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `flash_area_half_24fps_4hz_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_area_quarter_24fps_4hz_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_area_small_patch_24fps_4hz_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_contrast_005_24fps_4hz_full.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_contrast_010_24fps_4hz_full.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_contrast_020_24fps_4hz_full.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_edge_25fps_3hz_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_edge_30fps_2hz_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_edge_60fps_6hz_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_fps_25fps_4hz_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_fps_30fps_4hz_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_fps_60fps_4hz_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_rate_2hz_24fps_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_rate_3hz_24fps_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_rate_4hz_24fps_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `flash_rate_6hz_24fps_full_c050.mp4` | `flashing` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `audio_negative_ambient.mp4` | `flashing` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `flashing` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `fast_motion_negative.mp4` | `flashing` | Gentle/slow-panning spat | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `jump_scare_negative.mp4` | `flashing` | Gradual fade from black  | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `flashing` | Slow continuous fade (0. | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `flashing` | Smooth spatial sinusoida | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `flashing` | Completely static 50% ne | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_gunshot_test.mp4` | `gunshot` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `audio_multi_event_mix.mp4` | `gunshot` | Composite multi-event au | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `audio_negative_ambient.mp4` | `gunshot` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `gunshot` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `fast_motion_negative.mp4` | `gunshot` | Gentle/slow-panning spat | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `jump_scare_negative.mp4` | `gunshot` | Gradual fade from black  | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `gunshot` | Slow continuous fade (0. | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `gunshot` | Smooth spatial sinusoida | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `gunshot` | Completely static 50% ne | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `jump_scare_positive.mp4` | `jump_scare` | Synchronized 1-frame vis | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `audio_negative_ambient.mp4` | `jump_scare` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `jump_scare` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `fast_motion_negative.mp4` | `jump_scare` | Gentle/slow-panning spat | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `jump_scare_negative.mp4` | `jump_scare` | Gradual fade from black  | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `jump_scare` | Slow continuous fade (0. | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `jump_scare` | Smooth spatial sinusoida | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `jump_scare` | Completely static 50% ne | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_loud_spike_isolated.mp4` | `loud_spike` | Sharp isolated volume sp | 1 | 1 | 0 | 1 | 1 | 0.0% | 0.0% | *null* | *null* |
| `audio_loud_spike_test.mp4` | `loud_spike` | Quiet background drone w | 1 | 1 | 0 | 1 | 1 | 0.0% | 0.0% | *null* | *null* |
| `audio_negative_ambient.mp4` | `loud_spike` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `loud_spike` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `fast_motion_negative.mp4` | `loud_spike` | Gentle/slow-panning spat | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `jump_scare_negative.mp4` | `loud_spike` | Gradual fade from black  | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `loud_spike` | Slow continuous fade (0. | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `loud_spike` | Smooth spatial sinusoida | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `loud_spike` | Completely static 50% ne | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `speech_triggers_test.mp4` | `profanity` | Speech trigger clip with | 3 | 2 | 1 | 1 | 2 | 50.0% | 33.3% | 40.0% | 1.0000 |
| `audio_negative_ambient.mp4` | `profanity` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `profanity` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `fast_motion_negative.mp4` | `profanity` | Gentle/slow-panning spat | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `jump_scare_negative.mp4` | `profanity` | Gradual fade from black  | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `profanity` | Slow continuous fade (0. | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `profanity` | Smooth spatial sinusoida | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `profanity` | Completely static 50% ne | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_multi_event_mix.mp4` | `scream` | Composite multi-event au | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `audio_scream_test.mp4` | `scream` | test_clip | 1 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| `audio_negative_ambient.mp4` | `scream` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `audio_negative_dialogue.mp4` | `scream` | Negative baseline clip w | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `fast_motion_negative.mp4` | `scream` | Gentle/slow-panning spat | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `jump_scare_negative.mp4` | `scream` | Gradual fade from black  | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_slow_fade.mp4` | `scream` | Slow continuous fade (0. | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_smooth_gradient.mp4` | `scream` | Smooth spatial sinusoida | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |
| `video_negative_static.mp4` | `scream` | Completely static 50% ne | 0 | 0 | 0 | 0 | 0 | *null* | *null* | *null* | *null* |

### Individual Event Matches & Boundary Exactness

| Clip | Trigger | Match Type | Pred Interval | GT Interval | tIoU | Onset Error | Offset Error |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `audio_explosion_test.mp4` | `explosion` | Unmatched FN | — | [1.000, 3.200) | 0.0000 | — | — |
| `audio_multi_event_mix.mp4` | `explosion` | Unmatched FN | — | [6.000, 8.200) | 0.0000 | — | — |
| `fast_motion_positive.mp4` | `fast_motion` | Unmatched FN | — | [0.000, 3.000) | 0.0000 | — | — |
| `flash_area_half_24fps_4hz_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.000) | 0.0000 | — | — |
| `flash_area_quarter_24fps_4hz_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.000) | 0.0000 | — | — |
| `flash_area_small_patch_24fps_4hz_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.000) | 0.0000 | — | — |
| `flash_contrast_005_24fps_4hz_full.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.000) | 0.0000 | — | — |
| `flash_contrast_010_24fps_4hz_full.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.000) | 0.0000 | — | — |
| `flash_contrast_020_24fps_4hz_full.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.000) | 0.0000 | — | — |
| `flash_edge_25fps_3hz_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.560) | 0.0000 | — | — |
| `flash_edge_30fps_2hz_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 5.267) | 0.0000 | — | — |
| `flash_edge_60fps_6hz_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 2.333) | 0.0000 | — | — |
| `flash_fps_25fps_4hz_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 2.920) | 0.0000 | — | — |
| `flash_fps_30fps_4hz_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.133) | 0.0000 | — | — |
| `flash_fps_60fps_4hz_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.133) | 0.0000 | — | — |
| `flash_rate_2hz_24fps_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 5.000) | 0.0000 | — | — |
| `flash_rate_3hz_24fps_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.667) | 0.0000 | — | — |
| `flash_rate_4hz_24fps_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 3.000) | 0.0000 | — | — |
| `flash_rate_6hz_24fps_full_c050.mp4` | `flashing` | Unmatched FN | — | [1.000, 2.333) | 0.0000 | — | — |
| `audio_gunshot_test.mp4` | `gunshot` | Unmatched FN | — | [1.500, 2.000) | 0.0000 | — | — |
| `audio_multi_event_mix.mp4` | `gunshot` | Unmatched FN | — | [1.000, 1.500) | 0.0000 | — | — |
| `jump_scare_positive.mp4` | `jump_scare` | Unmatched FN | — | [1.500, 1.600) | 0.0000 | — | — |
| `audio_loud_spike_isolated.mp4` | `loud_spike` | Unmatched FP | [1.500, 1.600) | — | 0.0000 | — | — |
| `audio_loud_spike_isolated.mp4` | `loud_spike` | Unmatched FN | — | [1.500, 1.800) | 0.0000 | — | — |
| `audio_loud_spike_test.mp4` | `loud_spike` | Unmatched FP | [2.000, 2.100) | — | 0.0000 | — | — |
| `audio_loud_spike_test.mp4` | `loud_spike` | Unmatched FN | — | [2.000, 2.500) | 0.0000 | — | — |
| `speech_triggers_test.mp4` | `profanity` | Matched TP | [1.000, 3.500) | [1.000, 3.500) | 1.0000 | +0.000s | +0.000s |
| `speech_triggers_test.mp4` | `profanity` | Unmatched FP | [7.000, 12.500) | — | 0.0000 | — | — |
| `speech_triggers_test.mp4` | `profanity` | Unmatched FN | — | [7.000, 9.500) | 0.0000 | — | — |
| `speech_triggers_test.mp4` | `profanity` | Unmatched FN | — | [10.500, 12.500) | 0.0000 | — | — |
| `audio_multi_event_mix.mp4` | `scream` | Unmatched FN | — | [3.000, 4.500) | 0.0000 | — | — |
| `audio_scream_test.mp4` | `scream` | Unmatched FN | — | [1.200, 2.700) | 0.0000 | — | — |

---

## 5. Annotation Integrity & Unsupported Detectors

### Unsupported Detectors Registry

| Trigger | Component | Owner | Status | GT Labels in labels.csv | Reason / Blocking Dependency |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `spider` | spider_detector | Person A | **MISSING_DETECTOR** | 0 | Detector implementation not available in repository. |

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
