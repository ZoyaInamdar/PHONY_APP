// Jest manual mock for @amazon-devices/react-native-w3cmedia
//
// The real module calls TurboModuleRegistry.getEnforcing('KeplerW3CMediaTurboModule')
// at import time, which fails in a Node/Jest environment because the native
// Vega binary is not present. This mock provides a stateful stub so that
// App.spec.tsx and PlayerScreen unit tests can execute and assert on
// muted, volume, currentTime, seeking, and playback state without the
// device-native binary.

'use strict';

const React = require('react');

class VideoStub extends React.Component {
  constructor(props) {
    super(props);
    this._currentTime = props.currentTime || 0;
    this._duration = 52.209;
    this._muted = props.muted !== undefined ? Boolean(props.muted) : false;
    this._volume = props.volume !== undefined ? Number(props.volume) : 1;
    this._paused = props.autoplay !== undefined ? !props.autoplay : true;
    this._audioTracks = { length: 1, getTrackById: (id) => ({ id, enabled: true }) };
  }

  get currentTime() { return this._currentTime; }
  set currentTime(val) { this._currentTime = Number(val); }

  get duration() { return this._duration; }
  set duration(val) { this._duration = Number(val); }

  get paused() { return this._paused; }
  set paused(val) { this._paused = Boolean(val); }

  get muted() { return this._muted; }
  set muted(val) { this._muted = Boolean(val); }

  get volume() { return this._volume; }
  set volume(val) { this._volume = Number(val); }

  get audioTracks() { return this._audioTracks; }

  get error() { return null; }

  play() {
    this._paused = false;
    return Promise.resolve();
  }

  pause() {
    this._paused = true;
  }

  render() {
    return React.createElement('View', this.props);
  }
}

module.exports = {
  Video: VideoStub,
};
