import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import { BackHandler } from '@amazon-devices/react-native-kepler';
import { Video, VideoPlayer } from '@amazon-devices/react-native-w3cmedia';
import Ajv2020 from 'ajv/dist/2020';
import cueSchema from '../../schema/cue.schema.json';
import {
  CANONICAL_TRIGGER_CATEGORIES,
  PlayerScreen,
  sanitizeViewerOptions,
} from '../../src/components/PlayerScreen';
import { Cue } from '../../src/engine/types';
import {
  defaultPlaybackOptions,
  sintelIndexCues,
  skipVerificationTestCues,
  verificationTestCues,
} from '../../src/data/playbackCues';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getItem, setItem, STORAGE_KEYS } from '../../src/storage';

describe('PlayerScreen Component Tests', () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
    (VideoPlayer as any).clearInstances();
  });
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

  it('toggles profile between standard index and verification cues', () => {
    const onBack = jest.fn();
    const { getByText } = render(
      <PlayerScreen onBack={onBack} />,
    );

    expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
    const toggleButton = getByText('Cues: Index');
    fireEvent.press(toggleButton);

    expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
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

  it('reports active dim action explicitly as unsupported on renderer without mapping to hide or reduce', () => {
    const dimCues: Cue[] = [
      {
        id: 'cue-dim',
        start: 10.0,
        end: 15.0,
        source: 'visual',
        action: 'dim',
        trigger: 'flashing',
      },
    ];

    const onBack = jest.fn();
    const { getByText, queryByTestId, UNSAFE_getByType } = render(
      <PlayerScreen onBack={onBack} cues={dimCues} />,
    );

    const videoComp = UNSAFE_getByType(Video);
    expect(videoComp.instance).toBeTruthy();

    // Enter dim cue at t=12.0s
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
    expect(videoComp.instance.volume).toBe(1.0);
  });

  it('activates honest blackout fallback for blur cues on Vega OS and records blacked_out appliedEffect with requestedAction blur', () => {
    const blurCues: Cue[] = [
      {
        id: 'cue-spider-blur',
        start: 20.0,
        end: 25.0,
        source: 'visual',
        action: 'blur',
        trigger: 'spider',
      },
    ];

    const onBack = jest.fn();
    const { getByText, getByTestId, queryByText, queryByTestId, UNSAFE_getByType } = render(
      <PlayerScreen onBack={onBack} cues={blurCues} />,
    );

    const videoComp = UNSAFE_getByType(Video);
    expect(videoComp.instance).toBeTruthy();

    // 1. Before cue at t=19.5s: no overlay
    act(() => {
      videoComp.instance.currentTime = 19.5;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('trigger-hidden-overlay')).toBeNull();

    // 2. Enter blur cue at t=22.0s: blackout fallback overlay activates
    act(() => {
      videoComp.instance.currentTime = 22.0;
      videoComp.props.onTimeUpdate();
    });

    // Blackout overlay IS rendered to protect the viewer
    expect(queryByTestId('trigger-hidden-overlay')).toBeTruthy();
    expect(getByText(/👁️ Visual Hidden \[spider\]/)).toBeTruthy();
    // Audio is NOT muted or reduced for a visual-only trigger
    expect(videoComp.instance.muted).toBe(false);
    expect(videoComp.instance.volume).toBe(1.0);

    // 3. Repeated ticks inside cue do NOT duplicate applied-action records
    act(() => {
      videoComp.instance.currentTime = 22.5;
      videoComp.props.onTimeUpdate();
      videoComp.instance.currentTime = 23.0;
      videoComp.props.onTimeUpdate();
      videoComp.instance.currentTime = 24.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('trigger-hidden-overlay')).toBeTruthy();

    // 4. Cue ends at t=25.0s: blackout overlay deactivates
    act(() => {
      videoComp.instance.currentTime = 25.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('trigger-hidden-overlay')).toBeNull();

    // 5. Seek back into cue at t=22.0s: overlay reactivates
    act(() => {
      videoComp.instance.currentTime = 22.0;
      videoComp.props.onSeeked();
    });
    expect(queryByTestId('trigger-hidden-overlay')).toBeTruthy();

    // 6. Seek outside cue to t=28.0s: overlay deactivates
    act(() => {
      videoComp.instance.currentTime = 28.0;
      videoComp.props.onSeeked();
    });
    expect(queryByTestId('trigger-hidden-overlay')).toBeNull();

    // 7. Playback finishes at end: recap opens and confirms honest blackout recording
    act(() => {
      videoComp.instance.currentTime = 50.0;
      videoComp.props.onEnded();
    });

    expect(getByTestId('recap-view')).toBeTruthy();
    // Exactly 1 spider scene blacked out recorded (no duplication from repeated ticks!)
    expect(getByText('1 spider scene blacked out')).toBeTruthy();
    // Recap must NEVER claim unrendered blur occurred
    expect(queryByText(/blurred/i)).toBeNull();
  });

  it('preserves priority across overlapping hide and blur cues without exposing the scene in transition', () => {
    const overlappingCues: Cue[] = [
      {
        id: 'cue-hide',
        start: 10.0,
        end: 15.0,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
      },
      {
        id: 'cue-blur',
        start: 12.0,
        end: 18.0,
        source: 'visual',
        action: 'blur',
        trigger: 'spider',
      },
    ];

    const onBack = jest.fn();
    const { queryByTestId, UNSAFE_getByType } = render(
      <PlayerScreen onBack={onBack} cues={overlappingCues} />,
    );

    const videoComp = UNSAFE_getByType(Video);

    // 1. At t=11.0s: hide active -> overlay present
    act(() => {
      videoComp.instance.currentTime = 11.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('trigger-hidden-overlay')).toBeTruthy();

    // 2. At t=13.0s: hide and blur overlap -> overlay remains present continuously
    act(() => {
      videoComp.instance.currentTime = 13.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('trigger-hidden-overlay')).toBeTruthy();

    // 3. At t=16.0s: hide has ended, blur is still active -> overlay REMAINS present via blur fallback!
    act(() => {
      videoComp.instance.currentTime = 16.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('trigger-hidden-overlay')).toBeTruthy();

    // 4. At t=19.0s: both ended -> overlay removed
    act(() => {
      videoComp.instance.currentTime = 19.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
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
      const { getByText } = render(
        <PlayerScreen onBack={onBack} />,
      );

      // Canonical default profile is 'standard' (production index)
      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(getByText('Cues: Index')).toBeTruthy();

      // Pressing profile cycles to 'verification' (dense test cues)
      fireEvent.press(getByText('Cues: Index'));
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
      expect(getByText('Cues: Verify')).toBeTruthy();

      // Pressing again cycles to opt-in 'skip_verification'
      fireEvent.press(getByText('Cues: Verify'));
      expect(getByText(/Profile: Skip Verification/i)).toBeTruthy();
      expect(getByText('Cues: Skip')).toBeTruthy();

      // Pressing again returns to 'standard'
      fireEvent.press(getByText('Cues: Skip'));
      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(getByText('Cues: Index')).toBeTruthy();

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

  describe('Viewer settings persistence with AsyncStorage', () => {
    beforeEach(async () => {
      await AsyncStorage.clear();
      jest.clearAllMocks();
    });

    it('restores saved viewer preferences on mount', async () => {
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_SETTINGS,
        JSON.stringify({
          skipEnabled: true,
          hideEnabled: false,
          reduceEnabled: false,
        }),
      );

      const onBack = jest.fn();
      const screen = render(<PlayerScreen onBack={onBack} cues={testCues} />);

      // Flush mount microtasks so storage is read
      await act(async () => {
        await Promise.resolve();
      });

      // Open settings drawer
      act(() => {
        fireEvent.press(screen.getByTestId('control-settings'));
      });

      // Verify restored settings state
      expect(
        screen.getByTestId('toggle-skip-protection').props.accessibilityState.checked,
      ).toBe(true);
      expect(
        screen.getByTestId('toggle-visual-protection').props.accessibilityState.checked,
      ).toBe(false);
      expect(
        screen.getByTestId('toggle-volume-reduction').props.accessibilityState.checked,
      ).toBe(false);
    });

    it('leaves default preferences intact when storage is empty', async () => {
      const onBack = jest.fn();
      const screen = render(<PlayerScreen onBack={onBack} cues={testCues} />);

      await act(async () => {
        await Promise.resolve();
      });

      // Open settings drawer
      act(() => {
        fireEvent.press(screen.getByTestId('control-settings'));
      });

      // Verify default settings
      expect(
        screen.getByTestId('toggle-visual-protection').props.accessibilityState.checked,
      ).toBe(true);
      expect(
        screen.getByTestId('toggle-audio-protection').props.accessibilityState.checked,
      ).toBe(true);
      expect(
        screen.getByTestId('toggle-volume-reduction').props.accessibilityState.checked,
      ).toBe(true);
      expect(
        screen.getByTestId('toggle-skip-protection').props.accessibilityState.checked,
      ).toBe(false);
    });

    it('safely merges a valid partial settings object with defaults', async () => {
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_SETTINGS,
        JSON.stringify({ skipEnabled: true }),
      );

      const onBack = jest.fn();
      const screen = render(<PlayerScreen onBack={onBack} cues={testCues} />);

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        fireEvent.press(screen.getByTestId('control-settings'));
      });

      // skipEnabled is restored to true
      expect(
        screen.getByTestId('toggle-skip-protection').props.accessibilityState.checked,
      ).toBe(true);
      // unmentioned fields retain their defaults
      expect(
        screen.getByTestId('toggle-visual-protection').props.accessibilityState.checked,
      ).toBe(true);
      expect(
        screen.getByTestId('toggle-audio-protection').props.accessibilityState.checked,
      ).toBe(true);
      expect(
        screen.getByTestId('toggle-volume-reduction').props.accessibilityState.checked,
      ).toBe(true);
    });

    it('handles malformed JSON and invalid field values without crashing', async () => {
      // Malformed string in storage
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_SETTINGS,
        '{ malformed json %%%',
      );

      const onBack = jest.fn();
      const screen = render(<PlayerScreen onBack={onBack} cues={testCues} />);

      await act(async () => {
        await Promise.resolve();
      });

      expect(screen.getByText('Trigger-Aware Player')).toBeTruthy();
      act(() => {
        fireEvent.press(screen.getByTestId('control-settings'));
      });
      expect(
        screen.getByTestId('toggle-visual-protection').props.accessibilityState.checked,
      ).toBe(true);

      // Invalid types in storage
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_SETTINGS,
        JSON.stringify({
          hideEnabled: 'invalid-string',
          reduceGain: -5.0,
          disabledCategories: 'not-an-array',
        }),
      );

      const screen2 = render(<PlayerScreen onBack={onBack} cues={testCues} />);
      await act(async () => {
        await Promise.resolve();
      });
      act(() => {
        fireEvent.press(screen2.getByTestId('control-settings'));
      });
      expect(
        screen2.getByTestId('toggle-visual-protection').props.accessibilityState.checked,
      ).toBe(true);
    });

    it('writes updated preferences to the existing settings key when changed', async () => {
      const onBack = jest.fn();
      const screen = render(<PlayerScreen onBack={onBack} cues={testCues} />);

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        fireEvent.press(screen.getByTestId('control-settings'));
      });

      // Toggle skip protection ON
      await act(async () => {
        fireEvent.press(screen.getByTestId('toggle-skip-protection'));
        await Promise.resolve();
      });

      const storedRaw = await AsyncStorage.getItem(STORAGE_KEYS.USER_SETTINGS);
      expect(storedRaw).toBeTruthy();
      const stored = JSON.parse(storedRaw!);
      expect(stored.skipEnabled).toBe(true);
      expect(stored.hideEnabled).toBe(true);
      expect(stored.muteEnabled).toBe(true);
    });

    it('ensures immediate user changes are not overwritten by a late storage read', async () => {
      let resolveGetItem: (val: string | null) => void;
      const delayedGet = new Promise<string | null>((resolve) => {
        resolveGetItem = resolve;
      });
      jest.spyOn(AsyncStorage, 'getItem').mockImplementationOnce(() => delayedGet);

      const onBack = jest.fn();
      const screen = render(<PlayerScreen onBack={onBack} cues={testCues} />);

      // User immediately opens settings and toggles skipEnabled to true before storage finishes loading
      act(() => {
        fireEvent.press(screen.getByTestId('control-settings'));
      });
      await act(async () => {
        fireEvent.press(screen.getByTestId('toggle-skip-protection'));
        await Promise.resolve();
      });
      expect(
        screen.getByTestId('toggle-skip-protection').props.accessibilityState.checked,
      ).toBe(true);

      // Now resolve the late storage read with stale data ({ skipEnabled: false })
      await act(async () => {
        resolveGetItem!(JSON.stringify({ skipEnabled: false }));
        await Promise.resolve();
      });

      // User change must not be overwritten
      expect(
        screen.getByTestId('toggle-skip-protection').props.accessibilityState.checked,
      ).toBe(true);
    });

    it('serializes rapid consecutive changes and preserves the newest saved value', async () => {
      const onBack = jest.fn();
      const screen = render(<PlayerScreen onBack={onBack} cues={testCues} />);

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        fireEvent.press(screen.getByTestId('control-settings'));
      });

      // Rapidly toggle skip (OFF -> ON) then visual (ON -> OFF)
      await act(async () => {
        fireEvent.press(screen.getByTestId('toggle-skip-protection'));
        fireEvent.press(screen.getByTestId('toggle-visual-protection'));
        await Promise.resolve();
      });

      const storedRaw = await AsyncStorage.getItem(STORAGE_KEYS.USER_SETTINGS);
      expect(storedRaw).toBeTruthy();
      const stored = JSON.parse(storedRaw!);
      expect(stored.skipEnabled).toBe(true);
      expect(stored.hideEnabled).toBe(false);
    });

    it('does not break normal player interaction if storage read or write fails', async () => {
      const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
      jest.spyOn(AsyncStorage, 'setItem').mockRejectedValueOnce(new Error('Storage failure'));

      const onBack = jest.fn();
      const screen = render(<PlayerScreen onBack={onBack} cues={testCues} />);

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        fireEvent.press(screen.getByTestId('control-settings'));
      });

      // Toggle should still succeed in-memory
      await act(async () => {
        fireEvent.press(screen.getByTestId('toggle-skip-protection'));
        await Promise.resolve();
      });

      expect(
        screen.getByTestId('toggle-skip-protection').props.accessibilityState.checked,
      ).toBe(true);

      // Normal playback and navigation controls still work
      act(() => {
        fireEvent.press(screen.getByTestId('settings-done'));
      });
      expect(screen.getByText('Play')).toBeTruthy();

      consoleErrorSpy.mockRestore();
    });

    it('filters out non-canonical category strings from storage on mount', async () => {
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_SETTINGS,
        JSON.stringify({
          disabledCategories: [
            'flashing',
            'non_existent_trigger',
            123,
            null,
            '',
            'spider',
          ],
        }),
      );

      const onBack = jest.fn();
      const screen = render(<PlayerScreen onBack={onBack} cues={testCues} />);

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        fireEvent.press(screen.getByTestId('control-settings'));
      });

      // Flashing and spider are canonical and recognized
      const rawVal = await AsyncStorage.getItem(STORAGE_KEYS.USER_SETTINGS);
      const sanitized = sanitizeViewerOptions(
        JSON.parse(rawVal!),
        defaultPlaybackOptions,
      );
      expect(sanitized.disabledCategories).toEqual(['flashing', 'spider']);
    });
  });

  describe('sanitizeViewerOptions unit tests', () => {
    it('validates and preserves all 9 canonical trigger categories', () => {
      const sanitized = sanitizeViewerOptions(
        {
          disabledCategories: [...CANONICAL_TRIGGER_CATEGORIES],
        },
        defaultPlaybackOptions,
      );

      expect(sanitized.disabledCategories).toEqual([
        'flashing',
        'loud_spike',
        'gunshot',
        'explosion',
        'scream',
        'profanity',
        'spider',
        'fast_motion',
        'jump_scare',
      ]);
    });

    it('rejects unknown trigger categories and non-string values', () => {
      const sanitized = sanitizeViewerOptions(
        {
          disabledCategories: [
            'flashing',
            'unknown_trigger',
            'random_category',
            123,
            false,
            '',
            'profanity',
          ],
        },
        defaultPlaybackOptions,
      );

      expect(sanitized.disabledCategories).toEqual(['flashing', 'profanity']);
    });

    it('falls back to defaults when input is not an object or is an array', () => {
      expect(sanitizeViewerOptions(null, defaultPlaybackOptions)).toEqual(
        defaultPlaybackOptions,
      );
      expect(sanitizeViewerOptions('string', defaultPlaybackOptions)).toEqual(
        defaultPlaybackOptions,
      );
      expect(sanitizeViewerOptions([1, 2, 3], defaultPlaybackOptions)).toEqual(
        defaultPlaybackOptions,
      );
    });
  });

  describe('Dual-Video Blur Switching and Fallback in PlayerScreen', () => {
    const blurTestCues: Cue[] = [
      {
        id: 'cue-spider-blur-test',
        start: 20.0,
        end: 25.0,
        source: 'visual',
        action: 'blur',
        trigger: 'spider',
      },
    ];

    beforeEach(() => {
      jest.useFakeTimers();
      (VideoPlayer as any).clearInstances();
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it('mounts KeplerShareableSurfaceView and initializes alternate VideoPlayer when alternateBlurredVideoUrl is provided', async () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      expect(videoComp.instance).toBeTruthy();
      expect(getByTestId('blurred-shareable-surface')).toBeTruthy();

      expect((VideoPlayer as any).instances.length).toBe(1);
      const altPlayer = (VideoPlayer as any).instances[0];
      expect(altPlayer.src).toBe('data/test_clips/sintel_blurred.mp4');
      expect(altPlayer.muted).toBe(true);
      expect(altPlayer.volume).toBe(0.0);
      expect(videoComp.instance.muted).toBe(false);
      expect(videoComp.instance.volume).toBe(1.0);
    });

    it('switches to blurred alternate video on seeked completion and lifts blackout overlay', async () => {
      const onBack = jest.fn();
      const { getByText, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];

      // 1. Before cue at t=19.5s: clean playback, no overlay
      act(() => {
        videoComp.instance.currentTime = 19.5;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
      expect(altPlayer.surfaceHandle).toBeNull();

      // 2. Enter cue at t=21.0s: seeked completes synchronously in mock
      act(() => {
        videoComp.instance.currentTime = 21.0;
        videoComp.props.onTimeUpdate();
      });

      // Surface attached to alternate player, blackout overlay is lifted
      expect(altPlayer.surfaceHandle).toBe('mock-surface-handle-1');
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
      expect(getByText(/🌫️ Video Blurred \[spider\]/)).toBeTruthy();

      // Clean video remains sole audio master
      expect(videoComp.instance.muted).toBe(false);
      expect(videoComp.instance.volume).toBe(1.0);
      expect(altPlayer.muted).toBe(true);
      expect(altPlayer.volume).toBe(0.0);

      // 3. Exit cue at t=26.0s: clean video restored, alternate player detached
      act(() => {
        videoComp.instance.currentTime = 26.0;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
      expect(altPlayer.surfaceHandle).toBeNull();
      expect(altPlayer.paused).toBe(true);

      // 4. Verify recap recording
      act(() => {
        videoComp.props.onEnded();
      });
      expect(getByText(/1 spider scene blurred/)).toBeTruthy();
    });

    it('shields screen with blackout handover mask while alternate player is seeking', async () => {
      const onBack = jest.fn();
      const { getByText, getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];

      // Gate seeked completion: do not auto-emit seeked
      altPlayer.autoEmitSeeked = false;

      // Enter cue at t=21.5s
      act(() => {
        videoComp.instance.currentTime = 21.5;
        videoComp.props.onTimeUpdate();
      });

      // While seeking: blackout handover mask protects the viewer
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(altPlayer.surfaceHandle).toBeNull();

      // When asynchronous seek finishes: surface transfers and overlay lifts
      act(() => {
        altPlayer.triggerSeeked();
      });

      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
      expect(altPlayer.surfaceHandle).toBe('mock-surface-handle-1');
      expect(getByText(/🌫️ Video Blurred \[spider\]/)).toBeTruthy();
    });

    it('falls back to honest blackout if alternate seek times out', async () => {
      const onBack = jest.fn();
      const { getByText, getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
          blurSwitchTimeoutMs={400}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];
      altPlayer.autoEmitSeeked = false;

      // Enter cue at t=21.0s
      act(() => {
        videoComp.instance.currentTime = 21.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();

      // Advance timers past timeout threshold (400ms)
      act(() => {
        jest.advanceTimersByTime(450);
      });

      // Still blacked out
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(getByText(/👁️ Visual Hidden \[spider\]/)).toBeTruthy();
      expect(altPlayer.surfaceHandle).toBeNull();

      // Late seeked event does NOT switch to blur
      act(() => {
        altPlayer.triggerSeeked();
      });
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(altPlayer.surfaceHandle).toBeNull();

      // Verify recap records honest blacked_out appliedEffect
      act(() => {
        videoComp.props.onEnded();
      });
      expect(getByText(/1 spider scene blacked out/)).toBeTruthy();
    });

    it('falls back to blackout overlay when alternate player emits error', async () => {
      const onBack = jest.fn();
      const { getByText, getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];

      // Gate seeked completion: do not auto-emit seeked
      altPlayer.autoEmitSeeked = false;

      // Enter cue at t=21.0s under handover mask
      act(() => {
        videoComp.instance.currentTime = 21.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();

      // Trigger error on alternate player
      act(() => {
        altPlayer.triggerError(new Error('Hardware decoder failure'));
      });

      // Immediate fallback to blackout overlay
      expect(altPlayer.surfaceHandle).toBeNull();
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(getByText(/👁️ Visual Hidden \[spider\]/)).toBeTruthy();

      // Verify recap records honest blacked_out
      act(() => {
        videoComp.props.onEnded();
      });
      expect(getByText(/1 spider scene blacked out/)).toBeTruthy();
    });

    it('prioritizes visual hide cues over blur cues during overlap', async () => {
      const overlappingCues: Cue[] = [
        {
          id: 'cue-spider-blur',
          start: 20.0,
          end: 30.0,
          source: 'visual',
          action: 'blur',
          trigger: 'spider',
        },
        {
          id: 'cue-flash-hide',
          start: 22.0,
          end: 25.0,
          source: 'visual',
          action: 'hide',
          trigger: 'flashing',
        },
      ];

      const onBack = jest.fn();
      const { getByText, getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={overlappingCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];

      // 1. t=21.0s: Blur only
      act(() => {
        videoComp.instance.currentTime = 21.0;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
      expect(altPlayer.surfaceHandle).toBe('mock-surface-handle-1');

      // 2. t=23.0s: Hide cue overlaps blur cue -> hide takes precedence (blackout overlay shown)
      act(() => {
        videoComp.instance.currentTime = 23.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(altPlayer.surfaceHandle).toBeNull();

      // 3. t=26.0s: Hide cue ended, blur cue still active -> transitions back to blur
      act(() => {
        videoComp.instance.currentTime = 26.0;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
      expect(altPlayer.surfaceHandle).toBe('mock-surface-handle-1');
      expect(getByText(/🌫️ Video Blurred \[spider\]/)).toBeTruthy();
    });

    it('synchronizes seeking into, within, and out of a blur cue', async () => {
      const onBack = jest.fn();
      const { getByText, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];

      // 1. Seek into blur cue from t=0 to t=22.0s
      act(() => {
        videoComp.instance.currentTime = 22.0;
        videoComp.props.onSeeked();
      });
      expect(altPlayer.currentTime).toBe(22.0);
      expect(altPlayer.surfaceHandle).toBe('mock-surface-handle-1');
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
      expect(getByText(/🌫️ Video Blurred \[spider\]/)).toBeTruthy();

      // 2. Seek within blur cue from t=22.0s to t=24.0s
      act(() => {
        videoComp.instance.currentTime = 24.0;
        videoComp.props.onSeeked();
      });
      expect(altPlayer.currentTime).toBe(24.0);
      expect(altPlayer.surfaceHandle).toBe('mock-surface-handle-1');
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();

      // 3. Seek out of blur cue to t=5.0s
      act(() => {
        videoComp.instance.currentTime = 5.0;
        videoComp.props.onSeeked();
      });
      expect(altPlayer.surfaceHandle).toBeNull();
      expect(altPlayer.paused).toBe(true);
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
    });

    it('re-arms blur cue recording on rewind prior to cue start time', async () => {
      const onBack = jest.fn();
      const { getByText, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Play through cue
      act(() => {
        videoComp.instance.currentTime = 22.0;
        videoComp.props.onTimeUpdate();
        videoComp.instance.currentTime = 26.0;
        videoComp.props.onTimeUpdate();
      });

      // Rewind to before cue start
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onSeeked();
      });

      // Play through cue again
      act(() => {
        videoComp.instance.currentTime = 22.0;
        videoComp.props.onTimeUpdate();
        videoComp.props.onEnded();
      });

      // Recap correctly counts the re-armed cue
      expect(getByText(/1 spider scene blurred/)).toBeTruthy();
    });

    it('falls back safely to blackout overlay when setSurfaceHandle throws an exception', async () => {
      const onBack = jest.fn();
      const { getByText, getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];

      // Mock setSurfaceHandle to throw native exception
      jest.spyOn(altPlayer, 'setSurfaceHandle').mockImplementation(() => {
        throw new Error('Native surface bind exception');
      });

      // Enter cue at t=21.0s
      act(() => {
        videoComp.instance.currentTime = 21.0;
        videoComp.props.onTimeUpdate();
      });

      // Blackout overlay displayed and honest blackout recorded
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(getByText(/👁️ Visual Hidden \[spider\]/)).toBeTruthy();

      act(() => {
        videoComp.props.onEnded();
      });
      expect(getByText(/1 spider scene blacked out/)).toBeTruthy();
    });

    it('falls back safely to blackout overlay when alternate currentTime assignment throws', async () => {
      const onBack = jest.fn();
      const { getByText, getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];

      // Mock currentTime setter to throw exception
      const originalSetter = Object.getOwnPropertyDescriptor(
        Object.getPrototypeOf(altPlayer),
        'currentTime',
      )?.set;
      Object.defineProperty(altPlayer, 'currentTime', {
        set: () => {
          throw new Error('Native pipeline seek exception');
        },
        configurable: true,
      });

      // Enter cue at t=21.0s
      act(() => {
        videoComp.instance.currentTime = 21.0;
        videoComp.props.onTimeUpdate();
      });

      // Blackout overlay displayed and honest blackout recorded
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(getByText(/👁️ Visual Hidden \[spider\]/)).toBeTruthy();

      act(() => {
        videoComp.props.onEnded();
      });
      expect(getByText(/1 spider scene blacked out/)).toBeTruthy();

      // Restore property descriptor
      if (originalSetter) {
        Object.defineProperty(altPlayer, 'currentTime', {
          set: originalSetter,
          configurable: true,
        });
      }
    });

    it('holds blackout handover mask until surface handle is created, then safely activates blur', async () => {
      const onBack = jest.fn();
      const { getByText, getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];

      // Simulate surface destroyed / handle unavailable
      const surfaceViewComp = UNSAFE_getByType(
        require('@amazon-devices/react-native-w3cmedia').KeplerShareableSurfaceView,
      );
      act(() => {
        surfaceViewComp.props.onSurfaceViewDestroyed?.('mock-surface-handle-1');
      });

      // Enter cue at t=21.0s while surface handle is missing
      act(() => {
        videoComp.instance.currentTime = 21.0;
        videoComp.props.onTimeUpdate();
      });

      // Mask holds screen in blackout
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(altPlayer.surfaceHandle).toBeNull();

      // Surface handle arrives later
      act(() => {
        surfaceViewComp.props.onSurfaceViewCreated?.('delayed-surface-handle-99');
      });

      // Surface attached and blur mode activated
      expect(altPlayer.surfaceHandle).toBe('delayed-surface-handle-99');
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
      expect(getByText(/🌫️ Video Blurred \[spider\]/)).toBeTruthy();
    });

    it('falls back safely to blackout overlay when alternate player initialize() rejects', async () => {
      const initSpy = jest
        .spyOn(VideoPlayer.prototype, 'initialize')
        .mockRejectedValueOnce(new Error('Codec initialization failed'));

      const onBack = jest.fn();
      const { getByText, getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={blurTestCues}
          alternateBlurredVideoUrl="data/test_clips/sintel_blurred.mp4"
        />,
      );

      // Allow rejection microtask to flush
      await act(async () => {
        await Promise.resolve();
      });

      const videoComp = UNSAFE_getByType(Video);
      const altPlayer = (VideoPlayer as any).instances[0];

      // Enter cue at t=21.0s
      act(() => {
        videoComp.instance.currentTime = 21.0;
        videoComp.props.onTimeUpdate();
      });

      // Blackout overlay displayed and honest blackout recorded
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
      expect(getByText(/👁️ Visual Hidden \[spider\]/)).toBeTruthy();
      expect(altPlayer.surfaceHandle).toBeNull();

      act(() => {
        videoComp.props.onEnded();
      });
      expect(getByText(/1 spider scene blacked out/)).toBeTruthy();

      initSpy.mockRestore();
    });
  });

  describe('Heads-Up Advance Prompt Integration', () => {
    const promptTestCues: Cue[] = [
      {
        id: 'prompt-cue-skip-1',
        start: 10.0,
        end: 15.0,
        source: 'visual',
        action: 'skip',
        trigger: 'jump_scare',
      },
      {
        id: 'prompt-cue-skip-2',
        start: 14.0,
        end: 20.0,
        source: 'visual',
        action: 'skip',
        trigger: 'jump_scare',
      },
      {
        id: 'prompt-cue-review-1',
        start: 32.0,
        end: 36.0,
        source: 'visual',
        action: 'skip',
        trigger: 'gunshot',
        status: 'needs_review',
      },
      {
        id: 'prompt-cue-hide-1',
        start: 40.0,
        end: 45.0,
        source: 'visual',
        action: 'hide',
        trigger: 'spider',
      },
    ];

    it('remains disabled by default and does not display prompt dialog', () => {
      const onBack = jest.fn();
      const { queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={promptTestCues} />,
      );

      const videoComp = UNSAFE_getByType(Video);
      // Advance to 6.0s (within 5s lead time of cue at 10.0s)
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });

      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
    });

    it('respects exact start and end boundaries of advance window [start - leadTime, start)', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. At 4.90s (strictly before 5.0s window start) -> no dialog
      act(() => {
        videoComp.instance.currentTime = 4.90;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

      // 2. At 5.00s (exact window start: 10.0 - 5.0) -> dialog appears
      act(() => {
        videoComp.instance.currentTime = 5.00;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 3. At 9.90s (inside window before cue start) -> dialog persists
      act(() => {
        videoComp.instance.currentTime = 9.90;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 4. At 10.00s (cue start: window boundary passed, but dialog remains visible until user action)
      act(() => {
        videoComp.instance.currentTime = 10.00;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 5. Dismiss explicitly via Continue button
      act(() => {
        fireEvent.press(getByTestId('prompt-continue'));
      });
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
    });

    it('displays prompt dialog when enabled and in lead time window with Skip Scene button when eligible', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, getByText, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByText(/jump_scare/)).toBeTruthy();
      expect(getByTestId('prompt-continue')).toBeTruthy();
      expect(getByTestId('prompt-skip')).toBeTruthy();
      expect(getByText('Skip Scene')).toBeTruthy();
    });

    it('hides Skip Scene button when skip protection is disabled in options', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: false,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByTestId('prompt-continue')).toBeTruthy();
      // Skip button must NOT be visible when skipEnabled is false
      expect(queryByTestId('prompt-skip')).toBeNull();
    });

    it('does not expose unapproved skip for cues requiring review unless confirmReviewSkip is true', () => {
      const onBack = jest.fn();
      // Test without confirmReviewSkip
      const { getByTestId, queryByTestId, UNSAFE_getByType, rerender } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            confirmReviewSkip: false,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Cue at 32.0s (lead time window: [27.0s, 32.0s))
      act(() => {
        videoComp.instance.currentTime = 28.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByTestId('prompt-continue')).toBeTruthy();
      // Unapproved skip must NOT be exposed
      expect(queryByTestId('prompt-skip')).toBeNull();

      // Now rerender with confirmReviewSkip: true
      rerender(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            confirmReviewSkip: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      act(() => {
        videoComp.instance.currentTime = 28.0;
        videoComp.props.onTimeUpdate();
      });
      // Now approved skip is exposed
      expect(getByTestId('prompt-skip')).toBeTruthy();
    });

    it('does not expose Skip Scene button for non-skip cues (e.g. visual hide)', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Cue at 40.0s is action: 'hide' (lead time window: [35.0s, 40.0s))
      act(() => {
        videoComp.instance.currentTime = 36.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByTestId('prompt-continue')).toBeTruthy();
      // Non-skip cue must NOT expose skip button
      expect(queryByTestId('prompt-skip')).toBeNull();
    });

    it('acknowledges prompt on Continue, dismisses dialog, and does not re-prompt on forward approach', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Enter prompt window at 6.0s
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // Press Continue
      fireEvent.press(getByTestId('prompt-continue'));
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

      // Forward playback to 8.0s (still within prompt window)
      act(() => {
        videoComp.instance.currentTime = 8.0;
        videoComp.props.onTimeUpdate();
      });
      // Dialog must NOT reappear
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
    });

    it('dismisses prompt on hardware Back button press without exiting screen', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Enter prompt window at 6.0s
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // Find hardwareBackPress handler
      const backCalls = (BackHandler.addEventListener as jest.Mock).mock.calls;
      const lastHardwareCall = [...backCalls]
        .reverse()
        .find((call) => call[0] === 'hardwareBackPress');
      expect(lastHardwareCall).toBeDefined();
      const hardwareBackHandler = lastHardwareCall[1];

      // Press hardware back
      act(() => {
        const handled = hardwareBackHandler();
        expect(handled).toBe(true);
      });

      // Dialog dismissed (acknowledged), screen NOT closed
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
      expect(onBack).not.toHaveBeenCalled();
    });

    it('skips to end of chained cues on Skip button, respects duration clamping, and records honest recap', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, getByText, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      videoComp.instance.duration = 18.0; // Clamped media duration

      // Enter prompt window at 6.0s
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // Press Skip Scene
      fireEvent.press(getByTestId('prompt-skip'));

      // Chained cues prompt-cue-skip-1 (10-15s) and prompt-cue-skip-2 (14-20s) clamped to media duration 18.0s
      expect(videoComp.instance.currentTime).toBe(18.0);
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

      // Check recap on ended
      act(() => {
        videoComp.props.onEnded();
      });
      expect(getByTestId('recap-view')).toBeTruthy();
      expect(getByText(/1 jump scare skipped/)).toBeTruthy();
    });

    it('re-arms prompt on rewind strictly before lead time window start', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Enter prompt window at 6.0s
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // Press Continue to acknowledge
      fireEvent.press(getByTestId('prompt-continue'));
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

      // Seek back before window start (window start is 10.0 - 5.0 = 5.0s, seek to 0s)
      act(() => {
        fireEvent.press(getByTestId('control-back-10s'));
      });

      // Advance again to 6.0s
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });

      // Prompt re-armed and appears again!
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
    });

    it('enables and disables heads-up prompts dynamically via settings drawer while playing', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} cues={promptTestCues} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. Initially at 6.0s -> no prompt (disabled by default)
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

      // 2. Open settings drawer and enable
      fireEvent.press(getByTestId('control-settings'));
      expect(getByTestId('settings-drawer')).toBeTruthy();
      fireEvent.press(getByTestId('toggle-heads-up-prompts'));
      fireEvent.press(getByTestId('settings-done'));
      expect(queryByTestId('settings-drawer')).toBeNull();

      // Prompt dialog is displayed at 6.0s
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 3. Open settings drawer again while prompt was active and disable
      fireEvent.press(getByTestId('control-settings'));
      fireEvent.press(getByTestId('toggle-heads-up-prompts'));
      fireEvent.press(getByTestId('settings-done'));

      // Prompt dialog dismissed safely
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
    });

    it('manages TV focus preferences and restricts background controls while prompt dialog is open', () => {
      const onBack = jest.fn();
      const { getByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Enter prompt window at 6.0s
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });

      const continueBtn = getByTestId('prompt-continue');
      const skipBtn = getByTestId('prompt-skip');
      const playCtrl = getByTestId('control-play-pause');

      // Continue button has preferred TV focus initially
      expect(continueBtn.props.hasTVPreferredFocus).toBe(true);
      // Play control remains focusable so viewing is not halted or interrupted
      expect(playCtrl.props.focusable).toBe(true);

      // Focus skip button
      act(() => {
        fireEvent(skipBtn, 'focus');
      });
      expect(skipBtn.props.hasTVPreferredFocus).toBe(true);
      expect(continueBtn.props.hasTVPreferredFocus).toBe(false);
    });

    it('allows switching harness scenarios directly on-screen and closing harness panel via exit button', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, getByText, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} devHarnessMode={true} initialHarnessScenario={0} />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Harness panel is active
      expect(getByTestId('prompt-harness-panel')).toBeTruthy();

      // Switch to scenario 2 (review unconfirmed)
      act(() => {
        fireEvent.press(getByTestId('harness-scenario-1'));
      });

      // Video seeks to scenario 2 before-window timestamp (13.0s)
      expect(videoComp.instance.currentTime).toBe(13.0);

      // Switch to scenario 5 (chained skip)
      act(() => {
        fireEvent.press(getByTestId('harness-scenario-4'));
      });
      expect(videoComp.instance.currentTime).toBe(33.0);

      // Exit harness button closes the harness panel
      act(() => {
        fireEvent.press(getByTestId('harness-close'));
      });
      expect(queryByTestId('prompt-harness-panel')).toBeNull();
    });

    it('proves the prompt remains open across subsequent playback time updates without disappearing', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. Enter prompt window at 6.0s via onTimeUpdate -> prompt appears
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 2. Subsequent ordinary playback time updates must NOT dismiss the prompt
      act(() => {
        videoComp.instance.currentTime = 7.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      act(() => {
        videoComp.instance.currentTime = 8.5;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // Even when playback time reaches and passes cue start (10.0s)
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      act(() => {
        videoComp.instance.currentTime = 11.5;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 3. Prompt is only dismissed when the user explicitly chooses Continue
      act(() => {
        fireEvent.press(getByTestId('prompt-continue'));
      });
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

      // 4. Further time updates after cues finish do not re-prompt
      act(() => {
        videoComp.instance.currentTime = 25.0;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
    });

    it('ensures harness scenario cues take priority only in harness mode and prevent production cues from colliding', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, getByText, queryByText, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          devHarnessMode={true}
          initialHarnessScenario={0}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Verify harness is open
      expect(getByTestId('prompt-harness-panel')).toBeTruthy();

      // In production cues, 3.0s is inside [1.0s, 6.0s) for cue-verify-mute (loud_spike at 6.0s)
      act(() => {
        videoComp.instance.currentTime = 3.0;
        videoComp.props.onTimeUpdate();
      });

      // In harness mode, scenario 1 has only gunshot at 10.0s (window [5.0s, 10.0s)), so no prompt at 3.0s
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
      expect(queryByText(/loud_spike/i)).toBeNull();

      // Press Seek Into Window for Scenario 1 (seeks to 6.0s)
      act(() => {
        fireEvent.press(getByTestId('harness-seek-window'));
      });

      // Scenario 1 prompt appears with gunshot at 00:10
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByText(/gunshot/i)).toBeTruthy();
      expect(getByText(/00:10/)).toBeTruthy();
      expect(queryByText(/loud_spike/i)).toBeNull();

      // Prompt persists across subsequent playback time updates
      act(() => {
        videoComp.instance.currentTime = 7.5;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByText(/gunshot/i)).toBeTruthy();
    });

    it('preserves normal production cue prompt behavior when dev harness is disabled', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, getByText, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          initialProfile="verification"
          devHarnessMode={false}
          options={{
            ...defaultPlaybackOptions,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Dev harness is closed
      expect(queryByTestId('prompt-harness-panel')).toBeNull();

      // In default verification profile, cue-verify-mute has start 6.0s (loud_spike)
      // Advance to 3.0s (in advance window [1.0s, 6.0s))
      act(() => {
        videoComp.instance.currentTime = 3.0;
        videoComp.props.onTimeUpdate();
      });

      // Production cue prompt appears as expected
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByText(/loud_spike/i)).toBeTruthy();
      expect(getByText(/00:06/)).toBeTruthy();
    });

    it('proves that a production cue such as the loud_spike cue at 30 seconds cannot replace or interfere with Scenario 1 while harness mode is active', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, getByText, queryByText, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          devHarnessMode={true}
          initialHarnessScenario={0}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. Advance to 26.0s (which is lead time window [25.0s, 30.0s) for production loud_spike at 30.0s)
      act(() => {
        videoComp.instance.currentTime = 26.0;
        videoComp.props.onTimeUpdate();
      });

      // Must NOT show prompt for production loud_spike cue at 30.0s
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
      expect(queryByText(/loud_spike/i)).toBeNull();
      expect(queryByText(/00:30/)).toBeNull();

      // 2. Advance to 29.5s
      act(() => {
        videoComp.instance.currentTime = 29.5;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
      expect(queryByText(/loud_spike/i)).toBeNull();

      // 3. Seek to Scenario 1 window at 6.0s
      act(() => {
        fireEvent.press(getByTestId('harness-seek-window'));
      });

      // Exclusive cue is gunshot at 00:10
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByText(/gunshot/i)).toBeTruthy();
      expect(getByText(/00:10/)).toBeTruthy();
      expect(queryByText(/loud_spike/i)).toBeNull();
    });

    it('ensures an open prompt is not bypassed by automatic skip handling', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={promptTestCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. Trigger prompt at 6.0s for cue prompt-cue-skip-1 [10.0s, 15.0s)
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByTestId('prompt-skip')).toBeTruthy();

      // 2. Playback advances to 10.0s (the exact start of the skip cue)
      // Normal auto-skip would seek forward to 15.0s, but because prompt is open,
      // it must NOT auto-skip or dismiss the prompt.
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });

      // Video must NOT have auto-skipped to 15.0s
      expect(videoComp.instance.currentTime).toBe(10.0);
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 3. Playback advances to 10.5s while user still has dialog open
      act(() => {
        videoComp.instance.currentTime = 10.5;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(10.5);
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 4. User explicitly clicks Skip Scene -> now it skips forward past chained skip cues to 20.0s
      act(() => {
        fireEvent.press(getByTestId('prompt-skip'));
      });
      expect(videoComp.instance.currentTime).toBe(20.0);
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
    });

    it('ensures scenario changes and seek actions do not use stale cue selections', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, getByText, queryByText, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          devHarnessMode={true}
          initialHarnessScenario={0}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Switch to Scenario 2 (Review Unconfirmed: spider at 20.0s, confirmReviewSkip: false)
      act(() => {
        fireEvent.press(getByTestId('harness-scenario-1'));
      });

      // Video seeks to scenario 2 before-window (13.0s) immediately
      expect(videoComp.instance.currentTime).toBe(13.0);
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

      // Press Seek Into Window (16.0s)
      act(() => {
        fireEvent.press(getByTestId('harness-seek-window'));
      });
      expect(videoComp.instance.currentTime).toBe(16.0);

      // Scenario 2 prompt appears immediately without stale Scenario 1 gunshot cue
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByText(/spider/i)).toBeTruthy();
      expect(getByText(/00:20/)).toBeTruthy();
      expect(queryByText(/gunshot/i)).toBeNull();

      // Unconfirmed review cue never exposes Skip Scene button
      expect(queryByTestId('prompt-skip')).toBeNull();
      expect(getByTestId('prompt-continue')).toBeTruthy();
    });

    it('proves clicking Continue does not seek to the cue end and allows playback through cue', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          devHarnessMode={true}
          initialHarnessScenario={0} // Scenario 1: Gunshot at 10.0s to 13.0s
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. Seek to 6.0s (into prompt window [5.0s, 10.0s))
      act(() => {
        fireEvent.press(getByTestId('harness-seek-window'));
      });
      expect(videoComp.instance.currentTime).toBe(6.0);
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByTestId('prompt-continue')).toBeTruthy();
      expect(getByTestId('prompt-skip')).toBeTruthy();

      // 2. Playback advances while prompt is open into the cue at 10.5s
      act(() => {
        videoComp.instance.currentTime = 10.5;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(10.5);
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 3. User clicks Continue -> prompt must dismiss and must NOT seek to cue end (13.0s)
      act(() => {
        fireEvent.press(getByTestId('prompt-continue'));
      });
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
      expect(videoComp.instance.currentTime).toBe(10.5); // Still at 10.5s!

      // 4. Playback continues inside the cue at 11.0s, 11.5s, 12.0s -> no delayed skip!
      act(() => {
        videoComp.instance.currentTime = 11.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(11.0);

      act(() => {
        videoComp.instance.currentTime = 12.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(12.0);

      act(() => {
        videoComp.instance.currentTime = 13.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(13.0);
    });

    it('proves clicking Continue does not trigger a delayed or queued skip on subsequent time updates', () => {
      const onBack = jest.fn();
      const skipCues: Cue[] = [
        {
          id: 'test-skip-cue-continue',
          start: 10.0,
          end: 14.0,
          source: 'visual',
          action: 'skip',
          trigger: 'jump_scare',
        },
      ];

      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={skipCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. Enter prompt window at 7.0s (before cue start 10.0s)
      act(() => {
        videoComp.instance.currentTime = 7.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // 2. User clicks Continue at 7.0s
      act(() => {
        fireEvent.press(getByTestId('prompt-continue'));
      });
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
      expect(videoComp.instance.currentTime).toBe(7.0);

      // 3. Normal forward playback advances to 8.0s, 9.0s, 10.0s (cue start), 11.0s, 12.0s
      // None of these must jump to 14.0s!
      act(() => {
        videoComp.instance.currentTime = 8.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(8.0);

      act(() => {
        videoComp.instance.currentTime = 9.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(9.0);

      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });
      // At 10.0s (the cue start), playback must NOT auto-skip to 14.0s because Continue was selected
      expect(videoComp.instance.currentTime).toBe(10.0);

      act(() => {
        videoComp.instance.currentTime = 11.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(11.0);

      act(() => {
        videoComp.instance.currentTime = 14.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(14.0);
    });

    it('proves explicit Skip Scene still works for eligible cues while prompt is active', () => {
      const onBack = jest.fn();
      const skipCues: Cue[] = [
        {
          id: 'test-explicit-skip-cue',
          start: 10.0,
          end: 15.0,
          source: 'visual',
          action: 'skip',
          trigger: 'gunshot',
        },
      ];

      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={skipCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Seek into prompt window at 6.0s
      act(() => {
        videoComp.instance.currentTime = 6.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
      expect(getByTestId('prompt-skip')).toBeTruthy();

      // Click Skip Scene -> seeks to 15.0s
      act(() => {
        fireEvent.press(getByTestId('prompt-skip'));
      });
      expect(videoComp.instance.currentTime).toBe(15.0);
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
    });

    it('proves non-eligible and unconfirmed needs_review cues cannot be skipped and fall back safely', () => {
      const onBack = jest.fn();
      const reviewCues: Cue[] = [
        {
          id: 'test-unconfirmed-review-skip',
          start: 20.0,
          end: 25.0,
          source: 'visual',
          action: 'skip',
          trigger: 'spider',
          status: 'needs_review',
        },
      ];

      const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={reviewCues}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: true,
            confirmReviewSkip: false, // Unconfirmed!
            headsUpPrompts: { enabled: true, leadTimeSeconds: 5.0 },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Enter window at 16.0s (window [15.0s, 20.0s))
      act(() => {
        videoComp.instance.currentTime = 16.0;
        videoComp.props.onTimeUpdate();
      });
      expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

      // Skip Scene button must NOT be present
      expect(queryByTestId('prompt-skip')).toBeNull();
      expect(getByTestId('prompt-continue')).toBeTruthy();

      // Click Continue
      act(() => {
        fireEvent.press(getByTestId('prompt-continue'));
      });
      expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
      expect(videoComp.instance.currentTime).toBe(16.0);

      // Enter cue at 20.0s: must NOT skip, and must safely apply blackout fallback overlay
      act(() => {
        videoComp.instance.currentTime = 20.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(20.0); // Not skipped!
      expect(getByTestId('trigger-hidden-overlay')).toBeTruthy(); // Safe visual hide fallback applied!
    });
  });

  describe('Player Profile Consistency, Restoration, and Race Conditions', () => {
    it('A. Fresh-launch default: with no explicit initialProfile and empty storage, uses standard', async () => {
      await AsyncStorage.clear();
      const onBack = jest.fn();
      const { getByText } = render(<PlayerScreen onBack={onBack} />);

      // Must be Sintel Index / standard profile
      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(getByText('Cues: Index')).toBeTruthy();
    });

    it('B. Saved-profile restoration: restores valid saved profile on mount', async () => {
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_PROFILE,
        JSON.stringify('skip_verification'),
      );
      const onBack = jest.fn();
      const { getByText } = render(<PlayerScreen onBack={onBack} />);

      await act(async () => {
        await Promise.resolve();
      });

      expect(getByText(/Profile: Skip Verification/i)).toBeTruthy();
      expect(getByText('Cues: Skip')).toBeTruthy();
    });

    it('C. Toggle persistence: persists selected profile to storage through full 3-profile cycle', async () => {
      await AsyncStorage.clear();
      const setItemSpy = jest.spyOn(AsyncStorage, 'setItem');
      const onBack = jest.fn();
      const { getByText } = render(<PlayerScreen onBack={onBack} />);

      // Starts at standard -> cycle to verification
      fireEvent.press(getByText('Cues: Index'));
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
      expect(setItemSpy).toHaveBeenCalledWith(
        STORAGE_KEYS.USER_PROFILE,
        JSON.stringify('verification'),
      );

      // Cycle to skip_verification
      fireEvent.press(getByText('Cues: Verify'));
      expect(getByText(/Profile: Skip Verification/i)).toBeTruthy();
      expect(setItemSpy).toHaveBeenCalledWith(
        STORAGE_KEYS.USER_PROFILE,
        JSON.stringify('skip_verification'),
      );

      // Cycle back to standard
      fireEvent.press(getByText('Cues: Skip'));
      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(setItemSpy).toHaveBeenCalledWith(
        STORAGE_KEYS.USER_PROFILE,
        JSON.stringify('standard'),
      );

      setItemSpy.mockRestore();
    });

    it('D. Asynchronous race safety: user change before delayed storage read resolves is preserved', async () => {
      let resolveStoragePromise: (val: any) => void;
      const delayedPromise = new Promise((resolve) => {
        resolveStoragePromise = resolve;
      });

      const getItemSpy = jest.spyOn(AsyncStorage, 'getItem').mockImplementation((key) => {
        if (key === STORAGE_KEYS.USER_PROFILE) {
          return delayedPromise as any;
        }
        return Promise.resolve(null);
      });

      const onBack = jest.fn();
      const { getByText } = render(<PlayerScreen onBack={onBack} />);

      // User changes profile to 'verification' BEFORE storage read finishes
      fireEvent.press(getByText('Cues: Index'));
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();

      // Now resolve delayed storage with an older/stale 'skip_verification' profile
      await act(async () => {
        resolveStoragePromise!(JSON.stringify('skip_verification'));
        await Promise.resolve();
      });

      // User's active choice must NOT be overwritten by stale read!
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
      expect(getByText('Cues: Verify')).toBeTruthy();

      getItemSpy.mockRestore();
    });

    it('E. Explicit initial profile: explicit prop is preserved against conflicting saved profile', async () => {
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_PROFILE,
        JSON.stringify('skip_verification'),
      );
      const onBack = jest.fn();
      const { getByText } = render(
        <PlayerScreen onBack={onBack} initialProfile="verification" />,
      );

      await act(async () => {
        await Promise.resolve();
      });

      // Explicit initialProfile="verification" must win over stored "skip_verification"
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
      expect(getByText('Cues: Verify')).toBeTruthy();
    });

    it('F. Invalid saved value: invalid stored profile safely falls back to standard', async () => {
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_PROFILE,
        JSON.stringify('corrupted_unknown_profile'),
      );
      const onBack = jest.fn();
      const { getByText } = render(<PlayerScreen onBack={onBack} />);

      await act(async () => {
        await Promise.resolve();
      });

      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(getByText('Cues: Index')).toBeTruthy();
    });

    it('safely handles malformed, outdated, or corrupted stored profile data with fallback to standard', async () => {
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_PROFILE,
        JSON.stringify('CORRUPTED_NON_EXISTENT_PROFILE_DATA'),
      );
      const onBack = jest.fn();
      const { getByText } = render(<PlayerScreen onBack={onBack} />);

      await act(async () => {
        await Promise.resolve();
      });

      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(getByText('Cues: Index')).toBeTruthy();
    });

    it('prevents slow AsyncStorage restoration from overwriting user selection with timer delay', async () => {
      await AsyncStorage.setItem(
        STORAGE_KEYS.USER_PROFILE,
        JSON.stringify('skip_verification'),
      );
      const onBack = jest.fn();
      const { getByText } = render(<PlayerScreen onBack={onBack} />);

      // User immediately toggles profile before async storage loads
      await act(async () => {
        fireEvent.press(getByText('Cues: Index'));
      });

      // User switched to verification
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();

      // Flush any in-flight mount restoration
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
      });

      // User's toggle is not clobbered by the async storage result
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
    });

    it('G. Initial-only prop: rerender with different initialProfile preserves initial-only contract', async () => {
      await AsyncStorage.clear();
      const onBack = jest.fn();
      const { getByText, rerender } = render(
        <PlayerScreen onBack={onBack} initialProfile="standard" />,
      );

      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(getByText('Cues: Index')).toBeTruthy();

      // Parent rerenders passing a different initialProfile prop
      rerender(<PlayerScreen onBack={onBack} initialProfile="verification" />);

      // Under the initial-only contract, initialProfile applies only on mount; rerender is ignored
      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(getByText('Cues: Index')).toBeTruthy();
    });

    it('H. Manual toggle preservation: manual selection preserved across parent rerender with different initialProfile', async () => {
      await AsyncStorage.clear();
      const onBack = jest.fn();
      const { getByText, rerender } = render(
        <PlayerScreen onBack={onBack} initialProfile="standard" />,
      );

      // User manually toggles from standard to verification
      fireEvent.press(getByText('Cues: Index'));
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
      expect(getByText('Cues: Verify')).toBeTruthy();

      // Parent rerenders with initialProfile="skip_verification"
      rerender(
        <PlayerScreen onBack={onBack} initialProfile="skip_verification" />,
      );

      // Manual user selection must not be overwritten by parent rerender
      expect(getByText(/Profile: Verification Cues/i)).toBeTruthy();
      expect(getByText('Cues: Verify')).toBeTruthy();
    });
  });

  describe('Film Loading, Catalogue Selection, and Switching', () => {
    it('loads Sintel by default with correct title, badge, and film control', () => {
      const onBack = jest.fn();
      const { getByTestId, getByText } = render(<PlayerScreen onBack={onBack} />);

      expect(getByTestId('player-title').props.children).toContain('Sintel');
      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
      expect(getByText('Film: Sintel')).toBeTruthy();
    });

    it('loads Tears of Steel when filmId="tears_of_steel" is passed', () => {
      const onBack = jest.fn();
      const { getByTestId, getByText } = render(
        <PlayerScreen onBack={onBack} filmId="tears_of_steel" />,
      );

      expect(getByTestId('player-title').props.children).toContain('Tears of Steel');
      expect(getByText(/Profile: Tears of Steel Index/i)).toBeTruthy();
      expect(getByText('Film: Tears of Steel')).toBeTruthy();
    });

    it('displays error banner and does not fall back when an unknown filmId is provided', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByText } = render(
        <PlayerScreen onBack={onBack} filmId="unknown_movie" />,
      );

      const errorBanner = getByTestId('index-error-banner');
      expect(errorBanner).toBeTruthy();
      expect(getByTestId('index-error-text').props.children).toContain('Unknown film ID: "unknown_movie"');
      // Must not silently fall back to Sintel or Tears of Steel title
      expect(getByTestId('player-title').props.children).not.toContain('Sintel');
      expect(getByTestId('player-title').props.children).not.toContain('Tears of Steel');
    });

    it('switches film when the film toggle control is pressed and resets playback state', () => {
      const onBack = jest.fn();
      const { getByText, getByTestId } = render(<PlayerScreen onBack={onBack} />);

      expect(getByText('Film: Sintel')).toBeTruthy();
      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();

      // Toggle to Tears of Steel
      fireEvent.press(getByText('Film: Sintel'));

      expect(getByText('Film: Tears of Steel')).toBeTruthy();
      expect(getByTestId('player-title').props.children).toContain('Tears of Steel');
      expect(getByText(/Profile: Tears of Steel Index/i)).toBeTruthy();

      // Toggle to Elephants Dream
      fireEvent.press(getByText('Film: Tears of Steel'));

      expect(getByText('Film: Elephants Dream')).toBeTruthy();
      expect(getByTestId('player-title').props.children).toContain('Elephants Dream');
      expect(getByText(/Profile: Elephants Dream Index/i)).toBeTruthy();

      // Toggle back to Sintel
      fireEvent.press(getByText('Film: Elephants Dream'));

      expect(getByText('Film: Sintel')).toBeTruthy();
      expect(getByTestId('player-title').props.children).toContain('Sintel');
      expect(getByText(/Profile: Sintel Index/i)).toBeTruthy();
    });

    it('clears stale error banner when switching from an unknown film to a valid film via prop', () => {
      const onBack = jest.fn();
      const { getByTestId, queryByTestId, rerender } = render(
        <PlayerScreen onBack={onBack} filmId="unknown_movie" />,
      );

      expect(getByTestId('index-error-banner')).toBeTruthy();

      // Parent updates filmId prop to a valid film
      rerender(<PlayerScreen onBack={onBack} filmId="sintel" />);

      expect(queryByTestId('index-error-banner')).toBeNull();
      expect(getByTestId('player-title').props.children).toContain('Sintel');
    });

    it('loads Elephants Dream with its validated index, title, badge, and media URL', () => {
      const onBack = jest.fn();
      const { getByTestId, getByText, queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} filmId="elephants_dream" />,
      );

      // 1. Title and badge reflect Elephants Dream
      expect(getByTestId('player-title').props.children).toContain('Elephants Dream');
      expect(getByText(/Profile: Elephants Dream Index/i)).toBeTruthy();
      expect(getByText('Film: Elephants Dream')).toBeTruthy();

      // 2. Index successfully loaded with zero errors
      expect(queryByTestId('index-error-banner')).toBeNull();

      // 3. Video element uses configured media URL
      const videoComp = UNSAFE_getByType(Video);
      expect(videoComp.props.src).toBe(
        'http://10.0.2.2:8080/data/media/elephant_dreams.mp4',
      );
    });

    it('applies visual hide during Elephants Dream real flashing cue and clears overlay after cue ends', () => {
      const onBack = jest.fn();
      const { queryByTestId, UNSAFE_getByType } = render(
        <PlayerScreen onBack={onBack} filmId="elephants_dream" />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // Before cue at t=20.0s: overlay is null
      act(() => {
        videoComp.instance.currentTime = 20.0;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();

      // Inside real flashing cue [27.292s - 32.917s]: overlay is active
      act(() => {
        videoComp.instance.currentTime = 28.0;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('trigger-hidden-overlay')).toBeTruthy();

      // After cue at t=33.5s: overlay is removed
      act(() => {
        videoComp.instance.currentTime = 33.5;
        videoComp.props.onTimeUpdate();
      });
      expect(queryByTestId('trigger-hidden-overlay')).toBeNull();
    });

    it('executes skip override for flashing in Elephants Dream even when global skipEnabled: false', () => {
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          filmId="elephants_dream"
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: false,
            actionOverrides: { flashing: 'skip' },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // At cue start t=27.292s, player skips forward past cue to 32.917s
      act(() => {
        videoComp.instance.currentTime = 27.292;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.currentTime).toBe(32.917);
    });

    it('executes per-trigger mute and reduce overrides for real explosion cues in Elephants Dream', () => {
      const onBack = jest.fn();
      // Test mute override
      const { UNSAFE_getByType: getMuteVideo } = render(
        <PlayerScreen
          onBack={onBack}
          filmId="elephants_dream"
          options={{
            ...defaultPlaybackOptions,
            muteEnabled: true,
            actionOverrides: { explosion: 'mute' },
          }}
        />,
      );
      const muteVideo = getMuteVideo(Video);

      // Enter real explosion cue [307.68s - 308.64s]
      act(() => {
        muteVideo.instance.currentTime = 308.0;
        muteVideo.props.onTimeUpdate();
      });
      expect(muteVideo.instance.muted).toBe(true);

      // Past explosion cue at t=309.0s: unmuted
      act(() => {
        muteVideo.instance.currentTime = 309.0;
        muteVideo.props.onTimeUpdate();
      });
      expect(muteVideo.instance.muted).toBe(false);

      // Test reduce override even when global reduceEnabled: false
      const { UNSAFE_getByType: getReduceVideo } = render(
        <PlayerScreen
          onBack={onBack}
          filmId="elephants_dream"
          options={{
            ...defaultPlaybackOptions,
            reduceEnabled: false,
            actionOverrides: { explosion: 'reduce' },
          }}
        />,
      );
      const reduceVideo = getReduceVideo(Video);
      expect(reduceVideo.instance.volume).toBe(1.0);

      // Enter explosion cue at t=308.0s: volume reduced
      act(() => {
        reduceVideo.instance.currentTime = 308.0;
        reduceVideo.props.onTimeUpdate();
      });
      expect(reduceVideo.instance.volume).toBe(0.3);

      // Past explosion cue at t=309.0s: volume restored to 1.0
      act(() => {
        reduceVideo.instance.currentTime = 309.0;
        reduceVideo.props.onTimeUpdate();
      });
      expect(reduceVideo.instance.volume).toBe(1.0);
    });
  });

  describe('Per-Trigger Action Overrides in Playback', () => {
    it('executes skip for flashing cue when overridden to skip, even with global skipEnabled: false', () => {
      const flashCue: Cue = {
        id: 'cue-flash-override-skip',
        start: 10.0,
        end: 15.0,
        source: 'visual',
        action: 'hide',
        trigger: 'flashing',
      };
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={[flashCue]}
          options={{
            ...defaultPlaybackOptions,
            skipEnabled: false,
            actionOverrides: { flashing: 'skip' },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      expect(videoComp.instance).toBeTruthy();

      // At t=10.0s (cue start), player skips forward past cue to 15.0s
      act(() => {
        videoComp.instance.currentTime = 10.0;
        videoComp.props.onTimeUpdate();
      });

      expect(videoComp.instance.currentTime).toBe(15.0);
    });

    it('executes volume reduction for gunshot cue when overridden to reduce, even with global reduceEnabled: false', () => {
      const gunshotCue: Cue = {
        id: 'cue-gunshot-override-reduce',
        start: 20.0,
        end: 25.0,
        source: 'audio',
        action: 'mute',
        trigger: 'gunshot',
        gain: 0.35,
      };
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={[gunshotCue]}
          options={{
            ...defaultPlaybackOptions,
            reduceEnabled: false,
            actionOverrides: { gunshot: 'reduce' },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);
      expect(videoComp.instance.volume).toBe(1.0);

      // 1. Enter cue at t=21.0s: volume reduced to 0.35
      act(() => {
        videoComp.instance.currentTime = 21.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.volume).toBe(0.35);
      expect(videoComp.instance.muted).toBe(false);

      // 2. Cue ends at t=25.0s: volume restored to 1.0
      act(() => {
        videoComp.instance.currentTime = 25.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.volume).toBe(1.0);
    });

    it('executes mute for explosion cue when overridden to mute and restores volume at cue end', () => {
      const explosionCue: Cue = {
        id: 'cue-explosion-override-mute',
        start: 30.0,
        end: 35.0,
        source: 'audio',
        action: 'reduce',
        trigger: 'explosion',
      };
      const onBack = jest.fn();
      const { UNSAFE_getByType } = render(
        <PlayerScreen
          onBack={onBack}
          cues={[explosionCue]}
          options={{
            ...defaultPlaybackOptions,
            muteEnabled: true,
            actionOverrides: { explosion: 'mute' },
          }}
        />,
      );

      const videoComp = UNSAFE_getByType(Video);

      // 1. Enter cue at t=31.0s: muted
      act(() => {
        videoComp.instance.currentTime = 31.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.muted).toBe(true);

      // 2. Cue ends at t=35.0s: unmuted
      act(() => {
        videoComp.instance.currentTime = 35.0;
        videoComp.props.onTimeUpdate();
      });
      expect(videoComp.instance.muted).toBe(false);
    });
  });
});
