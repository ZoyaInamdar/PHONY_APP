/**
 * Dynamic Recap aggregation and display string generation.
 * Reflects what the Trigger-Aware Player actually applied during playback.
 */

export interface AppliedAction {
  triggerType: string;
  appliedEffect: string; // e.g. "muted", "blacked_out", "skipped", "reduced", "warned"
  requestedAction?: string; // e.g. "blur", "dim", "skip"
  cueId?: string;
  start?: number;
  end?: number;
}

export interface PlaybackSessionPayload {
  filmId?: string;
  duration?: number;
  appliedActions: AppliedAction[];
}

/**
 * Aggregates applied actions by grouping and counting them.
 * Example key: "profanity_muted" or "flash_blacked_out"
 */
export function aggregateAppliedActions(
  appliedActions: AppliedAction[],
): Record<string, number> {
  return appliedActions.reduce((acc, action) => {
    const key = `${action.triggerType}_${action.appliedEffect}`;
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);
}

/**
 * Normalizes trigger names into human-readable nouns/phrases.
 */
function formatTriggerNoun(triggerType: string, count: number): string {
  const norm = triggerType.toLowerCase().trim();
  const isPlural = count !== 1;

  if (norm === 'profanity') {
    return isPlural ? 'profanities' : 'profanity';
  }
  if (norm === 'flash' || norm === 'flashing') {
    return isPlural ? 'flash scenes' : 'flash scene';
  }
  if (norm === 'scene' || norm === 'skip') {
    return isPlural ? 'scenes' : 'scene';
  }
  if (norm === 'loud_spike' || norm === 'loud spike') {
    return isPlural ? 'loud spikes' : 'loud spike';
  }
  if (norm === 'jump_scare' || norm === 'jump scare') {
    return isPlural ? 'jump scares' : 'jump scare';
  }
  if (norm === 'fast_motion' || norm === 'fast motion') {
    return isPlural ? 'fast motion scenes' : 'fast motion scene';
  }
  if (norm === 'gunshot') {
    return isPlural ? 'gunshots' : 'gunshot';
  }
  if (norm === 'explosion') {
    return isPlural ? 'explosions' : 'explosion';
  }
  if (norm === 'scream') {
    return isPlural ? 'screams' : 'scream';
  }
  if (norm === 'spider') {
    return isPlural ? 'spider scenes' : 'spider scene';
  }

  // Fallback: replace underscores with spaces
  const clean = norm.replace(/_/g, ' ');
  return isPlural ? `${clean} scenes` : `${clean} scene`;
}

/**
 * Normalizes applied effects into readable participle verbs.
 *
 * Ensures that if a requested blur or dim fell back to blackout,
 * it faithfully outputs "blacked out". "blurred" is reserved exclusively
 * for cases where visual blur was confirmed as rendered on screen.
 */
function formatEffectVerb(appliedEffect: string): string {
  const norm = appliedEffect.toLowerCase().trim();

  switch (norm) {
    case 'blacked_out':
    case 'blackout':
    case 'hide':
      return 'blacked out';
    case 'muted':
    case 'mute':
      return 'muted';
    case 'skipped':
    case 'skip':
      return 'skipped';
    case 'reduced':
    case 'reduce':
      return 'volume reduced';
    case 'warned':
    case 'warn':
      return 'warned with heads-up';
    case 'blurred':
    case 'blur':
      return 'blurred';
    default:
      return norm.replace(/_/g, ' ');
  }
}

/**
 * Maps an aggregated count and key into a specific display string.
 * Example outputs:
 * - "12 profanities muted"
 * - "2 flash scenes blacked out"
 * - "1 scene skipped"
 */
export function formatActionDisplayString(key: string, count: number): string {
  const separatorIndex = key.lastIndexOf('_');
  let trigger = key;
  let effect = '';

  if (separatorIndex !== -1) {
    trigger = key.substring(0, separatorIndex);
    effect = key.substring(separatorIndex + 1);

    // Handle compound effect keys like "blacked_out"
    if (key.endsWith('_blacked_out')) {
      trigger = key.replace(/_blacked_out$/, '');
      effect = 'blacked_out';
    }
  }

  const noun = formatTriggerNoun(trigger, count);
  const verb = formatEffectVerb(effect);

  return `${count} ${noun} ${verb}`;
}

/**
 * Generates the full list of dynamic recap display strings from an applied actions record.
 */
export function generateRecapDisplayStrings(
  appliedActions: AppliedAction[],
): string[] {
  if (!appliedActions || appliedActions.length === 0) {
    return [];
  }

  const counts = aggregateAppliedActions(appliedActions);
  return Object.entries(counts).map(([key, count]) =>
    formatActionDisplayString(key, count),
  );
}
