
import { Cue, ViewerOptions } from './types';

/**
 * Downgrade unconfirmed needs_review skip cues to their
 * source-appropriate fallback action.
 *
 * Fallback mapping:
 *   visual skip         → hide  (if hideEnabled)
 *   audio skip          → mute  (if muteEnabled)
 *   text skip           → mute  (text cues mute audio; if muteEnabled)
 *   audio_visual skip   → mute  (best safe fallback; if muteEnabled)
 *   any other           → warn  (if preferred action is disabled)
 *
 * `dim` and `blur` cues will never have `action === 'skip'` in practice
 * (the shared config does not allow skip for flashing/spider in Phase 0),
 * but they are treated as visual sources here for completeness.
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

    if (
      (cue.source === 'visual') &&
      options.hideEnabled
    ) {
      return { ...cue, action: 'hide' };
    }

    if (
      (cue.source === 'audio' ||
        cue.source === 'text' ||
        cue.source === 'audio_visual') &&
      options.muteEnabled
    ) {
      return { ...cue, action: 'mute' };
    }

    // Don't invent a fallback action if the viewer disabled it.
    return { ...cue, action: 'warn' };
  });
}
