import { resolveActions } from './resolveActions';
import { Cue, EffectiveActions, ViewerOptions, hasExplicitActionOverride, resolveEffectiveAction } from './types';
import { AppliedAction, PlaybackSessionPayload } from './recap';
import { applyReviewPolicy } from './reviewPolicy';

export class PlaybackState {
  private firedSkipCueIds = new Set<string>();
  private acknowledgedPromptCueIds = new Set<string>();
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
    const availableCues = cues.filter((cue) => {
      const effectiveAction = resolveEffectiveAction(
        cue.trigger,
        cue.action,
        options.actionOverrides,
      );
      return effectiveAction !== 'skip' || !this.firedSkipCueIds.has(cue.id);
    });

    const actions = resolveActions(time, availableCues, options);
    this.currentTime = time;
    this.currentVisualAction = actions.visualAction;

    // Suppress prompt if already acknowledged during this approach
    if (actions.promptCue && this.acknowledgedPromptCueIds.has(actions.promptCue.id)) {
      actions.promptCue = null;
    }

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

      const effectiveAction = resolveEffectiveAction(
        cue.trigger,
        cue.action,
        options.actionOverrides,
      );

      const reviewAllowsSkip =
        cue.status !== 'needs_review' || options.confirmReviewSkip;

      const isExplicitSkipOverride =
        effectiveAction === 'skip' &&
        hasExplicitActionOverride(cue.trigger, 'skip', options.actionOverrides);

      // Skip evaluation & recording
      if (
        actions.skipTo !== null &&
        effectiveAction === 'skip' &&
        (options.skipEnabled || isExplicitSkipOverride) &&
        reviewAllowsSkip
      ) {
        this.firedSkipCueIds.add(cue.id);
        if (!this.recordedCueActionIds.has(`skip_${cue.id}`)) {
          this.recordedCueActionIds.add(`skip_${cue.id}`);
          this.appliedActions.push({
            triggerType: cue.trigger ?? 'scene',
            appliedEffect: 'skipped',
            requestedAction: effectiveAction,
            cueId: cue.id,
            start: cue.start,
            end: cue.end,
          });
        }
      }

      // Visual hide / blackout recording
      if (
        actions.visualAction === 'hide' &&
        options.hideEnabled &&
        effectiveAction === 'hide'
      ) {
        if (!this.recordedCueActionIds.has(`visual_${cue.id}`)) {
          this.recordedCueActionIds.add(`visual_${cue.id}`);
          this.appliedActions.push({
            triggerType: cue.trigger ?? 'visual',
            appliedEffect: 'blacked_out',
            requestedAction: effectiveAction,
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
        effectiveAction === 'mute'
      ) {
        if (!this.recordedCueActionIds.has(`audio_${cue.id}`)) {
          this.recordedCueActionIds.add(`audio_${cue.id}`);
          this.appliedActions.push({
            triggerType: cue.trigger ?? 'audio',
            appliedEffect: 'muted',
            requestedAction: effectiveAction,
            cueId: cue.id,
            start: cue.start,
            end: cue.end,
          });
        }
      }

      const isExplicitReduceOverride =
        effectiveAction === 'reduce' &&
        hasExplicitActionOverride(cue.trigger, 'reduce', options.actionOverrides);

      // Audio reduce recording
      if (
        actions.soundGain > 0 &&
        actions.soundGain < 1 &&
        (options.reduceEnabled || isExplicitReduceOverride) &&
        effectiveAction === 'reduce'
      ) {
        if (!this.recordedCueActionIds.has(`reduce_${cue.id}`)) {
          this.recordedCueActionIds.add(`reduce_${cue.id}`);
          this.appliedActions.push({
            triggerType: cue.trigger ?? 'audio',
            appliedEffect: 'reduced',
            requestedAction: effectiveAction,
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
   * Returns the eligible heads-up prompt cue for the given time and options,
   * delegating pure eligibility calculation to resolveActions() and applying
   * stateful suppression for already-acknowledged prompts.
   */
  getActivePromptCue(
    time: number,
    cues: Cue[],
    options: ViewerOptions,
  ): Cue | null {
    const actions = resolveActions(time, cues, options);
    if (actions.promptCue && this.acknowledgedPromptCueIds.has(actions.promptCue.id)) {
      return null;
    }
    return actions.promptCue ?? null;
  }

  /**
   * Acknowledge a heads-up prompt for a cue (Continue action).
   * Suppresses repeated prompts during forward playback through the cue.
   */
  acknowledgePrompt(cueId: string): void {
    this.acknowledgedPromptCueIds.add(cueId);
  }

  /**
   * Dismiss a heads-up prompt for a cue (alias for acknowledgePrompt).
   */
  dismissPrompt(cueId: string): void {
    this.acknowledgePrompt(cueId);
  }

  /**
   * Check if a prompt has been acknowledged.
   */
  isPromptAcknowledged(cueId: string): boolean {
    return this.acknowledgedPromptCueIds.has(cueId);
  }

  /**
   * Evaluates whether a cue is genuinely eligible for skipping under the effective
   * viewer options and review policy:
   * 1. Viewer skipEnabled must not be false.
   * 2. Trigger category must not be in disabledCategories.
   * 3. Cue effective action must be 'skip' (not hide, mute, reduce, or warn).
   * 4. If status is 'needs_review', confirmReviewSkip must be true.
   */
  isCueEligibleForSkip(cue: Cue, options?: ViewerOptions): boolean {
    const isExplicitSkipOverride = hasExplicitActionOverride(
      cue.trigger,
      'skip',
      options?.actionOverrides,
    );

    if (options && options.skipEnabled === false && !isExplicitSkipOverride) {
      return false;
    }

    if (
      options?.disabledCategories &&
      cue.trigger &&
      options.disabledCategories.includes(cue.trigger)
    ) {
      return false;
    }

    const effectiveAction = resolveEffectiveAction(
      cue.trigger,
      cue.action,
      options?.actionOverrides,
    );

    if (effectiveAction !== 'skip') {
      return false;
    }

    const reviewAllowsSkip =
      !options || cue.status !== 'needs_review' || options.confirmReviewSkip === true;
    if (!reviewAllowsSkip) {
      return false;
    }

    return true;
  }

  /**
   * Handles user selecting "Skip" on a heads-up prompt.
   *
   * Rules:
   * - Evaluates viewer and review policy for the requested cue:
   *   - If cue is disabled in options.disabledCategories, skip is disallowed.
   *   - If options.skipEnabled is false, skip is disallowed.
   *   - If cue effective action is not 'skip' (e.g. warn, hide, mute, reduce), skip is disallowed.
   *   - If cue status is 'needs_review' and confirmReviewSkip is not true, skip is disallowed.
   * - Marks the cue as acknowledged and handled before initiating seek to prevent loops.
   * - Chains across overlapping or contiguous cues ([start, end]) ONLY if they are genuinely
   *   eligible skip cues under the same effective policy.
   * - Non-skip cues, warning cues, disabled triggers, and unconfirmed needs_review cues
   *   never extend the skip destination.
   * - Ensures forward progress beyond cue.start.
   * - Clamps seek target to mediaDuration.
   * - Records applied action in session recap with appliedEffect: 'skipped'.
   */
  handlePromptSkip(
    cue: Cue,
    cues: Cue[],
    mediaDuration?: number,
    options?: ViewerOptions,
  ): number {
    const policyCues = options ? applyReviewPolicy(cues, options) : cues;
    const policyCue = policyCues.find((c) => c.id === cue.id) ?? cue;

    // 1. Initial cue eligibility check: must be an eligible skip cue under effective policy
    if (!this.isCueEligibleForSkip(policyCue, options) || this.firedSkipCueIds.has(cue.id)) {
      return this.currentTime;
    }

    this.acknowledgedPromptCueIds.add(cue.id);
    this.firedSkipCueIds.add(cue.id);

    let destination = cue.end;

    // 2. Chaining: only extend destination across overlapping or contiguous cues
    // that are genuinely eligible for the same skip operation.
    let extended = true;
    while (extended) {
      extended = false;
      for (const other of policyCues) {
        if (
          other.id !== cue.id &&
          !this.firedSkipCueIds.has(other.id) &&
          other.start <= destination &&
          other.end > destination
        ) {
          if (this.isCueEligibleForSkip(other, options)) {
            destination = other.end;
            this.acknowledgedPromptCueIds.add(other.id);
            this.firedSkipCueIds.add(other.id);
            extended = true;
          }
        }
      }
    }

    // 3. Mark any nested eligible skip cues fully enveloped by the skipped interval
    for (const nested of policyCues) {
      if (
        nested.id !== cue.id &&
        nested.start >= cue.start &&
        nested.end <= destination &&
        this.isCueEligibleForSkip(nested, options)
      ) {
        this.acknowledgedPromptCueIds.add(nested.id);
        this.firedSkipCueIds.add(nested.id);
      }
    }

    // Ensure strictly forward progress
    if (destination <= cue.start) {
      destination = cue.start + 0.1;
    }

    // Clamp to declared media duration if finite and positive
    if (Number.isFinite(mediaDuration) && (mediaDuration as number) > 0) {
      destination = Math.min(destination, mediaDuration as number);
    }

    // Record skip action for session recap
    this.recordAction({
      triggerType: cue.trigger ?? 'prompt',
      appliedEffect: 'skipped',
      requestedAction: 'skip',
      cueId: cue.id,
      start: cue.start,
      end: destination,
    });

    return destination;
  }

  /**
   * Notify the engine that the player has sought to a new time.
   * On rewind:
   * - Clears fired status for skip cues that start after the new position.
   * - Re-arms heads-up prompts when rewinding strictly before cue prompt window.
   */
  seek(newTime: number, cues: Cue[], options?: ViewerOptions): void {
    if (!Number.isFinite(newTime) || newTime < 0) {
      throw new Error('Seek time must be a finite, non-negative number.');
    }

    const leadTime = options?.headsUpPrompts?.leadTimeSeconds ?? 5.0;

    if (newTime < this.currentTime) {
      for (const cue of cues) {
        if (cue.start > newTime) {
          this.firedSkipCueIds.delete(cue.id);
        }
        const promptWindowStart = Math.max(0, cue.start - leadTime);
        if (newTime < promptWindowStart) {
          this.acknowledgedPromptCueIds.delete(cue.id);
        }
      }
    }

    this.currentTime = newTime;
    this.currentVisualAction = null;
  }

  /**
   * Get the active visual action resolved at the current playback time.
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

  /**
   * Check if a skip cue has already fired.
   */
  hasSkipFired(cueId: string): boolean {
    return this.firedSkipCueIds.has(cueId);
  }

  /**
   * Reset all playback state for a new session or video source.
   */
  reset(): void {
    this.firedSkipCueIds.clear();
    this.acknowledgedPromptCueIds.clear();
    this.recordedCueActionIds.clear();
    this.appliedActions = [];
    this.currentTime = 0;
    this.currentVisualAction = null;
  }
}
