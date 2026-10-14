import { Cue, CueAction, CueSource } from './types';

type TriggerPolicy = {
enabled: boolean;
action: string;
source: string;
};

type TriggerPolicies = Record<string, TriggerPolicy>;

type PackagedCue = {
id: string;
start: number;
end: number;
trigger: string;
score: number;
source: string;
};

type PackagedIndex = {
schema_version: number;
film_id: string;
timeline: {
duration_seconds: number;
timestamp_unit: string;
interval_convention: string;
fps?: number;
};
cues: PackagedCue[];
};

const SUPPORTED_ACTIONS = new Set<CueAction>([
'hide',
'mute',
'reduce',
'skip',
'warn',
]);

const SUPPORTED_SOURCES = new Set<CueSource>(['visual', 'audio']);

function isRecord(value: unknown): value is Record<string, unknown> {
return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validatePackagedIndex(input: unknown): PackagedIndex {
if (!isRecord(input)) {
throw new Error('Packaged index must be an object.');
}

if (input.schema_version !== 1) {
throw new Error('Unsupported packaged index schema_version.');
}

if (typeof input.film_id !== 'string' || input.film_id.length === 0) {
throw new Error('Packaged index film_id must be a non-empty string.');
}

if (!isRecord(input.timeline)) {
throw new Error('Packaged index timeline must be an object.');
}

const timeline = input.timeline;

if (
typeof timeline.duration_seconds !== 'number' ||
!Number.isFinite(timeline.duration_seconds) ||
timeline.duration_seconds <= 0
) {
throw new Error('Timeline duration_seconds must be a positive finite number.');
}

if (
timeline.timestamp_unit !== 'seconds' ||
timeline.interval_convention !== 'half_open'
) {
throw new Error('Unsupported timeline units or interval convention.');
}

if (!Array.isArray(input.cues)) {
throw new Error('Packaged index cues must be an array.');
}

for (const cue of input.cues) {
if (!isRecord(cue)) {
throw new Error('Each packaged cue must be an object.');
}

if (
  typeof cue.id !== 'string' ||
  cue.id.length === 0 ||
  typeof cue.trigger !== 'string' ||
  cue.trigger.length === 0 ||
  typeof cue.start !== 'number' ||
  !Number.isFinite(cue.start) ||
  typeof cue.end !== 'number' ||
  !Number.isFinite(cue.end) ||
  cue.start < 0 ||
  cue.end <= cue.start ||
  cue.end > timeline.duration_seconds ||
  typeof cue.score !== 'number' ||
  !Number.isFinite(cue.score) ||
  cue.score < 0 ||
  cue.score > 1 ||
  typeof cue.source !== 'string' ||
  cue.source.length === 0
) {
  throw new Error('Packaged index contains an invalid cue.');
}

}

return input as unknown as PackagedIndex;
}

export function adaptPackagedIndex(
indexInput: unknown,
policies: TriggerPolicies,
): Cue[] {
const index = validatePackagedIndex(indexInput);
const result: Cue[] = [];

for (const packagedCue of index.cues) {
const policy = policies[packagedCue.trigger];

if (!policy) {
  throw new Error(`Missing policy for trigger "${packagedCue.trigger}".`);
}

if (!policy.enabled) {
  continue;
}

if (!SUPPORTED_ACTIONS.has(policy.action as CueAction)) {
  throw new Error(
    `Unsupported action "${policy.action}" for trigger "${packagedCue.trigger}".`,
  );
}

if (!SUPPORTED_SOURCES.has(policy.source as CueSource)) {
  throw new Error(
    `Unsupported source "${policy.source}" for trigger "${packagedCue.trigger}".`,
  );
}

result.push({
  id: packagedCue.id,
  start: packagedCue.start,
  end: packagedCue.end,
  source: policy.source as CueSource,
  action: policy.action as CueAction,
  status: 'ready',
});

}

return result;
}