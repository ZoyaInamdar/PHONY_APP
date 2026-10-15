import { adaptPackagedIndex } from '../../src/engine/packagedIndexAdapter';

const packagedIndex = {
  schema_version: 1,
  film_id: 'sintel',
  timeline: {
    duration_seconds: 52,
    timestamp_unit: 'seconds',
    interval_convention: 'half_open',
    fps: 24,
  },
  cues: [
    {
      id: 'cue-a',
      start: 10.2,
      end: 11.8,
      trigger: 'loud_spike',
      score: 0.92,
      source: 'loudness_detector',
    },
  ],
};

const triggerPolicies = {
  loud_spike: {
    enabled: true,
    action: 'mute',
    source: 'audio',
  },
  fast_motion: {
    enabled: false,
    action: 'warn',
    source: 'visual',
  },
  flashing: {
    enabled: true,
    action: 'dim',
    source: 'visual',
  },
  spider: {
    enabled: true,
    action: 'blur',
    source: 'visual',
  },
};

describe('adaptPackagedIndex', () => {
  it('converts a supported cue and preserves its ID and timing', () => {
    const result = adaptPackagedIndex(packagedIndex, triggerPolicies);

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: 'cue-a',
      start: 10.2,
      end: 11.8,
      source: 'audio',
      action: 'mute',
    });
  });

  it('does not activate cues belonging to disabled triggers', () => {
    const index = {
      ...packagedIndex,
      cues: [
        {
          ...packagedIndex.cues[0],
          trigger: 'fast_motion',
        },
      ],
    };

    expect(adaptPackagedIndex(index, triggerPolicies)).toEqual([]);
  });

  it('carries dim action faithfully without converting it to hide (flashing trigger)', () => {
    // dim is a representable action. The adapter must NOT throw and must NOT
    // convert dim to hide. The caller is responsible for knowing that dim has
    // no Phase 0 rendering implementation.
    const index = {
      ...packagedIndex,
      cues: [
        {
          ...packagedIndex.cues[0],
          trigger: 'flashing',
        },
      ],
    };

    const result = adaptPackagedIndex(index, triggerPolicies);
    expect(result).toHaveLength(1);
    expect(result[0].action).toBe('dim');
    expect(result[0].action).not.toBe('hide');
  });

  it('carries blur action faithfully without converting it to hide (spider trigger)', () => {
    // blur is a representable action. The adapter must NOT throw and must NOT
    // convert blur to hide. The caller is responsible for knowing that blur has
    // no Phase 0 rendering implementation.
    const index = {
      ...packagedIndex,
      cues: [
        {
          ...packagedIndex.cues[0],
          trigger: 'spider',
        },
      ],
    };

    const result = adaptPackagedIndex(index, triggerPolicies);
    expect(result).toHaveLength(1);
    expect(result[0].action).toBe('blur');
    expect(result[0].action).not.toBe('hide');
  });

  it('rejects malformed packaged indexes', () => {
    const malformedIndex = {
      ...packagedIndex,
      schema_version: '1',
    };

    expect(() => adaptPackagedIndex(malformedIndex, triggerPolicies)).toThrow();
  });

  it('rejects duplicate cue IDs', () => {
    const duplicateIndex = {
      ...packagedIndex,
      cues: [
        packagedIndex.cues[0],
        {
          ...packagedIndex.cues[0],
          start: 20,
          end: 21,
        },
      ],
    };

    expect(() => adaptPackagedIndex(duplicateIndex, triggerPolicies)).toThrow(
      /duplicate/i,
    );
  });

  it('rejects cues that are not in chronological order', () => {
    const unorderedIndex = {
      ...packagedIndex,
      cues: [
        {
          ...packagedIndex.cues[0],
          id: 'later',
          start: 20,
          end: 21,
        },
        {
          ...packagedIndex.cues[0],
          id: 'earlier',
          start: 10,
          end: 11,
        },
      ],
    };

    expect(() => adaptPackagedIndex(unorderedIndex, triggerPolicies)).toThrow(
      /chronolog|order/i,
    );
  });

  it('rejects cues that extend beyond the film duration', () => {
    const outOfBoundsIndex = {
      ...packagedIndex,
      cues: [
        {
          ...packagedIndex.cues[0],
          start: 51,
          end: 53,
        },
      ],
    };

    expect(() => adaptPackagedIndex(outOfBoundsIndex, triggerPolicies)).toThrow(
      /invalid cue|duration|bounds/i,
    );
  });

  it('rejects a cue with a missing detector source', () => {
    const missingSourceIndex = {
      ...packagedIndex,
      cues: [
        {
          ...packagedIndex.cues[0],
          source: '',
        },
      ],
    };

    expect(() => adaptPackagedIndex(missingSourceIndex, triggerPolicies)).toThrow(
      /invalid cue|source/i,
    );
  });

  it('accepts the shared config field names', () => {
    const sharedPolicies = {
      loud_spike: {
        enabled: true,
        modality: 'audio',
        default_action: 'mute',
        allowed_actions: ['mute', 'skip', 'warn'],
      },
    };

    const result = adaptPackagedIndex(packagedIndex, sharedPolicies);

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: 'cue-a',
      start: 10.2,
      end: 11.8,
      source: 'audio',
      action: 'mute',
    });
  });

  it('does not silently convert dim into hide (shared config flashing)', () => {
    // dim is now representable. The adapter must carry it as 'dim', not
    // convert it to 'hide'. This test confirms no silent conversion occurs.
    const sharedPolicies = {
      flashing: {
        enabled: true,
        modality: 'visual',
        default_action: 'dim',
        allowed_actions: ['dim', 'blur', 'skip', 'warn'],
      },
    };

    const index = {
      ...packagedIndex,
      cues: [
        {
          ...packagedIndex.cues[0],
          trigger: 'flashing',
        },
      ],
    };

    const result = adaptPackagedIndex(index, sharedPolicies);
    expect(result).toHaveLength(1);
    expect(result[0].action).toBe('dim');
    expect(result[0].action).not.toBe('hide');
  });

  it('does not silently convert blur into hide (shared config spider)', () => {
    // blur is now representable. The adapter must carry it as 'blur', not
    // convert it to 'hide'. This test confirms no silent conversion occurs.
    const sharedPolicies = {
      spider: {
        enabled: true,
        modality: 'visual',
        default_action: 'blur',
        allowed_actions: ['blur', 'skip', 'warn'],
      },
    };

    const index = {
      ...packagedIndex,
      cues: [
        {
          ...packagedIndex.cues[0],
          trigger: 'spider',
        },
      ],
    };

    const result = adaptPackagedIndex(index, sharedPolicies);
    expect(result).toHaveLength(1);
    expect(result[0].action).toBe('blur');
    expect(result[0].action).not.toBe('hide');
  });

  it('retains detector metadata on the adapted cue', () => {
    const result = adaptPackagedIndex(packagedIndex, triggerPolicies);

    expect(result[0]).toMatchObject({
      id: 'cue-a',
      trigger: 'loud_spike',
      score: 0.92,
      detectorSource: 'loudness_detector',
    });
  });
});