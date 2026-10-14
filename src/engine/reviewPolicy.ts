
import { Cue, ViewerOptions } from './types';

/**
 * Downgrade unconfirmed needs_review skip cues to their
 * source-appropriate fallback action.
 *
 * Visual skip -> hide
 * Audio skip  -> mute
 *
 * This function does not mutate the input cues.
 */
export function applyReviewPolicy(
  cues: Cue[],
  options: ViewerOptions,
): Cue[] {
  return cues.map((cue) => {
    if (
      cue.action !== 'skip' ||
      cue.status !== 'needs_review' ||
      options.confirmReviewSkip
    ) {
      return { ...cue };
    }

    if (cue.source === 'visual' && options.hideEnabled) {
      return { ...cue, action: 'hide' };
    }

    if (cue.source === 'audio' && options.muteEnabled) {
      return { ...cue, action: 'mute' };
    }

    // Don't invent a fallback action if the viewer disabled it.
    return { ...cue, action: 'warn' };
  });
}
