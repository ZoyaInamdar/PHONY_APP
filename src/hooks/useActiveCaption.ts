import { useMemo } from 'react';
import {
  getActiveCaption,
  ActiveCaptionResult,
  CaptionUserSettings,
} from '../subtitles/activeCaption';

export type { ActiveCaptionResult, CaptionUserSettings };
export { getActiveCaption };

/**
 * Hook to retrieve the currently active subtitle caption based on playback time,
 * querying Person A's action-resolution engine and applying censoring rules
 * according to user preferences.
 */
export function useActiveCaption(
  currentTime: number,
  userSettings?: CaptionUserSettings | any,
): ActiveCaptionResult | null {
  return useMemo(
    () => getActiveCaption(currentTime, userSettings),
    [currentTime, userSettings],
  );
}
