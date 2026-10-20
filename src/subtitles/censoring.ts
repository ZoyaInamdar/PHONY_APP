import type { SubtitleCue, TimedCue } from './types';

/**
 * Escapes regex special characters in a string.
 */
function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Pure censoring function that finds whole-word matches and replaces
 * inner characters with asterisks (e.g., "f***") while preserving outer characters
 * and adjacent punctuation.
 *
 * Rules:
 * - Length <= 2: replaces second char with '*' (e.g. "sh" -> "s*")
 * - Length > 2: keeps first and last char, replaces middle with '*' (e.g. "damn" -> "d**n")
 */
export function censorText(text: string, flaggedWords: string[]): string {
  if (typeof text !== 'string' || !text) return '';
  if (!flaggedWords || flaggedWords.length === 0) return text;

  let censored = text;
  flaggedWords.forEach((word) => {
    if (!word || word.trim().length === 0) return;
    // Match the exact word with word boundaries to avoid partial matches
    const escaped = escapeRegex(word.trim());
    const regex = new RegExp(`\\b${escaped}\\b`, 'gi');
    censored = censored.replace(regex, (match) => {
      if (match.length <= 1) return '*';
      if (match.length === 2) return match[0] + '*';
      return match[0] + '*'.repeat(match.length - 2) + match[match.length - 1];
    });
  });
  return censored;
}

/**
 * Handles cue text processing taking into account cue kind (speech vs sound_description)
 * and user preferences.
 *
 * Rules:
 * - If kind is 'sound_description' and replaceSoundDescriptions is true -> '[sound muted]'
 * - If kind is 'speech' -> censors flagged words
 * - Otherwise -> returns text unchanged
 */
export function processCueText(
  text: string,
  kind: 'speech' | 'sound_description',
  flaggedWords: string[],
  replaceSoundDescriptions: boolean,
): string {
  if (kind === 'sound_description' && replaceSoundDescriptions) {
    return '[sound muted]';
  }
  if (kind === 'speech') {
    return censorText(text, flaggedWords);
  }
  return text;
}

/**
 * Extracts flagged word strings from a subtitle cue.
 *
 * Contract:
 * Regardless of whether `spoken_confirmed` is true, false, or null,
 * the visual text must be masked if the word was flagged.
 */
export function extractFlaggedWords(cue: SubtitleCue | TimedCue): string[] {
  const words: string[] = [];

  // 1. From flagged_words array if present
  if ('flagged_words' in cue && Array.isArray(cue.flagged_words)) {
    for (const item of cue.flagged_words) {
      if (typeof item === 'string') {
        words.push(item);
      } else if (item && typeof item.word === 'string') {
        words.push(item.word);
      }
    }
  }

  // 2. From flagged array (FlaggedTerm) if present
  if (cue.flagged && Array.isArray(cue.flagged)) {
    for (const term of cue.flagged) {
      if ('word' in term && typeof (term as any).word === 'string') {
        words.push((term as any).word);
      } else if (
        typeof term.char_start === 'number' &&
        typeof term.char_end === 'number' &&
        typeof cue.text === 'string' &&
        cue.text
      ) {
        words.push(cue.text.substring(term.char_start, term.char_end));
      }
    }
  }

  return Array.from(new Set(words));
}