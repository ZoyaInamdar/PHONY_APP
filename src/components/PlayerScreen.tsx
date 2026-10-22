import React, {useEffect, useRef, useState} from 'react';
import {
  Dimensions,
  GestureResponderEvent,
  PanResponder,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import {BackHandler, TVFocusGuideView} from '@amazon-devices/react-native-kepler';
import {
  Video,
  VideoPlayer,
  KeplerShareableSurfaceView,
} from '@amazon-devices/react-native-w3cmedia';
import {
  Cue,
  EffectiveActions,
  ViewerOptions,
  CANONICAL_TRIGGERS,
  CanonicalTrigger,
  resolveEffectiveAction,
  sanitizeActionOverrides,
} from '../engine/types';
import {PlaybackState} from '../engine/playbackState';
import {applyReviewPolicy} from '../engine/reviewPolicy';
import {
  defaultPlaybackOptions,
  sintelIndexCues,
  sintelTrailerCues,
  skipVerificationTestCues,
  verificationTestCues,
} from '../data/playbackCues';
import {loadAndValidateFilmIndex} from '../engine/validatedPlayback';
import {
  getFilmMetadata,
  loadFilmCues,
  loadFilmSubtitles,
  validateSubtitleCues,
} from '../data/catalogue';
import type {SubtitleCue} from '../subtitles/types';
import {CaptionRenderer} from './CaptionRenderer';
import {
  PROMPT_HARNESS_SCENARIOS,
  PromptScenario,
} from '../data/promptHarnessScenarios';
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

export const PLAYER_PROFILES = [
  'standard',
  'verification',
  'skip_verification',
] as const;
export type PlayerProfile = (typeof PLAYER_PROFILES)[number];

export function isValidPlayerProfile(value: unknown): value is PlayerProfile {
  return (
    typeof value === 'string' &&
    (value === 'standard' || value === 'verification' || value === 'skip_verification')
  );
}

export interface PlayerScreenProps {
  onBack: () => void;
  filmId?: string;
  cues?: Cue[];
  subtitles?: SubtitleCue[];
  options?: Partial<ViewerOptions>;
  initialProfile?: PlayerProfile;
  testVideoUrl?: string;
  alternateBlurredVideoUrl?: string;
  blurSwitchTimeoutMs?: number;
  rampDurationMs?: number;
  devHarnessMode?: boolean;
  initialHarnessScenario?: number;
  packagedIndex?: unknown;
  mediaDuration?: number;
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
 * Formats a duration in seconds to standard MM:SS or HH:MM:SS format.
 * Durations < 3600 seconds render as MM:SS (e.g. 00:00, 12:34).
 * Durations >= 3600 seconds render as HH:MM:SS (e.g. 01:00:00).
 */
export function formatTime(seconds: number, filmDuration?: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return '00:00';
  }

  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hrs > 0 || (typeof filmDuration === 'number' && Number.isFinite(filmDuration) && filmDuration >= 3600)) {
    return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

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
  if (
    obj.headsUpPrompts &&
    typeof obj.headsUpPrompts === 'object' &&
    !Array.isArray(obj.headsUpPrompts)
  ) {
    const promptObj = obj.headsUpPrompts as Record<string, unknown>;
    const enabled =
      typeof promptObj.enabled === 'boolean'
        ? promptObj.enabled
        : fallback.headsUpPrompts?.enabled ?? false;
    const leadTimeSeconds =
      typeof promptObj.leadTimeSeconds === 'number' &&
      Number.isFinite(promptObj.leadTimeSeconds) &&
      promptObj.leadTimeSeconds > 0
        ? promptObj.leadTimeSeconds
        : fallback.headsUpPrompts?.leadTimeSeconds ?? 5.0;
    result.headsUpPrompts = { enabled, leadTimeSeconds };
  }
  if (obj.actionOverrides && typeof obj.actionOverrides === 'object') {
    result.actionOverrides = sanitizeActionOverrides(obj.actionOverrides);
  } else {
    result.actionOverrides = { ...(fallback.actionOverrides ?? {}) };
  }

  return result;
}

export const PlayerScreen = ({
  onBack,
  filmId: initialFilmId = 'sintel',
  cues: customCues,
  subtitles: customSubtitles,
  options: customOptions,
  initialProfile,
  testVideoUrl,
  alternateBlurredVideoUrl,
  blurSwitchTimeoutMs = 500,
  rampDurationMs,
  devHarnessMode = false,
  initialHarnessScenario = 0,
  packagedIndex,
  mediaDuration,
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
  // Fallback deduplication tracking for unsupported renderer actions (e.g. Vega blur fallback)
  const recordedFallbackCueIdsRef = useRef<Set<string>>(new Set());

  // Dual-video switching state and refs for optional preprocessed video blur
  const isAlternateAssetConfigured =
    typeof alternateBlurredVideoUrl === 'string' &&
    alternateBlurredVideoUrl.trim().length > 0;
  const alternatePlayerRef = useRef<VideoPlayer | null>(null);
  const alternateSurfaceHandleRef = useRef<string | null>(null);
  const alternateReadyRef = useRef<boolean>(false);
  const alternateErrorRef = useRef<boolean>(false);
  const isBlurActiveRef = useRef<boolean>(false);
  const [isBlurActive, setIsBlurActive] = useState<boolean>(false);

  const blurTransitionTokenRef = useRef<number>(0);
  const activeBlurSeekTokenRef = useRef<number>(0);
  const isSeekingAlternateRef = useRef<boolean>(false);
  const blurSeekTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const blurFallbackActiveRef = useRef<boolean>(false);
  const lastBlurActiveRef = useRef<boolean>(false);

  // Selected film from catalogue
  const [currentFilmId, setCurrentFilmId] = useState<string>(initialFilmId);

  // Look up film metadata from catalogue
  const filmMetadata = getFilmMetadata(currentFilmId);

  // Load cues for standard index profile with strict validation (no silent fallback)
  let standardIndexCues: Cue[] = [];
  let filmSubtitles: SubtitleCue[] = [];
  let indexLoadError: string | null = null;
  if (!filmMetadata) {
    indexLoadError = `Unknown film ID: "${currentFilmId}". No matching entry found in catalogue.`;
  } else {
    try {
      standardIndexCues = loadFilmCues(currentFilmId);
    } catch (err: any) {
      indexLoadError = `Failed to load packaged index for "${currentFilmId}": ${err?.message ?? err}`;
    }
    if (customSubtitles && currentFilmId === initialFilmId) {
      try {
        filmSubtitles = validateSubtitleCues(customSubtitles, { filmId: currentFilmId });
      } catch (err: any) {
        console.warn(`[PlayerScreen] Invalid custom subtitles for "${currentFilmId}":`, err?.message ?? err);
        filmSubtitles = [];
      }
    } else {
      try {
        filmSubtitles = loadFilmSubtitles(currentFilmId);
      } catch (err: any) {
        console.warn(`[PlayerScreen] Failed to load subtitles for "${currentFilmId}":`, err);
      }
    }
  }

  useEffect(() => {
    if (initialFilmId && initialFilmId !== currentFilmId) {
      handleFilmSwitch(initialFilmId);
    }
  }, [initialFilmId]);

  // Selected trigger cues & profile
  const hasExplicitInitialProfileRef = useRef<boolean>(initialProfile !== undefined);
  const [profile, setProfile] = useState<PlayerProfile>(initialProfile ?? 'standard');
  const profileRef = useRef<PlayerProfile>(profile);
  profileRef.current = profile;
  const hasUserModifiedProfileRef = useRef<boolean>(false);

  // initialProfile seeds the profile state on mount; subsequent rerenders follow the initial-only contract

  // Serialized persistence worker for user profile to prevent out-of-order writes
  const latestSaveProfilePayloadRef = useRef<PlayerProfile | null>(null);
  const isSavingProfileRef = useRef<boolean>(false);

  const persistProfile = (profileToSave: PlayerProfile) => {
    latestSaveProfilePayloadRef.current = profileToSave;
    if (isSavingProfileRef.current) {
      return;
    }
    isSavingProfileRef.current = true;

    (async () => {
      try {
        while (latestSaveProfilePayloadRef.current !== null) {
          const payload = latestSaveProfilePayloadRef.current;
          latestSaveProfilePayloadRef.current = null;
          await setItem(STORAGE_KEYS.USER_PROFILE, payload);
        }
      } catch (e) {
        console.error('Failed to save user profile to storage', e);
      } finally {
        isSavingProfileRef.current = false;
        if (latestSaveProfilePayloadRef.current !== null) {
          persistProfile(latestSaveProfilePayloadRef.current);
        }
      }
    })();
  };

  // Developer prompt test harness state
  const [isDevHarnessOpen, setIsDevHarnessOpen] = useState<boolean>(devHarnessMode);
  const [selectedHarnessScenarioIndex, setSelectedHarnessScenarioIndex] = useState<number>(
    initialHarnessScenario >= 0 && initialHarnessScenario < PROMPT_HARNESS_SCENARIOS.length
      ? initialHarnessScenario
      : 0,
  );
  const [focusedHarnessItem, setFocusedHarnessItem] = useState<string>('harness-scenario-0');

  const resolvedIndexCues = React.useMemo(() => {
    if (customCues) {
      return customCues;
    }
    if (packagedIndex) {
      try {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const triggerPoliciesJson = require('../../test/fixtures/triggerPolicies.json');
        return loadAndValidateFilmIndex(
          packagedIndex,
          triggerPoliciesJson.policies,
          {
            filmId: currentFilmId,
            durationSeconds: mediaDuration,
          },
        );
      } catch (err) {
        console.warn('[PlayerScreen] Failed to load packaged index:', err);
        return [];
      }
    }
    if (currentFilmId === 'sintel' || currentFilmId === 'sintel_trailer') {
      return sintelTrailerCues;
    }
    if (currentFilmId === 'tears_of_steel') {
      try {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const triggerPoliciesJson = require('../../test/fixtures/triggerPolicies.json');
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const realTosIndex = require('../../data/processed/tears_of_steel/index.json');
        return loadAndValidateFilmIndex(
          realTosIndex,
          triggerPoliciesJson.policies,
          {
            filmId: 'tears_of_steel',
            durationSeconds: 734.167,
          },
        );
      } catch (e) {
        return tearsOfSteelIndexCues;
      }
    }
    return null;
  }, [customCues, packagedIndex, currentFilmId, mediaDuration]);

  const getProfileCues = (p: PlayerProfile): Cue[] => {
    switch (p) {
      case 'standard':
        return resolvedIndexCues ?? standardIndexCues;
      case 'skip_verification':
        return skipVerificationTestCues;
      case 'verification':
        return verificationTestCues;
      default:
        return resolvedIndexCues ?? standardIndexCues;
    }
  };

  const computeActiveCues = (
    harnessOpen: boolean,
    scenarioIndex: number,
    currentProfile: PlayerProfile,
  ): Cue[] => {
    if (harnessOpen) {
      const scenario = PROMPT_HARNESS_SCENARIOS[scenarioIndex];
      return scenario ? scenario.cues : [];
    }
    if (customCues) {
      return customCues;
    }
    return getProfileCues(currentProfile);
  };

  const activeCues: Cue[] = computeActiveCues(
    isDevHarnessOpen,
    selectedHarnessScenarioIndex,
    profile,
  );

  const activeCuesRef = useRef<Cue[]>(activeCues);
  activeCuesRef.current = activeCues;

  const isDevHarnessOpenRef = useRef<boolean>(isDevHarnessOpen);
  isDevHarnessOpenRef.current = isDevHarnessOpen;

  const selectedHarnessScenarioIndexRef = useRef<number>(selectedHarnessScenarioIndex);
  selectedHarnessScenarioIndexRef.current = selectedHarnessScenarioIndex;

  // User protection preferences state
  const initialHarness = devHarnessMode ? PROMPT_HARNESS_SCENARIOS[selectedHarnessScenarioIndex] : null;
  const baseDefaults: ViewerOptions = {
    ...defaultPlaybackOptions,
    ...customOptions,
    ...(initialHarness?.options ?? {}),
    disabledCategories:
      customOptions?.disabledCategories ??
      initialHarness?.options?.disabledCategories ??
      [],
    headsUpPrompts:
      customOptions?.headsUpPrompts ??
      initialHarness?.options?.headsUpPrompts ??
      defaultPlaybackOptions.headsUpPrompts,
  };

  const [viewerOptions, setViewerOptions] = useState<ViewerOptions>(baseDefaults);
  const viewerOptionsRef = useRef<ViewerOptions>(viewerOptions);
  viewerOptionsRef.current = viewerOptions;

  // Synchronize customOptions prop updates
  useEffect(() => {
    if (customOptions) {
      setViewerOptions((prev) => {
        const next: ViewerOptions = {
          ...prev,
          ...customOptions,
          disabledCategories:
            customOptions.disabledCategories ?? prev.disabledCategories ?? [],
          headsUpPrompts:
            customOptions.headsUpPrompts ?? prev.headsUpPrompts,
        };
        viewerOptionsRef.current = next;
        return next;
      });
    }
  }, [customOptions]);

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
  const [visualProtectionType, setVisualProtectionType] = useState<'hide' | 'blur'>('hide');
  const [activeActionStatus, setActiveActionStatus] = useState<string>('');
  const [warningStatus, setWarningStatus] = useState<string>('');
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isDraggingSeek, setIsDraggingSeek] = useState(false);
  const [dragSeekTime, setDragSeekTime] = useState<number | null>(null);
  const [isSeekBarFocused, setIsSeekBarFocused] = useState(false);
  const seekBarWidthRef = useRef<number>(0);
  const isDraggingSeekRef = useRef<boolean>(false);
  const dragSeekTimeRef = useRef<number | null>(null);
  const dragStartXRef = useRef<number>(0);
  const [status, setStatus] = useState('Loading video…');
  const [focusedControl, setFocusedControl] = useState<string>('play');
  const [botheredFeedback, setBotheredFeedback] = useState<string>('');
  const [showRecap, setShowRecap] = useState<boolean>(false);
  const botheredFeedbackTimerRef = useRef<NodeJS.Timeout | null>(null);

  // Heads-up advance prompt state
  const [activePromptCue, setActivePromptCue] = useState<Cue | null>(null);
  const activePromptCueRef = useRef<Cue | null>(null);
  activePromptCueRef.current = activePromptCue;
  const dismissedPromptCueIdsRef = useRef<Set<string>>(new Set());
  const [focusedPromptButton, setFocusedPromptButton] = useState<'continue' | 'skip'>('continue');
  const handlePromptContinueRef = useRef<() => void>(() => {});

  // Evaluate whether the upcoming prompt cue is eligible for skipping under current viewer options & review policy
  const isSkipEligible = (() => {
    if (!activePromptCue) {
      return false;
    }
    const currentCues = activeCuesRef.current;
    const currentOptions = viewerOptionsRef.current;
    const policyCues = applyReviewPolicy(currentCues, currentOptions);
    const effectiveCue =
      policyCues.find((c) => c.id === activePromptCue.id) ?? activePromptCue;
    return (
      playbackStateRef.current.isCueEligibleForSkip(
        effectiveCue,
        currentOptions,
      ) && effectiveCue.action === 'skip'
    );
  })();

  const promptButtonToFocus = isSkipEligible ? focusedPromptButton : 'continue';

  const [videoSourceOverride, setVideoSourceOverride] = useState<string | null>(null);

  useEffect(() => {
    setVideoSourceOverride(null);
  }, [currentFilmId]);

  const rawVideoSource =
    testVideoUrl ??
    filmMetadata?.vega_video_url ??
    filmMetadata?.video_url ??
    DEFAULT_VIDEO_URL;

  const resolvedVideoSource = (() => {
    if (
      rawVideoSource.startsWith('http://') ||
      rawVideoSource.startsWith('https://') ||
      rawVideoSource.startsWith('pkg://')
    ) {
      return rawVideoSource;
    }
    return `http://10.0.2.2:8080/${rawVideoSource.replace(/^\/+/, '')}`;
  })();

  const videoSource = videoSourceOverride ?? resolvedVideoSource;

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
      if (activePromptCueRef.current) {
        handlePromptContinueRef.current();
        return true;
      }
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

  // Ref to invoke applyTriggerActions from asynchronous callbacks without dependency cycles
  const applyTriggerActionsRef = useRef<
    (
      time: number,
      isSeek?: boolean,
      overrideOptions?: ViewerOptions,
      overrideCues?: Cue[],
    ) => void
  >(() => {});

  // Cleanup on unmount: cancel volume timers, alternate player, reset engine, and restore settings
  useEffect(() => {
    return () => {
      cancelVolumeRamp();
      if (botheredFeedbackTimerRef.current !== null) {
        clearTimeout(botheredFeedbackTimerRef.current);
        botheredFeedbackTimerRef.current = null;
      }
      if (blurSeekTimeoutRef.current !== null) {
        clearTimeout(blurSeekTimeoutRef.current);
        blurSeekTimeoutRef.current = null;
      }
      const video = videoRef.current ?? videoInstanceRef.current;
      if (video) {
        video.muted = false;
        video.volume = 1.0;
      }
      const altPlayer = alternatePlayerRef.current;
      if (altPlayer) {
        const handle = alternateSurfaceHandleRef.current;
        if (handle) {
          altPlayer.clearSurfaceHandle(handle);
        }
        altPlayer.pause();
        altPlayer.deinitialize?.();
      }
      playbackStateRef.current.reset();
      activePromptCueRef.current = null;
      setActivePromptCue(null);
      recordedFallbackCueIdsRef.current.clear();
      if (process.env.NODE_ENV !== 'test') {
        console.log('[Player] Unmounted — playback state reset, audio restored');
      }
    };
  }, []);

  /**
   * Records a blur cue action truthfully:
   * - appliedEffect: 'blurred' if alternate blurred video stream is displayed.
   * - appliedEffect: 'blacked_out' if fallback to blackout was used.
   */
  const recordBlurCueAction = (
    appliedEffect: 'blurred' | 'blacked_out',
    time: number,
    optionsToUse?: ViewerOptions,
    cuesToUse?: Cue[],
  ) => {
    const currentOptions = optionsToUse ?? viewerOptionsRef.current;
    const currentCues = cuesToUse ?? activeCues;
    for (const cue of currentCues) {
      if (
        cue.action === 'blur' &&
        (cue.source === 'visual' || cue.source === 'audio_visual') &&
        cue.start <= time &&
        time < cue.end
      ) {
        if (
          currentOptions.disabledCategories &&
          cue.trigger &&
          currentOptions.disabledCategories.includes(cue.trigger)
        ) {
          continue;
        }

        const actionKey = `blur_${cue.id}`;
        const fallbackKey = `blur_fallback_${cue.id}`;
        if (
          !recordedFallbackCueIdsRef.current.has(actionKey) &&
          !recordedFallbackCueIdsRef.current.has(fallbackKey)
        ) {
          recordedFallbackCueIdsRef.current.add(actionKey);
          recordedFallbackCueIdsRef.current.add(fallbackKey);
          playbackStateRef.current.recordAction({
            triggerType: cue.trigger ?? 'visual',
            appliedEffect,
            requestedAction: 'blur',
            cueId: cue.id,
            start: cue.start,
            end: cue.end,
          });
        }
      }
    }
  };

  /**
   * Resets blur switching state and safely restores clean player playback.
   */
  const exitBlurToClean = () => {
    ++blurTransitionTokenRef.current;
    activeBlurSeekTokenRef.current = 0;
    if (blurSeekTimeoutRef.current) {
      clearTimeout(blurSeekTimeoutRef.current);
      blurSeekTimeoutRef.current = null;
    }
    isSeekingAlternateRef.current = false;
    blurFallbackActiveRef.current = false;

    if (isBlurActiveRef.current) {
      isBlurActiveRef.current = false;
      setIsBlurActive(false);
      const handle = alternateSurfaceHandleRef.current;
      if (handle && alternatePlayerRef.current) {
        try {
          alternatePlayerRef.current.clearSurfaceHandle(handle);
        } catch (err) {
          console.warn('[Player] Failed to clearSurfaceHandle on exitBlur:', err);
        }
      }
      alternatePlayerRef.current?.pause();
    }
  };

  /**
   * Initiates asynchronous seek on alternate blurred player under blackout mask.
   * Gated on seek completion event ('seeked') to eliminate stale frames and black flashes.
   */
  const initiateAlternateSeek = (targetTime: number) => {
    if (
      isSeekingAlternateRef.current ||
      (isBlurActiveRef.current &&
        alternatePlayerRef.current &&
        Math.abs(alternatePlayerRef.current.currentTime - targetTime) < 0.05)
    ) {
      return;
    }

    const token = ++blurTransitionTokenRef.current;
    activeBlurSeekTokenRef.current = token;

    if (blurSeekTimeoutRef.current) {
      clearTimeout(blurSeekTimeoutRef.current);
      blurSeekTimeoutRef.current = null;
    }

    if (!isAlternateAssetConfigured || alternateErrorRef.current) {
      isSeekingAlternateRef.current = false;
      isBlurActiveRef.current = false;
      setIsBlurActive(false);
      isContentHiddenRef.current = true;
      setIsContentHidden(true);
      setVisualProtectionType('blur');
      recordBlurCueAction('blacked_out', targetTime);
      return;
    }

    // 1. Blackout handover mask: shield screen while alternate player is seeking
    isSeekingAlternateRef.current = true;
    isBlurActiveRef.current = false;
    setIsBlurActive(false);
    isContentHiddenRef.current = true;
    setIsContentHidden(true);

    const timeoutMs =
      typeof blurSwitchTimeoutMs === 'number' && blurSwitchTimeoutMs > 0
        ? blurSwitchTimeoutMs
        : 500;

    // 2. Set timeout fallback
    blurSeekTimeoutRef.current = setTimeout(() => {
      if (activeBlurSeekTokenRef.current !== token) return;
      console.warn(
        `[Player] Alternate seek timed out (${timeoutMs}ms); remaining in blackout fallback`,
      );
      blurFallbackActiveRef.current = true;
      isSeekingAlternateRef.current = false;
      isBlurActiveRef.current = false;
      setIsBlurActive(false);
      isContentHiddenRef.current = true;
      setIsContentHidden(true);
      recordBlurCueAction('blacked_out', targetTime);
      applyTriggerActionsRef.current(targetTime);
    }, timeoutMs);

    // 3. Request asynchronous seek on blurred player
    if (alternatePlayerRef.current) {
      try {
        alternatePlayerRef.current.currentTime = targetTime;
      } catch (err) {
        console.warn('[Player] Failed to assign currentTime to alternate player:', err);
        if (blurSeekTimeoutRef.current) {
          clearTimeout(blurSeekTimeoutRef.current);
          blurSeekTimeoutRef.current = null;
        }
        blurFallbackActiveRef.current = true;
        isSeekingAlternateRef.current = false;
        isBlurActiveRef.current = false;
        setIsBlurActive(false);
        isContentHiddenRef.current = true;
        setIsContentHidden(true);
        recordBlurCueAction('blacked_out', targetTime);
        applyTriggerActionsRef.current(targetTime);
      }
    }
  };

  // Manage secondary VideoPlayer instance for preprocessed video blur
  useEffect(() => {
    if (!isAlternateAssetConfigured || !alternateBlurredVideoUrl) {
      return;
    }

    let isDisposed = false;
    const player = new VideoPlayer();
    alternatePlayerRef.current = player;
    alternateReadyRef.current = false;
    alternateErrorRef.current = false;

    // Alternate player must remain completely silent (clean player is master clock & audio)
    player.muted = true;
    player.volume = 0.0;
    player.src = alternateBlurredVideoUrl;

    const handleCanPlay = () => {
      if (isDisposed) return;
      alternateReadyRef.current = true;
    };

    const handleSeeked = () => {
      if (isDisposed) return;
      const token = activeBlurSeekTokenRef.current;
      if (!isSeekingAlternateRef.current) return;
      if (token !== blurTransitionTokenRef.current) return;
      if (alternateErrorRef.current) return;

      const video = videoRef.current;
      const currentT = video ? video.currentTime : 0;

      // Clear seeking state and timeout
      isSeekingAlternateRef.current = false;
      if (blurSeekTimeoutRef.current) {
        clearTimeout(blurSeekTimeoutRef.current);
        blurSeekTimeoutRef.current = null;
      }

      // Check if current playback position still requires blur
      const actions = effectiveActionsRef.current;
      const shouldBlur = actions?.visualAction === 'blur' && !actions.hide;
      if (!shouldBlur) {
        return;
      }

      // Frame is ready: attach surface and switch to blurred mode
      const handle = alternateSurfaceHandleRef.current;
      if (!handle) {
        console.warn(
          '[Player] Alternate seek completed but surface handle is not yet ready; remaining in blackout handover',
        );
        return;
      }

      try {
        player.setSurfaceHandle(handle);
      } catch (err) {
        console.warn('[Player] Failed to setSurfaceHandle on seeked:', err);
        alternateErrorRef.current = true;
        blurFallbackActiveRef.current = true;
        isBlurActiveRef.current = false;
        setIsBlurActive(false);
        recordBlurCueAction('blacked_out', currentT);
        applyTriggerActionsRef.current(currentT);
        return;
      }

      if (video && !video.paused) {
        player.muted = true;
        player.volume = 0.0;
        player.play().catch(() => {});
      }

      isBlurActiveRef.current = true;
      setIsBlurActive(true);

      // Record applied action as 'blurred'
      recordBlurCueAction('blurred', currentT);
      applyTriggerActionsRef.current(currentT);
    };

    const handleError = () => {
      if (isDisposed) return;
      console.warn('[AlternatePlayer] Error event received; engaging blackout fallback');
      alternateErrorRef.current = true;
      blurFallbackActiveRef.current = true;
      if (blurSeekTimeoutRef.current) {
        clearTimeout(blurSeekTimeoutRef.current);
        blurSeekTimeoutRef.current = null;
      }
      isSeekingAlternateRef.current = false;

      // Detach surface
      const handle = alternateSurfaceHandleRef.current;
      if (handle) {
        try {
          player.clearSurfaceHandle(handle);
        } catch (err) {
          console.warn('[Player] Failed to clearSurfaceHandle on error:', err);
        }
      }

      isBlurActiveRef.current = false;
      setIsBlurActive(false);

      const video = videoRef.current;
      const currentT = video ? video.currentTime : 0;
      recordBlurCueAction('blacked_out', currentT);
      applyTriggerActionsRef.current(currentT);
    };

    player.addEventListener('canplay', handleCanPlay);
    player.addEventListener('seeked', handleSeeked);
    player.addEventListener('error', handleError);

    player.initialize().catch((err) => {
      console.warn('[AlternatePlayer] initialize() failed:', err);
      alternateErrorRef.current = true;
    });

    return () => {
      isDisposed = true;
      if (blurSeekTimeoutRef.current) {
        clearTimeout(blurSeekTimeoutRef.current);
        blurSeekTimeoutRef.current = null;
      }
      isSeekingAlternateRef.current = false;
      player.removeEventListener('canplay', handleCanPlay);
      player.removeEventListener('seeked', handleSeeked);
      player.removeEventListener('error', handleError);
      const handle = alternateSurfaceHandleRef.current;
      if (handle) {
        player.clearSurfaceHandle(handle);
      }
      player.pause();
      player.deinitialize?.();
      alternatePlayerRef.current = null;
      alternateReadyRef.current = false;
    };
  }, [isAlternateAssetConfigured, alternateBlurredVideoUrl]);

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
    const currentCues = overrideCues ?? activeCuesRef.current;

    // Filter out skip cues whose prompts have been dismissed via Continue
    // (preserving non-skip cues and unconfirmed needs_review cues that fall back to hide/mute)
    const cuesToResolve = currentCues.filter((cue) => {
      if (!dismissedPromptCueIdsRef.current.has(cue.id)) {
        return true;
      }
      const effectiveAction = resolveEffectiveAction(
        cue.trigger,
        cue.action,
        currentOptions.actionOverrides,
      );
      if (effectiveAction !== 'skip') {
        return true;
      }
      const reviewAllowsSkip =
        cue.status !== 'needs_review' || currentOptions.confirmReviewSkip;
      if (!reviewAllowsSkip) {
        return true;
      }
      return false;
    });

    const resolveOptions = activePromptCueRef.current
      ? { ...currentOptions, skipEnabled: false }
      : currentOptions;
    const actions = playbackStateRef.current.resolveAt(time, cuesToResolve, resolveOptions);
    effectiveActionsRef.current = actions;

    applyTriggerActionsRef.current = applyTriggerActions;

    // 0. Skip forward evaluation
    if (!isSeek && actions.skipTo !== null && Number.isFinite(actions.skipTo)) {
      const maxDur = Number.isFinite(video.duration) ? video.duration : Infinity;
      const targetTime = Math.max(0, Math.min(actions.skipTo, maxDur));

      const isDismissedPromptSkip = actions.activeCueIds.some((id) =>
        dismissedPromptCueIdsRef.current.has(id),
      );

      if (targetTime > time && !activePromptCueRef.current && !isDismissedPromptSkip) {
        console.log(
          `[Trigger] Skip forward: pos=${time.toFixed(2)} → ${targetTime.toFixed(2)} ` +
            `(activeCues: [${actions.activeCueIds.join(',')}])`,
        );

        cancelVolumeRamp();
        playbackStateRef.current.seek(targetTime, currentCues, currentOptions);
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

    // 3. Visual Hiding / Visual Blur evaluation
    const isBlurCueRequested = actions.visualAction === 'blur' && currentOptions.hideEnabled;
    const canAttemptBlur =
      isBlurCueRequested &&
      !actions.hide &&
      isAlternateAssetConfigured &&
      !alternateErrorRef.current &&
      !blurFallbackActiveRef.current;

    let targetHide = actions.hide;
    let targetBlur = false;

    if (actions.hide) {
      // Hide takes absolute precedence over blur (complete blackout)
      exitBlurToClean();
      targetHide = true;
      targetBlur = false;
      const vInst = videoInstanceRef.current ?? videoRef.current;
      if (vInst && (vInst as any).style) {
        (vInst as any).style.filter = 'none';
      }
    } else if (isBlurCueRequested) {
      if (isAlternateAssetConfigured) {
        if (canAttemptBlur) {
          if (isBlurActiveRef.current && !isSeek) {
            targetHide = false;
            targetBlur = true;
          } else {
            targetHide = true;
            targetBlur = false;
            initiateAlternateSeek(time);
            if (isBlurActiveRef.current) {
              targetHide = false;
              targetBlur = true;
            }
          }
        } else {
          exitBlurToClean();
          targetHide = true;
          targetBlur = false;
          recordBlurCueAction('blacked_out', time, currentOptions, currentCues);
        }
      } else {
        exitBlurToClean();
        targetHide = true;
        targetBlur = false;
        recordBlurCueAction('blacked_out', time, currentOptions, currentCues);
      }
      const vInst = videoInstanceRef.current ?? videoRef.current;
      if (vInst && (vInst as any).style) {
        (vInst as any).style.filter = 'blur(20px)';
      }
    } else {
      // Outside visual cues
      exitBlurToClean();
      targetHide = false;
      targetBlur = false;
      const vInst = videoInstanceRef.current ?? videoRef.current;
      if (vInst && (vInst as any).style) {
        (vInst as any).style.filter = 'none';
      }
    }

    const isVegaBlurFallback = !canAttemptBlur && isBlurCueRequested;

    // Compare against current hardware/element state
    const currentMuted = video.muted;
    const currentVolume = Number(video.volume.toFixed(2));
    const currentHide = isContentHiddenRef.current;

    const muteChanged = currentMuted !== targetMuted;
    const volumeChanged = Math.abs(currentVolume - targetVolume) > 0.01;
    const hideChanged = currentHide !== targetHide;
    const blurChanged = lastBlurActiveRef.current !== isBlurActiveRef.current;
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
    }

    if (blurChanged || isBlurActiveRef.current !== targetBlur) {
      isBlurActiveRef.current = targetBlur;
      setIsBlurActive(targetBlur);
    }

    if (targetHide) {
      const activeCueList = currentCues.filter((c) =>
        actions.activeCueIds.includes(c.id),
      );
      const isBlurTrigger = activeCueList.some(
        (c) =>
          c.trigger === 'spider' ||
          c.trigger === 'flashing' ||
          c.action === 'blur' ||
          c.action === 'hide' ||
          c.source === 'visual',
      );
      const isBlurMode =
        isVegaBlurFallback ||
        (actions.visualAction === 'blur' && !actions.hide) ||
        isBlurTrigger;
      setVisualProtectionType(isBlurMode ? 'blur' : 'hide');
    } else if (targetBlur) {
      setVisualProtectionType('blur');
    } else {
      setVisualProtectionType('hide');
    }

    if (actions.visualAction === 'dim') {
      if (cuesChanged || isSeek) {
        console.warn(
          `[Trigger] Unsupported visual action "${actions.visualAction}" requested on current renderer for cue(s): [${activeCueIdsKey}]. Preserving original cue metadata without mapping to hide.`,
        );
      }
    }

    // Applied-action recording for Vega OS:
    // If honest blackout fallback is active for a blur cue, record as blacked_out.
    // (If blur succeeded, it was recorded truthfully by handleSeeked as 'blurred').
    if (isVegaBlurFallback && targetHide) {
      recordBlurCueAction('blacked_out', time, currentOptions, currentCues);
    }

    // 4. Heads-up warning evaluation (updates on cue start, cue end, and seeking)
    if (warnChanged || isSeek) {
      lastWarnCueIdsRef.current = warnCueIdsKey;
      if (actions.warnCueIds.length === 0) {
        setWarningStatus('');
      } else {
        const warnCues = currentCues.filter((c) =>
          actions.warnCueIds.includes(c.id),
        );
        const warnTriggers = warnCues
          .map((c) => c.trigger ?? c.id)
          .join(', ');
        setWarningStatus(`⚠️ Heads-Up Warning: ${warnTriggers}`);
      }
    }

    // 5. Advance heads-up prompt awareness evaluation
    let promptCue = actions.promptCue ?? null;
    if (promptCue) {
      // Filter: Prompt window is NOT displayed except for:
      // skipping a scene, complete blackout, blur, or mute/reduce.
      const trig = promptCue.trigger as TriggerType | undefined;
      const override = trig ? currentOptions.actionOverrides?.[trig] : undefined;
      const effectiveAction = override ?? promptCue.action ?? 'warn';
      const isAllowedMitigation =
        effectiveAction === 'skip' ||
        effectiveAction === 'hide' ||
        effectiveAction === 'blur' ||
        effectiveAction === 'mute' ||
        effectiveAction === 'reduce' ||
        promptCue.action === 'skip' ||
        promptCue.action === 'hide' ||
        promptCue.action === 'blur' ||
        promptCue.action === 'mute' ||
        promptCue.action === 'reduce';

      if (!isAllowedMitigation) {
        promptCue = null;
      }
    }

    if (activePromptCueRef.current) {
      const leadTime = currentOptions.headsUpPrompts?.leadTimeSeconds ?? 5.0;
      const windowStart = Math.max(0, activePromptCueRef.current.start - leadTime);

      if (!isSeek) {
        // Non-interruptive awareness notification: automatically dismisses once playhead
        // passes cue end or moves outside prompt window
        if (time >= activePromptCueRef.current.end || time < windowStart) {
          activePromptCueRef.current = null;
          setActivePromptCue(null);
        }
      } else {
        // Intentional seek operation:
        if (promptCue) {
          if (promptCue.id !== activePromptCueRef.current.id) {
            activePromptCueRef.current = promptCue;
            setActivePromptCue(promptCue);
            setFocusedPromptButton('continue');
          }
        } else {
          // If seeking strictly before the prompt lead-time window or past cue end, dismiss prompt
          if (time < windowStart || time >= activePromptCueRef.current.end) {
            activePromptCueRef.current = null;
            setActivePromptCue(null);
          }
        }
      }
    } else if (promptCue) {
      activePromptCueRef.current = promptCue;
      setActivePromptCue(promptCue);
      setFocusedPromptButton('continue');
    }

    if (cuesChanged || muteChanged || volumeChanged || hideChanged || blurChanged || warnChanged || isSeek) {
      lastActiveCuesRef.current = activeCueIdsKey;
      lastBlurActiveRef.current = isBlurActiveRef.current;

      if (actions.activeCueIds.length === 0) {
        setActiveActionStatus('');
      } else {
        const activeCueList = currentCues.filter((c) =>
          actions.activeCueIds.includes(c.id),
        );
        const triggerNames = activeCueList
          .map((c) => c.trigger ?? c.id)
          .join(', ');

        let actionDesc = '';
        if (isBlurActiveRef.current) {
          actionDesc += '🌫️ Video Blurred';
        } else if (targetHide) {
          actionDesc += '👁️ Visual Hidden';
        } else if (actions.visualAction === 'dim') {
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
      alternatePlayerRef.current?.pause();
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

    // Notify playback engine of seek (for skip-invalidation and prompt re-arming on rewind)
    const cuesToUse = activeCuesRef.current;
    playbackStateRef.current.seek(newTime, cuesToUse, viewerOptionsRef.current);
    if (newTime < currentPos) {
      blurFallbackActiveRef.current = false;
      const leadTime = viewerOptionsRef.current.headsUpPrompts?.leadTimeSeconds ?? 5.0;
      for (const cue of cuesToUse) {
        if (cue.start > newTime) {
          recordedFallbackCueIdsRef.current.delete(`blur_${cue.id}`);
          recordedFallbackCueIdsRef.current.delete(`blur_fallback_${cue.id}`);
        }
        const windowStart = Math.max(0, cue.start - leadTime);
        if (newTime < windowStart) {
          dismissedPromptCueIdsRef.current.delete(cue.id);
        }
      }
    }

    // Apply native seek
    video.currentTime = newTime;
    setCurrentTime(newTime);

    // Immediately resolve and apply trigger actions for the new seek destination
    applyTriggerActions(newTime, true, viewerOptionsRef.current, cuesToUse);
  };

  const seekTo = (targetTime: number, overrideCues?: Cue[]) => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    const currentPos = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    const maxDur = Number.isFinite(video.duration) ? video.duration : Infinity;
    const newTime = Math.max(0, Math.min(targetTime, maxDur));

    console.log(
      `[Player] seekTo: currentTime=${currentPos.toFixed(2)} → ${newTime.toFixed(2)}`,
    );

    const cuesToUse = overrideCues ?? activeCuesRef.current;

    playbackStateRef.current.seek(newTime, cuesToUse, viewerOptionsRef.current);
    if (newTime < currentPos) {
      blurFallbackActiveRef.current = false;
      const leadTime = viewerOptionsRef.current.headsUpPrompts?.leadTimeSeconds ?? 5.0;
      for (const cue of cuesToUse) {
        if (cue.start > newTime) {
          recordedFallbackCueIdsRef.current.delete(`blur_${cue.id}`);
          recordedFallbackCueIdsRef.current.delete(`blur_fallback_${cue.id}`);
        }
        const windowStart = Math.max(0, cue.start - leadTime);
        if (newTime < windowStart) {
          dismissedPromptCueIdsRef.current.delete(cue.id);
        }
      }
    }

    video.currentTime = newTime;
    setCurrentTime(newTime);
    applyTriggerActions(newTime, true, viewerOptionsRef.current, cuesToUse);
  };

  const selectHarnessScenario = (index: number) => {
    const scenario = PROMPT_HARNESS_SCENARIOS[index];
    if (!scenario) return;

    setSelectedHarnessScenarioIndex(index);
    selectedHarnessScenarioIndexRef.current = index;

    const nextCues = scenario.cues ?? [];
    activeCuesRef.current = nextCues;

    const nextOptions: ViewerOptions = {
      ...viewerOptionsRef.current,
      ...scenario.options,
      headsUpPrompts: {
        enabled: scenario.options.headsUpPrompts?.enabled ?? true,
        leadTimeSeconds:
          scenario.options.headsUpPrompts?.leadTimeSeconds ?? scenario.leadTimeSeconds,
      },
    };

    viewerOptionsRef.current = nextOptions;
    setViewerOptions(nextOptions);

    playbackStateRef.current = new PlaybackState();
    recordedFallbackCueIdsRef.current.clear();
    lastActiveCuesRef.current = '';
    lastWarnCueIdsRef.current = '';
    activePromptCueRef.current = null;
    setActivePromptCue(null);
    dismissedPromptCueIdsRef.current.clear();

    seekTo(scenario.seekBeforeWindow, nextCues);
    persistViewerOptions(nextOptions);
  };

  const resetHarnessEngineState = () => {
    playbackStateRef.current = new PlaybackState();
    recordedFallbackCueIdsRef.current.clear();
    lastActiveCuesRef.current = '';
    lastWarnCueIdsRef.current = '';
    activePromptCueRef.current = null;
    setActivePromptCue(null);
    dismissedPromptCueIdsRef.current.clear();
    const video = videoRef.current;
    const pos = video ? video.currentTime : currentTime;
    applyTriggerActions(pos, true, viewerOptionsRef.current, activeCuesRef.current);
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

  const getChainedSkipCueIds = (
    cue: Cue,
    cues: Cue[],
    options: ViewerOptions,
  ): string[] => {
    if (cue.action !== 'skip') {
      return [cue.id];
    }
    const policyCues = applyReviewPolicy(cues, options);
    const chained = new Set<string>([cue.id]);
    let destination = cue.end;
    let extended = true;
    while (extended) {
      extended = false;
      for (const other of policyCues) {
        if (
          !chained.has(other.id) &&
          other.action === 'skip' &&
          other.start <= destination &&
          other.end > destination &&
          playbackStateRef.current.isCueEligibleForSkip(other, options)
        ) {
          destination = other.end;
          chained.add(other.id);
          extended = true;
        }
      }
    }
    for (const nested of policyCues) {
      if (
        !chained.has(nested.id) &&
        nested.action === 'skip' &&
        nested.start >= cue.start &&
        nested.end <= destination &&
        playbackStateRef.current.isCueEligibleForSkip(nested, options)
      ) {
        chained.add(nested.id);
      }
    }
    return Array.from(chained);
  };

  const handlePromptContinue = () => {
    const cue = activePromptCueRef.current;
    if (!cue) {
      return;
    }
    const currentOptions = viewerOptionsRef.current;
    const currentCues = activeCuesRef.current;

    playbackStateRef.current.acknowledgePrompt(cue.id);
    dismissedPromptCueIdsRef.current.add(cue.id);

    const chainedIds = getChainedSkipCueIds(cue, currentCues, currentOptions);
    for (const id of chainedIds) {
      playbackStateRef.current.acknowledgePrompt(id);
      dismissedPromptCueIdsRef.current.add(id);
    }

    activePromptCueRef.current = null;
    setActivePromptCue(null);
    setFocusedControl('play');

    const video = videoRef.current;
    const pos = video ? video.currentTime : currentTime;
    applyTriggerActions(pos, false, currentOptions, currentCues);
  };
  handlePromptContinueRef.current = handlePromptContinue;

  const handlePromptSkip = () => {
    const cue = activePromptCueRef.current;
    if (!cue) {
      return;
    }
    const video = videoRef.current;
    const mediaDuration =
      video && Number.isFinite(video.duration) && video.duration > 0
        ? video.duration
        : Number.isFinite(duration) && duration > 0
        ? duration
        : undefined;

    playbackStateRef.current.acknowledgePrompt(cue.id);
    activePromptCueRef.current = null;
    setActivePromptCue(null);
    setFocusedControl('play');

    const targetTime = playbackStateRef.current.handlePromptSkip(
      cue,
      activeCuesRef.current,
      mediaDuration,
      viewerOptionsRef.current,
    );

    if (video) {
      if (targetTime > video.currentTime) {
        cancelVolumeRamp();
        video.currentTime = targetTime;
        setCurrentTime(targetTime);
        applyTriggerActions(targetTime, true, viewerOptionsRef.current, activeCuesRef.current);
      } else {
        applyTriggerActions(video.currentTime, true, viewerOptionsRef.current, activeCuesRef.current);
      }
    }
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
      const pos = video ? video.currentTime : currentTime;
      applyTriggerActions(pos, true, next);
      if (!next.headsUpPrompts?.enabled) {
        activePromptCueRef.current = null;
        setActivePromptCue(null);
      }
      persistViewerOptions(next);
      return next;
    });
  };

  // Restore saved viewer preferences & profile from AsyncStorage on mount
  useEffect(() => {
    isMountedRef.current = true;

    (async () => {
      try {
        const [savedSettings, savedProfile] = await Promise.all([
          getItem<unknown>(STORAGE_KEYS.USER_SETTINGS),
          getItem<unknown>(STORAGE_KEYS.USER_PROFILE),
        ]);

        if (!isMountedRef.current) return;

        // 1. Viewer options restoration
        if (
          !hasUserModifiedOptionsRef.current &&
          savedSettings !== null &&
          savedSettings !== undefined
        ) {
          const merged = sanitizeViewerOptions(savedSettings, viewerOptionsRef.current);
          setViewerOptions(merged);
          viewerOptionsRef.current = merged;

          const video = videoRef.current;
          if (video) {
            applyTriggerActions(video.currentTime, true, merged);
          }
        }

        // 2. Viewer profile restoration (guarded against explicit initialProfile and user modification)
        if (
          !hasExplicitInitialProfileRef.current &&
          !hasUserModifiedProfileRef.current &&
          isValidPlayerProfile(savedProfile)
        ) {
          const restoredProfile: PlayerProfile = savedProfile;
          setProfile(restoredProfile);
          profileRef.current = restoredProfile;

          const nextCues = computeActiveCues(
            isDevHarnessOpenRef.current,
            selectedHarnessScenarioIndexRef.current,
            restoredProfile,
          );
          activeCuesRef.current = nextCues;

          const video = videoRef.current;
          if (video) {
            applyTriggerActions(video.currentTime, true, viewerOptionsRef.current, nextCues);
          }
        }
      } catch (e) {
        console.error('Failed to restore viewer preferences from storage', e);
      }
    })();

    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const handleFilmSwitch = (newFilmId: string) => {
    console.log(`[Player] Switching film from ${currentFilmId} to ${newFilmId}`);
    cancelVolumeRamp();
    playbackStateRef.current = new PlaybackState();
    recordedFallbackCueIdsRef.current.clear();
    lastActiveCuesRef.current = '';
    lastWarnCueIdsRef.current = '';
    activePromptCueRef.current = null;
    setActivePromptCue(null);
    dismissedPromptCueIdsRef.current.clear();
    setActiveActionStatus('');
    setWarningStatus('');
    setCurrentTime(0);
    setIsPlaying(false);
    setIsContentHidden(false);
    isContentHiddenRef.current = false;
    effectiveActionsRef.current = null;
    lastLoggedSecond.current = -1;
    setBotheredFeedback('');
    if (botheredFeedbackTimerRef.current !== null) {
      clearTimeout(botheredFeedbackTimerRef.current);
      botheredFeedbackTimerRef.current = null;
    }
    if (blurSeekTimeoutRef.current !== null) {
      clearTimeout(blurSeekTimeoutRef.current);
      blurSeekTimeoutRef.current = null;
    }
    isBlurActiveRef.current = false;
    setIsBlurActive(false);

    setCurrentFilmId(newFilmId);

    const video = videoRef.current;
    if (video) {
      try {
        video.currentTime = 0;
        video.pause();
        video.muted = userMutedRef.current;
        video.volume = userVolumeRef.current;
      } catch {
        // ignore in test environments
      }
    }
  };

  const toggleFilm = () => {
    const nextFilmId =
      currentFilmId === 'sintel'
        ? 'tears_of_steel'
        : currentFilmId === 'tears_of_steel'
        ? 'elephants_dream'
        : 'sintel';
    handleFilmSwitch(nextFilmId);
  };

  const getNextProfile = (current: PlayerProfile): PlayerProfile => {
    switch (current) {
      case 'standard':
        return 'verification';
      case 'verification':
        return 'skip_verification';
      case 'skip_verification':
      default:
        return 'standard';
    }
  };

  const toggleProfile = () => {
    hasUserModifiedProfileRef.current = true;
    const nextProfile: PlayerProfile = getNextProfile(profileRef.current);
    profileRef.current = nextProfile;
    setProfile(nextProfile);
    lastActiveCuesRef.current = '';
    lastWarnCueIdsRef.current = '';
    setWarningStatus('');
    cancelVolumeRamp();
    playbackStateRef.current.reset();
    recordedFallbackCueIdsRef.current.clear();
    console.log(`[Player] Switched trigger profile to: ${nextProfile}`);

    const nextCues = computeActiveCues(
      isDevHarnessOpenRef.current,
      selectedHarnessScenarioIndexRef.current,
      nextProfile,
    );
    activeCuesRef.current = nextCues;

    // Re-evaluate actions immediately with new cues
    const video = videoRef.current;
    if (video) {
      applyTriggerActions(video.currentTime, true, viewerOptionsRef.current, nextCues);
    }

    setItem(STORAGE_KEYS.USER_PROFILE, nextProfile).catch((err) => {
      console.error('[PlayerScreen] Failed to persist profile to storage', err);
    });
  };

  const calculateSeekTimeFromX = (locX: number): number => {
    const width =
      seekBarWidthRef.current > 0
        ? seekBarWidthRef.current
        : videoDimensions.width > 0
        ? videoDimensions.width
        : 1824;
    const dur =
      Number.isFinite(duration) && duration > 0
        ? duration
        : Number.isFinite(videoRef.current?.duration) && (videoRef.current?.duration ?? 0) > 0
        ? (videoRef.current?.duration as number)
        : 0;
    if (width <= 0 || !Number.isFinite(dur) || dur <= 0) {
      return 0;
    }
    const clampedX = Math.max(0, Math.min(locX, width));
    return Number(((clampedX / width) * dur).toFixed(2));
  };

  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onStartShouldSetPanResponderCapture: () => false,
      onMoveShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponderCapture: () => false,
      onPanResponderGrant: (evt) => {
        const width =
          seekBarWidthRef.current > 0
            ? seekBarWidthRef.current
            : videoDimensions.width > 0
            ? videoDimensions.width
            : 1824;
        const locX = evt.nativeEvent?.locationX ?? 0;
        const target = calculateSeekTimeFromX(locX);
        dragStartXRef.current = Math.max(0, Math.min(locX, width));
        dragSeekTimeRef.current = target;
        isDraggingSeekRef.current = true;
        setIsDraggingSeek(true);
        setDragSeekTime(target);
      },
      onPanResponderMove: (_evt, gestureState) => {
        const width =
          seekBarWidthRef.current > 0
            ? seekBarWidthRef.current
            : videoDimensions.width > 0
            ? videoDimensions.width
            : 1824;
        if (isDraggingSeekRef.current) {
          const currentX = Math.max(0, Math.min(dragStartXRef.current + gestureState.dx, width));
          const target = calculateSeekTimeFromX(currentX);
          dragSeekTimeRef.current = target;
          setDragSeekTime(target);
        }
      },
      onPanResponderRelease: () => {
        if (isDraggingSeekRef.current) {
          const finalTarget = dragSeekTimeRef.current;
          isDraggingSeekRef.current = false;
          dragSeekTimeRef.current = null;
          setIsDraggingSeek(false);
          setDragSeekTime(null);
          if (finalTarget !== null && Number.isFinite(finalTarget)) {
            seekTo(finalTarget);
          }
        }
      },
      onPanResponderTerminate: () => {
        isDraggingSeekRef.current = false;
        dragSeekTimeRef.current = null;
        setIsDraggingSeek(false);
        setDragSeekTime(null);
      },
    }),
  ).current;

  const handleSeekBarPress = (evt: any) => {
    const locX =
      evt.nativeEvent?.locationX ??
      evt.nativeEvent?.positionX ??
      (typeof evt.nativeEvent?.locationX === 'number' ? evt.nativeEvent.locationX : null);
    if (locX !== null && Number.isFinite(locX)) {
      const target = calculateSeekTimeFromX(locX);
      seekTo(target);
    }
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
        <Text style={styles.title} testID="player-title">
          <Text>Trigger-Aware Player</Text>
          {filmMetadata ? ' — ' : ''}
          {filmMetadata ? filmMetadata.title : ''}
        </Text>
        <Text style={styles.profileBadge} testID="player-profile-badge">
          {`Profile: ${
            profile === 'verification'
              ? 'Verification Cues'
              : profile === 'skip_verification'
              ? 'Skip Verification'
              : `${filmMetadata?.title ?? currentFilmId} Index`
          }`}
        </Text>
      </View>

      {indexLoadError && (
        <View style={styles.indexErrorBanner} testID="index-error-banner" accessibilityRole="alert">
          <Text style={styles.indexErrorTitle}>⚠️ Index Load Error</Text>
          <Text style={styles.indexErrorText} testID="index-error-text">{indexLoadError}</Text>
        </View>
      )}

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
          style={[
            styles.video,
            isContentHidden && visualProtectionType !== 'blur' && styles.videoHidden,
            isBlurActive && isAlternateAssetConfigured && styles.videoHidden,
          ]}
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
            if (isBlurActiveRef.current && alternatePlayerRef.current) {
              alternatePlayerRef.current.play().catch(() => {});
            }
          }}
          onPause={() => {
            console.log('[Player] onPause');
            setIsPlaying(false);
            setStatus('Paused');
            alternatePlayerRef.current?.pause();
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
            alternatePlayerRef.current?.pause();
            setShowRecap(true);
          }}
          onError={() => {
            const video = videoRef.current;
            const err = video?.error;
            const code = err?.code ?? 'unknown';
            const msg = err?.message ?? 'no message';
            console.log(`[Player] onError — code: ${code} message: ${msg} src: ${videoSource}`);
            const fallbackUrl = filmMetadata?.fallback_video_url;
            if (fallbackUrl && videoSource !== fallbackUrl && !videoSourceOverride) {
              console.log(`[Player] Primary source failed for "${currentFilmId}" (${code}: ${msg}); attempting fallback stream: ${fallbackUrl}`);
              setStatus('Retrying with remote stream…');
              setVideoSourceOverride(fallbackUrl);
              return;
            }
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

        {isAlternateAssetConfigured && (
          <KeplerShareableSurfaceView
            testID="blurred-shareable-surface"
            style={[
              styles.video,
              styles.alternateVideo,
              !isBlurActive && styles.videoHidden,
            ]}
            scalingmode="fit"
            onSurfaceViewCreated={(handleOrEvent: any) => {
              const handle =
                typeof handleOrEvent === 'string'
                  ? handleOrEvent
                  : handleOrEvent?.nativeEvent?.surfaceHandle ??
                    handleOrEvent?.surfaceHandle ??
                    null;
              alternateSurfaceHandleRef.current = handle;
              if (handle && alternatePlayerRef.current) {
                const video = videoRef.current;
                const currentT = video ? video.currentTime : 0;
                const actions = effectiveActionsRef.current;
                const shouldBlur = actions?.visualAction === 'blur' && !actions.hide;

                if (
                  shouldBlur &&
                  !isSeekingAlternateRef.current &&
                  !alternateErrorRef.current &&
                  !blurFallbackActiveRef.current
                ) {
                  try {
                    alternatePlayerRef.current.setSurfaceHandle(handle);
                    if (video && !video.paused) {
                      alternatePlayerRef.current.muted = true;
                      alternatePlayerRef.current.volume = 0.0;
                      alternatePlayerRef.current.play().catch(() => {});
                    }
                    isBlurActiveRef.current = true;
                    setIsBlurActive(true);
                    recordBlurCueAction('blurred', currentT);
                    applyTriggerActionsRef.current(currentT);
                  } catch (err) {
                    console.warn('[Player] Failed to attach surface on creation:', err);
                  }
                }
              }
            }}
            onSurfaceViewDestroyed={() => {
              const handle = alternateSurfaceHandleRef.current;
              if (handle && alternatePlayerRef.current) {
                try {
                  alternatePlayerRef.current.clearSurfaceHandle(handle);
                } catch (err) {
                  console.warn('[Player] Failed to clearSurfaceHandle on destroy:', err);
                }
              }
              alternateSurfaceHandleRef.current = null;
            }}
          />
        )}

        {/* Fallback visual overlay rendered when visual hiding or blur fallback is triggered */}
        {isContentHidden && (
          <View
            style={
              visualProtectionType === 'blur'
                ? styles.blurFallbackOverlay
                : styles.hiddenOverlay
            }
            testID="trigger-hidden-overlay"
            accessibilityLabel={
              visualProtectionType === 'blur'
                ? 'Visual blur protection active for sensitive visual trigger'
                : 'Visual content hidden due to trigger-aware filter'
            }>
            {visualProtectionType === 'blur' ? (
              <View style={styles.blurCard} testID="blur-protection-card">
                <Text style={styles.blurOverlayIcon}>🌫️</Text>
                <Text style={styles.blurOverlayTitle}>Visual Blur Active</Text>
                <Text style={styles.blurOverlaySubtitle}>
                  {(() => {
                    const currentTriggers = activeCues
                      .filter(
                        (c) =>
                          c.source === 'visual' &&
                          currentTime >= c.start &&
                          currentTime < c.end,
                      )
                      .map((c) => c.trigger)
                      .filter((t): t is string => typeof t === 'string' && t.length > 0);
                    return currentTriggers.length > 0
                      ? `Frosted blur protection active (${currentTriggers.join(', ')})`
                      : 'Frosted blur protection active for sensitive scene';
                  })()}
                </Text>
                <View style={styles.blurBadge}>
                  <Text style={styles.blurBadgeText}>BLUR PROTECTION</Text>
                </View>
              </View>
            ) : (
              <>
                <Text style={styles.hiddenOverlayIcon}>🛡️</Text>
                <Text style={styles.hiddenOverlayTitle}>Visual Content Hidden</Text>
                <Text style={styles.hiddenOverlaySubtitle}>
                  Protection active for sensitive visual trigger
                </Text>
              </>
            )}
          </View>
        )}

        {/* Film-specific subtitle captions with profanity masking */}
        <CaptionRenderer
          currentTime={currentTime}
          userSettings={{
            cues: filmSubtitles,
            engineCues: standardIndexCues,
            viewerOptions,
          }}
        />
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

      {/* Playback status and time display */}
      <Text style={styles.status}>{status}</Text>
      {(() => {
        const displayedTime =
          isDraggingSeek && dragSeekTime !== null ? dragSeekTime : currentTime;
        const effectiveDuration =
          Number.isFinite(duration) && duration > 0
            ? duration
            : Number.isFinite(videoRef.current?.duration) && (videoRef.current?.duration ?? 0) > 0
            ? (videoRef.current?.duration as number)
            : 0;
        const progressPercent =
          effectiveDuration > 0
            ? Math.max(0, Math.min(100, (displayedTime / effectiveDuration) * 100))
            : 0;

        return (
          <>
            <View style={styles.timeRow} testID="playback-time-container">
              <Text style={styles.time} testID="playback-current-time">
                {formatTime(displayedTime, duration)} / {formatTime(duration, duration)}
              </Text>
              {isDraggingSeek && (
                <Text style={styles.seekingBadge} testID="seeking-badge">
                  Scrubbing to {formatTime(displayedTime, duration)}
                </Text>
              )}
            </View>

            {/* Interactive Playback Timeline / Seek Bar */}
            <View
              style={styles.seekBarContainer}
              testID="seek-bar-container"
              onLayout={(e) => {
                const w = Math.round(e.nativeEvent.layout.width);
                if (w > 0) {
                  seekBarWidthRef.current = w;
                }
              }}
              {...panResponder.panHandlers}>
              <TouchableOpacity
                activeOpacity={0.9}
                style={[
                  styles.seekBarFocusable,
                  isSeekBarFocused && styles.seekBarFocused,
                ]}
                testID="seek-bar"
                accessibilityRole="adjustable"
                accessibilityLabel="Playback progress seek bar"
                accessibilityValue={{
                  min: 0,
                  max: Math.round(effectiveDuration),
                  now: Math.round(displayedTime),
                }}
                focusable={!isSettingsOpen}
                onFocus={() => {
                  setIsSeekBarFocused(true);
                  setFocusedControl('seek-bar');
                }}
                onBlur={() => {
                  setIsSeekBarFocused(false);
                  if (focusedControl === 'seek-bar') {
                    setFocusedControl('');
                  }
                }}
                onPress={handleSeekBarPress}
                onAccessibilityAction={(event) => {
                  if (event.nativeEvent.actionName === 'increment') {
                    seekBy(10);
                  } else if (event.nativeEvent.actionName === 'decrement') {
                    seekBy(-10);
                  }
                }}>
                <View style={styles.seekBarTrack} testID="seek-bar-track">
                  <View
                    style={[styles.seekBarProgress, {width: `${progressPercent}%`}]}
                    testID="seek-bar-progress"
                  />
                  <View
                    style={[
                      styles.seekBarThumb,
                      {left: `${progressPercent}%`},
                      isDraggingSeek && styles.seekBarThumbDragging,
                      isSeekBarFocused && styles.seekBarThumbFocused,
                    ]}
                    testID="seek-bar-thumb"
                  />
                </View>
              </TouchableOpacity>
            </View>
          </>
        );
      })()}

      {/* Developer prompt test harness panel */}
      {isDevHarnessOpen && (
        <View style={styles.harnessPanel} testID="prompt-harness-panel">
          <View style={styles.harnessHeader}>
            <View style={styles.harnessHeaderLeft}>
              <Text style={styles.harnessBadge}>DEV HARNESS</Text>
              <Text style={styles.harnessTitle}>Heads-Up Prompt Runtime Harness</Text>
            </View>
            <TouchableOpacity
              style={styles.harnessCloseButton}
              testID="harness-close"
              onPress={() => {
                setIsDevHarnessOpen(false);
                isDevHarnessOpenRef.current = false;
                const nextCues = computeActiveCues(
                  false,
                  selectedHarnessScenarioIndexRef.current,
                  profile,
                );
                activeCuesRef.current = nextCues;
                playbackStateRef.current = new PlaybackState();
                recordedFallbackCueIdsRef.current.clear();
                lastActiveCuesRef.current = '';
                lastWarnCueIdsRef.current = '';
                activePromptCueRef.current = null;
                setActivePromptCue(null);
                const video = videoRef.current;
                const pos = video ? video.currentTime : currentTime;
                applyTriggerActions(pos, true, viewerOptionsRef.current, nextCues);
              }}
              focusable={!isSettingsOpen}
              accessibilityRole="button"
              accessibilityLabel="Close prompt test harness">
              <Text style={styles.harnessCloseText}>✕ Exit Harness</Text>
            </TouchableOpacity>
          </View>

          {/* Scenario selector tabs */}
          <View style={styles.harnessTabRow}>
            {PROMPT_HARNESS_SCENARIOS.map((s, idx) => {
              const isSelected = selectedHarnessScenarioIndex === idx;
              const isFocused = focusedHarnessItem === `harness-scenario-${idx}`;
              return (
                <TouchableOpacity
                  key={s.id}
                  testID={`harness-scenario-${idx}`}
                  style={[
                    styles.harnessTab,
                    isSelected && styles.harnessTabSelected,
                    isFocused && styles.harnessTabFocused,
                  ]}
                  onFocus={() => setFocusedHarnessItem(`harness-scenario-${idx}`)}
                  onPress={() => selectHarnessScenario(idx)}
                  focusable={!isSettingsOpen}
                  accessibilityRole="tab"
                  accessibilityState={{ selected: isSelected }}
                  accessibilityLabel={s.name}>
                  <Text
                    style={[
                      styles.harnessTabText,
                      isSelected && styles.harnessTabTextSelected,
                    ]}>
                    {s.name}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          {/* Scenario metadata & cue status card */}
          {(() => {
            const currentScenario =
              PROMPT_HARNESS_SCENARIOS[selectedHarnessScenarioIndex] ??
              PROMPT_HARNESS_SCENARIOS[0];
            const primaryCue = currentScenario.cues[0];
            const windowStart = primaryCue
              ? Math.max(0, primaryCue.start - currentScenario.leadTimeSeconds)
              : 0;
            const windowEnd = primaryCue ? primaryCue.start : 0;
            return (
              <View style={styles.harnessInfoCard}>
                <Text style={styles.harnessInfoDesc}>{currentScenario.description}</Text>
                <View style={styles.harnessMetaRow}>
                  <Text style={styles.harnessMetaText}>
                    Cues: [{currentScenario.cues.map((c) => `${c.start.toFixed(1)}s-${c.end.toFixed(1)}s (${c.action})`).join(', ')}]
                  </Text>
                  <Text style={styles.harnessMetaText}>
                    Status: {primaryCue?.status ?? 'none'}
                  </Text>
                  <Text style={styles.harnessMetaText}>
                    Effective Action: {currentScenario.expectedEffectiveAction}
                  </Text>
                  <Text style={styles.harnessMetaText}>
                    Lead Time: {currentScenario.leadTimeSeconds.toFixed(1)}s
                  </Text>
                  <Text style={styles.harnessMetaText}>
                    Prompt Window: [{windowStart.toFixed(1)}s, {windowEnd.toFixed(1)}s)
                  </Text>
                  <Text style={styles.harnessMetaText}>
                    Skip Button: {currentScenario.expectedSkipButton ? 'Visible' : 'Hidden'}
                  </Text>
                </View>
              </View>
            );
          })()}

          {/* Direct seek & lifecycle controls */}
          {(() => {
            const currentScenario =
              PROMPT_HARNESS_SCENARIOS[selectedHarnessScenarioIndex] ??
              PROMPT_HARNESS_SCENARIOS[0];
            return (
              <View style={styles.harnessActionsRow}>
                <TouchableOpacity
                  style={[
                    styles.harnessActionButton,
                    focusedHarnessItem === 'harness-seek-before' && styles.harnessButtonFocused,
                  ]}
                  testID="harness-seek-before"
                  onFocus={() => setFocusedHarnessItem('harness-seek-before')}
                  onPress={() => seekTo(currentScenario.seekBeforeWindow)}
                  focusable={!isSettingsOpen}
                  accessibilityRole="button"
                  accessibilityLabel={`Seek before window (${currentScenario.seekBeforeWindow}s)`}>
                  <Text style={styles.harnessActionText}>
                    ⏪ Seek Before ({currentScenario.seekBeforeWindow.toFixed(1)}s)
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity
                  style={[
                    styles.harnessActionButton,
                    styles.harnessActionPrimary,
                    focusedHarnessItem === 'harness-seek-window' && styles.harnessButtonFocused,
                  ]}
                  testID="harness-seek-window"
                  onFocus={() => setFocusedHarnessItem('harness-seek-window')}
                  onPress={() => seekTo(currentScenario.seekInWindow)}
                  focusable={!isSettingsOpen}
                  accessibilityRole="button"
                  accessibilityLabel={`Seek into prompt window (${currentScenario.seekInWindow}s)`}>
                  <Text
                    style={[
                      styles.harnessActionText,
                      styles.harnessActionPrimaryText,
                    ]}>
                    🎯 Seek Into Window ({currentScenario.seekInWindow.toFixed(1)}s)
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity
                  style={[
                    styles.harnessActionButton,
                    focusedHarnessItem === 'harness-seek-cue' && styles.harnessButtonFocused,
                  ]}
                  testID="harness-seek-cue"
                  onFocus={() => setFocusedHarnessItem('harness-seek-cue')}
                  onPress={() => seekTo(currentScenario.seekCueStart)}
                  focusable={!isSettingsOpen}
                  accessibilityRole="button"
                  accessibilityLabel={`Seek cue start (${currentScenario.seekCueStart}s)`}>
                  <Text style={styles.harnessActionText}>
                    ⏩ Seek Cue Start ({currentScenario.seekCueStart.toFixed(1)}s)
                  </Text>
                </TouchableOpacity>

                <TouchableOpacity
                  style={[
                    styles.harnessActionButton,
                    focusedHarnessItem === 'harness-reset-state' && styles.harnessButtonFocused,
                  ]}
                  testID="harness-reset-state"
                  onFocus={() => setFocusedHarnessItem('harness-reset-state')}
                  onPress={resetHarnessEngineState}
                  focusable={!isSettingsOpen}
                  accessibilityRole="button"
                  accessibilityLabel="Reset Engine State">
                  <Text style={styles.harnessActionText}>🔄 Reset State</Text>
                </TouchableOpacity>
              </View>
            );
          })()}
        </View>
      )}

      <TVFocusGuideView style={styles.controls} autoFocus>
        <Control
          id="back"
          testID="control-back-10s"
          label="Back 10s"
          onPress={() => seekBy(-10)}
          isFocused={focusedControl === 'back'}
          onFocus={() => setFocusedControl('back')}
          focusable={!isSettingsOpen}
        />
        <Control
          id="play"
          testID="control-play-pause"
          label={isPlaying ? 'Pause' : 'Play'}
          onPress={togglePlayback}
          isFocused={focusedControl === 'play'}
          onFocus={() => setFocusedControl('play')}
          preferred={!isSettingsOpen}
          focusable={!isSettingsOpen}
        />
        <Control
          id="forward"
          testID="control-forward-10s"
          label="Forward 10s"
          onPress={() => seekBy(10)}
          isFocused={focusedControl === 'forward'}
          onFocus={() => setFocusedControl('forward')}
          focusable={!isSettingsOpen}
        />
        <Control
          id="mute"
          testID="control-mute"
          label={isMuted ? 'Unmute' : 'Mute'}
          onPress={toggleMute}
          isFocused={focusedControl === 'mute'}
          onFocus={() => setFocusedControl('mute')}
          focusable={!isSettingsOpen}
        />
        <Control
          id="film"
          testID="control-film"
          label={filmMetadata ? `Film: ${filmMetadata.title}` : `Film: ${currentFilmId}`}
          onPress={toggleFilm}
          isFocused={focusedControl === 'film'}
          onFocus={() => setFocusedControl('film')}
          focusable={!isSettingsOpen}
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
          onBlur={() => {
            if (focusedControl === 'bothered') {
              setFocusedControl('');
            }
          }}
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

      {/* Heads-up advance prompt awareness notification */}
      {activePromptCue && !showRecap && !isSettingsOpen && (
        <View style={styles.promptOverlay} testID="heads-up-prompt-overlay" pointerEvents="box-none">
          <TVFocusGuideView
            style={styles.promptContainer}
            testID="heads-up-prompt-dialog">
            <View style={styles.promptHeaderRow}>
              <Text style={styles.promptHeaderBadge}>⚡ INCOMING TRIGGER</Text>
            </View>
            <Text style={styles.promptTitle}>Upcoming Sensitive Content</Text>
            <Text style={styles.promptMessage}>
              {(() => {
                const cueTrig = activePromptCue.trigger as TriggerType | undefined;
                const effAction =
                  (cueTrig ? viewerOptions.actionOverrides?.[cueTrig] : undefined) ??
                  activePromptCue.action ??
                  'mitigation';
                const actionText =
                  effAction === 'skip'
                    ? 'Scene will be skipped automatically.'
                    : effAction === 'blur'
                    ? 'Scene will be blurred automatically.'
                    : effAction === 'hide'
                    ? 'Complete blackout will apply automatically.'
                    : effAction === 'mute'
                    ? 'Audio will be muted automatically.'
                    : effAction === 'reduce'
                    ? 'Audio volume will be reduced automatically.'
                    : 'Protection will apply automatically.';
                return `A ${activePromptCue.trigger ?? 'content'} trigger is upcoming at ${formatTime(activePromptCue.start)}. ${actionText}`;
              })()}
            </Text>
            <View style={styles.promptButtonGroup}>
              <TouchableOpacity
                style={[
                  styles.promptButton,
                  promptButtonToFocus === 'continue' && styles.promptButtonFocused,
                ]}
                testID="prompt-continue"
                hasTVPreferredFocus={promptButtonToFocus === 'continue'}
                onFocus={() => setFocusedPromptButton('continue')}
                onPress={handlePromptContinue}
                accessibilityRole="button"
                accessibilityLabel="Continue watching"
                activeOpacity={0.8}>
                <Text style={styles.promptButtonText}>Continue</Text>
              </TouchableOpacity>
              {isSkipEligible && (
                <TouchableOpacity
                  style={[
                    styles.promptButton,
                    styles.promptSkipButton,
                    promptButtonToFocus === 'skip' && styles.promptButtonFocused,
                  ]}
                  testID="prompt-skip"
                  hasTVPreferredFocus={promptButtonToFocus === 'skip'}
                  onFocus={() => setFocusedPromptButton('skip')}
                  onPress={handlePromptSkip}
                  accessibilityRole="button"
                  accessibilityLabel="Skip Scene"
                  activeOpacity={0.8}>
                  <Text style={styles.promptButtonText}>Skip Scene</Text>
                </TouchableOpacity>
              )}
            </View>
          </TVFocusGuideView>
        </View>
      )}

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
              <DrawerToggleItem
                testID="toggle-heads-up-prompts"
                label="Heads-Up Warning Prompts"
                enabled={viewerOptions.headsUpPrompts?.enabled ?? false}
                isFocused={focusedDrawerItem === 'toggle-heads-up'}
                onFocus={() => setFocusedDrawerItem('toggle-heads-up')}
                onPress={() =>
                  updateViewerOptions((prev) => ({
                    ...prev,
                    headsUpPrompts: {
                      enabled: !(prev.headsUpPrompts?.enabled ?? false),
                      leadTimeSeconds: prev.headsUpPrompts?.leadTimeSeconds ?? 5.0,
                    },
                  }))
                }
              />
              <Text style={styles.drawerSectionHeader}>DEVELOPER TESTING</Text>
              <DrawerToggleItem
                testID="toggle-prompt-harness"
                label="Prompt Test Harness"
                enabled={isDevHarnessOpen}
                isFocused={focusedDrawerItem === 'toggle-harness'}
                onFocus={() => setFocusedDrawerItem('toggle-harness')}
                onPress={() => {
                  const nextHarnessOpen = !isDevHarnessOpen;
                  setIsDevHarnessOpen(nextHarnessOpen);
                  isDevHarnessOpenRef.current = nextHarnessOpen;

                  const nextCues = computeActiveCues(
                    nextHarnessOpen,
                    selectedHarnessScenarioIndexRef.current,
                    profile,
                  );
                  activeCuesRef.current = nextCues;

                  playbackStateRef.current = new PlaybackState();
                  recordedFallbackCueIdsRef.current.clear();
                  lastActiveCuesRef.current = '';
                  lastWarnCueIdsRef.current = '';
                  activePromptCueRef.current = null;
                  setActivePromptCue(null);

                  if (nextHarnessOpen) {
                    const scenario =
                      PROMPT_HARNESS_SCENARIOS[selectedHarnessScenarioIndexRef.current];
                    if (scenario) {
                      const nextOptions: ViewerOptions = {
                        ...viewerOptionsRef.current,
                        ...scenario.options,
                        headsUpPrompts: {
                          enabled: scenario.options.headsUpPrompts?.enabled ?? true,
                          leadTimeSeconds:
                            scenario.options.headsUpPrompts?.leadTimeSeconds ??
                            scenario.leadTimeSeconds,
                        },
                      };
                      viewerOptionsRef.current = nextOptions;
                      setViewerOptions(nextOptions);

                      const video = videoRef.current;
                      const pos = video ? video.currentTime : currentTime;
                      applyTriggerActions(pos, true, nextOptions, nextCues);
                      persistViewerOptions(nextOptions);
                    }
                  } else {
                    const video = videoRef.current;
                    const pos = video ? video.currentTime : currentTime;
                    applyTriggerActions(pos, true, viewerOptionsRef.current, nextCues);
                  }
                }}
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
            playbackStateRef.current.reset();
            recordedFallbackCueIdsRef.current.clear();
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
  id,
  label,
  onPress,
  isFocused,
  onFocus,
  onBlur,
  preferred = false,
  testID,
  accessibilityLabel,
  focusable = true,
}: ControlProps) => {
  const isBothered = id === 'bothered';
  return (
    <TouchableOpacity
      style={[
        styles.control,
        isBothered && styles.botheredControl,
        preferred && styles.preferredControl,
        isFocused &&
          (isBothered
            ? styles.botheredControlFocused
            : styles.focusedControl),
      ]}
      onPress={onPress}
      onFocus={onFocus}
      onBlur={onBlur}
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      isTVSelectable={true}
      hasTVPreferredFocus={preferred}
      focusable={focusable}
      activeOpacity={0.8}>
      <Text
        style={[
          styles.controlText,
          isBothered && styles.botheredControlText,
          isFocused &&
            (isBothered
              ? styles.botheredControlFocusedText
              : styles.focusedControlText),
        ]}>
        {label}
      </Text>
    </TouchableOpacity>
  );
};

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
  indexErrorBanner: {
    backgroundColor: 'rgba(220, 38, 38, 0.95)',
    borderRadius: 8,
    paddingVertical: 14,
    paddingHorizontal: 20,
    marginBottom: 16,
    borderLeftWidth: 6,
    borderLeftColor: '#EF4444',
  },
  indexErrorTitle: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: 'bold',
    marginBottom: 4,
  },
  indexErrorText: {
    color: '#FEE2E2',
    fontSize: 14,
    lineHeight: 20,
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
  alternateVideo: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 1,
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
  blurFallbackOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(15, 23, 42, 0.35)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
    zIndex: 10,
  },
  blurCard: {
    backgroundColor: '#1E293B',
    borderWidth: 2,
    borderColor: '#38BDF8',
    borderRadius: 16,
    paddingVertical: 24,
    paddingHorizontal: 40,
    alignItems: 'center',
    justifyContent: 'center',
    maxWidth: 600,
  },
  blurOverlayIcon: {
    fontSize: 48,
    marginBottom: 8,
  },
  blurOverlayTitle: {
    color: '#F8FAFC',
    fontSize: 24,
    fontWeight: '700',
    textAlign: 'center',
    letterSpacing: 0.5,
  },
  blurOverlaySubtitle: {
    color: '#94A3B8',
    fontSize: 15,
    marginTop: 6,
    textAlign: 'center',
  },
  blurBadge: {
    marginTop: 14,
    backgroundColor: 'rgba(56, 189, 248, 0.18)',
    borderColor: 'rgba(56, 189, 248, 0.5)',
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 4,
    paddingHorizontal: 12,
  },
  blurBadgeText: {
    color: '#38BDF8',
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 1,
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
  timeRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 4,
  },
  seekingBadge: {
    color: '#38BDF8',
    fontSize: 16,
    fontWeight: '700',
    backgroundColor: 'rgba(56, 189, 248, 0.15)',
    paddingVertical: 2,
    paddingHorizontal: 10,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: 'rgba(56, 189, 248, 0.4)',
  },
  seekBarContainer: {
    width: '100%',
    paddingVertical: 8,
    marginTop: 6,
  },
  seekBarFocusable: {
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 2,
  },
  seekBarFocused: {
    backgroundColor: 'rgba(56, 189, 248, 0.12)',
    borderRadius: 8,
  },
  seekBarTrack: {
    height: 8,
    backgroundColor: '#334155',
    borderRadius: 4,
    overflow: 'visible',
    position: 'relative',
    justifyContent: 'center',
  },
  seekBarProgress: {
    height: '100%',
    backgroundColor: '#38BDF8',
    borderRadius: 4,
  },
  seekBarThumb: {
    position: 'absolute',
    top: -6,
    width: 20,
    height: 20,
    borderRadius: 10,
    backgroundColor: '#FFFFFF',
    borderWidth: 2,
    borderColor: '#38BDF8',
    marginLeft: -10,
    shadowColor: '#000000',
    shadowOffset: {width: 0, height: 2},
    shadowOpacity: 0.4,
    shadowRadius: 3,
    elevation: 4,
  },
  seekBarThumbDragging: {
    transform: [{scale: 1.3}],
    backgroundColor: '#38BDF8',
    borderColor: '#FFFFFF',
  },
  seekBarThumbFocused: {
    transform: [{scale: 1.25}],
    borderColor: '#F8FAFC',
    backgroundColor: '#0284C7',
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
  botheredControl: {
    backgroundColor: 'rgba(50, 50, 50, 0.8)',
    borderColor: 'transparent',
    borderWidth: 3,
  },
  botheredControlFocused: {
    backgroundColor: '#E5A900', // High-contrast TV yellow
    borderColor: '#FFFFFF',
    borderWidth: 3,
    transform: [{scale: 1.08}], // Pop-out effect
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
  botheredControlText: {
    color: '#F8FAFC',
    fontSize: 16,
    fontWeight: '600',
  },
  botheredControlFocusedText: {
    color: '#000000',
    fontSize: 16,
    fontWeight: '800',
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
  promptOverlay: {
    position: 'absolute',
    top: 24,
    right: CONTAINER_PADDING_HORIZONTAL,
    zIndex: 85,
    pointerEvents: 'box-none',
  },
  promptContainer: {
    backgroundColor: '#1E293B',
    borderRadius: 12,
    borderWidth: 2,
    borderColor: '#38BDF8',
    padding: 16,
    width: 360,
    maxWidth: 400,
    alignItems: 'flex-start',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.6,
    shadowRadius: 8,
    elevation: 8,
  },
  promptHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 6,
  },
  promptHeaderBadge: {
    color: '#38BDF8',
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 1,
  },
  promptTitle: {
    color: '#F8FAFC',
    fontSize: 18,
    fontWeight: 'bold',
    marginBottom: 6,
    textAlign: 'left',
  },
  promptMessage: {
    color: '#CBD5E1',
    fontSize: 13,
    textAlign: 'left',
    marginBottom: 14,
    lineHeight: 18,
  },
  promptButtonGroup: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    width: '100%',
  },
  promptButton: {
    backgroundColor: '#334155',
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 16,
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'transparent',
    marginLeft: 8,
  },
  promptSkipButton: {
    backgroundColor: '#DC2626',
  },
  promptButtonFocused: {
    borderColor: '#FFFFFF',
    backgroundColor: '#2563EB',
    transform: [{ scale: 1.05 }],
  },
  promptButtonText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: 'bold',
  },
  harnessPanel: {
    backgroundColor: '#0F172A',
    borderColor: '#3B82F6',
    borderWidth: 2,
    borderRadius: 10,
    padding: 14,
    marginHorizontal: CONTAINER_PADDING_HORIZONTAL,
    marginBottom: 14,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 6,
    elevation: 6,
  },
  harnessHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  harnessHeaderLeft: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  harnessBadge: {
    backgroundColor: '#2563EB',
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: 'bold',
    paddingVertical: 2,
    paddingHorizontal: 8,
    borderRadius: 4,
    overflow: 'hidden',
  },
  harnessTitle: {
    color: '#F8FAFC',
    fontSize: 16,
    fontWeight: '700',
    marginLeft: 10,
  },
  harnessCloseButton: {
    backgroundColor: '#334155',
    paddingVertical: 4,
    paddingHorizontal: 12,
    borderRadius: 6,
  },
  harnessCloseText: {
    color: '#CBD5E1',
    fontSize: 12,
    fontWeight: '600',
  },
  harnessTabRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 10,
  },
  harnessTab: {
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 6,
    backgroundColor: '#1E293B',
    borderWidth: 1,
    borderColor: '#334155',
  },
  harnessTabSelected: {
    backgroundColor: '#1D4ED8',
    borderColor: '#60A5FA',
  },
  harnessTabFocused: {
    borderColor: '#FFFFFF',
  },
  harnessTabText: {
    color: '#94A3B8',
    fontSize: 12,
    fontWeight: '600',
  },
  harnessTabTextSelected: {
    color: '#FFFFFF',
    fontWeight: '700',
  },
  harnessInfoCard: {
    backgroundColor: '#1E293B',
    borderRadius: 8,
    padding: 10,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: '#334155',
  },
  harnessInfoDesc: {
    color: '#CBD5E1',
    fontSize: 12,
    marginBottom: 6,
    lineHeight: 18,
  },
  harnessMetaRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  harnessMetaText: {
    color: '#94A3B8',
    fontSize: 11,
  },
  harnessActionsRow: {
    flexDirection: 'row',
    gap: 8,
    alignItems: 'center',
  },
  harnessActionButton: {
    flex: 1,
    backgroundColor: '#1E293B',
    paddingVertical: 8,
    paddingHorizontal: 8,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: '#475569',
    alignItems: 'center',
  },
  harnessActionPrimary: {
    backgroundColor: '#047857',
    borderColor: '#10B981',
  },
  harnessActionText: {
    color: '#E2E8F0',
    fontSize: 12,
    fontWeight: '600',
    textAlign: 'center',
  },
  harnessActionPrimaryText: {
    color: '#FFFFFF',
    fontWeight: '700',
  },
  harnessButtonFocused: {
    borderColor: '#FFFFFF',
    transform: [{ scale: 1.02 }],
  },
});
