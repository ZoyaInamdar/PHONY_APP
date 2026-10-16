import React, {useEffect, useRef, useState} from 'react';
import {
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import {Video} from '@amazon-devices/react-native-w3cmedia';
import {Cue, EffectiveActions, ViewerOptions} from '../engine/types';
import {PlaybackState} from '../engine/playbackState';
import {
  defaultPlaybackOptions,
  sintelIndexCues,
  verificationTestCues,
} from '../data/playbackCues';

// ---------------------------------------------------------------------------
// Trigger-Aware Player for Fire TV Vega OS
//
// 1. HTTPS Sintel trailer stream (H.264/AAC, 52.2s, verified on-device).
// 2. Dynamic trigger action engine (PlaybackState & resolveActions):
//    - Audio Mute: soundGain === 0 mutes native audio; restores on cue end.
//    - Audio Reduce: soundGain in (0, 1) lowers volume; restores to base volume.
//    - Visual Hide: hides video surface & displays fallback visual protection overlay.
//    - Precedence: Mute overrides Reduce; multiple cues resolved deterministically.
//    - Seeking: immediately updates trigger actions without pipeline glitches.
//    - Manual Mute: user manual mute preserved across cue boundaries.
// ---------------------------------------------------------------------------

const DEFAULT_VIDEO_URL =
  'https://media.w3.org/2010/05/sintel/trailer.mp4';

export interface PlayerScreenProps {
  onBack: () => void;
  cues?: Cue[];
  options?: Partial<ViewerOptions>;
  initialProfile?: 'verification' | 'standard';
  testVideoUrl?: string;
}

export const PlayerScreen = ({
  onBack,
  cues: customCues,
  options: customOptions,
  initialProfile = 'verification',
  testVideoUrl,
}: PlayerScreenProps) => {
  const videoRef = useRef<Video>(null);
  const playbackStateRef = useRef<PlaybackState>(new PlaybackState());
  const lastLoggedSecond = useRef<number>(-1);

  // User baseline settings (independent of automated trigger actions)
  const userVolumeRef = useRef<number>(1.0);
  const userMutedRef = useRef<boolean>(false);
  const isContentHiddenRef = useRef<boolean>(false);
  const effectiveActionsRef = useRef<EffectiveActions | null>(null);
  const lastActiveCuesRef = useRef<string>('');

  // Selected trigger cues & profile
  const [profile, setProfile] = useState<'verification' | 'standard'>(initialProfile);
  const activeCues: Cue[] =
    customCues ?? (profile === 'verification' ? verificationTestCues : sintelIndexCues);

  const viewerOptions: ViewerOptions = {
    ...defaultPlaybackOptions,
    ...customOptions,
  };

  // UI state
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [isContentHidden, setIsContentHidden] = useState(false);
  const [activeActionStatus, setActiveActionStatus] = useState<string>('');
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [status, setStatus] = useState('Loading video…');

  const videoSource = testVideoUrl ?? DEFAULT_VIDEO_URL;

  // Cleanup on unmount: reset engine and restore player settings
  useEffect(() => {
    return () => {
      const video = videoRef.current;
      if (video) {
        video.muted = false;
        video.volume = 1.0;
      }
      playbackStateRef.current.reset();
      if (process.env.NODE_ENV !== 'test') {
        console.log('[Player] Unmounted — playback state reset, audio restored');
      }
    };
  }, []);

  /**
   * Authoritative trigger action evaluator:
   * Resolves active cues at the given timestamp and applies mute, volume reduction,
   * and visual hiding without unnecessary property churn.
   */
  const applyTriggerActions = (time: number, isSeek = false) => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(time) || time < 0) {
      return;
    }

    const actions = playbackStateRef.current.resolveAt(time, activeCues, viewerOptions);
    effectiveActionsRef.current = actions;

    // 1. Audio Mute evaluation
    const triggerWantsMute = actions.soundGain === 0;
    const targetMuted = userMutedRef.current || triggerWantsMute;

    // 2. Audio Volume Reduction evaluation
    let targetVolume = userVolumeRef.current;
    if (!triggerWantsMute && actions.soundGain > 0 && actions.soundGain < 1) {
      targetVolume = Number((userVolumeRef.current * actions.soundGain).toFixed(2));
    }

    // 3. Visual Hiding evaluation
    const targetHide = actions.hide;

    // Compare against current hardware/element state
    const currentMuted = video.muted;
    const currentVolume = video.volume;
    const currentHide = isContentHiddenRef.current;

    const muteChanged = currentMuted !== targetMuted;
    const volumeChanged = Math.abs(currentVolume - targetVolume) > 0.01;
    const hideChanged = currentHide !== targetHide;
    const activeCueIdsKey = actions.activeCueIds.join(',');
    const cuesChanged = lastActiveCuesRef.current !== activeCueIdsKey;

    if (muteChanged) {
      console.log(
        `[Trigger] Mute transition: ${currentMuted} → ${targetMuted} ` +
          `(userMuted: ${userMutedRef.current}, triggerMute: ${triggerWantsMute}, activeCues: [${activeCueIdsKey}])`,
      );
      video.muted = targetMuted;
      setIsMuted(targetMuted);
    }

    if (volumeChanged) {
      console.log(
        `[Trigger] Volume transition: ${currentVolume.toFixed(2)} → ${targetVolume.toFixed(2)} ` +
          `(soundGain: ${actions.soundGain}, base: ${userVolumeRef.current}, activeCues: [${activeCueIdsKey}])`,
      );
      video.volume = targetVolume;
    }

    if (hideChanged) {
      console.log(
        `[Trigger] Visual hide transition: ${currentHide} → ${targetHide} ` +
          `(action: ${actions.visualAction}, activeCues: [${activeCueIdsKey}])`,
      );
      isContentHiddenRef.current = targetHide;
      setIsContentHidden(targetHide);
    }

    if (cuesChanged || muteChanged || volumeChanged || hideChanged || isSeek) {
      lastActiveCuesRef.current = activeCueIdsKey;

      if (actions.activeCueIds.length === 0) {
        setActiveActionStatus('');
      } else {
        const activeCueList = activeCues.filter((c) =>
          actions.activeCueIds.includes(c.id),
        );
        const triggerNames = activeCueList
          .map((c) => c.trigger ?? c.id)
          .join(', ');

        let actionDesc = '';
        if (targetHide) {
          actionDesc += '👁️ Visual Hidden';
        }
        if (triggerWantsMute) {
          actionDesc += `${actionDesc ? ' • ' : ''}🔇 Audio Muted`;
        } else if (actions.soundGain < 1) {
          actionDesc += `${actionDesc ? ' • ' : ''}🔉 Volume Reduced (${Math.round(
            actions.soundGain * 100,
          )}%)`;
        }

        setActiveActionStatus(`${actionDesc} [${triggerNames}]`);
      }
    }
  };

  const togglePlayback = async () => {
    const video = videoRef.current;
    if (!video) {
      setStatus('Player is not ready yet.');
      return;
    }

    if (isPlaying) {
      video.pause();
      setIsPlaying(false);
      setStatus('Paused');
      return;
    }

    console.log('[Player] play() called — muted:', video.muted, 'volume:', video.volume);
    try {
      await video.play();
    } catch {
      if (video.paused) {
        console.log('[Player] play() rejected and video is still paused — genuine failure');
        setStatus('Playback failed. Check the video URL and network.');
      } else {
        console.log('[Player] play() rejected but video is playing — ignoring library quirk');
      }
    }
  };

  const seekBy = (seconds: number) => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    const currentPos = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    const maxDur = Number.isFinite(video.duration) ? video.duration : Infinity;
    const newTime = Math.max(0, Math.min(currentPos + seconds, maxDur));

    console.log(
      `[Player] seek: currentTime=${currentPos.toFixed(2)}` +
        ` delta=${seconds > 0 ? '+' : ''}${seconds}` +
        ` → ${newTime.toFixed(2)}`,
    );

    // Notify playback engine of seek (for skip-invalidation on rewind)
    playbackStateRef.current.seek(newTime, activeCues);

    // Apply native seek
    video.currentTime = newTime;
    setCurrentTime(newTime);

    // Immediately resolve and apply trigger actions for the new seek destination
    applyTriggerActions(newTime, true);
  };

  const toggleMute = () => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    const nextUserMuted = !userMutedRef.current;
    userMutedRef.current = nextUserMuted;
    console.log('[Player] toggleMute — user manual mute set to:', nextUserMuted);

    // If active trigger requires mute, video must remain muted
    const currentActions = effectiveActionsRef.current;
    const triggerWantsMute = currentActions?.soundGain === 0;
    const targetMuted = nextUserMuted || triggerWantsMute;

    video.muted = targetMuted;
    setIsMuted(targetMuted);
    console.log('[Player] toggleMute — effective video.muted now:', targetMuted);
  };

  const toggleProfile = () => {
    const nextProfile = profile === 'verification' ? 'standard' : 'verification';
    setProfile(nextProfile);
    lastActiveCuesRef.current = '';
    playbackStateRef.current.reset();
    console.log(`[Player] Switched trigger profile to: ${nextProfile}`);

    // Re-evaluate actions immediately with new cues
    const video = videoRef.current;
    if (video) {
      applyTriggerActions(video.currentTime, true);
    }
  };

  const formatTime = (seconds: number) => {
    if (!Number.isFinite(seconds) || seconds < 0) {
      return '00:00';
    }

    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  };

  return (
    <View style={styles.container}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>Trigger-Aware Player</Text>
        <Text style={styles.profileBadge}>
          Profile: {profile === 'verification' ? 'Verification Cues' : 'Sintel Index'}
        </Text>
      </View>

      <View style={styles.videoFrame}>
        <Video
          ref={videoRef}
          style={[styles.video, isContentHidden && styles.videoHidden]}
          src={videoSource}
          autoplay={true}
          controls={false}
          volume={1}
          muted={false}
          scalingmode="fit"
          onComponentDidMount={() => {
            console.log('[Player] Component mounted — src:', videoSource);
            setStatus('Player ready');
          }}
          onLoadStart={() => {
            console.log('[Player] onLoadStart — network fetch begun');
            setStatus('Buffering…');
          }}
          onLoadedMetadata={() => {
            const video = videoRef.current;
            if (video) {
              const dur = video.duration;
              console.log('[Player] onLoadedMetadata — duration:', dur, 'muted:', video.muted, 'volume:', video.volume);

              const tracks = video.audioTracks;
              console.log('[Player]   audioTracks.length:', tracks?.length ?? 'none');
              if (tracks && tracks.length > 0) {
                for (let i = 0; i < tracks.length; i++) {
                  const t = tracks.getTrackById(String(i));
                  if (t && !t.enabled) {
                    console.log('[Player]   => enabling audioTrack', t.id);
                    t.enabled = true;
                  }
                }
              }

              if (Number.isFinite(dur)) {
                setDuration(dur);
              }
            }
            setStatus('Video loaded');
          }}
          onCanPlay={() => {
            console.log('[Player] onCanPlay — ready to play');
            setStatus('Ready');
            const video = videoRef.current;
            if (video && video.paused) {
              console.log('[Player] onCanPlay — initiating play()');
              video.play().catch(() => {});
            }
          }}
          onPlaying={() => {
            const video = videoRef.current;
            console.log(
              '[Player] onPlaying — muted:',
              video?.muted,
              'volume:',
              video?.volume,
            );
            setIsPlaying(true);
            setStatus('Playing');
          }}
          onPause={() => {
            console.log('[Player] onPause');
            setIsPlaying(false);
            setStatus('Paused');
          }}
          onTimeUpdate={() => {
            const video = videoRef.current;
            if (video) {
              const t = video.currentTime;
              setCurrentTime(t);
              const d = video.duration;
              if (Number.isFinite(d) && d !== duration) {
                setDuration(d);
              }

              // Apply trigger filtering actions for current playback time
              applyTriggerActions(t);

              if (Math.floor(t) !== Math.floor(lastLoggedSecond.current)) {
                lastLoggedSecond.current = t;
                console.log(
                  `[Player] onTimeUpdate: pos=${t.toFixed(2)}s / dur=${d ? d.toFixed(2) : '?'}s` +
                    ` muted=${video.muted} vol=${video.volume.toFixed(2)} hidden=${isContentHiddenRef.current}`,
                );
              }
            }
          }}
          onSeeked={() => {
            const video = videoRef.current;
            if (video) {
              console.log('[Player] onSeeked — currentTime:', video.currentTime.toFixed(2));
              setCurrentTime(video.currentTime);
              applyTriggerActions(video.currentTime, true);
            }
          }}
          onEnded={() => {
            console.log('[Player] onEnded');
            setIsPlaying(false);
            setStatus('Playback finished');
          }}
          onError={() => {
            const video = videoRef.current;
            const err = video?.error;
            const code = err?.code ?? 'unknown';
            const msg = err?.message ?? 'no message';
            console.log(`[Player] onError — code: ${code} message: ${msg} src: ${videoSource}`);
            setStatus(`Error ${code}: ${msg}`);
          }}
          onAudioFocusGranted={() => {
            console.log('[Player] onAudioFocusGranted — audio focus restored');
            setStatus('Playing');
          }}
          onAudioFocusPaused={() => {
            console.log('[Player] onAudioFocusPaused — audio focus lost temporarily');
            setIsPlaying(false);
            setStatus('Paused (audio focus lost)');
          }}
          onAudioFocusLost={() => {
            console.log('[Player] onAudioFocusLost — audio focus lost permanently');
            setIsPlaying(false);
            setStatus('Stopped (audio focus lost)');
          }}
        />

        {/* Fallback visual overlay rendered when visual hiding is triggered */}
        {isContentHidden && (
          <View
            style={styles.hiddenOverlay}
            testID="trigger-hidden-overlay"
            accessibilityLabel="Visual content hidden due to trigger-aware filter">
            <Text style={styles.hiddenOverlayIcon}>🛡️</Text>
            <Text style={styles.hiddenOverlayTitle}>Visual Content Hidden</Text>
            <Text style={styles.hiddenOverlaySubtitle}>
              Protection active for sensitive visual trigger
            </Text>
          </View>
        )}
      </View>

      {/* Active trigger notification banner */}
      {activeActionStatus ? (
        <View style={styles.triggerBanner} testID="trigger-banner">
          <Text style={styles.triggerBannerText}>{activeActionStatus}</Text>
        </View>
      ) : (
        <View style={styles.triggerBannerPlaceholder} />
      )}

      <Text style={styles.status}>{status}</Text>
      <Text style={styles.time}>
        {formatTime(currentTime)} / {formatTime(duration)}
      </Text>

      <View style={styles.controls}>
        <Control label="Back 10s" onPress={() => seekBy(-10)} />
        <Control
          label={isPlaying ? 'Pause' : 'Play'}
          onPress={togglePlayback}
          preferred
        />
        <Control label="Forward 10s" onPress={() => seekBy(10)} />
        <Control
          label={isMuted ? 'Unmute' : 'Mute'}
          onPress={toggleMute}
        />
        <Control
          label={profile === 'verification' ? 'Cues: Verify' : 'Cues: Index'}
          onPress={toggleProfile}
        />
        <Control label="Home" onPress={onBack} />
      </View>
    </View>
  );
};

const Control = ({
  label,
  onPress,
  preferred = false,
}: {
  label: string;
  onPress: () => void;
  preferred?: boolean;
}) => (
  <TouchableOpacity
    style={[styles.control, preferred && styles.preferredControl]}
    onPress={onPress}
    accessibilityRole="button"
    accessibilityLabel={label}
    hasTVPreferredFocus={preferred}
    activeOpacity={0.8}>
    <Text style={styles.controlText}>{label}</Text>
  </TouchableOpacity>
);

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#101820',
    paddingHorizontal: 48,
    paddingVertical: 32,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  title: {
    color: '#FFFFFF',
    fontSize: 32,
    fontWeight: 'bold',
  },
  profileBadge: {
    color: '#38BDF8',
    backgroundColor: '#0F172A',
    borderColor: '#0284C7',
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
    fontSize: 14,
    fontWeight: '600',
  },
  videoFrame: {
    height: 400,
    backgroundColor: '#000000',
    borderRadius: 12,
    overflow: 'hidden',
    position: 'relative',
  },
  video: {
    width: '100%',
    height: '100%',
  },
  videoHidden: {
    opacity: 0,
  },
  hiddenOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
    zIndex: 10,
  },
  hiddenOverlayIcon: {
    fontSize: 48,
    marginBottom: 12,
  },
  hiddenOverlayTitle: {
    color: '#F8FAFC',
    fontSize: 24,
    fontWeight: 'bold',
    textAlign: 'center',
  },
  hiddenOverlaySubtitle: {
    color: '#94A3B8',
    fontSize: 16,
    marginTop: 8,
    textAlign: 'center',
  },
  triggerBanner: {
    backgroundColor: '#1E293B',
    borderColor: '#38BDF8',
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 16,
    marginTop: 12,
    alignSelf: 'flex-start',
  },
  triggerBannerPlaceholder: {
    height: 38,
    marginTop: 12,
  },
  triggerBannerText: {
    color: '#F8FAFC',
    fontSize: 15,
    fontWeight: '600',
  },
  status: {
    color: '#FFFFFF',
    fontSize: 18,
    marginTop: 8,
  },
  time: {
    color: '#94A3B8',
    fontSize: 22,
    marginTop: 4,
    fontVariant: ['tabular-nums'],
  },
  controls: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 16,
    marginTop: 20,
  },
  control: {
    backgroundColor: '#1E293B',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 8,
  },
  preferredControl: {
    backgroundColor: '#2563EB',
  },
  controlText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
});
