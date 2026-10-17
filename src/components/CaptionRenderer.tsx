import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useActiveCaption } from '../hooks/useActiveCaption';

export interface CaptionRendererProps {
  currentTime: number;
  userSettings?: any;
}

export const CaptionRenderer: React.FC<CaptionRendererProps> = ({
  currentTime,
  userSettings,
}) => {
  const activeCue = useActiveCaption(currentTime, userSettings);

  if (!activeCue) return null;

  return (
    <View
      style={styles.container}
      pointerEvents="none"
      testID="caption-container"
    >
      <Text style={styles.text} testID="caption-text">
        {activeCue.processedText}
      </Text>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    bottom: 40,
    width: '100%',
    alignItems: 'center',
  },
  text: {
    backgroundColor: 'rgba(0,0,0,0.7)',
    color: '#FFF',
    fontSize: 24, // 10-foot UI polish
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 4,
    textAlign: 'center',
  },
});
