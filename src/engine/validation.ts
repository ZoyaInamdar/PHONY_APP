
import Ajv2020 from 'ajv/dist/2020';
import cueSchema from '../../schema/cue.schema.json';
import { Cue } from './types';

const ajv = new Ajv2020({ allErrors: true, strict: true });
const validateCue = ajv.compile(cueSchema);

export interface CueIndex {
  schema_version: string;
  cues: Cue[];
}

export interface AnalysisManifest {
  status: 'complete' | 'partial' | 'failed';
}

export function validateCueIndex(input: unknown): CueIndex {
  if (
    typeof input !== 'object' ||
    input === null ||
    Array.isArray(input)
  ) {
    throw new Error('Invalid index.json: expected a JSON object.');
  }

  const index = input as Record<string, unknown>;

  const allowedKeys = ['schema_version', 'cues'];
  const unknownKeys = Object.keys(index).filter(
    (key) => !allowedKeys.includes(key)
  );

  if (unknownKeys.length > 0) {
    throw new Error(
      `Invalid index.json: unknown field(s): ${unknownKeys.join(', ')}.`
    );
  }

  if (
    typeof index.schema_version !== 'string' ||
    index.schema_version.length === 0
  ) {
    throw new Error('Invalid index.json: schema_version is required.');
  }

  if (!Array.isArray(index.cues)) {
    throw new Error('Invalid index.json: cues must be an array.');
  }

  for (let i = 0; i < index.cues.length; i += 1) {
    const cue = index.cues[i];

    if (!validateCue(cue)) {
      const details = ajv.errorsText(validateCue.errors, {
        separator: '; ',
      });

      throw new Error(`Invalid cue at index ${i}: ${details}`);
    }

    const checkedCue = cue as unknown as Cue;

    if (checkedCue.end <= checkedCue.start) {
      throw new Error(
        `Invalid cue "${checkedCue.id}": end must be greater than start.`
      );
    }

    if (
      checkedCue.action === 'reduce' &&
      checkedCue.gain !== undefined &&
      !Number.isFinite(checkedCue.gain)
    ) {
      throw new Error(`Invalid gain for cue "${checkedCue.id}".`);
    }
  }

  return index as unknown as CueIndex;
}

export function validateManifest(
  input: unknown
): AnalysisManifest {
  if (
    typeof input !== 'object' ||
    input === null ||
    Array.isArray(input)
  ) {
    throw new Error(
      'Invalid manifest.json: expected a JSON object.'
    );
  }

  const manifest = input as Record<string, unknown>;

  const allowedKeys = ['status'];
  const unknownKeys = Object.keys(manifest).filter(
    (key) => !allowedKeys.includes(key)
  );

  if (unknownKeys.length > 0) {
    throw new Error(
      `Invalid manifest.json: unknown field(s): ${unknownKeys.join(', ')}.`
    );
  }

  const allowedStatuses = ['complete', 'partial', 'failed'];

  if (
    typeof manifest.status !== 'string' ||
    !allowedStatuses.includes(manifest.status)
  ) {
    throw new Error(
      'Invalid manifest.json: status must be complete, partial, or failed.'
    );
  }

  if (manifest.status !== 'complete') {
    throw new Error(
      `Playback refused: analysis status is "${manifest.status}".`
    );
  }

  return manifest as unknown as AnalysisManifest;
}

export function validatePlaybackInputs(
  indexInput: unknown,
  manifestInput: unknown
): CueIndex {
  // Reject incomplete or failed analysis before processing its cues.
  validateManifest(manifestInput);

  return validateCueIndex(indexInput);
}
