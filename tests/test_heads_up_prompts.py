"""Harness Part 2: Heads-up prompt configuration, schema, and timestamp preservation tests.

Person B scope:
- Configuration schema compliance and domain validation for `heads_up_prompts`
- Backward-compatible default handling for missing or partial configuration
- Rejection of invalid types, zero/negative lead times, NaN, infinity, and unknown fields
- Verification that cue packaging strictly preserves raw detection timestamps [start, end)
  without pre-padding, shifting, or synthesizing prompt cues
- Interface contract alignment for Person A runtime engine lookahead
"""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import jsonschema

from scripts.package_cues import merge_detections
from scripts.validate_config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_HEADS_UP_PROMPT_CONFIG,
    DEFAULT_SCHEMA_PATH,
    get_heads_up_prompt_config,
    load_config,
    load_schema,
    validate_config,
    validate_domain_rules,
    validate_schema_compliance,
)
from scripts.validate_index import load_index, validate_index


# ------------------------------------------------------------------------------
# 1. Configuration & Default Handling
# ------------------------------------------------------------------------------

def test_default_heads_up_prompt_config_values():
    """Verify default values meet Person A/B approved contract."""
    assert DEFAULT_HEADS_UP_PROMPT_CONFIG["enabled"] is False
    assert DEFAULT_HEADS_UP_PROMPT_CONFIG["lead_time_seconds"] == 5.0


def test_get_heads_up_prompt_config_missing_section():
    """Missing heads_up_prompts section returns default configuration."""
    cfg: Dict[str, Any] = {}
    resolved = get_heads_up_prompt_config(cfg)
    assert resolved == {"enabled": False, "lead_time_seconds": 5.0}


def test_get_heads_up_prompt_config_none_or_non_dict():
    """Non-dict heads_up_prompts section safely falls back to defaults."""
    assert get_heads_up_prompt_config({"heads_up_prompts": None}) == {
        "enabled": False,
        "lead_time_seconds": 5.0,
    }
    assert get_heads_up_prompt_config({"heads_up_prompts": "disabled"}) == {
        "enabled": False,
        "lead_time_seconds": 5.0,
    }


def test_get_heads_up_prompt_config_partial_enabled_only():
    """Partial config specifying only enabled preserves enabled and defaults lead time."""
    cfg = {"heads_up_prompts": {"enabled": True}}
    resolved = get_heads_up_prompt_config(cfg)
    assert resolved["enabled"] is True
    assert resolved["lead_time_seconds"] == 5.0


def test_get_heads_up_prompt_config_partial_lead_time_only():
    """Partial config specifying only lead_time preserves lead time and defaults enabled."""
    cfg = {"heads_up_prompts": {"lead_time_seconds": 12.5}}
    resolved = get_heads_up_prompt_config(cfg)
    assert resolved["enabled"] is False
    assert resolved["lead_time_seconds"] == 12.5


def test_get_heads_up_prompt_config_explicit_disabled():
    """Explicit enabled: false is preserved."""
    cfg = {"heads_up_prompts": {"enabled": False, "lead_time_seconds": 3.0}}
    resolved = get_heads_up_prompt_config(cfg)
    assert resolved["enabled"] is False
    assert resolved["lead_time_seconds"] == 3.0


def test_get_heads_up_prompt_config_invalid_lead_time_fallback():
    """Invalid lead times (<= 0, boolean, non-finite) safely fall back to default 5.0."""
    assert get_heads_up_prompt_config({"heads_up_prompts": {"lead_time_seconds": 0.0}})["lead_time_seconds"] == 5.0
    assert get_heads_up_prompt_config({"heads_up_prompts": {"lead_time_seconds": -5.0}})["lead_time_seconds"] == 5.0
    assert get_heads_up_prompt_config({"heads_up_prompts": {"lead_time_seconds": True}})["lead_time_seconds"] == 5.0
    assert get_heads_up_prompt_config({"heads_up_prompts": {"lead_time_seconds": float("nan")}})["lead_time_seconds"] == 5.0
    assert get_heads_up_prompt_config({"heads_up_prompts": {"lead_time_seconds": float("inf")}})["lead_time_seconds"] == 5.0


# ------------------------------------------------------------------------------
# 2. Schema Compliance & Domain Validation
# ------------------------------------------------------------------------------

def test_active_repo_config_validates():
    """The repository root config.yaml must pass full validation with heads_up_prompts."""
    is_valid, errors = validate_config(DEFAULT_CONFIG_PATH, DEFAULT_SCHEMA_PATH)
    assert is_valid, f"config.yaml validation failed: {errors}"
    assert errors == []

    config = load_config(DEFAULT_CONFIG_PATH)
    assert "heads_up_prompts" in config
    assert config["heads_up_prompts"]["enabled"] is False
    assert config["heads_up_prompts"]["lead_time_seconds"] == 5.0


def test_backward_compatibility_without_heads_up_prompts():
    """Existing configurations without heads_up_prompts section remain 100% valid."""
    config = load_config(DEFAULT_CONFIG_PATH)
    config_without = {k: v for k, v in config.items() if k != "heads_up_prompts"}

    schema = load_schema(DEFAULT_SCHEMA_PATH)
    schema_errors = validate_schema_compliance(config_without, schema)
    domain_errors = validate_domain_rules(config_without)

    assert schema_errors == []
    assert domain_errors == []

    resolved = get_heads_up_prompt_config(config_without)
    assert resolved == {"enabled": False, "lead_time_seconds": 5.0}


def test_schema_rejects_unknown_properties_in_heads_up_prompts():
    """additionalProperties: false must reject unknown fields under heads_up_prompts."""
    config = load_config(DEFAULT_CONFIG_PATH)
    bad_config = copy.deepcopy(config)
    bad_config["heads_up_prompts"]["extra_param"] = "unexpected"

    schema = load_schema(DEFAULT_SCHEMA_PATH)
    schema_errors = validate_schema_compliance(bad_config, schema)
    assert len(schema_errors) > 0
    assert any("extra_param" in err for err in schema_errors)


@pytest.mark.parametrize("invalid_lead_time", [0.0, -0.1, -5.0])
def test_schema_rejects_non_positive_lead_time(invalid_lead_time):
    """exclusiveMinimum: 0.0 rejects zero and negative values."""
    config = load_config(DEFAULT_CONFIG_PATH)
    bad_config = copy.deepcopy(config)
    bad_config["heads_up_prompts"]["lead_time_seconds"] = invalid_lead_time

    schema = load_schema(DEFAULT_SCHEMA_PATH)
    schema_errors = validate_schema_compliance(bad_config, schema)
    assert len(schema_errors) > 0
    assert any("lead_time_seconds" in err for err in schema_errors)


@pytest.mark.parametrize("invalid_lead_time", [0.0, -1.0, float("nan"), float("inf"), "5.0", True])
def test_domain_validation_rejects_invalid_lead_time(invalid_lead_time):
    """Domain validator strictly rejects non-positive, non-finite, and wrong-type lead times."""
    config = load_config(DEFAULT_CONFIG_PATH)
    bad_config = copy.deepcopy(config)
    bad_config["heads_up_prompts"]["lead_time_seconds"] = invalid_lead_time

    domain_errors = validate_domain_rules(bad_config)
    assert len(domain_errors) > 0
    assert any("lead_time_seconds" in err for err in domain_errors)


@pytest.mark.parametrize("invalid_enabled", ["false", 0, 1, None, [True]])
def test_validation_rejects_non_boolean_enabled(invalid_enabled):
    """Validation rejects non-boolean enabled values."""
    config = load_config(DEFAULT_CONFIG_PATH)
    bad_config = copy.deepcopy(config)
    bad_config["heads_up_prompts"]["enabled"] = invalid_enabled

    schema = load_schema(DEFAULT_SCHEMA_PATH)
    schema_errors = validate_schema_compliance(bad_config, schema)
    domain_errors = validate_domain_rules(bad_config)

    assert len(schema_errors) > 0 or len(domain_errors) > 0


# ------------------------------------------------------------------------------
# 3. Cue Timestamp Preservation Verification
# ------------------------------------------------------------------------------

def test_packaging_preserves_exact_detection_timestamps():
    """Verify cue packaging does not shift, pre-pad, or mutate detection timestamps.

    A detection at [10.0, 12.0) with a 5.0s prompt lead time must remain [10.0, 12.0)
    in the packaged cue. Lead time is exclusively player-side lookahead.
    """
    raw_detections = [
        {
            "start": 10.0,
            "end": 12.0,
            "trigger": "loud_spike",
            "score": 0.95,
            "source": "detector_audio_peak",
        },
        {
            "start": 25.5,
            "end": 28.0,
            "trigger": "profanity",
            "score": 0.99,
            "source": "detector_subtitle_profanity",
        },
    ]

    merged = merge_detections(raw_detections, default_merge_gap=0.5, max_segment_duration=10.0)

    assert len(merged) == 2
    # Verify exact preservation of start and end
    assert merged[0]["start"] == 10.0
    assert merged[0]["end"] == 12.0
    assert merged[1]["start"] == 25.5
    assert merged[1]["end"] == 28.0


def test_packaging_preserves_half_open_interval_convention():
    """Verify merged intervals satisfy start < end (half-open [start, end))."""
    raw_detections = [
        {
            "start": 5.0,
            "end": 7.0,
            "trigger": "flashing",
            "score": 0.88,
            "source": "detector_video_flash",
        }
    ]

    merged = merge_detections(raw_detections)
    assert len(merged) == 1
    assert merged[0]["start"] == 5.0
    assert merged[0]["end"] == 7.0
    assert merged[0]["start"] < merged[0]["end"]


def test_root_index_fixture_unchanged_and_valid():
    """Ensure root data/index.json fixture remains valid and unchanged."""
    index_path = Path("data/index.json")
    schema_path = Path("schema/index.schema.json")
    is_valid, errors = validate_index(index_path, schema_path)
    assert is_valid, f"data/index.json failed validation: {errors}"
    assert errors == []

    idx = load_index(index_path)
    assert idx["timeline"]["timestamp_unit"] == "seconds"
    assert idx["timeline"]["interval_convention"] == "half_open"
    # Ensure no synthetic prompt cues leaked into index.json
    for cue in idx["cues"]:
        assert cue["trigger"] != "heads_up_prompt"
        assert cue["trigger"] != "prompt"


# ------------------------------------------------------------------------------
# 4. Engine Interface & Person A Dependency Boundaries
# ------------------------------------------------------------------------------

def test_typescript_resolve_heads_up_prompt_options_helper():
    """Verify Person B TypeScript types and resolveHeadsUpPromptOptions helper via Node."""
    node_script = """
    import { resolveHeadsUpPromptOptions, DEFAULT_HEADS_UP_PROMPT_OPTIONS } from './src/engine/types.ts';
    import assert from 'node:assert';

    // Default resolution
    const def = resolveHeadsUpPromptOptions();
    assert.strictEqual(def.enabled, false);
    assert.strictEqual(def.leadTimeSeconds, 5.0);

    // Partial with snake_case
    const r1 = resolveHeadsUpPromptOptions({ enabled: true, lead_time_seconds: 7.5 });
    assert.strictEqual(r1.enabled, true);
    assert.strictEqual(r1.leadTimeSeconds, 7.5);

    // Partial with camelCase
    const r2 = resolveHeadsUpPromptOptions({ enabled: false, leadTimeSeconds: 2.0 });
    assert.strictEqual(r2.enabled, false);
    assert.strictEqual(r2.leadTimeSeconds, 2.0);

    // Nested wrapper config (as parsed from config.yaml)
    const r3 = resolveHeadsUpPromptOptions({ heads_up_prompts: { enabled: true, lead_time_seconds: 10.0 } });
    assert.strictEqual(r3.enabled, true);
    assert.strictEqual(r3.leadTimeSeconds, 10.0);

    // Fallback on invalid lead time (<= 0 or NaN)
    const r4 = resolveHeadsUpPromptOptions({ enabled: true, lead_time_seconds: -1 });
    assert.strictEqual(r4.enabled, true);
    assert.strictEqual(r4.leadTimeSeconds, 5.0);

    console.log('TypeScript HeadsUpPromptOptions tests passed');
    """

    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node execution failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert "TypeScript HeadsUpPromptOptions tests passed" in res.stdout


def test_heads_up_prompt_lead_time_window_specification():
    """Contract verification for Person A runtime engine lookahead window.

    The agreed prompt window is [cue.start - leadTimeSeconds, cue.start).
    This test verifies the boundary calculations that Person A's engine must implement.
    """
    cue_start = 10.0
    cue_end = 12.0
    lead_time = 5.0

    prompt_window_start = cue_start - lead_time
    prompt_window_end = cue_start

    assert prompt_window_start == 5.0
    assert prompt_window_end == 10.0

    # Before prompt window
    t_before = 4.99
    is_in_prompt_window_before = prompt_window_start <= t_before < prompt_window_end
    assert not is_in_prompt_window_before

    # Inside prompt window
    t_prompt = 7.5
    is_in_prompt_window = prompt_window_start <= t_prompt < prompt_window_end
    assert is_in_prompt_window

    # Inside cue itself (cue active, prompt window expired)
    t_cue = 10.5
    is_in_cue = cue_start <= t_cue < cue_end
    is_in_prompt_window_during = prompt_window_start <= t_cue < prompt_window_end
    assert is_in_cue
    assert not is_in_prompt_window_during


def test_resolve_actions_pure_deterministic_heads_up_prompts():
    """Verify resolveActions computes promptCue as a pure, deterministic calculation.

    Covers:
    1. Prompts disabled by default (promptCue: null)
    2. Prompt eligibility at window start (time == windowStart)
    3. No prompt at cue.start (time == cue.start)
    4. No prompt before window (time < windowStart)
    5. Disabled-category filtering (options.disabledCategories)
    6. Review-policy filtering (needs_review unconfirmed skip cues mapped by applyReviewPolicy)
    7. Deterministic tie-breaking on multiple eligible cues (earliest start, then cue.id)
    """
    node_script = """
    import { resolveActions } from './src/engine/resolveActions.ts';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';
    import assert from 'node:assert';

    const cues = [
      { id: 'cue-b', start: 10.0, end: 12.0, source: 'visual', action: 'hide', trigger: 'flashing' },
      { id: 'cue-a', start: 10.0, end: 12.0, source: 'audio', action: 'mute', trigger: 'loud_spike' },
      { id: 'cue-review-skip', start: 20.0, end: 25.0, source: 'visual', action: 'skip', trigger: 'jump_scare', status: 'needs_review' },
      { id: 'cue-spider', start: 30.0, end: 35.0, source: 'visual', action: 'hide', trigger: 'spider' },
    ];

    // 1. Prompts disabled by default (missing headsUpPrompts or enabled: false) -> promptCue: null
    const resDefault = resolveActions(5.0, cues, DEFAULT_VIEWER_OPTIONS);
    assert.strictEqual(resDefault.promptCue, null, 'Default options must produce promptCue: null');

    const resDisabled = resolveActions(5.0, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
      headsUpPrompts: { enabled: false, leadTimeSeconds: 5.0 },
    });
    assert.strictEqual(resDisabled.promptCue, null, 'Explicitly disabled prompts must produce promptCue: null');

    const enabledOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
    };

    // 2. Prompt eligibility at window start (time == windowStart, e.g. 10.0 - 5.0 = 5.0)
    const resAtStart = resolveActions(5.0, cues, enabledOptions);
    assert.ok(resAtStart.promptCue !== null, 'Cue must be eligible at windowStart');

    // 3. No prompt at cue.start (time == cue.start, e.g. time = 10.0)
    const resAtCueStart = resolveActions(10.0, cues, enabledOptions);
    assert.strictEqual(resAtCueStart.promptCue, null, 'Prompt must not be active at cue.start');

    // 4. No prompt before window (time < windowStart, e.g. time = 4.99)
    const resBeforeWindow = resolveActions(4.99, cues, enabledOptions);
    assert.strictEqual(resBeforeWindow.promptCue, null, 'Prompt must not be active before windowStart');

    // 5. Disabled-category filtering: if 'spider' is disabled, cue-spider must not prompt
    const optsSpiderDisabled = {
      ...enabledOptions,
      disabledCategories: ['spider'],
    };
    const resSpiderDisabled = resolveActions(27.0, cues, optsSpiderDisabled);
    assert.strictEqual(resSpiderDisabled.promptCue, null, 'Disabled category trigger must not prompt');

    const resSpiderEnabled = resolveActions(27.0, cues, enabledOptions);
    assert.strictEqual(resSpiderEnabled.promptCue?.id, 'cue-spider', 'Enabled category trigger must prompt');

    // 6. Review-policy filtering:
    // cue-review-skip is 'needs_review' with action 'skip'.
    // When confirmReviewSkip is false, applyReviewPolicy maps visual skip -> hide.
    const resUnconfirmedReview = resolveActions(16.0, cues, {
      ...enabledOptions,
      confirmReviewSkip: false,
    });
    assert.strictEqual(resUnconfirmedReview.promptCue?.id, 'cue-review-skip');
    assert.strictEqual(resUnconfirmedReview.promptCue?.action, 'hide', 'Review policy must downgrade unconfirmed skip action on prompt cue');

    const resConfirmedReview = resolveActions(16.0, cues, {
      ...enabledOptions,
      confirmReviewSkip: true,
    });
    assert.strictEqual(resConfirmedReview.promptCue?.action, 'skip', 'Confirmed review preserves skip action');

    // 7. Deterministic tie-breaking:
    // Both cue-a and cue-b have start = 10.0.
    // Alphabetical ID tie-breaking selects 'cue-a' before 'cue-b'.
    const resTie = resolveActions(6.0, cues, enabledOptions);
    assert.strictEqual(resTie.promptCue?.id, 'cue-a', 'Earliest start with alphabetical tie-breaker must select cue-a');

    // If an earlier cue exists, start time takes precedence over ID
    const earlierCues = [
      { id: 'cue-z', start: 9.0, end: 11.0, source: 'visual', action: 'hide', trigger: 'flashing' },
      ...cues,
    ];
    const resEarlier = resolveActions(5.0, earlierCues, enabledOptions);
    assert.strictEqual(resEarlier.promptCue?.id, 'cue-z', 'Earlier start time must take precedence over ID ordering');

    console.log('resolveActions pure deterministic prompt tests passed successfully.');
    """

    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node test execution failed:\\nSTDOUT:\\n{res.stdout}\\nSTDERR:\\n{res.stderr}"
    assert "resolveActions pure deterministic prompt tests passed successfully." in res.stdout


# ------------------------------------------------------------------------------
# 5. PlaybackState Runtime Actions & Prompt Integration via Node Runner
# ------------------------------------------------------------------------------

def test_playback_state_runtime_actions_and_prompt_lifecycle_integration():
    """Execute end-to-end integration tests on Person A's PlaybackState with Person B prompt extensions."""
    node_test_script = """
    import { PlaybackState } from './src/engine/playbackState.ts';
    import { resolveActions } from './src/engine/resolveActions.ts';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';
    import assert from 'node:assert';

    const testCues = [
      {
        id: 'cue-visual-hide',
        start: 10.0,
        end: 15.0,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
      },
      {
        id: 'cue-audio-reduce',
        start: 20.0,
        end: 30.0,
        source: 'audio',
        action: 'reduce',
        gain: 0.4,
        trigger: 'scream',
      },
      {
        id: 'cue-audio-mute',
        start: 24.0,
        end: 27.0,
        source: 'audio',
        action: 'mute',
        trigger: 'loud_spike',
      },
      {
        id: 'cue-prompt-target',
        start: 40.0,
        end: 45.0,
        source: 'visual',
        action: 'hide',
        trigger: 'spider',
      },
      {
        id: 'cue-chained-skip',
        start: 44.0,
        end: 48.0,
        source: 'visual',
        action: 'hide',
        trigger: 'spider',
      },
      {
        id: 'cue-skip-primary',
        start: 70.0,
        end: 75.0,
        source: 'visual',
        action: 'skip',
        trigger: 'jump_scare',
      },
      {
        id: 'cue-skip-chained',
        start: 74.0,
        end: 78.0,
        source: 'visual',
        action: 'skip',
        trigger: 'jump_scare',
      },
    ];

    const baseOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      hideEnabled: true,
      muteEnabled: true,
      reduceEnabled: true,
      skipEnabled: true,
      headsUpPrompts: {
        enabled: true,
        leadTimeSeconds: 5.0,
      },
    };

    const state = new PlaybackState();

    // -------------------------------------------------------------------------
    // A. Runtime Action Boundaries & Precedence
    // -------------------------------------------------------------------------
    // 1. Before cue-visual-hide (t=9.9)
    let a = state.resolveAt(9.9, testCues, baseOptions);
    assert.strictEqual(a.hide, false);
    assert.strictEqual(a.activeCueIds.includes('cue-visual-hide'), false);

    // 2. Exactly at start of cue-visual-hide (t=10.0)
    a = state.resolveAt(10.0, testCues, baseOptions);
    assert.strictEqual(a.hide, true);
    assert.strictEqual(a.activeCueIds.includes('cue-visual-hide'), true);

    // 3. At end of cue-visual-hide (t=15.0) -> restored
    a = state.resolveAt(15.0, testCues, baseOptions);
    assert.strictEqual(a.hide, false);

    // 4. Overlap & Precedence: reduce (t=22.0)
    a = state.resolveAt(22.0, testCues, baseOptions);
    assert.strictEqual(a.soundGain, 0.4);

    // 5. Mute overrides reduce (t=25.0)
    a = state.resolveAt(25.0, testCues, baseOptions);
    assert.strictEqual(a.soundGain, 0.0);

    // 6. Mute ends, reduce still active (t=28.0) -> restores to 0.4, NOT 1.0
    a = state.resolveAt(28.0, testCues, baseOptions);
    assert.strictEqual(a.soundGain, 0.4);

    // 7. Reduce ends (t=30.0) -> restores to 1.0
    a = state.resolveAt(30.0, testCues, baseOptions);
    assert.strictEqual(a.soundGain, 1.0);

    // -------------------------------------------------------------------------
    // B. Heads-Up Prompt Timing & Lifecycle
    // Target cue starts at t=40.0, lead_time=5.0 -> window is [35.0, 40.0)
    // -------------------------------------------------------------------------
    // 1. Disabled prompts do not show
    const disabledOptions = { ...baseOptions, headsUpPrompts: { enabled: false, leadTimeSeconds: 5.0 } };
    assert.strictEqual(state.resolveAt(36.0, testCues, disabledOptions).promptCue, null);

    // 2. Before window (t=34.9)
    assert.strictEqual(state.resolveAt(34.9, testCues, baseOptions).promptCue, null);

    // 3. Exactly at window start (t=35.0)
    let p = state.resolveAt(35.0, testCues, baseOptions).promptCue;
    assert.strictEqual(p?.id, 'cue-prompt-target');

    // 4. During window (t=38.0)
    p = state.resolveAt(38.0, testCues, baseOptions).promptCue;
    assert.strictEqual(p?.id, 'cue-prompt-target');

    // 5. At cue start (t=40.0) -> cue-prompt-target expired, and upcoming cue-chained-skip (start=44.0) prompts
    a = state.resolveAt(40.0, testCues, baseOptions);
    assert.strictEqual(a.promptCue?.id, 'cue-chained-skip');
    assert.strictEqual(a.hide, true);

    // -------------------------------------------------------------------------
    // C. Continue (Acknowledge) & Forward Suppression
    // -------------------------------------------------------------------------
    state.reset();
    assert.strictEqual(state.resolveAt(37.0, testCues, baseOptions).promptCue?.id, 'cue-prompt-target');

    // User chooses Continue
    state.acknowledgePrompt('cue-prompt-target');
    assert.strictEqual(state.isPromptAcknowledged('cue-prompt-target'), true);

    // Forward playback at t=38.0: cue-prompt-target is suppressed, and cue-chained-skip window hasn't started yet (< 39.0)
    assert.strictEqual(state.resolveAt(38.0, testCues, baseOptions).promptCue, null);

    // At t=41.0: cue-prompt-target is active and does NOT prompt; upcoming cue-chained-skip (start=44.0) prompts
    assert.strictEqual(state.resolveAt(41.0, testCues, baseOptions).promptCue?.id, 'cue-chained-skip');

    // Acknowledging cue-chained-skip dismisses it as well
    state.acknowledgePrompt('cue-chained-skip');
    assert.strictEqual(state.resolveAt(41.0, testCues, baseOptions).promptCue, null);

    // -------------------------------------------------------------------------
    // D. Rewind Re-arming
    // -------------------------------------------------------------------------
    // Rewind inside window (t=36.0) does NOT re-arm
    state.seek(36.0, testCues, baseOptions);
    assert.strictEqual(state.resolveAt(37.0, testCues, baseOptions).promptCue, null);

    // Rewind before window (t=34.0 < 40.0 - 5.0) RE-ARMS the cue!
    state.seek(34.0, testCues, baseOptions);
    assert.strictEqual(state.isPromptAcknowledged('cue-prompt-target'), false);

    // Playing forward back into window triggers prompt again!
    assert.strictEqual(state.resolveAt(36.0, testCues, baseOptions).promptCue?.id, 'cue-prompt-target');

    // -------------------------------------------------------------------------
    // E. Skip from Prompt with Chaining & Recap
    // -------------------------------------------------------------------------
    // 1. Non-skip cue (cue-prompt-target has action: 'hide') cannot be skipped
    state.currentTime = 36.0;
    const nonSkipCue = testCues.find(c => c.id === 'cue-prompt-target');
    const blockedDest = state.handlePromptSkip(nonSkipCue, testCues, 100.0, baseOptions);
    assert.strictEqual(blockedDest, 36.0, 'Non-skip cue must not be skipped');

    // 2. Eligible skip cue (cue-skip-primary [70.0, 75.0)) chains with cue-skip-chained [74.0, 78.0) -> 78.0
    const cueToSkip = testCues.find(c => c.id === 'cue-skip-primary');
    const destination = state.handlePromptSkip(cueToSkip, testCues, 100.0, baseOptions);
    assert.strictEqual(destination, 78.0);
    assert.strictEqual(state.isPromptAcknowledged('cue-skip-primary'), true);
    assert.strictEqual(state.isPromptAcknowledged('cue-skip-chained'), true);

    // Verify session recap contains skipped applied action
    const recap = state.getAppliedActions();
    const skipAction = recap.find(r => r.cueId === 'cue-skip-primary' && r.appliedEffect === 'skipped');
    assert.ok(skipAction, 'Recap must contain skipped applied action');
    assert.strictEqual(skipAction.requestedAction, 'skip');

    // Seeking to destination does not trigger prompt loop
    state.seek(destination, testCues, baseOptions);
    const destActions = state.resolveAt(destination, testCues, baseOptions);
    assert.strictEqual(destActions.promptCue, null);

    // Clamping to mediaDuration: if video is only 76.0s long, destination clamps to 76.0s
    state.reset();
    const clampedDest = state.handlePromptSkip(cueToSkip, testCues, 76.0, baseOptions);
    assert.strictEqual(clampedDest, 76.0, 'handlePromptSkip destination must be clamped to mediaDuration');

    // Review policy: unconfirmed needs_review cue whose skip action was downgraded cannot skip
    const reviewSkipCue = {
      id: 'cue-review-skip-test',
      start: 50.0,
      end: 55.0,
      source: 'visual',
      action: 'skip',
      status: 'needs_review',
      trigger: 'spider',
    };
    state.currentTime = 49.0;
    const noConfirmOptions = { ...baseOptions, confirmReviewSkip: false };
    const unconfirmedDest = state.handlePromptSkip(reviewSkipCue, [reviewSkipCue], 60.0, noConfirmOptions);
    assert.strictEqual(unconfirmedDest, 49.0, 'Unconfirmed needs_review cue cannot skip when confirmReviewSkip is false');

    // -------------------------------------------------------------------------
    // F. Disabled Trigger Categories
    // -------------------------------------------------------------------------
    state.reset();
    const filteredOptions = { ...baseOptions, disabledCategories: ['spider'] };
    assert.strictEqual(state.resolveAt(36.0, testCues, filteredOptions).promptCue, null);
    assert.strictEqual(state.resolveAt(41.0, testCues, filteredOptions).hide, false);

    console.log('PlaybackState Runtime Actions & Heads-Up Prompt tests passed successfully.');
    """

    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_test_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node integration test failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert "PlaybackState Runtime Actions & Heads-Up Prompt tests passed successfully." in res.stdout


def test_playback_state_skip_chaining_policy_enforcement():
    """Verify PlaybackState.handlePromptSkip enforces genuine skip eligibility and chaining policies.

    Covers:
    - Overlapping eligible skip cues chain correctly.
    - Contiguous eligible skip cues chain correctly.
    - A warning cue does not extend a skip.
    - A non-skip cue does not extend a skip.
    - A disabled trigger does not extend a skip.
    - An unconfirmed needs_review cue cannot be skipped indirectly through chaining.
    - Chaining terminates and final destination remains clamped to media duration.
    - Existing recap and loop-protection behavior remains correct.
    """
    node_test_script = """
    import { PlaybackState } from './src/engine/playbackState.ts';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';
    import assert from 'node:assert';

    const baseOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      skipEnabled: true,
      hideEnabled: true,
      muteEnabled: true,
      headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
    };

    // 1. Overlapping eligible skip cues chain correctly: [10, 15) and [13, 18) -> 18.0
    {
      const state = new PlaybackState();
      const cues = [
        { id: 'skip-1', start: 10.0, end: 15.0, action: 'skip', trigger: 'jump_scare' },
        { id: 'skip-2', start: 13.0, end: 18.0, action: 'skip', trigger: 'jump_scare' },
      ];
      const dest = state.handlePromptSkip(cues[0], cues, 100.0, baseOptions);
      assert.strictEqual(dest, 18.0, 'Overlapping skip cues must chain to 18.0');
      assert.strictEqual(state.isPromptAcknowledged('skip-1'), true);
      assert.strictEqual(state.isPromptAcknowledged('skip-2'), true);
    }

    // 2. Contiguous eligible skip cues chain correctly: [10, 15) and [15, 20) -> 20.0
    {
      const state = new PlaybackState();
      const cues = [
        { id: 'skip-1', start: 10.0, end: 15.0, action: 'skip', trigger: 'jump_scare' },
        { id: 'skip-contiguous', start: 15.0, end: 20.0, action: 'skip', trigger: 'jump_scare' },
      ];
      const dest = state.handlePromptSkip(cues[0], cues, 100.0, baseOptions);
      assert.strictEqual(dest, 20.0, 'Contiguous skip cues must chain to 20.0');
      assert.strictEqual(state.isPromptAcknowledged('skip-contiguous'), true);
    }

    // 3. A warning cue does NOT extend a skip: [10, 15) skip and [14, 22) warn -> destination stays 15.0
    {
      const state = new PlaybackState();
      const cues = [
        { id: 'skip-1', start: 10.0, end: 15.0, action: 'skip', trigger: 'jump_scare' },
        { id: 'warn-cue', start: 14.0, end: 22.0, action: 'warn', trigger: 'loud_spike' },
      ];
      const dest = state.handlePromptSkip(cues[0], cues, 100.0, baseOptions);
      assert.strictEqual(dest, 15.0, 'Warning cue must not extend skip destination');
      assert.strictEqual(state.isPromptAcknowledged('warn-cue'), false, 'Warning cue must not be acknowledged by skip');
    }

    // 4. A non-skip cue (hide/mute/reduce) does NOT extend a skip: [10, 15) skip and [14, 25) hide -> destination stays 15.0
    {
      const state = new PlaybackState();
      const cues = [
        { id: 'skip-1', start: 10.0, end: 15.0, action: 'skip', trigger: 'jump_scare' },
        { id: 'hide-cue', start: 14.0, end: 25.0, action: 'hide', trigger: 'spider' },
        { id: 'mute-cue', start: 14.0, end: 26.0, action: 'mute', trigger: 'loud_spike' },
      ];
      const dest = state.handlePromptSkip(cues[0], cues, 100.0, baseOptions);
      assert.strictEqual(dest, 15.0, 'Non-skip cues must not extend skip destination');
      assert.strictEqual(state.isPromptAcknowledged('hide-cue'), false);
      assert.strictEqual(state.isPromptAcknowledged('mute-cue'), false);
    }

    // 5. A disabled trigger does NOT extend a skip: [10, 15) skip (jump_scare) and [14, 24) skip (spider)
    // with disabledCategories: ['spider'] -> destination stays 15.0
    {
      const state = new PlaybackState();
      const cues = [
        { id: 'skip-1', start: 10.0, end: 15.0, action: 'skip', trigger: 'jump_scare' },
        { id: 'skip-spider', start: 14.0, end: 24.0, action: 'skip', trigger: 'spider' },
      ];
      const opts = { ...baseOptions, disabledCategories: ['spider'] };
      const dest = state.handlePromptSkip(cues[0], cues, 100.0, opts);
      assert.strictEqual(dest, 15.0, 'Disabled trigger must not extend skip destination');
      assert.strictEqual(state.isPromptAcknowledged('skip-spider'), false);
    }

    // 6. An unconfirmed needs_review cue cannot be skipped indirectly through chaining:
    // [10, 15) skip and [14, 25) skip (status: 'needs_review') with confirmReviewSkip: false
    // -> destination stays 15.0
    {
      const state = new PlaybackState();
      const cues = [
        { id: 'skip-1', start: 10.0, end: 15.0, action: 'skip', trigger: 'jump_scare' },
        { id: 'skip-review', start: 14.0, end: 25.0, action: 'skip', trigger: 'gunshot', status: 'needs_review' },
      ];
      const noConfirmOpts = { ...baseOptions, confirmReviewSkip: false };
      const dest = state.handlePromptSkip(cues[0], cues, 100.0, noConfirmOpts);
      assert.strictEqual(dest, 15.0, 'Unconfirmed needs_review cue must not extend skip destination');
      assert.strictEqual(state.isPromptAcknowledged('skip-review'), false);

      // But when confirmReviewSkip: true, it chains!
      state.reset();
      const confirmOpts = { ...baseOptions, confirmReviewSkip: true };
      const destConfirmed = state.handlePromptSkip(cues[0], cues, 100.0, confirmOpts);
      assert.strictEqual(destConfirmed, 25.0, 'Confirmed needs_review cue chains correctly');
      assert.strictEqual(state.isPromptAcknowledged('skip-review'), true);
    }

    // 7. Chaining terminates and final destination remains clamped to mediaDuration:
    // [10, 15), [14, 20), [19, 30) with mediaDuration: 22.0 -> destination is 22.0
    {
      const state = new PlaybackState();
      const cues = [
        { id: 's1', start: 10.0, end: 15.0, action: 'skip', trigger: 'jump_scare' },
        { id: 's2', start: 14.0, end: 20.0, action: 'skip', trigger: 'jump_scare' },
        { id: 's3', start: 19.0, end: 30.0, action: 'skip', trigger: 'jump_scare' },
      ];
      const dest = state.handlePromptSkip(cues[0], cues, 22.0, baseOptions);
      assert.strictEqual(dest, 22.0, 'Chaining destination must be clamped to mediaDuration');
    }

    // 8. Recap and loop-protection:
    {
      const state = new PlaybackState();
      const cues = [
        { id: 's1', start: 10.0, end: 15.0, action: 'skip', trigger: 'jump_scare' },
        { id: 's2', start: 13.0, end: 18.0, action: 'skip', trigger: 'jump_scare' },
      ];
      const dest = state.handlePromptSkip(cues[0], cues, 100.0, baseOptions);
      assert.strictEqual(dest, 18.0);
      const applied = state.getAppliedActions();
      assert.strictEqual(applied.length, 1);
      assert.strictEqual(applied[0].cueId, 's1');
      assert.strictEqual(applied[0].appliedEffect, 'skipped');
      assert.strictEqual(applied[0].requestedAction, 'skip');
      assert.strictEqual(applied[0].start, 10.0);
      assert.strictEqual(applied[0].end, 18.0);

      // Re-invoking handlePromptSkip on s1 returns currentTime (already handled / loop protected)
      state.currentTime = 18.0;
      const reSkipDest = state.handlePromptSkip(cues[0], cues, 100.0, baseOptions);
      assert.strictEqual(reSkipDest, 18.0, 'Re-skipping already handled cue must not loop');
    }

    console.log('Skip-chaining policy enforcement tests passed successfully.');
    """

    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_test_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node test execution failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert "Skip-chaining policy enforcement tests passed successfully." in res.stdout
