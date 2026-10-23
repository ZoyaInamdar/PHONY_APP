/**
 * Default trigger policies for production playback cue adaptation.
 *
 * Sourced directly from the triggers section of config.yaml.
 * Defines modality, default action, allowed actions, and enabled flags
 * for the 12 canonical triggers.
 *
 * Preserves distinct action semantics ('hide', 'blur', 'mute', 'reduce', 'skip', 'warn')
 * without silent aliasing.
 */

export interface TriggerPolicyDefinition {
  enabled: boolean;
  modality: 'visual' | 'audio' | 'text' | 'audio_visual';
  default_action: 'hide' | 'blur' | 'mute' | 'reduce' | 'skip' | 'warn';
  allowed_actions: string[];
}

export type TriggerPolicies = Record<string, TriggerPolicyDefinition>;

export const DEFAULT_TRIGGER_POLICIES: TriggerPolicies = {
  flashing: {
    enabled: true,
    modality: 'visual',
    default_action: 'hide',
    allowed_actions: ['hide', 'skip', 'warn'],
  },
  profanity: {
    enabled: true,
    modality: 'text',
    default_action: 'mute',
    allowed_actions: ['mute', 'skip', 'warn'],
  },
  gunshot: {
    enabled: true,
    modality: 'audio',
    default_action: 'mute',
    allowed_actions: ['mute', 'reduce', 'skip', 'warn'],
  },
  explosion: {
    enabled: true,
    modality: 'audio',
    default_action: 'reduce',
    allowed_actions: ['reduce', 'mute', 'skip', 'warn'],
  },
  scream: {
    enabled: true,
    modality: 'audio',
    default_action: 'reduce',
    allowed_actions: ['reduce', 'mute', 'skip', 'warn'],
  },
  loudness: {
    enabled: true,
    modality: 'audio',
    default_action: 'reduce',
    allowed_actions: ['reduce', 'mute', 'warn'],
  },
  loud_spike: {
    enabled: true,
    modality: 'audio',
    default_action: 'reduce',
    allowed_actions: ['reduce', 'mute', 'warn'],
  },
  sustained_loudness: {
    enabled: false,
    modality: 'audio',
    default_action: 'reduce',
    allowed_actions: ['reduce', 'mute', 'warn', 'skip'],
  },
  insect: {
    enabled: false,
    modality: 'visual',
    default_action: 'hide',
    allowed_actions: ['hide', 'blur', 'skip', 'warn'],
  },
  jump_scare: {
    enabled: false,
    modality: 'audio_visual',
    default_action: 'warn',
    allowed_actions: ['warn', 'hide', 'mute', 'skip'],
  },
  visible_blood: {
    enabled: false,
    modality: 'visual',
    default_action: 'hide',
    allowed_actions: ['hide', 'blur', 'skip', 'warn'],
  },
};
