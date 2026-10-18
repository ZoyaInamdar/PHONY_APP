import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import {
  DualVideoSwitchPrototype,
  VisualProtectionMode,
} from '../../src/prototypes/DualVideoSwitchPrototype';
import { VideoPlayer } from '@amazon-devices/react-native-w3cmedia';

describe('DualVideoSwitchPrototype Component Tests', () => {
  beforeEach(() => {
    (VideoPlayer as any).clearInstances();
  });

  it('initializes clean player on surface creation and starts in clean mode with surface attached to clean player', async () => {
    const onModeChange = jest.fn();
    const { getByTestId, getByText, queryByTestId } = render(
      <DualVideoSwitchPrototype onVisualModeChange={onModeChange} />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;

    expect(getByTestId('shareable-surface-view')).toBeTruthy();
    expect(getByTestId('prototype-subtitle')).toBeTruthy();
    expect(
      getByText(/Offline Experiment.*Unverified on Native Vega HW/),
    ).toBeTruthy();
    expect(getByText(/Mode: clean/)).toBeTruthy();
    expect(queryByTestId('prototype-blackout-overlay')).toBeNull();

    // Verify surface attachment and audio master status
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(blurredPlayer.surfaceHandle).toBeNull();
    expect(cleanPlayer.muted).toBe(false);
    expect(cleanPlayer.volume).toBe(1.0);
    expect(blurredPlayer.muted).toBe(true);
    expect(blurredPlayer.volume).toBe(0.0);
  });

  it('transitions through blackout handover mask to blurred player at 4.5s and returns to clean player at 1.0s', async () => {
    const modeHistory: VisualProtectionMode[] = [];
    const { getByTestId, getByText, queryByTestId } = render(
      <DualVideoSwitchPrototype
        blurStart={3.0}
        blurEnd={6.0}
        onVisualModeChange={(mode) => modeHistory.push(mode)}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;

    // 1. Initial time at 0.0s
    expect(getByText(/Mode: clean/)).toBeTruthy();
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(blurredPlayer.surfaceHandle).toBeNull();

    // 2. Seek into blur interval at 4.5s
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });

    expect(getByText(/Time: 4.50s \| Mode: blurred/)).toBeTruthy();
    expect(queryByTestId('prototype-blackout-overlay')).toBeNull();
    expect(cleanPlayer.surfaceHandle).toBeNull();
    expect(blurredPlayer.surfaceHandle).toBe('mock-surface-handle-1');

    // 3. Seek out of blur interval to 1.0s
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-out'));
    });

    expect(getByText(/Time: 1.00s \| Mode: clean/)).toBeTruthy();
    expect(queryByTestId('prototype-blackout-overlay')).toBeNull();
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(blurredPlayer.surfaceHandle).toBeNull();

    // Verifies the blackout handover mask engaged during the seek transition
    expect(modeHistory).toEqual(['blackout', 'blurred', 'clean']);
  });

  it('handles delayed seek completion: holds blackout handover mask until seeked fires, then attaches surface and switches to blurred', async () => {
    const modeHistory: VisualProtectionMode[] = [];
    const { getByTestId, getByText, queryByTestId } = render(
      <DualVideoSwitchPrototype
        blurStart={3.0}
        blurEnd={6.0}
        onVisualModeChange={(mode) => modeHistory.push(mode)}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;

    // Configure blurred player to delay seeked event
    blurredPlayer.autoEmitSeeked = false;

    // Trigger seek into blur cue
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });

    // While seek is in-flight:
    // 1. Mode is blackout (handover mask)
    // 2. Blackout overlay is mounted to shield the display
    // 3. Surface is NOT attached to blurredPlayer (preventing stale frame / black flash)
    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(getByTestId('prototype-blackout-overlay')).toBeTruthy();
    expect(getByText('Shielding Transition...')).toBeTruthy();
    expect(blurredPlayer.surfaceHandle).toBeNull();
    expect(cleanPlayer.surfaceHandle).toBeNull();
    expect(blurredPlayer.seeking).toBe(true);

    // Now complete the asynchronous seek
    await act(async () => {
      blurredPlayer.triggerSeeked();
    });

    // After seeked fires:
    // 1. Surface is transferred to blurred player
    // 2. Mode switches to blurred
    // 3. Handover mask is lifted
    expect(getByText(/Mode: blurred/)).toBeTruthy();
    expect(queryByTestId('prototype-blackout-overlay')).toBeNull();
    expect(blurredPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(cleanPlayer.surfaceHandle).toBeNull();

    expect(modeHistory).toEqual(['blackout', 'blurred']);
  });

  it('handles seek timeout fallback: remains in blackout fallback if seeked does not arrive before timeout', async () => {
    jest.useFakeTimers();
    const modeHistory: VisualProtectionMode[] = [];

    const { getByTestId, getByText } = render(
      <DualVideoSwitchPrototype
        blurStart={3.0}
        blurEnd={6.0}
        seekTimeoutMs={200}
        onVisualModeChange={(mode) => modeHistory.push(mode)}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [, blurredPlayer] = (VideoPlayer as any).instances;
    blurredPlayer.autoEmitSeeked = false;

    // Seek into blur cue
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });

    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(blurredPlayer.surfaceHandle).toBeNull();

    // Advance timers past timeout threshold
    await act(async () => {
      jest.advanceTimersByTime(250);
    });

    // Must remain safely in blackout fallback
    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(getByTestId('prototype-blackout-overlay')).toBeTruthy();
    expect(blurredPlayer.surfaceHandle).toBeNull();

    // Any late seeked callback arriving after timeout must be discarded
    await act(async () => {
      blurredPlayer.triggerSeeked();
    });

    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(blurredPlayer.surfaceHandle).toBeNull();

    jest.useRealTimers();
  });

  it('guards against rapid transition reversal: cancels pending blurred seek if user seeks back out before seek completes', async () => {
    const modeHistory: VisualProtectionMode[] = [];
    const { getByTestId, getByText, queryByTestId } = render(
      <DualVideoSwitchPrototype
        blurStart={3.0}
        blurEnd={6.0}
        onVisualModeChange={(mode) => modeHistory.push(mode)}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;
    blurredPlayer.autoEmitSeeked = false;

    // 1. Enter blur cue (seek pending)
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });

    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(blurredPlayer.surfaceHandle).toBeNull();

    // 2. Immediately reverse before seeked fires (seek back to clean 1.0s)
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-out'));
    });

    expect(getByText(/Mode: clean/)).toBeTruthy();
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(blurredPlayer.surfaceHandle).toBeNull();

    // 3. Late seeked arrives for the superseded seek
    await act(async () => {
      blurredPlayer.triggerSeeked();
    });

    // Must still remain clean; late seeked must be ignored
    expect(getByText(/Mode: clean/)).toBeTruthy();
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(blurredPlayer.surfaceHandle).toBeNull();
    expect(queryByTestId('prototype-blackout-overlay')).toBeNull();

    expect(modeHistory).toEqual(['blackout', 'clean']);
  });

  it('handles pause and resume during blurred interval while keeping surface on blurred player', async () => {
    const { getByTestId, getByText } = render(
      <DualVideoSwitchPrototype blurStart={3.0} blurEnd={6.0} />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    // Enter blur interval
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });

    // Pause during blur interval
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-pause'));
    });

    expect(getByText(/Mode: blurred \| Playing: NO/)).toBeTruthy();

    // Resume during blur interval
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-play'));
    });

    expect(getByText(/Mode: blurred \| Playing: YES/)).toBeTruthy();
  });

  it('activates blackout fallback when alternate asset fails to load', async () => {
    const modeHistory: VisualProtectionMode[] = [];
    const { getByTestId, getByText } = render(
      <DualVideoSwitchPrototype
        blurStart={3.0}
        blurEnd={6.0}
        simulateAlternateFailure={true}
        onVisualModeChange={(mode) => modeHistory.push(mode)}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    // Seek into the blur interval (3.0s - 6.0s)
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });

    // Because alternate stream failed, mode must fall back to blackout
    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(getByTestId('prototype-blackout-overlay')).toBeTruthy();
    expect(
      getByText('Blackout Fallback Active (Asset Unavailable)'),
    ).toBeTruthy();

    // Seek back out
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-out'));
    });

    expect(getByText(/Mode: clean/)).toBeTruthy();
    expect(modeHistory).toEqual(['blackout', 'clean']);
  });

  it('transitions to blackout fallback if alternate player encounters runtime error', async () => {
    const modeHistory: VisualProtectionMode[] = [];
    const { getByTestId, getByText } = render(
      <DualVideoSwitchPrototype
        blurStart={3.0}
        blurEnd={6.0}
        onVisualModeChange={(mode) => modeHistory.push(mode)}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [, blurredPlayer] = (VideoPlayer as any).instances;

    // Seek into blur interval
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });

    expect(getByText(/Mode: blurred/)).toBeTruthy();

    // Trigger runtime error on blurred player
    await act(async () => {
      blurredPlayer.triggerError(new Error('GStreamer pipeline decoding error'));
    });

    // Must safely fall back to blackout and detach surface
    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(getByTestId('prototype-blackout-overlay')).toBeTruthy();
    expect(blurredPlayer.surfaceHandle).toBeNull();
    expect(modeHistory).toEqual(['blackout', 'blurred', 'blackout']);
  });

  it('safely unmounts during pending seek and cleans up timers and player instances', async () => {
    const { unmount } = render(
      <DualVideoSwitchPrototype blurStart={3.0} blurEnd={6.0} />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;
    blurredPlayer.autoEmitSeeked = false;

    // Trigger seek into blur cue
    await act(async () => {
      cleanPlayer.currentTime = 4.5;
    });

    // Unmount while seek is pending
    act(() => {
      unmount();
    });

    expect(cleanPlayer.deinitialized).toBe(true);
    expect(blurredPlayer.deinitialized).toBe(true);
    expect(cleanPlayer.surfaceHandle).toBeNull();
    expect(blurredPlayer.surfaceHandle).toBeNull();
  });

  it('guarantees audio exclusivity: clean player remains unmuted and blurred player remains strictly muted', async () => {
    const { getByTestId } = render(
      <DualVideoSwitchPrototype blurStart={3.0} blurEnd={6.0} />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;

    // 1. Initial
    expect(cleanPlayer.muted).toBe(false);
    expect(cleanPlayer.volume).toBe(1.0);
    expect(blurredPlayer.muted).toBe(true);
    expect(blurredPlayer.volume).toBe(0.0);

    // 2. During blur
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });

    expect(cleanPlayer.muted).toBe(false);
    expect(cleanPlayer.volume).toBe(1.0);
    expect(blurredPlayer.muted).toBe(true);
    expect(blurredPlayer.volume).toBe(0.0);

    // 3. Play / Pause
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-play'));
    });

    expect(cleanPlayer.muted).toBe(false);
    expect(cleanPlayer.volume).toBe(1.0);
    expect(blurredPlayer.muted).toBe(true);
    expect(blurredPlayer.volume).toBe(0.0);

    // 4. Seek out
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-out'));
    });

    expect(cleanPlayer.muted).toBe(false);
    expect(cleanPlayer.volume).toBe(1.0);
    expect(blurredPlayer.muted).toBe(true);
    expect(blurredPlayer.volume).toBe(0.0);
  });

  it('maintains blackout fallback and never reveals clean video when playback continues inside a blur interval after timeout', async () => {
    jest.useFakeTimers();

    const { getByTestId, getByText, queryByTestId } = render(
      <DualVideoSwitchPrototype
        blurStart={3.0}
        blurEnd={6.0}
        seekTimeoutMs={150}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;
    blurredPlayer.autoEmitSeeked = false;

    // Seek into blur cue
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in')); // 4.5s
    });

    // Advance past timeout
    await act(async () => {
      jest.advanceTimersByTime(200);
    });

    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(getByTestId('prototype-blackout-overlay')).toBeTruthy();
    expect(cleanPlayer.surfaceHandle).toBeNull();
    expect(blurredPlayer.surfaceHandle).toBeNull();

    // Now playback continues forward inside the active blur cue (e.g. 4.8s, 5.2s, 5.8s)
    await act(async () => {
      cleanPlayer.currentTime = 4.8;
    });
    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(getByTestId('prototype-blackout-overlay')).toBeTruthy();
    expect(cleanPlayer.surfaceHandle).toBeNull();

    await act(async () => {
      cleanPlayer.currentTime = 5.5;
    });
    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(getByTestId('prototype-blackout-overlay')).toBeTruthy();
    expect(cleanPlayer.surfaceHandle).toBeNull();

    // Now playback advances across the boundary to 6.2s (outside cue)
    await act(async () => {
      cleanPlayer.currentTime = 6.2;
    });

    // Safely recovers to clean once playhead exits blur cue
    expect(getByText(/Mode: clean/)).toBeTruthy();
    expect(queryByTestId('prototype-blackout-overlay')).toBeNull();
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(blurredPlayer.surfaceHandle).toBeNull();

    jest.useRealTimers();
  });

  it('safely recovers to clean mode when user seeks outside the blur interval after a timeout', async () => {
    jest.useFakeTimers();

    const { getByTestId, getByText, queryByTestId } = render(
      <DualVideoSwitchPrototype
        blurStart={3.0}
        blurEnd={6.0}
        seekTimeoutMs={150}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;
    blurredPlayer.autoEmitSeeked = false;

    // Seek in and timeout
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });
    await act(async () => {
      jest.advanceTimersByTime(200);
    });

    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(cleanPlayer.surfaceHandle).toBeNull();
    expect(blurredPlayer.surfaceHandle).toBeNull();

    // User seeks outside to 1.0s
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-out'));
    });

    expect(getByText(/Time: 1.00s \| Mode: clean/)).toBeTruthy();
    expect(queryByTestId('prototype-blackout-overlay')).toBeNull();
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(blurredPlayer.surfaceHandle).toBeNull();

    jest.useRealTimers();
  });

  it('safely recovers to clean mode when user seeks outside after an alternate player runtime error', async () => {
    const { getByTestId, getByText, queryByTestId } = render(
      <DualVideoSwitchPrototype blurStart={3.0} blurEnd={6.0} />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;

    // Seek into blur cue
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });
    expect(getByText(/Mode: blurred/)).toBeTruthy();

    // Trigger runtime error
    await act(async () => {
      blurredPlayer.triggerError(new Error('GStreamer hardware decoding failure'));
    });

    expect(getByText(/Mode: blackout/)).toBeTruthy();
    expect(blurredPlayer.surfaceHandle).toBeNull();
    expect(cleanPlayer.surfaceHandle).toBeNull();

    // Seek out to 1.0s
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-out'));
    });

    expect(getByText(/Mode: clean/)).toBeTruthy();
    expect(queryByTestId('prototype-blackout-overlay')).toBeNull();
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(blurredPlayer.surfaceHandle).toBeNull();
  });

  it('unmount while a timeout is pending cancels the timer cleanly without throwing or firing state updates', async () => {
    jest.useFakeTimers();

    const { unmount } = render(
      <DualVideoSwitchPrototype
        blurStart={3.0}
        blurEnd={6.0}
        seekTimeoutMs={500}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;
    blurredPlayer.autoEmitSeeked = false;

    // Trigger seek to initiate pending timeout
    await act(async () => {
      cleanPlayer.currentTime = 4.5;
    });

    // Advance 200ms (timeout still pending)
    await act(async () => {
      jest.advanceTimersByTime(200);
    });

    // Unmount with timeout pending
    act(() => {
      unmount();
    });

    // Advance timers past original timeout threshold (500ms)
    await act(async () => {
      jest.advanceTimersByTime(600);
    });

    expect(cleanPlayer.deinitialized).toBe(true);
    expect(blurredPlayer.deinitialized).toBe(true);
    expect(cleanPlayer.surfaceHandle).toBeNull();
    expect(blurredPlayer.surfaceHandle).toBeNull();

    jest.useRealTimers();
  });

  it('ignores a stale seeked event arriving after the user has navigated outside the blur interval', async () => {
    const { getByTestId, getByText, queryByTestId } = render(
      <DualVideoSwitchPrototype blurStart={3.0} blurEnd={6.0} />,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const [cleanPlayer, blurredPlayer] = (VideoPlayer as any).instances;
    blurredPlayer.autoEmitSeeked = false;

    // 1. Seek into blur cue (4.5s)
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-in'));
    });
    expect(getByText(/Mode: blackout/)).toBeTruthy();

    // 2. User navigates out to 1.0s before seeked fires
    await act(async () => {
      fireEvent.press(getByTestId('proto-btn-seek-out'));
    });
    expect(getByText(/Mode: clean/)).toBeTruthy();
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');

    // 3. Stale seeked finally arrives for the cancelled 4.5s seek
    await act(async () => {
      blurredPlayer.triggerSeeked();
    });

    // Must remain strictly in clean mode; clean player must retain surface
    expect(getByText(/Mode: clean/)).toBeTruthy();
    expect(queryByTestId('prototype-blackout-overlay')).toBeNull();
    expect(cleanPlayer.surfaceHandle).toBe('mock-surface-handle-1');
    expect(blurredPlayer.surfaceHandle).toBeNull();
  });
});
