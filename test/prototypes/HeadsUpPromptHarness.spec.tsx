import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import { BackHandler } from '@amazon-devices/react-native-kepler';
import { Video, VideoPlayer } from '@amazon-devices/react-native-w3cmedia';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { HeadsUpPromptHarness } from '../../src/prototypes/HeadsUpPromptHarness';
import { PlayerScreen } from '../../src/components/PlayerScreen';
import { PROMPT_HARNESS_SCENARIOS } from '../../src/data/promptHarnessScenarios';

describe('HeadsUpPromptHarness & Deterministic Runtime Prompt Testing', () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
    (VideoPlayer as any).clearInstances();
  });

  it('renders harness disclaimer, badge, scenario tabs, and metadata info card', () => {
    const onBack = jest.fn();
    const { getByTestId, getByText } = render(
      <HeadsUpPromptHarness onBack={onBack} initialScenario={0} />,
    );

    expect(getByTestId('heads-up-prompt-harness-root')).toBeTruthy();
    expect(getByTestId('harness-disclaimer')).toBeTruthy();
    expect(getByText('PROTOTYPE HARNESS')).toBeTruthy();
    expect(getByTestId('prompt-harness-panel')).toBeTruthy();

    // Verify all 5 scenario tabs are rendered
    PROMPT_HARNESS_SCENARIOS.forEach((s, idx) => {
      expect(getByTestId(`harness-scenario-${idx}`)).toBeTruthy();
      expect(getByText(s.name)).toBeTruthy();
    });

    // Verify seek controls
    expect(getByTestId('harness-seek-before')).toBeTruthy();
    expect(getByTestId('harness-seek-window')).toBeTruthy();
    expect(getByTestId('harness-seek-cue')).toBeTruthy();
    expect(getByTestId('harness-reset-state')).toBeTruthy();
  });

  it('is disabled by default in standard PlayerScreen and can be opened via settings drawer', async () => {
    const onBack = jest.fn();
    const { getByTestId, queryByTestId } = render(
      <PlayerScreen onBack={onBack} />,
    );

    // Harness panel is hidden by default
    expect(queryByTestId('prompt-harness-panel')).toBeNull();

    // Open settings drawer
    fireEvent.press(getByTestId('control-settings'));
    expect(getByTestId('settings-drawer')).toBeTruthy();

    // Toggle prompt harness ON
    const harnessToggle = getByTestId('toggle-prompt-harness');
    expect(harnessToggle).toBeTruthy();
    fireEvent.press(harnessToggle);

    // Close settings drawer
    fireEvent.press(getByTestId('settings-done'));

    // Harness panel is now visible
    expect(getByTestId('prompt-harness-panel')).toBeTruthy();
  });

  it('Scenario 1: Verified eligible skip cue shows Skip Scene and jumps past cue end', () => {
    const onBack = jest.fn();
    const { getByTestId, UNSAFE_getByType } = render(
      <HeadsUpPromptHarness onBack={onBack} initialScenario={0} />,
    );

    const videoComp = UNSAFE_getByType(Video);

    // Seek into window (6.0s) via harness button
    act(() => {
      fireEvent.press(getByTestId('harness-seek-window'));
    });

    // Advance prompt dialog appears
    expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
    expect(getByTestId('prompt-continue')).toBeTruthy();
    expect(getByTestId('prompt-skip')).toBeTruthy();

    // Press Skip Scene
    act(() => {
      fireEvent.press(getByTestId('prompt-skip'));
    });

    // Playback seeks to cue end (14.0s)
    expect(videoComp.instance.currentTime).toBe(14.0);
  });

  it('Scenario 2: Needs-review cue with confirmation disabled hides Skip button and engages visual fallback', () => {
    const onBack = jest.fn();
    const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
      <HeadsUpPromptHarness onBack={onBack} initialScenario={1} />,
    );

    const videoComp = UNSAFE_getByType(Video);

    // Seek into window (16.0s)
    act(() => {
      fireEvent.press(getByTestId('harness-seek-window'));
    });

    // Prompt appears with Continue ONLY (Skip Scene is hidden)
    expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
    expect(getByTestId('prompt-continue')).toBeTruthy();
    expect(queryByTestId('prompt-skip')).toBeNull();

    // Acknowledge prompt via Continue
    act(() => {
      fireEvent.press(getByTestId('prompt-continue'));
    });
    expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

    // Seek to cue start (20.0s)
    act(() => {
      fireEvent.press(getByTestId('harness-seek-cue'));
    });

    // Downgraded review policy engages visual hide protection fallback
    expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
  });

  it('Scenario 3: Needs-review cue with confirmation enabled displays Skip button and skips to destination', () => {
    const onBack = jest.fn();
    const { getByTestId, UNSAFE_getByType } = render(
      <HeadsUpPromptHarness onBack={onBack} initialScenario={2} />,
    );

    const videoComp = UNSAFE_getByType(Video);

    // Seek into window (16.0s)
    act(() => {
      fireEvent.press(getByTestId('harness-seek-window'));
    });

    // Both Continue and Skip Scene buttons are visible
    expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
    expect(getByTestId('prompt-continue')).toBeTruthy();
    expect(getByTestId('prompt-skip')).toBeTruthy();

    // Skip Scene jumps to 24.0s
    act(() => {
      fireEvent.press(getByTestId('prompt-skip'));
    });
    expect(videoComp.instance.currentTime).toBe(24.0);
  });

  it('Scenario 4: Non-skip visual hide cue displays prompt without Skip button and hides video at cue start', () => {
    const onBack = jest.fn();
    const { getByTestId, queryByTestId } = render(
      <HeadsUpPromptHarness onBack={onBack} initialScenario={3} />,
    );

    // Seek into window (26.0s)
    act(() => {
      fireEvent.press(getByTestId('harness-seek-window'));
    });

    // Prompt dialog visible with Continue ONLY
    expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
    expect(getByTestId('prompt-continue')).toBeTruthy();
    expect(queryByTestId('prompt-skip')).toBeNull();

    // Acknowledge prompt
    act(() => {
      fireEvent.press(getByTestId('prompt-continue'));
    });

    // Seek to cue start (30.0s)
    act(() => {
      fireEvent.press(getByTestId('harness-seek-cue'));
    });

    // Blackout fallback overlay is active
    expect(getByTestId('trigger-hidden-overlay')).toBeTruthy();
  });

  it('Scenario 5: Chained overlapping/contiguous skip cues jump across both cues to 48.0s', () => {
    const onBack = jest.fn();
    const { getByTestId, UNSAFE_getByType } = render(
      <HeadsUpPromptHarness onBack={onBack} initialScenario={4} />,
    );

    const videoComp = UNSAFE_getByType(Video);

    // Seek into window for first cue (36.0s)
    act(() => {
      fireEvent.press(getByTestId('harness-seek-window'));
    });

    expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
    expect(getByTestId('prompt-skip')).toBeTruthy();

    // Pressing Skip chains across [40.0s, 44.0s) and [43.0s, 48.0s)
    act(() => {
      fireEvent.press(getByTestId('prompt-skip'));
    });

    // Final destination reaches 48.0s
    expect(videoComp.instance.currentTime).toBe(48.0);
  });

  it('supports rewind re-arming and reset state controls', () => {
    const onBack = jest.fn();
    const { getByTestId, queryByTestId, UNSAFE_getByType } = render(
      <HeadsUpPromptHarness onBack={onBack} initialScenario={0} />,
    );

    const videoComp = UNSAFE_getByType(Video);

    // 1. Trigger prompt and acknowledge via Continue
    act(() => {
      fireEvent.press(getByTestId('harness-seek-window'));
    });
    expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

    act(() => {
      fireEvent.press(getByTestId('prompt-continue'));
    });
    expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

    // 2. Advancing time inside window does NOT re-prompt (suppressed)
    act(() => {
      videoComp.instance.currentTime = 7.0;
      videoComp.props.onTimeUpdate();
    });
    expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

    // 3. Rewind before window (3.0s < 5.0s) re-arms prompt
    act(() => {
      fireEvent.press(getByTestId('harness-seek-before'));
    });
    expect(videoComp.instance.currentTime).toBe(3.0);

    // 4. Seeking back into window (6.0s) triggers prompt dialog again!
    act(() => {
      fireEvent.press(getByTestId('harness-seek-window'));
    });
    expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

    // 5. Acknowledge again
    act(() => {
      fireEvent.press(getByTestId('prompt-continue'));
    });
    expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();

    // 6. Test Reset State button
    act(() => {
      fireEvent.press(getByTestId('harness-reset-state'));
    });
    // Seeking to 6.0s triggers prompt again immediately after reset state
    act(() => {
      fireEvent.press(getByTestId('harness-seek-window'));
    });
    expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();
  });

  it('handles TV remote hardware back button to dismiss prompt without exiting player', () => {
    const onBack = jest.fn();
    const { getByTestId, queryByTestId } = render(
      <HeadsUpPromptHarness onBack={onBack} initialScenario={0} />,
    );

    // Seek into window to open prompt
    act(() => {
      fireEvent.press(getByTestId('harness-seek-window'));
    });
    expect(getByTestId('heads-up-prompt-dialog')).toBeTruthy();

    // Find registered BackHandler callback
    const backCalls = (BackHandler.addEventListener as jest.Mock).mock.calls;
    const lastHardwareCall = [...backCalls]
      .reverse()
      .find((call) => call[0] === 'hardwareBackPress');
    expect(lastHardwareCall).toBeDefined();
    const hardwareBackHandler = lastHardwareCall[1];

    // Simulate TV remote Back button press
    act(() => {
      const handled = hardwareBackHandler();
      expect(handled).toBe(true);
    });

    // Prompt is dismissed, but onBack was NOT called (user stayed on player screen)
    expect(queryByTestId('heads-up-prompt-dialog')).toBeNull();
    expect(onBack).not.toHaveBeenCalled();
  });
});
