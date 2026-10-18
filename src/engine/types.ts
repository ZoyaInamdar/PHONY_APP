/**
 * The modality of a cue, derived from the shared config's `modality` field.
 *
 * `visual` and `audio` are fully handled by the playback engine.
 *
 * `text` (profanity) and `audio_visual` (jump_scare) are carried through the
 * engine for correct identification but do NOT have rendering implementations
 * in Phase 0.  Caption censorship for `text` cues is Person C's responsibility.
 * Combined audio+visual rendering for `audio_visual` cues is a Phase 1 item.
 */
export type CueSource = 'visual' | 'audio' | 'text' | 'audio_visual';

/**
 * The playback action for a cue.
 *
 * `hide`, `mute`, `reduce`, `skip`, and `warn` are handled by the playback engine.
 *
 * `dim` (reduce brightness) and `blur` (apply blur filter) come from the shared
 * config for `flashing` and `spider` triggers respectively.  They are carried
 * through the engine but have NO rendering implementation in Phase 0.  The
 * visual rendering layer (Vega OS overlay API) is needed before either can be
 * applied on-device.  Do NOT silently convert `dim` or `blur` to `hide`.
 */
export type CueAction = 'hide' | 'mute' | 'reduce' | 'skip' | 'warn' | 'dim' | 'blur';

export type CueStatus = 'ready' | 'needs_review';

export interface Cue {
  id: string;
  start: number;
  end: number;
  source: CueSource;
  action: CueAction;
  status?: CueStatus;
  gain?: number;

  // Original detection metadata from the packaged index.
  trigger?: string;
  score?: number;
  detectorSource?: string;
}

export interface HeadsUpPromptOptions {
  enabled: boolean;
  leadTimeSeconds: number;
}

export const DEFAULT_HEADS_UP_PROMPT_OPTIONS: HeadsUpPromptOptions = {
  enabled: false,
  leadTimeSeconds: 5.0,
};

export interface ViewerOptions {
  hideEnabled: boolean;
  muteEnabled: boolean;
  reduceEnabled: boolean;
  skipEnabled: boolean;
  reduceGain: number;
  confirmReviewSkip: boolean;
  /**
   * Canonical trigger identifiers (e.g. 'flashing', 'loud_spike', 'profanity')
   * that the viewer has explicitly disabled. Cues matching a disabled trigger
   * category will not produce playback actions (mute, hide, reduce, skip, or warn).
   */
  disabledCategories?: string[];
  /**
   * Advance heads-up prompt options.
   * Disabled by default for backward compatibility.
   */
  headsUpPrompts?: HeadsUpPromptOptions;
}

export interface EffectiveActions {
  time: number;
  /**
   * `hide` is true when at least one active visual cue has action `hide`.
   * Kept for backward compatibility with existing callers.
   */
  hide: boolean;
  /**
   * The most specific visual action among active visual cues, or `null` when
   * there are no active visual cues.
   *
   * Priority order (highest to lowest): hide > blur > dim
   *
   * `blur` and `dim` have no Phase 0 rendering implementation.
   * The presence of a value here records what the config intends; the
   * caller is responsible for checking whether the rendering layer supports it.
   */
  visualAction: 'hide' | 'blur' | 'dim' | null;
  soundGain: number;
  skipTo: number | null;
  activeCueIds: string[];
  /**
   * IDs of active cues that specify the `warn` action, enabling heads-up
   * warning notifications without overriding audio/visual playback states.
   */
  warnCueIds: string[];
  /**
   * Active cue eligible for advance heads-up warning prompt during
   * [cue.start - leadTimeSeconds, cue.start), or null if none.
   */
  promptCue?: Cue | null;
}

export const DEFAULT_VIEWER_OPTIONS: ViewerOptions = {
  hideEnabled: true,
  muteEnabled: true,
  reduceEnabled: false,
  skipEnabled: false,
  reduceGain: 0.3,
  confirmReviewSkip: false,
  disabledCategories: [],
  headsUpPrompts: DEFAULT_HEADS_UP_PROMPT_OPTIONS,
};

/**
 * Resolves HeadsUpPromptOptions from partial input, applying default fallback values.
 * Maps snake_case configuration keys (heads_up_prompts) to camelCase if encountered.
 */
export function resolveHeadsUpPromptOptions(
  raw?: Partial<HeadsUpPromptOptions> | Record<string, unknown> | null,
): HeadsUpPromptOptions {
  if (!raw || typeof raw !== 'object') {
    return { ...DEFAULT_HEADS_UP_PROMPT_OPTIONS };
  }

  const source: Record<string, unknown> =
    typeof (raw as any).heads_up_prompts === 'object' && (raw as any).heads_up_prompts !== null
      ? (raw as any).heads_up_prompts
      : typeof (raw as any).headsUpPrompts === 'object' && (raw as any).headsUpPrompts !== null
      ? (raw as any).headsUpPrompts
      : (raw as Record<string, unknown>);

  const enabled =
    typeof source.enabled === 'boolean'
      ? source.enabled
      : DEFAULT_HEADS_UP_PROMPT_OPTIONS.enabled;

  const rawLeadTime =
    source.leadTimeSeconds ?? source.lead_time_seconds;
  const leadTimeSeconds =
    typeof rawLeadTime === 'number' && Number.isFinite(rawLeadTime) && rawLeadTime > 0
      ? rawLeadTime
      : DEFAULT_HEADS_UP_PROMPT_OPTIONS.leadTimeSeconds;

  return {
    enabled,
    leadTimeSeconds,
  };
}
