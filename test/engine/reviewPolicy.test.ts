
import { applyReviewPolicy } from '../../src/engine/reviewPolicy';
import { Cue, DEFAULT_VIEWER_OPTIONS } from '../../src/engine/types';

const options = {
  ...DEFAULT_VIEWER_OPTIONS,
  skipEnabled: true,
};

describe('applyReviewPolicy', () => {
  it('downgrades an unconfirmed visual skip to hide', () => {
    const cues: Cue[] = [
      {
        id: 'visual-skip',
        start: 2,
        end: 5,
        source: 'visual',
        action: 'skip',
        status: 'needs_review',
      },
    ];

    expect(applyReviewPolicy(cues, options)[0].action).toBe('hide');
  });

  it('downgrades an unconfirmed audio skip to mute', () => {
    const cues: Cue[] = [
      {
        id: 'audio-skip',
        start: 2,
        end: 5,
        source: 'audio',
        action: 'skip',
        status: 'needs_review',
      },
    ];

    expect(applyReviewPolicy(cues, options)[0].action).toBe('mute');
  });

  it('keeps skip when the viewer confirms review skips', () => {
    const cues: Cue[] = [
      {
        id: 'confirmed-skip',
        start: 2,
        end: 5,
        source: 'visual',
        action: 'skip',
        status: 'needs_review',
      },
    ];

    const result = applyReviewPolicy(cues, {
      ...options,
      confirmReviewSkip: true,
    });

    expect(result[0].action).toBe('skip');
  });

  it('uses warn if the visual fallback is disabled', () => {
    const cues: Cue[] = [
      {
        id: 'visual-skip',
        start: 2,
        end: 5,
        source: 'visual',
        action: 'skip',
        status: 'needs_review',
      },
    ];

    const result = applyReviewPolicy(cues, {
      ...options,
      hideEnabled: false,
    });

    expect(result[0].action).toBe('warn');
  });

  it('uses warn if the audio fallback is disabled', () => {
    const cues: Cue[] = [
      {
        id: 'audio-skip',
        start: 2,
        end: 5,
        source: 'audio',
        action: 'skip',
        status: 'needs_review',
      },
    ];

    const result = applyReviewPolicy(cues, {
      ...options,
      muteEnabled: false,
    });

    expect(result[0].action).toBe('warn');
  });

  it('does not mutate the original cue', () => {
    const cue: Cue = {
      id: 'visual-skip',
      start: 2,
      end: 5,
      source: 'visual',
      action: 'skip',
      status: 'needs_review',
    };

    applyReviewPolicy([cue], options);

    expect(cue.action).toBe('skip');
  });
});
