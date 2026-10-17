import { getActiveCaption } from '../../src/subtitles/activeCaption';
import { SubtitleCue } from '../../src/subtitles/types';

describe('activeCaption / getActiveCaption', () => {
  const testCues: SubtitleCue[] = [
    {
      id: 'cue-1',
      start: 2.0,
      end: 5.0,
      kind: 'speech',
      text: 'What the hell is that?',
      flagged_words: [{ word: 'hell', spoken_confirmed: true }],
    },
    {
      id: 'cue-2',
      start: 6.0,
      end: 8.0,
      kind: 'sound_description',
      text: '[explosion sounds]',
    },
    {
      id: 'cue-3',
      start: 10.0,
      end: 13.0,
      kind: 'speech',
      text: 'Damn right.',
      flagged_words: [
        {
          word: 'Damn',
          spoken_confirmed: false, // caption-only: unheard audio, but caption is censored
        },
      ],
    },
  ];

  it('returns null when playback time is out of range of any cue', () => {
    expect(getActiveCaption(1.0, { cues: testCues })).toBeNull();
    expect(getActiveCaption(5.5, { cues: testCues })).toBeNull();
    expect(getActiveCaption(-1.0, { cues: testCues })).toBeNull();
    expect(getActiveCaption(NaN, { cues: testCues })).toBeNull();
  });

  it('returns active cue with censored text when time is within range and action is demanded', () => {
    const result = getActiveCaption(3.0, { cues: testCues });
    expect(result).not.toBeNull();
    expect(result?.id).toBe('cue-1');
    expect(result?.rawText).toBe('What the hell is that?');
    expect(result?.processedText).toBe('What the h**l is that?');
    expect(result?.flaggedWords).toEqual(['hell']);
    expect(result?.isActionDemanded).toBe(true);
  });

  it('masks caption regardless of spoken_confirmed being false (caption-only rule)', () => {
    const result = getActiveCaption(11.0, { cues: testCues });
    expect(result).not.toBeNull();
    expect(result?.id).toBe('cue-3');
    expect(result?.processedText).toBe('D**n right.');
  });

  it('honors replaceSoundDescriptions setting for sound descriptions', () => {
    const resultNoReplace = getActiveCaption(7.0, {
      cues: testCues,
      replaceSoundDescriptions: false,
    });
    expect(resultNoReplace?.processedText).toBe('[explosion sounds]');

    const resultReplace = getActiveCaption(7.0, {
      cues: testCues,
      replaceSoundDescriptions: true,
    });
    expect(resultReplace?.processedText).toBe('[sound muted]');
  });

  it('skips censoring when profanity is in disabledCategories', () => {
    const result = getActiveCaption(3.0, {
      cues: testCues,
      disabledCategories: ['profanity'],
    });
    expect(result?.processedText).toBe('What the hell is that?');
    expect(result?.isActionDemanded).toBe(false);
  });

  it('skips censoring when censorProfanity is set to false in userSettings', () => {
    const result = getActiveCaption(3.0, {
      cues: testCues,
      censorProfanity: false,
    });
    expect(result?.processedText).toBe('What the hell is that?');
    expect(result?.isActionDemanded).toBe(false);
  });
});
