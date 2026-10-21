import { Cue, CueAction, CueSource, CueStatus } from './types';

/**
 * A trigger policy record, either from the legacy per-test fixture format or
 * from the shared `config.yaml` triggers section.
 *
 * Shared config fields take precedence over the legacy `action`/`source` fields
 * when both are present.
 */
type TriggerPolicy = {
  enabled: boolean;

  // Legacy fields — kept for existing callers and fixture tests.
  action?: string;
  source?: string;

  // Shared config fields (config.yaml → triggers.<id>).
  modality?: string;
  default_action?: string;
  allowed_actions?: string[];
};

type TriggerPolicies = Record<string, TriggerPolicy>;

type PackagedCue = {
  id: string;
  start: number;
  end: number;
  trigger: string;
  score: number;
  source: string;
  status?: CueStatus;
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

/**
 * Actions the player engine can carry and represent.
 * Supported actions: 'hide', 'mute', 'reduce', 'skip', 'warn', 'blur', 'dim'.
 */
const REPRESENTABLE_ACTIONS = new Set<CueAction>([
  'hide',
  'mute',
  'reduce',
  'skip',
  'warn',
  'blur',
  'dim',
]);

/**
 * Source modalities the engine can carry.
 */
const REPRESENTABLE_SOURCES = new Set<CueSource>([
  'visual',
  'audio',
  'text',
  'audio_visual',
]);

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

  const seenCueIds = new Set<string>();
  let previousCueStart = -Infinity;

  for (const cue of input.cues) {
    if (!isRecord(cue)) {
      throw new Error('Each packaged cue must be an object.');
    }

    if (typeof cue.id !== 'string' || cue.id.length === 0) {
      throw new Error('Packaged index contains an invalid cue ID.');
    }

    if (seenCueIds.has(cue.id)) {
      throw new Error(`Duplicate cue ID "${cue.id}".`);
    }
    seenCueIds.add(cue.id);

    if (
      typeof cue.start !== 'number' ||
      !Number.isFinite(cue.start) ||
      typeof cue.end !== 'number' ||
      !Number.isFinite(cue.end) ||
      cue.start < 0 ||
      cue.end <= cue.start ||
      cue.end > timeline.duration_seconds
    ) {
      throw new Error(`Packaged index contains an invalid cue "${cue.id}".`);
    }

    if (cue.start < previousCueStart) {
      throw new Error('Packaged cues must be in chronological order.');
    }
    previousCueStart = cue.start;

    if (
      typeof cue.trigger !== 'string' ||
      cue.trigger.length === 0 ||
      typeof cue.score !== 'number' ||
      !Number.isFinite(cue.score) ||
      cue.score < 0 ||
      cue.score > 1 ||
      typeof cue.source !== 'string' ||
      cue.source.length === 0
    ) {
      throw new Error(
        `Packaged index contains an invalid cue: missing or invalid trigger, score, or detector source for "${cue.id}".`,
      );
    }

    if (
      cue.status !== undefined &&
      cue.status !== 'ready' &&
      cue.status !== 'needs_review'
    ) {
      throw new Error(
        `Packaged index contains an invalid status "${String(cue.status)}" for cue "${cue.id}".`,
      );
    }
  }

  return input as unknown as PackagedIndex;
}

/**
 * Adapt a packaged detection index into playback `Cue[]` using the supplied
 * trigger policies.
 *
 * Policies are keyed by canonical trigger ID (e.g. `"flashing"`, `"spider"`).
 * When the shared `config.yaml` triggers section is used directly, pass the
 * trigger objects as-is; the adapter understands both the legacy
 * `action`/`source` shape and the shared-config `modality`/`default_action`
 * shape.
 *
 * Disabled triggers produce no output cues (they are silently skipped).
 *
 * Throws for:
 * - Missing policy for a trigger in the index.
 * - An action that is not in the shared allowed_actions list for that trigger.
 * - An action or source that cannot be represented by the engine at all.
 */
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
      // Disabled triggers (e.g. fast_motion, jump_scare) are skipped.
      continue;
    }

    // Prefer shared-config field names; fall back to legacy field names.
    const action = policy.default_action ?? policy.action;
    const source = policy.modality ?? policy.source;

    if (typeof action !== 'string' || !REPRESENTABLE_ACTIONS.has(action as CueAction)) {
      throw new Error(
        `Unsupported action "${String(action)}" for trigger "${packagedCue.trigger}".`,
      );
    }

    if (typeof source !== 'string' || !REPRESENTABLE_SOURCES.has(source as CueSource)) {
      throw new Error(
        `Unsupported source "${String(source)}" for trigger "${packagedCue.trigger}".`,
      );
    }

    if (
      policy.allowed_actions &&
      !policy.allowed_actions.includes(action)
    ) {
      throw new Error(
        `Action "${action}" is not allowed for trigger "${packagedCue.trigger}".`,
      );
    }

    result.push({
      id: packagedCue.id,
      start: packagedCue.start,
      end: packagedCue.end,
      source: source as CueSource,
      action: action as CueAction,
      status: packagedCue.status ?? 'ready',
      trigger: packagedCue.trigger,
      score: packagedCue.score,
      detectorSource: packagedCue.source,
    });
  }

  return result;
}
