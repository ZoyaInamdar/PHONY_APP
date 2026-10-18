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

class VideoPlayerStub {
  constructor() {
    this._currentTime = 0;
    this._duration = 10.0;
    this._muted = false;
    this._volume = 1;
    this._paused = true;
    this._src = '';
    this._seeking = false;
    this._readyState = 4;
    this._error = null;
    this.surfaceHandle = null;
    this._listeners = {};
    this.mediaElement = { _tag: 'NativeMediaElement', id: 1 };
    this.autoEmitSeeked = true;
    this.seekDelayMs = 0;
    this.deinitialized = false;
    VideoPlayerStub.instances.push(this);
  }

  get currentTime() { return this._currentTime; }
  set currentTime(val) {
    this._currentTime = Number(val);
    this._seeking = true;
    this._emit('seeking');
    this._emit('timeupdate');
    if (this.autoEmitSeeked) {
      if (this.seekDelayMs > 0) {
        setTimeout(() => {
          this._seeking = false;
          this._emit('seeked');
        }, this.seekDelayMs);
      } else {
        this._seeking = false;
        this._emit('seeked');
      }
    }
  }

  get seeking() { return this._seeking; }
  get readyState() { return this._readyState; }
  set readyState(val) { this._readyState = Number(val); }
  get error() { return this._error; }

  fastSeek(val) {
    this.currentTime = val;
  }

  triggerSeeked() {
    this._seeking = false;
    this._emit('seeked');
  }

  triggerError(err = new Error('Mock media error')) {
    this._error = err;
    this._emit('error');
  }

  get duration() { return this._duration; }
  set duration(val) { this._duration = Number(val); }

  get paused() { return this._paused; }
  set paused(val) { this._paused = Boolean(val); }

  get muted() { return this._muted; }
  set muted(val) { this._muted = Boolean(val); }

  get volume() { return this._volume; }
  set volume(val) { this._volume = Number(val); }

  get src() { return this._src; }
  set src(val) {
    this._src = val;
    this._emit('loadstart');
    this._emit('loadedmetadata');
    this._emit('canplay');
  }

  initialize() {
    return Promise.resolve();
  }

  deinitialize() {
    this.surfaceHandle = null;
    this.deinitialized = true;
    return Promise.resolve();
  }

  setSurfaceHandle(handle) {
    this.surfaceHandle = handle;
  }

  clearSurfaceHandle(handle) {
    if (this.surfaceHandle === handle) {
      this.surfaceHandle = null;
    }
  }

  play() {
    this._paused = false;
    this._emit('playing');
    return Promise.resolve();
  }

  pause() {
    this._paused = true;
    this._emit('pause');
  }

  addEventListener(name, callback) {
    if (!this._listeners[name]) this._listeners[name] = [];
    this._listeners[name].push(callback);
  }

  removeEventListener(name, callback) {
    if (!this._listeners[name]) return;
    this._listeners[name] = this._listeners[name].filter(cb => cb !== callback);
  }

  _emit(name) {
    const cbs = this._listeners[name] || [];
    cbs.forEach(cb => {
      try { cb({ name, src: 1 }); } catch (_) {}
    });
  }
}

VideoPlayerStub.instances = [];
VideoPlayerStub.clearInstances = () => {
  VideoPlayerStub.instances = [];
};

class KeplerShareableSurfaceViewStub extends React.Component {
  componentDidMount() {
    if (this.props.onComponentDidMount) this.props.onComponentDidMount();
    if (this.props.onSurfaceViewCreated) {
      this.props.onSurfaceViewCreated('mock-surface-handle-1');
    }
  }

  componentWillUnmount() {
    if (this.props.onSurfaceViewDestroyed) {
      this.props.onSurfaceViewDestroyed('mock-surface-handle-1');
    }
    if (this.props.onComponentWillUnmount) this.props.onComponentWillUnmount();
  }

  render() {
    return React.createElement('View', { testID: 'shareable-surface-view', ...this.props });
  }
}

module.exports = {
  Video: VideoStub,
  VideoPlayer: VideoPlayerStub,
  KeplerShareableSurfaceView: KeplerShareableSurfaceViewStub,
};
