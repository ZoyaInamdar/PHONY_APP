import { Cue, DEFAULT_VIEWER_OPTIONS, ViewerOptions } from '../engine/types';
import { adaptPackagedIndex } from '../engine/packagedIndexAdapter';
import { DEFAULT_TRIGGER_POLICIES } from './defaultTriggerPolicies';

// Load default detection index
// eslint-disable-next-line @typescript-eslint/no-var-requires
const sintelIndexJson = require('../../data/index.json');
// eslint-disable-next-line @typescript-eslint/no-var-requires
const tearsOfSteelIndexJson = require('../../data/tears_of_steel_index.json');

/**
 * Standard cues adapted from the repository's packaged index (data/index.json)
 * using the configured trigger policies.
 */
export const sintelIndexCues: Cue[] = adaptPackagedIndex(
  sintelIndexJson,
  DEFAULT_TRIGGER_POLICIES,
);

/**
 * Standard cues adapted from Tears of Steel packaged index (data/tears_of_steel_index.json).
 */
export const tearsOfSteelIndexCues: Cue[] = adaptPackagedIndex(
  tearsOfSteelIndexJson,
  DEFAULT_TRIGGER_POLICIES,
);

/**
 * Resolves cues for a given film_id from the film catalogue.
 */
export function getCuesForFilm(filmId: string): Cue[] {
  if (filmId === 'sintel') {
    return sintelIndexCues;
  }
  if (filmId === 'tears_of_steel') {
    return tearsOfSteelIndexCues;
  }
  throw new Error(`Unknown film ID: "${filmId}". Supported films: "sintel", "tears_of_steel".`);
}

/**
 * Temporary, deterministic test cue fixture specifically crafted to verify:
 * 1. Audio Mute [6.0s - 10.0s)
 * 2. Audio Volume Reduction [13.0s - 18.0s) with 35% gain
 * 3. Visual Hiding with Fallback Overlay [21.0s - 26.0s)
 * 4. Multi-Cue Overlap & Precedence [29.0s - 38.0s):
 *    - Visual Hide [29.0s - 35.0s)
 *    - Audio Reduce (gain 0.4) [30.0s - 37.0s)
 *    - Audio Mute [32.0s - 34.5s)
 *      -> Mute overrides Reduce during [32.0, 34.5).
 *      -> Volume restores to 0.4 at 34.5s while reduce is still active.
 *      -> Visual returns at 35.0s.
 *      -> Volume restores to 1.0 at 37.0s when reduce ends.
 */
export const verificationTestCues: Cue[] = [
  // 1. Standalone Mute Cue
  {
    id: 'cue-verify-mute',
    start: 6.0,
    end: 10.0,
    source: 'audio',
    action: 'mute',
    trigger: 'loud_spike',
    status: 'ready',
  },
  // 2. Standalone Reduce Cue (volume set to 35%)
  {
    id: 'cue-verify-reduce',
    start: 13.0,
    end: 18.0,
    source: 'audio',
    action: 'reduce',
    gain: 0.35,
    trigger: 'scream',
    status: 'ready',
  },
  // 3. Standalone Visual Hide Cue (video hidden, audio continues)
  {
    id: 'cue-verify-hide',
    start: 21.0,
    end: 26.0,
    source: 'visual',
    action: 'hide',
    trigger: 'flashing',
    status: 'ready',
  },
  // 4. Overlapping Cues (testing precedence and nested restorations)
  {
    id: 'cue-verify-overlap-hide',
    start: 29.0,
    end: 35.0,
    source: 'visual',
    action: 'hide',
    trigger: 'spider',
    status: 'ready',
  },
  {
    id: 'cue-verify-overlap-reduce',
    start: 30.0,
    end: 37.0,
    source: 'audio',
    action: 'reduce',
    gain: 0.4,
    trigger: 'loud_spike',
    status: 'ready',
  },
  {
    id: 'cue-verify-overlap-mute',
    start: 32.0,
    end: 34.5,
    source: 'audio',
    action: 'mute',
    trigger: 'gunshot',
    status: 'ready',
  },
];

/**
 * Dedicated, opt-in test cue fixture specifically crafted to verify Skip playback on Vega:
 * 1. Ready Skip Cue [10.0s - 18.0s):
 *    - Jumps forward past the trigger interval to 18.0s when skipEnabled is true.
 *    - Does NOT jump when skipEnabled is false.
 * 2. Destination Action Cue [18.0s - 24.0s):
 *    - Immediately activates audio mute at the seek destination (18.0s) to verify
 *      destination action synchronization and state restoration.
 * 3. Review-Required Skip Cue [30.0s - 36.0s):
 *    - status: 'needs_review'
 *    - Proves that unconfirmed review cues do NOT trigger a seek, falling back
 *      safely to visual hiding.
 */
export const skipVerificationTestCues: Cue[] = [
  // 1. Ready Skip Cue: triggers forward seek past interval [10.0, 18.0)
  {
    id: 'cue-skip-verify-main',
    start: 10.0,
    end: 18.0,
    source: 'visual',
    action: 'skip',
    trigger: 'flashing',
    status: 'ready',
  },
  // 2. Destination Action: active immediately at seek destination [18.0, 24.0)
  {
    id: 'cue-skip-verify-destination-mute',
    start: 18.0,
    end: 24.0,
    source: 'audio',
    action: 'mute',
    trigger: 'gunshot',
    status: 'ready',
  },
  // 3. Review-Required Skip Cue: must not seek without confirmation [30.0, 36.0)
  {
    id: 'cue-skip-verify-needs-review',
    start: 30.0,
    end: 36.0,
    source: 'visual',
    action: 'skip',
    trigger: 'spider',
    status: 'needs_review',
  },
];

/**
 * Default options with both mute, hide, and reduce explicitly enabled.
 */
export const defaultPlaybackOptions: ViewerOptions = {
  ...DEFAULT_VIEWER_OPTIONS,
  hideEnabled: true,
  muteEnabled: true,
  reduceEnabled: true,
  reduceGain: 0.3,
};
