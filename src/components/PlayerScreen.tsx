import React, {useEffect, useRef, useState} from 'react';
import {
  Dimensions,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import {BackHandler, TVFocusGuideView} from '@amazon-devices/react-native-kepler';
import {Video} from '@amazon-devices/react-native-w3cmedia';
import {Cue, EffectiveActions, ViewerOptions} from '../engine/types';
import {PlaybackState} from '../engine/playbackState';
import {
  defaultPlaybackOptions,
  sintelIndexCues,
  skipVerificationTestCues,
  verificationTestCues,
} from '../data/playbackCues';
import {
  appendBotheredLog,
  getItem,
  setItem,
  STORAGE_KEYS,
} from '../storage';
import {RecapView} from './RecapView';

// ---------------------------------------------------------------------------
// Trigger-Aware Player for Fire TV Vega OS
//
// 1. HTTPS Sintel trailer stream (H.264/AAC, 52.2s, verified on-device).
// 2. Dynamic trigger action engine (PlaybackState & resolveActions):
//    - Audio Mute: soundGain === 0 mutes native audio; restores on cue end.
//    - Audio Reduce: soundGain in (0, 1) lowers volume; restores to base volume.
//    - Visual Hide: hides video surface & displays fallback visual protection overlay.
//    - Video Skip: seeks playback forward past active trigger interval to actions.skipTo.
//    - Precedence: Mute overrides Reduce; multiple cues resolved deterministically.
//    - Seeking: immediately updates trigger actions without pipeline glitches.
//    - Manual Mute: user manual mute preserved across cue boundaries.
// ---------------------------------------------------------------------------

const DEFAULT_VIDEO_URL =
  'https://media.w3.org/2010/05/sintel/trailer.mp4';

export const DEFAULT_RAMP_DURATION_MS = 150;
export const CONTAINER_PADDING_HORIZONTAL = 48;
export const VIDEO_FRAME_HEIGHT = 400;

export type PlayerProfile = 'verification' | 'standard' | 'skip_verification';

export interface PlayerScreenProps {
  onBack: () => void;
  cues?: Cue[];
  options?: Partial<ViewerOptions>;
  initialProfile?: PlayerProfile;
  testVideoUrl?: string;
  rampDurationMs?: number;
}

export const CANONICAL_TRIGGER_CATEGORIES = [
  'flashing',
  'loud_spike',
  'gunshot',
  'explosion',
  'scream',
  'profanity',
  'spider',
  'fast_motion',
  'jump_scare',
] as const;

export type CanonicalTriggerCategory =
  (typeof CANONICAL_TRIGGER_CATEGORIES)[number];

const CANONICAL_TRIGGER_SET = new Set<string>(CANONICAL_TRIGGER_CATEGORIES);

/**
 * Sanitizes and validates arbitrary input into a safe ViewerOptions object.
 * Validates individual fields strictly by type and range, falling back to
 * default values for any missing or malformed properties.
 */
export function sanitizeViewerOptions(
  raw: unknown,
  fallback: ViewerOptions,
): ViewerOptions {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return fallback;
  }

  const obj = raw as Record<string, unknown>;
  const result: ViewerOptions = { ...fallback };

  if (typeof obj.hideEnabled === 'boolean') {
    result.hideEnabled = obj.hideEnabled;
  }
  if (typeof obj.muteEnabled === 'boolean') {
    result.muteEnabled = obj.muteEnabled;
  }
  if (typeof obj.reduceEnabled === 'boolean') {
    result.reduceEnabled = obj.reduceEnabled;
  }
  if (typeof obj.skipEnabled === 'boolean') {
    result.skipEnabled = obj.skipEnabled;
  }
  if (
    typeof obj.reduceGain === 'number' &&
    Number.isFinite(obj.reduceGain) &&
    obj.reduceGain >= 0 &&
    obj.reduceGain <= 1
  ) {
    result.reduceGain = obj.reduceGain;
  }
  if (typeof obj.confirmReviewSkip === 'boolean') {
    result.confirmReviewSkip = obj.confirmReviewSkip;
  }
  if (Array.isArray(obj.disabledCategories)) {
    result.disabledCategories = obj.disabledCategories.filter(
      (item): item is string =>
        typeof item === 'string' && CANONICAL_TRIGGER_SET.has(item),
    );
  }

  return result;
}

export const PlayerScreen = ({
  onBack,
  cues: customCues,
  options: customOptions,
  initialProfile = 'verification',
  testVideoUrl,
  rampDurationMs,
}: PlayerScreenProps) => {
  const rampDuration =
    rampDurationMs !== undefined
      ? rampDurationMs
      : process.env.NODE_ENV === 'test'
      ? 0
      : DEFAULT_RAMP_DURATION_MS;

  const videoRef = useRef<Video>(null);
  const videoInstanceRef = useRef<Video | null>(null);
  const playbackStateRef = useRef<PlaybackState>(new PlaybackState());
  const lastLoggedSecond = useRef<number>(-1);

  // Volume ramp controller refs for on-device smooth transitions
  const volumeRampTimerRef = useRef<NodeJS.Timeout | null>(null);
  const rampTargetVolumeRef = useRef<number | null>(null);

  // User baseline settings (independent of automated trigger actions)
  const userVolumeRef = useRef<number>(1.0);
  const userMutedRef = useRef<boolean>(false);
  const isContentHiddenRef = useRef<boolean>(false);
  const effectiveActionsRef = useRef<EffectiveActions | null>(null);
  const lastActiveCuesRef = useRef<string>('');
  const lastWarnCueIdsRef = useRef<string>('');

  // Selected trigger cues & profile
  const [profile, setProfile] = useState<PlayerProfile>(initialProfile);

  const getProfileCues = (p: PlayerProfile): Cue[] => {
    switch (p) {
      case 'standard':
        return sintelIndexCues;
      case 'skip_verification':
        return skipVerificationTestCues;
      case 'verification':
      default:
        return verificationTestCues;
    }
  };

  const activeCues: Cue[] =
    customCues ?? getProfileCues(profile);

  // User protection preferences state
  const baseDefaults: ViewerOptions = {
    ...defaultPlaybackOptions,
    ...customOptions,
    disabledCategories: customOptions?.disabledCategories ?? [],
  };

  const [viewerOptions, setViewerOptions] = useState<ViewerOptions>(baseDefaults);
  const viewerOptionsRef = useRef<ViewerOptions>(viewerOptions);
  viewerOptionsRef.current = viewerOptions;

  // Storage synchronization and race-condition guard refs
  const isMountedRef = useRef<boolean>(true);
  const hasUserModifiedOptionsRef = useRef<boolean>(false);
  const latestSavePayloadRef = useRef<ViewerOptions | null>(null);
  const isSavingRef = useRef<boolean>(false);

  // Settings drawer state
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const isSettingsOpenRef = useRef(false);
  isSettingsOpenRef.current = isSettingsOpen;
  const [focusedDrawerItem, setFocusedDrawerItem] = useState<string>('toggle-visual');

  // Active trigger categories in current index
  const activeCategories = Array.from(
    new Set(
      activeCues
        .map((c) => c.trigger)
        .filter((t): t is string => typeof t === 'string' && t.length > 0),
    ),
  ).sort();

  // UI state
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [isContentHidden, setIsContentHidden] = useState(false);
  const [activeActionStatus, setActiveActionStatus] = useState<string>('');
  const [warningStatus, setWarningStatus] = useState<string>('');
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [status, setStatus] = useState('Loading video…');
  const [focusedControl, setFocusedControl] = useState<string>('play');
  const [botheredFeedback, setBotheredFeedback] = useState<string>('');
  const [showRecap, setShowRecap] = useState<boolean>(false);
  const botheredFeedbackTimerRef = useRef<NodeJS.Timeout | null>(null);

  const videoSource = testVideoUrl ?? DEFAULT_VIDEO_URL;

  // Derived video layout dimensions to satisfy W3CMedia Video numeric prop contract
  const initialWindowWidth = Dimensions.get('window').width;
  const initialVideoWidth =
    typeof initialWindowWidth === 'number' && initialWindowWidth > 0
      ? Math.max(0, Math.round(initialWindowWidth - CONTAINER_PADDING_HORIZONTAL * 2))
      : 1824;

  const [videoDimensions, setVideoDimensions] = useState<{width: number; height: number}>({
    width: initialVideoWidth,
    height: VIDEO_FRAME_HEIGHT,
  });

  // TV Remote back button handling (Vega OS / Fire TV hardware back)
  useEffect(() => {
    const handler = BackHandler;
    const onBackPress = () => {
      if (isSettingsOpenRef.current) {
        setIsSettingsOpen(false);
        setFocusedControl('settings');
        return true;
      }
      onBack();
      return true;
    };

    const backSubscription = handler?.addEventListener(
      'hardwareBackPress',
      onBackPress,
    );

    return () => {
      if (backSubscription && typeof backSubscription.remove === 'function') {
        backSubscription.remove();
      } else if (handler && typeof handler.removeEventListener === 'function') {
        handler.removeEventListener('hardwareBackPress', onBackPress);
      }
    };
  }, [onBack]);

  // Helper to cancel any in-flight volume ramp
  const cancelVolumeRamp = () => {
    if (volumeRampTimerRef.current !== null) {
      clearInterval(volumeRampTimerRef.current);
      volumeRampTimerRef.current = null;
      rampTargetVolumeRef.current = null;
    }
  };

  /**
   * Smoothly transitions video volume towards targetVol over durationMs.
   * Eliminates speaker clicks/pops during cue mute/reduce on Vega OS hardware.
   */
  const rampVolume = (
    targetVol: number,
    durationMs: number = rampDuration,
    onComplete?: () => void,
  ) => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    cancelVolumeRamp();

    const startVol = Number(video.volume.toFixed(2));
    const normalizedTarget = Number(targetVol.toFixed(2));
    const delta = normalizedTarget - startVol;

    // If already at target within epsilon or no duration requested, snap immediately
    if (Math.abs(delta) <= 0.01 || durationMs <= 0) {
      video.volume = normalizedTarget;
      onComplete?.();
      return;
    }

    rampTargetVolumeRef.current = normalizedTarget;
    const stepMs = 25;
    const totalSteps = Math.max(1, Math.round(durationMs / stepMs));
    let currentStep = 0;

    const intervalId = setInterval(() => {
      // Guard: do nothing if this ramp was cancelled or replaced
      if (volumeRampTimerRef.current !== intervalId) {
        clearInterval(intervalId);
        return;
      }

      const activeVideo = videoRef.current;
      if (!activeVideo) {
        cancelVolumeRamp();
        return;
      }

      currentStep++;
      const progress = Math.min(1, currentStep / totalSteps);
      const nextVol = Number((startVol + delta * progress).toFixed(2));
      activeVideo.volume = nextVol;

      if (progress >= 1 || currentStep >= totalSteps) {
        cancelVolumeRamp();
        activeVideo.volume = normalizedTarget;
        onComplete?.();
      }
    }, stepMs);

    volumeRampTimerRef.current = intervalId;
  };

  // Cleanup on unmount: cancel volume timers, reset engine, and restore player settings
  useEffect(() => {
    return () => {
      cancelVolumeRamp();
      if (botheredFeedbackTimerRef.current !== null) {
        clearTimeout(botheredFeedbackTimerRef.current);
        botheredFeedbackTimerRef.current = null;
      }
      const video = videoRef.current ?? videoInstanceRef.current;
      if (video) {
        video.muted = false;
        video.volume = 1.0;
      }
      playbackStateRef.current.reset();
      if (process.env.NODE_ENV !== 'test') {
        console.log('[Player] Unmounted — playback state reset, audio restored');
      }
    };
  }, []);

  /**
   * Authoritative trigger action evaluator:
   * Resolves active cues at the given timestamp and applies mute, volume reduction,
   * and visual hiding without unnecessary property churn.
   */
  const applyTriggerActions = (
    time: number,
    isSeek = false,
    overrideOptions?: ViewerOptions,
    overrideCues?: Cue[],
  ) => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(time) || time < 0) {
      return;
    }

    const currentOptions = overrideOptions ?? viewerOptionsRef.current;
    const currentCues = overrideCues ?? activeCues;
    const actions = playbackStateRef.current.resolveAt(time, currentCues, currentOptions);
    effectiveActionsRef.current = actions;

    // 0. Skip forward evaluation
    if (!isSeek && actions.skipTo !== null && Number.isFinite(actions.skipTo)) {
      const maxDur = Number.isFinite(video.duration) ? video.duration : Infinity;
      const targetTime = Math.max(0, Math.min(actions.skipTo, maxDur));

      if (targetTime > time) {
        console.log(
          `[Trigger] Skip forward: pos=${time.toFixed(2)} → ${targetTime.toFixed(2)} ` +
            `(activeCues: [${actions.activeCueIds.join(',')}])`,
        );

        cancelVolumeRamp();
        playbackStateRef.current.seek(targetTime, currentCues);
        video.currentTime = targetTime;
        setCurrentTime(targetTime);
        applyTriggerActions(targetTime, true, currentOptions, currentCues);
        return;
      }
    }

    // 1. Audio Mute evaluation
    const triggerWantsMute = actions.soundGain === 0;
    const targetMuted = userMutedRef.current || triggerWantsMute;

    // 2. Audio Volume Reduction evaluation
    let targetVolume = userVolumeRef.current;
    if (targetMuted) {
      targetVolume = 0;
    } else if (actions.soundGain > 0 && actions.soundGain < 1) {
      targetVolume = Number((userVolumeRef.current * actions.soundGain).toFixed(2));
    }

    // 3. Visual Hiding evaluation
    const targetHide = actions.hide;

    // Compare against current hardware/element state
    const currentMuted = video.muted;
    const currentVolume = Number(video.volume.toFixed(2));
    const currentHide = isContentHiddenRef.current;

    const muteChanged = currentMuted !== targetMuted;
    const volumeChanged = Math.abs(currentVolume - targetVolume) > 0.01;
    const hideChanged = currentHide !== targetHide;
    const activeCueIdsKey = actions.activeCueIds.join(',');
    const cuesChanged = lastActiveCuesRef.current !== activeCueIdsKey;

    const warnCueIdsKey = actions.warnCueIds.join(',');
    const warnChanged = lastWarnCueIdsRef.current !== warnCueIdsKey;

    if (isSeek) {
      // Seek interruption: immediately cancel any active ramp timer and snap to target
      cancelVolumeRamp();
      if (muteChanged || volumeChanged) {
        console.log(
          `[Trigger] Seek audio snap: muted=${targetMuted}, vol=${targetVolume.toFixed(2)} ` +
            `(userMuted: ${userMutedRef.current}, triggerMute: ${triggerWantsMute}, activeCues: [${activeCueIdsKey}])`,
        );
        video.volume = targetVolume;
        video.muted = targetMuted;
        setIsMuted(targetMuted);
      }
    } else {
      // Normal playback with smooth volume ramp and fade-restoration
      if (targetMuted) {
        if (muteChanged) {
          console.log(
            `[Trigger] Mute transition (fade-out): ${currentMuted} → ${targetMuted} ` +
              `(userMuted: ${userMutedRef.current}, triggerMute: ${triggerWantsMute}, activeCues: [${activeCueIdsKey}])`,
          );
          setIsMuted(true);
          if (rampDuration <= 0) {
            cancelVolumeRamp();
            video.volume = 0;
            video.muted = true;
          } else {
            rampVolume(0, rampDuration, () => {
              const v = videoRef.current;
              if (v) {
                v.muted = targetMuted;
                v.volume = 0;
              }
            });
          }
        }
      } else {
        // targetMuted is false (audio should play at targetVolume)
        if (muteChanged) {
          // Exiting mute: unmute with volume 0, then smoothly ramp up to target volume
          console.log(
            `[Trigger] Unmute transition (fade-in): ${currentMuted} → false ` +
              `(targetVolume: ${targetVolume.toFixed(2)}, activeCues: [${activeCueIdsKey}])`,
          );
          setIsMuted(false);
          video.muted = false;
          video.volume = 0;
          if (rampDuration <= 0) {
            cancelVolumeRamp();
            video.volume = targetVolume;
          } else {
            rampVolume(targetVolume, rampDuration);
          }
        } else if (volumeChanged && rampTargetVolumeRef.current !== targetVolume) {
          // Volume reduction transition or return to baseline volume
          console.log(
            `[Trigger] Volume transition (ramp): ${currentVolume.toFixed(2)} → ${targetVolume.toFixed(2)} ` +
              `(soundGain: ${actions.soundGain}, base: ${userVolumeRef.current}, activeCues: [${activeCueIdsKey}])`,
          );
          if (rampDuration <= 0) {
            cancelVolumeRamp();
            video.volume = targetVolume;
          } else {
            rampVolume(targetVolume, rampDuration);
          }
        }
      }
    }

    if (hideChanged) {
      console.log(
        `[Trigger] Visual hide transition: ${currentHide} → ${targetHide} ` +
          `(action: ${actions.visualAction}, activeCues: [${activeCueIdsKey}])`,
      );
      isContentHiddenRef.current = targetHide;
      setIsContentHidden(targetHide);
    } else if (actions.visualAction === 'dim' || actions.visualAction === 'blur') {
      if (cuesChanged || isSeek) {
        console.warn(
          `[Trigger] Unsupported visual action "${actions.visualAction}" requested on current renderer for cue(s): [${activeCueIdsKey}]. Preserving original cue metadata without mapping to hide.`,
        );
      }
    }

    // 4. Heads-up warning evaluation (updates on cue start, cue end, and seeking)
    if (warnChanged || isSeek) {
      lastWarnCueIdsRef.current = warnCueIdsKey;
      if (actions.warnCueIds.length === 0) {
        setWarningStatus('');
      } else {
        const warnCues = activeCues.filter((c) =>
          actions.warnCueIds.includes(c.id),
        );
        const warnTriggers = warnCues
          .map((c) => c.trigger ?? c.id)
          .join(', ');
        setWarningStatus(`⚠️ Heads-Up Warning: ${warnTriggers}`);
      }
    }

    if (cuesChanged || muteChanged || volumeChanged || hideChanged || warnChanged || isSeek) {
      lastActiveCuesRef.current = activeCueIdsKey;

      if (actions.activeCueIds.length === 0) {
        setActiveActionStatus('');
      } else {
        const activeCueList = activeCues.filter((c) =>
          actions.activeCueIds.includes(c.id),
        );
        const triggerNames = activeCueList
          .map((c) => c.trigger ?? c.id)
          .join(', ');

        let actionDesc = '';
        if (targetHide) {
          actionDesc += '👁️ Visual Hidden';
        } else if (actions.visualAction === 'dim' || actions.visualAction === 'blur') {
          actionDesc += `⚠️ ${actions.visualAction.toUpperCase()} (unsupported on renderer)`;
        }

        if (triggerWantsMute) {
          actionDesc += `${actionDesc ? ' • ' : ''}🔇 Audio Muted`;
        } else if (actions.soundGain < 1) {
          actionDesc += `${actionDesc ? ' • ' : ''}🔉 Volume Reduced (${Math.round(
            actions.soundGain * 100,
          )}%)`;
        }

        if (actions.warnCueIds.length > 0 && !actionDesc) {
          actionDesc = '⚠️ Heads-Up Alert';
        }

        setActiveActionStatus(`${actionDesc} [${triggerNames}]`);
      }
    }
  };

  const togglePlayback = async () => {
    const video = videoRef.current;
    if (!video) {
      setStatus('Player is not ready yet.');
      return;
    }

    if (isPlaying) {
      video.pause();
      setIsPlaying(false);
      setStatus('Paused');
      return;
    }

    console.log('[Player] play() called — muted:', video.muted, 'volume:', video.volume);
    try {
      await video.play();
    } catch {
      if (video.paused) {
        console.log('[Player] play() rejected and video is still paused — genuine failure');
        setStatus('Playback failed. Check the video URL and network.');
      } else {
        console.log('[Player] play() rejected but video is playing — ignoring library quirk');
      }
    }
  };

  const seekBy = (seconds: number) => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    const currentPos = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    const maxDur = Number.isFinite(video.duration) ? video.duration : Infinity;
    const newTime = Math.max(0, Math.min(currentPos + seconds, maxDur));

    console.log(
      `[Player] seek: currentTime=${currentPos.toFixed(2)}` +
        ` delta=${seconds > 0 ? '+' : ''}${seconds}` +
        ` → ${newTime.toFixed(2)}`,
    );

    // Notify playback engine of seek (for skip-invalidation on rewind)
    playbackStateRef.current.seek(newTime, activeCues);

    // Apply native seek
    video.currentTime = newTime;
    setCurrentTime(newTime);

    // Immediately resolve and apply trigger actions for the new seek destination
    applyTriggerActions(newTime, true);
  };

  const toggleMute = () => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    const nextUserMuted = !userMutedRef.current;
    userMutedRef.current = nextUserMuted;
    console.log('[Player] toggleMute — user manual mute set to:', nextUserMuted);

    // If active trigger requires mute, video must remain muted
    const currentActions = effectiveActionsRef.current;
    const triggerWantsMute = currentActions?.soundGain === 0;
    const targetMuted = nextUserMuted || triggerWantsMute;

    cancelVolumeRamp();
    applyTriggerActions(video.currentTime, true);
    console.log(
      '[Player] toggleMute — effective video.muted now:',
      video.muted,
      'volume:',
      video.volume,
    );
  };

  // Serialized persistence worker to prevent concurrent/out-of-order writes
  const persistViewerOptions = (optionsToSave: ViewerOptions) => {
    latestSavePayloadRef.current = optionsToSave;
    if (isSavingRef.current) {
      return;
    }
    isSavingRef.current = true;

    (async () => {
      try {
        while (latestSavePayloadRef.current !== null) {
          const payload = latestSavePayloadRef.current;
          latestSavePayloadRef.current = null;
          await setItem(STORAGE_KEYS.USER_SETTINGS, payload);
        }
      } catch (e) {
        console.error('Failed to save viewer options to storage', e);
      } finally {
        isSavingRef.current = false;
        if (latestSavePayloadRef.current !== null) {
          persistViewerOptions(latestSavePayloadRef.current);
        }
      }
    })();
  };

  const updateViewerOptions = (
    updater: (prev: ViewerOptions) => ViewerOptions,
  ) => {
    hasUserModifiedOptionsRef.current = true;
    setViewerOptions((prev) => {
      const next = updater(prev);
      viewerOptionsRef.current = next;
      const video = videoRef.current;
      if (video) {
        applyTriggerActions(video.currentTime, true, next);
      }
      persistViewerOptions(next);
      return next;
    });
  };

  // Restore saved viewer preferences from AsyncStorage on mount
  useEffect(() => {
    isMountedRef.current = true;

    (async () => {
      try {
        const saved = await getItem<unknown>(STORAGE_KEYS.USER_SETTINGS);
        if (
          isMountedRef.current &&
          !hasUserModifiedOptionsRef.current &&
          saved !== null &&
          saved !== undefined
        ) {
          const merged = sanitizeViewerOptions(saved, viewerOptionsRef.current);
          setViewerOptions(merged);
          viewerOptionsRef.current = merged;

          const video = videoRef.current;
          if (video) {
            applyTriggerActions(video.currentTime, true, merged);
          }
        }
      } catch (e) {
        console.error('Failed to restore viewer options from storage', e);
      }
    })();

    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const toggleProfile = () => {
    const nextProfile: PlayerProfile =
      profile === 'verification'
        ? 'standard'
        : profile === 'standard'
        ? 'skip_verification'
        : 'verification';
    setProfile(nextProfile);
    lastActiveCuesRef.current = '';
    lastWarnCueIdsRef.current = '';
    setWarningStatus('');
    cancelVolumeRamp();
    playbackStateRef.current.reset();
    console.log(`[Player] Switched trigger profile to: ${nextProfile}`);

    const nextCues =
      customCues ?? getProfileCues(nextProfile);

    // Re-evaluate actions immediately with new cues
    const video = videoRef.current;
    if (video) {
      applyTriggerActions(video.currentTime, true, viewerOptionsRef.current, nextCues);
    }
  };

  const formatTime = (seconds: number) => {
    if (!Number.isFinite(seconds) || seconds < 0) {
      return '00:00';
    }

    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  };

  const handleThatBotheredMe = async () => {
    const video = videoRef.current;
    const time = Number.isFinite(video?.currentTime)
      ? (video?.currentTime as number)
      : currentTime;

    const activeAtTime = activeCues.filter(
      (cue) => cue.start <= time && time < cue.end,
    );
    const triggerNames = activeAtTime
      .map((c) => c.trigger ?? c.id)
      .filter(Boolean);
    const activeTrigger =
      triggerNames.length > 0 ? triggerNames.join(', ') : 'none';

    await appendBotheredLog({
      timestamp: Number(time.toFixed(2)),
      activeTrigger,
      loggedAt: Date.now(),
    });

    setBotheredFeedback(`🚩 Marked at ${formatTime(time)} [${activeTrigger}]`);

    if (botheredFeedbackTimerRef.current !== null) {
      clearTimeout(botheredFeedbackTimerRef.current);
    }
    botheredFeedbackTimerRef.current = setTimeout(() => {
      setBotheredFeedback('');
      botheredFeedbackTimerRef.current = null;
    }, 3000);
  };

  return (
    <View style={styles.container}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>Trigger-Aware Player</Text>
        <Text style={styles.profileBadge}>
          Profile:{' '}
          {profile === 'verification'
            ? 'Verification Cues'
            : profile === 'skip_verification'
            ? 'Skip Verification'
            : 'Sintel Index'}
        </Text>
      </View>

      <View
        style={styles.videoFrame}
        onLayout={(e) => {
          const {width, height} = e.nativeEvent.layout;
          if (width > 0 && height > 0) {
            const nextW = Math.round(width);
            const nextH = Math.round(height);
            setVideoDimensions((prev) =>
              prev.width === nextW && prev.height === nextH
                ? prev
                : {width: nextW, height: nextH},
            );
          }
        }}>
        <Video
          ref={(instance) => {
            (videoRef as any).current = instance;
            if (instance) {
              videoInstanceRef.current = instance;
            }
          }}
          width={videoDimensions.width}
          height={videoDimensions.height}
          style={[styles.video, isContentHidden && styles.videoHidden]}
          src={videoSource}
          autoplay={true}
          controls={false}
          volume={1}
          muted={false}
          scalingmode="fit"
          onComponentDidMount={() => {
            console.log('[Player] Component mounted — src:', videoSource);
            setStatus('Player ready');
          }}
          onLoadStart={() => {
            console.log('[Player] onLoadStart — network fetch begun');
            setStatus('Buffering…');
          }}
          onLoadedMetadata={() => {
            const video = videoRef.current;
            if (video) {
              const dur = video.duration;
              console.log('[Player] onLoadedMetadata — duration:', dur, 'muted:', video.muted, 'volume:', video.volume);

              const tracks = video.audioTracks;
              console.log('[Player]   audioTracks.length:', tracks?.length ?? 'none');
              if (tracks && tracks.length > 0) {
                for (let i = 0; i < tracks.length; i++) {
                  const t = tracks.getTrackById(String(i));
                  if (t && !t.enabled) {
                    console.log('[Player]   => enabling audioTrack', t.id);
                    t.enabled = true;
                  }
                }
              }

              if (Number.isFinite(dur)) {
                setDuration(dur);
              }
            }
            setStatus('Video loaded');
          }}
          onCanPlay={() => {
            console.log('[Player] onCanPlay — ready to play');
            setStatus('Ready');
            const video = videoRef.current;
            if (video && video.paused) {
              console.log('[Player] onCanPlay — initiating play()');
              video.play().catch(() => {});
            }
          }}
          onPlaying={() => {
            const video = videoRef.current;
            console.log(
              '[Player] onPlaying — muted:',
              video?.muted,
              'volume:',
              video?.volume,
            );
            setIsPlaying(true);
            setStatus('Playing');
          }}
          onPause={() => {
            console.log('[Player] onPause');
            setIsPlaying(false);
            setStatus('Paused');
          }}
          onTimeUpdate={() => {
            const video = videoRef.current;
            if (video) {
              const t = video.currentTime;
              setCurrentTime(t);
              const d = video.duration;
              if (Number.isFinite(d) && d !== duration) {
                setDuration(d);
              }

              // Apply trigger filtering actions for current playback time
              applyTriggerActions(t);

              if (Math.floor(t) !== Math.floor(lastLoggedSecond.current)) {
                lastLoggedSecond.current = t;
                console.log(
                  `[Player] onTimeUpdate: pos=${t.toFixed(2)}s / dur=${d ? d.toFixed(2) : '?'}s` +
                    ` muted=${video.muted} vol=${video.volume.toFixed(2)} hidden=${isContentHiddenRef.current}`,
                );
              }
            }
          }}
          onSeeked={() => {
            const video = videoRef.current;
            if (video) {
              console.log('[Player] onSeeked — currentTime:', video.currentTime.toFixed(2));
              setCurrentTime(video.currentTime);
              applyTriggerActions(video.currentTime, true);
            }
          }}
          onEnded={() => {
            console.log('[Player] onEnded');
            setIsPlaying(false);
            setStatus('Playback finished');
            setShowRecap(true);
          }}
          onError={() => {
            const video = videoRef.current;
            const err = video?.error;
            const code = err?.code ?? 'unknown';
            const msg = err?.message ?? 'no message';
            console.log(`[Player] onError — code: ${code} message: ${msg} src: ${videoSource}`);
            setStatus(`Error ${code}: ${msg}`);
          }}
          onAudioFocusGranted={() => {
            console.log('[Player] onAudioFocusGranted — audio focus restored');
            setStatus('Playing');
          }}
          onAudioFocusPaused={() => {
            console.log('[Player] onAudioFocusPaused — audio focus lost temporarily');
            setIsPlaying(false);
            setStatus('Paused (audio focus lost)');
          }}
          onAudioFocusLost={() => {
            console.log('[Player] onAudioFocusLost — audio focus lost permanently');
            setIsPlaying(false);
            setStatus('Stopped (audio focus lost)');
          }}
        />

        {/* Fallback visual overlay rendered when visual hiding is triggered */}
        {isContentHidden && (
          <View
            style={styles.hiddenOverlay}
            testID="trigger-hidden-overlay"
            accessibilityLabel="Visual content hidden due to trigger-aware filter">
            <Text style={styles.hiddenOverlayIcon}>🛡️</Text>
            <Text style={styles.hiddenOverlayTitle}>Visual Content Hidden</Text>
            <Text style={styles.hiddenOverlaySubtitle}>
              Protection active for sensitive visual trigger
            </Text>
          </View>
        )}
      </View>

      {/* Heads-up warning banner for warn cues */}
      {warningStatus ? (
        <View
          style={styles.warningBanner}
          testID="warning-banner"
          accessibilityRole="alert"
          accessibilityLabel={warningStatus}>
          <Text style={styles.warningBannerText}>{warningStatus}</Text>
        </View>
      ) : null}

      {/* Active trigger notification banner */}
      {activeActionStatus ? (
        <View style={styles.triggerBanner} testID="trigger-banner">
          <Text style={styles.triggerBannerText}>{activeActionStatus}</Text>
        </View>
      ) : (
        <View style={styles.triggerBannerPlaceholder} />
      )}

      <Text style={styles.status}>{status}</Text>
      <Text style={styles.time}>
        {formatTime(currentTime)} / {formatTime(duration)}
      </Text>

      <TVFocusGuideView style={styles.controls} autoFocus>
        <Control
          id="back"
          testID="control-back-10s"
          label="Back 10s"
          onPress={() => seekBy(-10)}
          isFocused={focusedControl === 'back'}
          onFocus={() => setFocusedControl('back')}
        />
        <Control
          id="play"
          testID="control-play-pause"
          label={isPlaying ? 'Pause' : 'Play'}
          onPress={togglePlayback}
          isFocused={focusedControl === 'play'}
          onFocus={() => setFocusedControl('play')}
          preferred
        />
        <Control
          id="forward"
          testID="control-forward-10s"
          label="Forward 10s"
          onPress={() => seekBy(10)}
          isFocused={focusedControl === 'forward'}
          onFocus={() => setFocusedControl('forward')}
        />
        <Control
          id="mute"
          testID="control-mute"
          label={isMuted ? 'Unmute' : 'Mute'}
          onPress={toggleMute}
          isFocused={focusedControl === 'mute'}
          onFocus={() => setFocusedControl('mute')}
        />
        <Control
          id="profile"
          testID="control-profile"
          label={
            profile === 'verification'
              ? 'Cues: Verify'
              : profile === 'skip_verification'
              ? 'Cues: Skip'
              : 'Cues: Index'
          }
          onPress={toggleProfile}
          isFocused={focusedControl === 'profile'}
          onFocus={() => setFocusedControl('profile')}
          focusable={!isSettingsOpen}
        />
        <Control
          id="bothered"
          testID="control-bothered"
          label="🚩 That Bothered Me"
          onPress={handleThatBotheredMe}
          isFocused={focusedControl === 'bothered'}
          onFocus={() => setFocusedControl('bothered')}
          focusable={!isSettingsOpen && !showRecap}
        />
        <Control
          id="settings"
          testID="control-settings"
          label="Settings"
          onPress={() => {
            setIsSettingsOpen(true);
            setFocusedDrawerItem('toggle-visual');
          }}
          isFocused={focusedControl === 'settings'}
          onFocus={() => setFocusedControl('settings')}
          focusable={!isSettingsOpen}
        />
        <Control
          id="home"
          testID="control-home"
          label="Home"
          onPress={onBack}
          isFocused={focusedControl === 'home'}
          onFocus={() => setFocusedControl('home')}
          focusable={!isSettingsOpen}
        />
      </TVFocusGuideView>

      {/* In-player settings drawer */}
      {isSettingsOpen && (
        <View style={styles.drawerBackdrop} testID="settings-drawer">
          <TVFocusGuideView
            style={styles.drawerPanel}
            autoFocus
            trapFocusUp
            trapFocusDown
            trapFocusLeft
            trapFocusRight>
            <View style={styles.drawerHeader}>
              <Text style={styles.drawerTitle}>Protection Settings</Text>
              <Text style={styles.drawerSubtitle}>
                Customize actions and active trigger categories
              </Text>
            </View>

            <ScrollView
              style={styles.drawerScroll}
              contentContainerStyle={styles.drawerScrollContent}>
              <Text style={styles.drawerSectionHeader}>ACTION PROTECTIONS</Text>
              <DrawerToggleItem
                testID="toggle-visual-protection"
                label="Visual Protection (Hide)"
                enabled={viewerOptions.hideEnabled}
                isFocused={focusedDrawerItem === 'toggle-visual'}
                onFocus={() => setFocusedDrawerItem('toggle-visual')}
                onPress={() =>
                  updateViewerOptions((prev) => ({
                    ...prev,
                    hideEnabled: !prev.hideEnabled,
                  }))
                }
                hasTVPreferredFocus={true}
              />
              <DrawerToggleItem
                testID="toggle-audio-protection"
                label="Audio Protection (Mute)"
                enabled={viewerOptions.muteEnabled}
                isFocused={focusedDrawerItem === 'toggle-audio'}
                onFocus={() => setFocusedDrawerItem('toggle-audio')}
                onPress={() =>
                  updateViewerOptions((prev) => ({
                    ...prev,
                    muteEnabled: !prev.muteEnabled,
                  }))
                }
              />
              <DrawerToggleItem
                testID="toggle-volume-reduction"
                label="Volume Reduction"
                enabled={viewerOptions.reduceEnabled}
                isFocused={focusedDrawerItem === 'toggle-reduce'}
                onFocus={() => setFocusedDrawerItem('toggle-reduce')}
                onPress={() =>
                  updateViewerOptions((prev) => ({
                    ...prev,
                    reduceEnabled: !prev.reduceEnabled,
                  }))
                }
              />
              <DrawerToggleItem
                testID="toggle-skip-protection"
                label="Skip Protection"
                enabled={viewerOptions.skipEnabled}
                isFocused={focusedDrawerItem === 'toggle-skip'}
                onFocus={() => setFocusedDrawerItem('toggle-skip')}
                onPress={() =>
                  updateViewerOptions((prev) => ({
                    ...prev,
                    skipEnabled: !prev.skipEnabled,
                  }))
                }
              />

              {activeCategories.length > 0 && (
                <>
                  <Text style={styles.drawerSectionHeader}>
                    ACTIVE TRIGGER CATEGORIES
                  </Text>
                  {activeCategories.map((category) => {
                    const isCategoryActive =
                      !viewerOptions.disabledCategories?.includes(category);
                    const itemKey = `toggle-cat-${category}`;
                    return (
                      <DrawerToggleItem
                        key={category}
                        testID={`toggle-category-${category}`}
                        label={`Trigger: ${category}`}
                        enabled={isCategoryActive}
                        isFocused={focusedDrawerItem === itemKey}
                        onFocus={() => setFocusedDrawerItem(itemKey)}
                        onPress={() =>
                          updateViewerOptions((prev) => {
                            const disabled = prev.disabledCategories ?? [];
                            const nextDisabled = disabled.includes(category)
                              ? disabled.filter((c) => c !== category)
                              : [...disabled, category];
                            return {
                              ...prev,
                              disabledCategories: nextDisabled,
                            };
                          })
                        }
                      />
                    );
                  })}
                </>
              )}
            </ScrollView>

            <TouchableOpacity
              style={[
                styles.doneButton,
                focusedDrawerItem === 'done' && styles.doneButtonFocused,
              ]}
              testID="settings-done"
              onPress={() => {
                setIsSettingsOpen(false);
                setFocusedControl('settings');
              }}
              onFocus={() => setFocusedDrawerItem('done')}
              accessibilityRole="button"
              accessibilityLabel="Done, close settings drawer"
              activeOpacity={0.8}>
              <Text style={styles.doneButtonText}>Done</Text>
            </TouchableOpacity>
          </TVFocusGuideView>
        </View>
      )}

      {/* Toast feedback when "That Bothered Me" is clicked */}
      {botheredFeedback ? (
        <View style={styles.botheredToast} testID="bothered-toast">
          <Text style={styles.botheredToastText}>{botheredFeedback}</Text>
        </View>
      ) : null}

      {/* Playback Protection Recap view rendered at end of playback */}
      {showRecap && (
        <RecapView
          sessionPayload={playbackStateRef.current.getSessionPayload()}
          onClose={() => setShowRecap(false)}
          onRestart={() => {
            setShowRecap(false);
            seekBy(-currentTime);
            togglePlayback();
          }}
        />
      )}
    </View>
  );
};

interface ControlProps {
  id: string;
  label: string;
  onPress: () => void;
  isFocused: boolean;
  onFocus: () => void;
  onBlur?: () => void;
  preferred?: boolean;
  testID?: string;
  accessibilityLabel?: string;
  focusable?: boolean;
}

const Control = ({
  id: _id,
  label,
  onPress,
  isFocused,
  onFocus,
  onBlur,
  preferred = false,
  testID,
  accessibilityLabel,
  focusable = true,
}: ControlProps) => (
  <TouchableOpacity
    style={[
      styles.control,
      preferred && styles.preferredControl,
      isFocused && styles.focusedControl,
    ]}
    onPress={onPress}
    onFocus={onFocus}
    onBlur={onBlur}
    testID={testID}
    accessibilityRole="button"
    accessibilityLabel={accessibilityLabel ?? label}
    hasTVPreferredFocus={preferred}
    focusable={focusable}
    activeOpacity={0.8}>
    <Text style={[styles.controlText, isFocused && styles.focusedControlText]}>
      {label}
    </Text>
  </TouchableOpacity>
);

interface DrawerToggleItemProps {
  label: string;
  enabled: boolean;
  onPress: () => void;
  isFocused: boolean;
  onFocus: () => void;
  testID: string;
  accessibilityLabel?: string;
  hasTVPreferredFocus?: boolean;
}

const DrawerToggleItem = ({
  label,
  enabled,
  onPress,
  isFocused,
  onFocus,
  testID,
  accessibilityLabel,
  hasTVPreferredFocus,
}: DrawerToggleItemProps) => (
  <TouchableOpacity
    style={[styles.drawerItem, isFocused && styles.drawerItemFocused]}
    onPress={onPress}
    onFocus={onFocus}
    testID={testID}
    accessibilityRole="switch"
    accessibilityLabel={
      accessibilityLabel ?? `${label}, currently ${enabled ? 'enabled' : 'disabled'}`
    }
    accessibilityState={{ checked: enabled }}
    hasTVPreferredFocus={hasTVPreferredFocus}
    activeOpacity={0.8}>
    <Text
      style={[
        styles.drawerItemLabel,
        isFocused && styles.drawerItemLabelFocused,
      ]}>
      {label}
    </Text>
    <View
      style={[
        styles.statusBadge,
        enabled ? styles.statusBadgeOn : styles.statusBadgeOff,
      ]}>
      <Text style={styles.statusBadgeText}>{enabled ? 'ON' : 'OFF'}</Text>
    </View>
  </TouchableOpacity>
);

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#101820',
    paddingHorizontal: 48,
    paddingVertical: 32,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  title: {
    color: '#FFFFFF',
    fontSize: 32,
    fontWeight: 'bold',
  },
  profileBadge: {
    color: '#38BDF8',
    backgroundColor: '#0F172A',
    borderColor: '#0284C7',
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    fontSize: 14,
    fontWeight: '600',
  },
  videoFrame: {
    height: 400,
    backgroundColor: '#000000',
    borderRadius: 12,
    overflow: 'hidden',
    position: 'relative',
  },
  video: {
    width: '100%',
    height: '100%',
  },
  videoHidden: {
    opacity: 0,
  },
  hiddenOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
    zIndex: 10,
  },
  hiddenOverlayIcon: {
    fontSize: 48,
    marginBottom: 12,
  },
  hiddenOverlayTitle: {
    color: '#F8FAFC',
    fontSize: 24,
    fontWeight: 'bold',
    textAlign: 'center',
  },
  hiddenOverlaySubtitle: {
    color: '#94A3B8',
    fontSize: 16,
    marginTop: 8,
    textAlign: 'center',
  },
  warningBanner: {
    backgroundColor: '#7C2D12',
    borderColor: '#F97316',
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 16,
    marginTop: 12,
    alignSelf: 'flex-start',
  },
  warningBannerText: {
    color: '#FFEDD5',
    fontSize: 15,
    fontWeight: '700',
  },
  triggerBanner: {
    backgroundColor: '#1E293B',
    borderColor: '#38BDF8',
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 16,
    marginTop: 12,
    alignSelf: 'flex-start',
  },
  triggerBannerPlaceholder: {
    height: 38,
    marginTop: 12,
  },
  triggerBannerText: {
    color: '#F8FAFC',
    fontSize: 15,
    fontWeight: '600',
  },
  status: {
    color: '#FFFFFF',
    fontSize: 18,
    marginTop: 8,
  },
  time: {
    color: '#94A3B8',
    fontSize: 22,
    marginTop: 4,
    fontVariant: ['tabular-nums'],
  },
  controls: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 16,
    marginTop: 20,
  },
  control: {
    backgroundColor: '#1E293B',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 8,
    borderWidth: 3,
    borderColor: 'transparent',
  },
  preferredControl: {
    backgroundColor: '#1E3A8A',
  },
  focusedControl: {
    backgroundColor: '#2563EB',
    borderColor: '#FFFFFF',
    borderWidth: 3,
    transform: [{scale: 1.08}],
  },
  controlText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
  focusedControlText: {
    color: '#FFFFFF',
    fontWeight: '700',
  },
  drawerBackdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0, 0, 0, 0.65)',
    flexDirection: 'row',
    justifyContent: 'flex-end',
    zIndex: 50,
  },
  drawerPanel: {
    width: 480,
    height: '100%',
    backgroundColor: '#0F172A',
    borderLeftWidth: 2,
    borderLeftColor: '#334155',
    paddingHorizontal: 28,
    paddingVertical: 24,
    justifyContent: 'space-between',
  },
  drawerHeader: {
    marginBottom: 16,
  },
  drawerTitle: {
    color: '#FFFFFF',
    fontSize: 24,
    fontWeight: 'bold',
  },
  drawerSubtitle: {
    color: '#94A3B8',
    fontSize: 14,
    marginTop: 4,
  },
  drawerScroll: {
    flex: 1,
  },
  drawerScrollContent: {
    paddingBottom: 16,
  },
  drawerSectionHeader: {
    color: '#38BDF8',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1,
    marginTop: 16,
    marginBottom: 8,
  },
  drawerItem: {
    backgroundColor: '#1E293B',
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 12,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
    borderWidth: 2,
    borderColor: 'transparent',
  },
  drawerItemFocused: {
    backgroundColor: '#1E3A8A',
    borderColor: '#FFFFFF',
    transform: [{ scale: 1.02 }],
  },
  drawerItemLabel: {
    color: '#F8FAFC',
    fontSize: 15,
    fontWeight: '500',
  },
  drawerItemLabelFocused: {
    color: '#FFFFFF',
    fontWeight: '700',
  },
  statusBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
  },
  statusBadgeOn: {
    backgroundColor: '#059669',
  },
  statusBadgeOff: {
    backgroundColor: '#475569',
  },
  statusBadgeText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: '700',
  },
  doneButton: {
    backgroundColor: '#2563EB',
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 12,
    borderWidth: 2,
    borderColor: 'transparent',
  },
  doneButtonFocused: {
    borderColor: '#FFFFFF',
    backgroundColor: '#1D4ED8',
    transform: [{ scale: 1.03 }],
  },
  doneButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: 'bold',
  },
  botheredToast: {
    position: 'absolute',
    top: 36,
    alignSelf: 'center',
    backgroundColor: '#0F172A',
    borderColor: '#EF4444',
    borderWidth: 2,
    borderRadius: 8,
    paddingHorizontal: 20,
    paddingVertical: 10,
    zIndex: 90,
  },
  botheredToastText: {
    color: '#F8FAFC',
    fontSize: 16,
    fontWeight: '700',
  },
});
