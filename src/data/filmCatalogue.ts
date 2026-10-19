import { Cue } from '../engine/types';
import { loadAndValidateFilmIndex } from '../engine/validatedPlayback';

// eslint-disable-next-line @typescript-eslint/no-var-requires
const triggerPoliciesJson = require('../../test/fixtures/triggerPolicies.json');

export interface FilmMetadata {
  id: string;
  title: string;
  description: string;
  durationSeconds: number;
  videoUrl: string | null;
  subtitlesUrl: string | null;
  packagedIndex: unknown | null;
  isAvailable: boolean;
  unavailableReason?: string;
}

export const FILM_CATALOGUE: Record<string, FilmMetadata> = {
  sintel_trailer: {
    id: 'sintel_trailer',
    title: 'Sintel (Trailer)',
    description: 'Durian Open Movie Project — 52s verified demonstration trailer.',
    durationSeconds: 52.21,
    videoUrl: 'https://media.w3.org/2010/05/sintel/trailer.mp4',
    subtitlesUrl: null, // Sintel trailer has no dialogue/subtitles
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    packagedIndex: require('../../data/processed/sintel_trailer/index.json'),
    isAvailable: true,
  },
  tears_of_steel: {
    id: 'tears_of_steel',
    title: 'Tears of Steel',
    description: 'Mango Open Movie Project — 734s sci-fi film.',
    durationSeconds: 734.167,
    videoUrl: null, // Full video asset tears_of_steel.webm is absent locally
    subtitlesUrl: 'data/subtitles/tears_of_steel.srt',
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    packagedIndex: require('../../data/processed/tears_of_steel/index.json'),
    isAvailable: false,
    unavailableReason: 'Full video media asset (tears_of_steel.webm) is absent locally.',
  },
};

/**
 * Retrieve verified film metadata by film ID.
 */
export function getFilmMetadata(filmId: string): FilmMetadata | undefined {
  return FILM_CATALOGUE[filmId];
}

/**
 * List all catalogue films with their availability status.
 */
export function getAllFilms(): FilmMetadata[] {
  return Object.values(FILM_CATALOGUE);
}

/**
 * Loads, validates, and adapts the packaged cues for a given film.
 * Rejects mismatched film assets or cues exceeding media duration.
 */
export function getFilmCues(filmId: string): Cue[] {
  const film = FILM_CATALOGUE[filmId];
  if (!film || !film.packagedIndex) {
    return [];
  }
  return loadAndValidateFilmIndex(
    film.packagedIndex,
    triggerPoliciesJson.policies,
    {
      filmId: film.id,
      durationSeconds: film.durationSeconds,
    },
  );
}
