import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Image,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { BackHandler, TVFocusGuideView } from '@amazon-devices/react-native-kepler';
import {
  CANONICAL_ACTIONS,
  CANONICAL_TRIGGERS,
  CanonicalAction,
  CanonicalTrigger,
  DEFAULT_VIEWER_OPTIONS,
  TRIGGER_ACTION_CONTRACTS,
  ViewerOptions,
  isActionAllowedForTrigger,
  sanitizeActionOverrides,
} from '../engine/types';
import { getItem, setItem, STORAGE_KEYS } from '../storage';

// ==============================================================================
// Trigger-Aware Player - Settings & Viewer Profile Screen (Person B)
// ==============================================================================
//
// Responsibilities:
// 1. Exposes agreed blur and volume reduction settings controls with stable
//    focus targets for Person C's nextFocusUp / nextFocusDown D-pad navigation.
// 2. Enforces non-aliased action semantics:
//    - 'dim' (brightness reduction) must NOT alias to 'hide' (blackout).
//    - 'blur' (spatial visual filter) must NOT alias to 'reduce' (audio attenuation).
//    - 'reduce' (fractional audio gain) must NOT alias to 'mute' (zero gain).
// 3. Displays calibrated blur preview image (src/assets/blur_preview.png)
//    with mandatory CC0 rights label: "sample: real films vary".
// 4. Reads and persists viewer preferences using STORAGE_KEYS.USER_SETTINGS
//    and STORAGE_KEYS.USER_PROFILE via AsyncStorage.
// 5. Exposes per-trigger action selection adhering to canonical contracts.
// ==============================================================================

export const REDUCTION_GAIN_OPTIONS = [0.15, 0.25, 0.30, 0.50, 0.75] as const;
export type ReductionGainOption = (typeof REDUCTION_GAIN_OPTIONS)[number];

export const BLUR_MODE_OPTIONS = ['off', 'standard', 'high'] as const;
export type BlurModeOption = (typeof BLUR_MODE_OPTIONS)[number];

export const CANONICAL_TRIGGER_CATEGORIES = CANONICAL_TRIGGERS;
export type CanonicalTriggerCategory = CanonicalTrigger;

export const PLAYER_PROFILES = [
  'standard',
  'verification',
  'skip_verification',
] as const;
export type PlayerProfile = (typeof PLAYER_PROFILES)[number];

export interface SettingsScreenProps {
  onBack?: () => void;
  initialOptions?: ViewerOptions;
  initialProfile?: PlayerProfile;
  onSave?: (options: ViewerOptions, profile: PlayerProfile) => void;
  testID?: string;

  // Extension points for Person C D-pad navigation chaining
  blurPickerNextFocusUp?: number | string;
  blurPickerNextFocusDown?: number | string;
  reductionPickerNextFocusUp?: number | string;
  reductionPickerNextFocusDown?: number | string;
}

/**
 * Sanitizes and validates arbitrary input into a safe ViewerOptions object.
 * Preserves strict type and range constraints with DEFAULT_VIEWER_OPTIONS fallback.
 */
export function sanitizeViewerOptions(
  raw: unknown,
  fallback: ViewerOptions = DEFAULT_VIEWER_OPTIONS,
): ViewerOptions {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ...fallback };
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
    const validTriggers = new Set<string>(CANONICAL_TRIGGER_CATEGORIES);
    result.disabledCategories = obj.disabledCategories.filter(
      (item): item is string => typeof item === 'string' && validTriggers.has(item),
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

export const SettingsScreen: React.FC<SettingsScreenProps> = ({
  onBack,
  initialOptions,
  initialProfile,
  onSave,
  testID = 'settings-screen',
  blurPickerNextFocusUp,
  blurPickerNextFocusDown,
  reductionPickerNextFocusUp,
  reductionPickerNextFocusDown,
}) => {
  const [options, setOptions] = useState<ViewerOptions>(
    initialOptions ? sanitizeViewerOptions(initialOptions) : DEFAULT_VIEWER_OPTIONS,
  );
  const [profile, setProfile] = useState<PlayerProfile>(
    initialProfile ?? 'standard',
  );
  const [blurMode, setBlurMode] = useState<BlurModeOption>('standard');
  const [focusedId, setFocusedId] = useState<string>('settings-back-button');
  const [saveStatusMessage, setSaveStatusMessage] = useState<string>('');

  const optionsRef = useRef<ViewerOptions>(options);
  optionsRef.current = options;
  const profileRef = useRef<PlayerProfile>(profile);
  profileRef.current = profile;

  const hasUserModifiedOptionsRef = useRef<boolean>(false);
  const hasUserModifiedProfileRef = useRef<boolean>(false);

  // Dedicated refs for Person C focus chaining
  const backButtonRef = useRef<any>(null);
  const resetButtonRef = useRef<any>(null);
  const profileStandardRef = useRef<any>(null);
  const reductionToggleRef = useRef<any>(null);
  const reductionOption30Ref = useRef<any>(null);
  const blurOptionStandardRef = useRef<any>(null);
  const visualProtectionRef = useRef<any>(null);
  const categoryFlashingRef = useRef<any>(null);
  const isMountedRef = useRef<boolean>(true);

  // Hardware BackHandler integration
  useEffect(() => {
    const subscription = BackHandler.addEventListener(
      'hardwareBackPress',
      () => {
        if (onBack) {
          onBack();
          return true;
        }
        return false;
      },
    );
    return () => subscription.remove();
  }, [onBack]);

  // Load persisted preferences on mount
  useEffect(() => {
    isMountedRef.current = true;
    async function loadStoredPreferences() {
      try {
        const [savedSettings, savedProfile] = await Promise.all([
          getItem<ViewerOptions>(STORAGE_KEYS.USER_SETTINGS),
          getItem<PlayerProfile>(STORAGE_KEYS.USER_PROFILE),
        ]);

        if (isMountedRef.current) {
          if (savedSettings && !hasUserModifiedOptionsRef.current) {
            const merged = sanitizeViewerOptions(savedSettings, optionsRef.current);
            setOptions(merged);
            optionsRef.current = merged;
          }
          if (
            savedProfile &&
            PLAYER_PROFILES.includes(savedProfile) &&
            !hasUserModifiedProfileRef.current
          ) {
            setProfile(savedProfile as PlayerProfile);
            profileRef.current = savedProfile;
          }
        }
      } catch (err) {
        console.warn('Failed to load settings from storage', err);
      }
    }
    loadStoredPreferences();
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const persistChanges = useCallback(
    async (nextOptions: ViewerOptions, nextProfile: PlayerProfile) => {
      try {
        await Promise.all([
          setItem(STORAGE_KEYS.USER_SETTINGS, nextOptions),
          setItem(STORAGE_KEYS.USER_PROFILE, nextProfile),
        ]);
        if (onSave) {
          onSave(nextOptions, nextProfile);
        }
        setSaveStatusMessage('Settings saved');
        setTimeout(() => setSaveStatusMessage(''), 2000);
      } catch (err) {
        console.warn('Failed to persist settings changes', err);
        setSaveStatusMessage('Save Error');
      }
    },
    [onSave],
  );

  const handleUpdateOptions = useCallback(
    (updater: (prev: ViewerOptions) => ViewerOptions) => {
      hasUserModifiedOptionsRef.current = true;
      setOptions((prev) => {
        const next = sanitizeViewerOptions(updater(prev), prev);
        optionsRef.current = next;
        persistChanges(next, profileRef.current);
        return next;
      });
    },
    [persistChanges],
  );

  const handleProfileChange = useCallback(
    (nextProfile: PlayerProfile) => {
      hasUserModifiedProfileRef.current = true;
      setProfile(nextProfile);
      profileRef.current = nextProfile;
      persistChanges(optionsRef.current, nextProfile);
    },
    [persistChanges],
  );

  const handleResetDefaults = useCallback(() => {
    hasUserModifiedOptionsRef.current = true;
    hasUserModifiedProfileRef.current = true;
    const defaults: ViewerOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      actionOverrides: {},
    };
    const defaultProfile: PlayerProfile = 'standard';
    setOptions(defaults);
    setProfile(defaultProfile);
    setBlurMode('standard');
    optionsRef.current = defaults;
    profileRef.current = defaultProfile;
    persistChanges(defaults, defaultProfile);
  }, [persistChanges]);

  return (
    <View style={styles.container} testID={testID}>
      {/* Header bar */}
      <View style={styles.header}>
        <View style={styles.headerTextContainer}>
          <Text style={styles.headerTitle}>Viewer Settings & Calibration</Text>
          <Text style={styles.headerSubtitle}>
            Configure trigger actions, audio reduction levels, and spatial blur calibration
          </Text>
        </View>

        <View style={styles.headerActionRow}>
          {onBack && (
            <TouchableOpacity
              ref={backButtonRef}
              style={[
                styles.navButton,
                focusedId === 'settings-back-button' && styles.navButtonFocused,
              ]}
              testID="settings-back-button"
              accessibilityRole="button"
              accessibilityLabel="Back to Player"
              isTVSelectable={true}
              hasTVPreferredFocus={true}
              nextFocusRight={resetButtonRef.current}
              nextFocusDown={profileStandardRef.current}
              onFocus={() => setFocusedId('settings-back-button')}
              onPress={onBack}
              activeOpacity={0.8}>
              <Text style={styles.navButtonText}>← Back</Text>
            </TouchableOpacity>
          )}

          <TouchableOpacity
            ref={resetButtonRef}
            style={[
              styles.navButton,
              styles.resetButton,
              focusedId === 'settings-reset-button' && styles.navButtonFocused,
            ]}
            testID="settings-reset-button"
            accessibilityRole="button"
            accessibilityLabel="Reset to Default Settings"
            isTVSelectable={true}
            nextFocusLeft={backButtonRef.current}
            nextFocusDown={profileStandardRef.current}
            onFocus={() => setFocusedId('settings-reset-button')}
            onPress={handleResetDefaults}
            activeOpacity={0.8}>
            <Text style={styles.navButtonText}>Reset Defaults</Text>
          </TouchableOpacity>
        </View>
      </View>

      {saveStatusMessage !== '' && (
        <View style={styles.statusToast} testID="settings-status-toast">
          <Text style={styles.statusToastText}>✓ {saveStatusMessage}</Text>
        </View>
      )}

      {/* Main settings body */}
      <ScrollView
        style={styles.scrollContainer}
        contentContainerStyle={styles.scrollContent}
        testID="settings-scroll-view">
        {/* SECTION 1: VIEWER PROFILE */}
        <TVFocusGuideView style={styles.sectionCard}>
          <Text style={styles.sectionHeader}>VIEWER PROFILE</Text>
          <Text style={styles.sectionDescription}>
            Select active testing or playback profile persisted to on-device storage.
          </Text>
          <View style={styles.optionsRow} testID="profile-selector">
            {PLAYER_PROFILES.map((p) => {
              const isSelected = profile === p;
              const itemId = `profile-${p}`;
              const isFocused = focusedId === itemId;
              return (
                <TouchableOpacity
                  key={p}
                  ref={p === 'standard' ? profileStandardRef : undefined}
                  style={[
                    styles.pickerButton,
                    isSelected && styles.pickerButtonSelected,
                    isFocused && styles.pickerButtonFocused,
                  ]}
                  testID={itemId}
                  accessibilityRole="radio"
                  accessibilityState={{ selected: isSelected }}
                  accessibilityLabel={`Profile: ${p}`}
                  isTVSelectable={true}
                  nextFocusUp={backButtonRef.current}
                  nextFocusDown={reductionToggleRef.current}
                  onFocus={() => setFocusedId(itemId)}
                  onPress={() => handleProfileChange(p)}
                  activeOpacity={0.8}>
                  <Text
                    style={[
                      styles.pickerButtonText,
                      isSelected && styles.pickerButtonTextSelected,
                      isFocused && styles.pickerButtonFocusedText,
                    ]}>
                    {p === 'standard'
                      ? 'Standard'
                      : p === 'verification'
                      ? 'Verification'
                      : 'Skip Verification'}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </TVFocusGuideView>

        {/* SECTION 2: VOLUME REDUCTION PICKER */}
        <TVFocusGuideView
          style={styles.sectionCard}
          testID="reduction-picker-container"
          autoFocus={true}>
          <View style={styles.sectionTitleRow}>
            <Text style={styles.sectionHeader}>AUDIO VOLUME REDUCTION</Text>
            <TouchableOpacity
              ref={reductionToggleRef}
              style={[
                styles.togglePill,
                options.reduceEnabled ? styles.togglePillActive : styles.togglePillInactive,
                focusedId === 'toggle-volume-reduction' && styles.togglePillFocused,
              ]}
              testID="toggle-volume-reduction"
              accessibilityRole="checkbox"
              accessibilityState={{ checked: options.reduceEnabled }}
              accessibilityLabel="Enable Audio Volume Reduction"
              isTVSelectable={true}
              nextFocusUp={profileStandardRef.current}
              nextFocusDown={reductionOption30Ref.current}
              onFocus={() => setFocusedId('toggle-volume-reduction')}
              onPress={() =>
                handleUpdateOptions((prev) => ({
                  ...prev,
                  reduceEnabled: !prev.reduceEnabled,
                }))
              }>
              <Text style={styles.togglePillText}>
                {options.reduceEnabled ? 'ENABLED' : 'DISABLED'}
              </Text>
            </TouchableOpacity>
          </View>

          <Text style={styles.sectionDescription}>
            Attenuates audio output to target gain during loud spikes, screams, or explosions.
            Action semantics note: 'reduce' softens volume ([0.0, 1.0]) and does NOT alias
            to 'mute' (zero gain) or 'blur' / 'dim' (visual operations).
          </Text>

          {/* Reduction Gain Level Picker - Target for Person C D-pad navigation */}
          <View
            style={styles.optionsRow}
            testID="reduction-picker"
            accessible={true}
            accessibilityRole="radiogroup"
            accessibilityLabel="Volume Reduction Level Options">
            {REDUCTION_GAIN_OPTIONS.map((gain) => {
              const isSelected = Math.abs(options.reduceGain - gain) < 0.01;
              const itemId = `reduction-option-${Math.round(gain * 100)}`;
              const isFocused = focusedId === itemId;
              const percent = Math.round(gain * 100);

              return (
                <TouchableOpacity
                  key={gain}
                  ref={gain === 0.3 ? reductionOption30Ref : undefined}
                  style={[
                    styles.pickerButton,
                    isSelected && styles.pickerButtonSelected,
                    isFocused && styles.pickerButtonFocused,
                  ]}
                  testID={itemId}
                  accessibilityRole="radio"
                  accessibilityState={{ selected: isSelected }}
                  accessibilityLabel={`Reduction Gain ${percent} percent`}
                  isTVSelectable={true}
                  nextFocusUp={
                    reductionPickerNextFocusUp !== undefined
                      ? reductionPickerNextFocusUp
                      : reductionToggleRef.current
                  }
                  nextFocusDown={
                    reductionPickerNextFocusDown !== undefined
                      ? reductionPickerNextFocusDown
                      : blurOptionStandardRef.current
                  }
                  onFocus={() => setFocusedId(itemId)}
                  onPress={() =>
                    handleUpdateOptions((prev) => ({
                      ...prev,
                      reduceGain: gain,
                      reduceEnabled: true,
                    }))
                  }
                  activeOpacity={0.8}>
                  <Text
                    style={[
                      styles.pickerButtonText,
                      isSelected && styles.pickerButtonTextSelected,
                      isFocused && styles.pickerButtonFocusedText,
                    ]}>
                    {percent}% {gain === 0.3 ? '(Default)' : ''}
                  </Text>
                  <Text
                    style={[
                      styles.pickerSubText,
                      isFocused && styles.pickerSubTextFocused,
                    ]}>
                    {gain === 0.15
                      ? '-16 dB'
                      : gain === 0.25
                      ? '-12 dB'
                      : gain === 0.3
                      ? '-10 dB'
                      : gain === 0.5
                      ? '-6 dB'
                      : '-2.5 dB'}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </TVFocusGuideView>

        {/* SECTION 3: SPATIAL BLUR CALIBRATION & PREVIEW */}
        <TVFocusGuideView
          style={styles.sectionCard}
          testID="blur-picker-container"
          autoFocus={true}>
          <View style={styles.sectionTitleRow}>
            <Text style={styles.sectionHeader}>SPATIAL BLUR CALIBRATION</Text>
            <Text style={styles.semanticsBadge}>SPATIAL FILTER (VISUAL)</Text>
          </View>

          <Text style={styles.sectionDescription}>
            Applies visual filter degradation to spiders and disturbing scenes.
            Action semantics note: 'blur' is spatial video softening. It does NOT alias
            to 'reduce' (audio) and 'dim' does NOT alias to 'hide' (blackout). If blur
            rendering is unsupported by hardware, player falls back safely to blackout.
          </Text>

          {/* Blur Intensity Picker - Target for Person C D-pad navigation */}
          <View
            style={styles.optionsRow}
            testID="blur-picker"
            accessible={true}
            accessibilityRole="radiogroup"
            accessibilityLabel="Blur Protection Intensity Options">
            {BLUR_MODE_OPTIONS.map((mode) => {
              const isSelected = blurMode === mode;
              const itemId = `blur-option-${mode}`;
              const isFocused = focusedId === itemId;

              return (
                <TouchableOpacity
                  key={mode}
                  ref={mode === 'standard' ? blurOptionStandardRef : undefined}
                  style={[
                    styles.pickerButton,
                    isSelected && styles.pickerButtonSelected,
                    isFocused && styles.pickerButtonFocused,
                  ]}
                  testID={itemId}
                  accessibilityRole="radio"
                  accessibilityState={{ selected: isSelected }}
                  accessibilityLabel={`Blur Intensity: ${mode}`}
                  isTVSelectable={true}
                  nextFocusUp={
                    blurPickerNextFocusUp !== undefined
                      ? blurPickerNextFocusUp
                      : reductionOption30Ref.current
                  }
                  nextFocusDown={
                    blurPickerNextFocusDown !== undefined
                      ? blurPickerNextFocusDown
                      : visualProtectionRef.current
                  }
                  onFocus={() => setFocusedId(itemId)}
                  onPress={() => setBlurMode(mode)}
                  activeOpacity={0.8}>
                  <Text
                    style={[
                      styles.pickerButtonText,
                      isSelected && styles.pickerButtonTextSelected,
                      isFocused && styles.pickerButtonFocusedText,
                    ]}>
                    {mode === 'off'
                      ? 'Off'
                      : mode === 'standard'
                      ? 'Standard Blur'
                      : 'High Blur'}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          {/* Step 5 Blur Preview Image & Calibration Display */}
          <View style={styles.previewContainer} testID="blur-preview-container">
            <View style={styles.previewImageWrapper}>
              <Image
                source={require('../assets/blur_preview.png')}
                style={[
                  styles.previewImage,
                  blurMode === 'off'
                    ? styles.previewImageOff
                    : blurMode === 'high'
                    ? styles.previewImageHigh
                    : styles.previewImageStandard,
                ]}
                resizeMode="contain"
                testID="blur-preview-image"
                accessibilityLabel="Blur calibration preview image"
              />
              {/* Mandatory CC0 Rights Label per docs/licenses/blur_preview_license.md */}
              <View style={styles.previewLabelBadge}>
                <Text style={styles.previewLabelText} testID="blur-preview-label">
                  sample: real films vary
                </Text>
              </View>
            </View>
            <View style={styles.previewInfo}>
              <Text style={styles.previewTitle}>Visual Calibration Target</Text>
              <Text style={styles.previewDetails}>
                CC0 Reference Photographic Sample with fine high-frequency contour detail.
                Allows calibrating visual degradation without exposing trigger imagery.
              </Text>
            </View>
          </View>
        </TVFocusGuideView>

        {/* SECTION 4: CORE ACTION PROTECTIONS */}
        <TVFocusGuideView style={styles.sectionCard}>
          <Text style={styles.sectionHeader}>CORE PLAYBACK ACTIONS</Text>
          <Text style={styles.sectionDescription}>
            Individual toggles for player protection mechanisms.
          </Text>

          <View style={styles.togglesGrid}>
            <ToggleItem
              forwardedRef={visualProtectionRef}
              testID="toggle-visual-protection"
              label="Visual Protection (Hide / Blackout)"
              enabled={options.hideEnabled}
              isFocused={focusedId === 'toggle-visual-protection'}
              nextFocusUp={blurOptionStandardRef.current}
              onFocus={() => setFocusedId('toggle-visual-protection')}
              onPress={() =>
                handleUpdateOptions((prev) => ({
                  ...prev,
                  hideEnabled: !prev.hideEnabled,
                }))
              }
            />

            <ToggleItem
              testID="toggle-audio-protection"
              label="Audio Protection (Mute)"
              enabled={options.muteEnabled}
              isFocused={focusedId === 'toggle-audio-protection'}
              onFocus={() => setFocusedId('toggle-audio-protection')}
              onPress={() =>
                handleUpdateOptions((prev) => ({
                  ...prev,
                  muteEnabled: !prev.muteEnabled,
                }))
              }
            />

            <ToggleItem
              testID="toggle-skip-protection"
              label="Skip Protection (Jump past triggers)"
              enabled={options.skipEnabled}
              isFocused={focusedId === 'toggle-skip-protection'}
              onFocus={() => setFocusedId('toggle-skip-protection')}
              onPress={() =>
                handleUpdateOptions((prev) => ({
                  ...prev,
                  skipEnabled: !prev.skipEnabled,
                }))
              }
            />

            <ToggleItem
              testID="toggle-heads-up-prompts"
              label="Advance Heads-Up Prompts (3-5s Notice)"
              enabled={options.headsUpPrompts?.enabled ?? false}
              isFocused={focusedId === 'toggle-heads-up-prompts'}
              nextFocusDown={categoryFlashingRef.current}
              onFocus={() => setFocusedId('toggle-heads-up-prompts')}
              onPress={() =>
                handleUpdateOptions((prev) => ({
                  ...prev,
                  headsUpPrompts: {
                    enabled: !(prev.headsUpPrompts?.enabled ?? false),
                    leadTimeSeconds: prev.headsUpPrompts?.leadTimeSeconds ?? 5.0,
                  },
                }))
              }
            />
          </View>
        </TVFocusGuideView>

        {/* SECTION 5: CANONICAL TRIGGER CATEGORIES */}
        <TVFocusGuideView style={styles.sectionCard}>
          <Text style={styles.sectionHeader}>CANONICAL TRIGGER CATEGORIES</Text>
          <Text style={styles.sectionDescription}>
            Disable specific trigger categories if you prefer unmoderated playback for them.
          </Text>

          <View style={styles.categoriesGrid}>
            {CANONICAL_TRIGGER_CATEGORIES.map((cat) => {
              const isEnabled = !options.disabledCategories?.includes(cat);
              const itemId = `toggle-category-${cat}`;
              const isFocused = focusedId === itemId;

              return (
                <TouchableOpacity
                  key={cat}
                  ref={cat === 'flashing' ? categoryFlashingRef : undefined}
                  style={[
                    styles.categoryPill,
                    isEnabled ? styles.categoryPillActive : styles.categoryPillDisabled,
                    isFocused && styles.categoryPillFocused,
                  ]}
                  testID={itemId}
                  accessibilityRole="checkbox"
                  accessibilityState={{ checked: isEnabled }}
                  accessibilityLabel={`Trigger Category: ${cat}`}
                  isTVSelectable={true}
                  nextFocusUp={visualProtectionRef.current}
                  onFocus={() => setFocusedId(itemId)}
                  onPress={() =>
                    handleUpdateOptions((prev) => {
                      const disabled = prev.disabledCategories ?? [];
                      const nextDisabled = disabled.includes(cat)
                        ? disabled.filter((c) => c !== cat)
                        : [...disabled, cat];
                      return {
                        ...prev,
                        disabledCategories: nextDisabled,
                      };
                    })
                  }
                  activeOpacity={0.8}>
                  <Text
                    style={[
                      styles.categoryPillIcon,
                      isFocused && styles.categoryPillFocusedIcon,
                    ]}>
                    {isEnabled ? '✓' : '✕'}
                  </Text>
                  <Text
                    style={[
                      styles.categoryPillText,
                      isEnabled
                        ? styles.categoryPillTextActive
                        : styles.categoryPillTextDisabled,
                      isFocused && styles.categoryPillFocusedText,
                    ]}>
                    {cat}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </TVFocusGuideView>

        {/* SECTION 6: PER-TRIGGER ACTION OVERRIDES */}
        <TVFocusGuideView style={styles.sectionCard} testID="trigger-action-overrides-section">
          <Text style={styles.sectionHeader}>PER-TRIGGER ACTION OVERRIDES</Text>
          <Text style={styles.sectionDescription}>
            Configure your preferred response for each trigger. Selecting the default restores standard behavior.
          </Text>

          <View style={styles.triggerOverridesList}>
            {CANONICAL_TRIGGER_CATEGORIES.map((cat) => {
              const contract = TRIGGER_ACTION_CONTRACTS[cat];
              const currentAction = options.actionOverrides?.[cat] ?? contract.defaultAction;
              const isOverridden = options.actionOverrides?.[cat] !== undefined;
              const allAvailableActions = [contract.defaultAction, ...contract.allowedAlternatives];
              const containerId = `trigger-action-picker-${cat}`;

              return (
                <View key={cat} style={styles.triggerOverrideCard} testID={containerId}>
                  <View style={styles.triggerOverrideHeader}>
                    <View style={styles.triggerTitleGroup}>
                      <Text style={styles.triggerOverrideTitle}>{contract.displayName}</Text>
                      <Text style={styles.triggerOverrideCategory}>[{cat}]</Text>
                    </View>
                    <View style={styles.triggerDefaultBadge}>
                      <Text style={styles.triggerDefaultBadgeText}>
                        Default: {contract.defaultAction}
                        {isOverridden ? ' (Customized)' : ''}
                      </Text>
                    </View>
                  </View>

                  <View style={styles.triggerActionsRow}>
                    {allAvailableActions.map((act) => {
                      const isSelected = currentAction === act;
                      const isDefault = act === contract.defaultAction;
                      const itemId = `action-option-${cat}-${act}`;
                      const isFocused = focusedId === itemId;

                      return (
                        <TouchableOpacity
                          key={act}
                          style={[
                            styles.actionOptionButton,
                            isSelected && styles.actionOptionButtonSelected,
                            isFocused && styles.actionOptionButtonFocused,
                          ]}
                          testID={itemId}
                          accessibilityRole="radio"
                          accessibilityState={{ selected: isSelected }}
                          accessibilityLabel={`${cat} action ${act}${isDefault ? ' default' : ''}`}
                          isTVSelectable={true}
                          onFocus={() => setFocusedId(itemId)}
                          onPress={() => {
                            handleUpdateOptions((prev) => {
                              const nextOverrides = { ...(prev.actionOverrides ?? {}) };
                              if (act === contract.defaultAction) {
                                delete nextOverrides[cat];
                              } else {
                                nextOverrides[cat] = act;
                              }
                              return {
                                ...prev,
                                actionOverrides: nextOverrides,
                              };
                            });
                          }}
                          activeOpacity={0.8}>
                          <Text
                            style={[
                              styles.actionOptionText,
                              isSelected && styles.actionOptionTextSelected,
                              isFocused && styles.actionOptionFocusedText,
                            ]}>
                            {act} {isDefault ? '(Default)' : ''}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </View>
                </View>
              );
            })}
          </View>
        </TVFocusGuideView>
      </ScrollView>
    </View>
  );
};

// -----------------------------------------------------------------------------
// Subcomponent: ToggleItem for action protections
// -----------------------------------------------------------------------------

interface ToggleItemProps {
  label: string;
  enabled: boolean;
  isFocused: boolean;
  onFocus: () => void;
  onPress: () => void;
  testID: string;
  forwardedRef?: React.Ref<any>;
  nextFocusUp?: any;
  nextFocusDown?: any;
}

const ToggleItem: React.FC<ToggleItemProps> = ({
  label,
  enabled,
  isFocused,
  onFocus,
  onPress,
  testID,
  forwardedRef,
  nextFocusUp,
  nextFocusDown,
}) => {
  return (
    <TouchableOpacity
      ref={forwardedRef}
      style={[
        styles.toggleRow,
        isFocused && styles.toggleRowFocused,
      ]}
      testID={testID}
      accessibilityRole="checkbox"
      accessibilityState={{ checked: enabled }}
      accessibilityLabel={`${label}: ${enabled ? 'Enabled' : 'Disabled'}`}
      isTVSelectable={true}
      nextFocusUp={nextFocusUp}
      nextFocusDown={nextFocusDown}
      onFocus={onFocus}
      onPress={onPress}
      activeOpacity={0.8}>
      <Text
        style={[
          styles.toggleRowLabel,
          isFocused && styles.toggleRowLabelFocused,
        ]}>
        {label}
      </Text>
      <View
        style={[
          styles.statusBadge,
          enabled ? styles.statusBadgeActive : styles.statusBadgeInactive,
        ]}>
        <Text style={styles.statusBadgeText}>
          {enabled ? 'ON' : 'OFF'}
        </Text>
      </View>
    </TouchableOpacity>
  );
};

// -----------------------------------------------------------------------------
// Styles
// -----------------------------------------------------------------------------

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#090D16',
    paddingHorizontal: 48,
    paddingTop: 32,
    paddingBottom: 24,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 20,
    borderBottomWidth: 1,
    borderBottomColor: '#1E293B',
    paddingBottom: 16,
  },
  headerTextContainer: {
    flex: 1,
  },
  headerTitle: {
    color: '#FFFFFF',
    fontSize: 28,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  headerSubtitle: {
    color: '#94A3B8',
    fontSize: 14,
    marginTop: 4,
  },
  headerActionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  navButton: {
    backgroundColor: '#1E293B',
    paddingVertical: 10,
    paddingHorizontal: 18,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: 'transparent',
  },
  navButtonFocused: {
    borderColor: '#FFFFFF',
    backgroundColor: '#2563EB',
    transform: [{ scale: 1.05 }],
  },
  resetButton: {
    backgroundColor: '#27272A',
  },
  navButtonText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
  },
  statusToast: {
    backgroundColor: '#065F46',
    paddingVertical: 6,
    paddingHorizontal: 16,
    borderRadius: 6,
    marginBottom: 12,
    alignSelf: 'flex-start',
  },
  statusToastText: {
    color: '#34D399',
    fontSize: 12,
    fontWeight: '600',
  },
  scrollContainer: {
    flex: 1,
  },
  scrollContent: {
    paddingBottom: 40,
    gap: 20,
  },
  sectionCard: {
    backgroundColor: '#0F172A',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#1E293B',
    padding: 20,
  },
  sectionTitleRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  sectionHeader: {
    color: '#38BDF8',
    fontSize: 16,
    fontWeight: '700',
    letterSpacing: 1,
  },
  sectionDescription: {
    color: '#94A3B8',
    fontSize: 13,
    lineHeight: 18,
    marginBottom: 16,
  },
  semanticsBadge: {
    color: '#F59E0B',
    fontSize: 11,
    fontWeight: '700',
    backgroundColor: 'rgba(245, 158, 11, 0.1)',
    paddingVertical: 4,
    paddingHorizontal: 8,
    borderRadius: 4,
  },
  optionsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  pickerButton: {
    backgroundColor: '#1E293B',
    borderRadius: 8,
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderWidth: 2,
    borderColor: '#334155',
    minWidth: 110,
    alignItems: 'center',
  },
  pickerButtonSelected: {
    backgroundColor: '#0284C7',
    borderColor: '#38BDF8',
  },
  pickerButtonFocused: {
    borderColor: '#FFFFFF',
    backgroundColor: '#E5A900', // High-contrast TV yellow
    transform: [{ scale: 1.05 }],
  },
  pickerButtonFocusedText: {
    color: '#000000',
    fontWeight: '800',
  },
  pickerButtonText: {
    color: '#E2E8F0',
    fontSize: 14,
    fontWeight: '600',
  },
  pickerButtonTextSelected: {
    color: '#FFFFFF',
    fontWeight: '700',
  },
  pickerSubText: {
    color: '#94A3B8',
    fontSize: 11,
    marginTop: 2,
  },
  pickerSubTextFocused: {
    color: '#000000',
    fontWeight: '700',
  },
  togglePill: {
    paddingVertical: 6,
    paddingHorizontal: 14,
    borderRadius: 16,
    borderWidth: 2,
    borderColor: 'transparent',
  },
  togglePillActive: {
    backgroundColor: '#059669',
  },
  togglePillInactive: {
    backgroundColor: '#475569',
  },
  togglePillFocused: {
    borderColor: '#FFFFFF',
    transform: [{ scale: 1.06 }],
  },
  togglePillText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '700',
  },
  previewContainer: {
    flexDirection: 'row',
    backgroundColor: '#1E293B',
    borderRadius: 8,
    padding: 16,
    marginTop: 16,
    gap: 20,
    alignItems: 'center',
  },
  previewImageWrapper: {
    position: 'relative',
    width: 160,
    height: 120,
    borderRadius: 6,
    overflow: 'hidden',
    backgroundColor: '#000000',
    justifyContent: 'center',
    alignItems: 'center',
  },
  previewImage: {
    width: 160,
    height: 120,
  },
  previewImageOff: {
    opacity: 1.0,
  },
  previewImageStandard: {
    opacity: 0.65,
  },
  previewImageHigh: {
    opacity: 0.35,
  },
  previewLabelBadge: {
    position: 'absolute',
    bottom: 4,
    backgroundColor: 'rgba(0, 0, 0, 0.75)',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  previewLabelText: {
    color: '#F8FAFC',
    fontSize: 9,
    fontStyle: 'italic',
  },
  previewInfo: {
    flex: 1,
  },
  previewTitle: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
    marginBottom: 4,
  },
  previewDetails: {
    color: '#94A3B8',
    fontSize: 12,
    lineHeight: 16,
  },
  togglesGrid: {
    gap: 10,
  },
  toggleRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#1E293B',
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: 'transparent',
  },
  toggleRowFocused: {
    borderColor: '#FFFFFF',
    backgroundColor: '#1E3A8A',
    transform: [{ scale: 1.02 }],
  },
  toggleRowLabel: {
    color: '#E2E8F0',
    fontSize: 14,
    fontWeight: '500',
  },
  toggleRowLabelFocused: {
    color: '#FFFFFF',
    fontWeight: '700',
  },
  statusBadge: {
    paddingVertical: 4,
    paddingHorizontal: 10,
    borderRadius: 4,
  },
  statusBadgeActive: {
    backgroundColor: '#059669',
  },
  statusBadgeInactive: {
    backgroundColor: '#64748B',
  },
  statusBadgeText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '700',
  },
  categoriesGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  categoryPill: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderRadius: 6,
    borderWidth: 2,
    gap: 6,
  },
  categoryPillActive: {
    backgroundColor: '#1E293B',
    borderColor: '#38BDF8',
  },
  categoryPillDisabled: {
    backgroundColor: '#181E29',
    borderColor: '#334155',
    opacity: 0.5,
  },
  categoryPillFocused: {
    borderColor: '#FFFFFF',
    backgroundColor: '#E5A900',
    transform: [{ scale: 1.05 }],
  },
  categoryPillFocusedIcon: {
    color: '#000000',
    fontWeight: '800',
  },
  categoryPillFocusedText: {
    color: '#000000',
    fontWeight: '800',
  },
  categoryPillIcon: {
    color: '#38BDF8',
    fontSize: 12,
    fontWeight: '700',
  },
  categoryPillText: {
    fontSize: 12,
    fontWeight: '600',
    textTransform: 'capitalize',
  },
  categoryPillTextActive: {
    color: '#FFFFFF',
  },
  categoryPillTextDisabled: {
    color: '#64748B',
    textDecorationLine: 'line-through',
  },
  triggerOverridesList: {
    gap: 14,
  },
  triggerOverrideCard: {
    backgroundColor: '#1E293B',
    borderRadius: 8,
    padding: 14,
    borderWidth: 1,
    borderColor: '#334155',
  },
  triggerOverrideHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  triggerTitleGroup: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  triggerOverrideTitle: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: '600',
  },
  triggerOverrideCategory: {
    color: '#94A3B8',
    fontSize: 12,
    fontFamily: 'monospace',
  },
  triggerDefaultBadge: {
    backgroundColor: '#0F172A',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
  },
  triggerDefaultBadgeText: {
    color: '#38BDF8',
    fontSize: 11,
    fontWeight: '600',
  },
  triggerActionsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  actionOptionButton: {
    backgroundColor: '#0F172A',
    borderRadius: 6,
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderWidth: 2,
    borderColor: '#334155',
  },
  actionOptionButtonSelected: {
    backgroundColor: '#0284C7',
    borderColor: '#38BDF8',
  },
  actionOptionButtonFocused: {
    borderColor: '#FFFFFF',
    backgroundColor: '#E5A900',
    transform: [{ scale: 1.05 }],
  },
  actionOptionText: {
    color: '#CBD5E1',
    fontSize: 12,
    fontWeight: '500',
  },
  actionOptionTextSelected: {
    color: '#FFFFFF',
    fontWeight: '700',
  },
  actionOptionFocusedText: {
    color: '#000000',
    fontWeight: '800',
  },
});
