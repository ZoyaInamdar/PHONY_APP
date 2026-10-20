"""Tests for Person B Multi-Film Playback, Catalogue Alignment, and Caption Integration.

Covers:
1. App default film ID aligned to canonical catalogue ('sintel').
2. Default launch error prevention and both film launch paths ('sintel' and 'tears_of_steel').
3. Film-specific subtitle loading from catalogue with strict rejection of unknown films.
4. CaptionRenderer integration into PlayerScreen.
5. Caption rendering at active timestamps and proper isolation across film switches.
"""

from pathlib import Path
import subprocess
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_TSX = REPO_ROOT / "src" / "App.tsx"
CATALOGUE_TS = REPO_ROOT / "src" / "data" / "catalogue.ts"
PLAYER_SCREEN_TSX = REPO_ROOT / "src" / "components" / "PlayerScreen.tsx"
CAPTION_RENDERER_TSX = REPO_ROOT / "src" / "components" / "CaptionRenderer.tsx"


def test_app_default_film_id_aligned_to_canonical_catalogue():
    """Verify that App.tsx initialFilmId defaults to canonical 'sintel' and imports catalogue.ts."""
    content = APP_TSX.read_text(encoding="utf-8")

    # Must import from canonical catalogue.ts, not filmCatalogue.ts
    assert "from './data/catalogue'" in content, "App.tsx must import from './data/catalogue'"
    assert "from './data/filmCatalogue'" not in content, "App.tsx must not import from './data/filmCatalogue'"

    # initialFilmId must be 'sintel'
    assert "initialFilmId = 'sintel'" in content, "App.tsx initialFilmId default must be 'sintel'"


def test_catalogue_exports_load_film_subtitles():
    """Verify that src/data/catalogue.ts exports loadFilmSubtitles and FilmCatalogueEntry supports subtitles."""
    content = CATALOGUE_TS.read_text(encoding="utf-8")
    assert "loadFilmSubtitles" in content, "catalogue.ts must export loadFilmSubtitles"
    assert "validateSubtitleCues" in content, "catalogue.ts must export validateSubtitleCues"
    assert "subtitles_path" in content, "catalogue.ts must declare subtitles_path"
    assert "loadSubtitles" in content, "catalogue.ts must declare loadSubtitles"


def test_subtitles_loading_for_both_films_and_strict_rejection():
    """Verify film-specific subtitle loading for Sintel and Tears of Steel, and strict rejection of unknown films."""
    node_script = """
    import assert from 'node:assert';
    import { loadFilmSubtitles, getFilm } from './src/data/catalogue.ts';

    // 1. Sintel subtitles load
    const sintelSubtitles = loadFilmSubtitles('sintel');
    assert.ok(Array.isArray(sintelSubtitles), 'Sintel subtitles must be an array');
    assert.ok(sintelSubtitles.length > 0, 'Sintel subtitles array must not be empty');

    // 2. Tears of Steel subtitles load
    const tosSubtitles = loadFilmSubtitles('tears_of_steel');
    assert.ok(Array.isArray(tosSubtitles), 'Tears of Steel subtitles must be an array');
    assert.ok(tosSubtitles.length >= 30, 'Tears of Steel must have multiple subtitle cues');

    // Verify first dialogue cue in Tears of Steel
    const firstTosCue = tosSubtitles[0];
    assert.strictEqual(firstTosCue.id, 'c1');
    assert.strictEqual(firstTosCue.start, 23.0);
    assert.strictEqual(firstTosCue.end, 24.5);
    assert.ok(firstTosCue.text.includes("You're a jerk, Thom"), 'First cue text must match ToS script');

    // 3. Strict rejection: unknown film must throw descriptive error, never fall back
    assert.throws(
      () => loadFilmSubtitles('unknown_movie'),
      /Unknown film ID: "unknown_movie"/,
      'loadFilmSubtitles must reject unknown film ID without silent fallback'
    );

    console.log('Subtitles loading and strict rejection verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Subtitles loading and strict rejection verified." in res.stdout


def test_caption_rendering_and_film_switching_isolation():
    """Verify caption resolution per film and clean isolation upon film switching (no stale caption leak)."""
    node_script = """
    import assert from 'node:assert';
    import { loadFilmSubtitles } from './src/data/catalogue.ts';
    import { getActiveCaption } from './src/subtitles/activeCaption.ts';

    const sintelSubtitles = loadFilmSubtitles('sintel');
    const tosSubtitles = loadFilmSubtitles('tears_of_steel');

    // 1. Tears of Steel has dialogue at t = 23.5s
    const tosActiveCaption = getActiveCaption(23.5, { cues: tosSubtitles });
    assert.ok(tosActiveCaption !== null, 'Tears of Steel must have an active caption at 23.5s');
    assert.strictEqual(tosActiveCaption.id, 'c1');
    assert.ok(tosActiveCaption.processedText.includes("You're a jerk, Thom"));

    // 2. Sintel trailer has NO dialogue at t = 23.5s (dialogue starts after 100s in full movie)
    const sintelActiveCaption = getActiveCaption(23.5, { cues: sintelSubtitles });
    assert.strictEqual(sintelActiveCaption, null, 'Sintel trailer must not have active caption at 23.5s');

    // 3. Simulate film switch: switching to Sintel resets currentTime to 0 and swaps cue set
    const switchedTime = 0.0;
    const switchedCaption = getActiveCaption(switchedTime, { cues: sintelSubtitles });
    assert.strictEqual(switchedCaption, null, 'Switched film at t=0 must have no stale captions');

    // 4. Ensure Tears of Steel captions do not leak when querying with Sintel cues
    for (let t = 0; t <= 52.0; t += 5.0) {
      const cap = getActiveCaption(t, { cues: sintelSubtitles });
      assert.strictEqual(cap, null, `Expected no Sintel trailer captions at t=${t}`);
    }

    console.log('Caption rendering and switching isolation verified.');
    """
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Caption rendering and switching isolation verified." in res.stdout


def test_player_screen_integrates_caption_renderer_and_subtitles():
    """Verify that PlayerScreen.tsx imports CaptionRenderer and renders it inside videoFrame."""
    content = PLAYER_SCREEN_TSX.read_text(encoding="utf-8")

    # 1. Imports
    assert "import {CaptionRenderer} from './CaptionRenderer';" in content
    assert "loadFilmSubtitles" in content

    # 2. Subtitles loading logic
    assert "filmSubtitles = loadFilmSubtitles(currentFilmId);" in content or "loadFilmSubtitles(currentFilmId)" in content

    # 3. CaptionRenderer JSX mounting
    assert "<CaptionRenderer" in content, "PlayerScreen must render <CaptionRenderer"
    assert "cues: filmSubtitles" in content, "CaptionRenderer must receive filmSubtitles"
    assert "currentTime={currentTime}" in content, "CaptionRenderer must receive currentTime"


def test_app_launcher_tiles_route_canonical_film_ids():
    """Verify that App.tsx tile click handlers route canonical film IDs ('sintel', 'tears_of_steel')."""
    content = APP_TSX.read_text(encoding="utf-8")

    # get-started / sintel tile
    assert "setSelectedFilmId('sintel')" in content

    # tears-of-steel tile
    assert "setSelectedFilmId('tears_of_steel')" in content


def test_subtitle_cue_runtime_validation():
    """Verify runtime subtitle cue validation rules: root array, non-null objects, required fields,
    timestamp finiteness, positive durations (start < end), valid kinds, string text, duration bounds,
    and metadata preservation."""
    node_script = '''
    import assert from 'node:assert';
    import { validateSubtitleCues } from './src/data/catalogue.ts';

    const validCue = {
      id: 'c1',
      start: 1.0,
      end: 2.5,
      kind: 'speech',
      text: 'Hello world',
      flagged_words: ['test'],
    };

    // 1. Valid cues accepted and metadata preserved
    const result = validateSubtitleCues([validCue], { filmId: 'test_film' });
    assert.strictEqual(result.length, 1);
    assert.strictEqual(result[0].id, 'c1');
    assert.deepStrictEqual(result[0].flagged_words, ['test']);

    // 2. Root payload is not an array
    for (const badRoot of [null, undefined, {}, 123, 'not an array', true]) {
      assert.throws(
        () => validateSubtitleCues(badRoot),
        /Invalid subtitle payload.*expected an array/,
        `Should reject non-array root: ${badRoot}`
      );
    }

    // 3. Null, numbers, strings, and arrays used as cue entries
    for (const badCue of [null, undefined, 42, 'string-cue', [1, 2, 3]]) {
      assert.throws(
        () => validateSubtitleCues([badCue]),
        /Invalid subtitle cue at index 0.*expected a non-null object/,
        `Should reject non-object cue entry: ${badCue}`
      );
    }

    // 4. Missing or invalid required fields
    // 4a. Missing / empty id
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, id: '' }]),
      /"id" must be a non-empty string/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, id: null }]),
      /"id" must be a non-empty string/
    );

    // 4b. Invalid / missing start
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, start: undefined }]),
      /"start" must be a finite number/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, start: NaN }]),
      /"start" must be a finite number/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, start: Infinity }]),
      /"start" must be a finite number/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, start: -0.5 }]),
      /"start" cannot be negative/
    );

    // 4c. Invalid / missing end
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, end: undefined }]),
      /"end" must be a finite number/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, end: NaN }]),
      /"end" must be a finite number/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, end: -1.0 }]),
      /"end" cannot be negative/
    );

    // 5. Zero-length cues and reversed timestamps (start >= end)
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, start: 2.0, end: 2.0 }]),
      /"start" \\(2\\) must be strictly less than "end" \\(2\\)/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, start: 3.5, end: 2.0 }]),
      /"start" \\(3.5\\) must be strictly less than "end" \\(2\\)/
    );

    // 6. Invalid cue kinds
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, kind: 'audio' }]),
      /"kind" must be either "speech" or "sound_description"/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, kind: 123 }]),
      /"kind" must be either "speech" or "sound_description"/
    );

    // 7. Invalid text types
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, text: 123 }]),
      /"text" must be a string/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, text: null }]),
      /"text" must be a string/
    );

    // 8. Duration boundary enforcement when authoritative maxDuration supplied
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, start: 50.0, end: 55.0 }], { filmId: 'short_film', maxDuration: 52.0 }),
      /exceeds film duration/
    );
    const withinDuration = validateSubtitleCues(
      [{ ...validCue, start: 10.0, end: 50.0 }],
      { filmId: 'short_film', maxDuration: 52.0 }
    );
    assert.strictEqual(withinDuration.length, 1);

    // 9. Optional metadata invalid shapes
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, flagged: 'not-an-array' }]),
      /optional "flagged" must be an array/
    );
    assert.throws(
      () => validateSubtitleCues([{ ...validCue, flagged_words: 'not-an-array' }]),
      /optional "flagged_words" must be an array/
    );

    console.log('Subtitle cue runtime validation verified.');
    '''
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\\n{res.stderr}"
    assert "Subtitle cue runtime validation verified." in res.stdout


def test_player_screen_validates_custom_subtitles_contract():
    """Verify that PlayerScreen.tsx imports validateSubtitleCues and validates customSubtitles prop."""
    content = PLAYER_SCREEN_TSX.read_text(encoding="utf-8")
    assert "validateSubtitleCues" in content, "PlayerScreen.tsx must import validateSubtitleCues"
    assert "validateSubtitleCues(customSubtitles" in content, "PlayerScreen.tsx must validate customSubtitles prop"


def test_malformed_custom_cues_and_censoring_safety():
    """Verify that malformed custom cues and non-string text never cause unhandled crashes in activeCaption or censoring."""
    node_script = '''
    import assert from 'node:assert';
    import { validateSubtitleCues } from './src/data/catalogue.ts';
    import { getActiveCaption } from './src/subtitles/activeCaption.ts';
    import { censorText, processCueText, extractFlaggedWords } from './src/subtitles/censoring.ts';

    // 1. censorText handles non-string and empty inputs gracefully without throwing
    assert.strictEqual(censorText(null, ['curse']), '');
    assert.strictEqual(censorText(undefined, ['curse']), '');
    assert.strictEqual(censorText(12345, ['curse']), '');
    assert.strictEqual(censorText({}, ['curse']), '');
    assert.strictEqual(censorText('damn', ['damn']), 'd**n');

    // 2. processCueText handles speech and sound_description correctly
    assert.strictEqual(processCueText('damn it', 'speech', ['damn'], false), 'd**n it');
    assert.strictEqual(processCueText('[creaking door]', 'sound_description', [], true), '[sound muted]');
    assert.strictEqual(processCueText('[creaking door]', 'sound_description', [], false), '[creaking door]');

    // 3. extractFlaggedWords safely ignores malformed flagged arrays
    assert.deepStrictEqual(extractFlaggedWords({ id: 'c1', start: 0, end: 1, kind: 'speech', text: 'hi', flagged: 'not-array' }), []);
    assert.deepStrictEqual(extractFlaggedWords({ id: 'c1', start: 0, end: 1, kind: 'speech', text: 'hi', flagged_words: 'not-array' }), []);

    // 4. getActiveCaption never crashes on malformed user cues
    // 4a. Non-array cues option returns null safely
    assert.strictEqual(getActiveCaption(5.0, { cues: 'not-an-array' }), null);
    assert.strictEqual(getActiveCaption(5.0, { cues: null }), null);

    // 4b. Array containing nulls, primitives, or malformed objects
    const malformedCues = [
      null,
      undefined,
      42,
      'cue-string',
      { start: 2.0, end: 4.0 }, // missing id, text, kind
      { id: 'c_bad', start: 4.0, end: 6.0, kind: 123, text: 9999 }, // non-string text, non-string kind
    ];
    // Must not crash when evaluating at t=5.0 where malformed cue matches time interval
    const result = getActiveCaption(5.0, { cues: malformedCues });
    assert.ok(result !== null);
    assert.strictEqual(result.id, 'c_bad');
    assert.strictEqual(result.rawText, '');
    assert.strictEqual(result.kind, 'speech');
    assert.strictEqual(result.processedText, '');

    // 5. Valid custom cue censoring in getActiveCaption
    const validCustomCues = [
      {
        id: 'c_custom',
        start: 10.0,
        end: 15.0,
        kind: 'speech',
        text: 'This is a damn good test',
        flagged_words: ['damn'],
      },
    ];
    const validResult = getActiveCaption(12.0, { cues: validCustomCues });
    assert.ok(validResult !== null);
    assert.strictEqual(validResult.id, 'c_custom');
    assert.strictEqual(validResult.processedText, 'This is a d**n good test');

    console.log('Malformed custom cues and censoring safety verified.');
    '''
    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\\n{res.stderr}"
    assert "Malformed custom cues and censoring safety verified." in res.stdout



def test_seek_bar_presence_and_panresponder_in_playerscreen():
    """Verify that PlayerScreen.tsx implements a functional seek bar with PanResponder, progress fill, and thumb."""
    content = PLAYER_SCREEN_TSX.read_text(encoding="utf-8")

    # 1. Must import PanResponder from react-native
    assert "PanResponder" in content, "PlayerScreen.tsx must import PanResponder"

    # 2. Must render interactive seek bar elements
    assert 'testID="seek-bar-container"' in content, "PlayerScreen must render seek-bar-container"
    assert 'testID="seek-bar"' in content, "PlayerScreen must render seek-bar touchable"
    assert 'testID="seek-bar-track"' in content, "PlayerScreen must render seek-bar-track"
    assert 'testID="seek-bar-progress"' in content, "PlayerScreen must render seek-bar-progress"
    assert 'testID="seek-bar-thumb"' in content, "PlayerScreen must render seek-bar-thumb"

    # 3. PanResponder gesture lifecycle handlers
    assert "onPanResponderGrant" in content, "PlayerScreen must handle onPanResponderGrant"
    assert "onPanResponderMove" in content, "PlayerScreen must handle onPanResponderMove"
    assert "onPanResponderRelease" in content, "PlayerScreen must handle onPanResponderRelease"

    # 4. Connected to native video seek API (seekTo)
    assert "calculateSeekTimeFromX" in content, "PlayerScreen must compute target seek time from X coordinate"
    assert "seekTo(finalTarget)" in content or "seekTo(target)" in content, "Seek bar must trigger seekTo"

    # 5. Exported formatTime handles < 3600s and >= 3600s (1-hour films)
    assert "export function formatTime" in content, "PlayerScreen must export formatTime"
    assert "hrs > 0" in content, "formatTime must format hours for 1-hour movies"


def test_seeking_preserves_subtitles_and_trigger_resolution_across_films():
    """Verify that seeking to timestamps throughout both films updates subtitles and trigger actions without drift."""
    node_script = """
    import assert from 'node:assert';
    import { loadFilmSubtitles, loadFilmCues } from './src/data/catalogue.ts';
    import { getActiveCaption } from './src/subtitles/activeCaption.ts';
    import { resolveActions } from './src/engine/resolveActions.ts';
    import { DEFAULT_VIEWER_OPTIONS } from './src/engine/types.ts';

    // 1. Sintel seeking checks
    const sintelSubtitles = loadFilmSubtitles('sintel');
    const sintelCues = loadFilmCues('sintel');

    // Seek to 23.0s in Sintel:
    // Sintel trailer duration is 52s; trailer subtitles do not have dialogue at 23s
    assert.strictEqual(getActiveCaption(23.0, { cues: sintelSubtitles }), null);
    // Trigger at 23.0s is explosion (22.0 to 24.5)
    const sintelActions23 = resolveActions(23.0, sintelCues, { ...DEFAULT_VIEWER_OPTIONS, actionOverrides: { explosion: 'mute' } });
    assert.strictEqual(sintelActions23.soundGain, 0, 'Sintel audio must be muted during explosion cue at 23s');
    assert.ok(sintelActions23.activeCueIds.some(id => id.includes('explosion')));

    // 2. Tears of Steel seeking checks (12-minute timeline)
    const tosSubtitles = loadFilmSubtitles('tears_of_steel');
    const tosCues = loadFilmCues('tears_of_steel');

    // Seek to 25.5s (TOS dialogue cue c2: 25.0s - 27.0s)
    const tosCaption25 = getActiveCaption(25.5, { cues: tosSubtitles });
    assert.ok(tosCaption25 !== null);
    assert.strictEqual(tosCaption25.id, 'c2');
    assert.ok(tosCaption25.processedText.includes('follow our passions'));

    // Seek to 360.0s (6 minutes into film: profanity trigger and cue c44)
    const tosActions360 = resolveActions(360.0, tosCues, DEFAULT_VIEWER_OPTIONS);
    assert.strictEqual(tosActions360.soundGain, 0, 'Audio must be muted during profanity cue at 360s');
    assert.ok(tosActions360.activeCueIds.some(id => id.includes('profanity')));

    // Seek to 650.5s (10.8 minutes into film: scream trigger)
    const tosActions650 = resolveActions(650.5, tosCues, { ...DEFAULT_VIEWER_OPTIONS, actionOverrides: { scream: 'mute' } });
    assert.strictEqual(tosActions650.soundGain, 0, 'Audio must be muted during scream cue at 650.5s');
    assert.ok(tosActions650.activeCueIds.some(id => id.includes('scream')));

    // Seek between cues at 400.0s: audio must be unmuted and caption null
    const tosActions400 = resolveActions(400.0, tosCues, DEFAULT_VIEWER_OPTIONS);
    assert.strictEqual(tosActions400.soundGain, 1, 'Audio must be unmuted outside trigger intervals at 400s');
    assert.strictEqual(tosActions400.visualAction, null, 'Visual action must be null at 400s');

    console.log('Seeking subtitle and trigger synchronization verified across both films.');
    """

    res = subprocess.run(
        ["node", "--loader", "./scripts/ts_loader.mjs", "-e", node_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"Node script failed:\n{res.stderr}"
    assert "Seeking subtitle and trigger synchronization verified across both films." in res.stdout
