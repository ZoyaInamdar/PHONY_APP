# Player Engine Integration Guide (Person A ↔ Person B)

**Project:** PHONY_APP — Trigger-Aware Player for Amazon Fire TV (Vega OS)
**Target:** On-Device Player Cue Engine & Detection Pipeline Handoff
**Module Entrypoint:** `src/engine` (`src/engine/index.ts`)

---

## 1. Overview & Architecture Boundary

The Trigger-Aware Player architecture enforces a clean separation of responsibilities between offline detection/packaging and on-device player runtime:

```
+-------------------------------------------------------------+
| Person B: Offline Detection & Packaging Pipeline             |
|   1. Offline detectors emit raw records (data/cues.json)    |
|   2. scripts/package_cues.py merges & packages intervals    |
|   3. Outputs validated data/index.json                      |
+-------------------------------------------------------------+
                              |
                              | Consumes data/index.json
                              v
+-------------------------------------------------------------+
| Person A: On-Device Player Engine (Fire TV / Vega OS)        |
|   1. adaptPackagedIndex(indexJson, triggerPolicies)         |
|   2. PlaybackState / resolveActions(time, cues, options)    |
|   3. PlayerScreen applies hardware mute/volume/hide/overlay |
+-------------------------------------------------------------+
```

- **Person B's Responsibilities:** Media ingestion, loudness/subtitle detectors, merging adjacent intervals, packaging into `data/index.json`, JSON schema validation (`schema/index.schema.json`), and shared policy configuration (`config.yaml`).
- **Person A's Responsibilities:** Consuming `data/index.json`, adapting cues using active trigger policies, resolving instantaneous playback actions (mute, volume reduction, visual hiding, warning alerts, skipping) across seeking and time updates, handling overlapping cue precedence, and driving playback hardware on Vega OS.

---

## 2. Packaged Index Input Contract (`data/index.json`)

The engine consumes the packaged index emitted by `scripts/package_cues.py`. The input must conform to `schema/index.schema.json`:

```json
{
  "schema_version": 1,
  "film_id": "sintel",
  "timeline": {
    "duration_seconds": 52.0,
    "timestamp_unit": "seconds",
    "interval_convention": "half_open",
    "fps": 24.0
  },
  "cues": [
    {
      "id": "cue_sintel_flashing_010200_011800",
      "start": 10.2,
      "end": 11.8,
      "trigger": "flashing",
      "score": 0.85,
      "source": "flash_detector"
    },
    {
      "id": "cue_sintel_loud_spike_014000_014800",
      "start": 14.0,
      "end": 14.8,
      "trigger": "loud_spike",
      "score": 0.92,
      "source": "loudness_detector"
    }
  ]
}
```

### Key Field Requirements:
- **`schema_version`**: Must be integer `1`.
- **`film_id`**: Non-empty string identifier.
- **`timeline.duration_seconds`**: Positive finite number (required).
- **`timeline.timestamp_unit`**: Must be `"seconds"` (required in `schema/index.schema.json`).
- **`timeline.interval_convention`**: Must be `"half_open"` ($[\text{start}, \text{end})$) (required in `schema/index.schema.json`).
- **`cues`**: Chronologically sorted array of cues. Each cue must include:
  - `id`: Unique, non-empty deterministic string.
  - `start`: Float in seconds ($\ge 0.0$).
  - `end`: Float in seconds ($> \text{start}$, $\le \text{duration\_seconds}$).
  - `trigger`: One of the 9 canonical trigger IDs (`flashing`, `loud_spike`, `gunshot`, `explosion`, `scream`, `profanity`, `spider`, `fast_motion`, `jump_scare`).
  - `score`: Normalized float in $[0.0, 1.0]$.
  - `source`: Detector component string (e.g., `loudness_detector`, `flash_detector`).
  - `status`: Optional review status string (`"ready"` or `"needs_review"`). Defaults to `"ready"` in `adaptPackagedIndex` if omitted.

---

## 3. Public Engine API & Entrypoints

Person B can import all necessary engine constructs directly from `src/engine`:

```typescript
import {
  adaptPackagedIndex,
  PlaybackState,
  resolveActions,
  DEFAULT_VIEWER_OPTIONS,
  Cue,
  ViewerOptions,
  EffectiveActions,
} from './src/engine';
```

### 3.1 `adaptPackagedIndex(indexInput, policies): Cue[]`
Converts a raw JSON packaged index into validated player `Cue[]` objects using the configured trigger policies:

```typescript
// Using TypeScript imports (tsconfig has resolveJsonModule enabled):
import { adaptPackagedIndex } from './src/engine';
import packagedIndex from './data/index.json';
import triggerPoliciesJson from './test/fixtures/triggerPolicies.json';

const cues = adaptPackagedIndex(packagedIndex, triggerPoliciesJson.policies);

// Alternatively, in Node.js / CommonJS:
// const { adaptPackagedIndex } = require('./src/engine');
// const packagedIndex = require('./data/index.json');
// const { policies } = require('./test/fixtures/triggerPolicies.json');
// const cues = adaptPackagedIndex(packagedIndex, policies);
```

**Behavior & Guarantees:**
- Validates the top-level structure, timeline, chronological ordering, and interval bounds. Throws descriptive errors if invalid.
- Matches `cue.trigger` against `policies[cue.trigger]`.
- Silently ignores cues whose trigger policy has `enabled: false` (e.g., experimental triggers).
- Resolves the cue's `action` (using `default_action` or legacy `action`) and `source` modality (`modality` or legacy `source`).
- **Preserves Unsupported Actions:** For triggers configured with `dim` or `blur`, the original action is preserved faithfully. It is **never** silently converted to `hide` or `reduce`.

### 3.2 `PlaybackState` Class
Manages stateful playback evaluation (including once-per-cue skip semantics and seek rewind clearing):

```typescript
import { PlaybackState, DEFAULT_VIEWER_OPTIONS } from './src/engine';

const playback = new PlaybackState();

// 1. Evaluate actions at current playback time (e.g., during onTimeUpdate)
const actions = playback.resolveAt(currentTime, cues, DEFAULT_VIEWER_OPTIONS);

// 2. Notify on user seek (clears re-armed skip cues on rewind)
playback.seek(newTime, cues);

// 3. Reset state on new video load
playback.reset();
```

### 3.3 `resolveActions(time, cues, options): EffectiveActions`
Pure, stateless action evaluation function for a specific timestamp:

```typescript
import { resolveActions, DEFAULT_VIEWER_OPTIONS } from './src/engine';

const actions = resolveActions(10.5, cues, DEFAULT_VIEWER_OPTIONS);
```

---

## 4. Output Contract (`EffectiveActions`)

On every playback tick or seek, the engine produces an `EffectiveActions` record:

```typescript
export interface EffectiveActions {
  /** The current evaluation timestamp in seconds */
  time: number;

  /** True if visual content must be hidden via blacked-out video surface */
  hide: boolean;

  /**
   * The resolved visual action priority: 'hide' > 'blur' > 'dim' | null.
   * Note: 'blur' and 'dim' report intended policy when renderer lacks native filter.
   */
  visualAction: 'hide' | 'blur' | 'dim' | null;

  /**
   * Sound multiplier in [0.0, 1.0].
   * 0.0 = Muted
   * (0.0, 1.0) = Volume Reduced (e.g. 0.35)
   * 1.0 = Normal audible playback
   */
  soundGain: number;

  /** Target seek timestamp if a skip action fired, otherwise null */
  skipTo: number | null;

  /** IDs of all active cues at this timestamp */
  activeCueIds: string[];

  /** IDs of active cues requesting heads-up warning alerts */
  warnCueIds: string[];
}
```

---

## 5. Action Resolution Rules & Precedence

1. **Interval Convention:** Half-open interval $[start, end)$. Active when $start \le time < end$.
2. **Audio Precedence (Mute vs. Reduce):**
   - If both `mute` and `reduce` cues are active simultaneously, `mute` wins (`soundGain = 0`).
   - When the `mute` cue finishes, if the `reduce` cue is still active, volume restores smoothly to the reduced gain (e.g., `0.40`), NOT directly to `1.0`. Full volume restores only after all reduction cues end.
3. **Visual Precedence (`hide` > `blur` > `dim`):**
   - If multiple visual cues overlap, the highest priority action wins.
   - `hide = true` only when `visualAction === 'hide'`.
   - `dim` and `blur` actions set `visualAction` accordingly and keep `hide = false`. The Vega player logs an explicit unsupported action notice and surfaces a UI badge without blanking the video.
4. **Independent Modalities:**
   - Visual hiding does not mute audio.
   - Audio muting does not blank video.
5. **Warning Alerts vs. Blocking Actions:**
   - **Non-blocking warning alerts (`action: 'warn'`):** Populates `warnCueIds` with the IDs of active warning cues. In the player, this surfaces a non-disruptive heads-up warning banner without modifying audio volume (`soundGain`), video hiding (`hide`), or triggering seeks (`skipTo`).
   - **Blocking actions (`mute`, `reduce`, `hide`, `skip`):** Directly intervene in media presentation:
     - `mute`: zeroes audio volume (`soundGain = 0`).
     - `reduce`: lowers audio gain (`soundGain = cue.gain ?? options.reduceGain`).
     - `hide`: sets `hide = true` to blank the video element and display the privacy/protection overlay.
     - `skip`: computes `skipTo` to forward seek past the trigger interval.
6. **Canonical Action Vocabulary & Modalities:**
   - **Supported Actions:**
     - `hide`: Visual blackout overlay during active cue.
     - `mute`: Audio ramp down to 0.0 over 150ms.
     - `reduce`: Audio ramp down to specified `cue.gain` or `options.reduceGain` over 150ms. Subordinate to `mute` during overlap.
     - `skip`: Seek video playback forward past active trigger interval.
     - `warn`: Non-intrusive heads-up banner notification without media disruption.
   - **Faithfully Represented (No Phase 0 Renderer):**
     - `dim`: Brightness reduction (stored in metadata; logged and badged in HUD banner).
     - `blur`: Low-pass filter / spatial blur (stored in metadata; logged and badged in HUD banner).
   - *Note on `obscure`:* `obscure` is rejected across all schemas and engine code; `dim` and `blur` are retained as distinct tokens.
7. **Skip Evaluation & Review-Status Contract (`status: 'needs_review'`):**
   - Packaged cues optionally provide `status: 'ready' | 'needs_review'` (defaulting to `'ready'`).
   - Computes `skipTo = Math.max(...activeSkipCueEnds)` across active skip cues.
   - In `PlaybackState`, each skip cue fires at most once per forward playback pass. Rewinding past `cue.start` re-arms the skip cue.
   - **Protection Guardrail:** Any cue marked `status: 'needs_review'` **never skips automatically** without explicit viewer confirmation (`options.confirmReviewSkip === true`).
   - **Safe Fallback (`src/engine/reviewPolicy.ts`):** When `confirmReviewSkip === false` (the runtime default), unconfirmed review skip cues automatically downgrade:
     - Visual skip $\rightarrow$ `hide` (if `options.hideEnabled`)
     - Audio, text, or compound skip $\rightarrow$ `mute` (if `options.muteEnabled`)
     - Otherwise $\rightarrow$ `warn` (heads-up banner alert)

---

## 6. Viewer Preferences (`ViewerOptions`)

Viewer preferences allow enabling or disabling specific avoidance actions:

```typescript
export interface ViewerOptions {
  hideEnabled: boolean;        // Default: true
  muteEnabled: boolean;        // Default: true
  reduceEnabled: boolean;      // Default: false
  skipEnabled: boolean;        // Default: false
  reduceGain: number;          // Default: 0.3 (30% volume)
  confirmReviewSkip: boolean;  // Default: false
}
```

If an action is disabled (e.g., `muteEnabled: false`), cues of that action type are bypassed.

---

## 7. Error Handling & Validation Diagnostics

| Failure Scenario | Engine Behavior | Diagnostic Output |
| :--- | :--- | :--- |
| **Invalid Schema Version** | Throws `Error` | `Unsupported packaged index schema_version.` |
| **Non-Chronological Cues** | Throws `Error` | `Packaged cues must be in chronological order.` |
| **Malformed Interval ($end \le start$)** | Throws `Error` | `Packaged index contains an invalid cue "<id>".` |
| **Missing Trigger Policy** | Throws `Error` | `Missing policy for trigger "<trigger>".` |
| **Action Not in Allowed List** | Throws `Error` | `Action "<act>" is not allowed for trigger "<trigger>".` |
| **Disabled Trigger** | Graceful Skip | Cue omitted from adapted array; no error thrown. |
| **Unsupported Action (`dim`/`blur`)** | Handled Gracefully | Stored faithfully; logs renderer warning in player console. |

---

## 8. Verification & Test Commands

Person B can verify engine compatibility and test execution using the standard package scripts:

```bash
# Run TypeScript engine typecheck
npm run typecheck

# Run pure engine Jest tests (node environment)
npm test -- --runInBand

# Run all tests including PlayerScreen and UI components (react-native environment)
npm run test:rn -- --runInBand
```
