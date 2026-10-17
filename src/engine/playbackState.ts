import { resolveActions } from './resolveActions';
import { Cue, EffectiveActions, ViewerOptions } from './types';
import { AppliedAction, PlaybackSessionPayload } from './recap';

export class PlaybackState {
  private firedSkipCueIds = new Set<string>();
  private recordedCueActionIds = new Set<string>();
  private appliedActions: AppliedAction[] = [];
  private currentTime = 0;
  private currentVisualAction: 'hide' | 'blur' | 'dim' | null = null;

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
    this.currentVisualAction = actions.visualAction;

    // Record applied actions for session recap
    for (const cue of cues) {
      if (
        options.disabledCategories &&
        cue.trigger &&
        options.disabledCategories.includes(cue.trigger)
      ) {
        continue;
      }

      const isActive = cue.start <= time && time < cue.end;
      if (!isActive) {
        continue;
      }

      const reviewAllowsSkip =
        cue.status !== 'needs_review' || options.confirmReviewSkip;

      // Skip evaluation & recording
      if (
        actions.skipTo !== null &&
        cue.action === 'skip' &&
        options.skipEnabled &&
        reviewAllowsSkip
      ) {
        this.firedSkipCueIds.add(cue.id);
        if (!this.recordedCueActionIds.has(`skip_${cue.id}`)) {
          this.recordedCueActionIds.add(`skip_${cue.id}`);
          this.appliedActions.push({
            triggerType: cue.trigger ?? 'scene',
            appliedEffect: 'skipped',
            requestedAction: cue.action,
            cueId: cue.id,
            start: cue.start,
            end: cue.end,
          });
        }
      }

      // Visual hide / blackout recording (including device fallback from blur/dim)
      if (
        actions.visualAction === 'hide' &&
        options.hideEnabled &&
        (cue.source === 'visual' || cue.source === 'audio_visual')
      ) {
        if (!this.recordedCueActionIds.has(`visual_${cue.id}`)) {
          this.recordedCueActionIds.add(`visual_${cue.id}`);
          this.appliedActions.push({
            triggerType: cue.trigger ?? 'visual',
            appliedEffect: 'blacked_out',
            requestedAction: cue.action,
            cueId: cue.id,
            start: cue.start,
            end: cue.end,
          });
        }
      }

      // Audio mute recording
      if (
        actions.soundGain === 0 &&
        options.muteEnabled &&
        cue.action === 'mute' &&
        (cue.source === 'audio' || cue.source === 'text' || cue.source === 'audio_visual')
      ) {
        if (!this.recordedCueActionIds.has(`audio_${cue.id}`)) {
          this.recordedCueActionIds.add(`audio_${cue.id}`);
          this.appliedActions.push({
            triggerType: cue.trigger ?? 'audio',
            appliedEffect: 'muted',
            requestedAction: cue.action,
            cueId: cue.id,
            start: cue.start,
            end: cue.end,
          });
        }
      }

      // Audio reduce recording
      if (
        actions.soundGain > 0 &&
        actions.soundGain < 1 &&
        options.reduceEnabled &&
        cue.action === 'reduce' &&
        (cue.source === 'audio' || cue.source === 'audio_visual')
      ) {
        if (!this.recordedCueActionIds.has(`reduce_${cue.id}`)) {
          this.recordedCueActionIds.add(`reduce_${cue.id}`);
          this.appliedActions.push({
            triggerType: cue.trigger ?? 'audio',
            appliedEffect: 'reduced',
            requestedAction: cue.action,
            cueId: cue.id,
            start: cue.start,
            end: cue.end,
          });
        }
      }
    }

    return actions;
  }

  /**
   * Request the final session payload from the engine.
   * Tells you if an intended action was downgraded or changed
   * (e.g. if a requested blur fell back to blackout due to device limits).
   */
  getSessionPayload(): PlaybackSessionPayload {
    return {
      appliedActions: [...this.appliedActions],
    };
  }

  /**
   * Get the list of all actions applied during this playback session.
   */
  getAppliedActions(): AppliedAction[] {
    return [...this.appliedActions];
  }

  /**
   * Directly record an applied action into the session record.
   */
  recordAction(action: AppliedAction): void {
    this.appliedActions.push(action);
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
    this.currentVisualAction = null;
  }

  /**
   * Get the active visual action resolved at the current playback time.
   *
   * Note: This reflects the engine-resolved action ('hide' | 'blur' | 'dim' | null)
   * requested by active cues according to viewer preferences and action priority.
   * It does not represent whether the underlying video hardware has rendered the effect.
   */
  getActiveVisualAction(): 'hide' | 'blur' | 'dim' | null {
    return this.currentVisualAction;
  }

  /**
   * Get the current playback time recorded by the engine.
   */
  getCurrentTime(): number {
    return this.currentTime;
  }

  /** Useful when starting a completely new video. */
  reset(): void {
    this.firedSkipCueIds.clear();
    this.recordedCueActionIds.clear();
    this.appliedActions = [];
    this.currentTime = 0;
    this.currentVisualAction = null;
  }
}
