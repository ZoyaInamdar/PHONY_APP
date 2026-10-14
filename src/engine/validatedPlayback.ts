import { validatePlaybackInputs } from './validation';
import { resolveActions } from './resolveActions';
import { Cue, EffectiveActions, ViewerOptions } from './types';

export function resolveValidatedPlayback(
time: number,
indexInput: unknown,
manifestInput: unknown,
options: ViewerOptions
): EffectiveActions {
const index = validatePlaybackInputs(indexInput, manifestInput);

const cues: Cue[] = index.cues;

return resolveActions(time, cues, options);
}