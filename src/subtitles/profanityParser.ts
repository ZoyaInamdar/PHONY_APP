import {
  CueKind,
  FlaggedTerm,
  ProfanityParserOptions,
  RawDetection,
  SubtitlesParseResult,
  TimedCue,
} from './types';

/**
 * Parses timestamp string "HH:MM:SS,mmm" or "HH:MM:SS.mmm" into seconds (float).
 */
export function parseSrtTimestamp(timestampStr: string): number {
  const trimmed = timestampStr.trim().replace(',', '.');
  const match = /^(\d{2,}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?$/.exec(trimmed);
  if (!match) {
    throw new Error(`Invalid SRT timestamp: "${timestampStr}"`);
  }

  const hours = parseInt(match[1], 10);
  const minutes = parseInt(match[2], 10);
  const seconds = parseInt(match[3], 10);
  const millisRaw = match[4] || '0';
  const millis = parseInt(millisRaw.padEnd(3, '0').slice(0, 3), 10);

  return hours * 3600 + minutes * 60 + seconds + millis / 1000;
}

/**
 * Classifies subtitle text as speech or sound_description.
 * Sound descriptions are typically fully enclosed in brackets, e.g. [gunshots], (dramatic music).
 */
export function classifyCueKind(text: string): CueKind {
  const trimmed = text.trim();
  if (
    (trimmed.startsWith('[') && trimmed.endsWith(']')) ||
    (trimmed.startsWith('(') && trimmed.endsWith(')'))
  ) {
    return 'sound_description';
  }
  return 'speech';
}

function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Parses SRT subtitle content and detects profanity terms against a provided word list.
 */
export function parseSubtitlesAndDetectProfanity(
  srtContent: string,
  options: ProfanityParserOptions,
): SubtitlesParseResult {
  if (typeof srtContent !== 'string') {
    throw new Error('Subtitle content must be a string.');
  }

  const trimmed = srtContent.trim();
  if (!trimmed) {
    return { cues: [], detections: [] };
  }

  // Normalize line endings
  const normalized = trimmed.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const blocks = normalized.split(/\n\n+/);

  const cues: TimedCue[] = [];
  const detections: RawDetection[] = [];

  // Prepare case-insensitive whole-word regexes for each profanity word
  const termMatchers = (options.wordList || [])
    .filter((w) => w && w.trim().length > 0)
    .map((word, idx) => ({
      termId: `w${idx + 1}`,
      term: word.trim(),
      regex: new RegExp(`\\b${escapeRegex(word.trim())}\\b`, 'gi'),
    }));

  let cueCounter = 1;

  for (const block of blocks) {
    const lines = block.split('\n').map((l) => l.trim()).filter((l) => l.length > 0);
    if (lines.length < 2) {
      throw new Error(`Invalid subtitle block at block ${cueCounter}: insufficient lines.`);
    }

    let timestampLineIndex = 0;
    // Optional integer cue index on line 0
    if (/^\d+$/.test(lines[0]) && lines.length >= 3) {
      timestampLineIndex = 1;
    }

    const timestampLine = lines[timestampLineIndex];
    const arrowParts = timestampLine.split('-->');
    if (arrowParts.length !== 2) {
      throw new Error(
        `Invalid SRT timestamp format in cue ${cueCounter}: "${timestampLine}"`,
      );
    }

    const start = parseSrtTimestamp(arrowParts[0]);
    const end = parseSrtTimestamp(arrowParts[1]);

    if (end <= start) {
      throw new Error(
        `Invalid cue interval: end (${end}) must be strictly greater than start (${start}) in cue ${cueCounter}.`,
      );
    }

    const textLines = lines.slice(timestampLineIndex + 1);
    const rawText = textLines.join(' ');
    const kind = classifyCueKind(rawText);

    const flagged: FlaggedTerm[] = [];

    // Only search profanity in speech cues
    if (kind === 'speech' && termMatchers.length > 0) {
      for (const matcher of termMatchers) {
        matcher.regex.lastIndex = 0;
        let match: RegExpExecArray | null;

        while ((match = matcher.regex.exec(rawText)) !== null) {
          const charStart = match.index;
          const charEnd = match.index + match[0].length;

          // Linear interpolation for estimated word timing within cue
          const cueDuration = end - start;
          const textLength = Math.max(rawText.length, 1);
          const wordStart = Number(
            (start + (charStart / textLength) * cueDuration).toFixed(3),
          );
          const wordEnd = Number(
            (start + (charEnd / textLength) * cueDuration).toFixed(3),
          );

          flagged.push({
            term_id: matcher.termId,
            char_start: charStart,
            char_end: charEnd,
            evidence: 'subtitle_text',
            timing: 'estimated',
            start: wordStart,
            end: wordEnd,
            confidence: options.defaultConfidence ?? 1.0,
            spoken_confirmed: null,
          });
        }
      }
    }

    // Sort flagged terms by position in cue text
    flagged.sort((a, b) => a.char_start - b.char_start);

    const cue: TimedCue = {
      id: `c${cueCounter}`,
      start,
      end,
      kind,
      text: rawText,
    };

    if (flagged.length > 0) {
      cue.flagged = flagged;

      // With estimated timing from subtitle text only, the whole cue interval is flagged for muting
      detections.push({
        start,
        end,
        trigger: 'profanity',
        score: 1.0,
        source: options.source ?? 'subtitles',
      });
    }

    cues.push(cue);
    cueCounter++;
  }

  return { cues, detections };
}
