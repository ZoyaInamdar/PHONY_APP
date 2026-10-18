import React, { useState } from 'react';
import {
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { TVFocusGuideView } from '@amazon-devices/react-native-kepler';
import {
  AppliedAction,
  PlaybackSessionPayload,
  generateRecapDisplayStrings,
} from '../engine/recap';

export interface RecapViewProps {
  appliedActions?: AppliedAction[];
  sessionPayload?: PlaybackSessionPayload;
  onClose?: () => void;
  onRestart?: () => void;
  testID?: string;
}

export const RecapView: React.FC<RecapViewProps> = ({
  appliedActions: directActions,
  sessionPayload,
  onClose,
  onRestart,
  testID = 'recap-view',
}) => {
  const [focusedButton, setFocusedButton] = useState<'restart' | 'done'>('done');

  const actions = directActions ?? sessionPayload?.appliedActions ?? [];
  const displayStrings = generateRecapDisplayStrings(actions);

  return (
    <View
      style={styles.overlay}
      testID={testID}
      accessibilityRole="summary"
      accessibilityLabel="Playback Recap Summary">
      <TVFocusGuideView style={styles.card} autoFocus>
        <View style={styles.header}>
          <Text style={styles.badge}>SESSION COMPLETED</Text>
          <Text style={styles.title}>Playback Protection Recap</Text>
          <Text style={styles.subtitle}>
            Summary of actions applied by the Trigger-Aware Player during playback
          </Text>
        </View>

        <ScrollView
          style={styles.listContainer}
          contentContainerStyle={styles.listContent}>
          {displayStrings.length > 0 ? (
            displayStrings.map((phrase, index) => (
              <View
                key={`${phrase}-${index}`}
                style={styles.recapItem}
                testID={`recap-item-${index}`}>
                <Text style={styles.itemBullet}>🛡️</Text>
                <Text style={styles.itemText}>{phrase}</Text>
              </View>
            ))
          ) : (
            <View style={styles.emptyContainer} testID="recap-empty">
              <Text style={styles.emptyIcon}>✨</Text>
              <Text style={styles.emptyTitle}>
                No sensitive triggers encountered
              </Text>
              <Text style={styles.emptySubtitle}>
                Playback completed smoothly without requiring intervention.
              </Text>
            </View>
          )}
        </ScrollView>

        <View style={styles.buttonRow}>
          {onRestart && (
            <TouchableOpacity
              style={[
                styles.button,
                styles.restartButton,
                focusedButton === 'restart' && styles.restartButtonFocused,
              ]}
              testID="recap-watch-again"
              onPress={onRestart}
              onFocus={() => setFocusedButton('restart')}
              onBlur={() => {
                if (focusedButton === 'restart') {
                  setFocusedButton('done');
                }
              }}
              isTVSelectable={true}
              accessibilityRole="button"
              accessibilityLabel="Watch Again"
              activeOpacity={0.8}>
              <Text style={styles.buttonText}>Watch Again</Text>
            </TouchableOpacity>
          )}

          <TouchableOpacity
            style={[
              styles.button,
              styles.doneButton,
              focusedButton === 'done' && styles.doneButtonFocused,
            ]}
            testID="recap-done"
            onPress={onClose}
            onFocus={() => setFocusedButton('done')}
            isTVSelectable={true}
            hasTVPreferredFocus={true}
            accessibilityRole="button"
            accessibilityLabel="Done, close recap"
            activeOpacity={0.8}>
            <Text
              style={[
                styles.buttonText,
                focusedButton === 'done' && styles.doneButtonFocusedText,
              ]}>
              Done
            </Text>
          </TouchableOpacity>
        </View>
      </TVFocusGuideView>
    </View>
  );
};

const styles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(10, 15, 26, 0.92)',
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 100,
    padding: 32,
  },
  card: {
    width: '80%',
    maxWidth: 820,
    maxHeight: '85%',
    backgroundColor: '#0F172A',
    borderRadius: 16,
    borderWidth: 2,
    borderColor: '#334155',
    padding: 36,
    justifyContent: 'space-between',
  },
  header: {
    marginBottom: 20,
  },
  badge: {
    color: '#38BDF8',
    fontSize: 13,
    fontWeight: '700',
    letterSpacing: 1.5,
    marginBottom: 6,
  },
  title: {
    color: '#FFFFFF',
    fontSize: 32,
    fontWeight: 'bold',
  },
  subtitle: {
    color: '#94A3B8',
    fontSize: 16,
    marginTop: 6,
  },
  listContainer: {
    flexGrow: 0,
    maxHeight: 280,
    marginVertical: 12,
  },
  listContent: {
    paddingVertical: 4,
  },
  recapItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#1E293B',
    borderRadius: 10,
    paddingHorizontal: 20,
    paddingVertical: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: '#334155',
  },
  itemBullet: {
    fontSize: 20,
    marginRight: 14,
  },
  itemText: {
    color: '#F8FAFC',
    fontSize: 20,
    fontWeight: '600',
  },
  emptyContainer: {
    alignItems: 'center',
    paddingVertical: 24,
  },
  emptyIcon: {
    fontSize: 36,
    marginBottom: 8,
  },
  emptyTitle: {
    color: '#F8FAFC',
    fontSize: 20,
    fontWeight: '600',
  },
  emptySubtitle: {
    color: '#94A3B8',
    fontSize: 15,
    marginTop: 4,
  },
  buttonRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 16,
    marginTop: 20,
  },
  button: {
    paddingHorizontal: 28,
    paddingVertical: 14,
    borderRadius: 8,
    borderWidth: 3,
    borderColor: 'transparent',
    alignItems: 'center',
  },
  restartButton: {
    backgroundColor: '#334155',
  },
  doneButton: {
    backgroundColor: '#2563EB',
  },
  buttonFocused: {
    borderColor: '#FFFFFF',
    transform: [{ scale: 1.05 }],
  },
  doneButtonFocused: {
    backgroundColor: '#E5A900', // High-contrast TV yellow
    borderColor: '#FFFFFF',
    borderWidth: 3,
    transform: [{ scale: 1.08 }], // Pop-out effect
  },
  restartButtonFocused: {
    backgroundColor: '#1E3A8A',
    borderColor: '#FFFFFF',
    borderWidth: 3,
    transform: [{ scale: 1.05 }],
  },
  buttonText: {
    color: '#FFFFFF',
    fontSize: 17,
    fontWeight: 'bold',
  },
  doneButtonFocusedText: {
    color: '#000000',
    fontWeight: '800',
  },
});
