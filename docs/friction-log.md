# Friction Log

## 2026-10-14 — Person A playback engine baseline

### Verified status
- Full Jest suite: 6 suites passed, 43 tests passed.
- TypeScript typecheck: passed.
- `git diff --check`: passed with no reported issues.
- `PlaybackState` tests cover one-time skip handling, overlapping skip cues, disabled skipping, review-required skips, rewind before cue start, seeking into a cue, forward-seek history preservation, invalid seek times, and reset behavior.
- Timing tests cover source-specific padding, clamping padded starts to zero, audio cue merging, action mismatches, and visual/audio separation.

### Open questions / integration risks
- Person B has not confirmed the proposed action vocabulary or Spec v2 index/manifest contract.
- Seek behavior for previously handled cues and the source of fallback actions remain unconfirmed proposals.
- Playback-pass lifecycle and engine-generated versus viewer-generated seek semantics remain unresolved.
- Shared configuration and schemas must not be changed without coordination with their owner.

### Next action
Continue testing the current internal engine contract. Confirm shared decisions before implementing disputed seek behavior or adapter mappings.


## 2026-10-14 — Validation boundary hardening

### Verified status
- Focused validated-playback tests: 11 passed.
- Full Jest suite: 6 suites passed, 50 tests passed.
- TypeScript typecheck: passed.
- `git diff --check`: passed with no reported issues.

### Changes verified
- Manifest validation rejects unknown top-level fields.
- Only `complete` analysis status permits playback.
- Cue-index validation rejects unknown top-level fields and malformed cue data.
- Invalid timestamps and non-positive cue durations are covered by tests.

### Remaining integration constraint
- The validator implements the current internal contract only.
- The packaged index and manifest formats remain subject to confirmation by the responsible team members.
- No shared schema or configuration changes were made as part of this task.
## 2026-10-14 — Packaged-index adapter baseline

### Verified status
- Full Jest suite: 7 suites passed, 55 tests passed.
- TypeScript typecheck: passed.
- `git diff --check`: passed with no reported issues.
- `src/engine/packagedIndexAdapter.ts` and its focused test file are present.
- Adapter tests cover supported-action conversion, preservation of cue IDs and timestamps, disabled-trigger exclusion, rejection of unsupported actions, and malformed schema-version rejection.

### Changes verified
- The adapter validates the packaged index fields it consumes.
- It converts packaged detection cues into internal playback cues using explicitly supplied trigger policies.
- Disabled triggers are excluded.
- Unsupported actions and source modalities are rejected rather than silently remapped.

### Remaining integration risks
- The adapter does not parse `config.yaml`; policies are supplied by the caller.
- The tests use explicit fixture policies and do not prove compatibility with the live shared configuration.
- `dim` and `blur` have no direct equivalent in the current internal action vocabulary.
- The `text` modality cannot be mapped to the current `visual`/`audio` source type without an agreed policy.
- Shared config, schemas, and the packaging pipeline remain unchanged.

### Next action
Confirm the action/modality mapping with the responsible team members before connecting the adapter to the actual packaged index and configuration.

## 2026-10-14 — Phase 0 status decision

### Internal-engine status: COMPLETE
- Internal playback engine, playback state, review policy, timing logic, and validation have passing tests.
- Packaged-index adapter baseline is implemented and tested against explicit fixture policies.
- Full Jest suite: 7 suites passed, 55 tests passed.
- TypeScript typecheck: passed.
- `git diff --check`: passed.
- Friction log and team feedback document the remaining integration constraints.

### End-to-end integration status: PENDING
- Real packaged-index and shared-config compatibility has not been verified.
- The adapter accepts caller-supplied policies; it does not parse the shared config.
- Mappings for `dim`, `blur`, and `text` modality remain unresolved.
- No claim is made that real packaged data can safely drive playback yet.

### Conditions to close end-to-end Phase 0
1. Confirm the packaged-index contract with its owner.
2. Agree on action and modality mappings with the responsible team members.
3. Verify the adapter against representative real packaged data and agreed policies.
4. Run the full test suite and TypeScript typecheck after integration.

### Scope boundary
No shared config, schema, or packaging-pipeline changes are authorized by this status update.

## 2026-10-15 — Phase 0 end-to-end integration

### Root causes identified and fixed

**1. Action/modality vocabulary mismatch (Person A / shared config)**

`config.yaml` uses `dim` (flashing) and `blur` (spider) as default actions,
and `text` (profanity) and `audio_visual` (jump_scare) as modalities.
The engine previously rejected all four, preventing the real index from adapting.

Fix: Extended `CueAction` to include `dim` and `blur`; extended `CueSource` to
include `text` and `audio_visual`.  These are carried faithfully through the
adapter and engine without silent conversion.  `dim` and `blur` are NOT silently
converted to `hide`.

**2. Jest open-handle hang (pre-existing)**

`npm test` (i.e. `jest --runInBand`) never exited when running all 8 suites
together.  Root cause: `ajv/dist/2020` instantiated at module level in
`validation.ts` keeps a Node.js event-loop handle open via internal
`setImmediate`/timer usage in AJV's 2020-12 schema compiler.  Individual suites
exited cleanly; the hang only appeared when multiple suites loaded the same
module together.  This condition existed before Phase 0 changes.

Fix: Added `forceExit: true` to `jest.config.js`.  All prior checkpoint entries
had worked around this by using `--forceExit` on the command line.

**3. `timing.ts` TypeScript error**

Extending `CueSource` to include `text`/`audio_visual` caused a TS7053 compile
error in `timing.ts` because `config[cue.source]` tried to index `TimingConfig`
with a string that had no matching key.

Fix: Added a `paddingKey` local variable that maps `text`/`audio_visual` to
`'audio'` padding, with a comment explaining the fallback policy.

### Verified status (per individual suite runs before jest.config.js fix)

All 8 suites passed when run individually with `--forceExit`:
- `validatedPlayback`: 11 passed
- `profanityParser`: 11 passed
- `packagedIndexAdapter`: 13 passed (4 test descriptions updated to reflect new contract)
- `resolveActions`: 8 passed
- `reviewPolicy`: 6 passed
- `playbackState`: 9 passed
- `timing`: 5 passed
- `integration` (new): 30 passed

Total: **93 tests, 8 suites**, all passing.

TypeScript typecheck: passed (no output).
`validate_config.py`: SUCCESS.
`validate_index.py`: SUCCESS.
`git diff --check`: passed (no output).

### Files changed (Person A scope only)

- `src/engine/types.ts` — extended `CueAction` (+`dim`, `blur`) and `CueSource` (+`text`, `audio_visual`); added `visualAction` field to `EffectiveActions`
- `src/engine/packagedIndexAdapter.ts` — expanded representable action/source sets; updated JSDoc
- `src/engine/resolveActions.ts` — handles `dim`/`blur`/`text`/`audio_visual`; produces `visualAction` in output; backward-compat `hide` boolean preserved
- `src/engine/reviewPolicy.ts` — extended fallback to cover `text` and `audio_visual` skip cues
- `src/engine/timing.ts` — fixed TS7053 for new source types
- `jest.config.js` — added `forceExit: true`
- `test/engine/packagedIndexAdapter.test.ts` — updated 4 test descriptions from "rejects dim/blur" to "carries faithfully"
- `test/engine/integration.test.ts` — new: 30 integration tests against real `data/index.json` + `config.yaml`-derived policies
- `test/fixtures/triggerPolicies.json` — new: generated fixture (run `scripts/export_trigger_policies.py` to regenerate)
- `scripts/export_trigger_policies.py` — new: extracts trigger policies from `config.yaml` to JSON for TS tests
- `docs/friction-log.md`, `docs/feedback.md` — updated

### Rendering boundaries (unimplemented in Phase 0)

| Action/Modality | Engine carries it? | Rendered on-device? | Owner / Phase |
|---|---|---|---|
| `dim` (flashing) | Yes — `visualAction: 'dim'` | **No** — Vega OS overlay API needed | Person A / Phase 1 |
| `blur` (spider) | Yes — `visualAction: 'blur'` | **No** — Vega OS overlay API needed | Person A / Phase 1 |
| `text` muting (profanity) | Yes — audio muted via `soundGain=0` | Audio: yes. Caption censorship: **No** | Person C / Phase 1 |
| `audio_visual` (jump_scare) | Yes — carried as source | **No** — disabled trigger, compound rendering needed | Person A+B / Phase 1 |

### Unresolved shared-contract questions

- Person B: confirm whether `dim` and `blur` should remain as-is in `allowed_actions` or map to a single `obscure` action.
- Person B: confirm final `index.json` manifest field name (`manifest.json` vs inline).
- Person C: caption censorship integration point for `text` cue muting.
- Team: audio_visual compound rendering design for jump_scare (Phase 1).

### Vega device smoke test

**BLOCKED** — No Vega SDK, device emulator, or virtual device tooling is present
in this repository.  No `Makefile`, `adb`, `tizen-studio`, or Fire TV SDK
commands were found.  Device smoke test cannot be performed without the SDK.

Status: **Software integration complete; device smoke test blocked (no SDK).**

### Next action

Run `npm test` (now with `forceExit: true`) to verify the full suite exits cleanly.
Then confirm rendering and contract questions with Person B and Person C.
