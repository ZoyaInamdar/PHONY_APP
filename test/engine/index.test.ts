import {
  adaptPackagedIndex,
  DEFAULT_VIEWER_OPTIONS,
  PlaybackState,
  resolveActions,
  resolveValidatedPlayback,
  validateCueIndex,
  validateManifest,
} from '../../src/engine';

describe('src/engine entrypoint exports', () => {
  it('exports all core engine functions, classes, and constants', () => {
    expect(typeof adaptPackagedIndex).toBe('function');
    expect(typeof resolveActions).toBe('function');
    expect(typeof PlaybackState).toBe('function');
    expect(typeof resolveValidatedPlayback).toBe('function');
    expect(typeof validateCueIndex).toBe('function');
    expect(typeof validateManifest).toBe('function');
    expect(DEFAULT_VIEWER_OPTIONS).toBeDefined();
    expect(DEFAULT_VIEWER_OPTIONS.muteEnabled).toBe(true);
    expect(DEFAULT_VIEWER_OPTIONS.hideEnabled).toBe(true);
  });
});
