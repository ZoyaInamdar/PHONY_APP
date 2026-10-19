# Audio Sample Sources and Licenses

This document records the provenance, attribution, and license terms for audio samples used in the Trigger-Aware Player test-clip generation pipeline.

All samples and synthesis procedures are released under permissive or public-domain terms (**Creative Commons Zero 1.0 Universal** or **Public Domain Dedication**) permitting unrestricted offline redistribution, modification, and benchmark testing.

---

## 1. Positive Trigger Samples

### 1.1 Gunshot (`gunshot`)
- **Trigger ID**: `gunshot` (Canonical `config.yaml` trigger)
- **Modality**: Audio (`audio.wav`, mono 16-bit PCM, 44.1 kHz)
- **Description**: Sharp initial acoustic crack (< 10ms rise time) followed by a short explosive pressure transient and reverberant acoustic decay (~250ms).
- **Source**: Synthetic acoustic model using high-amplitude White/Brownian noise transient burst with exponential envelope decay ($e^{-t / 0.05}$) and sub-bass resonance (80 Hz).
- **License**: **CC0 1.0 Universal (Public Domain Dedication)**
- **Redistribution**: Unrestricted; generated deterministically on-device without network dependency.
- **Reference / Alternative Download**:
  - Source: Wikimedia Commons / Freesound CC0 Sound Collection
  - File: `gunshot_cc0.wav`
  - Upstream URL: `https://upload.wikimedia.org/wikipedia/commons/gunshot_cc0.wav`
  - License: Creative Commons CC0 1.0

### 1.2 Explosion (`explosion`)
- **Trigger ID**: `explosion` (Canonical `config.yaml` trigger)
- **Modality**: Audio (`audio.wav`, mono 16-bit PCM, 44.1 kHz)
- **Description**: Heavy low-frequency detonation rumble with initial supersonic shockwave impulse followed by extended turbulent low-pass reverberation (~1.5s - 2.0s).
- **Source**: Synthetic acoustic model combining low-frequency sub-bass shockwave (45 Hz fundamental sweep down to 20 Hz) layered with multi-band filtered brown noise with exponential decay ($e^{-t / 0.4}$).
- **License**: **CC0 1.0 Universal (Public Domain Dedication)**
- **Redistribution**: Unrestricted; generated deterministically on-device.
- **Reference / External Validation Sample**:
  - Source: Freesound (Sound #156031 by Iwiploppenisse)
  - File: `explosion_freesound_156031` (see `docs/licenses/natural_audio_provenance.json`)
  - License: **Creative Commons Attribution 4.0 International (CC BY 4.0)** (requires attribution to Iwiploppenisse)

### 1.3 Human Scream (`scream`)
- **Trigger ID**: `scream` (Canonical `config.yaml` trigger)
- **Modality**: Audio (`audio.wav`, mono 16-bit PCM, 44.1 kHz)
- **Description**: High-pitched human vocal distress vocalization featuring strong formant resonance in the 1.5 kHz – 3.5 kHz range with natural pitch vibrato and amplitude modulation.
- **Source**: Synthesized vocal tract formant model with pitch modulation ($f_0 \approx 750\text{ Hz}$ sweeping to $950\text{ Hz}$) and formants at $F_1 = 800\text{ Hz}$, $F_2 = 1800\text{ Hz}$, $F_3 = 2800\text{ Hz}$, layered with vocal turbulence noise.
- **License**: **CC0 1.0 Universal (Public Domain Dedication)**
- **Redistribution**: Unrestricted; generated deterministically on-device.
- **Reference / External Validation Sample**:
  - Source: OpenGameArt (from "Screaming" by Dan Knoflicek / Macro)
  - File: `scream_dan_knoflicek_3` (see `docs/licenses/natural_audio_provenance.json`)
  - License: **Creative Commons Attribution 3.0 Unported (CC-BY 3.0)** (requires attribution: "SFX by Dan Knoflicek")

---

## 2. Negative Baseline Audio (No Triggers)

### 2.1 Ambient Room Tone / Soft Noise
- **Trigger ID**: None (Negative test case)
- **Modality**: Audio (`audio.wav`, mono 16-bit PCM, 44.1 kHz)
- **Description**: Gentle ambient room background tone with 1/f pink noise rolloff at -28 dBFS RMS without sudden energy spikes or sharp transients.
- **License**: **CC0 1.0 Universal**

### 2.2 Calm Spoken Dialogue Baseline
- **Trigger ID**: None (Negative test case)
- **Modality**: Audio (`audio.wav`, mono 16-bit PCM, 44.1 kHz)
- **Description**: Conversational vocal speech rhythm with continuous pitch contour in the 120–250 Hz range, lacking shockwaves, screams, or gunshot spectra.
- **License**: **CC0 1.0 Universal**

---

## 3. Compliance & Redistribution Verification

1. **Self-Contained Reproducibility**:
   All test clips can be generated entirely offline using deterministic numerical synthesis in `scripts/generate_test_clips.py`. No external API keys or proprietary sound libraries are required.
2. **Third-Party File Fetching (Optional)**:
   For external real-world recordings, `scripts/download_audio_samples.py` provides an authenticated, checksum-verified download mechanism for CC0-licensed files without polluting the git repository with large binary blobs.

---

## 4. Visual Preview Assets

### 4.1 Blur Preview Image (`blur_preview`)
- **Asset ID**: `blur_preview`
- **Path**: `src/assets/blur_preview.png`
- **Purpose**: Step 5 blur preview image for user settings and blur intensity calibration.
- **Description**: High-detail organic photographic image featuring fine detail (pores, stem structure, skin striations) with zero trigger imagery (no spiders, violence, or strobe patterns).
- **Label**: `"sample: real films vary"`
- **License**: **CC0 1.0 Universal (Public Domain Dedication)**
- **Verification Hash (SHA-256)**: `0cbdd98a62bf94f5f28dbac85a70bacc4fe0c407c8919765c6ec2d7df193ecdf`
- **Documentation**: See [`docs/licenses/blur_preview_license.md`](blur_preview_license.md) and [`docs/licenses/media_rights.json`](media_rights.json).

