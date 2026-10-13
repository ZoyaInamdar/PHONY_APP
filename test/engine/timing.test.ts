import {
  applySourcePadding,
  mergeNearbyAudioCues,
} from '../../src/engine/timing';
import { Cue } from '../../src/engine/types';

const cues: Cue[] = [
  {
    id: 'visual-1',
    start: 5,
    end: 7,
    source: 'visual',
    action: 'hide',
  },
  {
    id: 'audio-1',
    start: 10,
    end: 11,
    source: 'audio',
    action: 'mute',
  },
];

describe('applySourcePadding', () => {
  it('applies different padding to visual and audio cues', () => {
    const result = applySourcePadding(cues, {
      visual: { start: 0.5, end: 0.25 },
      audio: { start: 0.2, end: 0.4 },
      audioMergeGap: 0.3,
    });

    expect(result[0]).toMatchObject({ start: 4.5, end: 7.25 });
    expect(result[1]).toMatchObject({ start: 9.8, end: 11.4 });
  });

  it('does not allow a padded start time below zero', () => {
    const result = applySourcePadding(
      [{ ...cues[0], start: 0.1 }],
      {
        visual: { start: 0.5, end: 0 },
        audio: { start: 0, end: 0 },
        audioMergeGap: 0.3,
      },
    );

    expect(result[0].start).toBe(0);
  });
});

describe('mergeNearbyAudioCues', () => {
  it('merges matching audio cues separated by less than 300 ms', () => {
    const input: Cue[] = [
      { id: 'a', start: 1, end: 2, source: 'audio', action: 'mute' },
      { id: 'b', start: 2.2, end: 3, source: 'audio', action: 'mute' },
    ];

    const result = mergeNearbyAudioCues(input);

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: 'a,b',
      start: 1,
      end: 3,
      source: 'audio',
      action: 'mute',
    });
  });

  it('does not merge audio cues with different actions', () => {
    const input: Cue[] = [
      { id: 'a', start: 1, end: 2, source: 'audio', action: 'mute' },
      { id: 'b', start: 2.1, end: 3, source: 'audio', action: 'reduce' },
    ];

    expect(mergeNearbyAudioCues(input)).toHaveLength(2);
  });

  it('never merges visual cues into audio cues', () => {
    const input: Cue[] = [
      { id: 'a', start: 1, end: 2, source: 'audio', action: 'mute' },
      { id: 'v', start: 2.1, end: 3, source: 'visual', action: 'hide' },
      { id: 'b', start: 2.2, end: 3, source: 'audio', action: 'mute' },
    ];

    const result = mergeNearbyAudioCues(input);

    expect(result.some((cue) => cue.source === 'visual')).toBe(true);
    expect(result.filter((cue) => cue.source === 'audio')).toHaveLength(1);
  });
});