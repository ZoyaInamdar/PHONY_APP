export type CueKind = 'speech' | 'sound_description';

export type EvidenceType = 'subtitle_text' | 'stt_word' | 'aligned_word';

export type TimingType = 'exact' | 'aligned' | 'estimated';

export interface FlaggedTerm {
  term_id: string;
  word?: string;
  char_start: number;
  char_end: number;
  evidence: EvidenceType;
  timing: TimingType;
  start: number;
  end: number;
  confidence: number;
  /**
   * Spoken audio confirmation status:
   * - true: spoken audio confirmed -> censor caption on screen & mute audio.
   * - null: spoken audio unconfirmed / estimated -> censor caption & mute audio.
   * - false: spoken audio unconfirmed / absent -> censor caption ONLY (audio NOT muted).
   *
   * Note: The UI renderer masks visual text regardless of this boolean as long as the word is flagged.
   */
  spoken_confirmed: boolean | null;
}

export interface FlaggedWordSummary {
  word: string;
  char_start?: number;
  char_end?: number;
  evidence?: EvidenceType;
  timing?: TimingType;
  /**
   * If true or null, word is censored and audio is muted.
   * If false, only caption is censored.
   */
  spoken_confirmed?: boolean | null;
}

export interface TimedCue {
  id: string;
  start: number;
  end: number;
  kind: CueKind;
  text: string;
  flagged?: FlaggedTerm[];
  flagged_words?: (string | FlaggedWordSummary)[];
}

export interface SubtitleCue extends TimedCue {
  flagged_words?: (string | FlaggedWordSummary)[];
}

export interface RawDetection {
  start: number;
  end: number;
  trigger: string;
  score: number;
  source: string;
}

export interface SubtitlesParseResult {
  cues: TimedCue[];
  detections: RawDetection[];
}

export interface ProfanityParserOptions {
  wordList: string[];
  source?: string;
  defaultConfidence?: number;
  timingPaddingSeconds?: number;
}
