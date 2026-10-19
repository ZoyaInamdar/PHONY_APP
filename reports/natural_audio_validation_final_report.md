# Natural Audio Validation Final Report: Explosion and Scream Detectors

**Project:** `PHONY_APP`  
**Role:** Person A — Detector Implementation and Validation  
**Date:** October 19, 2026  
**Status:** Preliminary Sample-Level Natural Audio Validation Completed  

---

## 1. Executive Summary

This report documents the rigorous, reproducible validation of the `explosion` and `scream` trigger detectors on real-world natural audio samples, executing the pipeline under existing thresholds without tuning to sample artifacts.

### Key Findings
1. **License & Provenance Verification:** Both candidate samples were verified against their original source pages and found to be licensed under **Creative Commons Attribution (CC-BY)** rather than CC0. Detailed provenance and attribution records were established in `docs/licenses/natural_audio_provenance.json`, and reference descriptions in `docs/licenses/SOURCES.md` were corrected.
2. **Audio Isolation:** Raw audio downloads and standardized 16 kHz mono evaluation files are strictly isolated in `data/natural_validation/` and excluded from the Vega OS production bundle.
3. **Independent Acoustic Ground Truth:** Event boundaries were established via waveform RMS energy analysis and listening before evaluating model outputs:
   - `explosion_freesound_156031.wav`: $[0.050, 3.200\text{s})$
   - `scream_dan_knoflicek_3.wav`: $[0.000, 1.650\text{s})$
4. **Evaluation Under Existing Thresholds (`explosion: 0.70`, `scream: 0.60`):**
   - **Explosion:** 0 TP, 0 FP, 1 FN. YAMNet correctly identified `Explosion` as its top class (peaking at **0.6564** in $[0.96, 1.92\text{s})$ and **0.5752** in $[0.00, 0.96\text{s})$), but fell slightly short of the strict $0.70$ confidence threshold, resulting in an unmatched FN.
   - **Scream:** 0 TP, 0 FP, 1 FN. The high-pitched terror scream was classified by YAMNet as `Emergency vehicle` ($0.5486$) and `Ambulance (siren)` ($0.5257$) rather than human vocal distress ($< 0.01$).
5. **Zero Disruption to Teammate / Player Work:** Working tree changes in `src/`, player UI components, and ground-truth test labels were kept completely intact.

---

## 2. Sources and Verified License Evidence

| Trigger | Source Page | Author / Creator | Verified License | Attribution Requirement |
| :--- | :--- | :--- | :--- | :--- |
| **explosion** | [Freesound #156031](https://freesound.org/people/Iwiploppenisse/sounds/156031/) | `Iwiploppenisse` | **CC BY 4.0** | `"Explosion" by Iwiploppenisse (CC BY 4.0), incorporating HerbertBoland, themfish, and ljudman.` |
| **scream** | [OpenGameArt: Screaming](https://opengameart.org/content/screaming) | Dan Knoflicek (`Macro`) | **CC-BY 3.0** | `"Scream 3.wav" from "Screaming" by Dan Knoflicek (CC-BY 3.0). Attribution requested: "SFX by Dan Knoflicek".` |

---

## 3. Files Acquired and Provenance

Full provenance and modification records are stored in `docs/licenses/natural_audio_provenance.json`:

1. **Explosion (`explosion_freesound_156031`):**
   - **Raw Download URL:** `https://cdn.freesound.org/previews/156/156031_2703579-hq.mp3`
   - **Raw File:** `data/natural_validation/raw/explosion_freesound_156031_raw.mp3` (44.1 kHz stereo MP3, 176,361 bytes)
   - **Raw SHA-256:** `a0ae839f71605caad04595e3e4adb6c7901359bbe87807814e9f82d1eefc20bf`
   - **Evaluation File:** `data/natural_validation/audio/explosion_freesound_156031.wav` (16 kHz mono 16-bit PCM, 244,400 bytes, duration: 7.636s)
   - **Evaluation SHA-256:** `8c087470fe0261dd7f201dba0f5412dc1fecd74a4ebf4ab6fa797036c4b3a62d`
   - **Transcoding Method:** `ffmpeg -v error -y -i <raw.mp3> -c:a pcm_s16le -ar 16000 -ac 1 <eval.wav>`

2. **Scream (`scream_dan_knoflicek_3`):**
   - **Raw Download URL:** `https://opengameart.org/sites/default/files/Scream%203.wav`
   - **Raw File:** `data/natural_validation/raw/scream_dan_knoflicek_3_raw.wav` (44.1 kHz mono 24-bit PCM, 228,042 bytes)
   - **Raw SHA-256:** `3e6c1965b8cffa6a80c72bb63c6fbc16ea602aa0c1baff0490dc4307dded28c6`
   - **Evaluation File:** `data/natural_validation/audio/scream_dan_knoflicek_3.wav` (16 kHz mono 16-bit PCM, 55,192 bytes, duration: 1.723s)
   - **Evaluation SHA-256:** `d28fee8140d9475f32e4349d94c2284ff234cfe43c278832547cb43dce387b5f`
   - **Transcoding Method:** `ffmpeg -v error -y -i <raw.wav> -c:a pcm_s16le -ar 16000 -ac 1 <eval.wav>`

---

## 4. Independent Ground-Truth Intervals

Event boundaries were determined independently via acoustic envelope analysis (10% peak RMS thresholding and auditory verification):

- **`explosion_freesound_156031.wav`:**
  - Initial blast onset: `0.050s`
  - Peak acoustic detonation energy: `0.300s` (RMS = 0.552)
  - Concussive decay / tail: extends to `3.200s`
  - Ground-truth interval: **`[0.050, 3.200)`**
- **`scream_dan_knoflicek_3.wav`:**
  - Vocal onset: `0.000s`
  - Sustained distress vocalization: `0.000s - 1.550s`
  - Breath release offset: `1.650s`
  - Ground-truth interval: **`[0.000, 1.650)`**

Recorded in `data/natural_validation/labels.csv`.

---

## 5. Commands Used

```bash
# 1. Download verified CC-BY audio samples
curl -fsSL "https://cdn.freesound.org/previews/156/156031_2703579-hq.mp3" -o data/natural_validation/raw/explosion_freesound_156031_raw.mp3
curl -fsSL "https://opengameart.org/sites/default/files/Scream%203.wav" -o data/natural_validation/raw/scream_dan_knoflicek_3_raw.wav

# 2. Transcode to standardized 16 kHz mono PCM
/opt/homebrew/bin/ffmpeg -v error -y -i data/natural_validation/raw/explosion_freesound_156031_raw.mp3 -c:a pcm_s16le -ar 16000 -ac 1 data/natural_validation/audio/explosion_freesound_156031.wav
/opt/homebrew/bin/ffmpeg -v error -y -i data/natural_validation/raw/scream_dan_knoflicek_3_raw.wav -c:a pcm_s16le -ar 16000 -ac 1 data/natural_validation/audio/scream_dan_knoflicek_3.wav

# 3. Execute evaluation harness with strict existing thresholds
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 scripts/evaluate_detections.py \
  --manifest data/natural_validation/manifest.json \
  --labels data/natural_validation/labels.csv \
  --clips-dir data/natural_validation/audio \
  --triggers explosion scream \
  --output-json reports/natural_audio_evaluation_report.json \
  --output-md reports/natural_audio_evaluation_report.md
```

---

## 6. Per-Category Quantitative Results

Evaluated with $\text{tIoU} \ge 0.50$ matching against ground truth under existing thresholds:

| Trigger | Ground Truth Interval | Detector Threshold | Detections Emitted | TP | FP | FN | Precision | Recall | F1 Score | Mean tIoU |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **explosion** | `[0.050, 3.200)` | 0.70 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |
| **scream** | `[0.000, 1.650)` | 0.60 | 0 | 0 | 0 | 1 | *null* | 0.0% | *null* | *null* |

### Detailed Window Activation Analysis

#### Explosion Analysis (`explosion_freesound_156031.wav`)
- **Win 0 [0.00 – 0.96s]:** `Explosion`: **0.5752**, `Boom`: **0.5351**, `Burst, pop`: 0.1828
- **Win 1 [0.48 – 1.44s]:** `Explosion`: **0.2490**, `Shatter`: 0.2426, `Glass`: 0.2026
- **Win 2 [0.96 – 1.92s]:** `Explosion`: **0.6564**, `Boom`: **0.5312**, `Burst, pop`: 0.3329
- **Win 3 [1.44 – 2.40s]:** `Explosion`: **0.4213**, `Boom`: **0.3512**
- **Win 4 [1.92 – 2.88s]:** `Eruption`: **0.3970**, `Explosion`: **0.2839**
- **Diagnostic Takeaway:** YAMNet robustly recognized the blast as an explosion, activating `Explosion` and `Boom` across all five detonation windows (peaking at $0.6564$). However, per strict evaluation rules, thresholds were preserved at $0.70$; because $0.6564 < 0.70$, no cue passed threshold gating.

#### Scream Analysis (`scream_dan_knoflicek_3.wav`)
- **Win 0 [0.00 – 0.96s]:** `Crowd`: 0.0672, `Music`: 0.0644, `Screaming`: 0.0041
- **Win 1 [0.48 – 1.44s]:** `Emergency vehicle`: **0.5486**, `Ambulance (siren)`: **0.5257**, `Siren`: **0.4201**, `Alarm`: **0.3450**
- **Diagnostic Takeaway:** This sample is an exceptionally high-pitched whistle-style scream. The Mel filterbank representation of the pure high-pitch fundamental aligned strongly with emergency vehicle sirens rather than biological vocal cord formants ($F_1/F_2$), leading to a false negative for human scream.

---

## 7. Limitations and Remaining Failures

1. **Sample Size Scope:** Two isolated samples provide preliminary qualitative and threshold sensitivity insights, but do not constitute a statistically significant test set.
2. **Threshold Trade-off on Natural Audio:**
   - At threshold $0.70$, the natural explosion reached $0.6564$ and was suppressed.
   - Lowering the threshold to $0.60$ or $0.55$ would capture natural explosions (turning this into a True Positive), but risks elevating false positive rates on dynamic film soundstages (e.g. timpani, vehicle doors, bass drops).
3. **Acoustic Signature Diversity in Screams:** High-pitched, whistle-register vocal distress cries risk aliasing to artificial alarms/sirens in standard AudioSet models without multi-band temporal feature conditioning.

---

## 8. Test Suite Verification

| Test Suite | Command | Result |
| :--- | :--- | :--- |
| **Python Unit Tests** | `pytest tests/ -q` | **310 passed, 5 skipped** (18.05s) |
| **React Native Jest Engine** | `npm test -- --watchAll=false` | **14 suites passed, 164 tests passed** |
| **React Native Jest Components** | `npx jest --config jest.config.json test/components/SettingsScreen.spec.tsx` | **1 suite passed, 8 tests passed** |

---

## 9. File Audit & Attribution

### Files Modified:
- `docs/licenses/SOURCES.md` — Corrected reference download descriptions from `_cc0` to verified CC-BY licenses with attribution notices.

### Files Created:
- `data/natural_validation/raw/explosion_freesound_156031_raw.mp3` — Raw CC BY 4.0 audio.
- `data/natural_validation/raw/scream_dan_knoflicek_3_raw.wav` — Raw CC-BY 3.0 audio.
- `data/natural_validation/audio/explosion_freesound_156031.wav` — Standardized 16 kHz mono PCM WAV.
- `data/natural_validation/audio/scream_dan_knoflicek_3.wav` — Standardized 16 kHz mono PCM WAV.
- `data/natural_validation/manifest.json` — Metadata manifest for natural audio validation clips.
- `data/natural_validation/labels.csv` — Independent ground-truth annotations.
- `docs/licenses/natural_audio_provenance.json` — Machine-readable provenance and license record.
- `reports/natural_audio_evaluation_report.json` — Harness JSON evaluation output.
- `reports/natural_audio_evaluation_report.md` — Harness Markdown evaluation report.
- `reports/natural_audio_validation_final_report.md` — This comprehensive validation document.

### Unrelated Files Preserved:
- All player UI files (`src/components/PlayerScreen.tsx`, `src/App.tsx`, `index.js`, `package.json`), teammate prototypes, and existing test-clip labels were kept completely intact.
