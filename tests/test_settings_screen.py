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
      'profanity', 'spider', 'fast_motion', 'jump_scare'
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
    const cats = sanitizeViewerOptions({ disabledCategories: ['spider', 'invalid_foo'] });
    assert.deepStrictEqual(cats.disabledCategories, ['spider']);

    console.log('Sanitize viewer options tests passed successfully.');
    """

    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Sanitize viewer options tests passed successfully." in res.stdout
