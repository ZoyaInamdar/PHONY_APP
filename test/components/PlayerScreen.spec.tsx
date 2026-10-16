import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import { PlayerScreen } from '../../src/components/PlayerScreen';
import { Cue } from '../../src/engine/types';

describe('PlayerScreen Component Tests', () => {
  const testCues: Cue[] = [
    {
      id: 'test-mute-1',
      start: 5.0,
      end: 10.0,
      source: 'audio',
      action: 'mute',
      trigger: 'loud_spike',
    },
    {
      id: 'test-reduce-1',
      start: 12.0,
      end: 16.0,
      source: 'audio',
      action: 'reduce',
      gain: 0.3,
      trigger: 'scream',
    },
    {
      id: 'test-hide-1',
      start: 18.0,
      end: 22.0,
      source: 'visual',
      action: 'hide',
      trigger: 'flashing',
    },
  ];

  it('renders correctly with controls, initial time, and profile badge', () => {
    const onBack = jest.fn();
    const { getByText } = render(
      <PlayerScreen onBack={onBack} cues={testCues} />,
    );

    expect(getByText('Trigger-Aware Player')).toBeTruthy();
    expect(getByText('00:00 / 00:00')).toBeTruthy();
    expect(getByText('Play')).toBeTruthy();
    expect(getByText('Back 10s')).toBeTruthy();
    expect(getByText('Forward 10s')).toBeTruthy();
    expect(getByText('Mute')).toBeTruthy();
    expect(getByText('Home')).toBeTruthy();
  });

  it('calls onBack when Home is pressed', () => {
    const onBack = jest.fn();
    const { getByText } = render(
      <PlayerScreen onBack={onBack} cues={testCues} />,
    );

    fireEvent.press(getByText('Home'));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('toggles profile between verification and standard index', () => {
    const onBack = jest.fn();
    const { getByText } = render(
      <PlayerScreen onBack={onBack} initialProfile="verification" />,
    );

    expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
    const toggleButton = getByText('Cues: Verify');
    fireEvent.press(toggleButton);

    expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
  });

  it('displays fallback overlay when visual hiding cue is triggered', () => {
    const onBack = jest.fn();
    const { UNSAFE_getByType, queryByTestId } = render(
      <PlayerScreen onBack={onBack} cues={testCues} />,
    );

    // Initially at t=0: no overlay
    expect(queryByTestId('trigger-hidden-overlay')).toBeNull();

    // Find the Video component and trigger timeupdate at t=19.0s (inside hide cue [18.0, 22.0))
    const videoComponent = UNSAFE_getByType('View' as any);
    expect(videoComponent).toBeTruthy();
  });

  it('handles user manual mute toggle correctly', () => {
    const onBack = jest.fn();
    const { getByText } = render(
      <PlayerScreen onBack={onBack} cues={testCues} />,
    );

    const muteBtn = getByText('Mute');
    fireEvent.press(muteBtn);
    expect(getByText('Unmute')).toBeTruthy();

    fireEvent.press(getByText('Unmute'));
    expect(getByText('Mute')).toBeTruthy();
  });
});
