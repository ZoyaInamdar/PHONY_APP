"""Tests for Person B Content Delivery, Two-Film Catalogue, and Packaged Index Selection.

Covers:
1. Film catalogue discovery (getAllFilms, getFilm, getFilmMetadata for 'sintel' and 'tears_of_steel').
2. Validation of packaged detection indexes for both films against schema and domain rules.
3. Cue isolation and prevention of cross-film contamination (no cue leakage).
4. Strict error handling: unknown film IDs and mismatched/corrupted indexes produce clear,
   recoverable errors with zero silent fallbacks.
5. Licensing and rights verification (docs/licenses/media_rights.json).
6. Navigation and PlayerScreen two-film integration (tiles, props, film switching, reset on switch).
"""

import json
from pathlib import Path
import subprocess
import sys
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
INDEX_SCHEMA_PATH = REPO_ROOT / "schema" / "index.schema.json"
VALIDATE_SCRIPT = REPO_ROOT / "scripts" / "validate_index.py"
MEDIA_RIGHTS_PATH = REPO_ROOT / "docs" / "licenses" / "media_rights.json"

SINTEL_INDEX = REPO_ROOT / "data" / "index.json"
TOS_INDEX = REPO_ROOT / "data" / "tears_of_steel_index.json"
ED_INDEX = REPO_ROOT / "data" / "elephants_dream_index.json"

CATALOGUE_TS = REPO_ROOT / "src" / "data" / "catalogue.ts"
PLAYBACK_CUES_TS = REPO_ROOT / "src" / "data" / "playbackCues.ts"
DEFAULT_POLICIES_TS = REPO_ROOT / "src" / "data" / "defaultTriggerPolicies.ts"
FIXTURE_PATH = REPO_ROOT / "test" / "fixtures" / "triggerPolicies.json"
TILES_TSX = REPO_ROOT / "src" / "data" / "tiles.tsx"
APP_TSX = REPO_ROOT / "src" / "App.tsx"
PLAYER_SCREEN_TSX = REPO_ROOT / "src" / "components" / "PlayerScreen.tsx"


def test_catalogue_file_exists():
    """Verify that src/data/catalogue.ts exists and exports required catalogue members."""
    assert CATALOGUE_TS.is_file(), f"Missing {CATALOGUE_TS}"
    content = CATALOGUE_TS.read_text(encoding="utf-8")
    assert "FILM_CATALOGUE" in content
    assert "getAllFilms" in content
    assert "getFilmMetadata" in content
    assert "getFilm" in content
    assert "loadFilmIndex" in content
    assert "loadFilmCues" in content


def test_catalogue_discovery_metadata():
    """Verify that Sintel, Tears of Steel, and Elephants Dream are discoverable with valid metadata."""
    node_script = """
    import assert from 'node:assert';
    import { getAllFilms, getFilm, getFilmMetadata } from './src/data/catalogue.ts';

    const films = getAllFilms();
    assert.strictEqual(films.length, 3, 'Expected 3 films in catalogue');

    const filmIds = films.map(f => f.film_id).sort();
    assert.deepStrictEqual(filmIds, ['elephants_dream', 'sintel', 'tears_of_steel']);

    // Film 1: Sintel
    const sintel = getFilm('sintel');
    assert.strictEqual(sintel.film_id, 'sintel');
    assert.strictEqual(sintel.title, 'Sintel');
    assert.strictEqual(sintel.duration_seconds, 52.0);
    assert.strictEqual(sintel.fps, 24.0);
    assert.ok(sintel.video_url.startsWith('https://'), 'Sintel video URL must be https');
    assert.strictEqual(sintel.index_path, 'data/index.json');

    // Film 2: Tears of Steel
    const tos = getFilm('tears_of_steel');
    assert.strictEqual(tos.film_id, 'tears_of_steel');
    assert.strictEqual(tos.title, 'Tears of Steel');
    assert.strictEqual(tos.duration_seconds, 734.167);
    assert.strictEqual(tos.fps, 24.0);
    assert.ok(tos.video_url.startsWith('https://'), 'Tears of Steel video URL must be https');
    assert.strictEqual(tos.index_path, 'data/tears_of_steel_index.json');

    // Film 3: Elephants Dream
    const ed = getFilm('elephants_dream');
    assert.strictEqual(ed.film_id, 'elephants_dream');
    assert.strictEqual(ed.title, 'Elephants Dream');
    assert.strictEqual(ed.duration_seconds, 653.83);
    assert.strictEqual(ed.fps, 24.0);
    assert.strictEqual(ed.video_url, 'data/media/elephant_dreams.mp4');
    assert.strictEqual(ed.index_path, 'data/elephants_dream_index.json');
    const edIndex = ed.loadIndex();
    assert.strictEqual(edIndex.film_id, 'elephants_dream');
    assert.strictEqual(edIndex.synthetic, false);
    assert.ok(Array.isArray(edIndex.cues) && edIndex.cues.length > 0, 'Elephants Dream cues must not be empty');

    // getFilmMetadata returns null for unknown, does not throw
    assert.strictEqual(getFilmMetadata('nonexistent'), null);

    console.log('Catalogue discovery metadata verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Catalogue discovery metadata verified." in res.stdout


def test_packaged_indexes_exist_and_pass_validation():
    """Verify that Sintel, Tears of Steel, and Elephants Dream packaged indexes exist and pass validate_index.py."""
    assert SINTEL_INDEX.is_file(), f"Missing {SINTEL_INDEX}"
    assert TOS_INDEX.is_file(), f"Missing {TOS_INDEX}"
    assert ED_INDEX.is_file(), f"Missing {ED_INDEX}"

    # Validate Sintel index
    res_sintel = subprocess.run(
        [
            sys.executable,
            str(VALIDATE_SCRIPT),
            "--index", str(SINTEL_INDEX),
            "--config", str(CONFIG_PATH),
            "--schema", str(INDEX_SCHEMA_PATH),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res_sintel.returncode == 0, f"Sintel index validation failed:\n{res_sintel.stdout}\n{res_sintel.stderr}"
    assert "SUCCESS: index.json passed all schema and domain validation checks." in res_sintel.stdout

    # Validate Tears of Steel index
    res_tos = subprocess.run(
        [
            sys.executable,
            str(VALIDATE_SCRIPT),
            "--index", str(TOS_INDEX),
            "--config", str(CONFIG_PATH),
            "--schema", str(INDEX_SCHEMA_PATH),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res_tos.returncode == 0, f"TOS index validation failed:\n{res_tos.stdout}\n{res_tos.stderr}"
    assert "SUCCESS: index.json passed all schema and domain validation checks." in res_tos.stdout

    # Validate Elephants Dream index
    ed_media = REPO_ROOT / "data" / "media" / "elephants_dream.mp4"
    ed_val_cmd = [
        sys.executable,
        str(VALIDATE_SCRIPT),
        "--index", str(ED_INDEX),
        "--config", str(CONFIG_PATH),
        "--schema", str(INDEX_SCHEMA_PATH),
    ]
    if ed_media.is_file():
        ed_val_cmd.extend(["--media", str(ed_media), "--duration-tolerance", "0.6"])
    res_ed = subprocess.run(
        ed_val_cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res_ed.returncode == 0, f"Elephants Dream index validation failed:\n{res_ed.stdout}\n{res_ed.stderr}"
    assert "SUCCESS: index.json passed all schema and domain validation checks." in res_ed.stdout


def test_cue_isolation_and_no_cross_contamination():
    """Verify that loading cues for each film produces isolated cues without leakage."""
    node_script = """
    import assert from 'node:assert';
    import { loadFilmCues } from './src/data/catalogue.ts';
    import { sintelIndexCues, tearsOfSteelIndexCues, getCuesForFilm } from './src/data/playbackCues.ts';

    const sintelCues = loadFilmCues('sintel');
    const tosCues = loadFilmCues('tears_of_steel');
    const edCues = loadFilmCues('elephants_dream');

    assert.ok(sintelCues.length > 0, 'Sintel cues must not be empty');
    assert.ok(tosCues.length > 0, 'Tears of Steel cues must not be empty');
    assert.ok(edCues.length > 0, 'Elephants Dream cues must not be empty');

    // Confirm playbackCues exports match catalogue
    assert.strictEqual(sintelIndexCues.length, sintelCues.length);
    assert.strictEqual(tearsOfSteelIndexCues.length, tosCues.length);
    assert.strictEqual(getCuesForFilm('sintel').length, sintelCues.length);
    assert.strictEqual(getCuesForFilm('tears_of_steel').length, tosCues.length);

    // Timestamps within respective film timelines
    for (const cue of sintelCues) {
      assert.ok(cue.start >= 0 && cue.end <= 52.0, `Sintel cue out of range: ${cue.id}`);
      assert.ok(cue.id.startsWith('cue_sintel_'), `Sintel cue id prefix mismatch: ${cue.id}`);
    }

    for (const cue of tosCues) {
      assert.ok(cue.start >= 0 && cue.end <= 734.167, `ToS cue out of range: ${cue.id}`);
      assert.ok(cue.id.startsWith('cue_tears_of_steel_'), `ToS cue id prefix mismatch: ${cue.id}`);
    }

    for (const cue of edCues) {
      assert.ok(cue.start >= 0 && cue.end <= 653.83, `Elephants Dream cue out of range: ${cue.id}`);
      assert.ok(cue.id.startsWith('cue_elephants_dream_'), `Elephants Dream cue id prefix mismatch: ${cue.id}`);
    }

    // Ensure completely disjoint sets of IDs (no cue leakage across films)
    const sintelIds = new Set(sintelCues.map(c => c.id));
    const tosIds = new Set(tosCues.map(c => c.id));
    const edIds = new Set(edCues.map(c => c.id));
    for (const id of sintelIds) {
      assert.ok(!tosIds.has(id), `Cue ${id} leaked into Tears of Steel!`);
      assert.ok(!edIds.has(id), `Cue ${id} leaked into Elephants Dream!`);
    }
    for (const id of edIds) {
      assert.ok(!sintelIds.has(id), `Cue ${id} leaked into Sintel!`);
      assert.ok(!tosIds.has(id), `Cue ${id} leaked into Tears of Steel!`);
    }

    // Verify canonical triggers represented in Elephants Dream
    const edTriggers = new Set(edCues.map(c => c.trigger));
    assert.ok(edTriggers.has('flashing'), 'Elephants Dream must contain flashing trigger');
    assert.ok(edTriggers.has('loud_spike'), 'Elephants Dream must contain loud_spike trigger');
    assert.ok(edTriggers.has('explosion'), 'Elephants Dream must contain explosion trigger');

    // Verify canonical triggers represented in ToS
    const tosTriggers = new Set(tosCues.map(c => c.trigger));
    assert.ok(tosTriggers.has('gunshot'), 'ToS must contain gunshot trigger');
    assert.ok(tosTriggers.has('loud_spike'), 'ToS must contain loud_spike trigger');
    assert.ok(tosTriggers.has('profanity'), 'ToS must contain profanity trigger');
    assert.ok(tosTriggers.has('explosion'), 'ToS must contain explosion trigger');
    assert.ok(tosTriggers.has('flashing'), 'ToS must contain flashing trigger');
    assert.ok(tosTriggers.has('scream'), 'ToS must contain scream trigger');

    console.log('Cue isolation and timeline boundaries verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Cue isolation and timeline boundaries verified." in res.stdout


def test_strict_rejection_and_no_silent_fallback():
    """Verify that unknown film IDs or corrupted/mismatched indexes throw explicit errors.

    Crucially: NEVER silently fall back to Sintel or another film.
    """
    node_script = """
    import assert from 'node:assert';
    import { getFilm, loadFilmIndex, loadFilmCues } from './src/data/catalogue.ts';
    import { getCuesForFilm } from './src/data/playbackCues.ts';

    // 1. Unknown film ID must throw explicit error
    assert.throws(
      () => getFilm('unknown_film'),
      /Unknown film ID: "unknown_film"/,
      'Must reject unknown film ID'
    );

    assert.throws(
      () => loadFilmIndex('unknown_film'),
      /Unknown film ID: "unknown_film"/,
      'loadFilmIndex must reject unknown film ID'
    );

    assert.throws(
      () => loadFilmCues('unknown_film'),
      /Unknown film ID: "unknown_film"/,
      'loadFilmCues must reject unknown film ID'
    );

    assert.throws(
      () => getCuesForFilm('unknown_film'),
      /Unknown film ID: "unknown_film"/,
      'getCuesForFilm must reject unknown film ID'
    );

    // 2. Catalogue loads Elephants Dream index with matching film_id
    const edIndex = loadFilmIndex('elephants_dream');
    assert.strictEqual(edIndex.film_id, 'elephants_dream', 'Elephants Dream index must match film_id');

    // 3. Catalogue loadFilmIndex strictly rejects mismatched film_id
    import { FILM_CATALOGUE } from './src/data/catalogue.ts';
    const originalEntry = FILM_CATALOGUE['elephants_dream'];
    try {
      FILM_CATALOGUE['elephants_dream'] = {
        ...originalEntry,
        loadIndex: () => ({ film_id: 'mismatched_film_id', cues: [] }),
      };
      assert.throws(
        () => loadFilmIndex('elephants_dream'),
        /Packaged index film_id mismatch: expected "elephants_dream", but found "mismatched_film_id"/,
        'loadFilmIndex must strictly reject index with mismatched film_id'
      );
    } finally {
      FILM_CATALOGUE['elephants_dream'] = originalEntry;
    }

    console.log('Strict rejection verified without silent fallbacks.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Strict rejection verified without silent fallbacks." in res.stdout


def test_media_rights_documentation():
    """Verify that both films have documented license rights in docs/licenses/media_rights.json."""
    assert MEDIA_RIGHTS_PATH.is_file(), f"Missing {MEDIA_RIGHTS_PATH}"
    with open(MEDIA_RIGHTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Both films documented
    films = {f["film_id"]: f for f in data.get("films", [])}
    assert "sintel" in films, "Sintel must be documented in media_rights.json"
    assert "tears_of_steel" in films, "Tears of Steel must be documented in media_rights.json"

    sintel_info = films["sintel"]
    tos_info = films["tears_of_steel"]

    assert "CC BY 3.0" in sintel_info["media"]["license_name"]
    assert "CC BY 3.0" in tos_info["media"]["license_name"]
    assert "Blender Foundation" in sintel_info["media"]["attribution"]
    assert "Blender Foundation" in tos_info["media"]["attribution"]


def test_two_film_ui_and_player_integration():
    """Verify that tiles.tsx, App.tsx, and PlayerScreen.tsx properly wire both films."""
    # 1. tiles.tsx contains tiles for both films
    tiles_content = TILES_TSX.read_text(encoding="utf-8")
    assert "'get-started'" in tiles_content or '"get-started"' in tiles_content
    assert "'tears-of-steel'" in tiles_content or '"tears-of-steel"' in tiles_content

    # 2. App.tsx tracks selectedFilmId and passes filmId prop to PlayerScreen
    app_content = APP_TSX.read_text(encoding="utf-8")
    assert "selectedFilmId" in app_content
    assert "filmId={selectedFilmId}" in app_content
    assert "tears_of_steel" in app_content

    # 3. PlayerScreen.tsx accepts filmId and has film switcher and error banner
    player_content = PLAYER_SCREEN_TSX.read_text(encoding="utf-8")
    assert "filmId?:" in player_content
    assert "handleFilmSwitch" in player_content
    assert "toggleFilm" in player_content
    assert "control-film" in player_content
    assert "index-error-banner" in player_content
    assert "indexLoadError" in player_content


def test_no_production_dependency_on_test_fixtures():
    """1. Verify neither src/data/catalogue.ts nor src/data/playbackCues.ts imports from test/fixtures."""
    catalogue_content = CATALOGUE_TS.read_text(encoding="utf-8")
    playback_content = PLAYBACK_CUES_TS.read_text(encoding="utf-8")

    assert "test/fixtures" not in catalogue_content, "catalogue.ts must not reference test/fixtures"
    assert "triggerPolicies.json" not in catalogue_content, "catalogue.ts must not reference triggerPolicies.json"
    assert "test/fixtures" not in playback_content, "playbackCues.ts must not reference test/fixtures"
    assert "triggerPolicies.json" not in playback_content, "playbackCues.ts must not reference triggerPolicies.json"
    assert DEFAULT_POLICIES_TS.is_file(), f"Missing {DEFAULT_POLICIES_TS}"


def test_production_defaults_match_shared_config_and_fixture():
    """7. Verify all 9 canonical triggers and every required field match across config.yaml, fixture, and production defaults."""
    import os
    import yaml

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    triggers_config = config.get("triggers", {})

    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        fixture_data = json.load(f)
    fixture_policies = fixture_data.get("policies", {})

    # Derive normalized expected policies dictionary directly from config.yaml
    expected_policies = {}
    for trigger_id, cfg in triggers_config.items():
        expected_policies[trigger_id] = {
            "enabled": bool(cfg.get("enabled", False)),
            "modality": str(cfg.get("modality", "")),
            "default_action": str(cfg.get("default_action", "")),
            "allowed_actions": list(cfg.get("allowed_actions", [])),
        }

    canonical_triggers = set(triggers_config.keys())

    # 1. Verify complete key set for config.yaml and fixture
    assert set(expected_policies.keys()) == canonical_triggers, f"config.yaml triggers mismatch: {canonical_triggers}"
    assert set(fixture_policies.keys()) == canonical_triggers, f"fixture triggers mismatch: {canonical_triggers}"

    # 2. Verify all four fields (enabled, modality, default_action, allowed_actions) for every trigger in fixture
    for trigger_id in canonical_triggers:
        exp = expected_policies[trigger_id]
        fix = fixture_policies[trigger_id]
        assert fix["enabled"] == exp["enabled"], f"Fixture enabled mismatch for {trigger_id}"
        assert fix["modality"] == exp["modality"], f"Fixture modality mismatch for {trigger_id}"
        assert fix["default_action"] == exp["default_action"], f"Fixture default_action mismatch for {trigger_id}"
        assert fix["allowed_actions"] == exp["allowed_actions"], f"Fixture allowed_actions mismatch for {trigger_id}"
        assert fix == exp, f"Fixture mismatch for {trigger_id}"

    # 3. Verify production defaultTriggerPolicies.ts matches normalized config.yaml in Node
    node_script = """
    import assert from 'node:assert';
    import { DEFAULT_TRIGGER_POLICIES } from './src/data/defaultTriggerPolicies.ts';

    const expected = JSON.parse(process.env.EXPECTED_POLICIES_JSON);
    const expectedKeys = Object.keys(expected).sort();

    // Check complete canonical key set
    const actualKeys = Object.keys(DEFAULT_TRIGGER_POLICIES).sort();
    assert.deepStrictEqual(actualKeys, expectedKeys, 'All canonical triggers must be keys in DEFAULT_TRIGGER_POLICIES');

    // Check every trigger and every field matches normalized config.yaml
    for (const key of expectedKeys) {
      const exp = expected[key];
      const act = DEFAULT_TRIGGER_POLICIES[key];
      assert.ok(act, `Missing trigger policy in production defaults: ${key}`);
      assert.strictEqual(act.enabled, exp.enabled, `enabled mismatch for trigger ${key}`);
      assert.strictEqual(act.modality, exp.modality, `modality mismatch for trigger ${key}`);
      assert.strictEqual(act.default_action, exp.default_action, `default_action mismatch for trigger ${key}`);
      assert.deepStrictEqual(act.allowed_actions, exp.allowed_actions, `allowed_actions mismatch for trigger ${key}`);
      assert.deepStrictEqual(act, exp, `Full policy object mismatch for trigger ${key}`);
    }

    // Complete object equivalence
    assert.deepStrictEqual(
      DEFAULT_TRIGGER_POLICIES,
      expected,
      'DEFAULT_TRIGGER_POLICIES must exactly match normalized config.yaml policies'
    );

    console.log('All canonical triggers and fields verified across config.yaml, fixture, and production defaults.');
    """
    env = {**os.environ, "EXPECTED_POLICIES_JSON": json.dumps(expected_policies)}
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "All canonical triggers and fields verified across config.yaml, fixture, and production defaults." in res.stdout


def test_film_indexes_load_and_adapt_with_production_defaults():
    """2, 3, 4. Verify Sintel and ToS load/adapt with production defaults, retain actions/sources, and filter disabled."""
    node_script = """
    import assert from 'node:assert';
    import { loadFilmCues } from './src/data/catalogue.ts';
    import { sintelIndexCues, tearsOfSteelIndexCues } from './src/data/playbackCues.ts';

    // 2. Both load and adapt successfully using production defaults
    const sintelCues = loadFilmCues('sintel');
    const tosCues = loadFilmCues('tears_of_steel');

    assert.ok(sintelCues.length > 0, 'Sintel cues must not be empty');
    assert.ok(tosCues.length > 0, 'ToS cues must not be empty');
    assert.strictEqual(sintelIndexCues.length, sintelCues.length);
    assert.strictEqual(tearsOfSteelIndexCues.length, tosCues.length);

    // 3. Retain expected actions and sources for representative enabled triggers
    // Sintel contains flashing cues
    const sintelFlashing = sintelCues.find(c => c.trigger === 'flashing');
    assert.ok(sintelFlashing, 'Sintel must have at least one flashing cue');
    assert.strictEqual(sintelFlashing.action, 'hide', 'Flashing default action must be hide');
    assert.strictEqual(sintelFlashing.source, 'visual', 'Flashing source must be visual');

    // ToS contains gunshot, explosion, profanity, scream
    const tosGunshot = tosCues.find(c => c.trigger === 'gunshot');
    assert.ok(tosGunshot, 'ToS must have a gunshot cue');
    assert.strictEqual(tosGunshot.action, 'mute');
    assert.strictEqual(tosGunshot.source, 'audio');

    const tosProfanity = tosCues.find(c => c.trigger === 'profanity');
    assert.ok(tosProfanity, 'ToS must have a profanity cue');
    assert.strictEqual(tosProfanity.action, 'mute');
    assert.strictEqual(tosProfanity.source, 'text');

    // 4. Disabled triggers (fast_motion, jump_scare) must remain filtered out
    const allCues = [...sintelCues, ...tosCues];
    const fastMotionCues = allCues.filter(c => c.trigger === 'fast_motion');
    const jumpScareCues = allCues.filter(c => c.trigger === 'jump_scare');
    assert.strictEqual(fastMotionCues.length, 0, 'Disabled trigger fast_motion must produce 0 cues');
    assert.strictEqual(jumpScareCues.length, 0, 'Disabled trigger jump_scare must produce 0 cues');

    console.log('Production defaults adaptation verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Production defaults adaptation verified." in res.stdout


def test_custom_policies_override_defaults_with_asserted_loud_spike():
    """5. Custom policies override defaults; assert at least one matching loud_spike cue exists and has action 'skip'."""
    node_script = """
    import assert from 'node:assert';
    import { loadFilmCues } from './src/data/catalogue.ts';

    // In Tears of Steel, loud_spike cues exist.
    // Default action for loud_spike is 'reduce'.
    const defaultCues = loadFilmCues('tears_of_steel');
    const defaultSpikes = defaultCues.filter(c => c.trigger === 'loud_spike');
    assert.ok(defaultSpikes.length >= 1, 'Expected at least one loud_spike cue in Tears of Steel by default');
    assert.strictEqual(defaultSpikes[0].action, 'reduce', 'Default action for loud_spike must be reduce');

    // Override with custom policy: loud_spike -> skip
    const customPolicies = {
      flashing: { enabled: true, modality: 'visual', default_action: 'dim', allowed_actions: ['dim', 'blur', 'skip', 'warn'] },
      loud_spike: { enabled: true, modality: 'audio', default_action: 'skip', allowed_actions: ['mute', 'skip', 'warn'] },
      gunshot: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute', 'skip', 'warn'] },
      explosion: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute', 'skip', 'warn'] },
      scream: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute', 'skip', 'warn'] },
      profanity: { enabled: true, modality: 'text', default_action: 'mute', allowed_actions: ['mute', 'skip', 'warn'] },
      spider: { enabled: true, modality: 'visual', default_action: 'blur', allowed_actions: ['blur', 'skip', 'warn'] },
      fast_motion: { enabled: false, modality: 'visual', default_action: 'warn', allowed_actions: ['warn', 'dim', 'skip'] },
      jump_scare: { enabled: false, modality: 'audio_visual', default_action: 'warn', allowed_actions: ['warn', 'mute', 'skip'] },
    };

    const overriddenCues = loadFilmCues('tears_of_steel', customPolicies);
    const overriddenSpikes = overriddenCues.filter(c => c.trigger === 'loud_spike');

    // POSITIVE ASSERTION: at least one loud_spike cue MUST exist and MUST be 'skip'
    assert.ok(overriddenSpikes.length >= 1, 'Must find at least one matching loud_spike cue in overridden cues');
    for (const cue of overriddenSpikes) {
      assert.strictEqual(cue.action, 'skip', `Expected cue ${cue.id} action to be 'skip', got '${cue.action}'`);
    }

    console.log('Custom policies override verified with positive loud_spike assertion.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Custom policies override verified with positive loud_spike assertion." in res.stdout


def test_unsupported_and_disallowed_actions_fail():
    """6. Unsupported actions and actions not in allowed_actions continue to fail strictly."""
    node_script = """
    import assert from 'node:assert';
    import { loadFilmCues } from './src/data/catalogue.ts';

    // 6a: Unsupported action not in REPRESENTABLE_ACTIONS (e.g. 'unsupported_magic_action')
    const unsupportedActionPolicy = {
      flashing: { enabled: true, modality: 'visual', default_action: 'unsupported_magic_action', allowed_actions: ['unsupported_magic_action'] },
      loud_spike: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute'] },
      gunshot: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute'] },
      explosion: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute'] },
      scream: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute'] },
      profanity: { enabled: true, modality: 'text', default_action: 'mute', allowed_actions: ['mute'] },
      spider: { enabled: true, modality: 'visual', default_action: 'blur', allowed_actions: ['blur'] },
      fast_motion: { enabled: false, modality: 'visual', default_action: 'warn', allowed_actions: ['warn'] },
      jump_scare: { enabled: false, modality: 'audio_visual', default_action: 'warn', allowed_actions: ['warn'] },
    };

    assert.throws(
      () => loadFilmCues('sintel', unsupportedActionPolicy),
      /Unsupported action "unsupported_magic_action"/,
      'Must throw Error for action outside REPRESENTABLE_ACTIONS'
    );

    // 6b: Action is representable ('dim') but not in allowed_actions for loud_spike (['mute', 'skip', 'warn'])
    const disallowedActionPolicy = {
      flashing: { enabled: true, modality: 'visual', default_action: 'dim', allowed_actions: ['dim'] },
      loud_spike: { enabled: true, modality: 'audio', default_action: 'dim', allowed_actions: ['mute', 'skip', 'warn'] },
      gunshot: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute'] },
      explosion: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute'] },
      scream: { enabled: true, modality: 'audio', default_action: 'mute', allowed_actions: ['mute'] },
      profanity: { enabled: true, modality: 'text', default_action: 'mute', allowed_actions: ['mute'] },
      spider: { enabled: true, modality: 'visual', default_action: 'blur', allowed_actions: ['blur'] },
      fast_motion: { enabled: false, modality: 'visual', default_action: 'warn', allowed_actions: ['warn'] },
      jump_scare: { enabled: false, modality: 'audio_visual', default_action: 'warn', allowed_actions: ['warn'] },
    };

    assert.throws(
      () => loadFilmCues('tears_of_steel', disallowedActionPolicy),
      /Action "dim" is not allowed for trigger "loud_spike"/,
      'Must throw Error when action is not in allowed_actions'
    );

    console.log('Unsupported and disallowed actions rejection verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Unsupported and disallowed actions rejection verified." in res.stdout
