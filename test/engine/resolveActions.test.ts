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

  it('treats cue end as exclusive', () => {
    const result = resolveActions(10, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
    });

    expect(result.hide).toBe(false);
  });
});
