import { resolveValidatedPlayback } from '../../src/engine/validatedPlayback';
import { DEFAULT_VIEWER_OPTIONS } from '../../src/engine/types';

const validIndex = {
schema_version: '1.0.0',
cues: [
{
id: 'visual-001',
start: 2,
end: 4,
source: 'visual',
action: 'hide',
status: 'ready',
},
],
};

describe('resolveValidatedPlayback', () => {
test('resolves actions for valid, complete analysis', () => {
const result = resolveValidatedPlayback(
3,
validIndex,
{ status: 'complete' },
DEFAULT_VIEWER_OPTIONS
);

expect(result.hide).toBe(true);
expect(result.soundGain).toBe(1);
expect(result.skipTo).toBeNull();

});

test('refuses playback when analysis is partial', () => {
expect(() =>
resolveValidatedPlayback(
3,
validIndex,
{ status: 'partial' },
DEFAULT_VIEWER_OPTIONS
)
).toThrow(/Playback refused/);
});

test('refuses playback when analysis failed', () => {
expect(() =>
resolveValidatedPlayback(
3,
validIndex,
{ status: 'failed' },
DEFAULT_VIEWER_OPTIONS
)
).toThrow(/Playback refused/);
});

test('refuses invalid cue data', () => {
const invalidIndex = {
...validIndex,
cues: [{ ...validIndex.cues[0], action: 'blur' }],
};

expect(() =>
  resolveValidatedPlayback(
    3,
    invalidIndex,
    { status: 'complete' },
    DEFAULT_VIEWER_OPTIONS
  )
).toThrow(/Invalid cue at index 0/);

});

test('refuses a missing manifest', () => {
expect(() =>
resolveValidatedPlayback(
3,
validIndex,
undefined,
DEFAULT_VIEWER_OPTIONS
)
).toThrow();
});

test('refuses an invalid manifest status', () => {
expect(() =>
resolveValidatedPlayback(
3,
validIndex,
{ status: 'unknown' },
DEFAULT_VIEWER_OPTIONS
)
).toThrow();
});

test('refuses a malformed cue index', () => {
expect(() =>
resolveValidatedPlayback(
3,
{ schema_version: '1.0.0', cues: 'not-an-array' },
{ status: 'complete' },
DEFAULT_VIEWER_OPTIONS
)
).toThrow();
});

test('refuses invalid playback time', () => {
expect(() =>
resolveValidatedPlayback(
-1,
validIndex,
{ status: 'complete' },
DEFAULT_VIEWER_OPTIONS
)
).toThrow(/finite, non-negative/);
});

test('rejects a manifest with unexpected fields', () => {
expect(() =>
resolveValidatedPlayback(
3,
validIndex,
{ status: 'complete', unexpected: true },
DEFAULT_VIEWER_OPTIONS
)
).toThrow();
});

test('rejects non-finite cue timestamps', () => {
const invalidIndex = {
...validIndex,
cues: [{ ...validIndex.cues[0], start: Number.NaN }],
};

expect(() =>
  resolveValidatedPlayback(
    3,
    invalidIndex,
    { status: 'complete' },
    DEFAULT_VIEWER_OPTIONS
  )
).toThrow(/Invalid cue at index 0/);

});

test('rejects a cue whose end is not greater than its start', () => {
const invalidIndex = {
...validIndex,
cues: [{ ...validIndex.cues[0], start: 4, end: 4 }],
};

expect(() =>
  resolveValidatedPlayback(
    3,
    invalidIndex,
    { status: 'complete' },
    DEFAULT_VIEWER_OPTIONS
  )
).toThrow(/Invalid cue at index 0|end must be greater than start/);

});
});