
import { PlaybackState } from '../../src/engine/playbackState';
import { Cue, DEFAULT_VIEWER_OPTIONS } from '../../src/engine/types';

const options = {
  ...DEFAULT_VIEWER_OPTIONS,
  skipEnabled: true,
};

const skipCues: Cue[] = [
  {
    id: 'skip-1',
    start: 10,
    end: 15,
    source: 'audio',
    action: 'skip',
  },
];

describe('PlaybackState', () => {
  it('fires a skip cue only once', () => {
    const state = new PlaybackState();

    expect(state.resolveAt(11, skipCues, options).skipTo).toBe(15);
    expect(state.resolveAt(11, skipCues, options).skipTo).toBeNull();
  });

  it('uses the latest end when skip cues overlap', () => {
    const state = new PlaybackState();

    const cues: Cue[] = [
      { ...skipCues[0], end: 16 },
      {
        id: 'skip-2',
        start: 11,
        end: 20,
        source: 'audio',
        action: 'skip',
      },
    ];

    expect(state.resolveAt(12, cues, options).skipTo).toBe(20);
  });

  it('does not skip when the viewer disables skipping', () => {
    const state = new PlaybackState();

    expect(
      state.resolveAt(11, skipCues, {
        ...options,
        skipEnabled: false,
      }).skipTo,
    ).toBeNull();
  });

  it('does not automatically skip a needs_review cue without confirmation', () => {
    const state = new PlaybackState();
    const cues: Cue[] = [
      { ...skipCues[0], status: 'needs_review' },
    ];

    expect(
      state.resolveAt(11, cues, {
        ...options,
        confirmReviewSkip: false,
      }).skipTo,
    ).toBeNull();
  });

  it('allows a skip cue to fire again after rewinding before its start', () => {
    const state = new PlaybackState();

    expect(state.resolveAt(11, skipCues, options).skipTo).toBe(15);

    state.seek(5, skipCues);

    expect(state.resolveAt(11, skipCues, options).skipTo).toBe(15);
  });

  it('can resolve a skip when playback seeks into its time range', () => {
    const state = new PlaybackState();

    state.seek(11, skipCues);

    expect(state.resolveAt(11, skipCues, options).skipTo).toBe(15);
  });

  it('rejects an invalid seek time', () => {
    const state = new PlaybackState();

    expect(() => state.seek(-1, skipCues)).toThrow(
      'Seek time must be a finite, non-negative number.',
    );
  });


it('preserves skip history after a forward seek', () => {
  const state = new PlaybackState();

  expect(state.resolveAt(11, skipCues, options).skipTo).toBe(15);

  state.seek(20, skipCues);

  expect(state.resolveAt(11, skipCues, options).skipTo).toBeNull();
});


  it('reset clears skip history', () => {
    const state = new PlaybackState();

    state.resolveAt(11, skipCues, options);
    state.reset();

    expect(state.resolveAt(11, skipCues, options).skipTo).toBe(15);
  });
});
