/**
 * Default trigger policies for production playback cue adaptation.
 *
 * Sourced directly from the triggers section of config.yaml.
 * Defines modality, default action, allowed actions, and enabled flags
 * for the 9 canonical triggers.
 *
 * Preserves distinct action semantics ('dim', 'blur', 'mute', 'warn', etc.)
 * without silent aliasing.
 */

export interface TriggerPolicyDefinition {
  enabled: boolean;
  modality: 'visual' | 'audio' | 'text' | 'audio_visual';
  default_action: 'dim' | 'blur' | 'mute' | 'warn' | 'hide' | 'reduce' | 'skip';
  allowed_actions: string[];
}

export type TriggerPolicies = Record<string, TriggerPolicyDefinition>;

export const DEFAULT_TRIGGER_POLICIES: TriggerPolicies = {
  flashing: {
    enabled: true,
    modality: 'visual',
    default_action: 'dim',
    allowed_actions: ['dim', 'blur', 'skip', 'warn'],
  },
  loud_spike: {
    enabled: true,
    modality: 'audio',
    default_action: 'mute',
    allowed_actions: ['mute', 'skip', 'warn'],
  },
  gunshot: {
    enabled: true,
    modality: 'audio',
    default_action: 'mute',
    allowed_actions: ['mute', 'skip', 'warn'],
  },
  explosion: {
    enabled: true,
    modality: 'audio',
    default_action: 'mute',
    allowed_actions: ['mute', 'skip', 'warn'],
  },
  scream: {
    enabled: true,
    modality: 'audio',
    default_action: 'mute',
    allowed_actions: ['mute', 'skip', 'warn'],
  },
  profanity: {
    enabled: true,
    modality: 'text',
    default_action: 'mute',
    allowed_actions: ['mute', 'skip', 'warn'],
  },
  spider: {
    enabled: true,
    modality: 'visual',
    default_action: 'blur',
    allowed_actions: ['blur', 'skip', 'warn'],
  },
  fast_motion: {
    enabled: false,
    modality: 'visual',
    default_action: 'warn',
    allowed_actions: ['warn', 'dim', 'skip'],
  },
  jump_scare: {
    enabled: false,
    modality: 'audio_visual',
    default_action: 'warn',
    allowed_actions: ['warn', 'mute', 'skip'],
  },
};
