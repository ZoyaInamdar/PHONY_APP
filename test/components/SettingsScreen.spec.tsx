import 'react-native';
import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import {
  SettingsScreen,
  sanitizeViewerOptions,
  REDUCTION_GAIN_OPTIONS,
  BLUR_MODE_OPTIONS,
  CANONICAL_TRIGGER_CATEGORIES,
  ACTION_DISPLAY_LABELS,
} from '../../src/components/SettingsScreen';
import { DEFAULT_VIEWER_OPTIONS, ViewerOptions } from '../../src/engine/types';
import * as storageModule from '../../src/storage/index';

describe('Person B - SettingsScreen UI Component', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('Contract and Constants', () => {
    it('defines agreed reduction gain options', () => {
      expect(REDUCTION_GAIN_OPTIONS).toEqual([0.15, 0.25, 0.30, 0.50, 0.75]);
    });

    it('defines agreed blur modes and canonical trigger categories', () => {
      expect(BLUR_MODE_OPTIONS).toEqual(['off', 'standard', 'high']);
      expect(CANONICAL_TRIGGER_CATEGORIES).toContain('flashing');
      expect(CANONICAL_TRIGGER_CATEGORIES).toContain('spider');
      expect(CANONICAL_TRIGGER_CATEGORIES).toContain('profanity');
      expect(CANONICAL_TRIGGER_CATEGORIES).toContain('loud_spike');
    });

    it('sanitizeViewerOptions handles malformed or partial options correctly', () => {
      const sanitized = sanitizeViewerOptions({
        reduceGain: 0.25,
        hideEnabled: false,
        disabledCategories: ['spider', 'invalid_category'],
      });

      expect(sanitized.reduceGain).toBe(0.25);
      expect(sanitized.hideEnabled).toBe(false);
      expect(sanitized.muteEnabled).toBe(true); // default
      expect(sanitized.disabledCategories).toEqual(['spider']);
    });
  });

  describe('Rendering and Controls', () => {
    it('renders header, back button, and reset button', () => {
      const onBack = jest.fn();
      const { getByTestId, getByText } = render(
        <SettingsScreen onBack={onBack} />,
      );

      expect(getByTestId('settings-screen')).toBeTruthy();
      expect(getByText('Viewer Settings & Calibration')).toBeTruthy();
      expect(getByTestId('settings-back-button')).toBeTruthy();
      expect(getByTestId('settings-reset-button')).toBeTruthy();

      fireEvent.press(getByTestId('settings-back-button'));
      expect(onBack).toHaveBeenCalledTimes(1);
    });

    it('renders volume reduction picker with all gain levels', () => {
      const { getByTestId } = render(<SettingsScreen />);

      expect(getByTestId('reduction-picker')).toBeTruthy();
      expect(getByTestId('reduction-option-15')).toBeTruthy();
      expect(getByTestId('reduction-option-25')).toBeTruthy();
      expect(getByTestId('reduction-option-30')).toBeTruthy();
      expect(getByTestId('reduction-option-50')).toBeTruthy();
      expect(getByTestId('reduction-option-75')).toBeTruthy();
    });

    it('renders blur calibration picker and mandatory CC0 sample label', () => {
      const { getByTestId, getByText } = render(<SettingsScreen />);

      expect(getByTestId('blur-picker')).toBeTruthy();
      expect(getByTestId('blur-option-off')).toBeTruthy();
      expect(getByTestId('blur-option-standard')).toBeTruthy();
      expect(getByTestId('blur-option-high')).toBeTruthy();

      // Calibration image and mandatory CC0 label
      expect(getByTestId('blur-preview-image')).toBeTruthy();
      expect(getByTestId('blur-preview-label')).toBeTruthy();
      expect(getByText('sample: real films vary')).toBeTruthy();
    });

    it('allows changing volume reduction gain and saves to storage', async () => {
      const setItemSpy = jest.spyOn(storageModule, 'setItem').mockResolvedValue(undefined);
      const onSave = jest.fn();

      const { getByTestId } = render(<SettingsScreen onSave={onSave} />);

      const option50 = getByTestId('reduction-option-50');
      await act(async () => {
        fireEvent.press(option50);
      });

      expect(setItemSpy).toHaveBeenCalledWith(
        storageModule.STORAGE_KEYS.USER_SETTINGS,
        expect.objectContaining({
          reduceGain: 0.5,
          reduceEnabled: true,
        }),
      );

      setItemSpy.mockRestore();
    });

    it('allows toggling trigger categories', async () => {
      const setItemSpy = jest.spyOn(storageModule, 'setItem').mockResolvedValue(undefined);

      const { getByTestId } = render(<SettingsScreen />);

      const spiderPill = getByTestId('toggle-category-spider');
      await act(async () => {
        fireEvent.press(spiderPill);
      });

      expect(setItemSpy).toHaveBeenCalledWith(
        storageModule.STORAGE_KEYS.USER_SETTINGS,
        expect.objectContaining({
          disabledCategories: ['spider'],
        }),
      );

      setItemSpy.mockRestore();
    });
  });

  describe('10-foot UI TV Focus & D-pad Trapping Prevention', () => {
    it('ensures all volume reduction picker options have explicit D-pad escape routes', () => {
      const { getByTestId } = render(
        <SettingsScreen
          reductionPickerNextFocusUp="custom-up-anchor"
          reductionPickerNextFocusDown="custom-down-anchor"
        />,
      );

      for (const gain of REDUCTION_GAIN_OPTIONS) {
        const percent = Math.round(gain * 100);
        const button = getByTestId(`reduction-option-${percent}`);
        expect(button.props.nextFocusUp).toBe('custom-up-anchor');
        expect(button.props.nextFocusDown).toBe('custom-down-anchor');
      }
    });

    it('ensures all blur calibration picker options have explicit D-pad escape routes', () => {
      const { getByTestId } = render(
        <SettingsScreen
          blurPickerNextFocusUp="custom-blur-up"
          blurPickerNextFocusDown="custom-blur-down"
        />,
      );

      for (const mode of BLUR_MODE_OPTIONS) {
        const button = getByTestId(`blur-option-${mode}`);
        expect(button.props.nextFocusUp).toBe('custom-blur-up');
        expect(button.props.nextFocusDown).toBe('custom-blur-down');
      }
    });

    it('mounts with TVFocusGuideViews having autoFocus configured for smooth container entry', () => {
      const { getByTestId } = render(<SettingsScreen />);
      const reductionContainer = getByTestId('reduction-picker-container');
      const blurContainer = getByTestId('blur-picker-container');

      expect(reductionContainer.props.autoFocus).toBe(true);
      expect(blurContainer.props.autoFocus).toBe(true);
    });
  });

  describe('Section 6: Per-Trigger Customization & Overrides', () => {
    it('renders the per-trigger overrides section and all 12 canonical trigger cards', () => {
      const { getByTestId, getByText } = render(<SettingsScreen />);

      expect(getByTestId('trigger-action-overrides-section')).toBeTruthy();
      expect(getByText('PER-TRIGGER ACTION OVERRIDES')).toBeTruthy();

      for (const cat of CANONICAL_TRIGGER_CATEGORIES) {
        expect(getByTestId(`trigger-action-picker-${cat}`)).toBeTruthy();
      }
    });

    it('renders human-readable labels and the unavailable indicator for profanity beep', () => {
      const { getByTestId, getByText } = render(<SettingsScreen />);

      expect(ACTION_DISPLAY_LABELS.hide).toBe('Hide / Blackout');
      expect(ACTION_DISPLAY_LABELS.reduce).toBe('Reduce Volume');
      expect(ACTION_DISPLAY_LABELS.skip).toBe('Skip Scene');
      expect(ACTION_DISPLAY_LABELS.mute).toBe('Mute Audio');

      // Flashing actions
      expect(getByTestId('action-option-flashing-hide')).toBeTruthy();
      expect(getByTestId('action-option-flashing-skip')).toBeTruthy();

      // Gunshot actions
      expect(getByTestId('action-option-gunshot-mute')).toBeTruthy();
      expect(getByTestId('action-option-gunshot-reduce')).toBeTruthy();
      expect(getByTestId('action-option-gunshot-skip')).toBeTruthy();

      // Explosion actions
      expect(getByTestId('action-option-explosion-reduce')).toBeTruthy();
      expect(getByTestId('action-option-explosion-mute')).toBeTruthy();
      expect(getByTestId('action-option-explosion-skip')).toBeTruthy();

      // Profanity beep unavailable badge
      expect(getByTestId('action-option-profanity-beep')).toBeTruthy();
      expect(getByText('Beep (Unavailable)')).toBeTruthy();
    });

    it('persists selected action override to storage and restores default when default is selected', async () => {
      const setItemSpy = jest.spyOn(storageModule, 'setItem').mockResolvedValue(undefined);

      const { getByTestId } = render(<SettingsScreen />);

      // 1. Select 'reduce' for gunshot
      const gunshotReduceBtn = getByTestId('action-option-gunshot-reduce');
      await act(async () => {
        fireEvent.press(gunshotReduceBtn);
      });

      expect(setItemSpy).toHaveBeenCalledWith(
        storageModule.STORAGE_KEYS.USER_SETTINGS,
        expect.objectContaining({
          actionOverrides: { gunshot: 'reduce' },
        }),
      );

      // 2. Select 'skip' for flashing -> gunshot override is preserved!
      const flashSkipBtn = getByTestId('action-option-flashing-skip');
      await act(async () => {
        fireEvent.press(flashSkipBtn);
      });

      expect(setItemSpy).toHaveBeenCalledWith(
        storageModule.STORAGE_KEYS.USER_SETTINGS,
        expect.objectContaining({
          actionOverrides: { gunshot: 'reduce', flashing: 'skip' },
        }),
      );

      // 3. Select default ('mute') for gunshot -> gunshot key is deleted, flashing remains
      const gunshotMuteBtn = getByTestId('action-option-gunshot-mute');
      await act(async () => {
        fireEvent.press(gunshotMuteBtn);
      });

      expect(setItemSpy).toHaveBeenCalledWith(
        storageModule.STORAGE_KEYS.USER_SETTINGS,
        expect.objectContaining({
          actionOverrides: { flashing: 'skip' },
        }),
      );

      setItemSpy.mockRestore();
    });
  });
});

