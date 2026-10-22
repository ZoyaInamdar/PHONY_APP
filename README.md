# Trigger-Aware Player (`PHONY_APP`)

Fire TV (Vega OS) player that hides, mutes, dims, or skips content a viewer chooses to avoid, applied on-device.

Amazon Developer Hackathon 2026 Project.

---

## 1. Shared Pipeline Configuration (`config.yaml`)

The offline detection pipeline, cue merging logic, and cue-sheet packaging are governed by `config.yaml` located at the root of the repository. This configuration serves as the contract between:
- **Person A**: Player-side cue-sheet engine and runtime viewer action application.
- **Person B**: Pipeline configuration, schema validation, media ingest, loudness detection, cue merging, packaging, and evaluation harness.
- **Person C**: Additional offline detectors (flashing, audio event classification).

### How to Load the Configuration in Python

```python
import yaml
from pathlib import Path

def load_pipeline_config(config_path: str = "config.yaml") -> dict:
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# Example usage:
config = load_pipeline_config()
timeline_unit = config["timeline"]["timestamp_unit"]  # "seconds"
flash_threshold = config["triggers"]["flashing"]["threshold"]  # 0.10
```

Alternatively, use the built-in validator module to load and validate simultaneously:

```python
from scripts.validate_config import load_config, validate_config

is_valid, errors = validate_config("config.yaml", "schema/config.schema.json")
if not is_valid:
    raise ValueError(f"Configuration invalid: {errors}")

config = load_config("config.yaml")
```

### How to Validate Configuration
```bash
python scripts/validate_config.py
pytest tests/test_config.py -v
```

---

## 2. Detection-to-Player Data Contract

### Raw Detections (`data/cues.json`) vs. Packaged Player Index (`data/index.json`)

To maintain clean separation between offline detector components and the on-device player engine:

1. **Raw Detections (`data/cues.json`)**:
   - Emitted directly by offline detectors (`flash_detector`, `loudness_detector`, `audio_event_classifier`, etc.).
   - Contains unmerged, unpadded trigger intervals.
   - Minimal contract fields: `start`, `end`, `trigger`, `score`, `source`.
   - Free from player-side concerns: does **not** include viewer actions, playback padding, or UI preferences.
2. **Packaged Player Index (`data/index.json`)**:
   - Input consumed by Person A's player-side cue-sheet engine on Fire TV (Vega OS).
   - Produced by `scripts/package_cues.py` after offline merge and packaging logic groups adjacent detections of the same trigger and source.
   - Contains top-level metadata: `schema_version`, `film_id`, `timeline` (`duration_seconds`, optional `fps`), and an array of `cues`.
   - Each packaged cue contains: deterministic `id`, `start`, `end`, `trigger`, `score`, `source`.
   - Does **not** hardcode viewer actions; runtime actions (`blur`, `mute`, `skip`, `dim`, `warn`) and action precedence are applied on-device by Person A based on viewer settings.
   - For engine integration API, types, and adapter details, see [`docs/PLAYER_ENGINE_INTEGRATION.md`](docs/PLAYER_ENGINE_INTEGRATION.md).

> [!NOTE]
> All files in `data/cues.json` and `data/index.json` are **synthetic fixtures** for contract verification, automated tests, and player integration. They do not represent real media detection results.

---

### Common Detector Record Contract

Every raw detection record must adhere to this contract:

```json
{
  "start": 12.5,
  "end": 14.2,
  "trigger": "loud_spike",
  "score": 0.91,
  "source": "loudness_detector"
}
```

- **Interval Convention**: Mathematical half-open intervals `[start, end)`.
- **Field Naming**: Strictly `start` and `end` (never `start_seconds` / `end_seconds`).
- **Timing**: Seconds as decimal floats; `end` must be strictly greater than `start`, `start >= 0.0`.
- **Score Semantics**: Normalized float in `[0.0, 1.0]`. Detector-specific metric; not cross-comparable.
- **Non-finite Values**: `NaN` and `Infinity` are strictly forbidden.

---

### Canonical Trigger Identifiers

The 9 canonical identifiers defined in `config.yaml`:

| Trigger ID | Modality | Default Action | Status | Detection Method |
| :--- | :--- | :--- | :--- | :--- |
| `flashing` | Visual | `dim` | Baseline | Rule-based signal processing (16×9 grid, linear luminance) |
| `loud_spike` | Audio | `mute` | Baseline | Rule-based RMS relative spike (+12 dB above 10s median) |
| `gunshot` | Audio | `mute` | Experimental | Pretrained audio classification (AST / YAMNet / PANNs) |
| `explosion` | Audio | `mute` | Experimental | Pretrained audio classification (AST / YAMNet / PANNs) |
| `scream` | Audio | `mute` | Experimental | Pretrained audio classification (AST / YAMNet / PANNs) |
| `profanity` | Text | `mute` | Baseline | Rule-based text matching on subtitles (STT fallback) |
| `insect` | Visual | `hide` | Experimental | Visual detection (stretch feature, disabled) |
| `jump_scare` | Audio-Visual | `warn` | Experimental | Compound heuristic (stretch feature, disabled) |
| `sustained_loudness` | Audio | `reduce` | Experimental | Audio loudness heuristic (stretch feature, disabled) |
| `visible_blood` | Visual | `hide` | Experimental | Visual detection (stretch feature, disabled) |

Allowed actions vocabulary: `dim`, `mute`, `blur`, `skip`, `warn`.

---

## 3. Merging and Packaging Policies

### Maximum Segment Duration and Merge Policy

Implemented in `scripts/package_cues.py` (`merge_detections`):

1. **Grouping**: Detections are grouped strictly by matching `(trigger, source)`. Detections with different triggers or different sources are **never merged**.
2. **Merge Gap**: Adjacent intervals of the same trigger/source are merged if the time gap between them satisfies:
   $$\text{gap} \le \text{merge\_gap\_seconds} \quad (\text{default: } 2.5\text{s})$$
3. **Maximum Duration**: No merged interval may exceed `max_segment_duration_seconds` (default: `60.0s`).
4. **Segmentation Behavior on Limit**: If the next detection would cause the potential merged segment duration to exceed `max_segment_duration_seconds`, the current segment is finalized at its boundary, and a new segment is started with the next detection. Detections are **never truncated** or silently dropped.
5. **Overlong Raw Detection Validation**: Any single raw detection whose duration already exceeds `max_segment_duration_seconds` is rejected with an explicit validation error (`ValueError`).
6. **Overlapping Detections**: Overlapping detections of the same trigger and source ($\text{gap} \le 0$) are merged into a single span $[ \text{start}_1, \max(\text{end}_1, \text{end}_2) ]$, retaining the maximum score.
7. **Deterministic Ordering**: All output cues are sorted chronologically by `start`, then `end`, then `trigger`, then `source`.
8. **Separation from Padding**: Detection timestamps reflect raw event boundaries. Playback padding is applied at player runtime and does not overwrite detection bounds.

---

### Deterministic Cue ID Strategy and Collision Safety

Packaged cues use a deterministic ID scheme that guarantees identical IDs across repeated runs:

```text
cue_{film_id}_{trigger}_{start_ms:06d}_{end_ms:06d}
```

*Example*: `cue_sintel_flashing_010200_011800`

- **Timestamp-to-Millisecond Conversion**:
  $$\text{start\_ms} = \text{int}(\text{round}(\text{start} \times 1000.0))$$
  $$\text{end\_ms} = \max(\text{int}(\text{round}(\text{end} \times 1000.0)), \text{start\_ms} + 1)$$
  This guarantees that sub-millisecond valid intervals ($\text{end} > \text{start}$) always produce $\text{end\_ms} > \text{start\_ms}$.
- **Collision Detection**: If two distinct detections produce the same ID (e.g. sub-millisecond boundaries that round to the same millisecond), packaging halts with a `CueIdCollisionError` identifying both conflicting records. Cues are never silently overwritten or discarded.
- **Reproducibility**: Repeated packaging of identical raw inputs produces byte-for-byte identical output. Shuffling raw input records does not alter the output cue IDs or order.

---

### True Configuration-to-Schema Trigger Synchronization

To prevent silent configuration drift between `config.yaml` and `schema/index.schema.json`:

- The index validator (`scripts/validate_index.py`) executes `check_trigger_synchronization()`.
- It directly inspects the canonical keys in `config.yaml` under `triggers:` and the enum list in `schema/index.schema.json` under `#/$defs/packaged_cue/properties/trigger/enum`.
- It verifies that the sets match exactly and checks for duplicates.
- If a trigger is missing, unexpected, misspelled, or duplicated, validation fails with an actionable diagnostic identifying the exact trigger IDs.

---

### Media Duration Verification

The validator supports optional verification against the actual media container:

```bash
# Verify synthetic index against schema and domain rules only
python scripts/validate_index.py

# Verify index against an actual media file via ffprobe
python scripts/validate_index.py --media data/media/sintel_trailer.mp4 --duration-tolerance 0.1
```

- **Tolerance**: `--duration-tolerance` (default: `0.1s`).
- **ffprobe Inspection**: Checks presentation container duration (`format=duration`).
- **Failure Handling**: If `--media` is passed but the media file does not exist or `ffprobe` is not installed in PATH, validation fails clearly.
- **Synthetic Fixtures**: Media files are **not required** when validating synthetic fixtures in standard mode.

---

### Score Semantics

The `score` field is normalized to $[0.0, 1.0]$, but carries detector-specific semantics:

- **Not Cross-Comparable**: Scores from different detectors (e.g. RMS dB ratio vs. CLIP softmax probability vs. binary text match) cannot be compared directly.
- **Not Calibrated Probabilities**: A score of `0.9` does **not** signify a 90% posterior probability of a trigger event.
- **Independent Thresholds**: Detection thresholds must be tuned and evaluated independently per trigger against labeled ground-truth clips.

---

## 4. Media Ingestion Pipeline CLI (`scripts/ingest_media.py`)

Run the ingestion pipeline to normalize incoming media, extract audio, generate low-resolution frames, and extract preview snapshots:

```bash
# Ingest media with default 24 fps and auto film ID
python scripts/ingest_media.py --input data/media/example.mp4 --output-dir data/processed/example

# Ingest media with explicit film ID and parameters
python scripts/ingest_media.py --input data/media/example.mp4 --output-dir data/processed/example --film-id example --fps 24.0 --max-width 1920 --max-height 1080
```

### Ingestion Specifications & Guarantees:
- **Video Normalization**: H.264 (`libx264`), `yuv420p`, constant 24.0 fps, maximum resolution 1920×1080 (preserving aspect ratio and enforcing even dimensions), timeline origin set to `0.0`.
- **Audio Extraction**: Signed 16-bit little-endian mono PCM WAV at 44,100 Hz (`pcm_s16le`). If the source contains no audio stream, this is recorded explicitly (`present: false`, `extraction_status: "skipped_no_stream"`) without generating fake audio.
- **Low-Resolution Video Frames**: Extracted to raw streaming RGB24 frames (`low_res_frames_128x72.rgb`) at 128×72 resolution (27,648 bytes/frame). Memory-efficient and directly accessible via `numpy.memmap`.
- **Preview Snapshots**: Sampled at $\le 1.0$ fps, maximum dimension 512 pixels, with snapshot catalog recorded in `snapshots/frames_manifest.json`.
- **Rich Ingestion Manifest**: Emits `ingest_manifest.json` validated against `schema/manifest.schema.json` (JSON Schema Draft 2020-12).
- **Atomic Staging & Playback Isolation**: Ingestion processes assets in an isolated staging directory and publishes them atomically upon schema validation. Ingestion alone **never marks the engine playback manifest (`data/manifest.json`) as complete**, preserving the boundary between media ingestion and full analysis completion.

---

## 5. Packaging Pipeline CLI (`scripts/package_cues.py`)

Run the packaging pipeline to merge raw detector emissions and generate a validated player index:

```bash
# Basic packaging using defaults
python scripts/package_cues.py --input data/cues.json --output data/index.json

# Packaging with explicit film ID, timeline duration, and frame rate
python scripts/package_cues.py --input data/cues.json --output data/index.json --film-id sintel --duration 52.0 --fps 24.0
```

- **Atomic Writes**: Output is written to a temporary file in the destination directory and renamed atomically (`os.replace`). If packaging or schema validation fails, existing output files remain untouched.
- **Reproducibility**: Repeated runs produce byte-for-byte identical output.

---

## 6. Running Validators and Automated Tests

### CLI Validation
```bash
# Validate config.yaml
python scripts/validate_config.py

# Validate packaged index.json
python scripts/validate_index.py

# Ingest raw media
python scripts/ingest_media.py --input data/media/sintel.mp4 --output-dir data/processed/sintel

# Package cues
python scripts/package_cues.py --input data/cues.json --output data/index.json
```

### Automated Pytest Suite
```bash
# Run all tests (87 tests: 71 existing + 16 ingest)
pytest -v

# Run media ingestion tests (16 tests)
pytest tests/test_ingest.py -v

# Run packaging pipeline tests (24 tests)
pytest tests/test_package.py -v

# Run index contract tests (31 tests)
pytest tests/test_index.py -v

# Run config tests (16 tests)
pytest tests/test_config.py -v
```

---

## 7. Validation Responsibilities: JSON Schema vs. Python

| Validation Rule | Enforced by JSON Schema (`schema/index.schema.json`) | Enforced by Python (`scripts/validate_index.py`) |
| :--- | :---: | :---: |
| Top-level required fields (`schema_version`, `film_id`, `timeline`, `cues`) | ✅ | ✅ |
| Field types and non-empty strings | ✅ | ✅ |
| Score range $[0.0, 1.0]$ | ✅ | ✅ |
| Canonical trigger ID enumeration | ✅ | ✅ |
| Interval validity ($\text{end} > \text{start}$) | ❌ | ✅ |
| Strict rejection of non-finite numbers (`NaN`, `Infinity`) | ❌ | ✅ |
| Boundary checks against `timeline.duration_seconds` | ❌ | ✅ |
| Cue ID uniqueness (no duplicate IDs) | ❌ | ✅ |
| Strict chronological ordering (`start`, `end`, `trigger`, `source`) | ❌ | ✅ |
| Zero-drift check against `config.yaml` triggers | ❌ | ✅ |
| Actual media duration verification via `ffprobe` | ❌ | ✅ |

---

## 8. Unresolved Model Selections & Stretch Features

1. **Audio Event Classifier (`gunshot`, `explosion`, `scream`)**: `AST` vs. `YAMNet` vs. `PANNs` (Owner: Person C & Person B; status: `unresolved`).
2. **Speech-to-Text Fallback (`profanity`)**: `faster-whisper` vs. `Amazon Transcribe` (Owner: Person B; status: `unresolved`).
3. **Stretch Features (`jump_scare`, `insect`, `visible_blood`)**: Disabled by default (`status: "experimental"`, `enabled: false`).

---

## 9. Automated Synthetic Test-Clip & Ground-Truth Generator (Person C)

To ensure detector reproducibility, calibration, and regression testing prior to real-media ingest, Person C implemented an automated synthetic test-clip generator in [`scripts/generate_test_clips.py`](scripts/generate_test_clips.py).

### CLI Usage

```bash
# Generate the full benchmark suite (sync, flash, detail, audio, speech, negatives)
python scripts/generate_test_clips.py --output-dir data/test_clips

# Generate individual benchmark components
python scripts/generate_test_clips.py --sync-only      # Flash + beep sync calibration clip
python scripts/generate_test_clips.py --flash-only     # Flash rate, fps, area, contrast matrix
python scripts/generate_test_clips.py --detail-only    # Fine-detail text pattern for blur checks
python scripts/generate_test_clips.py --audio-only     # Gunshot, explosion, scream & negative audio
python scripts/generate_test_clips.py --speech-only    # Spoken lines with truth .srt & parser audit
python scripts/generate_test_clips.py --negatives-only # Negative video gradients & fades
```

### Generated Asset Inventory

| Asset Name | Target Purpose | Trigger Label in `labels.csv` | Notes / Properties |
| :--- | :--- | :---: | :--- |
| `sync_test.mp4` | Person A Vega sync calibration | *None* (logged to `sync_labels.csv`) | 10s MP4, 3 simultaneous white flashes & 1000 Hz beeps |
| `flash_rate_*hz_*.mp4` | Flash detector rate sensitivity | `flashing` | 2, 3, 4, 6 flashes/sec at 24 fps (WCAG $\ge 3$ Hz threshold) |
| `flash_fps_*fps_*.mp4` | Framerate invariance | `flashing` | 24, 25, 30, 60 fps test cases with whole-frame phase timing |
| `flash_area_*_*.mp4` | Spatial area cutoff calibration | `flashing` | `full` (100%), `half` (50%), `quarter` (25% WCAG), `small_patch` (6.25%) |
| `flash_contrast_*_*.mp4`| Luminance step thresholds | `flashing` | Contrast steps: 0.05, 0.10, 0.20, 0.50 |
| `detail_test_pattern.mp4` | Blur & blackout check on Vega | *None* | Fine text (8–10pt), 1px grid lines, contrast swatches |
| `audio_gunshot_test.mp4` | AudioSet gunshot classifier | `gunshot` | High-frequency acoustic transient burst ($e^{-t / 0.04}$) |
| `audio_explosion_test.mp4`| AudioSet explosion classifier | `explosion` | Low-frequency detonation rumble (55 Hz $\rightarrow$ 25 Hz sweep) |
| `audio_scream_test.mp4` | AudioSet scream classifier | `scream` | Vocal distress vocalization (750–950 Hz vibrato + formants) |
| `audio_multi_event_mix.mp4`| Multi-modal composite mix | `gunshot`, `scream`, `explosion` | Composite 9-second clip with sequential acoustic triggers |
| `speech_triggers_test.mp4` | Subtitle & profanity pipeline | `profanity` | Dialogue lines matched to [`speech_triggers_truth.srt`](data/test_clips/speech_triggers_truth.srt) |
| `audio_negative_*.mp4` | False-positive control | *None* | Continuous ambient noise (-30 dBFS) & calm dialogue |
| `video_negative_*.mp4` | False-positive control | *None* | Sinusoidal spatial gradient, slow 0.2 Hz fade, static gray |

### Ground-Truth Contracts (`data/test_clips/labels.csv`)
- **Strict Canonical Enums**: Every positive row maps strictly to canonical trigger IDs (`flashing`, `gunshot`, `explosion`, `scream`, `profanity`).
- **Interval Bounds**: Mathematical intervals $[start, end)$ span from the first trigger event to the last trigger event ($0.0 \le start < end \le \text{duration}$).
- **Negative Isolation**: All negative controls and calibration markers (`sync_marker`) are strictly excluded from `labels.csv`.

---

### Critical Evaluation Limitations: Synthetic Clips vs. Real Films

> [!WARNING]
> **Synthetic test clips are significantly easier for automated detectors to parse than real-world commercial films.**
>
> 1. **Acoustic Simplicity**:
>    - Synthetic audio samples have isolated, high signal-to-noise ratios (SNR) with zero background musical accompaniment, dialogue overlap, or room reverberation.
>    - In contrast, real film audio mixes (*Sintel*, *Tears of Steel*) feature complex orchestral scores, Foley layers, ambient environmental effects, and overlapping speaker dialogue that can mask acoustic peaks and decrease detector confidence.
> 2. **Photometric & Geometric Uniformity**:
>    - Synthetic flash cases alternate between mathematically uniform RGB values across rectangular screen boundaries without motion blur, camera pans, compression macroblocking, or lighting gradients.
>    - Real-world flash sequences (e.g. muzzle flashes, lightning, emergency strobes) feature rapid non-uniform lighting, rolling shutter artifacts, lens flares, and dynamic scene motion that challenge luminance-delta algorithms.
> 3. **Subtitle Precision**:
>    - Synthetic speech lines have 100% accurate spelling, whole-cue alignment, and clean punctuation in [`speech_triggers_truth.srt`](data/test_clips/speech_triggers_truth.srt).
>    - Real film subtitles frequently feature dialect slang, mistranscriptions, timing drift, and sound effect annotations that require robust heuristic parsing.
>
> **Role in Pipeline:** These synthetic clips establish the **idealized upper bound of detector performance** and provide zero-regression smoke testing. High precision and recall on synthetic benchmarks are necessary but not sufficient for real-media deployment.

---

## 10. Multi-Film Catalogue & Content Delivery

The Trigger-Aware Player supports demo content and packaged detection indexes for selectable open-movie films licensed under Creative Commons Attribution 3.0 (Blender Foundation):

| Film Metadata | Film 1: Sintel Trailer (`sintel_trailer` / `sintel`) | Film 2: Tears of Steel (`tears_of_steel`) |
|---|---|---|
| **Film ID** | `sintel_trailer` (demo stream: `sintel`) | `tears_of_steel` |
| **Title** | *Sintel* | *Tears of Steel* |
| **Duration & FPS** | 52.21s @ 24.0 fps (Trailer demo) | 734.167s @ 24.0 fps (Full film demo) |
| **Stream / Media URL** | `https://media.w3.org/2010/05/sintel/trailer.mp4` | `https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov` |
| **Packaged Index** | `data/processed/sintel_trailer/index.json` & `data/index.json` | `data/tears_of_steel_index.json` & `data/processed/tears_of_steel/index.json` |
| **Subtitles** | `data/subtitles/sintel.srt` | `data/subtitles/tears_of_steel.srt` |
| **Rights / License** | CC BY 3.0 Blender Foundation | CC BY 3.0 Blender Foundation |

### Architecture & Catalogue Abstraction

Content metadata and index resolution are centralized in [`src/data/catalogue.ts`](src/data/catalogue.ts) and [`src/data/filmCatalogue.ts`](src/data/filmCatalogue.ts):
- `getAllFilms()`: Returns all discoverable film catalogue entries.
- `getFilm(filmId)`: Returns metadata for a specific film; throws descriptive error if `filmId` is unknown.
- `getFilmMetadata(filmId)`: Safe non-throwing lookup.
- `loadFilmIndex(filmId)`: Loads and strictly validates the packaged index against `schema/index.schema.json` and checks that the index `film_id` strictly matches the requested film to prevent cross-film contamination.
- `loadFilmCues(filmId)`: Loads and adapts trigger cues using configured trigger policies.

Backward-compatible helpers in [`src/data/playbackCues.ts`](src/data/playbackCues.ts):
- `sintelIndexCues`: Pre-adapted cues for *Sintel*.
- `sintelTrailerCues`: Packaged cues adapted from real Sintel trailer detection index (`data/processed/sintel_trailer/index.json`).
- `tearsOfSteelIndexCues`: Pre-adapted cues for *Tears of Steel*.
- `getCuesForFilm(filmId)`: Dynamic lookup resolving cues by film ID.

### Packaging a Film Index

To package detections for a film using the standard Person B packaging script:

```bash
# Package Tears of Steel detections into schema-compliant index
python scripts/package_cues.py \
  --input data/processed/tears_of_steel/raw_detections.json \
  --output data/tears_of_steel_index.json \
  --film-id tears_of_steel \
  --duration 734.167 \
  --fps 24.0 \
  --config config.yaml \
  --schema schema/index.schema.json
```

### Validating Packaged Indexes

```bash
# Validate Sintel index
python scripts/validate_index.py --index data/index.json

# Validate Tears of Steel index
python scripts/validate_index.py --index data/tears_of_steel_index.json
```

### Film Selection & Cue Isolation Rules

1. **Clean Slate on Switch**: Switching films via home screen tiles (`App.tsx`) or the in-player film toggle (`PlayerScreen.tsx`) immediately cancels in-flight volume ramps, resets `PlaybackState`, clears active prompt dialogs, resets elapsed playback time, and unloads previous cues.
2. **Zero Silent Fallback**: If an unknown film ID is selected or a film's index file is missing/malformed, the player displays an `Index Load Error` banner (`index-error-banner`) and stops playback rather than silently defaulting to another film's cues.

### Running Content Delivery Tests

```bash
pytest tests/test_content_delivery.py -v
```

---

## 11. Opt-in Experimental Detector Pipeline

The multi-detector evaluation pipeline integrates optional experimental detectors (`jump_scare`) alongside the canonical triggers.

### Experimental Detectors
- **`jump_scare`**: Multimodal audio-visual compound detector combining acoustic transient loudness spikes with rapid visual transitions.
- **Experimental Test Clips**: Ground-truth benchmark includes both positive and negative validation clips (`jump_scare_positive.mp4`, `jump_scare_negative.mp4`).

### Running Multi-Detector Pipeline

```bash
# Standard pipeline execution
python scripts/run_multi_detector_pipeline.py

# Opt-in experimental detector pipeline
python scripts/run_multi_detector_pipeline.py --include-experimental
```

---

## 12. Hackathon Demonstration & Reproduction Guide

### 12.1 App Scope & Capabilities

The **Trigger-Aware Player** is an on-device accessible media player for Amazon Fire TV (Vega OS) that automatically mitigates distressing or harmful audiovisual triggers (`flashing`, `gunshot`, `explosion`, `scream`, `loud_spike`, `sustained_loudness`, `profanity`, `insect`, `jump_scare`, `visible_blood`) according to viewer-selected sensitivity profiles.

- **What the app does**:
  - Dynamically enforces viewer preferences on-device using mathematical half-open intervals $[start, end)$.
  - Executes **`hide`** (instant visual blackout protection).
  - Executes **`mute`** (complete audio silencing with smooth volume ramp fade-in/fade-out).
  - Executes **`reduce`** (configurable fractional audio volume attenuation, e.g. 25%, 35%, 50%).
  - Executes **`warn`** (advance heads-up notification without interrupting playback).
  - Executes **`skip`** (forward scene seek past trigger interval with duration clamping).
  - Resolves overlapping cues with strict precedence: `mute` overrides `reduce`; `hide` overrides `blur`/`dim`.
  - Enforces review safety: unconfirmed `needs_review` cues **never** trigger a skip without explicit viewer confirmation.
  - Restores audio volume and visual display immediately upon cue exit, seek, or media change.
  - Persists preferences and disabled trigger categories across app restarts via `AsyncStorage`.

- **What the app does not do (Known Hardware Limitations)**:
  - *Spatial Blur & Brightness Dimming*: The engine computes `visualAction: 'blur'` and `visualAction: 'dim'`, but hardware shader filtering requires native Vega OS display overlay APIs. In Phase 0, blur cues fall back to honest visual blackout protection without fabricating unsupported shader effects.
  - *Tears of Steel Playback*: The full 734s video container (`tears_of_steel.webm`) is absent on disk. The film is locked as `isAvailable: false` in the catalogue with an explicit blocker message to prevent phantom playback.

---

### 12.2 Media Asset & Detector Inventory

| Asset Name | Local File Path | Duration | Status | Packaged Index |
| :--- | :--- | :--- | :--- | :--- |
| **Sintel (Trailer)** | `data/media/sintel_trailer.mp4` | 52.21s | **Verified Available** (H.264/AAC, 1080p, CC-BY 3.0 Blender Foundation) | `data/processed/sintel_trailer/index.json` (`synthetic: false`) |
| **Tears of Steel** | *Absent locally* (`*.webm` excluded by `.gitignore`) | 734.167s | **Blocked** (Subtitles present at `data/subtitles/tears_of_steel.srt`) | `data/processed/tears_of_steel/index.json` (`isAvailable: false`) |
| **Synthetic Test Clips** | `data/test_clips/*.mp4` | 1.0s – 10.0s | **Verified Available** (Generated by `scripts/generate_test_clips.py`) | Benchmarked against `data/test_clips/labels.csv` |

**Real Media Detector Ingestion**:
- `flash_detector`, `loudness_detector`, and `audio_event_classifier` were run on the verified Sintel trailer.
- Packaged cues are saved in `data/processed/sintel_trailer/index.json` and bundled into the player.
- Reported precision, recall, and F1 figures in evaluation summaries derive from the synthetic ground-truth benchmark (`data/test_clips/labels.csv`), as commercial films lack frame-by-frame human ground truth.

---

### 12.3 Exact Setup, Build, and Execution Commands

#### 1. Prerequisites Verification
```bash
# Python 3.10+ virtual environment
source .venv/bin/activate
pip install -r requirements.txt

# Node.js & dependencies
npm install

# Verify Vega CLI
/Users/zoya/vega/bin/vega device list
```

#### 2. Run Automated Verification Suites
```bash
# TypeScript engine typecheck (0 errors)
npm run typecheck

# Jest pure engine & subtitles tests (15 suites, 179 tests)
npm test

# Jest React Native / Kepler component tests (23 suites, 336 tests)
npm run test:rn

# Python detector & packaging tests (421 passed, 5 skipped)
PATH="/opt/homebrew/bin:$PATH" pytest tests/ -q

# Evaluation results summary
npm run results
```

#### 3. Build & Launch on Vega Virtual Device
```bash
# Build the Vega aarch64 debug package
npm run build:debug

# Deploy and launch the application onto the running VirtualDevice
/Users/zoya/vega/bin/vega run-app build/private/kepler/@amazon-devices/triggerawareplayer/undefined/vega/aarch64/Debug/@amazon-devices/triggerawareplayer_aarch64.vpkg com.hackathon2026.triggerawareplayer.main --deviceId VirtualDevice

# Alternatively, run the complete automated launch script:
bash scripts/launch_player.sh
```

---

### 12.4 Live Demonstration Workflow

1. **Launch App**: The app starts on the Fire TV Home Screen with 4 D-pad navigable tiles: `Home`, `Get Started`, `Debug`, and `Learn More`.
2. **Select Media**:
   - Press **Get Started** (`KEY_ENTER`) to launch the player with the verified **Sintel Trailer**.
   - Notice that if Tears of Steel is selected via `Learn More`, the app displays the truthful blocker message: *"Full video media asset (tears_of_steel.webm) is absent locally."*
3. **Real Film Playback (`Cues: Index`)**:
   - The player loads real packaged cues from `data/processed/sintel_trailer/index.json`.
   - At 1.8s – 10.6s: Real `loud_spike` cue triggers audio mute with smooth volume restoration.
   - At 8.64s – 9.6s: Real `explosion` / `gunshot` audio event triggers audio muting.
   - At 24.0s – 25.5s: Real `flashing` trigger activates visual blackout protection.
4. **Viewer Settings Drawer**:
   - Focus the **Settings** button in the player control bar or press `Debug` from Home.
   - Adjust **Sensitivity** (`strict`, `moderate`, `off`).
   - Toggle **Disabled Categories** (e.g. disabling `loud_spike` allows loud audio to pass through unmuted).
   - Adjust **Volume Reduction Gain** (15%, 25%, 35%, 50%, 75%).
5. **Demonstrating All Supported Actions via Demo Profiles**:
   - Focus the **`Cues`** toggle button (`control-profile`) on the player control bar.
   - Press Enter to cycle to **`Cues: Verify`**:
     - *6.0s – 10.0s*: Standalone Audio Mute.
     - *13.0s – 18.0s*: Standalone Audio Volume Reduction (attenuates volume to 35%).
     - *21.0s – 26.0s*: Standalone Visual Blackout (`hide`).
     - *29.0s – 38.0s*: Overlapping Cues — `hide` [29s–35s), `reduce` [30s–37s), and `mute` [32s–34.5s). Demonstrates that mute strictly overrides reduce, then restores to reduced volume at 34.5s, visual returns at 35.0s, and volume recovers to 100% at 37.0s.
   - Press Enter again to cycle to **`Cues: Skip`**:
     - *10.0s – 18.0s*: Ready Skip cue jumps forward past the scene to 18.0s.
     - *18.0s – 24.0s*: Immediate destination action synchronization (audio mute at destination).
     - *30.0s – 36.0s*: `needs_review` Skip cue — proves that without confirmation, the player safely falls back to visual blackout and **never** skips unexpectedly.
   - Press Enter again to return to **`Cues: Index`** (real film packaged cues).

---

### 12.5 Troubleshooting Common Failures

- **`Virtual device not found`**: Ensure the Vega Virtual Device is started:
  ```bash
  /Users/zoya/vega/bin/vega virtual-device start --timeoutSeconds 180
  ```
- **`ModuleNotFoundError: No module named 'yaml'`**: Activate the project virtual environment before running Python scripts:
  ```bash
  source .venv/bin/activate
  ```
- **`Tears of Steel asset unavailable`**: Expected behavior. The full video asset `tears_of_steel.webm` was excluded from source control (`*.webm` in `.gitignore`). Use `sintel_trailer` for live video playback.

