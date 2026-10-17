import 'react-native';
import React from 'react';
import { render } from '@testing-library/react-native';
import { CaptionRenderer } from '../../src/components/CaptionRenderer';
import { SubtitleCue } from '../../src/subtitles/types';

describe('Person C - CaptionRenderer UI Component', () => {
  const mockCues: SubtitleCue[] = [
    {
      id: 'c1',
      start: 1.0,
      end: 4.0,
      kind: 'speech',
      text: 'What the hell is going on here?',
      flagged_words: [
        {
          word: 'hell',
          spoken_confirmed: null,
        },
      ],
    },
    {
      id: 'c2',
      start: 5.0,
      end: 7.0,
      kind: 'sound_description',
      text: '[gunshots firing]',
      flagged_words: [],
    },
    {
      id: 'c3',
      start: 8.0,
      end: 11.0,
      kind: 'speech',
      text: 'Damn, this is crazy.',
      flagged_words: [
        {
          word: 'Damn',
          spoken_confirmed: false, // caption-only: unheard audio, but caption is censored
        },
      ],
    },
  ];

  it('renders nothing (null) when current time does not match any active caption', () => {
    const { queryByTestId } = render(
      <CaptionRenderer currentTime={0.5} userSettings={{ cues: mockCues }} />,
    );
    expect(queryByTestId('caption-container')).toBeNull();
  });

  it('renders censored text when a speech cue with profanity is active', () => {
    const { getByTestId, getByText } = render(
      <CaptionRenderer currentTime={2.5} userSettings={{ cues: mockCues }} />,
    );

    expect(getByTestId('caption-container')).toBeTruthy();
    expect(getByTestId('caption-text')).toBeTruthy();
    expect(getByText('What the h**l is going on here?')).toBeTruthy();
  });

  it('masks caption regardless of spoken_confirmed being false (caption-only rule)', () => {
    const { getByText } = render(
      <CaptionRenderer currentTime={9.5} userSettings={{ cues: mockCues }} />,
    );

    expect(getByText('D**n, this is crazy.')).toBeTruthy();
  });

  it('keeps sound descriptions intact when replaceSoundDescriptions is false', () => {
    const { getByText } = render(
      <CaptionRenderer
        currentTime={6.0}
        userSettings={{ cues: mockCues, replaceSoundDescriptions: false }}
      />,
    );

    expect(getByText('[gunshots firing]')).toBeTruthy();
  });

  it('replaces sound descriptions with [sound muted] when replaceSoundDescriptions is true', () => {
    const { getByText } = render(
      <CaptionRenderer
        currentTime={6.0}
        userSettings={{ cues: mockCues, replaceSoundDescriptions: true }}
      />,
    );

    expect(getByText('[sound muted]')).toBeTruthy();
  });

  it('does not censor speech text when viewer disables profanity protection in user settings', () => {
    const { getByText } = render(
      <CaptionRenderer
        currentTime={2.5}
        userSettings={{
          cues: mockCues,
          disabledCategories: ['profanity'],
        }}
      />,
    );

    expect(getByText('What the hell is going on here?')).toBeTruthy();
  });
});
