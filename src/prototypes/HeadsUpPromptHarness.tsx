import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { PlayerScreen } from '../components/PlayerScreen';
import { PROMPT_HARNESS_SCENARIOS } from '../data/promptHarnessScenarios';

export interface HeadsUpPromptHarnessProps {
  onBack?: () => void;
  initialScenario?: number;
  testVideoUrl?: string;
}

/**
 * Isolated prototype screen for deterministic heads-up prompt testing on Vega OS / Fire TV.
 *
 * STRICT SAFEGUARDS:
 * - This prototype is a developer tool to exercise the real heads-up prompt UI,
 *   engine resolver, review policy downgrades, and skip chaining mechanics.
 * - By default, production runs in standard player mode with the prompt harness disabled.
 * - Uses the 5 deterministic scenarios defined in PROMPT_HARNESS_SCENARIOS.
 */
export const HeadsUpPromptHarness: React.FC<HeadsUpPromptHarnessProps> = ({
  onBack = () => {},
  initialScenario = 0,
  testVideoUrl,
}) => {
  return (
    <View style={styles.container} testID="heads-up-prompt-harness-root">
      <View style={styles.disclaimerBanner} testID="harness-disclaimer">
        <Text style={styles.disclaimerBadge}>PROTOTYPE HARNESS</Text>
        <Text style={styles.disclaimerText}>
          Deterministic Heads-Up Prompt Runtime Test Harness — Vega OS Virtual Device
        </Text>
      </View>
      <View style={styles.playerContainer}>
        <PlayerScreen
          onBack={onBack}
          devHarnessMode={true}
          initialHarnessScenario={initialScenario}
          testVideoUrl={testVideoUrl}
        />
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0E17',
  },
  disclaimerBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#1E293B',
    paddingVertical: 6,
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    borderBottomColor: '#3B82F6',
  },
  disclaimerBadge: {
    backgroundColor: '#2563EB',
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: 'bold',
    paddingVertical: 2,
    paddingHorizontal: 6,
    borderRadius: 4,
    marginRight: 10,
    overflow: 'hidden',
  },
  disclaimerText: {
    color: '#94A3B8',
    fontSize: 12,
    fontWeight: '500',
  },
  playerContainer: {
    flex: 1,
  },
});
