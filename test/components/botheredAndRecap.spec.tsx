import 'react-native';
import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import { RecapView } from '../../src/components/RecapView';
import { PlayerScreen } from '../../src/components/PlayerScreen';
import { AppliedAction } from '../../src/engine/recap';
import { Cue } from '../../src/engine/types';
import * as storageModule from '../../src/storage/index';

describe('Person C - "That Bothered Me" and RecapView', () => {
  const mockCues: Cue[] = [
    {
      id: 'cue-profanity',
      start: 5.0,
      end: 10.0,
      source: 'text',
      action: 'mute',
      trigger: 'profanity',
    },
    {
      id: 'cue-flash',
      start: 12.0,
      end: 16.0,
      source: 'visual',
      action: 'hide',
      trigger: 'flashing',
    },
  ];

  it('renders "That Bothered Me" button and saves active trigger to log on press', async () => {
    const appendSpy = jest.spyOn(storageModule, 'appendBotheredLog');
    const onBack = jest.fn();

    const { getByTestId, findByText } = render(
      <PlayerScreen onBack={onBack} cues={mockCues} />,
    );

    const botheredBtn = getByTestId('control-bothered');
    expect(botheredBtn).toBeTruthy();

    await act(async () => {
      fireEvent.press(botheredBtn);
    });

    expect(appendSpy).toHaveBeenCalledTimes(1);
    expect(appendSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        timestamp: expect.any(Number),
        activeTrigger: expect.any(String),
        loggedAt: expect.any(Number),
      }),
    );

    // Toast confirmation displayed on screen
    const toast = await findByText(/🚩 Marked at/i);
    expect(toast).toBeTruthy();

    appendSpy.mockRestore();
  });

  describe('RecapView Component', () => {
    const appliedActions: AppliedAction[] = [
      ...Array(12).fill({ triggerType: 'profanity', appliedEffect: 'muted' }),
      ...Array(2).fill({
        triggerType: 'flash',
        appliedEffect: 'blacked_out',
        requestedAction: 'blur', // blur fell back to blackout
      }),
      { triggerType: 'scene', appliedEffect: 'skipped' },
    ];

    it('displays programmatically generated dynamic recap strings without hardcoding', () => {
      const onClose = jest.fn();
      const onRestart = jest.fn();

      const { getByText, getByTestId } = render(
        <RecapView
          appliedActions={appliedActions}
          onClose={onClose}
          onRestart={onRestart}
        />,
      );

      expect(getByTestId('recap-view')).toBeTruthy();
      expect(getByText('Playback Protection Recap')).toBeTruthy();

      // Formatted phrases
      expect(getByText('12 profanities muted')).toBeTruthy();
      expect(getByText('2 flash scenes blacked out')).toBeTruthy();
      expect(getByText('1 scene skipped')).toBeTruthy();

      // Action buttons
      const doneBtn = getByTestId('recap-done');
      const restartBtn = getByTestId('recap-watch-again');
      expect(doneBtn).toBeTruthy();
      expect(restartBtn).toBeTruthy();

      fireEvent.press(doneBtn);
      expect(onClose).toHaveBeenCalledTimes(1);

      fireEvent.press(restartBtn);
      expect(onRestart).toHaveBeenCalledTimes(1);
    });

    it('renders clean empty state when no actions were applied', () => {
      const onClose = jest.fn();
      const { getByText, getByTestId } = render(
        <RecapView appliedActions={[]} onClose={onClose} />,
      );

      expect(getByTestId('recap-empty')).toBeTruthy();
      expect(getByText('No sensitive triggers encountered')).toBeTruthy();
    });
  });
});
