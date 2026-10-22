# Open Cue-Sheet Format Specification

This document outlines the standard for PHONY_APP's open cue-sheet format. It allows external producers to author, and internal engines to consume, timestamped trigger data for media playback.

## 1. Versioning
- **Field:** `schema_version` (integer).
- **Rule:** Increments only on breaking changes to the schema fields or allowed enum values. New, non-breaking fields can be added without bumping the version. Current version: `1`.

## 2. File Structures

### `index.json`
Acts as the entry point and manifest for a media asset.
- `schema_version`: (int) The schema version.
- `film_id`: (string) Unique identifier for the media (e.g., `"sintel"`).
- `media_hash`: (string) SHA-256 hash of the target media file for integrity checking.
- `cues_file`: (string) Relative path to the actual cue data (e.g., `"cues.json"`).

### `cues.json`
Contains the array of timestamped trigger events.
- `schema_version`: (int) The schema version.
- `cues`: (array of Cue Objects) The list of trigger events on the timeline.

## 3. The Cue Object & Intervals
All timing is based on the playback file's absolute timeline in seconds (float). 
- **Interval Format:** Cues use half-open intervals `[start, end)`. The trigger is active exactly at `start` and ends immediately before `end`.
- `start`: (float) Start time in seconds.
- `end`: (float) End time in seconds.

### Allowed Values
* **Triggers (The 10 Canonical):** `flashing`, `gunshot`, `explosion`, `scream`, `loud_spike`, `sustained_loudness`, `profanity`, `insect`, `jump_scare`, `visible_blood`.
* **Modality:** `audio`, `video`, `subtitle`.
* **Actions:** `blackout`, `blur`, `mute`, `reduce`, `warn`, `skip`.

## 4. Profanity Specifics (Evidence & Timing)
When `trigger: "profanity"` is used, the following fields are required:
- `evidence`: (string) The specific flagged word (e.g., `"f***"`).
- `timing`: (string) Typically `"estimated"`, indicating the interval spans the whole subtitle cue duration rather than exact word phonemes.
- `spoken_confirmed`: (boolean | null) 
  - `true` or `null`: The word is confirmed spoken; the engine must censor the caption **and** mute the audio.
  - `false`: The word is visually present but unheard; the engine must censor the caption only.

## 5. Padding Table
To ensure safety boundaries, raw detection intervals `[start, end)` receive enforced padding by the ingestion source before being written to the final cue sheet.

| Trigger | Pre-Padding (Seconds) | Post-Padding (Seconds) |
| :--- | :--- | :--- |
| `flashing` / `jump_scare` | -0.5s | +1.0s |
| `gunshot` / `explosion` | -0.2s | +0.5s |
| `loud_spike` / `scream` | -0.2s | +0.5s |
| `profanity` | 0.0s (Matches subtitle duration) | 0.0s |
| `insect` / `visible_blood` | -1.0s | +1.0s |

## 6. Minimal Example

```json
{
  "schema_version": 1,
  "cues": [
    {
      "start": 14.500,
      "end": 17.200,
      "trigger": "profanity",
      "modality": "audio",
      "action": "mute",
      "evidence": "f***",
      "timing": "estimated",
      "spoken_confirmed": true
    },
    {
      "start": 125.100,
      "end": 128.500,
      "trigger": "flashing",
      "modality": "video",
      "action": "blackout"
    }
  ]
}
```
