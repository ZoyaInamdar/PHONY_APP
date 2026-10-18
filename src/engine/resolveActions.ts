import {
  Cue,
  CueAction,
  EffectiveActions,
  ViewerOptions,
} from './types';

import { applyReviewPolicy } from './reviewPolicy';

/**
 * Visual action priority order for resolving the single `visualAction` output
 * when multiple visual cues are active simultaneously.
 *
 * hide > blur > dim
 *
 * `blur` and `dim` have no Phase 0 rendering implementation.  Their presence
 * in `visualAction` records what the shared config intends; the rendering layer
 * (Vega OS overlay API) is required before either can be applied on-device.
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
    // -------------------------------------------------------------------------
    // Warning actions
    // `warn` cues provide a heads-up alert without overriding audio, visual,
    // or skip actions.
    // -------------------------------------------------------------------------
    if (cue.action === 'warn') {
      warnCueIds.push(cue.id);
    }

    // -------------------------------------------------------------------------
    // Visual actions
    // `visual` cues, and `audio_visual` cues when hideEnabled is on, produce a
    // visual effect.  `dim` and `blur` are recorded faithfully even though Phase 0
    // has no rendering implementation for them.
    // -------------------------------------------------------------------------
    const isVisualSource = cue.source === 'visual' || cue.source === 'audio_visual';

    if (isVisualSource && options.hideEnabled) {
      const priority = VISUAL_ACTION_PRIORITY.get(cue.action) ?? 0;

      if (priority > 0 && priority > bestVisualActionPriority) {
        bestVisualActionPriority = priority;
        visualAction = cue.action as 'hide' | 'blur' | 'dim';
      }
    }

    // -------------------------------------------------------------------------
    // Audio actions
    // `audio` cues apply directly.  `text` cues (profanity) have audio muting
    // as their player-side action (muting the audio channel during the cue).
    // `audio_visual` cues also carry an audio component.
    //
    // Caption censorship for `text` cues is handled separately by Person C's
    // caption renderer — NOT by the audio mute here, though both may fire on
    // the same interval.
    // -------------------------------------------------------------------------
    const hasAudioComponent =
      cue.source === 'audio' ||
      cue.source === 'text' ||
      cue.source === 'audio_visual';

    if (hasAudioComponent && cue.action === 'mute' && options.muteEnabled) {
      soundGain = 0;
    } else if (hasAudioComponent && cue.action === 'reduce' && options.reduceEnabled) {
      const gain = cue.gain ?? options.reduceGain;

      if (!Number.isFinite(gain) || gain < 0 || gain > 1) {
        throw new Error(`Invalid gain for cue: ${cue.id}`);
      }

      soundGain = Math.min(soundGain, gain);
    }

    // -------------------------------------------------------------------------
    // Skip actions
    // -------------------------------------------------------------------------
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
