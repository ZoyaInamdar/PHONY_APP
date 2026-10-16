import { resolveActions } from '../../src/engine/resolveActions';
import {
  Cue,
  DEFAULT_VIEWER_OPTIONS,
} from '../../src/engine/types';

const cues: Cue[] = [
  {
    id: 'visual-1',
    start: 5,
    end: 10,
    source: 'visual',
    action: 'hide',
  },
  {
    id: 'audio-1',
    start: 6,
    end: 9,
    source: 'audio',
    action: 'reduce',
    gain: 0.4,
  },
  {
    id: 'audio-2',
    start: 7,
    end: 8,
    source: 'audio',
    action: 'mute',
  },
  {
    id: 'skip-1',
    start: 7,
    end: 12,
    source: 'visual',
    action: 'skip',
  },
];

describe('resolveActions', () => {
  it('hides visual content while keeping audio independent', () => {
    const result = resolveActions(5.5, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
    });

    expect(result.hide).toBe(true);
    expect(result.soundGain).toBe(1);
    expect(result.skipTo).toBeNull();
  });

  it('uses the configured reduction gain when enabled', () => {
    const result = resolveActions(6.5, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
      reduceEnabled: true,
    });

    expect(result.hide).toBe(true);
    expect(result.soundGain).toBe(0.4);
  });

  it('gives mute priority over reduction', () => {
    const result = resolveActions(7.5, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
      reduceEnabled: true,
      skipEnabled: true,
    });

    expect(result.soundGain).toBe(0);
    expect(result.skipTo).toBe(12);
  });

  it('does not skip unless the viewer enables skipping', () => {
    const result = resolveActions(7.5, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
      skipEnabled: false,
    });

    expect(result.skipTo).toBeNull();
  });

  it('does not skip a needs_review cue without confirmation', () => {
    const reviewCue: Cue = {
      id: 'review-skip',
      start: 1,
      end: 4,
      source: 'visual',
      action: 'skip',
      status: 'needs_review',
    };

    const result = resolveActions(2, [reviewCue], {
      ...DEFAULT_VIEWER_OPTIONS,
      skipEnabled: true,
    });

    expect(result.skipTo).toBeNull();
  });

  it('rejects invalid playback time', () => {
    expect(() =>
      resolveActions(-1, [], DEFAULT_VIEWER_OPTIONS),
    ).toThrow('Playback time must be a finite, non-negative number.');
  });

  it('treats cue start as inclusive', () => {
    const result = resolveActions(5, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
    });

    expect(result.hide).toBe(true);
    expect(result.activeCueIds).toContain('visual-1');
  });

  it('treats cue end as exclusive', () => {
    const result = resolveActions(10, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
    });

    expect(result.hide).toBe(false);
  });

  it('populates warnCueIds when a warn cue is active', () => {
    const warnCue: Cue = {
      id: 'warn-1',
      start: 20,
      end: 25,
      source: 'visual',
      action: 'warn',
      trigger: 'fast_motion',
    };

    const before = resolveActions(19.9, [warnCue], DEFAULT_VIEWER_OPTIONS);
    expect(before.warnCueIds).toEqual([]);

    const during = resolveActions(22.0, [warnCue], DEFAULT_VIEWER_OPTIONS);
    expect(during.warnCueIds).toEqual(['warn-1']);
    expect(during.hide).toBe(false);
    expect(during.soundGain).toBe(1);

    const after = resolveActions(25.0, [warnCue], DEFAULT_VIEWER_OPTIONS);
    expect(after.warnCueIds).toEqual([]);
  });

  it('does not allow warning cues to override mute, reduce, or hide actions', () => {
    const mixedCues: Cue[] = [
      {
        id: 'mute-1',
        start: 30,
        end: 35,
        source: 'audio',
        action: 'mute',
        trigger: 'loud_spike',
      },
      {
        id: 'warn-2',
        start: 32,
        end: 37,
        source: 'visual',
        action: 'warn',
        trigger: 'jump_scare',
      },
    ];

    const overlapping = resolveActions(33.0, mixedCues, {
      ...DEFAULT_VIEWER_OPTIONS,
      muteEnabled: true,
    });

    expect(overlapping.soundGain).toBe(0); // Audio remains muted!
    expect(overlapping.warnCueIds).toEqual(['warn-2']); // Warning is tracked!
    expect(overlapping.activeCueIds).toEqual(['mute-1', 'warn-2']);
  });

  it('faithfully preserves dim and blur without converting them to hide', () => {
    const dimCue: Cue = {
      id: 'dim-1',
      start: 40,
      end: 45,
      source: 'visual',
      action: 'dim',
      trigger: 'flashing',
    };
    const blurCue: Cue = {
      id: 'blur-1',
      start: 50,
      end: 55,
      source: 'visual',
      action: 'blur',
      trigger: 'spider',
    };

    const dimResult = resolveActions(42.0, [dimCue], DEFAULT_VIEWER_OPTIONS);
    expect(dimResult.visualAction).toBe('dim');
    expect(dimResult.hide).toBe(false);

    const blurResult = resolveActions(52.0, [blurCue], DEFAULT_VIEWER_OPTIONS);
    expect(blurResult.visualAction).toBe('blur');
    expect(blurResult.hide).toBe(false);
  });
});
