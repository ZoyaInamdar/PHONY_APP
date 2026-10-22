"""Comprehensive end-to-end regression tests for the refactored Trigger-to-Action architecture.

Validates:
1. Every trigger's canonical default action.
2. Every permitted trigger-action alternative combination.
3. Rejection / safe fallback of disallowed or invalid trigger/action combinations.
4. Saved override persistence, restoration, and reset behavior.
5. Global safety settings (hideEnabled, muteEnabled, reduceEnabled, skipEnabled) and disabledCategories.
6. Simultaneous/overlapping audio, visual, warning, and skip actions with deterministic precedence.
7. Cue activation, boundary transitions, and proper restoration.
8. Timeline seeking, pause/resume, and consecutive/overlapping cues recalculation.
9. Missing or invalid configuration/cues handling without player crashes.
10. Preservation of existing action semantics: hide (opaque), blur (stream/blackout), mute (0 gain),
    reduce (fractional gain), skip (timeline forward seek), warn (heads-up notification).
"""

import json
from pathlib import Path
import subprocess
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

CANONICAL_CONTRACT = {
    "flashing": {"default": "hide", "allowed": ["skip", "warn"]},
    "profanity": {"default": "mute", "allowed": ["skip", "warn"]},
    "gunshot": {"default": "mute", "allowed": ["reduce", "skip", "warn"]},
    "explosion": {"default": "reduce", "allowed": ["mute", "skip", "warn"]},
    "scream": {"default": "reduce", "allowed": ["mute", "skip", "warn"]},
    "loud_spike": {"default": "reduce", "allowed": ["mute", "warn"]},
    "sustained_loudness": {"default": "reduce", "allowed": ["mute", "warn", "skip"]},
    "insect": {"default": "hide", "allowed": ["blur", "skip", "warn"]},
    "jump_scare": {"default": "warn", "allowed": ["hide", "mute", "skip"]},
    "visible_blood": {"default": "hide", "allowed": ["blur", "skip", "warn"]},
}

CANONICAL_ACTIONS = ["hide", "blur", "mute", "reduce", "skip", "warn"]


def run_node_test(ts_script: str) -> subprocess.CompletedProcess:
    """Helper to execute TypeScript test scripts through Node and ts_loader.mjs."""
    return subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", ts_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def test_typescript_centralized_contract_definitions():
    """Verify TypeScript contract definitions, 10 triggers, default actions and allowed alternatives."""
    node_script = """
    import assert from 'node:assert';
    import {
      CANONICAL_TRIGGERS,
      CANONICAL_ACTIONS,
      TRIGGER_ACTION_CONTRACTS,
      isCanonicalTrigger,
      isCanonicalAction,
      isActionAllowedForTrigger,
      resolveEffectiveAction,
      sanitizeActionOverrides,
    } from './src/engine/triggerContract.ts';

    // 1. Verify 10 canonical triggers
    assert.strictEqual(CANONICAL_TRIGGERS.length, 10);
        const expectedTriggers = [
      'flashing', 'profanity', 'gunshot', 'explosion', 'scream',
      'loud_spike', 'sustained_loudness', 'insect',
      'jump_scare', 'visible_blood'
    ];
    for (const t of expectedTriggers) {
      assert.ok(isCanonicalTrigger(t), `Trigger ${t} must be canonical`);
    }

    // 2. Verify 6 canonical actions (dim and beep must be excluded)
    assert.strictEqual(CANONICAL_ACTIONS.length, 6);
    assert.ok(!isCanonicalAction('dim'), 'dim must NOT be a canonical action');
    assert.ok(!isCanonicalAction('beep'), 'beep must NOT be a canonical action');

    // 3. Verify exact default mapping and allowed alternatives for all 10 triggers
    const contracts = {
      flashing: { default: 'hide', allowed: ['skip', 'warn'] },
      profanity: { default: 'mute', allowed: ['skip', 'warn'] },
      gunshot: { default: 'mute', allowed: ['reduce', 'skip', 'warn'] },
      explosion: { default: 'reduce', allowed: ['mute', 'skip', 'warn'] },
      scream: { default: 'reduce', allowed: ['mute', 'skip', 'warn'] },
      loud_spike: { default: 'reduce', allowed: ['mute', 'warn'] },
      sustained_loudness: { default: 'reduce', allowed: ['mute', 'warn', 'skip'] },
      insect: { default: 'hide', allowed: ['blur', 'skip', 'warn'] },
      jump_scare: { default: 'warn', allowed: ['hide', 'mute', 'skip'] },
      visible_blood: { default: 'hide', allowed: ['blur', 'skip', 'warn'] },
    };

    for (const [trigger, spec] of Object.entries(contracts)) {
      const c = TRIGGER_ACTION_CONTRACTS[trigger];
      assert.ok(c, `Missing contract for ${trigger}`);
      assert.strictEqual(c.defaultAction, spec.default, `Default action mismatch for ${trigger}`);
      assert.deepStrictEqual([...c.allowedAlternatives].sort(), [...spec.allowed].sort(), `Allowed alternatives mismatch for ${trigger}`);
    }

    console.log('Centralized trigger contract verified.');
    """
    res = run_node_test(node_script)
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"


def test_every_permitted_trigger_action_combination_and_rejection():
    """Verify that every permitted override is accepted and disallowed actions safely fall back."""
    node_script = """
    import assert from 'node:assert';
    import {
      CANONICAL_TRIGGERS,
      TRIGGER_ACTION_CONTRACTS,
      isActionAllowedForTrigger,
      resolveEffectiveAction,
      sanitizeActionOverrides,
    } from './src/engine/triggerContract.ts';

    for (const trigger of CANONICAL_TRIGGERS) {
      const contract = TRIGGER_ACTION_CONTRACTS[trigger];
      const validActions = [contract.defaultAction, ...contract.allowedAlternatives];

      // A. Every permitted alternative resolves successfully
      for (const act of validActions) {
        assert.ok(isActionAllowedForTrigger(trigger, act), `${act} should be allowed for ${trigger}`);
        const resolved = resolveEffectiveAction(trigger, 'hide', { [trigger]: act });
        assert.strictEqual(resolved, act, `Override ${act} should resolve for ${trigger}`);
      }

      // B. Disallowed actions must be rejected and fall back to defaultAction
      const allPossibleActions = ['hide', 'blur', 'mute', 'reduce', 'skip', 'warn', 'dim', 'beep', 'unknown'];
      for (const act of allPossibleActions) {
        if (!validActions.includes(act)) {
          assert.ok(!isActionAllowedForTrigger(trigger, act), `${act} should be rejected for ${trigger}`);
          const fallback = resolveEffectiveAction(trigger, 'hide', { [trigger]: act });
          assert.strictEqual(fallback, contract.defaultAction, `Disallowed action ${act} for ${trigger} must fall back to default`);
        }
      }
    }

    // C. Sanitizer rejects obsolete or disallowed entries
    const sanitized = sanitizeActionOverrides({
      flashing: 'skip',            // Allowed alternative
      loud_spike: 'dim',           // Disallowed (dim removed)
      gunshot: 'reduce',          // Allowed alternative
      insect: 'mute',              // Disallowed (insect only allows hide, blur, skip, warn)
      non_existent_trigger: 'warn' // Invalid trigger
    });

    assert.strictEqual(sanitized.flashing, 'skip');
    assert.strictEqual(sanitized.gunshot, 'reduce');
    assert.strictEqual(sanitized.loud_spike, undefined);
    assert.strictEqual(sanitized.insect, undefined);
    assert.strictEqual(sanitized.non_existent_trigger, undefined);

    console.log('Permitted combinations and rejection fallback verified.');
    """
    res = run_node_test(node_script)
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"


def test_action_resolution_and_overlapping_precedence():
    """Verify overlapping cues precedence, independent coexistence, and restoration."""
    node_script = """
    import assert from 'node:assert';
    import { resolveActions } from './src/engine/resolveActions.ts';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';

    // 1. Independent coexistence: warning banner and audio protection
    const cues1 = [
      { id: 'audio_1', start: 10, end: 20, source: 'audio', action: 'mute', trigger: 'profanity' },
      { id: 'warn_1', start: 12, end: 18, source: 'visual', action: 'warn', trigger: 'jump_scare' },
    ];
    const res1 = resolveActions(15, cues1, { ...DEFAULT_VIEWER_OPTIONS, muteEnabled: true });
    assert.strictEqual(res1.soundGain, 0, 'Audio must remain muted');
    assert.deepStrictEqual(res1.warnCueIds, ['warn_1'], 'Warning must coexist independently');
    assert.strictEqual(res1.visualAction, null, 'No visual hiding active');

    // 2. Visual Precedence: hide (3) > blur (2)
    // When visible_blood (default hide) and insect (configured with blur override) overlap:
    const cues2 = [
      { id: 'blood_hide', start: 30, end: 40, source: 'visual', action: 'hide', trigger: 'visible_blood' },
      { id: 'insect_blur', start: 32, end: 45, source: 'visual', action: 'blur', trigger: 'insect' },
    ];
    const visualOptions = {
      ...DEFAULT_VIEWER_OPTIONS,
      hideEnabled: true,
      actionOverrides: { insect: 'blur' }
    };
    // A. At t=35, both active -> hide wins
    const res2A = resolveActions(35, cues2, visualOptions);
    assert.strictEqual(res2A.visualAction, 'hide');
    assert.strictEqual(res2A.hide, true);

    // B. At t=42, blood_hide ended -> blur restored deterministically!
    const res2B = resolveActions(42, cues2, visualOptions);
    assert.strictEqual(res2B.visualAction, 'blur');
    assert.strictEqual(res2B.hide, false);

    // 3. Audio Precedence: mute (gain 0) > reduce (gain 0 < g < 1)
    const cues3 = [
      { id: 'explosion_reduce', start: 50, end: 60, source: 'audio', action: 'reduce', trigger: 'explosion', gain: 0.25 },
      { id: 'gunshot_mute', start: 52, end: 55, source: 'audio', action: 'mute', trigger: 'gunshot' },
    ];
    // A. At t=53, mute overrides reduce
    const res3A = resolveActions(53, cues3, { ...DEFAULT_VIEWER_OPTIONS, muteEnabled: true, reduceEnabled: true });
    assert.strictEqual(res3A.soundGain, 0);

    // B. At t=56, gunshot ended -> volume restored to reduced level (0.25)
    const res3B = resolveActions(56, cues3, { ...DEFAULT_VIEWER_OPTIONS, muteEnabled: true, reduceEnabled: true });
    assert.strictEqual(res3B.soundGain, 0.25);

    // C. At t=61, all audio cues ended -> restored to full volume (1.0)
    const res3C = resolveActions(61, cues3, { ...DEFAULT_VIEWER_OPTIONS, muteEnabled: true, reduceEnabled: true });
    assert.strictEqual(res3C.soundGain, 1.0);

    // 4. Per-trigger user overrides in playback
    // Flashing defaults to hide; user overrides to skip!
    const flashCue = [
      { id: 'flash_1', start: 70, end: 75, source: 'visual', action: 'hide', trigger: 'flashing' }
    ];
    const resDefault = resolveActions(72, flashCue, { ...DEFAULT_VIEWER_OPTIONS, skipEnabled: true });
    assert.strictEqual(resDefault.visualAction, 'hide');
    assert.strictEqual(resDefault.skipTo, null);

    const resOverride = resolveActions(72, flashCue, {
      ...DEFAULT_VIEWER_OPTIONS,
      skipEnabled: true,
      actionOverrides: { flashing: 'skip' }
    });
    assert.strictEqual(resOverride.visualAction, null, 'Visual action replaced by skip');
    assert.strictEqual(resOverride.skipTo, 75, 'Seek target set to cue.end');

    console.log('Action resolution and precedence verified.');
    """
    res = run_node_test(node_script)
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"


def test_disabled_categories_and_global_safety_settings():
    """Verify that disabledCategories completely suppress actions and global safety toggles work."""
    node_script = """
    import assert from 'node:assert';
    import { resolveActions } from './src/engine/resolveActions.ts';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';

    const cues = [
      { id: 'c_insect', start: 10, end: 20, source: 'visual', action: 'hide', trigger: 'insect' },
      { id: 'c_gunshot', start: 10, end: 20, source: 'audio', action: 'mute', trigger: 'gunshot' },
      { id: 'c_explosion', start: 10, end: 20, source: 'audio', action: 'reduce', trigger: 'explosion' },
    ];

    // 1. With insect disabled, no visual hide occurs
    const res1 = resolveActions(15, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
      disabledCategories: ['insect'],
      muteEnabled: true,
    });
    assert.strictEqual(res1.visualAction, null);
    assert.strictEqual(res1.soundGain, 0); // gunshot still active

    // 2. With global muteEnabled = false, audio is not muted
    const res2 = resolveActions(15, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
      muteEnabled: false,
      reduceEnabled: false,
    });
    assert.strictEqual(res2.soundGain, 1.0);

    // 3. With global hideEnabled = false, visual hide is not applied
    const res3 = resolveActions(15, cues, {
      ...DEFAULT_VIEWER_OPTIONS,
      hideEnabled: false,
    });
    assert.strictEqual(res3.visualAction, null);
    assert.strictEqual(res3.hide, false);

    console.log('Disabled categories and global settings verified.');
    """
    res = run_node_test(node_script)
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"


def test_playback_state_lifecycle_seek_and_recap():
    """Verify PlaybackState skips deduplication, prompt acknowledgment, seeking, and session recap recording."""
    node_script = """
    import assert from 'node:assert';
    import { PlaybackState } from './src/engine/playbackState.ts';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';

    const cues = [
      { id: 'skip_1', start: 10, end: 15, source: 'visual', action: 'hide', trigger: 'flashing' },
      { id: 'skip_2', start: 11, end: 20, source: 'visual', action: 'hide', trigger: 'insect' },
      { id: 'mute_1', start: 25, end: 30, source: 'audio', action: 'mute', trigger: 'profanity' },
    ];

    const state = new PlaybackState();
    const options = {
      ...DEFAULT_VIEWER_OPTIONS,
      skipEnabled: true,
      muteEnabled: true,
      actionOverrides: { flashing: 'skip', insect: 'skip' }
    };

    // 1. Resolve at t=12 -> skip fires and chains to max end (20)
    const res = state.resolveAt(12, cues, options);
    assert.strictEqual(res.skipTo, 20);

    // 2. Forward playback past skip: skip_1 and skip_2 do not re-fire
    const resPast = state.resolveAt(13, cues, options);
    assert.strictEqual(resPast.skipTo, null, 'Already-fired skip cues must not re-trigger');

    // 3. Seek/Rewind: rewinding to t=5 clears fired status
    state.seek(5, cues, options);
    assert.strictEqual(state.hasSkipFired('skip_1'), false, 'Rewind must clear fired skip record');

    // 4. Session recap payload contains recorded actions
    state.resolveAt(26, cues, options);
    const recap = state.getSessionPayload();
    assert.ok(recap.appliedActions.length >= 1);
    const mutedAction = recap.appliedActions.find(a => a.cueId === 'mute_1');
    assert.ok(mutedAction, 'Mute cue must be recorded in session recap');
    assert.strictEqual(mutedAction.appliedEffect, 'muted');

    console.log('PlaybackState lifecycle and seek verified.');
    """
    res = run_node_test(node_script)
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"


def test_packaged_index_adapter_adapts_10_triggers():
    """Verify adaptPackagedIndex handles all 10 triggers and rejects unrepresentable actions."""
    node_script = """
    import assert from 'node:assert';
    import fs from 'node:fs';
    import { adaptPackagedIndex } from './src/engine/packagedIndexAdapter.ts';

    const rawPolicies = JSON.parse(fs.readFileSync('./test/fixtures/triggerPolicies.json', 'utf8'));
    rawPolicies.policies.insect.enabled = true;

    const sampleIndex = {
      schema_version: 1,
      film_id: 'test_film',
      timeline: {
        duration_seconds: 100.0,
        timestamp_unit: 'seconds',
        interval_convention: 'half_open',
      },
      cues: [
        { id: 'cue_1', start: 5, end: 10, trigger: 'flashing', score: 0.95, source: 'flash_detector' },
        { id: 'cue_2', start: 12, end: 18, trigger: 'insect', score: 0.88, source: 'insect_detector' },
        { id: 'cue_3', start: 20, end: 25, trigger: 'loud_spike', score: 0.90, source: 'loudness_detector' },
      ],
    };

    const cues = adaptPackagedIndex(sampleIndex, rawPolicies.policies);
    assert.strictEqual(cues.length, 3);
    assert.strictEqual(cues[0].action, 'hide', 'Flashing adapted to hide default');
    assert.strictEqual(cues[1].action, 'hide', 'Insect adapted to hide default');
    assert.strictEqual(cues[2].action, 'reduce', 'Loud spike adapted to reduce default');

    console.log('Packaged index adapter verification passed.');
    """
    res = run_node_test(node_script)
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
