import { resolveActions } from '../../src/engine/resolveActions';
import { PlaybackState } from '../../src/engine/playbackState';
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
      trigger: 'jump_scare',
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
      trigger: 'insect',
    };

    const dimResult = resolveActions(42.0, [dimCue], DEFAULT_VIEWER_OPTIONS);
    expect(dimResult.visualAction).toBe('dim');
    expect(dimResult.hide).toBe(false);

    const blurResult = resolveActions(52.0, [blurCue], DEFAULT_VIEWER_OPTIONS);
    expect(blurResult.visualAction).toBe('blur');
    expect(blurResult.hide).toBe(false);
  });

  describe('disabledCategories viewer preferences', () => {
    const multiCategoryCues: Cue[] = [
      {
        id: 'cue-spike',
        start: 10,
        end: 15,
        source: 'audio',
        action: 'mute',
        trigger: 'loud_spike',
      },
      {
        id: 'cue-flash',
        start: 10,
        end: 15,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
      },
      {
        id: 'cue-profanity',
        start: 12,
        end: 14,
        source: 'text',
        action: 'mute',
        trigger: 'profanity',
      },
    ];

    it('filters out disabled trigger categories while keeping other triggers active', () => {
      // Disable loud_spike: flashing (hide) and profanity (mute) should still resolve
      const res = resolveActions(13.0, multiCategoryCues, {
        ...DEFAULT_VIEWER_OPTIONS,
        disabledCategories: ['loud_spike'],
      });

      expect(res.hide).toBe(true); // flashing is active
      expect(res.soundGain).toBe(0); // profanity is active
      expect(res.activeCueIds).toEqual(['cue-flash', 'cue-profanity']);
      expect(res.activeCueIds).not.toContain('cue-spike');
    });

    it('disabling all active categories results in clear playback state', () => {
      const res = resolveActions(13.0, multiCategoryCues, {
        ...DEFAULT_VIEWER_OPTIONS,
        disabledCategories: ['loud_spike', 'flashing', 'profanity'],
      });

      expect(res.hide).toBe(false);
      expect(res.visualAction).toBeNull();
      expect(res.soundGain).toBe(1);
      expect(res.activeCueIds).toEqual([]);
    });

    it('does not filter cues when disabledCategories is empty or undefined', () => {
      const res = resolveActions(13.0, multiCategoryCues, {
        ...DEFAULT_VIEWER_OPTIONS,
        disabledCategories: [],
      });

      expect(res.hide).toBe(true);
      expect(res.soundGain).toBe(0);
      expect(res.activeCueIds).toEqual(['cue-spike', 'cue-flash', 'cue-profanity']);
    });
  });

  describe('Per-Trigger Reduce/Skip Override Gating Regression Suite', () => {
    it('1. applies reduction for an eligible trigger with an explicit reduce override while reduceEnabled === false', () => {
      // gunshot default is mute, allowed alternatives: ['reduce', 'skip', 'warn']
      const gunshotCue: Cue = {
        id: 'cue-gunshot-reduce',
        start: 10,
        end: 15,
        source: 'audio',
        action: 'mute',
        trigger: 'gunshot',
        gain: 0.35,
      };
      const result = resolveActions(12, [gunshotCue], {
        ...DEFAULT_VIEWER_OPTIONS,
        reduceEnabled: false, // global toggle is false!
        actionOverrides: { gunshot: 'reduce' },
      });
      expect(result.soundGain).toBe(0.35);
    });

    it('2. applies reduction for the same explicit override when reduceEnabled === true', () => {
      const gunshotCue: Cue = {
        id: 'cue-gunshot-reduce',
        start: 10,
        end: 15,
        source: 'audio',
        action: 'mute',
        trigger: 'gunshot',
        gain: 0.35,
      };
      const result = resolveActions(12, [gunshotCue], {
        ...DEFAULT_VIEWER_OPTIONS,
        reduceEnabled: true,
        actionOverrides: { gunshot: 'reduce' },
      });
      expect(result.soundGain).toBe(0.35);
    });

    it('3. executes skip for an eligible trigger with an explicit skip override while skipEnabled === false', () => {
      // flashing default is hide, allowed alternatives: ['skip', 'warn']
      const flashCue: Cue = {
        id: 'cue-flash-skip',
        start: 20,
        end: 25,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
      };
      const result = resolveActions(22, [flashCue], {
        ...DEFAULT_VIEWER_OPTIONS,
        skipEnabled: false, // global toggle is false!
        actionOverrides: { flashing: 'skip' },
      });
      expect(result.skipTo).toBe(25);
      expect(result.visualAction).toBeNull();
    });

    it('4. executes skip for the same explicit skip override when skipEnabled === true', () => {
      const flashCue: Cue = {
        id: 'cue-flash-skip',
        start: 20,
        end: 25,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
      };
      const result = resolveActions(22, [flashCue], {
        ...DEFAULT_VIEWER_OPTIONS,
        skipEnabled: true,
        actionOverrides: { flashing: 'skip' },
      });
      expect(result.skipTo).toBe(25);
      expect(result.visualAction).toBeNull();
    });

    it('5. retains existing global-toggle behavior for a default action when no explicit override is set', () => {
      // explosion defaultAction is 'reduce', gain 0.2
      const explosionCue: Cue = {
        id: 'cue-explosion-default',
        start: 30,
        end: 35,
        source: 'audio',
        action: 'reduce',
        trigger: 'explosion',
        gain: 0.2,
      };
      // A. With reduceEnabled === false and no override, reduction is NOT applied
      const resDisabled = resolveActions(32, [explosionCue], {
        ...DEFAULT_VIEWER_OPTIONS,
        reduceEnabled: false,
        actionOverrides: {},
      });
      expect(resDisabled.soundGain).toBe(1);

      // B. With reduceEnabled === true and no override, reduction IS applied
      const resEnabled = resolveActions(32, [explosionCue], {
        ...DEFAULT_VIEWER_OPTIONS,
        reduceEnabled: true,
        actionOverrides: {},
      });
      expect(resEnabled.soundGain).toBe(0.2);
    });

    it('6. retains correct behavior for an explicit action other than reduce or skip (e.g. blur or warn)', () => {
      // insect default is 'hide', allowed: ['blur', 'skip', 'warn']
      const insectCue: Cue = {
        id: 'cue-insect-blur',
        start: 40,
        end: 45,
        source: 'visual',
        action: 'hide',
        trigger: 'insect',
      };
      const resBlur = resolveActions(42, [insectCue], {
        ...DEFAULT_VIEWER_OPTIONS,
        hideEnabled: true,
        actionOverrides: { insect: 'blur' },
      });
      expect(resBlur.visualAction).toBe('blur');
      expect(resBlur.hide).toBe(false);

      // jump_scare default is 'warn', override to 'hide'
      const motionCue: Cue = {
        id: 'cue-motion-hide',
        start: 50,
        end: 55,
        source: 'visual',
        action: 'warn',
        trigger: 'jump_scare',
      };
      const resHide = resolveActions(52, [motionCue], {
        ...DEFAULT_VIEWER_OPTIONS,
        hideEnabled: true,
        actionOverrides: { jump_scare: 'hide' },
      });
      expect(resHide.visualAction).toBe('hide');
      expect(resHide.hide).toBe(true);
    });

    it('7. ensures a disallowed override cannot bypass trigger-contract validation and falls back to default action', () => {
      // loud_spike default is 'reduce', allowed alternatives are ['mute', 'warn']. 'skip' is disallowed!
      const spikeCue: Cue = {
        id: 'cue-spike-disallowed',
        start: 60,
        end: 65,
        source: 'audio',
        action: 'reduce',
        trigger: 'loud_spike',
        gain: 0.3,
      };
      const result = resolveActions(62, [spikeCue], {
        ...DEFAULT_VIEWER_OPTIONS,
        skipEnabled: false,
        reduceEnabled: false,
        actionOverrides: { loud_spike: 'skip' as any }, // Disallowed action!
      });
      // Disallowed 'skip' falls back to default 'reduce'.
      // Since reduce is the default and reduceEnabled is false, soundGain remains 1 and skipTo remains null.
      expect(result.skipTo).toBeNull();
      expect(result.soundGain).toBe(1);
    });

    it('8. ensures mute takes precedence over reduction when overlapping cues require both', () => {
      const reduceCue: Cue = {
        id: 'cue-override-reduce',
        start: 70,
        end: 80,
        source: 'audio',
        action: 'mute',
        trigger: 'gunshot',
        gain: 0.3,
      };
      const muteCue: Cue = {
        id: 'cue-profanity-mute',
        start: 72,
        end: 75,
        source: 'text',
        action: 'mute',
        trigger: 'profanity',
      };
      const options = {
        ...DEFAULT_VIEWER_OPTIONS,
        reduceEnabled: false, // override reduce
        muteEnabled: true,
        actionOverrides: { gunshot: 'reduce' as const },
      };

      // At t=71, only gunshot is active -> reduction (0.3)
      const res1 = resolveActions(71, [reduceCue, muteCue], options);
      expect(res1.soundGain).toBe(0.3);

      // At t=73, both are active -> mute (0) overrides reduction!
      const res2 = resolveActions(73, [reduceCue, muteCue], options);
      expect(res2.soundGain).toBe(0);

      // At t=76, mute cue ended -> reduction (0.3) restored
      const res3 = resolveActions(76, [reduceCue, muteCue], options);
      expect(res3.soundGain).toBe(0.3);
    });

    it('9. ends reduction correctly when a cue ends, including when other non-audio cues remain active', () => {
      const reduceCue: Cue = {
        id: 'cue-reduce-end',
        start: 80,
        end: 85,
        source: 'audio',
        action: 'mute',
        trigger: 'gunshot',
        gain: 0.4,
      };
      const visualCue: Cue = {
        id: 'cue-visual-continue',
        start: 80,
        end: 90,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
      };
      const options = {
        ...DEFAULT_VIEWER_OPTIONS,
        reduceEnabled: false,
        hideEnabled: true,
        actionOverrides: { gunshot: 'reduce' as const },
      };

      // During both cues at t=82
      const during = resolveActions(82, [reduceCue, visualCue], options);
      expect(during.soundGain).toBe(0.4);
      expect(during.hide).toBe(true);

      // After reduce cue ends at t=86 (visual cue still active)
      const afterReduce = resolveActions(86, [reduceCue, visualCue], options);
      expect(afterReduce.soundGain).toBe(1.0);
      expect(afterReduce.hide).toBe(true);

      // After all cues end at t=91
      const allEnded = resolveActions(91, [reduceCue, visualCue], options);
      expect(allEnded.soundGain).toBe(1.0);
      expect(allEnded.hide).toBe(false);
    });

    it('10. ensures skip remains deduplicated and does not repeatedly seek during the same approach using PlaybackState', () => {
      const playbackState = new PlaybackState();
      const skipCue: Cue = {
        id: 'cue-skip-dedup',
        start: 100,
        end: 105,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
      };
      const options = {
        ...DEFAULT_VIEWER_OPTIONS,
        skipEnabled: false, // global skip is false
        actionOverrides: { flashing: 'skip' as const },
      };

      // 1. Initial approach at t=101: skip fires and seeks to 105
      const first = playbackState.resolveAt(101, [skipCue], options);
      expect(first.skipTo).toBe(105);
      expect(playbackState.hasSkipFired('cue-skip-dedup')).toBe(true);

      // 2. Playback lands at t=101.5 or t=102 on same approach: does NOT re-fire
      const second = playbackState.resolveAt(102, [skipCue], options);
      expect(second.skipTo).toBeNull();
    });

    it('11. retains existing skip-reset semantics when rewinding and replaying using PlaybackState', () => {
      const playbackState = new PlaybackState();
      const skipCue: Cue = {
        id: 'cue-skip-rewind',
        start: 110,
        end: 115,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
      };
      const options = {
        ...DEFAULT_VIEWER_OPTIONS,
        skipEnabled: false,
        actionOverrides: { flashing: 'skip' as const },
      };

      // 1. Skip fires at t=111
      const res1 = playbackState.resolveAt(111, [skipCue], options);
      expect(res1.skipTo).toBe(115);
      expect(playbackState.hasSkipFired('cue-skip-rewind')).toBe(true);

      // 2. User rewinds back to t=100 (before cue.start)
      playbackState.seek(100, [skipCue], options);
      expect(playbackState.hasSkipFired('cue-skip-rewind')).toBe(false);

      // 3. Playback advances forward again to t=111: skip re-arms and fires again!
      const res2 = playbackState.resolveAt(111, [skipCue], options);
      expect(res2.skipTo).toBe(115);
      expect(playbackState.hasSkipFired('cue-skip-rewind')).toBe(true);
    });

    it('12. ensures multiple overlapping cues with mixed overrides and defaults resolve with deterministic precedence', () => {
      const multiCues: Cue[] = [
        { id: 'c-flash-skip', start: 120, end: 128, source: 'visual', action: 'hide', trigger: 'flashing' },
        { id: 'c-gunshot-reduce', start: 122, end: 126, source: 'audio', action: 'mute', trigger: 'gunshot', gain: 0.25 },
        { id: 'c-spike-default', start: 123, end: 125, source: 'audio', action: 'reduce', trigger: 'loud_spike', gain: 0.5 },
      ];
      const options = {
        ...DEFAULT_VIEWER_OPTIONS,
        skipEnabled: false,
        reduceEnabled: false,
        actionOverrides: {
          flashing: 'skip' as const,
          gunshot: 'reduce' as const,
        },
      };

      // At t=124:
      // - flashing has explicit skip override -> skipTo: 128
      // - gunshot has explicit reduce override -> soundGain: 0.25
      // - loud_spike has no override (default reduce), and reduceEnabled is false -> ignored
      const res = resolveActions(124, multiCues, options);
      expect(res.skipTo).toBe(128);
      expect(res.soundGain).toBe(0.25);
      expect(res.visualAction).toBeNull();
    });
  });
});
