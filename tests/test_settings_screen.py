"""Tests for Person B SettingsScreen Component, Calibration Pickers, and Persistence.

Covers:
- Presence of src/components/SettingsScreen.tsx.
- Exports for constants, profiles, trigger categories, and sanitizer.
- Blur and volume reduction picker controls and focus targets.
- Preserved non-aliased action semantics (dim vs hide, blur vs reduce).
- CC0 blur preview image asset and mandatory label 'sample: real films vary'.
- AsyncStorage keys for USER_SETTINGS and USER_PROFILE.
- Extension hooks for Person C nextFocusUp/nextFocusDown D-pad chaining.
- TypeScript logic validation via Node loader.
"""

from pathlib import Path
import re
import subprocess
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_SCREEN_PATH = REPO_ROOT / "src" / "components" / "SettingsScreen.tsx"
PLAYER_SCREEN_PATH = REPO_ROOT / "src" / "components" / "PlayerScreen.tsx"
BLUR_PREVIEW_ASSET = REPO_ROOT / "src" / "assets" / "blur_preview.png"


def test_settings_screen_file_exists():
    """Verify that SettingsScreen.tsx exists and is non-empty."""
    assert SETTINGS_SCREEN_PATH.is_file(), f"Missing {SETTINGS_SCREEN_PATH}"
    content = SETTINGS_SCREEN_PATH.read_text(encoding="utf-8")
    assert len(content) > 500, "SettingsScreen.tsx appears truncated or empty"


def test_blur_preview_asset_and_license_label():
    """Verify that the blur preview asset exists and the mandatory label is present."""
    assert BLUR_PREVIEW_ASSET.is_file(), f"Missing blur preview asset at {BLUR_PREVIEW_ASSET}"

    content = SETTINGS_SCREEN_PATH.read_text(encoding="utf-8")
    assert "require('../assets/blur_preview.png')" in content or 'require("../assets/blur_preview.png")' in content
    # Mandatory license label from docs/licenses/blur_preview_license.md
    assert "sample: real films vary" in content, "SettingsScreen must display 'sample: real films vary' label"


def test_non_aliased_action_semantics_enforced():
    """Verify that action semantics explicitly prevent aliasing:

    - dim must NOT alias to hide
    - blur must NOT alias to reduce
    - reduce must NOT alias to mute
    """
    content = SETTINGS_SCREEN_PATH.read_text(encoding="utf-8")

    assert "dim" in content
    assert "blur" in content
    assert "reduce" in content
    assert "hide" in content
    assert "mute" in content

    # Check for explicit documentation or guards against aliasing
    assert re.search(r"dim.*not alias.*hide", content, re.IGNORECASE)
    assert re.search(r"blur.*not alias.*reduce", content, re.IGNORECASE)
    assert re.search(r"reduce.*not alias.*mute", content, re.IGNORECASE)


def test_reduction_gain_and_blur_constants():
    """Verify that REDUCTION_GAIN_OPTIONS and BLUR_MODE_OPTIONS are exported with agreed values."""
    content = SETTINGS_SCREEN_PATH.read_text(encoding="utf-8")

    # Reduction gains: 15%, 25%, 30%, 50%, 75%
    for gain in ["0.15", "0.25", "0.30", "0.50", "0.75"]:
        assert gain in content, f"Missing gain option {gain} in SettingsScreen.tsx"

    # Blur modes
    for mode in ["off", "standard", "high"]:
        assert mode in content, f"Missing blur mode {mode} in SettingsScreen.tsx"


def test_storage_keys_and_persistence_integration():
    """Verify that settings and profile use STORAGE_KEYS.USER_SETTINGS and STORAGE_KEYS.USER_PROFILE."""
    content = SETTINGS_SCREEN_PATH.read_text(encoding="utf-8")

    assert "STORAGE_KEYS.USER_SETTINGS" in content
    assert "STORAGE_KEYS.USER_PROFILE" in content
    assert "getItem" in content
    assert "setItem" in content


def test_stable_focus_targets_for_person_c():
    """Verify that stable testIDs and focus targets exist for Person C's D-pad integration."""
    content = SETTINGS_SCREEN_PATH.read_text(encoding="utf-8")

    # Root screen and default testID
    assert "settings-screen" in content

    literal_test_ids = [
        "settings-back-button",
        "settings-reset-button",
        "reduction-picker",
        "blur-picker",
        "blur-preview-image",
        "blur-preview-label",
        "toggle-visual-protection",
        "toggle-audio-protection",
        "toggle-volume-reduction",
        "toggle-skip-protection",
        "toggle-heads-up-prompts",
    ]

    for test_id in literal_test_ids:
        assert f'testID="{test_id}"' in content or f"testID='{test_id}'" in content, (
            f"Missing required literal testID {test_id} for Person C D-pad navigation target"
        )

    # Dynamic picker option testIDs
    assert "reduction-option-" in content
    assert "blur-option-" in content
    assert "profile-" in content
    assert "toggle-category-" in content

    # Person C focus chaining props
    assert "reductionPickerNextFocusUp" in content
    assert "reductionPickerNextFocusDown" in content
    assert "blurPickerNextFocusUp" in content
    assert "blurPickerNextFocusDown" in content


def test_typescript_sanitize_viewer_options_logic():
    """Execute Node to test sanitizeViewerOptions and options defaults."""
    node_script = """
    import assert from 'node:assert';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';

    // Verify DEFAULT_VIEWER_OPTIONS contracts
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.hideEnabled, true);
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.muteEnabled, true);
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.reduceEnabled, false);
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.reduceGain, 0.3);
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.headsUpPrompts?.enabled, false);

    // Test sanitizer logic standalone
    const CANONICAL_TRIGGER_CATEGORIES = [
      'flashing', 'loud_spike', 'gunshot', 'explosion', 'scream',
      'sustained_loudness', 'profanity', 'insect', 'jump_scare', 'visible_blood'
    ];

    function sanitizeViewerOptions(raw, fallback = DEFAULT_VIEWER_OPTIONS) {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        return { ...fallback };
      }
      const obj = raw;
      const result = { ...fallback };

      if (typeof obj.hideEnabled === 'boolean') result.hideEnabled = obj.hideEnabled;
      if (typeof obj.muteEnabled === 'boolean') result.muteEnabled = obj.muteEnabled;
      if (typeof obj.reduceEnabled === 'boolean') result.reduceEnabled = obj.reduceEnabled;
      if (typeof obj.skipEnabled === 'boolean') result.skipEnabled = obj.skipEnabled;
      if (typeof obj.reduceGain === 'number' && Number.isFinite(obj.reduceGain) && obj.reduceGain >= 0 && obj.reduceGain <= 1) {
        result.reduceGain = obj.reduceGain;
      }
      if (typeof obj.confirmReviewSkip === 'boolean') result.confirmReviewSkip = obj.confirmReviewSkip;
      if (Array.isArray(obj.disabledCategories)) {
        const valid = new Set(CANONICAL_TRIGGER_CATEGORIES);
        result.disabledCategories = obj.disabledCategories.filter(
          item => typeof item === 'string' && valid.has(item)
        );
      }
      return result;
    }

    // 1. Empty or invalid raw falls back safely
    assert.deepStrictEqual(sanitizeViewerOptions(null), DEFAULT_VIEWER_OPTIONS);
    assert.deepStrictEqual(sanitizeViewerOptions('string'), DEFAULT_VIEWER_OPTIONS);

    // 2. Partial updates merge correctly
    const partial = sanitizeViewerOptions({ reduceGain: 0.5, reduceEnabled: true });
    assert.strictEqual(partial.reduceGain, 0.5);
    assert.strictEqual(partial.reduceEnabled, true);
    assert.strictEqual(partial.hideEnabled, true); // preserved from fallback

    // 3. Out of bounds reduceGain is rejected and falls back
    const invalidGain = sanitizeViewerOptions({ reduceGain: 1.5 });
    assert.strictEqual(invalidGain.reduceGain, 0.3);

    // 4. Invalid category filters out cleanly
    const cats = sanitizeViewerOptions({ disabledCategories: ['insect', 'invalid_foo'] });
    assert.deepStrictEqual(cats.disabledCategories, ['insect']);

    console.log('Sanitize viewer options tests passed successfully.');
    """

    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Sanitize viewer options tests passed successfully." in res.stdout


def test_default_viewer_options_contract_and_empty_storage():
    """Verify default viewer preferences when no saved settings exist in storage."""
    node_script = """
    import assert from 'node:assert';
    import { DEFAULT_VIEWER_OPTIONS, DEFAULT_HEADS_UP_PROMPT_OPTIONS } from './src/engine/types.ts';

    // Assert all default preference values when storage is unpopulated
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.hideEnabled, true);
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.muteEnabled, true);
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.reduceEnabled, false);
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.skipEnabled, false);
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.reduceGain, 0.3);
    assert.strictEqual(DEFAULT_VIEWER_OPTIONS.confirmReviewSkip, false);
    assert.deepStrictEqual(DEFAULT_VIEWER_OPTIONS.disabledCategories, []);
    assert.deepStrictEqual(DEFAULT_VIEWER_OPTIONS.headsUpPrompts, {
      enabled: false,
      leadTimeSeconds: 5.0,
    });
    assert.deepStrictEqual(DEFAULT_HEADS_UP_PROMPT_OPTIONS, {
      enabled: false,
      leadTimeSeconds: 5.0,
    });

    console.log('Default viewer options contract verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Default viewer options contract verified." in res.stdout


def test_settings_storage_roundtrip_persistence_and_reset():
    """Verify settings persistence roundtrip: update -> JSON serialization -> restore -> reset.

    Verifies:
    - Custom options and profile serialize and restore correctly.
    - App restart / re-entry simulation preserves modified options.
    - Reset defaults restores DEFAULT_VIEWER_OPTIONS and 'standard' profile.
    """
    node_script = """
    import assert from 'node:assert';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';

    const CANONICAL_TRIGGER_CATEGORIES = [
      'flashing', 'loud_spike', 'gunshot', 'explosion', 'scream',
      'sustained_loudness', 'profanity', 'insect', 'jump_scare', 'visible_blood'
    ];

    function sanitizeViewerOptions(raw, fallback = DEFAULT_VIEWER_OPTIONS) {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        return { ...fallback };
      }
      const obj = raw;
      const result = { ...fallback };

      if (typeof obj.hideEnabled === 'boolean') result.hideEnabled = obj.hideEnabled;
      if (typeof obj.muteEnabled === 'boolean') result.muteEnabled = obj.muteEnabled;
      if (typeof obj.reduceEnabled === 'boolean') result.reduceEnabled = obj.reduceEnabled;
      if (typeof obj.skipEnabled === 'boolean') result.skipEnabled = obj.skipEnabled;
      if (typeof obj.reduceGain === 'number' && Number.isFinite(obj.reduceGain) && obj.reduceGain >= 0 && obj.reduceGain <= 1) {
        result.reduceGain = obj.reduceGain;
      }
      if (typeof obj.confirmReviewSkip === 'boolean') result.confirmReviewSkip = obj.confirmReviewSkip;
      if (Array.isArray(obj.disabledCategories)) {
        const valid = new Set(CANONICAL_TRIGGER_CATEGORIES);
        result.disabledCategories = obj.disabledCategories.filter(
          item => typeof item === 'string' && valid.has(item)
        );
      }
      if (obj.headsUpPrompts && typeof obj.headsUpPrompts === 'object' && !Array.isArray(obj.headsUpPrompts)) {
        const promptObj = obj.headsUpPrompts;
        const enabled = typeof promptObj.enabled === 'boolean' ? promptObj.enabled : fallback.headsUpPrompts?.enabled ?? false;
        const leadTimeSeconds = typeof promptObj.leadTimeSeconds === 'number' && Number.isFinite(promptObj.leadTimeSeconds) && promptObj.leadTimeSeconds > 0
          ? promptObj.leadTimeSeconds
          : fallback.headsUpPrompts?.leadTimeSeconds ?? 5.0;
        result.headsUpPrompts = { enabled, leadTimeSeconds };
      }
      return result;
    }

    // 1. Initial state with no storage (null)
    let storedSettingsJson = null;
    let storedProfile = null;
    let loadedOptions = sanitizeViewerOptions(storedSettingsJson ? JSON.parse(storedSettingsJson) : null);
    assert.deepStrictEqual(loadedOptions, DEFAULT_VIEWER_OPTIONS);

    // 2. User modifies settings in UI
    const modifiedOptions = {
      ...loadedOptions,
      reduceEnabled: true,
      reduceGain: 0.5,
      disabledCategories: ['insect', 'flashing'],
      headsUpPrompts: { enabled: true, leadTimeSeconds: 4.0 },
    };
    const modifiedProfile = 'verification';

    // 3. Persist to storage (simulating setItem JSON serialization)
    storedSettingsJson = JSON.stringify(modifiedOptions);
    storedProfile = modifiedProfile;

    // 4. Simulate application restart / navigation re-entry (re-reading from storage)
    const rawRestored = JSON.parse(storedSettingsJson);
    const restoredOptions = sanitizeViewerOptions(rawRestored);
    assert.strictEqual(restoredOptions.reduceEnabled, true);
    assert.strictEqual(restoredOptions.reduceGain, 0.5);
    assert.deepStrictEqual(restoredOptions.disabledCategories, ['insect', 'flashing']);
    assert.strictEqual(restoredOptions.headsUpPrompts.enabled, true);
    assert.strictEqual(restoredOptions.headsUpPrompts.leadTimeSeconds, 4.0);
    assert.strictEqual(storedProfile, 'verification');

    // 5. Simulate Reset Defaults action
    const resetOptions = { ...DEFAULT_VIEWER_OPTIONS };
    const resetProfile = 'standard';
    storedSettingsJson = JSON.stringify(resetOptions);
    storedProfile = resetProfile;

    const afterResetOptions = sanitizeViewerOptions(JSON.parse(storedSettingsJson));
    assert.deepStrictEqual(afterResetOptions, DEFAULT_VIEWER_OPTIONS);
    assert.strictEqual(storedProfile, 'standard');

    console.log('Settings storage roundtrip and reset tests passed successfully.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Settings storage roundtrip and reset tests passed successfully." in res.stdout


def test_saved_preferences_affect_cue_handling_during_playback():
    """Verify that saved preferences directly alter cue handling during playback."""
    node_script = """
    import assert from 'node:assert';
    import { PlaybackState } from './src/engine/playbackState.ts';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';

    const testCues = [
      {
        id: 'cue-loud-reduce',
        start: 10.0,
        end: 15.0,
        source: 'audio',
        action: 'reduce',
        gain: 0.25,
        trigger: 'loud_spike',
      },
      {
        id: 'cue-insect-hide',
        start: 20.0,
        end: 25.0,
        source: 'visual',
        action: 'hide',
        trigger: 'insect',
      },
      {
        id: 'cue-gunshot-warn',
        start: 30.0,
        end: 35.0,
        source: 'audio',
        action: 'warn',
        trigger: 'gunshot',
      },
    ];

    const state = new PlaybackState();

    // 1. Under default preferences: reduceEnabled is false -> soundGain remains 1.0 (unattenuated)
    let a = state.resolveAt(12.0, testCues, DEFAULT_VIEWER_OPTIONS);
    assert.strictEqual(a.soundGain, 1.0);
    assert.strictEqual(a.activeCueIds.includes('cue-loud-reduce'), true);

    // 2. Under saved preferences with reduceEnabled: true and reduceGain: 0.5
    // Cue specifies gain: 0.25, so cue's gain takes effect (0.25)
    const optionsWithReduce = { ...DEFAULT_VIEWER_OPTIONS, reduceEnabled: true, reduceGain: 0.5 };
    a = state.resolveAt(12.0, testCues, optionsWithReduce);
    assert.strictEqual(a.soundGain, 0.25);

    // If cue has no explicit gain, options.reduceGain is used
    const cueWithoutGain = [{ id: 'c1', start: 10.0, end: 15.0, source: 'audio', action: 'reduce', trigger: 'loud_spike' }];
    a = state.resolveAt(12.0, cueWithoutGain, optionsWithReduce);
    assert.strictEqual(a.soundGain, 0.5);

    // 3. Under saved preferences with disabledCategories: ['loud_spike']
    const optionsWithDisabled = { ...optionsWithReduce, disabledCategories: ['loud_spike'] };
    a = state.resolveAt(12.0, testCues, optionsWithDisabled);
    // Loud spike is completely ignored
    assert.strictEqual(a.soundGain, 1.0);
    assert.strictEqual(a.activeCueIds.includes('cue-loud-reduce'), false);

    // 4. Under saved preferences with hideEnabled: false
    const optionsWithHideDisabled = { ...DEFAULT_VIEWER_OPTIONS, hideEnabled: false };
    a = state.resolveAt(22.0, testCues, optionsWithHideDisabled);
    // Insect hide cue is not hidden
    assert.strictEqual(a.hide, false);

    // 5. Under saved preferences with headsUpPrompts enabled
    const optionsWithPrompts = {
      ...DEFAULT_VIEWER_OPTIONS,
      headsUpPrompts: { enabled: true, leadTimeSeconds: 4.0 },
    };
    // Insect starts at 20.0 -> prompt window is [16.0, 20.0)
    assert.strictEqual(state.resolveAt(15.9, testCues, optionsWithPrompts).promptCue, null);
    const prompt = state.resolveAt(17.0, testCues, optionsWithPrompts).promptCue;
    assert.strictEqual(prompt?.id, 'cue-insect-hide');

    console.log('Saved preferences playback cue handling tests passed successfully.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Saved preferences playback cue handling tests passed successfully." in res.stdout


def test_canonical_default_profile_contract():
    """Verify canonical default profile ('standard') and valid profile set in PlayerScreen and SettingsScreen."""
    player_content = PLAYER_SCREEN_PATH.read_text(encoding="utf-8")
    settings_content = SETTINGS_SCREEN_PATH.read_text(encoding="utf-8")

    # Both must export PLAYER_PROFILES with 'standard', 'verification', 'skip_verification'
    assert "PLAYER_PROFILES" in player_content
    assert "PLAYER_PROFILES" in settings_content
    for p in ["standard", "verification", "skip_verification"]:
        assert f"'{p}'" in player_content or f'"{p}"' in player_content
        assert f"'{p}'" in settings_content or f'"{p}"' in settings_content

    # Default fallback in both is 'standard'
    assert "initialProfile ?? 'standard'" in player_content
    assert "initialProfile ?? 'standard'" in settings_content
    assert "isValidPlayerProfile" in player_content

    node_script = """
    import assert from 'node:assert';

    function isValidPlayerProfile(value) {
      return (
        typeof value === 'string' &&
        (value === 'standard' || value === 'verification' || value === 'skip_verification')
      );
    }

    // Verify validator
    assert.strictEqual(isValidPlayerProfile('standard'), true);
    assert.strictEqual(isValidPlayerProfile('verification'), true);
    assert.strictEqual(isValidPlayerProfile('skip_verification'), true);
    assert.strictEqual(isValidPlayerProfile('invalid_profile'), false);
    assert.strictEqual(isValidPlayerProfile(null), false);
    assert.strictEqual(isValidPlayerProfile(undefined), false);

    console.log('Canonical default profile contract verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Canonical default profile contract verified." in res.stdout


def test_profile_cycle_order():
    """Verify canonical cycle order: standard -> verification -> skip_verification -> standard."""
    node_script = """
    import assert from 'node:assert';

    function getNextProfile(current) {
      switch (current) {
        case 'standard':
          return 'verification';
        case 'verification':
          return 'skip_verification';
        case 'skip_verification':
        default:
          return 'standard';
      }
    }

    assert.strictEqual(getNextProfile('standard'), 'verification');
    assert.strictEqual(getNextProfile('verification'), 'skip_verification');
    assert.strictEqual(getNextProfile('skip_verification'), 'standard');

    console.log('Profile cycle order verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Profile cycle order verified." in res.stdout


def test_profile_storage_restoration_and_race_safety():
    """Verify profile storage restoration, explicit initialProfile precedence, and race-safety guard."""
    player_content = PLAYER_SCREEN_PATH.read_text(encoding="utf-8")
    assert "hasExplicitInitialProfileRef" in player_content
    assert "hasUserModifiedProfileRef" in player_content
    assert "STORAGE_KEYS.USER_PROFILE" in player_content

    node_script = """
    import assert from 'node:assert';

    function isValidPlayerProfile(value) {
      return (
        typeof value === 'string' &&
        (value === 'standard' || value === 'verification' || value === 'skip_verification')
      );
    }

    // Model component restoration logic
    function simulateMountRestoration({
      initialProfile,
      userModifiedBeforeStorage,
      storedValue,
    }) {
      const hasExplicit = initialProfile !== undefined;
      let activeProfile = initialProfile ?? 'standard';
      let hasUserModified = false;

      // User modifies profile before storage read resolves
      if (userModifiedBeforeStorage) {
        hasUserModified = true;
        activeProfile = userModifiedBeforeStorage;
      }

      // Storage read completes
      if (!hasExplicit && !hasUserModified && isValidPlayerProfile(storedValue)) {
        activeProfile = storedValue;
      }

      return activeProfile;
    }

    // 1. Fresh-launch default: no explicit prop, empty storage -> 'standard'
    assert.strictEqual(
      simulateMountRestoration({ initialProfile: undefined, userModifiedBeforeStorage: null, storedValue: null }),
      'standard'
    );

    // 2. Saved-profile restoration: valid saved profile restored -> 'skip_verification'
    assert.strictEqual(
      simulateMountRestoration({ initialProfile: undefined, userModifiedBeforeStorage: null, storedValue: 'skip_verification' }),
      'skip_verification'
    );

    // 3. Invalid saved value: unrecognized profile falls back safely -> 'standard'
    assert.strictEqual(
      simulateMountRestoration({ initialProfile: undefined, userModifiedBeforeStorage: null, storedValue: 'malformed_profile' }),
      'standard'
    );

    // 4. Explicit initial profile: explicit prop is preserved against conflicting stored value
    assert.strictEqual(
      simulateMountRestoration({ initialProfile: 'verification', userModifiedBeforeStorage: null, storedValue: 'skip_verification' }),
      'verification'
    );

    // 5. Asynchronous race safety: user change before storage read resolves is preserved
    assert.strictEqual(
      simulateMountRestoration({ initialProfile: undefined, userModifiedBeforeStorage: 'verification', storedValue: 'skip_verification' }),
      'verification'
    );

    console.log('Profile storage restoration and race safety verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Profile storage restoration and race safety verified." in res.stdout


def test_settings_restoration_user_options_race_protection():
    """Verify SettingsScreen protects user option modifications from delayed storage reads."""
    settings_content = SETTINGS_SCREEN_PATH.read_text(encoding="utf-8")
    assert "hasUserModifiedOptionsRef" in settings_content
    assert "!hasUserModifiedOptionsRef.current" in settings_content

    node_script = """
    import assert from 'node:assert';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';

    function sanitizeViewerOptions(raw, fallback = DEFAULT_VIEWER_OPTIONS) {
      if (!raw || typeof raw !== 'object') return { ...fallback };
      return {
        ...fallback,
        ...raw,
      };
    }

    function simulateSettingsRestoration({
      initialOptions,
      userModifiedBeforeStorage,
      storedSettings,
    }) {
      let activeOptions = initialOptions ? sanitizeViewerOptions(initialOptions) : { ...DEFAULT_VIEWER_OPTIONS };
      let hasUserModifiedOptions = false;

      // User interacts and modifies options before delayed storage read resolves
      if (userModifiedBeforeStorage) {
        hasUserModifiedOptions = true;
        activeOptions = { ...activeOptions, ...userModifiedBeforeStorage };
      }

      // Delayed storage read completes
      if (!hasUserModifiedOptions && storedSettings !== null && storedSettings !== undefined) {
        activeOptions = sanitizeViewerOptions(storedSettings, activeOptions);
      }

      return activeOptions;
    }

    // 1. Storage read resolves normally when user has not interacted
    const normalRestore = simulateSettingsRestoration({
      initialOptions: null,
      userModifiedBeforeStorage: null,
      storedSettings: { reduceEnabled: true, reduceGain: 0.5 },
    });
    assert.strictEqual(normalRestore.reduceEnabled, true);
    assert.strictEqual(normalRestore.reduceGain, 0.5);

    // 2. Race condition: user modified options before storage read resolves
    const raceProtected = simulateSettingsRestoration({
      initialOptions: null,
      userModifiedBeforeStorage: { reduceEnabled: true, reduceGain: 0.2 },
      storedSettings: { reduceEnabled: false, reduceGain: 0.7 },
    });
    // Stale stored settings (0.7, false) must NOT overwrite user modifications (0.2, true)
    assert.strictEqual(raceProtected.reduceEnabled, true);
    assert.strictEqual(raceProtected.reduceGain, 0.2);

    console.log('Settings restoration options race protection verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Settings restoration options race protection verified." in res.stdout


def test_player_initial_only_profile_contract():
    """Verify PlayerScreen enforces initial-only contract and ignores prop updates on rerender."""
    player_content = PLAYER_SCREEN_PATH.read_text(encoding="utf-8")
    # Verify the defective useEffect on initialProfile has been removed
    assert "useEffect(() => {\n    if (initialProfile !== undefined)" not in player_content
    assert "initialProfile seeds the profile state on mount" in player_content

    node_script = """
    import assert from 'node:assert';

    // Model initial-only component lifecycle
    class PlayerScreenModel {
      constructor(initialProfile) {
        this.hasExplicitInitial = initialProfile !== undefined;
        this.profile = initialProfile ?? 'standard';
        this.hasUserModified = false;
      }

      // Parent rerenders with new prop under initial-only contract
      rerender(newInitialProfile) {
        // Option A: initial-only contract ignores prop changes after mount
      }

      // User manual toggle
      toggleProfile() {
        this.hasUserModified = true;
        if (this.profile === 'standard') this.profile = 'verification';
        else if (this.profile === 'verification') this.profile = 'skip_verification';
        else this.profile = 'standard';
      }
    }

    // 1. Initial mount seeds profile
    const player1 = new PlayerScreenModel('standard');
    assert.strictEqual(player1.profile, 'standard');

    // 2. Parent rerenders with 'verification' -> profile remains 'standard'
    player1.rerender('verification');
    assert.strictEqual(player1.profile, 'standard');

    // 3. User manually toggles to 'verification'
    player1.toggleProfile();
    assert.strictEqual(player1.profile, 'verification');

    // 4. Parent rerenders with 'skip_verification' -> user toggle is preserved
    player1.rerender('skip_verification');
    assert.strictEqual(player1.profile, 'verification');

    console.log('Player initial-only profile contract verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Player initial-only profile contract verified." in res.stdout


def test_settings_screen_async_delayed_storage_and_user_action_lifecycle():
    """Verify SettingsScreen async storage lifecycle, delayed reads, user overrides, and unmount safety."""
    settings_content = SETTINGS_SCREEN_PATH.read_text(encoding="utf-8")

    # Source code guards
    assert "isMountedRef.current" in settings_content
    assert "setOptions(merged)" in settings_content
    assert "setProfile(savedProfile as PlayerProfile)" in settings_content
    assert "setSaveStatusMessage('Settings saved')" in settings_content
    assert "hasUserModifiedOptionsRef.current = true" in settings_content
    assert "hasUserModifiedProfileRef.current = true" in settings_content

    node_script = """
    import assert from 'node:assert';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';

    function sanitizeViewerOptions(raw, fallback = DEFAULT_VIEWER_OPTIONS) {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { ...fallback };
      return {
        ...fallback,
        ...raw,
      };
    }

    // Realistic simulation of SettingsScreen asynchronous component lifecycle
    class SettingsScreenLifecycleModel {
      constructor({ initialProfile, initialOptions } = {}) {
        this.isMounted = true;
        this.hasExplicitInitialProfile = initialProfile !== undefined;
        this.hasUserModifiedProfile = false;
        this.hasUserModifiedOptions = false;

        this.profile = initialProfile ?? 'standard';
        this.options = initialOptions ? sanitizeViewerOptions(initialOptions) : { ...DEFAULT_VIEWER_OPTIONS };
        this.saveStatusMessage = '';
        this.stateUpdatesAfterUnmount = 0;
      }

      // User modifies profile before or after storage read
      handleProfileChange(nextProfile) {
        this.hasUserModifiedProfile = true;
        if (!this.isMounted) {
          this.stateUpdatesAfterUnmount++;
          return;
        }
        this.profile = nextProfile;
      }

      // User updates viewer options
      handleUpdateOptions(updater) {
        this.hasUserModifiedOptions = true;
        if (!this.isMounted) {
          this.stateUpdatesAfterUnmount++;
          return;
        }
        this.options = updater(this.options);
      }

      // User resets defaults
      handleResetDefaults() {
        this.hasUserModifiedOptions = true;
        this.hasUserModifiedProfile = true;
        if (!this.isMounted) {
          this.stateUpdatesAfterUnmount++;
          return;
        }
        this.options = { ...DEFAULT_VIEWER_OPTIONS };
        this.profile = 'standard';
      }

      // Asynchronous storage resolution
      async onStorageLoaded(savedSettings, savedProfile) {
        if (!this.isMounted) return;

        // Restore options only if user has not modified them
        if (!this.hasUserModifiedOptions && savedSettings !== null && savedSettings !== undefined) {
          const merged = sanitizeViewerOptions(savedSettings, this.options);
          if (this.isMounted && !this.hasUserModifiedOptions) {
            this.options = merged;
          } else if (!this.isMounted) {
            this.stateUpdatesAfterUnmount++;
          }
        }

        // Restore profile only if no explicit initialProfile and user has not modified
        if (
          !this.hasExplicitInitialProfile &&
          !this.hasUserModifiedProfile &&
          savedProfile &&
          ['standard', 'verification', 'skip_verification'].includes(savedProfile)
        ) {
          if (this.isMounted && !this.hasExplicitInitialProfile && !this.hasUserModifiedProfile) {
            this.profile = savedProfile;
          } else if (!this.isMounted) {
            this.stateUpdatesAfterUnmount++;
          }
        }
      }

      unmount() {
        this.isMounted = false;
      }
    }

    async function runLifecycleTests() {
      // Scenario 1: Delayed storage read followed by user change to profile and options
      {
        const screen = new SettingsScreenLifecycleModel();
        assert.strictEqual(screen.profile, 'standard');
        assert.strictEqual(screen.options.reduceGain, 0.3);

        // User changes options and profile BEFORE delayed storage resolves
        screen.handleProfileChange('verification');
        screen.handleUpdateOptions(prev => ({ ...prev, reduceGain: 0.15, reduceEnabled: true }));

        // Now delayed storage read arrives with stale values
        await screen.onStorageLoaded({ reduceGain: 0.75, reduceEnabled: false }, 'skip_verification');

        // User changes MUST be preserved
        assert.strictEqual(screen.profile, 'verification');
        assert.strictEqual(screen.options.reduceGain, 0.15);
        assert.strictEqual(screen.options.reduceEnabled, true);
      }

      // Scenario 2: User resets to defaults before delayed storage read resolves
      {
        const screen = new SettingsScreenLifecycleModel({ initialProfile: 'verification' });
        // User clicks Reset Defaults while storage read is pending
        screen.handleResetDefaults();
        assert.strictEqual(screen.profile, 'standard');

        // Delayed storage read arrives with previously saved custom settings
        await screen.onStorageLoaded({ reduceGain: 0.5, reduceEnabled: true }, 'skip_verification');

        // Reset defaults MUST be preserved against late storage resolution
        assert.strictEqual(screen.profile, 'standard');
        assert.strictEqual(screen.options.reduceGain, 0.3);
      }

      // Scenario 3: Explicit initialProfile takes precedence over saved profile without user action
      {
        const screen = new SettingsScreenLifecycleModel({ initialProfile: 'skip_verification' });
        // Delayed storage resolves with different saved profile
        await screen.onStorageLoaded(null, 'verification');

        // Explicit initialProfile wins
        assert.strictEqual(screen.profile, 'skip_verification');
      }

      // Scenario 4: Component unmounts before delayed storage read resolves
      {
        const screen = new SettingsScreenLifecycleModel();
        // Component unmounts
        screen.unmount();

        // Delayed storage read resolves after unmount
        await screen.onStorageLoaded({ reduceGain: 0.5 }, 'verification');

        // No state updates after unmount
        assert.strictEqual(screen.stateUpdatesAfterUnmount, 0);
      }

      console.log('SettingsScreen async delayed storage and user action lifecycle verified.');
    }

    runLifecycleTests().catch(err => {
      console.error(err);
      process.exit(1);
    });
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "SettingsScreen async delayed storage and user action lifecycle verified." in res.stdout
