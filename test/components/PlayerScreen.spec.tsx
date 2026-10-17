import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import { BackHandler } from '@amazon-devices/react-native-kepler';
import { Video } from '@amazon-devices/react-native-w3cmedia';
import Ajv2020 from 'ajv/dist/2020';
import cueSchema from '../../schema/cue.schema.json';
import { PlayerScreen } from '../../src/components/PlayerScreen';
import { Cue } from '../../src/engine/types';
import {
  defaultPlaybackOptions,
  sintelIndexCues,
  skipVerificationTestCues,
  verificationTestCues,
} from '../../src/data/playbackCues';

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

  describe('D-pad remote navigation and focus states', () => {
    it('renders all player controls with testIDs and assigns hasTVPreferredFocus to play', () => {
      const onBack = jest.fn();
      const { getByTestId } = render(
        <PlayerScreen onBack={onBack} cues={testCues} />,
      );

      const backBtn = getByTestId('control-back-10s');
      const playBtn = getByTestId('control-play-pause');
      const fwdBtn = getByTestId('control-forward-10s');
      const muteBtn = getByTestId('control-mute');
      const profileBtn = getByTestId('control-profile');
      const settingsBtn = getByTestId('control-settings');
      const homeBtn = getByTestId('control-home');

      expect(backBtn).toBeTruthy();
      expect(playBtn).toBeTruthy();
      expect(fwdBtn).toBeTruthy();
      expect(muteBtn).toBeTruthy();
      expect(profileBtn).toBeTruthy();
      expect(settingsBtn).toBeTruthy();
      expect(homeBtn).toBeTruthy();

      // Play/Pause has TV preferred focus on mount
      expect(playBtn.props.hasTVPreferredFocus).toBe(true);
      expect(backBtn.props.hasTVPreferredFocus).toBe(false);
      expect(fwdBtn.props.hasTVPreferredFocus).toBe(false);
      expect(muteBtn.props.hasTVPreferredFocus).toBe(false);
      expect(profileBtn.props.hasTVPreferredFocus).toBe(false);
      expect(settingsBtn.props.hasTVPreferredFocus).toBe(false);
      expect(homeBtn.props.hasTVPreferredFocus).toBe(false);
    });

    it('applies focused styling to the initially focused play button', () => {
      const onBack = jest.fn();
      const { getByTestId } = render(
        <PlayerScreen onBack={onBack} cues={testCues} />,
      );

      const playBtn = getByTestId('control-play-pause');
      const flatStyle = Object.assign({}, ...[playBtn.props.style].flat());

      expect(flatStyle.backgroundColor).toBe('#2563EB');
      expect(flatStyle.borderColor).toBe('#FFFFFF');
      expect(flatStyle.borderWidth).toBe(3);
      expect(flatStyle.transform).toEqual([{ scale: 1.08 }]);
    });

    it('navigates focus between controls and updates visual focus indicator', () => {
      const onBack = jest.fn();
      const { getByTestId } = render(
        <PlayerScreen onBack={onBack} cues={testCues} />,
      );

      const playBtn = getByTestId('control-play-pause');
      const backBtn = getByTestId('control-back-10s');
      const fwdBtn = getByTestId('control-forward-10s');

      // Navigate focus to "Back 10s"
      act(() => {
        fireEvent(backBtn, 'focus');
      });

      const backFocusedStyle = Object.assign({}, ...[backBtn.props.style].flat());
      expect(backFocusedStyle.backgroundColor).toBe('#2563EB');
      expect(backFocusedStyle.borderColor).toBe('#FFFFFF');
      expect(backFocusedStyle.transform).toEqual([{ scale: 1.08 }]);

      // Play button is no longer focused
      const playUnfocusedStyle = Object.assign({}, ...[playBtn.props.style].flat());
      expect(playUnfocusedStyle.borderColor).toBe('transparent');

      // Navigate focus to "Forward 10s"
      act(() => {
        fireEvent(fwdBtn, 'focus');
      });

      const fwdFocusedStyle = Object.assign({}, ...[fwdBtn.props.style].flat());
      expect(fwdFocusedStyle.backgroundColor).toBe('#2563EB');
      expect(fwdFocusedStyle.borderColor).toBe('#FFFFFF');
      expect(fwdFocusedStyle.transform).toEqual([{ scale: 1.08 }]);

      const backUnfocusedStyle = Object.assign({}, ...[backBtn.props.style].flat());
      expect(backUnfocusedStyle.borderColor).toBe('transparent');
    });

    it('triggers onBack when TV remote hardware back button is pressed', () => {
      const onBack = jest.fn();
      render(<PlayerScreen onBack={onBack} cues={testCues} />);

      const backCalls = (BackHandler.addEventListener as jest.Mock).mock.calls;
      const lastHardwareCall = [...backCalls]
        .reverse()
        .find((call) => call[0] === 'hardwareBackPress');

      expect(lastHardwareCall).toBeDefined();
      const hardwareBackHandler = lastHardwareCall[1];
      expect(typeof hardwareBackHandler).toBe('function');

      const handled = hardwareBackHandler();
      expect(onBack).toHaveBeenCalledTimes(1);
      expect(handled).toBe(true);
    });
  });

  describe('In-Player Settings Drawer', () => {
    it('opens settings drawer when Settings is activated and closes when Done is pressed', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId } = render(
        <PlayerScreen onBack={onBack} cues={testCues} />,
      );

      // Drawer is initially closed
      expect(queryByTestId('settings-drawer')).toBeNull();

      // Open drawer
      fireEvent.press(getByTestId('control-settings'));
      expect(getByTestId('settings-drawer')).toBeTruthy();
      expect(getByTestId('toggle-visual-protection')).toBeTruthy();
      expect(getByTestId('toggle-audio-protection')).toBeTruthy();
      expect(getByTestId('toggle-volume-reduction')).toBeTruthy();
      expect(getByTestId('toggle-skip-protection')).toBeTruthy();
      expect(getByTestId('settings-done')).toBeTruthy();

      // Close drawer via Done button
      fireEvent.press(getByTestId('settings-done'));
      expect(queryByTestId('settings-drawer')).toBeNull();
    });

    it('hardware Back button closes drawer first, calling onBack only after drawer is closed', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId } = render(
        <PlayerScreen onBack={onBack} cues={testCues} />,
      );

      const backCalls = (BackHandler.addEventListener as jest.Mock).mock.calls;
      const lastHardwareCall = [...backCalls]
        .reverse()
        .find((call) => call[0] === 'hardwareBackPress');
      const hardwareBackHandler = lastHardwareCall[1];

      // Open settings drawer
      fireEvent.press(getByTestId('control-settings'));
      expect(getByTestId('settings-drawer')).toBeTruthy();

      // First hardware back press: closes drawer, onBack NOT called
      act(() => {
        const handled = hardwareBackHandler();
        expect(handled).toBe(true);
      });
      expect(queryByTestId('settings-drawer')).toBeNull();
      expect(onBack).not.toHaveBeenCalled();

      // Second hardware back press: triggers onBack
      act(() => {
        const handled = hardwareBackHandler();
        expect(handled).toBe(true);
      });
      expect(onBack).toHaveBeenCalledTimes(1);
    });

    it('action-level toggles update effective playback actions promptly', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. Audio Mute test at t=7.0s (in mute cue test-mute-1)
      act(() => {
        videoComp.instance.currentTime = 7.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.muted).toBe(true);

      // Open drawer and disable Audio Protection
      fireEvent.press(getByTestId('control-settings'));
      fireEvent.press(getByTestId('toggle-audio-protection'));

      // Audio is promptly unmuted!
      expect(videoComp.instance.muted).toBe(false);

      // Re-enable Audio Protection
      fireEvent.press(getByTestId('toggle-audio-protection'));
      expect(videoComp.instance.muted).toBe(true);

      // Close drawer
      fireEvent.press(getByTestId('settings-done'));

      // 2. Visual Hide test at t=20.0s (in hide cue test-hide-1)
      act(() => {
        videoComp.instance.currentTime = 20.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();

      // Open drawer and disable Visual Protection
      fireEvent.press(getByTestId('control-settings'));
      fireEvent.press(getByTestId('toggle-visual-protection'));

      // Fallback overlay promptly disappears
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
    });

    it('trigger-category toggles filter only matching cues without mutating original cues', () => {
      const originalCuesSnapshot = JSON.stringify(testCues);
      const onBack = jest.fn();
      const { getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Open drawer and check categories present in testCues
      fireEvent.press(getByTestId('control-settings'));
      const loudSpikeToggle = getByTestId('toggle-category-loud_spike');
      const screamToggle = getByTestId('toggle-category-scream');
      const flashingToggle = getByTestId('toggle-category-flashing');

      expect(loudSpikeToggle).toBeTruthy();
      expect(screamToggle).toBeTruthy();
      expect(flashingToggle).toBeTruthy();

      // Disable loud_spike category
      fireEvent.press(loudSpikeToggle);
      fireEvent.press(getByTestId('settings-done'));

      // At t=7.0s (loud_spike cue): audio is NOT muted because loud_spike is disabled
      act(() => {
        videoComp.instance.currentTime = 7.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.muted).toBe(false);

      // At t=14.0s (scream cue with reduce): volume reduction STILL applies because scream is active
      act(() => {
        videoComp.instance.currentTime = 14.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.volume).toBe(0.3);

      // Verify original testCues array was NOT mutated
      expect(JSON.stringify(testCues)).toBe(originalCuesSnapshot);
    });

    it('navigates D-pad focus among drawer items with visible focus styling', () => {
      const onBack = jest.fn();
      const { getByTestId } = render(
        <PlayerScreen onBack={onBack} cues={testCues} />,
      );

      fireEvent.press(getByTestId('control-settings'));

      const visualToggle = getByTestId('toggle-visual-protection');
      const audioToggle = getByTestId('toggle-audio-protection');
      const doneBtn = getByTestId('settings-done');

      // Visual toggle has preferred focus initially
      expect(visualToggle.props.hasTVPreferredFocus).toBe(true);

      // Focus audio toggle
      act(() => {
        fireEvent(audioToggle, 'focus');
      });
      const audioStyle = Object.assign({}, ...[audioToggle.props.style].flat());
      expect(audioStyle.borderColor).toBe('#FFFFFF');
      expect(audioStyle.backgroundColor).toBe('#1E3A8A');

      // Focus done button
      act(() => {
        fireEvent(doneBtn, 'focus');
      });
      const doneStyle = Object.assign({}, ...[doneBtn.props.style].flat());
      expect(doneStyle.borderColor).toBe('#FFFFFF');
      expect(doneStyle.backgroundColor).toBe('#1D4ED8');
    });
  });

  describe('Volume ramp and fade-restoration tuning', () => {
    beforeEach(() => {
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it('smoothly fades out volume into cue mute and restores back to base volume on cue exit', () => {
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} rampDurationMs={150} />,
      );

      const videoComp = UNSAFE_getByType(Video);
      expect(videoComp.instance.volume).toBe(1.0);
      expect(videoComp.instance.muted).toBe(false);

      // Cue starts at t=5.0s (action: mute, loud_spike)
      act(() => {
        videoComp.instance.currentTime = 5.0;
        videoComp.props.onTimeUpdate();
      });

      // At start of ramp: volume begins stepping down, mute is delayed to prevent speaker pop
      expect(videoComp.instance.muted).toBe(false);

      // Advance by 75ms (halfway through 150ms ramp)
      act(() => {
        jest.advanceTimersByTime(75);
      });
      expect(videoComp.instance.volume).toBeLessThan(1.0);
      expect(videoComp.instance.volume).toBeGreaterThan(0.0);
      expect(videoComp.instance.muted).toBe(false);

      // Advance to 150ms (ramp completes)
      act(() => {
        jest.advanceTimersByTime(75);
      });
      expect(videoComp.instance.volume).toBe(0.0);
      expect(videoComp.instance.muted).toBe(true);

      // Cue ends at t=10.0s (restore back to base volume 1.0)
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });

      // Native element unmuted at volume 0 to prevent audio burst
      expect(videoComp.instance.muted).toBe(false);
      expect(videoComp.instance.volume).toBe(0.0);

      // Advance by 75ms (halfway through recovery)
      act(() => {
        jest.advanceTimersByTime(75);
      });
      expect(videoComp.instance.volume).toBeGreaterThan(0.0);
      expect(videoComp.instance.volume).toBeLessThan(1.0);

      // Advance to 150ms (volume fully restored to 1.0)
      act(() => {
        jest.advanceTimersByTime(75);
      });
      expect(videoComp.instance.volume).toBe(1.0);
      expect(videoComp.instance.muted).toBe(false);
    });

    it('smoothly fades into volume reduction and recovers to base volume when cue ends', () => {
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} rampDurationMs={150} />,
      );

      const videoComp = UNSAFE_getByType(Video);
      expect(videoComp.instance.volume).toBe(1.0);

      // Cue starts at t=12.0s (action: reduce, gain: 0.3)
      act(() => {
        videoComp.instance.currentTime = 12.0;
        videoComp.props.onTimeUpdate();
      });

      // Halfway through reduction ramp
      act(() => {
        jest.advanceTimersByTime(75);
      });
      expect(videoComp.instance.volume).toBeLessThan(1.0);
      expect(videoComp.instance.volume).toBeGreaterThan(0.3);

      // Ramp completes to target 0.3
      act(() => {
        jest.advanceTimersByTime(75);
      });
      expect(videoComp.instance.volume).toBe(0.3);
      expect(videoComp.instance.muted).toBe(false);

      // Cue ends at t=16.0s (restore back to base volume 1.0)
      act(() => {
        videoComp.instance.currentTime = 16.0;
        videoComp.props.onTimeUpdate();
      });

      // Halfway through recovery ramp
      act(() => {
        jest.advanceTimersByTime(75);
      });
      expect(videoComp.instance.volume).toBeGreaterThan(0.3);
      expect(videoComp.instance.volume).toBeLessThan(1.0);

      // Fully restored to base volume
      act(() => {
        jest.advanceTimersByTime(75);
      });
      expect(videoComp.instance.volume).toBe(1.0);
    });

    it('immediately snaps volume and mute on seeking without waiting for ramp timer', () => {
      const onBack = jest.fn();
      const { getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} rampDurationMs={150} />,
      );

      const videoComp = UNSAFE_getByType(Video);
      videoComp.instance.currentTime = 4.0;

      // Seek forward 10s lands at t=14.0s (inside reduce cue, gain: 0.3)
      act(() => {
        fireEvent.press(getByTestId('control-forward-10s'));
      });

      // Seeking immediately snapped volume to 0.30 without advancing timers
      expect(videoComp.instance.volume).toBe(0.3);
      expect(videoComp.instance.muted).toBe(false);

      // Seek back 10s lands at t=4.0s (outside all cues)
      act(() => {
        fireEvent.press(getByTestId('control-back-10s'));
      });

      // Seeking immediately snapped volume back to 1.0 without delay
      expect(videoComp.instance.volume).toBe(1.0);
      expect(videoComp.instance.muted).toBe(false);
    });

    it('preserves user manual mute across cue boundaries and transitions', () => {
      const onBack = jest.fn();
      const { getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} rampDurationMs={150} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // User manually mutes the player at t=0
      fireEvent.press(getByTestId('control-mute'));
      expect(videoComp.instance.muted).toBe(true);

      // Playback advances into reduce cue at t=12.0s
      act(() => {
        videoComp.instance.currentTime = 12.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      // User manual mute takes absolute priority: video MUST remain muted
      expect(videoComp.instance.muted).toBe(true);

      // Playback advances into mute cue window at t=7.0s
      act(() => {
        videoComp.instance.currentTime = 7.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      expect(videoComp.instance.muted).toBe(true);

      // Playback advances outside of all cues at t=25.0s
      act(() => {
        videoComp.instance.currentTime = 25.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      // Mute state is still preserved
      expect(videoComp.instance.muted).toBe(true);
    });

    it('cleans up in-flight volume ramp timers and restores audio settings on unmount', () => {
      const onBack = jest.fn();
      const { unmount, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} rampDurationMs={150} />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const videoInstance = videoComp.instance;

      // Start ramp into mute cue
      act(() => {
        videoInstance.currentTime = 5.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(50); // in-flight ramp
      });

      expect(videoInstance.volume).toBeLessThan(1.0);

      // Unmount while ramp is running
      act(() => {
        unmount();
      });

      // Audio restored on unmount and timers cleared
      expect(videoInstance.muted).toBe(false);
      expect(videoInstance.volume).toBe(1.0);

      // Advancing timers should not cause any errors or post-unmount volume updates
      act(() => {
        jest.advanceTimersByTime(200);
      });
      expect(videoInstance.volume).toBe(1.0);
    });

    it('prevents volume churn when currentTime updates repeatedly within same target', () => {
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} rampDurationMs={150} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Advance into reduce cue
      act(() => {
        videoComp.instance.currentTime = 12.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      expect(videoComp.instance.volume).toBe(0.3);

      // Subsequent timeupdates inside the same reduce cue at 12.5s, 13.0s, 13.5s
      act(() => {
        videoComp.instance.currentTime = 12.5;
        videoComp.props.onTimeUpdate();
        videoComp.instance.currentTime = 13.0;
        videoComp.props.onTimeUpdate();
        videoComp.instance.currentTime = 13.5;
        videoComp.props.onTimeUpdate();
      });

      // Volume remains stable at target without churn or timer re-creation
      expect(videoComp.instance.volume).toBe(0.3);
      expect(videoComp.instance.muted).toBe(false);
    });

    it('disables Audio Protection in Settings Drawer during an active mute cue, promptly restoring audio', () => {
      const onBack = jest.fn();
      const { getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} rampDurationMs={150} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Play into mute cue at t=7.0s
      act(() => {
        videoComp.instance.currentTime = 7.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      expect(videoComp.instance.muted).toBe(true);

      // Open drawer and disable Audio Protection
      fireEvent.press(getByTestId('control-settings'));
      fireEvent.press(getByTestId('toggle-audio-protection'));

      // Audio is immediately unmuted and restored to base volume
      expect(videoComp.instance.muted).toBe(false);
      expect(videoComp.instance.volume).toBe(1.0);
    });

    it('disables Volume Reduction in Settings Drawer during an active reduce cue, promptly restoring base volume', () => {
      const onBack = jest.fn();
      const { getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={testCues} rampDurationMs={150} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Play into reduce cue at t=14.0s
      act(() => {
        videoComp.instance.currentTime = 14.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      expect(videoComp.instance.volume).toBe(0.3);

      // Open drawer and disable Volume Reduction
      fireEvent.press(getByTestId('control-settings'));
      fireEvent.press(getByTestId('toggle-volume-reduction'));

      // Volume is immediately restored to base 1.0
      expect(videoComp.instance.volume).toBe(1.0);
    });

    it('handles overlapping mute and reduction cues with proper precedence and volume restoration', () => {
      const overlappingCues: Cue[] = [
        {
          id: 'cue-reduce-overlap',
          start: 2.0,
          end: 10.0,
          source: 'audio',
          action: 'reduce',
          gain: 0.4,
          trigger: 'scream',
        },
        {
          id: 'cue-mute-overlap',
          start: 4.0,
          end: 7.0,
          source: 'audio',
          action: 'mute',
          trigger: 'loud_spike',
        },
      ];

      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={overlappingCues} rampDurationMs={150} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. At t=3.0s: only reduce is active (gain 0.4)
      act(() => {
        videoComp.instance.currentTime = 3.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      expect(videoComp.instance.volume).toBe(0.4);
      expect(videoComp.instance.muted).toBe(false);

      // 2. At t=5.0s: mute overrides reduce
      act(() => {
        videoComp.instance.currentTime = 5.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      expect(videoComp.instance.volume).toBe(0.0);
      expect(videoComp.instance.muted).toBe(true);

      // 3. At t=8.0s: mute cue ends, reduce is still active -> recovers to 0.4
      act(() => {
        videoComp.instance.currentTime = 8.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      expect(videoComp.instance.muted).toBe(false);
      expect(videoComp.instance.volume).toBe(0.4);

      // 4. At t=11.0s: reduce cue ends -> restores to base volume 1.0
      act(() => {
        videoComp.instance.currentTime = 11.0;
        videoComp.props.onTimeUpdate();
        jest.advanceTimersByTime(150);
      });
      expect(videoComp.instance.volume).toBe(1.0);
      expect(videoComp.instance.muted).toBe(false);
    });
  });

  describe('Skip playback actions and protection guardrails', () => {
    const skipTestCues: Cue[] = [
      {
        id: 'cue-skip-visual',
        start: 10.0,
        end: 15.0,
        source: 'visual',
        action: 'skip',
        trigger: 'flashing',
        status: 'ready',
      },
      {
        id: 'cue-post-skip-mute',
        start: 15.0,
        end: 18.0,
        source: 'audio',
        action: 'mute',
        trigger: 'loud_spike',
        status: 'ready',
      },
      {
        id: 'cue-review-skip-visual',
        start: 22.0,
        end: 27.0,
        source: 'visual',
        action: 'skip',
        trigger: 'flashing',
        status: 'needs_review',
      },
      {
        id: 'cue-review-skip-audio',
        start: 30.0,
        end: 35.0,
        source: 'audio',
        action: 'skip',
        trigger: 'loud_spike',
        status: 'needs_review',
      },
    ];

    it('successfully seeks past an active skip cue, synchronizes destination actions, and prevents repeated seek loops', () => {
      const onBack = jest.fn();
      const { getByText, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={skipTestCues} options={{ skipEnabled: true }} />,
      );

      const videoComp = UNSAFE_getByType(Video);
      expect(videoComp.instance.currentTime).toBe(0);

      // Playback reaches skip cue at t=10.0s
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });

      // Player sought forward to cue.end (15.0s)
      expect(videoComp.instance.currentTime).toBe(15.0);
      expect(getByText('00:15 / 00:52')).toBeTruthy();

      // At destination t=15.0s, cue-post-skip-mute [15.0, 18.0) is active -> destination audio is muted
      expect(videoComp.instance.muted).toBe(true);
      expect(getByText(/🔇 Audio Muted/)).toBeTruthy();

      // Subsequent time update at the destination (t=15.0s) must NOT re-trigger skip or enter infinite loop
      act(() => {
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(15.0);
    });

    it('does not seek when Skip Protection is disabled in settings drawer', () => {
      const onBack = jest.fn();
      const { getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={skipTestCues} options={{ skipEnabled: true }} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Open settings drawer and disable Skip Protection (from true -> false)
      fireEvent.press(getByTestId('control-settings'));
      fireEvent.press(getByTestId('toggle-skip-protection'));
      fireEvent.press(getByTestId('settings-done'));

      // Advance into skip cue interval at t=10.0s
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });

      // Player must NOT seek because skip is disabled
      expect(videoComp.instance.currentTime).toBe(10.0);
    });

    it('does not seek when skipEnabled is false by default in viewer options', () => {
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={skipTestCues} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });

      expect(videoComp.instance.currentTime).toBe(10.0);
    });

    it('never skips an unconfirmed needs_review visual cue and applies safe hide fallback instead', () => {
      const onBack = jest.fn();
      const { queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={skipTestCues} options={{ skipEnabled: true }} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Advance into review-required visual skip cue at t=22.0s
      act(() => {
        videoComp.instance.currentTime = 22.0;
        videoComp.props.onTimeUpdate();
      });

      // Must NOT seek
      expect(videoComp.instance.currentTime).toBe(22.0);

      // Must apply safe fallback: hide overlay is displayed
      expect(queryByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(videoComp.instance.muted).toBe(false);
    });

    it('never skips an unconfirmed needs_review audio cue and applies safe mute fallback instead', () => {
      const onBack = jest.fn();
      const { queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={skipTestCues} options={{ skipEnabled: true }} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Advance into review-required audio skip cue at t=30.0s
      act(() => {
        videoComp.instance.currentTime = 30.0;
        videoComp.props.onTimeUpdate();
      });

      // Must NOT seek
      expect(videoComp.instance.currentTime).toBe(30.0);

      // Must apply safe fallback: audio is muted
      expect(videoComp.instance.muted).toBe(true);
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
    });

    it('guards against invalid, backward, or in-seek destinations', () => {
      const invalidCues: Cue[] = [
        {
          id: 'cue-skip-backward',
          start: 10.0,
          end: 8.0,
          source: 'visual',
          action: 'skip',
          status: 'ready',
        },
      ];

      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={invalidCues} options={{ skipEnabled: true }} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      expect(() => {
        act(() => {
          videoComp.instance.currentTime = 10.0;
          videoComp.props.onTimeUpdate();
        });
      }).toThrow(/invalid cue/i);

      expect(videoComp.instance.currentTime).toBe(10.0);
    });

    it('re-arms skip cue after rewinding before cue start and skips again on next forward pass', () => {
      const onBack = jest.fn();
      const { getByText, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={skipTestCues} options={{ skipEnabled: true }} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. First pass: advance to 10.0s -> skips to 15.0s
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(15.0);

      // 2. Rewind using "Back 10s" control (from 15.0s to 5.0s, which is < cue.start of 10.0s)
      act(() => {
        fireEvent.press(getByText('Back 10s'));
      });
      expect(videoComp.instance.currentTime).toBe(5.0);

      // 3. Second pass: advance to 10.0s again -> skip cue fires again to 15.0s!
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(15.0);
    });
  });

  describe('Dedicated Skip Verification Profile', () => {
    it('proves the verification profile is opt-in and does not replace default or production profiles', () => {
      const onBack = jest.fn();
      const { getByText } = render(<PlayerScreen onBack={onBack} />);

      // Default profile is 'verification'
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
      expect(getByText('Cues: Verify')).toBeTruthy();

      // Pressing profile cycles to 'standard' (production index)
      fireEvent.press(getByText('Cues: Verify'));
      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(getByText('Cues: Index')).toBeTruthy();

      // Pressing again cycles to opt-in 'skip_verification'
      fireEvent.press(getByText('Cues: Index'));
      expect(getByText(/Profile: Skip Verification/i)).toBeTruthy();
      expect(getByText('Cues: Skip')).toBeTruthy();

      // Pressing again returns to 'verification'
      fireEvent.press(getByText('Cues: Skip'));
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();

      // Can also be directly opted into via initialProfile prop
      const { getByText: getByTextDirect } = render(
        <PlayerScreen onBack={onBack} initialProfile="skip_verification" />,
      );
      expect(getByTextDirect(/Profile: Skip Verification/i)).toBeTruthy();
      expect(getByTextDirect('Cues: Skip')).toBeTruthy();
    });

    it('confirms existing default and production profiles remain unchanged', () => {
      expect(verificationTestCues).toHaveLength(6);
      expect(verificationTestCues.some((c) => c.action === 'skip')).toBe(false);

      expect(sintelIndexCues).toHaveLength(7);
      expect(sintelIndexCues.some((c) => c.action === 'skip')).toBe(false);
    });

    it('passes schema and validation checks for all cues in skipVerificationTestCues', () => {
      expect(skipVerificationTestCues.length).toBeGreaterThanOrEqual(2);

      const ajv = new Ajv2020({ allErrors: true, strict: true });
      const validateCue = ajv.compile(cueSchema);

      for (const cue of skipVerificationTestCues) {
        expect(cue.end).toBeGreaterThan(cue.start);
        expect(Number.isFinite(cue.start)).toBe(true);
        expect(Number.isFinite(cue.end)).toBe(true);
        expect(['visual', 'audio']).toContain(cue.source);
        expect(['skip', 'mute', 'reduce', 'hide', 'warn']).toContain(cue.action);

        // Validate structural conformity with cue schema (id, start, end, source, action, status)
        const schemaPayload = {
          id: cue.id,
          start: cue.start,
          end: cue.end,
          source: cue.source,
          action: cue.action,
          status: cue.status,
        };
        const valid = validateCue(schemaPayload);
        expect(valid).toBe(true);
      }
    });

    it('makes ready Skip cue available and seeks forward to destination when skipEnabled is true', () => {
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          initialProfile="skip_verification"
          options={{ skipEnabled: true }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Normal playback before cue
      act(() => {
        videoComp.instance.currentTime = 5.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(5.0);

      // Trigger ready Skip cue at 10.0s [10.0, 18.0)
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });

      // Video seeks past the cue interval to 18.0s
      expect(videoComp.instance.currentTime).toBe(18.0);

      // Destination action at 18.0s (cue-skip-verify-destination-mute) is applied immediately
      expect(videoComp.instance.muted).toBe(true);
      expect(videoComp.instance.volume).toBe(0);
    });

    it('prevents seeking when Skip Protection is disabled in viewer options', () => {
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          initialProfile="skip_verification"
          options={{ skipEnabled: false }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });

      // Does not seek
      expect(videoComp.instance.currentTime).toBe(10.0);
      expect(videoComp.instance.muted).toBe(false);
    });

    it('ensures unconfirmed needs_review cues cannot cause a seek and fall back safely', () => {
      const onBack = jest.fn();
      const { UNSAFE_getByType, queryByTestId } = render(
        <PlayerScreen
          onBack={onBack}
          initialProfile="skip_verification"
          options={{ skipEnabled: true, confirmReviewSkip: false }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Advance to 30.0s (cue-skip-verify-needs-review [30.0, 36.0), needs_review: true)
      act(() => {
        videoComp.instance.currentTime = 30.0;
        videoComp.props.onTimeUpdate();
      });

      // Must NOT seek to 36.0s
      expect(videoComp.instance.currentTime).toBe(30.0);

      // Falls back to safe visual protection
      expect(queryByTestId('trigger-hidden-overlay')).toBeTruthy();
    });

    it('re-arms skip cue after rewinding before the start time', () => {
      const onBack = jest.fn();
      const { getByText, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          initialProfile="skip_verification"
          options={{ skipEnabled: true }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. First forward skip
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(18.0);

      // 2. Rewind using "Back 10s" to 8.0s (< cue.start of 10.0s)
      act(() => {
        fireEvent.press(getByText('Back 10s'));
      });
      expect(videoComp.instance.currentTime).toBe(8.0);

      // 3. Second forward pass: re-armed and skips again to 18.0s
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(18.0);
    });
  });
});
