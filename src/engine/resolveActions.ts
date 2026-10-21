import {
  Cue,
  CueAction,
  EffectiveActions,
  ViewerOptions,
  resolveEffectiveAction,
} from './types';

import { applyReviewPolicy } from './reviewPolicy';

/**
 * Visual action priority order for resolving the single `visualAction` output
 * when multiple visual cues are active simultaneously.
 *
 * hide > blur > dim
 *
 * When both 'hide' and 'blur' cues overlap, 'hide' takes precedence.
 * If 'hide' ends while 'blur' remains active, visual protection restores to 'blur'.
 */
const VISUAL_ACTION_PRIORITY: ReadonlyMap<CueAction, number> = new Map([
  ['hide', 3],
  ['blur', 2],
  ['dim', 1],
]);

export function resolveActions(
  time: number,
  cues: Cue[],
  options: ViewerOptions,
): EffectiveActions {
  if (!Number.isFinite(time) || time < 0) {
    throw new Error('Playback time must be a finite, non-negative number.');
  }

  const policyCues = applyReviewPolicy(cues, options);

  const active = policyCues.filter((cue) => {
    if (
      !cue.id ||
      !Number.isFinite(cue.start) ||
      !Number.isFinite(cue.end) ||
      cue.start < 0 ||
      cue.end <= cue.start
    ) {
      throw new Error(`Invalid cue: ${cue.id || '(missing id)'}`);
    }

    if (
      options.disabledCategories &&
      cue.trigger &&
      options.disabledCategories.includes(cue.trigger)
    ) {
      return false;
    }

    return cue.start <= time && time < cue.end;
  });

  let bestVisualActionPriority = 0;
  let visualAction: 'hide' | 'blur' | 'dim' | null = null;
  let soundGain = 1;
  let skipTo: number | null = null;
  const warnCueIds: string[] = [];

  for (const cue of active) {
    // Determine effective action using centralized trigger contract and user overrides
    const effectiveAction = resolveEffectiveAction(
      cue.trigger,
      cue.action,
      options.actionOverrides,
    );

    // -------------------------------------------------------------------------
    // Warning actions
    // `warn` cues provide a heads-up alert without overriding audio, visual,
    // or skip actions.
    // -------------------------------------------------------------------------
    if (effectiveAction === 'warn') {
      warnCueIds.push(cue.id);
    }

    // -------------------------------------------------------------------------
    // Visual actions ('hide' | 'blur' | 'dim')
    // Governed by global hideEnabled safety setting.
    // Precedence: hide (3) > blur (2) > dim (1).
    // -------------------------------------------------------------------------
    if (
      (effectiveAction === 'hide' || effectiveAction === 'blur' || effectiveAction === 'dim') &&
      options.hideEnabled
    ) {
      const priority = VISUAL_ACTION_PRIORITY.get(effectiveAction) ?? 0;

      if (priority > 0 && priority > bestVisualActionPriority) {
        bestVisualActionPriority = priority;
        visualAction = effectiveAction as 'hide' | 'blur' | 'dim';
      }
    }

    // -------------------------------------------------------------------------
    // Audio actions ('mute' | 'reduce')
    // Precedence: mute (gain 0) > reduce (gain 0 < g < 1) > normal (gain 1).
    // -------------------------------------------------------------------------
    if (effectiveAction === 'mute' && options.muteEnabled) {
      soundGain = 0;
    } else if (effectiveAction === 'reduce' && options.reduceEnabled) {
      const gain = cue.gain ?? options.reduceGain;

      if (!Number.isFinite(gain) || gain < 0 || gain > 1) {
        throw new Error(`Invalid gain for cue: ${cue.id}`);
      }

      soundGain = Math.min(soundGain, gain);
    }

    // -------------------------------------------------------------------------
    // Skip actions
    // Seeks past the latest ending skip cue.
    // -------------------------------------------------------------------------
    const reviewAllowsSkip =
      cue.status !== 'needs_review' || options.confirmReviewSkip;

    if (
      effectiveAction === 'skip' &&
      options.skipEnabled &&
      reviewAllowsSkip
    ) {
      skipTo = Math.max(skipTo ?? 0, cue.end);
    }
  }

  // ---------------------------------------------------------------------------
  // Advance heads-up prompt eligibility
  // ---------------------------------------------------------------------------
  let promptCue: Cue | null = null;
  if (options.headsUpPrompts?.enabled) {
    const leadTime = options.headsUpPrompts.leadTimeSeconds;
    if (Number.isFinite(leadTime) && leadTime > 0) {
      const eligiblePrompts = policyCues.filter((cue) => {
        if (
          !cue.id ||
          !Number.isFinite(cue.start) ||
          !Number.isFinite(cue.end) ||
          cue.start < 0 ||
          cue.end <= cue.start
        ) {
          throw new Error(`Invalid cue: ${cue.id || '(missing id)'}`);
        }

        if (
          options.disabledCategories &&
          cue.trigger &&
          options.disabledCategories.includes(cue.trigger)
        ) {
          return false;
        }

        const windowStart = Math.max(0, cue.start - leadTime);
        return windowStart <= time && time < cue.start;
      });

      if (eligiblePrompts.length > 0) {
        // Deterministic selection: earliest cue.start, then tie-break by cue.id
        eligiblePrompts.sort((a, b) => {
          if (a.start !== b.start) {
            return a.start - b.start;
          }
          return a.id.localeCompare(b.id);
        });
        promptCue = eligiblePrompts[0];
      }
    }
  }

  // `hide` true is kept for backward-compat: it is true exactly when visualAction is 'hide'.
  const hide = visualAction === 'hide';

  return {
    time,
    hide,
    visualAction,
    soundGain,
    skipTo,
    activeCueIds: active.map((cue) => cue.id),
    warnCueIds,
    promptCue,
  };
}
