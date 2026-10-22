import { Cue } from '../engine/types';
import type { SubtitleCue } from '../subtitles/types';
import { validateSubtitleCues } from '../subtitles/validation';
import { adaptPackagedIndex } from '../engine/packagedIndexAdapter';
import { DEFAULT_TRIGGER_POLICIES } from './defaultTriggerPolicies';

export { validateSubtitleCues };

export interface FilmCatalogueEntry {
  film_id: string;
  title: string;
  description: string;
  duration_seconds: number;
  fps: number;
  video_url: string;
  vega_video_url?: string;
  index_path: string;
  loadIndex: () => unknown;
  subtitles_path?: string | null;
  loadSubtitles?: () => SubtitleCue[];
}

export const FILM_CATALOGUE: Record<string, FilmCatalogueEntry> = {
  sintel: {
    film_id: 'sintel',
    title: 'Sintel',
    description:
      'Blender open movie project (Trailer). A lonely young woman searches for a baby dragon.',
    duration_seconds: 52.0,
    fps: 24.0,
    video_url: 'https://media.w3.org/2010/05/sintel/trailer.mp4',
    index_path: 'data/index.json',
    loadIndex: () => require('../../data/index.json'),
    subtitles_path: 'data/processed/sintel/cues.json',
    loadSubtitles: () => require('../../data/processed/sintel/cues.json'),
  },
  tears_of_steel: {
    film_id: 'tears_of_steel',
    title: 'Tears of Steel',
    description:
      'Blender open movie project (Full film). Set in a dystopian future in Amsterdam.',
    duration_seconds: 734.167,
    fps: 24.0,
    video_url:
      'https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov',
    index_path: 'data/tears_of_steel_index.json',
    loadIndex: () => require('../../data/tears_of_steel_index.json'),
    subtitles_path: 'data/processed/tears_of_steel/cues.json',
    loadSubtitles: () => require('../../data/processed/tears_of_steel/cues.json'),
  },
  elephants_dream: {
    film_id: 'elephants_dream',
    title: 'Elephants Dream',
    description:
      "Orange Open Movie Project (2006) by Blender Foundation. The world's first open movie.",
    duration_seconds: 653.83,
    fps: 24.0,
    video_url: 'data/media/elephant_dreams.mp4',
    vega_video_url: 'http://10.0.2.2:8080/data/media/elephant_dreams.mp4',
    index_path: 'data/elephants_dream_index.json',
    loadIndex: () => require('../../data/elephants_dream_index.json'),
    subtitles_path: null,
    loadSubtitles: () => [],
  },
};

/**
 * Returns all discoverable film catalogue entries.
 */
export function getAllFilms(): FilmCatalogueEntry[] {
  return Object.values(FILM_CATALOGUE);
}

/**
 * Returns metadata for a film, or null if not found.
 */
export function getFilmMetadata(filmId: string): FilmCatalogueEntry | null {
  if (typeof filmId !== 'string' || !filmId.trim()) {
    return null;
  }
  return FILM_CATALOGUE[filmId] ?? null;
}

/**
 * Returns the catalogue entry for a film, or throws a descriptive error if not found.
 * Never silently falls back to another film.
 */
export function getFilm(filmId: string): FilmCatalogueEntry {
  const entry = getFilmMetadata(filmId);
  if (!entry) {
    const available = Object.keys(FILM_CATALOGUE).join(', ');
    throw new Error(
      `Unknown film ID: "${filmId}". Available catalogue films: ${available}.`,
    );
  }
  return entry;
}

/**
 * Loads the raw packaged index for a given film.
 * Validates that the index is a valid object and that the index's film_id
 * strictly matches the requested filmId to prevent cross-film cue contamination.
 */
export function loadFilmIndex(filmId: string): unknown {
  const entry = getFilm(filmId);
  let rawIndex: any;
  try {
    rawIndex = entry.loadIndex();
  } catch (err: any) {
    throw new Error(
      `Failed to load packaged index for "${filmId}" from "${entry.index_path}": ${err?.message ?? err}`,
    );
  }

  if (!rawIndex || typeof rawIndex !== 'object') {
    throw new Error(
      `Packaged index for "${filmId}" at "${entry.index_path}" is not a valid object.`,
    );
  }

  if (rawIndex.film_id !== filmId) {
    throw new Error(
      `Packaged index film_id mismatch: expected "${filmId}", but found "${rawIndex.film_id}".`,
    );
  }

  return rawIndex;
}

/**
 * Loads and adapts trigger cues for a given film using configured policies.
 * Never silently falls back if the index is missing or invalid.
 */
export function loadFilmCues(filmId: string, customPolicies?: unknown): Cue[] {
  const rawIndex = loadFilmIndex(filmId);
  const policies =
    (customPolicies as any) ?? DEFAULT_TRIGGER_POLICIES;
  return adaptPackagedIndex(rawIndex, policies);
}

/**
 * Loads parsed subtitle cues for a given film.
 * Returns empty array if the film has no subtitle tracks configured.
 * Never silently falls back to another film's subtitles.
 */
export function loadFilmSubtitles(filmId: string): SubtitleCue[] {
  const entry = getFilm(filmId);
  if (!entry.loadSubtitles) {
    return [];
  }
  let rawSubtitles: any;
  try {
    rawSubtitles = entry.loadSubtitles();
  } catch (err: any) {
    throw new Error(
      `Failed to load subtitles for "${filmId}" from "${entry.subtitles_path}": ${err?.message ?? err}`,
    );
  }

  if (!Array.isArray(rawSubtitles)) {
    throw new Error(
      `Subtitles for "${filmId}" at "${entry.subtitles_path}" must be an array.`,
    );
  }

  return validateSubtitleCues(rawSubtitles, { filmId });
}

