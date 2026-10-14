
import { resolveActions } from './resolveActions';
import { Cue, EffectiveActions, ViewerOptions } from './types';

export class PlaybackState {
  private firedSkipCueIds = new Set<string>();
  private currentTime = 0;

  /**
   * Resolve actions at the current playback time.
   * Each eligible skip cue fires at most once until a rewind
   * clears its record.
   */
  resolveAt(
    time: number,
    cues: Cue[],
    options: ViewerOptions,
  ): EffectiveActions {
    const availableCues = cues.filter(
      (cue) =>
        cue.action !== 'skip' || !this.firedSkipCueIds.has(cue.id),
    );

    const actions = resolveActions(time, availableCues, options);
    this.currentTime = time;

    if (actions.skipTo !== null) {
      for (const cue of availableCues) {
        const isActive = cue.start <= time && time < cue.end;
        const reviewAllowsSkip =
          cue.status !== 'needs_review' || options.confirmReviewSkip;

        if (
          isActive &&
          cue.action === 'skip' &&
          options.skipEnabled &&
          reviewAllowsSkip
        ) {
          this.firedSkipCueIds.add(cue.id);
        }
      }
    }

    return actions;
  }

  /**
   * Notify the engine that the player has sought to a new time.
   * On rewind, forget skip cues that start after the new position.
   */
  seek(newTime: number, cues: Cue[]): void {
    if (!Number.isFinite(newTime) || newTime < 0) {
      throw new Error('Seek time must be a finite, non-negative number.');
    }

    if (newTime < this.currentTime) {
      for (const cue of cues) {
        if (cue.start > newTime) {
          this.firedSkipCueIds.delete(cue.id);
        }
      }
    }

    this.currentTime = newTime;
  }

  /** Useful when starting a completely new video. */
  reset(): void {
    this.firedSkipCueIds.clear();
    this.currentTime = 0;
  }
}
