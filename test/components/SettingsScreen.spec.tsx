import 'react-native';
import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import {
  SettingsScreen,
  sanitizeViewerOptions,
  REDUCTION_GAIN_OPTIONS,
  BLUR_MODE_OPTIONS,
  CANONICAL_TRIGGER_CATEGORIES,
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
});
