import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import { Video } from '@amazon-devices/react-native-w3cmedia';
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

    const video = UNSAFE_getByType('View' as any);
    expect(video).toBeTruthy();
  });

  it('reports active dim and blur actions explicitly as unsupported on renderer without mapping to hide or reduce', () => {
    const unsupportedCues: Cue[] = [
      {
        id: 'cue-dim',
        start: 10.0,
        end: 15.0,
        source: 'visual',
        action: 'dim',
        trigger: 'flashing',
      },
      {
        id: 'cue-blur',
        start: 20.0,
        end: 25.0,
        source: 'visual',
        action: 'blur',
        trigger: 'spider',
      },
    ];

    const onBack = jest.fn();
    const { getByText, queryByTestId, UNSAFE_getByType } = render(
      <PlayerScreen onBack={onBack} cues={unsupportedCues} />,
    );

    const videoComp = UNSAFE_getByType(Video);
    expect(videoComp.instance).toBeTruthy();

    // 1. Enter dim cue at t=12.0s
    act(() => {
      videoComp.instance.currentTime = 12.0;
      videoComp.props.onTimeUpdate();
    });

    // Explicitly reported as unsupported dim on the current renderer
    expect(getByText(/⚠️ DIM \(unsupported on renderer\) \[flashing\]/)).toBeTruthy();
    // Must NOT be mapped to hide (no overlay rendered)
    expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
    // Must NOT be muted
    expect(videoComp.instance.muted).toBe(false);

    // 2. Enter blur cue at t=22.0s
    act(() => {
      videoComp.instance.currentTime = 22.0;
      videoComp.props.onTimeUpdate();
    });

    // Explicitly reported as unsupported blur on current renderer
    expect(getByText(/⚠️ BLUR \(unsupported on renderer\) \[spider\]/)).toBeTruthy();
    // Must NOT be mapped to hide
    expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
    // Must NOT be muted or reduced
    expect(videoComp.instance.muted).toBe(false);
    expect(videoComp.instance.volume).toBe(1.0);
  });

  it('renders heads-up warning banner on cue start, expires it on cue end, and updates on seek', () => {
    const warnCues: Cue[] = [
      {
        id: 'cue-warn-motion',
        start: 30.0,
        end: 35.0,
        source: 'visual',
        action: 'warn',
        trigger: 'fast_motion',
      },
    ];

    const onBack = jest.fn();
    const { getByText, queryByTestId, UNSAFE_getByType } = render(
      <PlayerScreen onBack={onBack} cues={warnCues} />,
    );

    const videoComp = UNSAFE_getByType(Video);
    expect(videoComp.instance).toBeTruthy();

    // Before cue at t=29.0s: no warning banner
    act(() => {
      videoComp.instance.currentTime = 29.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('warning-banner')).toBeNull();

    // Cue start at t=30.0s: warning banner appears
    act(() => {
      videoComp.instance.currentTime = 30.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('warning-banner')).toBeTruthy();
    expect(getByText('⚠️ Heads-Up Warning: fast_motion')).toBeTruthy();

    // Cue end at t=35.0s: warning banner expires
    act(() => {
      videoComp.instance.currentTime = 35.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('warning-banner')).toBeNull();

    // Seeking directly into warning cue: warning banner appears immediately
    const backBtn = getByText('Back 10s');
    act(() => {
      // Current is 35.0s; seek back 10s -> 25.0s (outside)
      fireEvent.press(backBtn);
    });
    expect(queryByTestId('warning-banner')).toBeNull();

    act(() => {
      // Set to 22.0s and seek forward 10s -> 32.0s (inside warning cue)
      videoComp.instance.currentTime = 22.0;
      fireEvent.press(getByText('Forward 10s'));
    });
    expect(queryByTestId('warning-banner')).toBeTruthy();
    expect(getByText('⚠️ Heads-Up Warning: fast_motion')).toBeTruthy();

    // Seek forward 10s -> 42.0s (out of warning cue): clears banner
    act(() => {
      fireEvent.press(getByText('Forward 10s'));
    });
    expect(queryByTestId('warning-banner')).toBeNull();
  });

  it('preserves mute action when warning cue co-occurs (warning does not override mute)', () => {
    const mixedCues: Cue[] = [
      {
        id: 'cue-mute',
        start: 40.0,
        end: 46.0,
        source: 'audio',
        action: 'mute',
        trigger: 'loud_spike',
      },
      {
        id: 'cue-warn',
        start: 42.0,
        end: 45.0,
        source: 'visual',
        action: 'warn',
        trigger: 'jump_scare',
      },
    ];

    const onBack = jest.fn();
    const { getByText, queryByTestId, UNSAFE_getByType } = render(
      <PlayerScreen onBack={onBack} cues={mixedCues} />,
    );

    const videoComp = UNSAFE_getByType(Video);
    expect(videoComp.instance).toBeTruthy();

    // Inside overlapping window at t=43.0s
    act(() => {
      videoComp.instance.currentTime = 43.0;
      videoComp.props.onTimeUpdate();
    });

    // Warning banner is displayed
    expect(queryByTestId('warning-banner')).toBeTruthy();
    expect(getByText('⚠️ Heads-Up Warning: jump_scare')).toBeTruthy();

    // Audio MUST be muted (warning does not override mute!)
    expect(videoComp.instance.muted).toBe(true);
    expect(getByText(/🔇 Audio Muted/)).toBeTruthy();
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
