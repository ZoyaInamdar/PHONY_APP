import type { SubtitleCue } from './types';
import { extractFlaggedWords, processCueText } from './censoring';
import { resolveActions } from '../engine/resolveActions';
import { Cue, DEFAULT_VIEWER_OPTIONS, ViewerOptions } from '../engine/types';
import { sintelIndexCues } from '../data/playbackCues';

// Load default cues from processed subtitle artifacts (fallback to sintel or subtitle_test)
let defaultParsedCues: SubtitleCue[] = [];
try {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  defaultParsedCues = require('../../data/processed/sintel/cues.json');
} catch {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    defaultParsedCues = require('../../data/processed/subtitle_test/cues.json');
  } catch {
    defaultParsedCues = [];
  }
}

export interface ActiveCaptionResult {
  id: string;
  start: number;
  end: number;
  kind: 'speech' | 'sound_description';
  rawText: string;
  processedText: string;
  flaggedWords: string[];
  isActionDemanded: boolean;
}

export interface CaptionUserSettings {
  replaceSoundDescriptions?: boolean;
  censorProfanity?: boolean;
  viewerOptions?: ViewerOptions;
  disabledCategories?: string[];
  cues?: SubtitleCue[];
  engineCues?: Cue[];
  [key: string]: any;
}

/**
 * Pure calculation function that determines the active caption at currentTime,
 * queries Person A's engine, and applies censoring/replacement.
 */
export function getActiveCaption(
  currentTime: number,
  userSettings?: CaptionUserSettings | any,
): ActiveCaptionResult | null {
  if (!Number.isFinite(currentTime) || currentTime < 0) {
    return null;
  }

  const subtitleCues: SubtitleCue[] =
    userSettings?.cues ?? userSettings?.subtitleCues ?? defaultParsedCues;
  if (!Array.isArray(subtitleCues)) {
    return null;
  }

  // Find the active subtitle cue at the current playback time [start, end)
  const activeCue = subtitleCues.find(
    (cue) =>
      cue &&
      typeof cue === 'object' &&
      typeof cue.start === 'number' &&
      typeof cue.end === 'number' &&
      cue.start <= currentTime &&
      currentTime < cue.end,
  );

  if (!activeCue) {
    return null;
  }

  // Resolve viewer options for Person A's engine
  const viewerOptions: ViewerOptions = {
    ...DEFAULT_VIEWER_OPTIONS,
    ...(userSettings?.viewerOptions ?? {}),
    disabledCategories:
      userSettings?.disabledCategories ??
      userSettings?.viewerOptions?.disabledCategories ??
      DEFAULT_VIEWER_OPTIONS.disabledCategories ??
      [],
  };

  const engineCues: Cue[] = userSettings?.engineCues ?? sintelIndexCues;

  // Query Person A's engine to see if trigger action resolution demands action at this time
  let isActionDemanded = true;
  try {
    resolveActions(currentTime, engineCues, viewerOptions);
    if (viewerOptions.disabledCategories?.includes('profanity')) {
      isActionDemanded = false;
    }
  } catch {
    if (viewerOptions.disabledCategories?.includes('profanity')) {
      isActionDemanded = false;
    }
  }

  // Direct override in userSettings if provided
  if (userSettings?.censorProfanity === false) {
    isActionDemanded = false;
  }

  const replaceSoundDescriptions = Boolean(
    userSettings?.replaceSoundDescriptions ??
      userSettings?.options?.replaceSoundDescriptions,
  );

  const flaggedWords = extractFlaggedWords(activeCue);

  // If action is demanded for profanity, censor flagged words; otherwise leave speech text uncensored
  const wordsToCensor = isActionDemanded ? flaggedWords : [];
  const rawText = typeof activeCue.text === 'string' ? activeCue.text : '';
  const cueKind =
    activeCue.kind === 'sound_description' ? 'sound_description' : 'speech';
  const processedText = processCueText(
    rawText,
    cueKind,
    wordsToCensor,
    replaceSoundDescriptions,
  );

  return {
    id: typeof activeCue.id === 'string' ? activeCue.id : String(activeCue.id ?? ''),
    start: activeCue.start,
    end: activeCue.end,
    kind: cueKind,
    rawText,
    processedText,
    flaggedWords,
    isActionDemanded,
  };
}
