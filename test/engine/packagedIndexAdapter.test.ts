import { adaptPackagedIndex } from '../../src/engine/packagedIndexAdapter';

const packagedIndex = {
schema_version: 1,
film_id: 'sintel',
timeline: {
duration_seconds: 52,
timestamp_unit: 'seconds',
interval_convention: 'half_open',
fps: 24,
},
cues: [
{
id: 'cue-a',
start: 10.2,
end: 11.8,
trigger: 'loud_spike',
score: 0.92,
source: 'loudness_detector',
},
],
};

const triggerPolicies = {
loud_spike: {
enabled: true,
action: 'mute',
source: 'audio',
},
fast_motion: {
enabled: false,
action: 'warn',
source: 'visual',
},
flashing: {
enabled: true,
action: 'dim',
source: 'visual',
},
spider: {
enabled: true,
action: 'blur',
source: 'visual',
},
};

describe('adaptPackagedIndex', () => {
it('converts a supported cue and preserves its ID and timing', () => {
const result = adaptPackagedIndex(packagedIndex, triggerPolicies);

expect(result).toHaveLength(1);
expect(result[0]).toMatchObject({
  id: 'cue-a',
  start: 10.2,
  end: 11.8,
  source: 'audio',
  action: 'mute',
});

});

it('does not activate cues belonging to disabled triggers', () => {
const index = {
...packagedIndex,
cues: [
{
...packagedIndex.cues[0],
trigger: 'fast_motion',
},
],
};

expect(adaptPackagedIndex(index, triggerPolicies)).toEqual([]);

});

it('rejects unsupported dim actions instead of silently changing them', () => {
const index = {
...packagedIndex,
cues: [
{
...packagedIndex.cues[0],
trigger: 'flashing',
},
],
};

expect(() => adaptPackagedIndex(index, triggerPolicies)).toThrow(/dim/i);

});

it('rejects unsupported blur actions instead of silently changing them', () => {
const index = {
...packagedIndex,
cues: [
{
...packagedIndex.cues[0],
trigger: 'spider',
},
],
};

expect(() => adaptPackagedIndex(index, triggerPolicies)).toThrow(/blur/i);

});

it('rejects malformed packaged indexes', () => {
expect(() =>
adaptPackagedIndex(
{ ...packagedIndex, schema_version: '1' },
triggerPolicies,
),
).toThrow();
});
});