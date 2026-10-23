/**
 * Centralized, typed source of truth for canonical triggers and action contracts.
 *
 * Enforces:
 * - 10 Canonical trigger identifiers.
 * - 6 Canonical playback actions: hide, blur, mute, reduce, skip, warn.
 * - Explicit default action and permitted alternatives per trigger.
 * - Validation and fallback logic for user overrides and unknown inputs.
 */

export const CANONICAL_TRIGGERS = [
  'flashing',
  'profanity',
  'gunshot',
  'explosion',
  'scream',
  'loudness',
  'insect',
  'jump_scare',
  'visible_blood',
] as const;

export const LEGACY_TRIGGERS = [
  'loud_spike',
  'sustained_loudness',
] as const;

export type CanonicalTrigger = (typeof CANONICAL_TRIGGERS)[number];

export const CANONICAL_TRIGGER_SET = new Set<string>(CANONICAL_TRIGGERS);

export const CANONICAL_ACTIONS = [
  'hide',
  'blur',
  'mute',
  'reduce',
  'skip',
  'warn',
] as const;

export type CanonicalAction = (typeof CANONICAL_ACTIONS)[number];

export const CANONICAL_ACTION_SET = new Set<string>(CANONICAL_ACTIONS);

export interface TriggerActionContract {
  readonly defaultAction: CanonicalAction;
  readonly allowedAlternatives: readonly CanonicalAction[];
  readonly modality: 'visual' | 'audio' | 'text' | 'audio_visual';
  readonly displayName: string;
}

export const TRIGGER_ACTION_CONTRACTS: Readonly<Record<CanonicalTrigger | 'loud_spike' | 'sustained_loudness', TriggerActionContract>> = {
  loudness: {
    defaultAction: 'reduce',
    allowedAlternatives: ['mute', 'warn'],
    modality: 'audio',
    displayName: 'Loud Audio / Loudness',
  },
  flashing: {
    defaultAction: 'hide',
    allowedAlternatives: ['skip', 'warn'],
    modality: 'visual',
    displayName: 'Rapid Flashing / Strobe Lights',
  },
  profanity: {
    defaultAction: 'mute',
    allowedAlternatives: ['skip', 'warn'],
    modality: 'text',
    displayName: 'Profanity / Vulgarity',
  },
  gunshot: {
    defaultAction: 'mute',
    allowedAlternatives: ['reduce', 'skip', 'warn'],
    modality: 'audio',
    displayName: 'Gunshot / Gunfire',
  },
  explosion: {
    defaultAction: 'reduce',
    allowedAlternatives: ['mute', 'skip', 'warn'],
    modality: 'audio',
    displayName: 'Explosion / Blast',
  },
  scream: {
    defaultAction: 'reduce',
    allowedAlternatives: ['mute', 'skip', 'warn'],
    modality: 'audio',
    displayName: 'Scream / Screaming',
  },
  loud_spike: {
    defaultAction: 'reduce',
    allowedAlternatives: ['mute', 'warn'],
    modality: 'audio',
    displayName: 'Sudden Loudness Increase',
  },
  sustained_loudness: {
    defaultAction: 'reduce',
    allowedAlternatives: ['mute', 'warn', 'skip'],
    modality: 'audio',
    displayName: 'Sustained Loudness',
  },
  insect: {
    defaultAction: 'hide',
    allowedAlternatives: ['blur', 'skip', 'warn'],
    modality: 'visual',
    displayName: 'Insects / Swarms',
  },
  jump_scare: {
    defaultAction: 'warn',
    allowedAlternatives: ['hide', 'mute', 'skip'],
    modality: 'audio_visual',
    displayName: 'Jump Scare',
  },
  visible_blood: {
    defaultAction: 'hide',
    allowedAlternatives: ['blur', 'skip', 'warn'],
    modality: 'visual',
    displayName: 'Visible Blood / Gore',
  },
};

/**
 * Checks if a string is a recognized canonical trigger.
 */
export function isCanonicalTrigger(trigger: unknown): trigger is CanonicalTrigger {
  return typeof trigger === 'string' && CANONICAL_TRIGGER_SET.has(trigger);
}

/**
 * Checks if a string is a recognized canonical action.
 */
export function isCanonicalAction(action: unknown): action is CanonicalAction {
  return typeof action === 'string' && CANONICAL_ACTION_SET.has(action);
}

/**
 * Returns whether a given action is allowed for a specified trigger.
 */
export function isActionAllowedForTrigger(trigger: string, action: string): boolean {
  if (!isCanonicalTrigger(trigger) || !isCanonicalAction(action)) {
    return false;
  }
  const contract = TRIGGER_ACTION_CONTRACTS[trigger];
  return contract.defaultAction === action || contract.allowedAlternatives.includes(action);
}

/**
 * Resolves the effective action for a trigger given an optional user override map.
 * If the override is disallowed or invalid, falls back safely to the trigger's default action.
 * If the trigger is unrecognized, falls back to the provided fallbackAction or 'hide'.
 */
export function resolveEffectiveAction(
  trigger?: string | null,
  fallbackAction: string = 'hide',
  actionOverrides?: Partial<Record<string, string>> | null,
): CanonicalAction | 'dim' {
  if (trigger && isCanonicalTrigger(trigger)) {
    const contract = TRIGGER_ACTION_CONTRACTS[trigger];
    if (actionOverrides && trigger in actionOverrides) {
      const override = actionOverrides[trigger];
      if (override && isActionAllowedForTrigger(trigger, override)) {
        return override as CanonicalAction;
      }
      return contract.defaultAction;
    }
    if (fallbackAction === 'dim') {
      return 'dim';
    }
    if (isCanonicalAction(fallbackAction)) {
      return fallbackAction;
    }
    return contract.defaultAction;
  }

  if (fallbackAction === 'dim') {
    return 'dim';
  }
  return isCanonicalAction(fallbackAction) ? fallbackAction : 'hide';
}

/**
 * Checks whether an explicit, valid user override is set for a specific trigger and action.
 */
export function hasExplicitActionOverride(
  trigger?: string | null,
  action?: string | null,
  actionOverrides?: Partial<Record<string, string>> | null,
): boolean {
  if (!trigger || !action || !actionOverrides) {
    return false;
  }
  if (!isCanonicalTrigger(trigger) || !isCanonicalAction(action)) {
    return false;
  }
  if (!(trigger in actionOverrides)) {
    return false;
  }
  const override = actionOverrides[trigger];
  return override === action && isActionAllowedForTrigger(trigger, action);
}

/**
 * Sanitizes an action overrides dictionary, keeping only canonical triggers with permitted actions.
 */
export function sanitizeActionOverrides(
  raw: unknown,
): Partial<Record<CanonicalTrigger, CanonicalAction>> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {};
  }

  const result: Partial<Record<CanonicalTrigger, CanonicalAction>> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof key === 'string' && typeof value === 'string') {
      if (isCanonicalTrigger(key) && isActionAllowedForTrigger(key, value)) {
        result[key] = value as CanonicalAction;
      }
    }
  }

  return result;
}
