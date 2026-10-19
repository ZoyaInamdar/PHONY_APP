import { validatePlaybackInputs } from './validation';
import { resolveActions } from './resolveActions';
import { Cue, EffectiveActions, ViewerOptions } from './types';
import { adaptPackagedIndex } from './packagedIndexAdapter';

/**
 * Validates a canonical packaged index and enforces strict media consistency:
 * 1. Film ID alignment (preventing cross-film cue application).
 * 2. Media duration boundaries (rejecting cues extending past media duration).
 * 3. Schema and policy adaptation to player cues without fabrication.
 */
export function loadAndValidateFilmIndex(
  packagedIndex: unknown,
  triggerPolicies: Record<string, any>,
  expectedMedia?: {
    filmId?: string;
    durationSeconds?: number;
  },
): Cue[] {
  if (!packagedIndex || typeof packagedIndex !== 'object') {
    throw new Error('Packaged index must be a valid JSON object.');
  }

  const idxObj = packagedIndex as Record<string, unknown>;

  // 1. Film ID consistency check
  if (expectedMedia?.filmId) {
    const declaredFilmId =
      typeof idxObj.film_id === 'string' ? idxObj.film_id.trim().toLowerCase() : '';
    const cleanExpected = expectedMedia.filmId.trim().toLowerCase();
    if (declaredFilmId !== cleanExpected) {
      throw new Error(
        `Incompatible film index: expected film_id "${expectedMedia.filmId}", ` +
        `but packaged index specifies "${declaredFilmId}". ` +
        `Cross-film cue packaging is strictly forbidden.`
      );
    }
  }

  // 2. Adapt packaged cues into engine cues
  const adaptedCues = adaptPackagedIndex(packagedIndex, triggerPolicies);

  // 3. Media duration boundary enforcement
  if (
    typeof expectedMedia?.durationSeconds === 'number' &&
    expectedMedia.durationSeconds > 0
  ) {
    for (const cue of adaptedCues) {
      if (cue.end > expectedMedia.durationSeconds + 0.1) {
        throw new Error(
          `Cue "${cue.id}" interval [${cue.start}, ${cue.end}) extends beyond ` +
          `actual media duration (${expectedMedia.durationSeconds}s).`
        );
      }
    }
  }

  return adaptedCues;
}

/**
 * End-to-end resolver connecting packaged index to current playback actions:
 * Validates the index, adapts cues, and resolves effective actions for the given time.
 */
export function resolvePackagedIndexPlayback(
  time: number,
  packagedIndex: unknown,
  triggerPolicies: Record<string, any>,
  options: ViewerOptions,
  expectedMedia?: {
    filmId?: string;
    durationSeconds?: number;
  },
): EffectiveActions {
  const cues = loadAndValidateFilmIndex(packagedIndex, triggerPolicies, expectedMedia);
  return resolveActions(time, cues, options);
}

/**
 * Legacy validated playback resolver for initial test manifests.
 */
export function resolveValidatedPlayback(
  time: number,
  indexInput: unknown,
  manifestInput: unknown,
  options: ViewerOptions,
): EffectiveActions {
  const index = validatePlaybackInputs(indexInput, manifestInput);
  const cues: Cue[] = index.cues;
  return resolveActions(time, cues, options);
}