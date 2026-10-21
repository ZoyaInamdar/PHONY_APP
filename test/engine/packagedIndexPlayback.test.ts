import {
  loadAndValidateFilmIndex,
  resolvePackagedIndexPlayback,
} from '../../src/engine/validatedPlayback';
import {
  FILM_CATALOGUE,
  getFilmMetadata,
  getAllFilms,
  getFilmCues,
} from '../../src/data/filmCatalogue';
import { DEFAULT_VIEWER_OPTIONS, ViewerOptions } from '../../src/engine/types';

// Load policy fixtures
// eslint-disable-next-line @typescript-eslint/no-var-requires
const policyFixture = require('../fixtures/triggerPolicies.json') as {
  policies: Record<
    string,
    {
      enabled: boolean;
      modality: string;
      default_action: string;
      allowed_actions: string[];
    }
  >;
};

const POLICIES = policyFixture.policies;

describe('loadAndValidateFilmIndex', () => {
  const validPackagedIndex = {
    schema_version: 1,
    film_id: 'sintel_trailer',
    timeline: {
      duration_seconds: 52.21,
      timestamp_unit: 'seconds',
      interval_convention: 'half_open',
      fps: 24,
    },
    cues: [
      {
        id: 'cue-loud-1',
        start: 1.8,
        end: 10.6,
        trigger: 'loud_spike',
        score: 0.95,
        source: 'loudness_detector',
      },
      {
        id: 'cue-flash-1',
        start: 24.0,
        end: 25.5,
        trigger: 'flashing',
        score: 0.88,
        source: 'flashing_detector',
      },
    ],
  };

  it('successfully loads and adapts valid index with matching filmId and duration', () => {
    const cues = loadAndValidateFilmIndex(validPackagedIndex, POLICIES, {
      filmId: 'sintel_trailer',
      durationSeconds: 52.21,
    });

    expect(cues).toHaveLength(2);
    expect(cues[0]).toMatchObject({
      id: 'cue-loud-1',
      start: 1.8,
      end: 10.6,
      source: 'audio',
      action: POLICIES.loud_spike.default_action,
    });
    expect(cues[1]).toMatchObject({
      id: 'cue-flash-1',
      start: 24.0,
      end: 25.5,
      source: 'visual',
      action: POLICIES.flashing.default_action,
    });
  });

  it('rejects cross-film cue application when film_id does not match', () => {
    expect(() =>
      loadAndValidateFilmIndex(validPackagedIndex, POLICIES, {
        filmId: 'tears_of_steel',
        durationSeconds: 734.167,
      }),
    ).toThrow(/Incompatible film index: expected film_id "tears_of_steel"/);
  });

  it('rejects cues extending beyond declared media duration', () => {
    const outOfBoundsIndex = {
      ...validPackagedIndex,
      timeline: {
        ...validPackagedIndex.timeline,
        duration_seconds: 60.0,
      },
      cues: [
        {
          id: 'cue-overflow',
          start: 50.0,
          end: 55.0,
          trigger: 'loud_spike',
          score: 0.9,
          source: 'loudness_detector',
        },
      ],
    };

    expect(() =>
      loadAndValidateFilmIndex(outOfBoundsIndex, POLICIES, {
        filmId: 'sintel_trailer',
        durationSeconds: 52.21,
      }),
    ).toThrow(/extends beyond actual media duration/);
  });

  it('rejects non-object index input', () => {
    expect(() => loadAndValidateFilmIndex(null, POLICIES)).toThrow(
      /Packaged index must be a valid JSON object/,
    );
    expect(() => loadAndValidateFilmIndex('invalid-json', POLICIES)).toThrow(
      /Packaged index must be a valid JSON object/,
    );
  });
});

describe('resolvePackagedIndexPlayback', () => {
  const packagedIndex = {
    schema_version: 1,
    film_id: 'sintel_trailer',
    timeline: {
      duration_seconds: 52.21,
      timestamp_unit: 'seconds',
      interval_convention: 'half_open',
      fps: 24,
    },
    cues: [
      {
        id: 'cue-spike',
        start: 5.0,
        end: 8.0,
        trigger: 'loud_spike',
        score: 0.95,
        source: 'loudness_detector',
      },
    ],
  };

  const options: ViewerOptions = {
    ...DEFAULT_VIEWER_OPTIONS,
    muteEnabled: true,
    actionOverrides: { loud_spike: 'mute' },
  };

  it('enforces half-open interval [start, end)', () => {
    // Before start: soundGain = 1
    const before = resolvePackagedIndexPlayback(4.99, packagedIndex, POLICIES, options, {
      filmId: 'sintel_trailer',
      durationSeconds: 52.21,
    });
    expect(before.soundGain).toBe(1);

    // At start: active (soundGain = 0)
    const atStart = resolvePackagedIndexPlayback(5.0, packagedIndex, POLICIES, options, {
      filmId: 'sintel_trailer',
      durationSeconds: 52.21,
    });
    expect(atStart.soundGain).toBe(0);

    // During interval: active (soundGain = 0)
    const during = resolvePackagedIndexPlayback(6.5, packagedIndex, POLICIES, options, {
      filmId: 'sintel_trailer',
      durationSeconds: 52.21,
    });
    expect(during.soundGain).toBe(0);

    // At end: inactive (soundGain = 1)
    const atEnd = resolvePackagedIndexPlayback(8.0, packagedIndex, POLICIES, options, {
      filmId: 'sintel_trailer',
      durationSeconds: 52.21,
    });
    expect(atEnd.soundGain).toBe(1);
  });

  it('respects viewer options disabling mute', () => {
    const unmutedOptions: ViewerOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      muteEnabled: false,
    };

    const result = resolvePackagedIndexPlayback(6.0, packagedIndex, POLICIES, unmutedOptions, {
      filmId: 'sintel_trailer',
      durationSeconds: 52.21,
    });
    expect(result.soundGain).toBe(1);
  });

  it('enforces audio priority: mute strictly overrides volume reduction during overlap', () => {
    const overlappingAudioIndex = {
      schema_version: 1,
      film_id: 'sintel_trailer',
      timeline: {
        duration_seconds: 52.21,
        timestamp_unit: 'seconds',
        interval_convention: 'half_open',
        fps: 24,
      },
      cues: [
        {
          id: 'cue-reduce',
          start: 2.0,
          end: 6.0,
          trigger: 'loud_spike',
          score: 0.8,
          source: 'loudness_detector',
        },
        {
          id: 'cue-mute',
          start: 3.0,
          end: 5.0,
          trigger: 'gunshot',
          score: 0.9,
          source: 'audio_event_classifier',
        },
      ],
    };

    const policiesWithReduce = {
      ...POLICIES,
      loud_spike: {
        enabled: true,
        action: 'reduce',
        source: 'audio',
      },
      gunshot: {
        enabled: true,
        action: 'mute',
        source: 'audio',
      },
    };

    const optionsWithBoth: ViewerOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      muteEnabled: true,
      reduceEnabled: true,
      reduceGain: 0.25,
    };

    // At 2.5s: only reduce is active -> soundGain = 0.25
    const at2_5 = resolvePackagedIndexPlayback(
      2.5,
      overlappingAudioIndex,
      policiesWithReduce,
      optionsWithBoth,
      { filmId: 'sintel_trailer', durationSeconds: 52.21 },
    );
    expect(at2_5.soundGain).toBe(0.25);

    // At 3.5s: both reduce and mute are active -> mute strictly takes precedence (soundGain = 0)
    const at3_5 = resolvePackagedIndexPlayback(
      3.5,
      overlappingAudioIndex,
      policiesWithReduce,
      optionsWithBoth,
      { filmId: 'sintel_trailer', durationSeconds: 52.21 },
    );
    expect(at3_5.soundGain).toBe(0);

    // At 5.5s: mute has ended, reduce remains active -> soundGain restored to 0.25
    const at5_5 = resolvePackagedIndexPlayback(
      5.5,
      overlappingAudioIndex,
      policiesWithReduce,
      optionsWithBoth,
      { filmId: 'sintel_trailer', durationSeconds: 52.21 },
    );
    expect(at5_5.soundGain).toBe(0.25);

    // At 6.5s: all cues ended -> soundGain restored to 1.0
    const at6_5 = resolvePackagedIndexPlayback(
      6.5,
      overlappingAudioIndex,
      policiesWithReduce,
      optionsWithBoth,
      { filmId: 'sintel_trailer', durationSeconds: 52.21 },
    );
    expect(at6_5.soundGain).toBe(1);
  });

  it('respects viewer disabledCategories by ignoring disabled triggers entirely', () => {
    const disabledOptions: ViewerOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      muteEnabled: true,
      disabledCategories: ['loud_spike'],
    };

    const result = resolvePackagedIndexPlayback(6.0, packagedIndex, POLICIES, disabledOptions, {
      filmId: 'sintel_trailer',
      durationSeconds: 52.21,
    });
    // Even though time 6.0 is inside the loud_spike cue, it is ignored because loud_spike is disabled
    expect(result.soundGain).toBe(1);
    expect(result.activeCueIds).not.toContain('cue-spike');
  });

  it('enforces needs_review policy: does not skip without confirmReviewSkip', () => {
    const skipIndex = {
      schema_version: 1,
      film_id: 'sintel_trailer',
      timeline: {
        duration_seconds: 52.21,
        timestamp_unit: 'seconds',
        interval_convention: 'half_open',
        fps: 24,
      },
      cues: [
        {
          id: 'cue-skip-review',
          start: 10.0,
          end: 15.0,
          trigger: 'explosion',
          score: 0.65,
          source: 'audio_event_classifier',
          status: 'needs_review',
        },
      ],
    };

    const skipPolicies = {
      ...POLICIES,
      explosion: {
        enabled: true,
        action: 'skip',
        source: 'audio',
      },
    };

    const unconfirmedOptions: ViewerOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      skipEnabled: true,
      confirmReviewSkip: false,
      muteEnabled: true,
    };

    // Unconfirmed: skipTo must be null, but falls back to mute because source is audio
    const unconfirmed = resolvePackagedIndexPlayback(
      11.0,
      skipIndex,
      skipPolicies,
      unconfirmedOptions,
      { filmId: 'sintel_trailer', durationSeconds: 52.21 },
    );
    expect(unconfirmed.skipTo).toBeNull();
    expect(unconfirmed.soundGain).toBe(0);

    // Confirmed: skipTo jumps past cue end
    const confirmedOptions: ViewerOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      skipEnabled: true,
      confirmReviewSkip: true,
      muteEnabled: true,
    };

    const confirmed = resolvePackagedIndexPlayback(
      11.0,
      skipIndex,
      skipPolicies,
      confirmedOptions,
      { filmId: 'sintel_trailer', durationSeconds: 52.21 },
    );
    expect(confirmed.skipTo).toBe(15.0);
  });
});

describe('Film Catalogue and Real Packaged Index Integration', () => {
  it('correctly reports availability for Sintel Trailer and Tears of Steel', () => {
    const films = getAllFilms();
    expect(films).toHaveLength(2);

    const sintel = getFilmMetadata('sintel_trailer');
    expect(sintel).toBeDefined();
    expect(sintel?.isAvailable).toBe(true);
    expect(sintel?.durationSeconds).toBe(52.21);
    expect(sintel?.packagedIndex).toBeDefined();

    const tears = getFilmMetadata('tears_of_steel');
    expect(tears).toBeDefined();
    expect(tears?.isAvailable).toBe(false);
    expect(tears?.unavailableReason).toContain('absent locally');
  });

  it('loads real packaged cues for Sintel Trailer without errors', () => {
    const cues = getFilmCues('sintel_trailer');
    expect(cues.length).toBeGreaterThan(0);

    // All cues must have valid timestamps within media duration
    for (const cue of cues) {
      expect(cue.start).toBeGreaterThanOrEqual(0);
      expect(cue.end).toBeLessThanOrEqual(52.21);
      expect(cue.end).toBeGreaterThan(cue.start);
    }
  });

  it('returns empty array or handles unavailable film requests safely', () => {
    const unknownCues = getFilmCues('unknown_film');
    expect(unknownCues).toEqual([]);
  });
});

describe('Demo Mode and Test Fixture Isolation', () => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const realSintelIndex = require('../../data/processed/sintel_trailer/index.json');
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const syntheticIndex = require('../../data/index.json');
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { verificationTestCues, skipVerificationTestCues } = require('../../src/data/playbackCues');

  it('keeps real film packaged indexes strictly non-synthetic and uncorrupted', () => {
    expect(realSintelIndex.synthetic).toBe(false);
    expect(realSintelIndex.film_id).toBe('sintel_trailer');
    expect(realSintelIndex.cues.length).toBeGreaterThan(0);

    // All real cues must originate from real detector sources, not test fixtures
    for (const cue of realSintelIndex.cues) {
      expect([
        'loudness_detector',
        'audio_event_classifier',
        'flash_detector',
        'flashing_detector',
        'profanity_detector',
      ]).toContain(cue.source);
      expect(cue.id).not.toContain('verify');
    }
  });

  it('preserves synthetic index marking for benchmark data', () => {
    expect(syntheticIndex.synthetic).toBe(true);
    expect(syntheticIndex.film_id).toBe('sintel');
  });

  it('demonstrates all required player actions across demo test fixtures', () => {
    const allDemoCues = [...verificationTestCues, ...skipVerificationTestCues];
    const actions = new Set(allDemoCues.map((c: any) => c.action));

    // Verify all primary player actions are present
    expect(actions.has('hide')).toBe(true);
    expect(actions.has('mute')).toBe(true);
    expect(actions.has('reduce')).toBe(true);
    expect(actions.has('skip')).toBe(true);

    // Verify presence of needs_review cue
    const needsReviewCues = allDemoCues.filter((c: any) => c.status === 'needs_review');
    expect(needsReviewCues.length).toBeGreaterThan(0);
    expect(needsReviewCues[0].action).toBe('skip');

    // Verify overlapping mute and reduce in verification fixture
    const overlapReduce = verificationTestCues.find((c: any) => c.id === 'cue-verify-overlap-reduce');
    const overlapMute = verificationTestCues.find((c: any) => c.id === 'cue-verify-overlap-mute');
    expect(overlapReduce).toBeDefined();
    expect(overlapMute).toBeDefined();
    expect(overlapMute.start).toBeGreaterThan(overlapReduce.start);
    expect(overlapMute.end).toBeLessThan(overlapReduce.end);
  });
});
