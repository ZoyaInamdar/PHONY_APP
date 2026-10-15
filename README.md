# Trigger-Aware Player (`PHONY_APP`)

Fire TV (Vega OS) player that hides, mutes, dims, or skips content a viewer chooses to avoid, applied on-device.

Amazon Developer Hackathon 2026 Project.

---

## 1. Shared Pipeline Configuration (`config.yaml`)

The offline detection pipeline, cue merging logic, and cue-sheet packaging are governed by `config.yaml` located at the root of the repository. This configuration serves as the contract between:
- **Person A**: Player-side cue-sheet engine and runtime viewer action application.
- **Person B**: Pipeline configuration, schema validation, media ingest, loudness detection, cue merging, packaging, and evaluation harness.
- **Person C**: Additional offline detectors (flashing, spider detection, audio event classification).

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
| `spider` | Visual | `blur` | Baseline | Zero-shot multimodal image-text matching (CLIP / SigLIP) |
| `fast_motion` | Visual | `warn` | Experimental | Frame difference rule (stretch feature, disabled) |
| `jump_scare` | Audio-Visual | `warn` | Experimental | Compound heuristic (stretch feature, disabled) |

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
3. **Zero-Shot Visual Classifier (`spider`)**: `CLIP` vs. `SigLIP` (Owner: Person C; status: `unresolved`).
4. **Stretch Features (`fast_motion`, `jump_scare`)**: Disabled by default (`status: "experimental"`, `enabled: false`).
