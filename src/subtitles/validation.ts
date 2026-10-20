import type { SubtitleCue } from './types';

export interface SubtitleValidationOptions {
  filmId?: string;
  maxDuration?: number;
}

const SUPPORTED_CUE_KINDS = new Set(['speech', 'sound_description']);

/**
 * Validates raw subtitle data at runtime against the SubtitleCue schema and contract.
 * Rejects malformed payloads, non-objects, missing required fields, non-finite/negative timestamps,
 * inverted intervals (start >= end), invalid kinds, non-string text, or cues exceeding maxDuration.
 * Preserves optional fields and metadata.
 */
export function validateSubtitleCues(
  rawSubtitles: unknown,
  options?: SubtitleValidationOptions,
): SubtitleCue[] {
  const filmContext = options?.filmId ? ` for film "${options.filmId}"` : '';

  if (!Array.isArray(rawSubtitles)) {
    throw new Error(`Invalid subtitle payload${filmContext}: expected an array.`);
  }

  const maxDuration = options?.maxDuration;
  const hasMaxDuration =
    typeof maxDuration === 'number' && Number.isFinite(maxDuration) && maxDuration > 0;

  for (let i = 0; i < rawSubtitles.length; i++) {
    const cue = rawSubtitles[i];
    const cuePrefix = `Invalid subtitle cue at index ${i}${filmContext}`;

    if (!cue || typeof cue !== 'object' || Array.isArray(cue)) {
      throw new Error(`${cuePrefix}: expected a non-null object.`);
    }

    // 1. Required "id" validation
    if (typeof cue.id !== 'string' || cue.id.trim().length === 0) {
      throw new Error(`${cuePrefix}: "id" must be a non-empty string.`);
    }
    const cueId = cue.id;
    const identifiedPrefix = `${cuePrefix} ("${cueId}")`;

    // 2. Required "start" validation
    if (typeof cue.start !== 'number' || !Number.isFinite(cue.start)) {
      throw new Error(`${identifiedPrefix}: "start" must be a finite number.`);
    }
    if (cue.start < 0) {
      throw new Error(`${identifiedPrefix}: "start" cannot be negative (${cue.start}).`);
    }

    // 3. Required "end" validation
    if (typeof cue.end !== 'number' || !Number.isFinite(cue.end)) {
      throw new Error(`${identifiedPrefix}: "end" must be a finite number.`);
    }
    if (cue.end < 0) {
      throw new Error(`${identifiedPrefix}: "end" cannot be negative (${cue.end}).`);
    }

    // 4. Interval convention: start must be strictly less than end (half-open [start, end))
    if (cue.start >= cue.end) {
      throw new Error(
        `${identifiedPrefix}: "start" (${cue.start}) must be strictly less than "end" (${cue.end}).`,
      );
    }

    // 5. Authoritative film duration boundary check (if supplied)
    if (hasMaxDuration && cue.end > maxDuration!) {
      throw new Error(
        `${identifiedPrefix}: cue end (${cue.end}) exceeds film duration (${maxDuration}).`,
      );
    }

    // 6. Required "kind" validation
    if (typeof cue.kind !== 'string' || !SUPPORTED_CUE_KINDS.has(cue.kind)) {
      throw new Error(
        `${identifiedPrefix}: "kind" must be either "speech" or "sound_description", received "${cue.kind}".`,
      );
    }

    // 7. Required "text" validation
    if (typeof cue.text !== 'string') {
      throw new Error(`${identifiedPrefix}: "text" must be a string.`);
    }

    // 8. Optional fields validation (preserves valid metadata without rejecting)
    if (cue.flagged !== undefined && !Array.isArray(cue.flagged)) {
      throw new Error(`${identifiedPrefix}: optional "flagged" must be an array.`);
    }
    if (cue.flagged_words !== undefined && !Array.isArray(cue.flagged_words)) {
      throw new Error(`${identifiedPrefix}: optional "flagged_words" must be an array.`);
    }
  }

  return rawSubtitles as SubtitleCue[];
}
