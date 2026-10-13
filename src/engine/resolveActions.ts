import {
  Cue,
  EffectiveActions,
  ViewerOptions,
} from './types';

export function resolveActions(
  time: number,
  cues: Cue[],
  options: ViewerOptions,
): EffectiveActions {
  if (!Number.isFinite(time) || time < 0) {
    throw new Error('Playback time must be a finite, non-negative number.');
  }

  const active = cues.filter((cue) => {
    if (
      !cue.id ||
      !Number.isFinite(cue.start) ||
      !Number.isFinite(cue.end) ||
      cue.start < 0 ||
      cue.end <= cue.start
    ) {
      throw new Error(`Invalid cue: ${cue.id || '(missing id)'}`);
    }

    return cue.start <= time && time < cue.end;
  });

  let hide = false;
  let soundGain = 1;
  let skipTo: number | null = null;

  for (const cue of active) {
    if (
      cue.action === 'hide' &&
      cue.source === 'visual' &&
      options.hideEnabled
    ) {
      hide = true;
    }

    if (
      cue.source === 'audio' &&
      cue.action === 'mute' &&
      options.muteEnabled
    ) {
      soundGain = 0;
    } else if (
      cue.source === 'audio' &&
      cue.action === 'reduce' &&
      options.reduceEnabled
    ) {
      const gain = cue.gain ?? options.reduceGain;

      if (!Number.isFinite(gain) || gain < 0 || gain > 1) {
        throw new Error(`Invalid gain for cue: ${cue.id}`);
      }

      soundGain = Math.min(soundGain, gain);
    }

    const reviewAllowsSkip =
      cue.status !== 'needs_review' || options.confirmReviewSkip;

    if (
      cue.action === 'skip' &&
      options.skipEnabled &&
      reviewAllowsSkip
    ) {
      // If multiple skips overlap, seek past the latest ending cue.
      skipTo = Math.max(skipTo ?? 0, cue.end);
    }
  }

  return {
    time,
    hide,
    soundGain,
    skipTo,
    activeCueIds: active.map((cue) => cue.id),
  };
}
