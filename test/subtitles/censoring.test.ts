import {
  censorText,
  processCueText,
  extractFlaggedWords,
} from '../../src/subtitles/censoring';
import { SubtitleCue } from '../../src/subtitles/types';

describe('Person C - Pure Censoring Function and Cue Processing', () => {
  describe('censorText', () => {
    it('returns the original text when no flagged words are provided or array is empty', () => {
      expect(censorText('Hello world', [])).toBe('Hello world');
      expect(censorText('', ['damn'])).toBe('');
      // @ts-expect-error test null/undefined safety
      expect(censorText('Clean text', null)).toBe('Clean text');
    });

    it('censors a 4-letter word by preserving first and last letter with asterisks', () => {
      expect(censorText('What the hell is going on?', ['hell'])).toBe(
        'What the h**l is going on?',
      );
      expect(censorText('Damn right, we are ready.', ['damn'])).toBe(
        'D**n right, we are ready.',
      );
    });

    it('handles words of length 1, 2, and 3 correctly', () => {
      expect(censorText('I said a test', ['a'])).toBe('I said * test');
      expect(censorText('Be quiet, sh!', ['sh'])).toBe('Be quiet, s*!');
      expect(censorText('You are an ass.', ['ass'])).toBe('You are an a*s.');
    });

    it('handles longer words correctly (keeping first and last char)', () => {
      expect(censorText('That is bullshit.', ['bullshit'])).toBe(
        'That is b******t.',
      );
    });

    it('handles case-insensitivity and preserves casing of first and last letter', () => {
      expect(censorText('HELL is here', ['hell'])).toBe('H**L is here');
      expect(censorText('Hell is here', ['hell'])).toBe('H**l is here');
      expect(censorText('hell is here', ['HELL'])).toBe('h**l is here');
    });

    it('preserves adjacent punctuation and brackets', () => {
      expect(censorText('What the (hell)?!', ['hell'])).toBe(
        'What the (h**l)?!',
      );
      expect(censorText('"damn", he said.', ['damn'])).toBe('"d**n", he said.');
      expect(censorText("Don't give me that crap!", ['crap'])).toBe(
        "Don't give me that c**p!",
      );
    });

    it('strictly enforces whole-word boundary matching to avoid partial substring false positives', () => {
      // 'hell' inside 'hello' or 'shell', 'crap' inside 'scrappy'
      expect(censorText('hello shell scrappy', ['hell', 'crap'])).toBe(
        'hello shell scrappy',
      );
    });

    it('censors multiple distinct flagged words in the same text', () => {
      expect(censorText('What the hell is this crap?', ['hell', 'crap'])).toBe(
        'What the h**l is this c**p?',
      );
    });
  });

  describe('processCueText', () => {
    it('keeps sound_description cues intact when replaceSoundDescriptions is false', () => {
      const result = processCueText('[gunshots]', 'sound_description', ['gun'], false);
      expect(result).toBe('[gunshots]');
    });

    it('replaces sound_description with [sound muted] when replaceSoundDescriptions is true', () => {
      const result = processCueText('[gunshots]', 'sound_description', [], true);
      expect(result).toBe('[sound muted]');
    });

    it('applies censorText on speech cues', () => {
      const result = processCueText(
        'What the hell?',
        'speech',
        ['hell'],
        false,
      );
      expect(result).toBe('What the h**l?');
    });

    it('leaves clean speech cues unchanged even if replaceSoundDescriptions is true', () => {
      const result = processCueText('Good morning!', 'speech', [], true);
      expect(result).toBe('Good morning!');
    });
  });

  describe('extractFlaggedWords & Caption-Only Rule (spoken_confirmed)', () => {
    it('extracts words regardless of whether spoken_confirmed is true, false, or null', () => {
      const cue: SubtitleCue = {
        id: 'c1',
        start: 1.0,
        end: 3.5,
        kind: 'speech',
        text: 'What the hell is this crap and damn it?',
        flagged_words: [
          {
            word: 'hell',
            spoken_confirmed: true, // confirmed audio
          },
          {
            word: 'crap',
            spoken_confirmed: false, // caption-only: unheard audio, but caption MUST be censored
          },
          {
            word: 'damn',
            spoken_confirmed: null, // unconfirmed / estimated
          },
        ],
      };

      const extracted = extractFlaggedWords(cue);
      expect(extracted).toEqual(['hell', 'crap', 'damn']);

      const processed = processCueText(cue.text, cue.kind, extracted, false);
      expect(processed).toBe('What the h**l is this c**p and d**n it?');
    });

    it('extracts words from FlaggedTerm char offsets if flagged_words is missing', () => {
      const cue: SubtitleCue = {
        id: 'c2',
        start: 1.0,
        end: 3.0,
        kind: 'speech',
        text: 'Holy crap!',
        flagged: [
          {
            term_id: 'w1',
            char_start: 5,
            char_end: 9,
            evidence: 'subtitle_text',
            timing: 'estimated',
            start: 1.0,
            end: 3.0,
            confidence: 1.0,
            spoken_confirmed: false,
          },
        ],
      };

      const extracted = extractFlaggedWords(cue);
      expect(extracted).toEqual(['crap']);
      expect(processCueText(cue.text, cue.kind, extracted, false)).toBe(
        'Holy c**p!',
      );
    });
  });
});
