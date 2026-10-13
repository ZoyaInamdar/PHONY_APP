export type CueSource = 'visual' | 'audio';

export type CueAction = 'hide' | 'mute' | 'reduce' | 'skip' | 'warn';

export type CueStatus = 'ready' | 'needs_review';

export interface Cue {
  id: string;
  start: number;
  end: number;
  source: CueSource;
  action: CueAction;
  status?: CueStatus;
  gain?: number;
}

export interface ViewerOptions {
  hideEnabled: boolean;
  muteEnabled: boolean;
  reduceEnabled: boolean;
  skipEnabled: boolean;
  reduceGain: number;
  confirmReviewSkip: boolean;
}

export interface EffectiveActions {
  time: number;
  hide: boolean;
  soundGain: number;
  skipTo: number | null;
  activeCueIds: string[];
}

export const DEFAULT_VIEWER_OPTIONS: ViewerOptions = {
  hideEnabled: true,
  muteEnabled: true,
  reduceEnabled: false,
  skipEnabled: false,
  reduceGain: 0.3,
  confirmReviewSkip: false,
};
