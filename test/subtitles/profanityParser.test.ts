import {
  parseSubtitlesAndDetectProfanity,
  parseSrtTimestamp,
  classifyCueKind,
} from '../../src/subtitles/profanityParser';
import { ProfanityParserOptions } from '../../src/subtitles/types';

describe('Person C - Subtitle and Profanity Parser', () => {
  const sampleWordList = ['damn', 'hell', 'crap'];
  const defaultOptions: ProfanityParserOptions = {
    wordList: sampleWordList,
    source: 'subtitles',
    defaultConfidence: 1.0,
  };

  // Requirement 1: Lowercase and uppercase profanity matching
  it('1. matches profanity in both lowercase and uppercase (case-insensitive)', () => {
    const srt = `1
00:00:01,000 --> 00:00:03,000
Damn it, this is HELL!
`;
    const result = parseSubtitlesAndDetectProfanity(srt, defaultOptions);

    expect(result.cues.length).toBe(1);
    expect(result.cues[0].flagged).toBeDefined();
    expect(result.cues[0].flagged?.length).toBe(2);

    const flaggedWords = result.cues[0].flagged!.map((f) =>
      result.cues[0].text.substring(f.char_start, f.char_end).toLowerCase(),
    );
    expect(flaggedWords).toEqual(['damn', 'hell']);
  });

  // Requirement 2: Punctuation around flagged words
  it('2. correctly identifies flagged words surrounded by punctuation', () => {
    const srt = `1
00:00:01,000 --> 00:00:03,000
What the (hell)... That is "damn" cool!
`;
    const result = parseSubtitlesAndDetectProfanity(srt, defaultOptions);

    expect(result.cues[0].flagged?.length).toBe(2);
    const matchedTexts = result.cues[0].flagged!.map((f) =>
      result.cues[0].text.substring(f.char_start, f.char_end),
    );
    expect(matchedTexts).toEqual(['hell', 'damn']);
  });

  // Requirement 3: Whole-word matching rather than matching inside unrelated words
  it('3. enforces whole-word matching and avoids substring false positives', () => {
    const srt = `1
00:00:01,000 --> 00:00:03,000
Hello everyone, look at the shell and the scraper.
`;
    // 'hell' inside 'hello' or 'shell', 'crap' inside 'scraper'
    const result = parseSubtitlesAndDetectProfanity(srt, defaultOptions);

    expect(result.cues.length).toBe(1);
    expect(result.cues[0].flagged).toBeUndefined();
    expect(result.detections.length).toBe(0);
  });

  // Requirement 4: Multiple flagged words in one subtitle cue
  it('4. detects multiple flagged words in a single subtitle cue in chronological character order', () => {
    const srt = `1
00:00:02,500 --> 00:00:05,000
Damn, this crap is pure hell.
`;
    const result = parseSubtitlesAndDetectProfanity(srt, defaultOptions);

    expect(result.cues[0].flagged?.length).toBe(3);
    const words = result.cues[0].flagged!.map((f) =>
      result.cues[0].text.substring(f.char_start, f.char_end),
    );
    expect(words).toEqual(['Damn', 'crap', 'hell']);
  });

  // Requirement 5: Preservation of original subtitle text and timestamps
  it('5. preserves exact original subtitle text and timestamps', () => {
    const originalText = 'We need to hurry, damn it!';
    const srt = `12
00:01:23,456 --> 00:01:27,890
${originalText}
`;
    const result = parseSubtitlesAndDetectProfanity(srt, defaultOptions);

    expect(result.cues[0].id).toBe('c1');
    expect(result.cues[0].start).toBeCloseTo(83.456, 3);
    expect(result.cues[0].end).toBeCloseTo(87.89, 3);
    expect(result.cues[0].text).toBe(originalText);
  });

  // Requirement 6: Empty subtitle files
  it('6. handles empty subtitle files cleanly without errors', () => {
    const emptyResult = parseSubtitlesAndDetectProfanity('', defaultOptions);
    expect(emptyResult.cues).toEqual([]);
    expect(emptyResult.detections).toEqual([]);

    const whitespaceResult = parseSubtitlesAndDetectProfanity('   \n\n  ', defaultOptions);
    expect(whitespaceResult.cues).toEqual([]);
    expect(whitespaceResult.detections).toEqual([]);
  });

  // Requirement 7: Invalid or unreadable subtitle files
  it('7. throws descriptive errors on invalid or malformed subtitle content', () => {
    expect(() =>
      parseSubtitlesAndDetectProfanity(null as any, defaultOptions),
    ).toThrow('Subtitle content must be a string.');

    const malformedTimestampSrt = `1
not-a-valid-timestamp
Some subtitle text
`;
    expect(() =>
      parseSubtitlesAndDetectProfanity(malformedTimestampSrt, defaultOptions),
    ).toThrow(/Invalid SRT timestamp format/);

    const reversedIntervalSrt = `1
00:00:10,000 --> 00:00:05,000
Backward in time
`;
    expect(() =>
      parseSubtitlesAndDetectProfanity(reversedIntervalSrt, defaultOptions),
    ).toThrow(/end .* must be strictly greater than start/);
  });

  // Requirement 8: Correct output structure for detections/subtitles.json
  it('8. outputs raw detection records adhering to shared detection schema contract', () => {
    const srt = `1
00:00:10,500 --> 00:00:13,200
Oh damn, watch out!
`;
    const result = parseSubtitlesAndDetectProfanity(srt, defaultOptions);

    expect(result.detections.length).toBe(1);
    const detection = result.detections[0];

    // Shared detection contract: start, end, trigger, score, source
    expect(detection).toHaveProperty('start', 10.5);
    expect(detection).toHaveProperty('end', 13.2);
    expect(detection).toHaveProperty('trigger', 'profanity');
    expect(detection).toHaveProperty('score', 1.0);
    expect(detection).toHaveProperty('source', 'subtitles');
    expect(typeof detection.start).toBe('number');
    expect(typeof detection.end).toBe('number');
    expect(detection.end).toBeGreaterThan(detection.start);
  });

  // Requirement 9: Correct cue structure for cues.json
  it('9. produces cues.json structure with required evidence, timing, and spoken_confirmed fields', () => {
    const srt = `1
00:00:05,000 --> 00:00:08,000
Get down, damn it!
`;
    const result = parseSubtitlesAndDetectProfanity(srt, defaultOptions);
    const cue = result.cues[0];

    expect(cue).toHaveProperty('id', 'c1');
    expect(cue).toHaveProperty('start', 5.0);
    expect(cue).toHaveProperty('end', 8.0);
    expect(cue).toHaveProperty('kind', 'speech');
    expect(cue).toHaveProperty('text', 'Get down, damn it!');
    expect(cue.flagged).toBeDefined();

    const flagged = cue.flagged![0];
    expect(flagged).toHaveProperty('term_id');
    expect(flagged).toHaveProperty('char_start');
    expect(flagged).toHaveProperty('char_end');
    expect(flagged).toHaveProperty('evidence', 'subtitle_text');
    expect(flagged).toHaveProperty('timing', 'estimated');
    expect(flagged).toHaveProperty('start');
    expect(flagged).toHaveProperty('end');
    expect(flagged).toHaveProperty('confidence', 1.0);
    expect(flagged).toHaveProperty('spoken_confirmed', null);
  });

  // Requirement 10: Ordinary dialogue mentioning trigger keywords is not censored as profanity
  it('10. does not censor ordinary dialogue mentioning trigger keywords (e.g. spider, gunshot) unless in profanity wordlist', () => {
    const srt = `1
00:00:01,000 --> 00:00:04,000
I heard a gunshot and saw a huge spider, but there was no profanity.
`;
    const result = parseSubtitlesAndDetectProfanity(srt, defaultOptions);

    expect(result.cues.length).toBe(1);
    expect(result.cues[0].flagged).toBeUndefined();
    expect(result.detections.length).toBe(0);
  });

  // Bonus: Sound descriptions like [gunshots] are classified as sound_description and preserved
  it('preserves sound descriptions like [gunshots] as sound_description without profanity muting', () => {
    const srt = `1
00:00:01,000 --> 00:00:03,000
[gunshots and screams]
`;
    const result = parseSubtitlesAndDetectProfanity(srt, defaultOptions);

    expect(result.cues.length).toBe(1);
    expect(result.cues[0].kind).toBe('sound_description');
    expect(result.cues[0].flagged).toBeUndefined();
    expect(result.detections.length).toBe(0);
  });
});
