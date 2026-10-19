import React, { useEffect, useRef, useState, useCallback } from 'react';
import { StyleSheet, Text, View, TouchableOpacity } from 'react-native';
import {
  KeplerShareableSurfaceView,
  VideoPlayer,
} from '@amazon-devices/react-native-w3cmedia';

export type VisualProtectionMode = 'clean' | 'blurred' | 'blackout';

export interface DualVideoSwitchPrototypeProps {
  cleanSrc?: string;
  blurredSrc?: string;
  blurStart?: number;
  blurEnd?: number;
  seekTimeoutMs?: number;
  simulateAlternateFailure?: boolean;
  onVisualModeChange?: (mode: VisualProtectionMode, timestamp: number) => void;
  onTimeUpdate?: (timestamp: number) => void;
}

/**
 * Isolated prototype component validating dual-player surface switching
 * using Vega OS KeplerShareableSurfaceView and VideoPlayer APIs.
 *
 * PURPOSE & LIMITATIONS:
 * This component is an isolated offline experiment designed exclusively to evaluate
 * surface detachment, blackout handover masks, and seek-completion gating mechanics
 * between two video players.
 *
 * STRICT SAFEGUARDS:
 * - This prototype does NOT claim native on-device blur rendering or verified hardware
 *   surface synchronization on Fire TV / Vega OS devices.
 * - This component remains strictly isolated from production playback paths.
 * - Production continues to use the proven blackout protection overlay.
 *
 * Architecture safeguards:
 * 1. Single visual surface mounted in the UI tree (KeplerShareableSurfaceView).
 * 2. Clean player acts as continuous audio master and reference clock.
 * 3. Alternate player remains strictly muted (volume = 0, muted = true) at all times.
 * 4. Surface attachment is gated on seek completion ('seeked' event) to prevent stale
 *    frames or black flashes during pipeline preroll.
 * 5. Blackout handover mask shields the surface while the alternate player is seeking.
 * 6. Monotonic transition generation token guards against rapid cue reversals / seek races.
 * 7. Seek timeout fallback keeps blackout active if the alternate player hangs or fails.
 * 8. Surface handle is detached before attachment to ensure safe GStreamer surface transfer.
 * 9. Both players are properly paused and deinitialized on unmount.
 */
export const DualVideoSwitchPrototype: React.FC<DualVideoSwitchPrototypeProps> = ({
  cleanSrc = 'data/prototypes/proto_clean.mp4',
  blurredSrc = 'data/prototypes/proto_blurred.mp4',
  blurStart = 3.0,
  blurEnd = 6.0,
  seekTimeoutMs = 500,
  simulateAlternateFailure = false,
  onVisualModeChange,
  onTimeUpdate,
}) => {
  const [surfaceHandle, setSurfaceHandle] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [visualMode, setVisualMode] = useState<VisualProtectionMode>('clean');
  const [isAlternateReady, setIsAlternateReady] = useState<boolean>(false);
  const [alternateLoadError, setAlternateLoadError] = useState<boolean>(false);

  const cleanPlayerRef = useRef<VideoPlayer | null>(null);
  const blurredPlayerRef = useRef<VideoPlayer | null>(null);
  const surfaceHandleRef = useRef<string | null>(null);
  const currentVisualModeRef = useRef<VisualProtectionMode>('clean');

  // Concurrency & seek race guards
  const transitionTokenRef = useRef<number>(0);
  const activeSeekTokenRef = useRef<number>(0);
  const isSeekingAlternateRef = useRef<boolean>(false);
  const seekTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const alternateLoadErrorRef = useRef<boolean>(false);

  // Keep fresh props accessible to long-lived media listeners
  const propsRef = useRef({
    blurStart,
    blurEnd,
    seekTimeoutMs,
    simulateAlternateFailure,
    onVisualModeChange,
    onTimeUpdate,
  });

  propsRef.current = {
    blurStart,
    blurEnd,
    seekTimeoutMs,
    simulateAlternateFailure,
    onVisualModeChange,
    onTimeUpdate,
  };

  // Helper to safely transfer surface attachment
  const attachSurface = useCallback((target: 'clean' | 'blurred' | 'none') => {
    const handle = surfaceHandleRef.current;
    if (!handle) return;

    const cleanPlayer = cleanPlayerRef.current;
    const blurredPlayer = blurredPlayerRef.current;

    if (target === 'blurred') {
      if (cleanPlayer) {
        cleanPlayer.clearSurfaceHandle(handle);
      }
      if (blurredPlayer) {
        blurredPlayer.setSurfaceHandle(handle);
      }
    } else if (target === 'clean') {
      if (blurredPlayer) {
        blurredPlayer.clearSurfaceHandle(handle);
      }
      if (cleanPlayer) {
        cleanPlayer.setSurfaceHandle(handle);
      }
    } else {
      if (cleanPlayer) cleanPlayer.clearSurfaceHandle(handle);
      if (blurredPlayer) blurredPlayer.clearSurfaceHandle(handle);
    }
  }, []);

  // Initiate blur transition with blackout handover protection and seek completion gate
  const initiateAlternateSeek = useCallback(
    (targetTime: number) => {
      // Guard against duplicate invocations while already seeking or already at target
      if (
        isSeekingAlternateRef.current ||
        (currentVisualModeRef.current === 'blurred' &&
          blurredPlayerRef.current &&
          Math.abs(blurredPlayerRef.current.currentTime - targetTime) < 0.05)
      ) {
        return;
      }

      const token = ++transitionTokenRef.current;
      activeSeekTokenRef.current = token;

      if (seekTimeoutRef.current) {
        clearTimeout(seekTimeoutRef.current);
        seekTimeoutRef.current = null;
      }

      const { simulateAlternateFailure: simFail, seekTimeoutMs: timeoutMs } = propsRef.current;

      // If alternate player failed or is simulated to fail, fall back to blackout directly
      if (simFail || alternateLoadErrorRef.current) {
        isSeekingAlternateRef.current = false;
        if (currentVisualModeRef.current !== 'blackout') {
          currentVisualModeRef.current = 'blackout';
          setVisualMode('blackout');
          attachSurface('none');
          blurredPlayerRef.current?.pause();
          propsRef.current.onVisualModeChange?.('blackout', targetTime);
        }
        return;
      }

      // 1. Activate blackout handover mask immediately to shield screen
      isSeekingAlternateRef.current = true;
      attachSurface('none');
      if (currentVisualModeRef.current !== 'blackout') {
        currentVisualModeRef.current = 'blackout';
        setVisualMode('blackout');
        propsRef.current.onVisualModeChange?.('blackout', targetTime);
      }

      // 2. Set timeout fallback
      seekTimeoutRef.current = setTimeout(() => {
        if (activeSeekTokenRef.current !== token) return;
        console.warn(
          `[DualVideoPrototype] Alternate seek timed out (${timeoutMs}ms); remaining in blackout fallback`,
        );
        isSeekingAlternateRef.current = false;
        if (currentVisualModeRef.current !== 'blackout') {
          currentVisualModeRef.current = 'blackout';
          setVisualMode('blackout');
          attachSurface('none');
          propsRef.current.onVisualModeChange?.('blackout', targetTime);
        }
      }, timeoutMs);

      // 3. Request asynchronous seek on blurred player
      if (blurredPlayerRef.current) {
        try {
          blurredPlayerRef.current.currentTime = targetTime;
        } catch (err) {
          console.warn('[DualVideoPrototype] Failed to assign currentTime to alternate player:', err);
          if (seekTimeoutRef.current) {
            clearTimeout(seekTimeoutRef.current);
            seekTimeoutRef.current = null;
          }
          isSeekingAlternateRef.current = false;
          if (currentVisualModeRef.current !== 'blackout') {
            currentVisualModeRef.current = 'blackout';
            setVisualMode('blackout');
            attachSurface('none');
            propsRef.current.onVisualModeChange?.('blackout', targetTime);
          }
        }
      }
    },
    [attachSurface],
  );

  // Exit blur cue cleanly back to clean player
  const exitToClean = useCallback(
    (targetTime: number) => {
      ++transitionTokenRef.current;
      activeSeekTokenRef.current = 0;
      if (seekTimeoutRef.current) {
        clearTimeout(seekTimeoutRef.current);
        seekTimeoutRef.current = null;
      }
      isSeekingAlternateRef.current = false;

      if (currentVisualModeRef.current !== 'clean') {
        currentVisualModeRef.current = 'clean';
        setVisualMode('clean');
        attachSurface('clean');
        blurredPlayerRef.current?.pause();
        propsRef.current.onVisualModeChange?.('clean', targetTime);
      }
    },
    [attachSurface],
  );

  // Initialize both players on mount
  useEffect(() => {
    let isCancelled = false;

    const cleanPlayer = new VideoPlayer();
    const blurredPlayer = new VideoPlayer();

    cleanPlayerRef.current = cleanPlayer;
    blurredPlayerRef.current = blurredPlayer;

    // Clean player timeupdate handler
    const handleCleanTimeUpdate = () => {
      const t = cleanPlayer.currentTime;
      setCurrentTime(t);
      propsRef.current.onTimeUpdate?.(t);

      const {
        blurStart: bStart,
        blurEnd: bEnd,
        simulateAlternateFailure: simFail,
      } = propsRef.current;
      const isInsideCue = t >= bStart && t < bEnd;

      if (isInsideCue) {
        if (simFail || alternateLoadErrorRef.current) {
          if (currentVisualModeRef.current !== 'blackout') {
            currentVisualModeRef.current = 'blackout';
            setVisualMode('blackout');
            attachSurface('none');
            blurredPlayer.pause();
            propsRef.current.onVisualModeChange?.('blackout', t);
          }
        } else if (
          currentVisualModeRef.current === 'clean' &&
          !isSeekingAlternateRef.current
        ) {
          initiateAlternateSeek(t);
        }
      } else {
        if (
          currentVisualModeRef.current !== 'clean' ||
          isSeekingAlternateRef.current
        ) {
          exitToClean(t);
        }
      }
    };

    // Clean player playing handler
    const handleCleanPlaying = () => {
      setIsPlaying(true);
      if (
        currentVisualModeRef.current === 'blurred' &&
        blurredPlayerRef.current
      ) {
        blurredPlayerRef.current.play().catch(() => {});
      }
    };

    // Clean player pause handler
    const handleCleanPause = () => {
      setIsPlaying(false);
      if (blurredPlayerRef.current) {
        blurredPlayerRef.current.pause();
      }
    };

    // Blurred player readiness handler
    const handleAlternateCanPlay = () => {
      setIsAlternateReady(true);
    };

    // Blurred player seek-completion handler (SEEK RACE FIX)
    const handleAlternateSeeked = () => {
      const token = activeSeekTokenRef.current;
      if (!isSeekingAlternateRef.current) return;
      if (token !== transitionTokenRef.current) return;
      if (
        propsRef.current.simulateAlternateFailure ||
        alternateLoadErrorRef.current
      ) {
        return;
      }

      const currentT = cleanPlayer.currentTime;
      const { blurStart: bStart, blurEnd: bEnd } = propsRef.current;

      // Clear seeking state and timeout
      isSeekingAlternateRef.current = false;
      if (seekTimeoutRef.current) {
        clearTimeout(seekTimeoutRef.current);
        seekTimeoutRef.current = null;
      }

      // Verify playback position is still inside blur interval
      if (currentT < bStart || currentT >= bEnd) {
        return;
      }

      // Asynchronous seek finished and frame is ready: transfer surface
      attachSurface('blurred');

      if (!cleanPlayer.paused) {
        blurredPlayer.play().catch(() => {});
      }

      currentVisualModeRef.current = 'blurred';
      setVisualMode('blurred');
      propsRef.current.onVisualModeChange?.('blurred', currentT);
    };

    // Blurred player error handler
    const handleAlternateError = () => {
      alternateLoadErrorRef.current = true;
      setAlternateLoadError(true);
      if (seekTimeoutRef.current) {
        clearTimeout(seekTimeoutRef.current);
        seekTimeoutRef.current = null;
      }
      isSeekingAlternateRef.current = false;

      const currentT = cleanPlayer.currentTime;
      const { blurStart: bStart, blurEnd: bEnd } = propsRef.current;
      if (currentT >= bStart && currentT < bEnd) {
        attachSurface('none');
        currentVisualModeRef.current = 'blackout';
        setVisualMode('blackout');
        propsRef.current.onVisualModeChange?.('blackout', currentT);
      }
    };

    cleanPlayer.addEventListener('timeupdate', handleCleanTimeUpdate);
    cleanPlayer.addEventListener('playing', handleCleanPlaying);
    cleanPlayer.addEventListener('pause', handleCleanPause);
    blurredPlayer.addEventListener('canplay', handleAlternateCanPlay);
    blurredPlayer.addEventListener('seeked', handleAlternateSeeked);
    blurredPlayer.addEventListener('error', handleAlternateError);

    Promise.all([cleanPlayer.initialize(), blurredPlayer.initialize()]).then(
      () => {
        if (isCancelled) return;

        // Configure clean player (Audio Master & Playback Clock)
        cleanPlayer.muted = false;
        cleanPlayer.volume = 1.0;
        cleanPlayer.src = cleanSrc;

        // Configure blurred player (Strictly Muted Video Alternate)
        blurredPlayer.muted = true;
        blurredPlayer.volume = 0.0;

        if (simulateAlternateFailure) {
          alternateLoadErrorRef.current = true;
          setAlternateLoadError(true);
        } else {
          blurredPlayer.src = blurredSrc;
        }

        // If surface handle arrived before player initialization completed
        if (surfaceHandleRef.current) {
          cleanPlayer.setSurfaceHandle(surfaceHandleRef.current);
        }
      },
    );

    return () => {
      isCancelled = true;
      ++transitionTokenRef.current;
      activeSeekTokenRef.current = 0;
      if (seekTimeoutRef.current) {
        clearTimeout(seekTimeoutRef.current);
        seekTimeoutRef.current = null;
      }
      isSeekingAlternateRef.current = false;

      cleanPlayer.removeEventListener('timeupdate', handleCleanTimeUpdate);
      cleanPlayer.removeEventListener('playing', handleCleanPlaying);
      cleanPlayer.removeEventListener('pause', handleCleanPause);
      blurredPlayer.removeEventListener('canplay', handleAlternateCanPlay);
      blurredPlayer.removeEventListener('seeked', handleAlternateSeeked);
      blurredPlayer.removeEventListener('error', handleAlternateError);

      if (surfaceHandleRef.current) {
        cleanPlayer.clearSurfaceHandle(surfaceHandleRef.current);
        blurredPlayer.clearSurfaceHandle(surfaceHandleRef.current);
      }
      cleanPlayer.deinitialize().catch(() => {});
      blurredPlayer.deinitialize().catch(() => {});
    };
  }, [
    cleanSrc,
    blurredSrc,
    simulateAlternateFailure,
    initiateAlternateSeek,
    exitToClean,
    attachSurface,
  ]);

  // Surface creation / destruction callbacks
  const handleSurfaceCreated = (handle: string) => {
    surfaceHandleRef.current = handle;
    setSurfaceHandle(handle);
    if (currentVisualModeRef.current === 'blurred') {
      blurredPlayerRef.current?.setSurfaceHandle(handle);
    } else if (currentVisualModeRef.current === 'clean') {
      cleanPlayerRef.current?.setSurfaceHandle(handle);
    }
  };

  const handleSurfaceDestroyed = (handle: string) => {
    if (surfaceHandleRef.current === handle) {
      cleanPlayerRef.current?.clearSurfaceHandle(handle);
      blurredPlayerRef.current?.clearSurfaceHandle(handle);
      surfaceHandleRef.current = null;
      setSurfaceHandle(null);
    }
  };

  // Playback control helpers
  const play = () => {
    cleanPlayerRef.current?.play().catch(() => {});
    if (currentVisualModeRef.current === 'blurred') {
      blurredPlayerRef.current?.play().catch(() => {});
    }
  };

  const pause = () => {
    cleanPlayerRef.current?.pause();
    blurredPlayerRef.current?.pause();
  };

  const seek = (targetTime: number) => {
    setCurrentTime(targetTime);

    if (cleanPlayerRef.current) {
      try {
        cleanPlayerRef.current.currentTime = targetTime;
      } catch (err) {
        console.warn('[DualVideoPrototype] Failed to assign currentTime to clean player:', err);
      }
    }

    const { blurStart: bStart, blurEnd: bEnd } = propsRef.current;
    const isInsideCue = targetTime >= bStart && targetTime < bEnd;

    if (isInsideCue) {
      initiateAlternateSeek(targetTime);
    } else {
      exitToClean(targetTime);
    }
  };

  return (
    <View style={styles.container} testID="dual-video-prototype-container">
      <Text style={styles.title}>Dual-Video Switching Prototype</Text>
      <Text style={styles.subtitle} testID="prototype-subtitle">
        Offline Experiment: Dual-Player Surface Handover (Unverified on Native Vega HW)
      </Text>
      <Text style={styles.status} testID="prototype-status">
        Time: {currentTime.toFixed(2)}s | Mode: {visualMode} | Playing:{' '}
        {isPlaying ? 'YES' : 'NO'}
      </Text>

      <View style={styles.surfaceContainer} testID="surface-container">
        <KeplerShareableSurfaceView
          style={styles.surface}
          scalingmode="fit"
          onSurfaceViewCreated={handleSurfaceCreated}
          onSurfaceViewDestroyed={handleSurfaceDestroyed}
        />

        {/* Blackout Fallback / Handover Overlay */}
        {visualMode === 'blackout' && (
          <View
            style={styles.blackoutOverlay}
            testID="prototype-blackout-overlay">
            <Text style={styles.blackoutIcon}>🛡️</Text>
            <Text style={styles.blackoutText} testID="prototype-blackout-text">
              {simulateAlternateFailure || alternateLoadError
                ? 'Blackout Fallback Active (Asset Unavailable)'
                : 'Shielding Transition...'}
            </Text>
          </View>
        )}
      </View>

      <View style={styles.controlsRow}>
        <TouchableOpacity
          testID="proto-btn-play"
          style={styles.btn}
          onPress={play}>
          <Text style={styles.btnText}>Play</Text>
        </TouchableOpacity>
        <TouchableOpacity
          testID="proto-btn-pause"
          style={styles.btn}
          onPress={pause}>
          <Text style={styles.btnText}>Pause</Text>
        </TouchableOpacity>
        <TouchableOpacity
          testID="proto-btn-seek-in"
          style={styles.btn}
          onPress={() => seek(4.5)}>
          <Text style={styles.btnText}>Seek In (4.5s)</Text>
        </TouchableOpacity>
        <TouchableOpacity
          testID="proto-btn-seek-out"
          style={styles.btn}
          onPress={() => seek(1.0)}>
          <Text style={styles.btnText}>Seek Out (1.0s)</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    padding: 24,
    backgroundColor: '#0F172A',
    borderRadius: 12,
  },
  title: {
    color: '#F8FAFC',
    fontSize: 20,
    fontWeight: '700',
    marginBottom: 4,
  },
  subtitle: {
    color: '#94A3B8',
    fontSize: 13,
    marginBottom: 12,
  },
  status: {
    color: '#38BDF8',
    fontSize: 14,
    marginBottom: 16,
    fontFamily: 'monospace',
  },
  surfaceContainer: {
    width: 640,
    height: 360,
    backgroundColor: '#000000',
    position: 'relative',
    overflow: 'hidden',
    borderRadius: 8,
  },
  surface: {
    width: '100%',
    height: '100%',
  },
  blackoutOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#000000',
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 10,
  },
  blackoutIcon: {
    fontSize: 32,
    marginBottom: 8,
  },
  blackoutText: {
    color: '#EF4444',
    fontSize: 14,
    fontWeight: '600',
  },
  controlsRow: {
    flexDirection: 'row',
    marginTop: 16,
    gap: 12,
  },
  btn: {
    paddingVertical: 8,
    paddingHorizontal: 16,
    backgroundColor: '#1E293B',
    borderColor: '#334155',
    borderWidth: 1,
    borderRadius: 6,
  },
  btnText: {
    color: '#F1F5F9',
    fontSize: 13,
    fontWeight: '600',
  },
});
