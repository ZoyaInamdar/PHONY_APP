# Feedback and Team Decisions

## 2026-10-14 — Person A

### Current implementation
- The current internal cue engine and playback-state tests pass.
- Review-required skip cues are downgraded to a source-appropriate fallback when the viewer has not confirmed the skip.
- Skip history is cleared on reset and can be cleared for cues after a rewind position.

### Proposed, not yet approved
- Suppress a second skip when seeking into a previously handled cue and apply a fallback action.
- Trigger a skip immediately when seeking into an unhandled skip cue.
- Define a playback pass and specify when skip and prompt history reset.
- Extend internal cue metadata for adapter-provided fallback actions and modality information.

### Awaiting team confirmation
- Person B: final action vocabulary and packaged index/manifest contract.
- Team: seek behavior, fallback-action source, and playback-pass lifecycle.
- Shared schema/config changes remain outside Person A's scope until approved.

### Working rule
Treat recommendations as proposals until the responsible owner confirms them. Keep the current internal engine isolated from packaged data formats through an adapter.


## 2026-10-14 — Packaged-index adapter

### Current implementation
- A separate adapter converts packaged detection cues into internal playback cues using caller-supplied trigger policies.
- Disabled triggers are skipped.
- Unsupported actions and source modalities are rejected.
- The adapter preserves cue IDs and timestamps.

### Verified tests
- Full Jest suite: 7 suites passed, 55 tests passed.
- TypeScript typecheck passed.
- `git diff --check` passed.

### Awaiting team confirmation
- Agree how configured `dim` and `blur` actions map to playback behavior.
- Agree how `text` modality should be handled.
- Confirm the adapter's input contract against the shared configuration and packaged-index producer.
- Confirm the appropriate integration point without changing shared schemas or configuration unilaterally.

### Working rule
The adapter's fixture tests establish behavior for explicit policies only. They do not establish that the live configuration is compatible. Keep that distinction explicit until integration is verified.
## 2026-10-14 — Phase 0 completion boundary

### Decision
- Internal-engine Phase 0 work is considered complete against the current internal contract.
- End-to-end integration remains pending.
- Passing unit tests do not establish compatibility with the live packaged index or shared configuration.

### Required before end-to-end closure
- Confirm the packaged-index contract and action/modality mapping with the relevant owners.
- Test the adapter against representative packaged data using agreed policies.
- Re-run the full test suite and TypeScript typecheck.

### Scope
This decision does not authorize changes to shared config, schemas, or the packaging pipeline.
