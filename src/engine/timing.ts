
import { Cue } from './types';

export interface CuePadding {
  start: number;
  end: number;
}

export interface TimingConfig {
  visual: CuePadding;
  audio: CuePadding;
  audioMergeGap: number;
}

export const DEFAULT_TIMING_CONFIG: TimingConfig = {
  visual: { start: 0, end: 0 },
  audio: { start: 0, end: 0 },
  audioMergeGap: 0.3,
};

export function applySourcePadding(
  cues: Cue[],
  config: TimingConfig = DEFAULT_TIMING_CONFIG,
): Cue[] {
  return cues.map((cue) => {
    // `text` and `audio_visual` sources share audio padding in the absence of
    // a dedicated timing config entry.  If a future config version adds
    // explicit entries for them, those will take precedence.
    const paddingKey: 'visual' | 'audio' =
      cue.source === 'visual' ? 'visual' : 'audio';

    const padding = config[paddingKey];

    if (
      !Number.isFinite(padding.start) ||
      !Number.isFinite(padding.end) ||
      padding.start < 0 ||
      padding.end < 0
    ) {
      throw new Error(`Invalid timing padding for ${cue.source} cues.`);
    }

    return {
      ...cue,
      start: Math.max(0, cue.start - padding.start),
      end: cue.end + padding.end,
    };
  });
}

export function mergeNearbyAudioCues(
  cues: Cue[],
  maxGap = DEFAULT_TIMING_CONFIG.audioMergeGap,
): Cue[] {
  if (!Number.isFinite(maxGap) || maxGap < 0) {
    throw new Error('Audio merge gap must be a finite, non-negative number.');
  }

  const sorted = [...cues].sort(
    (a, b) => a.start - b.start || a.end - b.end,
  );

  const result: Cue[] = [];
  const lastAudioIndex = new Map<string, number>();

  for (const cue of sorted) {
    if (cue.source !== 'audio') {
      result.push({ ...cue });
      continue;
    }

    // Only compare audio cues with the previous compatible audio cue.
    const key = [
      cue.action,
      cue.status ?? '',
      cue.gain ?? '',
    ].join('|');

    const previousIndex = lastAudioIndex.get(key);
    const previous =
      previousIndex === undefined ? undefined : result[previousIndex];

    if (
      previous &&
      cue.start - previous.end < maxGap
    ) {
      result[previousIndex!] = {
        ...previous,
        end: Math.max(previous.end, cue.end),
        id: `${previous.id},${cue.id}`,
      };
    } else {
      result.push({ ...cue });
      lastAudioIndex.set(key, result.length - 1);
    }
  }

  return result.sort(
    (a, b) => a.start - b.start || a.end - b.end,
  );
}
