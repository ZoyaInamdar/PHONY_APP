import {
  aggregateAppliedActions,
  formatActionDisplayString,
  generateRecapDisplayStrings,
  AppliedAction,
} from '../../src/engine/recap';
import { PlaybackState } from '../../src/engine/playbackState';
import { Cue, DEFAULT_VIEWER_OPTIONS } from '../../src/engine/types';

describe('Dynamic Recap Aggregation & Display Strings', () => {
  it('aggregates applied actions by triggerType_appliedEffect key', () => {
    const actions: AppliedAction[] = [
      { triggerType: 'profanity', appliedEffect: 'muted' },
      { triggerType: 'profanity', appliedEffect: 'muted' },
      { triggerType: 'flash', appliedEffect: 'blacked_out' },
      { triggerType: 'flash', appliedEffect: 'blacked_out' },
      { triggerType: 'scene', appliedEffect: 'skipped' },
    ];

    const counts = aggregateAppliedActions(actions);
    expect(counts).toEqual({
      profanity_muted: 2,
      flash_blacked_out: 2,
      scene_skipped: 1,
    });
  });

  it('generates exact formatted strings as requested in specifications', () => {
    expect(formatActionDisplayString('profanity_muted', 12)).toBe(
      '12 profanities muted',
    );
    expect(formatActionDisplayString('profanity_muted', 1)).toBe(
      '1 profanity muted',
    );

    // Verify it says "blacked out" instead of "blurred" if engine reports blackout
    expect(formatActionDisplayString('flash_blacked_out', 2)).toBe(
      '2 flash scenes blacked out',
    );
    expect(formatActionDisplayString('flash_blacked_out', 1)).toBe(
      '1 flash scene blacked out',
    );

    expect(formatActionDisplayString('scene_skipped', 1)).toBe(
      '1 scene skipped',
    );
    expect(formatActionDisplayString('scene_skipped', 3)).toBe(
      '3 scenes skipped',
    );
  });

  it('correctly outputs display strings from a session list', () => {
    const sessionActions: AppliedAction[] = [
      ...Array(12).fill({ triggerType: 'profanity', appliedEffect: 'muted' }),
      ...Array(2).fill({ triggerType: 'flash', appliedEffect: 'blacked_out' }),
      { triggerType: 'scene', appliedEffect: 'skipped' },
    ];

    const strings = generateRecapDisplayStrings(sessionActions);
    expect(strings).toContain('12 profanities muted');
    expect(strings).toContain('2 flash scenes blacked out');
    expect(strings).toContain('1 scene skipped');
  });

  it('handles empty session actions cleanly', () => {
    expect(generateRecapDisplayStrings([])).toEqual([]);
  });

  it('tracks applied actions in PlaybackState and provides getSessionPayload()', () => {
    const state = new PlaybackState();
    const cues: Cue[] = [
      {
        id: 'c1',
        start: 2,
        end: 5,
        source: 'text',
        action: 'mute',
        trigger: 'profanity',
      },
      {
        id: 'c2',
        start: 6,
        end: 9,
        source: 'visual',
        action: 'hide',
        trigger: 'flash',
      },
      {
        id: 'c3',
        start: 10,
        end: 15,
        source: 'visual',
        action: 'skip',
        trigger: 'spider',
      },
    ];

    const options = {
      ...DEFAULT_VIEWER_OPTIONS,
      skipEnabled: true,
      muteEnabled: true,
      hideEnabled: true,
    };

    // Time update inside profanity mute cue
    state.resolveAt(3, cues, options);
    // Time update inside flash hide cue
    state.resolveAt(7, cues, options);
    // Time update inside skip cue
    state.resolveAt(11, cues, options);

    const payload = state.getSessionPayload();
    expect(payload.appliedActions.length).toBe(3);

    const strings = generateRecapDisplayStrings(payload.appliedActions);
    expect(strings).toContain('1 profanity muted');
    expect(strings).toContain('1 flash scene blacked out');
    expect(strings).toContain('1 spider scene skipped');
  });
});
