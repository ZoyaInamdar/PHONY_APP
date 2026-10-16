import { Cue, DEFAULT_VIEWER_OPTIONS, ViewerOptions } from '../engine/types';
import { adaptPackagedIndex } from '../engine/packagedIndexAdapter';

// Load default detection index and policies
// eslint-disable-next-line @typescript-eslint/no-var-requires
const sintelIndexJson = require('../../data/index.json');
// eslint-disable-next-line @typescript-eslint/no-var-requires
const triggerPoliciesJson = require('../../test/fixtures/triggerPolicies.json');

/**
 * Standard cues adapted from the repository's packaged index (data/index.json)
 * using the configured trigger policies.
 */
export const sintelIndexCues: Cue[] = adaptPackagedIndex(
  sintelIndexJson,
  triggerPoliciesJson.policies,
);

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
 * Default options with both mute, hide, and reduce explicitly enabled.
 */
export const defaultPlaybackOptions: ViewerOptions = {
  ...DEFAULT_VIEWER_OPTIONS,
  hideEnabled: true,
  muteEnabled: true,
  reduceEnabled: true,
  reduceGain: 0.3,
};
