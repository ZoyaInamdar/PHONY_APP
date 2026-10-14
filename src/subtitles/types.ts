export type CueKind = 'speech' | 'sound_description';

export type EvidenceType = 'subtitle_text' | 'stt_word' | 'aligned_word';

export type TimingType = 'exact' | 'aligned' | 'estimated';

export interface FlaggedTerm {
  term_id: string;
  char_start: number;
  char_end: number;
  evidence: EvidenceType;
  timing: TimingType;
  start: number;
  end: number;
  confidence: number;
  spoken_confirmed: boolean | null;
}

export interface TimedCue {
  id: string;
  start: number;
  end: number;
  kind: CueKind;
  text: string;
  flagged?: FlaggedTerm[];
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
