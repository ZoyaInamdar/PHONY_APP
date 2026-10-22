import { Cue, ViewerOptions } from '../engine/types';

export interface PromptScenario {
  id: string;
  name: string;
  description: string;
  cues: Cue[];
  options: Partial<ViewerOptions>;
  leadTimeSeconds: number;
  seekBeforeWindow: number;
  seekInWindow: number;
  seekCueStart: number;
  expectedSkipButton: boolean;
  expectedEffectiveAction: 'skip' | 'hide' | 'mute' | 'warn';
}

/**
 * 5 Deterministic Heads-Up Prompt Scenarios for runtime testing on Vega OS / Fire TV.
 *
 * Timings are calibrated against the 52.2s Sintel trailer stream to allow safe
 * playback, seeking, and rewind re-arming verification.
 */
export const PROMPT_HARNESS_SCENARIOS: PromptScenario[] = [
  // Scenario 1: An eligible skip cue (action: 'skip', status: 'verified')
  {
    id: 'eligible_skip',
    name: '1: Eligible Skip (Verified)',
    description:
      'Verified skip cue with lead time 5.0s. Prompt appears at [5.0s, 10.0s). Both Continue and Skip Scene buttons are visible. Skip jumps to 14.0s.',
    cues: [
      {
        id: 'harness-cue-skip-verified',
        start: 10.0,
        end: 14.0,
        source: 'visual',
        action: 'skip',
        trigger: 'gunshot',
        status: 'verified',
      },
    ],
    options: {
      skipEnabled: true,
      confirmReviewSkip: false,
      headsUpPrompts: {
        enabled: true,
        leadTimeSeconds: 5.0,
      },
    },
    leadTimeSeconds: 5.0,
    seekBeforeWindow: 3.0,
    seekInWindow: 6.0,
    seekCueStart: 10.0,
    expectedSkipButton: true,
    expectedEffectiveAction: 'skip',
  },

  // Scenario 2: A needs_review cue with confirmation disabled (confirmReviewSkip: false)
  {
    id: 'review_unconfirmed',
    name: '2: Review (Unconfirmed)',
    description:
      'Needs-review cue with confirmReviewSkip: false. Review policy automatically downgrades skip to visual hide fallback. Skip Scene button is hidden; blackout overlay activates at 20.0s.',
    cues: [
      {
        id: 'harness-cue-review-unconfirmed',
        start: 20.0,
        end: 24.0,
        source: 'visual',
        action: 'skip',
        trigger: 'insect',
        status: 'needs_review',
      },
    ],
    options: {
      skipEnabled: true,
      hideEnabled: true,
      confirmReviewSkip: false,
      headsUpPrompts: {
        enabled: true,
        leadTimeSeconds: 5.0,
      },
    },
    leadTimeSeconds: 5.0,
    seekBeforeWindow: 13.0,
    seekInWindow: 16.0,
    seekCueStart: 20.0,
    expectedSkipButton: false,
    expectedEffectiveAction: 'hide',
  },

  // Scenario 3: The same needs_review cue with confirmation enabled (confirmReviewSkip: true)
  {
    id: 'review_confirmed',
    name: '3: Review (Confirmed)',
    description:
      'Needs-review cue with confirmReviewSkip: true. User explicitly allows skip for review cues. Skip Scene button is visible and skips to 24.0s.',
    cues: [
      {
        id: 'harness-cue-review-confirmed',
        start: 20.0,
        end: 24.0,
        source: 'visual',
        action: 'skip',
        trigger: 'insect',
        status: 'needs_review',
      },
    ],
    options: {
      skipEnabled: true,
      hideEnabled: true,
      confirmReviewSkip: true,
      headsUpPrompts: {
        enabled: true,
        leadTimeSeconds: 5.0,
      },
    },
    leadTimeSeconds: 5.0,
    seekBeforeWindow: 13.0,
    seekInWindow: 16.0,
    seekCueStart: 20.0,
    expectedSkipButton: true,
    expectedEffectiveAction: 'skip',
  },

  // Scenario 4: A non-skip cue, such as visual hide (action: 'hide')
  {
    id: 'non_skip_hide',
    name: '4: Non-Skip (Hide)',
    description:
      'Visual hide cue. Prompt notifies of upcoming sensitive visual trigger. Skip Scene button is hidden; blackout fallback overlay activates at 30.0s.',
    cues: [
      {
        id: 'harness-cue-non-skip-hide',
        start: 30.0,
        end: 34.0,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
        status: 'verified',
      },
    ],
    options: {
      hideEnabled: true,
      headsUpPrompts: {
        enabled: true,
        leadTimeSeconds: 5.0,
      },
    },
    leadTimeSeconds: 5.0,
    seekBeforeWindow: 23.0,
    seekInWindow: 26.0,
    seekCueStart: 30.0,
    expectedSkipButton: false,
    expectedEffectiveAction: 'hide',
  },

  // Scenario 5: Chained overlapping/contiguous skip cues ([40, 44) and [43, 48))
  {
    id: 'chained_skip',
    name: '5: Chained Skip',
    description:
      'Overlapping/contiguous skip cues [40.0s, 44.0s) & [43.0s, 48.0s). Prompt for first cue appears at 35.0s. Skip Scene chains across both cues to 48.0s.',
    cues: [
      {
        id: 'harness-cue-chain-1',
        start: 40.0,
        end: 44.0,
        source: 'visual',
        action: 'skip',
        trigger: 'gunshot',
        status: 'verified',
      },
      {
        id: 'harness-cue-chain-2',
        start: 43.0,
        end: 48.0,
        source: 'visual',
        action: 'skip',
        trigger: 'explosion',
        status: 'verified',
      },
    ],
    options: {
      skipEnabled: true,
      headsUpPrompts: {
        enabled: true,
        leadTimeSeconds: 5.0,
      },
    },
    leadTimeSeconds: 5.0,
    seekBeforeWindow: 33.0,
    seekInWindow: 36.0,
    seekCueStart: 40.0,
    expectedSkipButton: true,
    expectedEffectiveAction: 'skip',
  },
];
