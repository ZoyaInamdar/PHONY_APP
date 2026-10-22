import { PlaybackState } from '../../src/engine/playbackState';
import { resolveActions } from '../../src/engine/resolveActions';
import { Cue, DEFAULT_VIEWER_OPTIONS, ViewerOptions } from '../../src/engine/types';
import { verificationTestCues, defaultPlaybackOptions } from '../../src/data/playbackCues';

describe('Trigger-Aware Playback Actions — Unit & Integration Engine Tests', () => {
  let playbackState: PlaybackState;
  const options: ViewerOptions = {
    ...defaultPlaybackOptions,
  };

  beforeEach(() => {
    playbackState = new PlaybackState();
  });

  // ---------------------------------------------------------------------------
  // 1. Mute Activation and Restoration
  // ---------------------------------------------------------------------------
  describe('Mute activation and restoration', () => {
    it('is audible before cue start, muted during cue, and restored after cue ends', () => {
      // Cue: [6.0, 10.0) mute
      // 1. Just before cue starts
      const before = playbackState.resolveAt(5.9, verificationTestCues, options);
      expect(before.soundGain).toBe(1);
      expect(before.activeCueIds).not.toContain('cue-verify-mute');

      // 2. Exactly at cue start (inclusive)
      const atStart = playbackState.resolveAt(6.0, verificationTestCues, options);
      expect(atStart.soundGain).toBe(0);
      expect(atStart.activeCueIds).toContain('cue-verify-mute');

      // 3. During cue
      const during = playbackState.resolveAt(8.0, verificationTestCues, options);
      expect(during.soundGain).toBe(0);

      // 4. Exactly at cue end (exclusive) — audio restored
      const atEnd = playbackState.resolveAt(10.0, verificationTestCues, options);
      expect(atEnd.soundGain).toBe(1);
      expect(atEnd.activeCueIds).not.toContain('cue-verify-mute');

      // 5. Well after cue ends
      const after = playbackState.resolveAt(11.0, verificationTestCues, options);
      expect(after.soundGain).toBe(1);
    });

    it('does not mute if muteEnabled is false', () => {
      const disabledOptions = { ...options, muteEnabled: false };
      const during = playbackState.resolveAt(7.0, verificationTestCues, disabledOptions);
      expect(during.soundGain).toBe(1);
    });

    it('does not disable video or visual display during audio mute', () => {
      const during = playbackState.resolveAt(7.0, verificationTestCues, options);
      expect(during.soundGain).toBe(0);
      expect(during.hide).toBe(false);
      expect(during.visualAction).toBeNull();
    });
  });

  // ---------------------------------------------------------------------------
  // 2. Reduce Activation and Restoration
  // ---------------------------------------------------------------------------
  describe('Reduce activation and restoration', () => {
    it('applies configured reduction gain during cue and restores volume after cue ends', () => {
      // Cue: [13.0, 18.0) reduce gain: 0.35
      // 1. Before cue
      const before = playbackState.resolveAt(12.9, verificationTestCues, options);
      expect(before.soundGain).toBe(1);

      // 2. At cue start
      const atStart = playbackState.resolveAt(13.0, verificationTestCues, options);
      expect(atStart.soundGain).toBe(0.35);
      expect(atStart.activeCueIds).toContain('cue-verify-reduce');

      // 3. During cue
      const during = playbackState.resolveAt(15.5, verificationTestCues, options);
      expect(during.soundGain).toBe(0.35);

      // 4. At cue end — restored
      const atEnd = playbackState.resolveAt(18.0, verificationTestCues, options);
      expect(atEnd.soundGain).toBe(1);
      expect(atEnd.activeCueIds).not.toContain('cue-verify-reduce');
    });

    it('falls back to options.reduceGain when cue does not specify gain', () => {
      const cueWithoutGain: Cue = {
        id: 'no-gain-reduce',
        start: 1,
        end: 5,
        source: 'audio',
        action: 'reduce',
      };
      const customOptions = { ...options, reduceGain: 0.25 };
      const result = resolveActions(2, [cueWithoutGain], customOptions);
      expect(result.soundGain).toBe(0.25);
    });

    it('does not reduce volume if reduceEnabled is false', () => {
      const disabledOptions = { ...options, reduceEnabled: false };
      const during = playbackState.resolveAt(15.0, verificationTestCues, disabledOptions);
      expect(during.soundGain).toBe(1);
    });
  });

  // ---------------------------------------------------------------------------
  // 3. Visual Hiding Activation and Restoration
  // ---------------------------------------------------------------------------
  describe('Visual hiding activation and restoration', () => {
    it('hides visual content only during cue interval and restores when cue ends', () => {
      // Cue: [21.0, 26.0) visual hide
      // 1. Before cue
      const before = playbackState.resolveAt(20.9, verificationTestCues, options);
      expect(before.hide).toBe(false);
      expect(before.visualAction).toBeNull();

      // 2. At cue start
      const atStart = playbackState.resolveAt(21.0, verificationTestCues, options);
      expect(atStart.hide).toBe(true);
      expect(atStart.visualAction).toBe('hide');
      expect(atStart.activeCueIds).toContain('cue-verify-hide');

      // 3. During cue: audio remains unaffected (soundGain=1) while video is hidden
      const during = playbackState.resolveAt(24.0, verificationTestCues, options);
      expect(during.hide).toBe(true);
      expect(during.visualAction).toBe('hide');
      expect(during.soundGain).toBe(1);

      // 4. At cue end
      const atEnd = playbackState.resolveAt(26.0, verificationTestCues, options);
      expect(atEnd.hide).toBe(false);
      expect(atEnd.visualAction).toBeNull();
      expect(atEnd.activeCueIds).not.toContain('cue-verify-hide');
    });

    it('does not hide video if hideEnabled is false', () => {
      const disabledOptions = { ...options, hideEnabled: false };
      const during = playbackState.resolveAt(23.0, verificationTestCues, disabledOptions);
      expect(during.hide).toBe(false);
      expect(during.visualAction).toBeNull();
    });
  });

  // ---------------------------------------------------------------------------
  // 4. Overlapping Cues and Action Precedence
  // ---------------------------------------------------------------------------
  describe('Overlapping cues and action precedence', () => {
    // Overlapping block in verificationTestCues:
    //   - Visual Hide:   [29.0, 35.0)
    //   - Audio Reduce:  [30.0, 37.0) gain: 0.4
    //   - Audio Mute:    [32.0, 34.5)
    it('resolves precedence correctly across overlapping visual, reduce, and mute cues', () => {
      // At t=29.5: Visual hidden, audio full volume
      const t29_5 = playbackState.resolveAt(29.5, verificationTestCues, options);
      expect(t29_5.hide).toBe(true);
      expect(t29_5.soundGain).toBe(1);

      // At t=30.5: Visual hidden, audio reduced to 0.4
      const t30_5 = playbackState.resolveAt(30.5, verificationTestCues, options);
      expect(t30_5.hide).toBe(true);
      expect(t30_5.soundGain).toBe(0.4);

      // At t=33.0: Visual hidden, audio MUTED (mute overrides reduce!)
      const t33_0 = playbackState.resolveAt(33.0, verificationTestCues, options);
      expect(t33_0.hide).toBe(true);
      expect(t33_0.soundGain).toBe(0);

      // At t=34.8: Mute ended, but Reduce is still active!
      // Volume MUST restore to 0.4, NOT abruptly to 1.0!
      const t34_8 = playbackState.resolveAt(34.8, verificationTestCues, options);
      expect(t34_8.hide).toBe(true);
      expect(t34_8.soundGain).toBe(0.4);

      // At t=35.5: Visual hide ended (hide=false), but Reduce is still active (0.4)
      const t35_5 = playbackState.resolveAt(35.5, verificationTestCues, options);
      expect(t35_5.hide).toBe(false);
      expect(t35_5.soundGain).toBe(0.4);

      // At t=37.0: Reduce ended -> audio completely restored to 1.0!
      const t37_0 = playbackState.resolveAt(37.0, verificationTestCues, options);
      expect(t37_0.hide).toBe(false);
      expect(t37_0.soundGain).toBe(1);
    });

    it('takes the minimum gain when multiple reduce cues overlap', () => {
      const overlappingReduces: Cue[] = [
        { id: 'r1', start: 1, end: 10, source: 'audio', action: 'reduce', gain: 0.6 },
        { id: 'r2', start: 3, end: 7, source: 'audio', action: 'reduce', gain: 0.2 },
      ];
      // Before r2: gain is 0.6
      expect(resolveActions(2, overlappingReduces, options).soundGain).toBe(0.6);
      // During r2 overlap: minimum gain is 0.2
      expect(resolveActions(4, overlappingReduces, options).soundGain).toBe(0.2);
      // After r2 ends, still inside r1: restores back to 0.6!
      expect(resolveActions(8, overlappingReduces, options).soundGain).toBe(0.6);
      // After r1 ends: restores to 1.0
      expect(resolveActions(11, overlappingReduces, options).soundGain).toBe(1);
    });

    it('keeps visual hidden across multiple overlapping visual cues without premature reveal', () => {
      const overlappingHides: Cue[] = [
        { id: 'h1', start: 2, end: 6, source: 'visual', action: 'hide' },
        { id: 'h2', start: 4, end: 8, source: 'visual', action: 'hide' },
      ];
      expect(resolveActions(3, overlappingHides, options).hide).toBe(true);
      // At t=5, h1 is active and h2 is active
      expect(resolveActions(5, overlappingHides, options).hide).toBe(true);
      // At t=7, h1 has ended but h2 is still active -> remains hidden!
      expect(resolveActions(7, overlappingHides, options).hide).toBe(true);
      // At t=8, both ended -> returns to visible
      expect(resolveActions(8, overlappingHides, options).hide).toBe(false);
    });
  });

  // ---------------------------------------------------------------------------
  // 5. Seeking Into and Out of Cues
  // ---------------------------------------------------------------------------
  describe('Seeking into and out of cues', () => {
    it('immediately activates actions when seeking into a cue', () => {
      // Seek from t=0 directly to t=7.0 (inside mute cue [6.0, 10.0))
      playbackState.seek(7.0, verificationTestCues);
      const result = playbackState.resolveAt(7.0, verificationTestCues, options);
      expect(result.soundGain).toBe(0);
      expect(result.activeCueIds).toContain('cue-verify-mute');
    });

    it('immediately restores state when seeking out of a cue', () => {
      // In mute cue at t=7.0
      playbackState.resolveAt(7.0, verificationTestCues, options);
      // Seek out to t=11.0
      playbackState.seek(11.0, verificationTestCues);
      const result = playbackState.resolveAt(11.0, verificationTestCues, options);
      expect(result.soundGain).toBe(1);
      expect(result.activeCueIds).toHaveLength(0);
    });

    it('immediately activates hide when seeking into a visual hide cue', () => {
      playbackState.seek(23.0, verificationTestCues);
      const result = playbackState.resolveAt(23.0, verificationTestCues, options);
      expect(result.hide).toBe(true);
      expect(result.visualAction).toBe('hide');
    });

    it('immediately activates reduce when seeking into a volume reduction cue', () => {
      playbackState.seek(15.0, verificationTestCues);
      const result = playbackState.resolveAt(15.0, verificationTestCues, options);
      expect(result.soundGain).toBe(0.35);
    });
  });

  // ---------------------------------------------------------------------------
  // 6. Pause and Resume Behavior
  // ---------------------------------------------------------------------------
  describe('Pause and resume behavior', () => {
    it('maintains active trigger state while paused and continues consistently on resume', () => {
      // Playhead advances into mute cue
      const playingAt7 = playbackState.resolveAt(7.0, verificationTestCues, options);
      expect(playingAt7.soundGain).toBe(0);

      // Paused at t=7.0 (clock does not advance, repeated evaluations yield identical state)
      const pausedAt7_1 = playbackState.resolveAt(7.0, verificationTestCues, options);
      const pausedAt7_2 = playbackState.resolveAt(7.0, verificationTestCues, options);
      expect(pausedAt7_1.soundGain).toBe(0);
      expect(pausedAt7_2.soundGain).toBe(0);

      // Resumed: playhead advances to t=7.5 then t=10.5
      expect(playbackState.resolveAt(7.5, verificationTestCues, options).soundGain).toBe(0);
      expect(playbackState.resolveAt(10.5, verificationTestCues, options).soundGain).toBe(1);
    });
  });

  // ---------------------------------------------------------------------------
  // 7. Invalid or Missing Cue Data & Edge Cases
  // ---------------------------------------------------------------------------
  describe('Invalid, empty, and edge case cue data', () => {
    it('handles an empty cue array gracefully', () => {
      const result = resolveActions(15.0, [], options);
      expect(result.hide).toBe(false);
      expect(result.visualAction).toBeNull();
      expect(result.soundGain).toBe(1);
      expect(result.skipTo).toBeNull();
      expect(result.activeCueIds).toEqual([]);
    });

    it('ignores cues that fall outside the current playback timestamp', () => {
      const pastCues: Cue[] = [
        { id: 'past', start: 1, end: 5, source: 'audio', action: 'mute' },
        { id: 'future', start: 50, end: 60, source: 'audio', action: 'mute' },
      ];
      const result = resolveActions(20.0, pastCues, options);
      expect(result.soundGain).toBe(1);
      expect(result.activeCueIds).toHaveLength(0);
    });

    it('rejects malformed cues with invalid timing', () => {
      const malformed: Cue[] = [
        { id: 'bad', start: 10, end: 5, source: 'audio', action: 'mute' }, // end <= start
      ];
      expect(() => resolveActions(7.0, malformed, options)).toThrow(/Invalid cue/);
    });

    it('rejects cues with invalid gain values', () => {
      const badGain: Cue[] = [
        { id: 'bad-gain', start: 5, end: 10, source: 'audio', action: 'reduce', gain: 1.5 }, // > 1
      ];
      expect(() => resolveActions(7.0, badGain, options)).toThrow(/Invalid gain/);
    });

    it('resets cleanly when reset() is called on PlaybackState', () => {
      playbackState.resolveAt(7.0, verificationTestCues, options);
      playbackState.reset();
      const fresh = playbackState.resolveAt(0, verificationTestCues, options);
      expect(fresh.time).toBe(0);
      expect(fresh.soundGain).toBe(1);
      expect(playbackState.getActiveVisualAction()).toBeNull();
    });
  });

  // ---------------------------------------------------------------------------
  // 8. Visual Blur State Representation, Boundaries, and Precedence (Phase 2A)
  // ---------------------------------------------------------------------------
  describe('Visual blur state representation and boundaries (Phase 2A)', () => {
    const blurCues: Cue[] = [
      {
        id: 'cue-insect-blur',
        start: 40.0,
        end: 45.0,
        source: 'visual',
        action: 'blur',
        trigger: 'insect',
        status: 'ready',
      },
    ];

    it('activates blur at cue start, maintains it during cue, and deactivates at cue end', () => {
      // 1. Immediately before cue start (t=39.9s)
      const before = playbackState.resolveAt(39.9, blurCues, options);
      expect(before.visualAction).toBeNull();
      expect(before.hide).toBe(false);
      expect(before.soundGain).toBe(1);
      expect(before.activeCueIds).not.toContain('cue-insect-blur');
      expect(playbackState.getActiveVisualAction()).toBeNull();

      // 2. Exactly at cue start (t=40.0s, boundary inclusive)
      const atStart = playbackState.resolveAt(40.0, blurCues, options);
      expect(atStart.visualAction).toBe('blur');
      expect(atStart.hide).toBe(false);
      expect(atStart.soundGain).toBe(1);
      expect(atStart.activeCueIds).toContain('cue-insect-blur');
      expect(playbackState.getActiveVisualAction()).toBe('blur');

      // 3. During cue (t=42.5s)
      const during = playbackState.resolveAt(42.5, blurCues, options);
      expect(during.visualAction).toBe('blur');
      expect(during.hide).toBe(false);
      expect(during.soundGain).toBe(1);
      expect(playbackState.getActiveVisualAction()).toBe('blur');

      // 4. Exactly at cue end (t=45.0s, boundary exclusive)
      const atEnd = playbackState.resolveAt(45.0, blurCues, options);
      expect(atEnd.visualAction).toBeNull();
      expect(atEnd.hide).toBe(false);
      expect(atEnd.soundGain).toBe(1);
      expect(atEnd.activeCueIds).not.toContain('cue-insect-blur');
      expect(playbackState.getActiveVisualAction()).toBeNull();

      // 5. Well after cue end (t=46.0s)
      const after = playbackState.resolveAt(46.0, blurCues, options);
      expect(after.visualAction).toBeNull();
      expect(after.hide).toBe(false);
      expect(playbackState.getActiveVisualAction()).toBeNull();
    });

    it('does not activate blur if hideEnabled (visual protection toggle) is false', () => {
      const disabledOptions = { ...options, hideEnabled: false };
      const result = playbackState.resolveAt(42.0, blurCues, disabledOptions);
      expect(result.visualAction).toBeNull();
      expect(result.hide).toBe(false);
      expect(playbackState.getActiveVisualAction()).toBeNull();
    });

    it('does not activate blur if the trigger category is disabled by viewer preferences', () => {
      const disabledOptions = {
        ...options,
        disabledCategories: ['insect'],
      };
      const result = playbackState.resolveAt(42.0, blurCues, disabledOptions);
      expect(result.visualAction).toBeNull();
      expect(result.hide).toBe(false);
      expect(result.activeCueIds).not.toContain('cue-insect-blur');
      expect(playbackState.getActiveVisualAction()).toBeNull();
    });

    it('immediately activates blur when seeking into a blur cue', () => {
      playbackState.seek(42.0, blurCues);
      const result = playbackState.resolveAt(42.0, blurCues, options);
      expect(result.visualAction).toBe('blur');
      expect(result.hide).toBe(false);
      expect(playbackState.getActiveVisualAction()).toBe('blur');
      expect(playbackState.getCurrentTime()).toBe(42.0);
    });

    it('immediately deactivates blur when seeking out of a blur cue', () => {
      // First resolve inside cue
      playbackState.resolveAt(42.0, blurCues, options);
      expect(playbackState.getActiveVisualAction()).toBe('blur');

      // Seek outside cue: stale visual action is immediately invalidated
      playbackState.seek(48.0, blurCues);
      expect(playbackState.getActiveVisualAction()).toBeNull();
      expect(playbackState.getCurrentTime()).toBe(48.0);

      const result = playbackState.resolveAt(48.0, blurCues, options);
      expect(result.visualAction).toBeNull();
      expect(result.hide).toBe(false);
      expect(playbackState.getActiveVisualAction()).toBeNull();
    });

    describe('Blur overlapping cues and action precedence', () => {
      it('gives hide precedence over blur when both are active simultaneously, and restores blur when hide ends', () => {
        const overlappingCues: Cue[] = [
          {
            id: 'c-hide',
            start: 10.0,
            end: 15.0,
            source: 'visual',
            action: 'hide',
            trigger: 'flashing',
          },
          {
            id: 'c-blur',
            start: 12.0,
            end: 18.0,
            source: 'visual',
            action: 'blur',
            trigger: 'insect',
          },
        ];

        // Before blur starts (t=11.0s): hide only
        const t11 = playbackState.resolveAt(11.0, overlappingCues, options);
        expect(t11.visualAction).toBe('hide');
        expect(t11.hide).toBe(true);
        expect(playbackState.getActiveVisualAction()).toBe('hide');

        // Overlap region (t=13.0s): hide > blur priority (3 > 2)
        const t13 = playbackState.resolveAt(13.0, overlappingCues, options);
        expect(t13.visualAction).toBe('hide');
        expect(t13.hide).toBe(true);
        expect(t13.activeCueIds).toEqual(['c-hide', 'c-blur']);
        expect(playbackState.getActiveVisualAction()).toBe('hide');

        // Hide ends, blur still active (t=16.0s): transitions cleanly to blur, hide becomes false
        const t16 = playbackState.resolveAt(16.0, overlappingCues, options);
        expect(t16.visualAction).toBe('blur');
        expect(t16.hide).toBe(false);
        expect(t16.activeCueIds).toEqual(['c-blur']);
        expect(playbackState.getActiveVisualAction()).toBe('blur');

        // Blur ends (t=19.0s): all visual actions clear
        const t19 = playbackState.resolveAt(19.0, overlappingCues, options);
        expect(t19.visualAction).toBeNull();
        expect(t19.hide).toBe(false);
        expect(playbackState.getActiveVisualAction()).toBeNull();
      });

      it('gives blur precedence over dim when both are active simultaneously, and restores dim when blur ends', () => {
        const overlappingCues: Cue[] = [
          {
            id: 'c-dim',
            start: 10.0,
            end: 20.0,
            source: 'visual',
            action: 'dim',
            trigger: 'flashing',
          },
          {
            id: 'c-blur',
            start: 12.0,
            end: 16.0,
            source: 'visual',
            action: 'blur',
            trigger: 'insect',
          },
        ];

        // Before blur starts (t=11.0s): dim active
        const t11 = playbackState.resolveAt(11.0, overlappingCues, options);
        expect(t11.visualAction).toBe('dim');
        expect(t11.hide).toBe(false);

        // During overlap (t=14.0s): blur > dim priority (2 > 1)
        const t14 = playbackState.resolveAt(14.0, overlappingCues, options);
        expect(t14.visualAction).toBe('blur');
        expect(t14.hide).toBe(false);
        expect(playbackState.getActiveVisualAction()).toBe('blur');

        // Blur ends, dim still active (t=18.0s): restores to dim
        const t18 = playbackState.resolveAt(18.0, overlappingCues, options);
        expect(t18.visualAction).toBe('dim');
        expect(t18.hide).toBe(false);
        expect(playbackState.getActiveVisualAction()).toBe('dim');
      });

      it('maintains independent audio mute and volume reduction alongside visual blur', () => {
        const multiChannelCues: Cue[] = [
          {
            id: 'c-blur',
            start: 10.0,
            end: 20.0,
            source: 'visual',
            action: 'blur',
            trigger: 'insect',
          },
          {
            id: 'c-reduce',
            start: 12.0,
            end: 18.0,
            source: 'audio',
            action: 'reduce',
            gain: 0.35,
            trigger: 'scream',
          },
          {
            id: 'c-mute',
            start: 14.0,
            end: 16.0,
            source: 'audio',
            action: 'mute',
            trigger: 'gunshot',
          },
        ];

        // t=11.0s: Blur active, audio full
        const t11 = playbackState.resolveAt(11.0, multiChannelCues, options);
        expect(t11.visualAction).toBe('blur');
        expect(t11.hide).toBe(false);
        expect(t11.soundGain).toBe(1);

        // t=13.0s: Blur active, audio reduced
        const t13 = playbackState.resolveAt(13.0, multiChannelCues, options);
        expect(t13.visualAction).toBe('blur');
        expect(t13.soundGain).toBe(0.35);

        // t=15.0s: Blur active, audio muted
        const t15 = playbackState.resolveAt(15.0, multiChannelCues, options);
        expect(t15.visualAction).toBe('blur');
        expect(t15.soundGain).toBe(0);

        // t=17.0s: Blur active, audio restores to reduced (0.35)
        const t17 = playbackState.resolveAt(17.0, multiChannelCues, options);
        expect(t17.visualAction).toBe('blur');
        expect(t17.soundGain).toBe(0.35);

        // t=19.0s: Blur active, audio restores to full volume (1)
        const t19 = playbackState.resolveAt(19.0, multiChannelCues, options);
        expect(t19.visualAction).toBe('blur');
        expect(t19.soundGain).toBe(1);
      });

      it('preserves skip action when skip cue overlaps with visual blur cue', () => {
        const skipAndBlurCues: Cue[] = [
          {
            id: 'c-blur',
            start: 10.0,
            end: 20.0,
            source: 'visual',
            action: 'blur',
            trigger: 'insect',
          },
          {
            id: 'c-skip',
            start: 12.0,
            end: 16.0,
            source: 'visual',
            action: 'skip',
            trigger: 'flashing',
            status: 'ready',
          },
        ];

        // When skipEnabled is true: skipTo is populated
        const skipOptions = { ...options, skipEnabled: true };
        const result = playbackState.resolveAt(13.0, skipAndBlurCues, skipOptions);
        expect(result.skipTo).toBe(16.0);
        expect(result.visualAction).toBe('blur');

        // When skipEnabled is false: skipTo is null, blur remains active
        const noSkipOptions = { ...options, skipEnabled: false };
        const resultNoSkip = playbackState.resolveAt(13.0, skipAndBlurCues, noSkipOptions);
        expect(resultNoSkip.skipTo).toBeNull();
        expect(resultNoSkip.visualAction).toBe('blur');
      });
    });

    it('ensures no active blur outside applicable cue intervals or on non-visual modalities', () => {
      const nonVisualCues: Cue[] = [
        {
          id: 'c-audio',
          start: 5.0,
          end: 10.0,
          source: 'audio',
          action: 'mute',
          trigger: 'loud_spike',
        },
        {
          id: 'c-text',
          start: 8.0,
          end: 12.0,
          source: 'text',
          action: 'mute',
          trigger: 'profanity',
        },
      ];

      // At t=6.0s: audio mute
      const t6 = playbackState.resolveAt(6.0, nonVisualCues, options);
      expect(t6.visualAction).toBeNull();
      expect(t6.hide).toBe(false);

      // At t=9.0s: audio + text
      const t9 = playbackState.resolveAt(9.0, nonVisualCues, options);
      expect(t9.visualAction).toBeNull();
      expect(t9.hide).toBe(false);

      // Empty cue array
      const emptyResult = playbackState.resolveAt(25.0, [], options);
      expect(emptyResult.visualAction).toBeNull();
      expect(emptyResult.hide).toBe(false);
    });
  });
});
