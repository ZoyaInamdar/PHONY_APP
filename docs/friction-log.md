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
