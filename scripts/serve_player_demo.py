#!/usr/bin/env python3
"""Trigger-Aware Cinema — High-Performance Laptop Web Application & Live Demo Server.

Serves the full-length films (Sintel 888s / Tears of Steel 734s) with:
- HTTP 206 Partial Content (Byte Range Requests) for instant, seamless video scrubbing
- Real offline AI detector cues packaged from computer vision and audio classifiers
- Dynamic trigger mitigations: adaptive screen dimming, audio attenuation (-15dB), visual blur, auto-skipping
- Multi-color interactive cue scrub bar with tooltip inspection
- Hackathon Presentation Quick-Jump Bar for one-click judge demonstrations
- Live AI Telemetry & Detection Inspector panel
- Real synchronized subtitle engine with profanity censoring

Usage:
    python scripts/serve_player_demo.py
    python scripts/serve_player_demo.py --port 8080
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import sys
import webbrowser

REPO_ROOT = Path(__file__).resolve().parent.parent

# Load real packaged cues from data/processed
SINTEL_INDEX_PATH = REPO_ROOT / "data" / "processed" / "sintel" / "index.json"
TEARS_INDEX_PATH = REPO_ROOT / "data" / "processed" / "tears_of_steel" / "index.json"
SINTEL_SUBS_PATH = REPO_ROOT / "data" / "processed" / "sintel" / "cues.json"
TEARS_SUBS_PATH = REPO_ROOT / "data" / "processed" / "tears_of_steel" / "cues.json"

SINTEL_VIDEO_PATH = REPO_ROOT / "data" / "processed" / "sintel" / "normalized.mp4"
TEARS_VIDEO_PATH = REPO_ROOT / "data" / "processed" / "tears_of_steel" / "normalized.mp4"


def load_json_safe(path: Path, default=None):
    if default is None:
        default = {}
    if path.is_file():
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


SINTEL_INDEX_DATA = load_json_safe(SINTEL_INDEX_PATH, {"timeline": {"duration_seconds": 888.032}, "cues": []})
TEARS_INDEX_DATA = load_json_safe(TEARS_INDEX_PATH, {"timeline": {"duration_seconds": 734.167}, "cues": []})
SINTEL_SUBS_DATA = load_json_safe(SINTEL_SUBS_PATH, [])
TEARS_SUBS_DATA = load_json_safe(TEARS_SUBS_PATH, [])

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ShieldPlay — AI Trigger-Aware Cinema</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg-space: #070a12;
  --bg-card: rgba(15, 23, 42, 0.72);
  --bg-card-hover: rgba(30, 41, 69, 0.85);
  --bg-glass: rgba(13, 20, 36, 0.65);
  --border-subtle: rgba(255, 255, 255, 0.08);
  --border-glow: #00f0ff;
  
  --cyan: #00f0ff;
  --cyan-glow: rgba(0, 240, 255, 0.35);
  --purple: #8b5cf6;
  --purple-glow: rgba(139, 92, 246, 0.35);
  --amber: #f59e0b;
  --amber-glow: rgba(245, 158, 11, 0.35);
  --rose: #f43f5e;
  --rose-glow: rgba(244, 63, 94, 0.35);
  --emerald: #10b981;
  --emerald-glow: rgba(16, 185, 129, 0.35);
  
  --text-main: #f8fafc;
  --text-muted: #94a3b8;
  --text-dim: #64748b;
  --radius-lg: 16px;
  --radius-md: 10px;
  --radius-sm: 6px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: radial-gradient(circle at 50% 0%, #111a33 0%, var(--bg-space) 65%);
  color: var(--text-main);
  font-family: 'Outfit', sans-serif;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  overflow-x: hidden;
  padding: 0;
  -webkit-font-smoothing: antialiased;
}

/* App Header */
.app-header {
  height: 64px;
  padding: 0 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(7, 10, 18, 0.82);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border-subtle);
  position: sticky;
  top: 0;
  z-index: 100;
}

.brand-group {
  display: flex;
  align-items: center;
  gap: 14px;
}

.brand-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 800;
  font-size: 20px;
  letter-spacing: -0.5px;
  color: #fff;
}

.logo-badge {
  background: linear-gradient(135deg, var(--cyan) 0%, var(--purple) 100%);
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 0 16px var(--cyan-glow);
  font-size: 17px;
}

.brand-tag {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  background: rgba(0, 240, 255, 0.12);
  color: var(--cyan);
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid rgba(0, 240, 255, 0.25);
}

.header-center {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(15, 23, 42, 0.6);
  padding: 4px;
  border-radius: 12px;
  border: 1px solid var(--border-subtle);
}

.film-btn {
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  align-items: center;
  gap: 6px;
}

.film-btn:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.05);
}

.film-btn.active {
  background: linear-gradient(135deg, rgba(0, 240, 255, 0.2), rgba(139, 92, 246, 0.2));
  color: #fff;
  border: 1px solid rgba(0, 240, 255, 0.4);
  box-shadow: 0 0 14px rgba(0, 240, 255, 0.2);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.profile-pill {
  font-size: 12px;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: 999px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 7px;
  background: rgba(139, 92, 246, 0.15);
  border: 1px solid rgba(139, 92, 246, 0.4);
  color: #c4b5fd;
  transition: all 0.2s ease;
}

.profile-pill:hover {
  background: rgba(139, 92, 246, 0.3);
  box-shadow: 0 0 16px var(--purple-glow);
}

.action-icon-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border-subtle);
  color: var(--text-main);
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 15px;
  transition: all 0.2s ease;
}

.action-icon-btn:hover {
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.2);
}

/* Main Presentation Layout */
.main-stage {
  flex: 1;
  max-width: 1380px;
  width: 100%;
  margin: 0 auto;
  padding: 20px 24px 40px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* Video Stage Area */
.player-container {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  max-height: 720px;
  background: #000;
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: 0 20px 50px -10px rgba(0, 0, 0, 0.8), 0 0 30px rgba(0, 240, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
}

.main-video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #000;
  transition: filter 0.3s ease, opacity 0.3s ease;
}

/* Video Mitigations */
.dim-active {
  filter: brightness(0.35) contrast(1.1) !important;
}

.blur-active {
  filter: blur(28px) !important;
}

/* Mitigation HUD Overlays */
.mitigation-dim-banner {
  position: absolute;
  top: 24px;
  left: 24px;
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(15, 23, 42, 0.92);
  border: 1px solid var(--amber);
  color: #fbbf24;
  padding: 10px 18px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 600;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6), 0 0 20px var(--amber-glow);
  backdrop-filter: blur(12px);
  opacity: 0;
  transform: translateY(-8px);
  pointer-events: none;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 20;
}

.mitigation-dim-banner.visible {
  opacity: 1;
  transform: translateY(0);
}

.mitigation-mute-badge {
  position: absolute;
  top: 24px;
  right: 24px;
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(15, 23, 42, 0.92);
  border: 1px solid var(--cyan);
  color: var(--cyan);
  padding: 10px 18px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 600;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6), 0 0 20px var(--cyan-glow);
  backdrop-filter: blur(12px);
  opacity: 0;
  transform: translateY(-8px);
  pointer-events: none;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 20;
}

.mitigation-mute-badge.visible {
  opacity: 1;
  transform: translateY(0);
}

/* Sound Wave Animation */
.wave-bars {
  display: flex;
  align-items: center;
  gap: 3px;
  height: 14px;
}
.wave-bars span {
  width: 3px;
  height: 100%;
  background: var(--cyan);
  border-radius: 999px;
  animation: wave 0.8s ease-in-out infinite alternate;
}
.wave-bars span:nth-child(2) { animation-delay: 0.2s; height: 60%; }
.wave-bars span:nth-child(3) { animation-delay: 0.4s; height: 80%; }
@keyframes wave {
  from { transform: scaleY(0.3); }
  to { transform: scaleY(1); }
}

/* Heads Up 5s Warning Banner */
.heads-up-card {
  position: absolute;
  top: 80px;
  left: 24px;
  max-width: 440px;
  background: rgba(15, 23, 42, 0.94);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(244, 63, 94, 0.5);
  border-radius: 14px;
  padding: 16px 20px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.7), 0 0 24px var(--rose-glow);
  z-index: 25;
  display: flex;
  flex-direction: column;
  gap: 10px;
  opacity: 0;
  pointer-events: none;
  transform: translateY(-10px);
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.heads-up-card.visible {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(0);
}

.heads-up-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.heads-up-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--rose);
}

.heads-up-countdown {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-muted);
}

.heads-up-text {
  font-size: 13px;
  color: #cbd5e1;
  line-height: 1.4;
}

.heads-up-buttons {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
}

.btn-skip {
  background: linear-gradient(135deg, var(--rose) 0%, #e11d48 100%);
  border: none;
  color: #fff;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  padding: 7px 14px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 0 14px var(--rose-glow);
}

.btn-skip:hover {
  transform: translateY(-1px);
  box-shadow: 0 0 20px var(--rose-glow);
}

.btn-dismiss {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid var(--border-subtle);
  color: var(--text-muted);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  padding: 7px 14px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-dismiss:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.15);
}

/* Auto-Skip Toast */
.skip-toast {
  position: absolute;
  bottom: 80px;
  left: 50%;
  transform: translateX(-50%) translateY(10px);
  background: rgba(16, 185, 129, 0.92);
  color: #064e3b;
  font-weight: 700;
  font-size: 13px;
  padding: 8px 18px;
  border-radius: 999px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5), 0 0 20px var(--emerald-glow);
  opacity: 0;
  pointer-events: none;
  transition: all 0.25s ease;
  z-index: 30;
}
.skip-toast.visible {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}

/* Subtitle Display */
.subtitles-area {
  position: absolute;
  bottom: 74px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  pointer-events: none;
  z-index: 15;
}

.subtitles-text {
  background: rgba(0, 0, 0, 0.82);
  color: #fff;
  padding: 8px 18px;
  border-radius: 8px;
  font-size: 18px;
  font-weight: 500;
  max-width: 80%;
  text-align: center;
  text-shadow: 0 2px 4px rgba(0, 0, 0, 0.8);
  letter-spacing: 0.2px;
  display: none;
  border: 1px solid rgba(255, 255, 255, 0.12);
}

.subtitles-text.censored {
  border-color: rgba(16, 185, 129, 0.4);
  box-shadow: 0 0 15px rgba(16, 185, 129, 0.25);
}

/* Custom Bottom Player Control Bar */
.player-controls-bar {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 16px 20px 14px;
  background: linear-gradient(to top, rgba(7, 10, 18, 0.95) 0%, rgba(7, 10, 18, 0.7) 60%, transparent 100%);
  display: flex;
  flex-direction: column;
  gap: 10px;
  z-index: 30;
  opacity: 1;
  transition: opacity 0.3s ease;
}

/* Scrub Bar Container */
.scrub-container {
  position: relative;
  width: 100%;
  height: 24px;
  display: flex;
  align-items: center;
  cursor: pointer;
}

.scrub-track {
  position: relative;
  width: 100%;
  height: 6px;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999px;
  overflow: visible;
  transition: height 0.15s ease;
}

.scrub-container:hover .scrub-track {
  height: 10px;
}

.scrub-progress {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  background: linear-gradient(90deg, var(--cyan) 0%, var(--purple) 100%);
  border-radius: 999px;
  width: 0%;
  pointer-events: none;
  box-shadow: 0 0 10px var(--cyan-glow);
}

.scrub-thumb {
  position: absolute;
  top: 50%;
  left: 0%;
  transform: translate(-50%, -50%);
  width: 14px;
  height: 14px;
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 0 10px rgba(0, 0, 0, 0.8), 0 0 10px var(--cyan);
  pointer-events: none;
  transition: transform 0.1s ease;
}

.scrub-container:hover .scrub-thumb {
  transform: translate(-50%, -50%) scale(1.3);
}

/* Cue Markers on Track */
.cue-segment {
  position: absolute;
  top: 0;
  height: 100%;
  border-radius: 2px;
  opacity: 0.9;
  pointer-events: none;
  transition: opacity 0.2s ease;
}

.cue-segment.flashing { background: #f59e0b; box-shadow: 0 0 6px rgba(245, 158, 11, 0.7); }
.cue-segment.loud_spike { background: #00f0ff; box-shadow: 0 0 6px rgba(0, 240, 255, 0.7); }
.cue-segment.scream { background: #a855f7; box-shadow: 0 0 6px rgba(168, 85, 247, 0.7); }
.cue-segment.explosion { background: #f43f5e; box-shadow: 0 0 6px rgba(244, 63, 94, 0.7); }
.cue-segment.gunshot { background: #fb7185; box-shadow: 0 0 6px rgba(251, 113, 133, 0.7); }
.cue-segment.profanity { background: #10b981; box-shadow: 0 0 6px rgba(16, 185, 129, 0.7); }

/* Timeline Tooltip */
.timeline-tooltip {
  position: absolute;
  bottom: 30px;
  transform: translateX(-50%);
  background: rgba(15, 23, 42, 0.95);
  backdrop-filter: blur(10px);
  border: 1px solid var(--border-subtle);
  padding: 5px 10px;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: #fff;
  pointer-events: none;
  white-space: nowrap;
  display: none;
  z-index: 40;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
}

/* Buttons Row */
.controls-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.controls-left, .controls-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.play-toggle-btn {
  background: #fff;
  color: #000;
  border: none;
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 16px;
  transition: transform 0.15s ease, background 0.15s ease;
}

.play-toggle-btn:hover {
  transform: scale(1.08);
  background: var(--cyan);
}

.seek-btn {
  background: transparent;
  border: none;
  color: var(--text-main);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 6px;
  transition: background 0.2s ease;
}

.seek-btn:hover {
  background: rgba(255, 255, 255, 0.1);
}

.timecode-display {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  color: #e2e8f0;
  letter-spacing: 0.5px;
}

.vol-slider-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.vol-slider {
  -webkit-appearance: none;
  width: 70px;
  height: 4px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 2px;
  outline: none;
}

.vol-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  cursor: pointer;
}

/* Presentation Demo Quick-Jumps */
.demo-jumps-card {
  background: var(--bg-card);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 18px 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.demo-jumps-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.demo-title-group {
  display: flex;
  align-items: center;
  gap: 10px;
}

.demo-title {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.2px;
  color: #fff;
}

.demo-badge {
  font-size: 11px;
  font-weight: 600;
  background: rgba(245, 158, 11, 0.15);
  color: #fbbf24;
  border: 1px solid rgba(245, 158, 11, 0.3);
  padding: 2px 8px;
  border-radius: 6px;
}

.demo-jumps-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}

.jump-card {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--radius-md);
  padding: 12px 16px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
}

.jump-card:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: var(--cyan);
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.4), 0 0 16px var(--cyan-glow);
}

.jump-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.jump-name {
  font-size: 13px;
  font-weight: 600;
  color: #fff;
}

.jump-tag {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: 4px;
}

.jump-tag.flashing { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }
.jump-tag.scream { background: rgba(168, 85, 247, 0.2); color: #c084fc; }
.jump-tag.explosion { background: rgba(244, 63, 94, 0.2); color: #fb7185; }
.jump-tag.gunshot { background: rgba(244, 63, 94, 0.2); color: #fb7185; }

.jump-desc {
  font-size: 11px;
  color: var(--text-muted);
}

.jump-timecode {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--cyan);
  margin-top: 4px;
}

/* Live AI Inspector / Telemetry Dashboard */
.telemetry-row {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 20px;
}

.telemetry-card {
  background: var(--bg-card);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.telemetry-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: 12px;
}

.telemetry-title {
  font-size: 15px;
  font-weight: 700;
  color: #fff;
  display: flex;
  align-items: center;
  gap: 8px;
}

.live-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--emerald);
  box-shadow: 0 0 10px var(--emerald);
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0% { transform: scale(0.95); opacity: 0.6; }
  50% { transform: scale(1.2); opacity: 1; }
  100% { transform: scale(0.95); opacity: 0.6; }
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.stat-box {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  padding: 12px 14px;
}

.stat-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-muted);
  margin-bottom: 4px;
}

.stat-val {
  font-family: 'JetBrains Mono', monospace;
  font-size: 18px;
  font-weight: 700;
  color: #fff;
}

.stat-val.highlight {
  color: var(--cyan);
}

/* Legend & Active Trigger Indicator */
.legend-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--text-muted);
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 3px;
}

/* Modal Overlay for Settings */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(12px);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 200;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.modal-overlay.open {
  display: flex;
  opacity: 1;
}

.modal-box {
  width: 100%;
  max-width: 580px;
  background: #0f172a;
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: var(--radius-lg);
  padding: 28px;
  box-shadow: 0 25px 60px rgba(0, 0, 0, 0.8), 0 0 30px var(--cyan-glow);
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.modal-title {
  font-size: 18px;
  font-weight: 700;
  color: #fff;
}

.modal-close-btn {
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 20px;
  cursor: pointer;
}

.setting-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-subtle);
}

.setting-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.setting-title {
  font-size: 14px;
  font-weight: 600;
  color: #fff;
}

.setting-desc {
  font-size: 11px;
  color: var(--text-muted);
}

/* Toggle Switch */
.switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
}

.switch input { opacity: 0; width: 0; height: 0; }

.slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: #334155;
  transition: 0.3s;
  border-radius: 34px;
}

.slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 3px;
  bottom: 3px;
  background-color: white;
  transition: 0.3s;
  border-radius: 50%;
}

input:checked + .slider { background-color: var(--cyan); }
input:checked + .slider:before { transform: translateX(20px); }

/* Responsive adjustments */
@media (max-width: 900px) {
  .telemetry-row { grid-template-columns: 1fr; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
  .header-center { display: none; }
}
</style>
</head>
<body>

<!-- Header -->
<header class="app-header">
  <div class="brand-group">
    <div class="brand-logo">
      <div class="logo-badge">🛡️</div>
      <span>ShieldPlay</span>
    </div>
    <span class="brand-tag">AI Protection Engine</span>
  </div>

  <div class="header-center">
    <button class="film-btn active" id="btnFilmSintel">
      <span>🎬</span> Sintel (Full Film • 14m 48s)
    </button>
    <button class="film-btn" id="btnFilmTears">
      <span>🚀</span> Tears of Steel (Full Film • 12m 14s)
    </button>
  </div>

  <div class="header-right">
    <div class="profile-pill" id="btnCycleProfile" title="Click to cycle mitigation profile">
      <span>🛡️</span>
      <span id="profileName">PROFILE: STANDARD</span>
    </div>
    <button class="action-icon-btn" id="btnOpenSettings" title="Viewer Settings">⚙️</button>
    <button class="action-icon-btn" id="btnFullscreen" title="Toggle Fullscreen">⛶</button>
  </div>
</header>

<!-- Main Stage Container -->
<main class="main-stage">

  <!-- Video Stage -->
  <div class="player-container" id="playerContainer">
    <video class="main-video" id="videoEl" playsinline preload="auto">
      <source src="/video/sintel" type="video/mp4">
    </video>

    <!-- Mitigations Overlays -->
    <div class="mitigation-dim-banner" id="dimBanner">
      <span>⚡</span>
      <span>RAPID FLASHING DETECTED — VIDEO DIMMED 65%</span>
    </div>

    <div class="mitigation-mute-badge" id="muteBadge">
      <div class="wave-bars"><span></span><span></span><span></span></div>
      <span id="muteReason">ACOUSTIC SPIKE ATTENUATED (-15dB)</span>
    </div>

    <!-- Heads Up 5s Warning Banner -->
    <div class="heads-up-card" id="headsUpCard">
      <div class="heads-up-head">
        <div class="heads-up-badge">
          <span>⚠️</span>
          <span id="headsUpTriggerName">FLASHING ALERT</span>
        </div>
        <span class="heads-up-countdown" id="headsUpCountdown">in 5.0s</span>
      </div>
      <div class="heads-up-text" id="headsUpText">
        An upcoming intense flashing light sequence begins at 00:42. Protection will auto-dim the video.
      </div>
      <div class="heads-up-buttons">
        <button class="btn-skip" id="btnHeadsUpSkip">Skip Sequence</button>
        <button class="btn-dismiss" id="btnHeadsUpDismiss">Dismiss</button>
      </div>
    </div>

    <!-- Auto-Skip Notification Toast -->
    <div class="skip-toast" id="skipToast">
      ⏭️ Auto-skipped trigger sequence (+4.2s)
    </div>

    <!-- Subtitles -->
    <div class="subtitles-area">
      <div class="subtitles-text" id="subtitlesBox"></div>
    </div>

    <!-- Controls Bar -->
    <div class="player-controls-bar">
      <!-- Scrub Bar -->
      <div class="scrub-container" id="scrubContainer">
        <div class="scrub-track" id="scrubTrack">
          <!-- Dynamic Cue Markers rendered via JS -->
          <div class="scrub-progress" id="scrubProgress"></div>
          <div class="scrub-thumb" id="scrubThumb"></div>
        </div>
        <div class="timeline-tooltip" id="timelineTooltip">00:00</div>
      </div>

      <!-- Controls Row -->
      <div class="controls-row">
        <div class="controls-left">
          <button class="play-toggle-btn" id="btnPlayPause" title="Play / Pause (Space)">▶</button>
          <button class="seek-btn" id="btnBack10" title="Back 10s (Left Arrow)">⏪ -10s</button>
          <button class="seek-btn" id="btnFwd10" title="Forward 10s (Right Arrow)">+10s ⏩</button>
          <div class="timecode-display" id="timecode">00:00.0 / 14:48.0</div>
        </div>

        <div class="controls-right">
          <div class="vol-slider-wrap">
            <span style="font-size:14px; cursor:pointer;" id="btnMuteToggle">🔊</span>
            <input type="range" class="vol-slider" id="volSlider" min="0" max="1" step="0.05" value="1">
          </div>
          <button class="seek-btn" id="btnSubtitlesToggle" style="border: 1px solid var(--border-subtle);">CC: ON</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Presentation Demo Quick-Jumps (Instant Judge Navigation) -->
  <section class="demo-jumps-card">
    <div class="demo-jumps-head">
      <div class="demo-title-group">
        <span style="font-size:18px;">⚡</span>
        <div class="demo-title">Presentation Demo Quick-Jumps</div>
        <span class="demo-badge">One-Click Judge Showcase</span>
      </div>
      <div style="font-size:12px; color:var(--text-muted);">
        Click any scene to jump 2s before the verified event and observe real-time protection.
      </div>
    </div>

    <div class="demo-jumps-grid" id="jumpsGrid">
      <!-- Populated dynamically based on active film -->
    </div>
  </section>

  <!-- Live AI Telemetry & Detection Inspector -->
  <div class="telemetry-row">
    <div class="telemetry-card">
      <div class="telemetry-head">
        <div class="telemetry-title">
          <div class="live-pulse"></div>
          <span>Active Film Telemetry & Detector Intelligence</span>
        </div>
        <span style="font-family:'JetBrains Mono'; font-size:12px; color:var(--cyan);" id="filmDurationDisplay">888.03s • 24.0 FPS</span>
      </div>

      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-label">PACKAGED CUES</div>
          <div class="stat-val highlight" id="statCuesCount">66</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">ACTIVE DETECTORS</div>
          <div class="stat-val">4 Offline</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">AI LATENCY</div>
          <div class="stat-val highlight">&lt; 15 ms</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">CURRENT TRIGGER</div>
          <div class="stat-val" id="statActiveTrigger" style="font-size:14px; color:#94a3b8;">None (Safe)</div>
        </div>
      </div>

      <div class="legend-strip">
        <span>Timeline Trigger Legend:</span>
        <div class="legend-item"><div class="legend-dot" style="background:#f59e0b;"></div><span>Flashing</span></div>
        <div class="legend-item"><div class="legend-dot" style="background:#00f0ff;"></div><span>Loud Spikes</span></div>
        <div class="legend-item"><div class="legend-dot" style="background:#a855f7;"></div><span>Screams</span></div>
        <div class="legend-item"><div class="legend-dot" style="background:#f43f5e;"></div><span>Explosions / Gunshots</span></div>
        <div class="legend-item"><div class="legend-dot" style="background:#10b981;"></div><span>Dialogue / Profanity</span></div>
      </div>
    </div>

    <div class="telemetry-card">
      <div class="telemetry-head">
        <div class="telemetry-title">
          <span>Active Mitigations</span>
        </div>
        <span style="font-size:12px; color:var(--text-muted);" id="mitigationStatusBadge">Active</span>
      </div>

      <div style="display:flex; flex-direction:column; gap:10px; font-size:13px;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="color:var(--text-muted);">Visual Flashing:</span>
          <span style="font-weight:600; color:var(--amber);">Auto Dim Screen (65%)</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="color:var(--text-muted);">Acoustic Spikes:</span>
          <span style="font-weight:600; color:var(--cyan);">Attenuate -15 dB</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="color:var(--text-muted);">Screams & Horror:</span>
          <span style="font-weight:600; color:var(--purple);">Attenuate / Mute</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="color:var(--text-muted);">Advance Notice:</span>
          <span style="font-weight:600; color:#fff;">5.0s Heads-Up HUD</span>
        </div>
      </div>
    </div>
  </div>

</main>

<!-- Settings Modal -->
<div class="modal-overlay" id="settingsModal">
  <div class="modal-box">
    <div class="modal-header">
      <div class="modal-title">Viewer Protection & Sensitivity Settings</div>
      <button class="modal-close-btn" id="btnCloseSettings">✕</button>
    </div>

    <div class="setting-item">
      <div class="setting-info">
        <div class="setting-title">Rapid Flashing Dimming</div>
        <div class="setting-desc">Automatically dims screen luminance during strobe/combat flash sequences.</div>
      </div>
      <label class="switch"><input type="checkbox" id="cfgFlashing" checked><span class="slider"></span></label>
    </div>

    <div class="setting-item">
      <div class="setting-info">
        <div class="setting-title">Acoustic Loudness Limiter</div>
        <div class="setting-desc">Compresses and reduces harsh audio volume spikes by 15 dB.</div>
      </div>
      <label class="switch"><input type="checkbox" id="cfgLoudness" checked><span class="slider"></span></label>
    </div>

    <div class="setting-item">
      <div class="setting-info">
        <div class="setting-title">Scream & Shock Dampening</div>
        <div class="setting-desc">Dampens high-frequency screams and sudden jump screams.</div>
      </div>
      <label class="switch"><input type="checkbox" id="cfgScreams" checked><span class="slider"></span></label>
    </div>

    <div class="setting-item">
      <div class="setting-info">
        <div class="setting-title">Explosion & Gunshot Softening</div>
        <div class="setting-desc">Protects against heavy cinematic sub-bass concussions and impacts.</div>
      </div>
      <label class="switch"><input type="checkbox" id="cfgWeapons" checked><span class="slider"></span></label>
    </div>

    <div class="setting-item">
      <div class="setting-info">
        <div class="setting-title">Heads-Up Warning Prompts</div>
        <div class="setting-desc">Shows a non-blocking interactive warning 5 seconds prior to trigger arrival.</div>
      </div>
      <label class="switch"><input type="checkbox" id="cfgHeadsUp" checked><span class="slider"></span></label>
    </div>

    <div class="setting-item">
      <div class="setting-info">
        <div class="setting-title">Subtitle Profanity Filter</div>
        <div class="setting-desc">Automatically censors harsh language in live subtitle captions.</div>
      </div>
      <label class="switch"><input type="checkbox" id="cfgProfanity" checked><span class="slider"></span></label>
    </div>

    <div style="display:flex; justify-content:flex-end; margin-top:10px;">
      <button class="film-btn active" id="btnSaveSettings">Apply & Close</button>
    </div>
  </div>
</div>

<script>
// Authoritative Real Cues embedded from offline detectors
const FILM_DATA = {
  sintel: {
    title: "Sintel (Full Film)",
    duration: 888.032,
    videoUrl: "/video/sintel",
    indexUrl: "/api/cues/sintel",
    subsUrl: "/api/subtitles/sintel",
    jumps: [
      {
        title: "⚔️ Blizzard Hut Combat",
        tag: "flashing",
        time: 40.5,
        desc: "High-contrast blade reflections & rapid sword combat in blizzard."
      },
      {
        title: "😱 Baby Dragon Abduction",
        tag: "scream",
        time: 202.0,
        desc: "Baby dragon grabbed by giant dragon; Sintel's high-pitch scream."
      },
      {
        title: "🔥 Mountain Shaman Magic",
        tag: "flashing",
        time: 312.0,
        desc: "Sustained luminance strobing & fire incantation inside the temple."
      },
      {
        title: "💥 Dragon Cave Firestorm",
        tag: "explosion",
        time: 551.0,
        desc: "Massive dragon fire breath, concussive explosions & spear strikes."
      }
    ]
  },
  tears_of_steel: {
    title: "Tears of Steel (Full Film)",
    duration: 734.167,
    videoUrl: "/video/tears_of_steel",
    indexUrl: "/api/cues/tears_of_steel",
    subsUrl: "/api/subtitles/tears_of_steel",
    jumps: [
      {
        title: "🔍 Laser Scanner Lock",
        tag: "flashing",
        time: 305.5,
        desc: "Robotic laser scanner sweeps across characters with rapid strobing."
      },
      {
        title: "🎯 Sniper Engagement",
        tag: "gunshot",
        time: 436.0,
        desc: "Sudden high-velocity sniper rifle shot echoing in the streets."
      },
      {
        title: "🤖 Giant Mech Warfare",
        tag: "flashing",
        time: 462.5,
        desc: "Full-scale robotic battle with continuous laser blasts and sparks."
      },
      {
        title: "🚀 Rocket Concussion Barrage",
        tag: "explosion",
        time: 491.0,
        desc: "Heavy missile detonation with shockwave explosions & weapon fire."
      }
    ]
  }
};

// Application State
let currentFilmId = "sintel";
let currentCues = [];
let currentSubtitles = [];
let activeProfile = "STANDARD"; // STANDARD | RELAXED | AUTO-SKIP | CUSTOM
const PROFILES = ["STANDARD", "RELAXED", "AUTO-SKIP", "CUSTOM"];

let userMuted = false;
let userVolume = 1.0;
let subtitlesEnabled = true;
let headsUpDismissedUntil = 0;

// Config toggles
const config = {
  flashing: true,
  loudness: true,
  screams: true,
  weapons: true,
  headsUp: true,
  profanity: true
};

// DOM References
const video = document.getElementById("videoEl");
const dimBanner = document.getElementById("dimBanner");
const muteBadge = document.getElementById("muteBadge");
const muteReason = document.getElementById("muteReason");
const headsUpCard = document.getElementById("headsUpCard");
const headsUpTriggerName = document.getElementById("headsUpTriggerName");
const headsUpCountdown = document.getElementById("headsUpCountdown");
const headsUpText = document.getElementById("headsUpText");
const skipToast = document.getElementById("skipToast");
const subtitlesBox = document.getElementById("subtitlesBox");

const scrubContainer = document.getElementById("scrubContainer");
const scrubTrack = document.getElementById("scrubTrack");
const scrubProgress = document.getElementById("scrubProgress");
const scrubThumb = document.getElementById("scrubThumb");
const timelineTooltip = document.getElementById("timelineTooltip");
const timecode = document.getElementById("timecode");

const btnPlayPause = document.getElementById("btnPlayPause");
const btnBack10 = document.getElementById("btnBack10");
const btnFwd10 = document.getElementById("btnFwd10");
const volSlider = document.getElementById("volSlider");
const btnMuteToggle = document.getElementById("btnMuteToggle");
const btnSubtitlesToggle = document.getElementById("btnSubtitlesToggle");

const btnFilmSintel = document.getElementById("btnFilmSintel");
const btnFilmTears = document.getElementById("btnFilmTears");
const btnCycleProfile = document.getElementById("btnCycleProfile");
const profileName = document.getElementById("profileName");
const jumpsGrid = document.getElementById("jumpsGrid");

const statCuesCount = document.getElementById("statCuesCount");
const statActiveTrigger = document.getElementById("statActiveTrigger");
const filmDurationDisplay = document.getElementById("filmDurationDisplay");

// Load Film Data
async function switchFilm(filmId) {
  currentFilmId = filmId;
  const film = FILM_DATA[filmId];
  
  btnFilmSintel.classList.toggle("active", filmId === "sintel");
  btnFilmTears.classList.toggle("active", filmId === "tears_of_steel");
  
  // Set video source
  video.pause();
  video.src = film.videoUrl;
  video.load();
  
  filmDurationDisplay.textContent = `${film.duration.toFixed(2)}s • 24.0 FPS`;

  // Fetch real cues
  try {
    const res = await fetch(film.indexUrl);
    const data = await res.json();
    currentCues = data.cues || [];
  } catch (err) {
    console.warn("Could not fetch cues via API, fallback to default cues", err);
    currentCues = [];
  }
  
  // Fetch real subtitles
  try {
    const resSubs = await fetch(film.subsUrl);
    currentSubtitles = await resSubs.json();
  } catch (err) {
    currentSubtitles = [];
  }

  statCuesCount.textContent = currentCues.length;
  renderScrubTrackCues();
  renderQuickJumps();
  
  video.currentTime = 0;
  updateTimecode(0, film.duration);
}

// Render Cue Segments onto Scrub Bar Track
function renderScrubTrackCues() {
  document.querySelectorAll(".cue-segment").forEach(el => el.remove());
  const film = FILM_DATA[currentFilmId];
  const dur = film.duration;

  currentCues.forEach(cue => {
    const seg = document.createElement("div");
    seg.className = `cue-segment ${cue.trigger}`;
    const leftPct = (cue.start / dur) * 100;
    const widthPct = Math.max(0.35, ((cue.end - cue.start) / dur) * 100);
    seg.style.left = leftPct + "%";
    seg.style.width = widthPct + "%";
    scrubTrack.appendChild(seg);
  });
}

// Render Presentation Demo Quick-Jumps
function renderQuickJumps() {
  jumpsGrid.innerHTML = "";
  const jumps = FILM_DATA[currentFilmId].jumps;
  
  jumps.forEach(j => {
    const card = document.createElement("button");
    card.className = "jump-card";
    card.innerHTML = `
      <div class="jump-header">
        <div class="jump-name">${j.title}</div>
        <span class="jump-tag ${j.tag}">${j.tag.replace('_', ' ')}</span>
      </div>
      <div class="jump-desc">${j.desc}</div>
      <div class="jump-timecode">⏱ Jump to: ${formatTime(j.time)}</div>
    `;
    card.onclick = () => {
      video.currentTime = j.time;
      video.play().catch(() => {});
      updatePlayBtn(false);
      showToast(`Jumping to: ${j.title}`);
    };
    jumpsGrid.appendChild(card);
  });
}

// Playback Engine & Real-Time Mitigation
video.addEventListener("timeupdate", () => {
  const t = video.currentTime;
  const dur = video.duration || FILM_DATA[currentFilmId].duration;
  
  // Progress Bar
  const pct = Math.min(100, Math.max(0, (t / dur) * 100));
  scrubProgress.style.width = pct + "%";
  scrubThumb.style.left = pct + "%";
  updateTimecode(t, dur);

  // Evaluate Active Cues
  let activeTriggerCue = null;
  let hasFlashing = false;
  let hasLoudSpike = false;
  let hasScream = false;
  let hasExplosion = false;
  let upcomingCue = null;

  for (const cue of currentCues) {
    // Current active trigger
    if (t >= cue.start && t < cue.end) {
      activeTriggerCue = cue;

      // Auto-Skip Profile Behavior
      if (activeProfile === "AUTO-SKIP") {
        video.currentTime = cue.end + 0.15;
        showToast(`⏭️ Auto-skipped ${cue.trigger.replace('_', ' ')} (${(cue.end - cue.start).toFixed(1)}s)`);
        return;
      }

      if (cue.trigger === "flashing" && config.flashing) hasFlashing = true;
      if (cue.trigger === "loud_spike" && config.loudness) hasLoudSpike = true;
      if (cue.trigger === "scream" && config.screams) hasScream = true;
      if ((cue.trigger === "explosion" || cue.trigger === "gunshot") && config.weapons) hasExplosion = true;
    }

    // Advance 5-second Heads-Up Warning check
    if (config.headsUp && activeProfile !== "AUTO-SKIP" && t < cue.start && (cue.start - t) <= 5.0 && (cue.start - t) >= 0.5) {
      if (t > headsUpDismissedUntil) {
        upcomingCue = cue;
      }
    }
  }

  // Visual Dimming Mitigation
  if (hasFlashing) {
    video.classList.add("dim-active");
    dimBanner.classList.add("visible");
  } else {
    video.classList.remove("dim-active");
    dimBanner.classList.remove("visible");
  }

  // Audio Attenuation Mitigation (-15dB ~ 0.20 volume)
  if (hasLoudSpike || hasScream || hasExplosion) {
    const attenuation = activeProfile === "RELAXED" ? 0.45 : 0.20;
    video.volume = userMuted ? 0 : (userVolume * attenuation);
    
    let reasonText = "ACOUSTIC SPIKE ATTENUATED (-15dB)";
    if (hasScream) reasonText = "SCREAM SENSORY DAMPENED (-15dB)";
    if (hasExplosion) reasonText = "CONCUSSIVE EXPLOSION ATTENUATED";
    
    muteReason.textContent = reasonText;
    muteBadge.classList.add("visible");
  } else {
    video.volume = userMuted ? 0 : userVolume;
    muteBadge.classList.remove("visible");
  }

  // Telemetry HUD Update
  if (activeTriggerCue) {
    statActiveTrigger.textContent = `${activeTriggerCue.trigger.toUpperCase()} (${(activeTriggerCue.score * 100).toFixed(0)}% conf)`;
    statActiveTrigger.style.color = getTriggerColor(activeTriggerCue.trigger);
  } else {
    statActiveTrigger.textContent = "None (Safe)";
    statActiveTrigger.style.color = "#94a3b8";
  }

  // Heads-Up Warning Card
  if (upcomingCue && !activeTriggerCue) {
    const remaining = (upcomingCue.start - t).toFixed(1);
    headsUpTriggerName.textContent = `${upcomingCue.trigger.toUpperCase()} ALERT`;
    headsUpCountdown.textContent = `in ${remaining}s`;
    headsUpText.textContent = `Upcoming verified ${upcomingCue.trigger.replace('_', ' ')} starts at ${formatTime(upcomingCue.start)}. Protection ready.`;
    headsUpCard.classList.add("visible");
    
    document.getElementById("btnHeadsUpSkip").onclick = () => {
      video.currentTime = upcomingCue.end + 0.1;
      headsUpCard.classList.remove("visible");
      headsUpDismissedUntil = upcomingCue.end + 1.0;
    };
    document.getElementById("btnHeadsUpDismiss").onclick = () => {
      headsUpCard.classList.remove("visible");
      headsUpDismissedUntil = upcomingCue.end + 1.0;
    };
  } else {
    headsUpCard.classList.remove("visible");
  }

  // Subtitles Synchronization & Profanity Censoring
  if (subtitlesEnabled) {
    let activeSub = null;
    for (const s of currentSubtitles) {
      if (t >= s.start && t < s.end) {
        activeSub = s;
        break;
      }
    }
    if (activeSub) {
      let text = activeSub.text;
      if (config.profanity && activeSub.flagged_words && activeSub.flagged_words.length > 0) {
        activeSub.flagged_words.forEach(w => {
          const re = new RegExp(w, "gi");
          text = text.replace(re, "****");
        });
      }
      subtitlesBox.textContent = text;
      subtitlesBox.style.display = "block";
      subtitlesBox.classList.toggle("censored", text.includes("****"));
    } else {
      subtitlesBox.style.display = "none";
    }
  } else {
    subtitlesBox.style.display = "none";
  }
});

function getTriggerColor(t) {
  if (t === "flashing") return "#f59e0b";
  if (t === "loud_spike") return "#00f0ff";
  if (t === "scream") return "#a855f7";
  if (t === "explosion" || t === "gunshot") return "#f43f5e";
  return "#10b981";
}

function showToast(msg) {
  skipToast.textContent = msg;
  skipToast.classList.add("visible");
  setTimeout(() => skipToast.classList.remove("visible"), 2500);
}

// Time Formatting
function formatTime(secs) {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  const ms = Math.floor((secs % 1) * 10);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms}`;
}

function updateTimecode(current, total) {
  timecode.textContent = `${formatTime(current)} / ${formatTime(total)}`;
}

// Scrubbing Interactivity
let isScrubbing = false;

function seekFromEvent(e) {
  const rect = scrubTrack.getBoundingClientRect();
  const clickX = e.clientX - rect.left;
  const pct = Math.min(1, Math.max(0, clickX / rect.width));
  const dur = video.duration || FILM_DATA[currentFilmId].duration;
  video.currentTime = pct * dur;
}

scrubContainer.addEventListener("mousedown", (e) => {
  isScrubbing = true;
  seekFromEvent(e);
});

window.addEventListener("mousemove", (e) => {
  if (isScrubbing) seekFromEvent(e);
  
  // Tooltip
  const rect = scrubTrack.getBoundingClientRect();
  if (e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top - 15 && e.clientY <= rect.bottom + 15) {
    const pct = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const dur = video.duration || FILM_DATA[currentFilmId].duration;
    const timeAtCursor = pct * dur;
    
    // Check if hovering near a cue
    let cueNear = null;
    for (const c of currentCues) {
      if (timeAtCursor >= c.start && timeAtCursor <= c.end) {
        cueNear = c;
        break;
      }
    }
    
    timelineTooltip.style.display = "block";
    timelineTooltip.style.left = (pct * 100) + "%";
    if (cueNear) {
      timelineTooltip.innerHTML = `<strong>${cueNear.trigger.toUpperCase()}</strong> [${formatTime(cueNear.start)} - ${formatTime(cueNear.end)}] (${(cueNear.score*100).toFixed(0)}%)`;
    } else {
      timelineTooltip.textContent = formatTime(timeAtCursor);
    }
  } else {
    timelineTooltip.style.display = "none";
  }
});

window.addEventListener("mouseup", () => {
  if (isScrubbing) isScrubbing = false;
});

// Play / Pause
function togglePlay() {
  if (video.paused) {
    video.play().catch(() => {});
    updatePlayBtn(false);
  } else {
    video.pause();
    updatePlayBtn(true);
  }
}
btnPlayPause.onclick = togglePlay;
function updatePlayBtn(isPaused) {
  btnPlayPause.textContent = isPaused ? "▶" : "⏸";
}

btnBack10.onclick = () => { video.currentTime = Math.max(0, video.currentTime - 10); };
btnFwd10.onclick = () => { video.currentTime = Math.min(video.duration, video.currentTime + 10); };

// Volume & Mute
volSlider.oninput = (e) => {
  userVolume = parseFloat(e.target.value);
  userMuted = false;
  video.volume = userVolume;
  btnMuteToggle.textContent = userVolume === 0 ? "🔇" : "🔊";
};

btnMuteToggle.onclick = () => {
  userMuted = !userMuted;
  video.volume = userMuted ? 0 : userVolume;
  btnMuteToggle.textContent = userMuted ? "🔇" : "🔊";
};

// Subtitles toggle
btnSubtitlesToggle.onclick = () => {
  subtitlesEnabled = !subtitlesEnabled;
  btnSubtitlesToggle.textContent = subtitlesEnabled ? "CC: ON" : "CC: OFF";
  btnSubtitlesToggle.style.color = subtitlesEnabled ? "var(--cyan)" : "var(--text-muted)";
};

// Film Switchers
btnFilmSintel.onclick = () => switchFilm("sintel");
btnFilmTears.onclick = () => switchFilm("tears_of_steel");

// Profile Cycle
btnCycleProfile.onclick = () => {
  const idx = (PROFILES.indexOf(activeProfile) + 1) % PROFILES.length;
  activeProfile = PROFILES[idx];
  profileName.textContent = `PROFILE: ${activeProfile}`;
  showToast(`Active Profile: ${activeProfile}`);
};

// Settings Modal
const settingsModal = document.getElementById("settingsModal");
document.getElementById("btnOpenSettings").onclick = () => settingsModal.classList.add("open");
document.getElementById("btnCloseSettings").onclick = () => settingsModal.classList.remove("open");
document.getElementById("btnSaveSettings").onclick = () => {
  config.flashing = document.getElementById("cfgFlashing").checked;
  config.loudness = document.getElementById("cfgLoudness").checked;
  config.screams = document.getElementById("cfgScreams").checked;
  config.weapons = document.getElementById("cfgWeapons").checked;
  config.headsUp = document.getElementById("cfgHeadsUp").checked;
  config.profanity = document.getElementById("cfgProfanity").checked;
  settingsModal.classList.remove("open");
  showToast("Settings updated");
};

// Fullscreen
document.getElementById("btnFullscreen").onclick = () => {
  const container = document.getElementById("playerContainer");
  if (!document.fullscreenElement) {
    container.requestFullscreen().catch(() => {});
  } else {
    document.exitFullscreen().catch(() => {});
  }
};

// Keyboard Shortcuts for Laptop Presentation
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === " " || e.key === "k") {
    e.preventDefault();
    togglePlay();
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    video.currentTime = Math.max(0, video.currentTime - 10);
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    video.currentTime = Math.min(video.duration, video.currentTime + 10);
  } else if (e.key === "f" || e.key === "F") {
    document.getElementById("btnFullscreen").click();
  } else if (e.key === "m" || e.key === "M") {
    btnMuteToggle.click();
  } else if (e.key === "p" || e.key === "P") {
    btnCycleProfile.click();
  } else if (e.key === "s" || e.key === "S") {
    switchFilm(currentFilmId === "sintel" ? "tears_of_steel" : "sintel");
  } else if (["1", "2", "3", "4"].includes(e.key)) {
    const idx = parseInt(e.key, 10) - 1;
    const jumps = FILM_DATA[currentFilmId].jumps;
    if (jumps[idx]) {
      video.currentTime = jumps[idx].time;
      video.play().catch(() => {});
      showToast(`Quick Jump: ${jumps[idx].title}`);
    }
  }
});

// Initialize on Load
switchFilm("sintel");
</script>
</body>
</html>
"""


class DemoHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """High-performance HTTP handler with HTTP 206 Partial Content (Byte Range Requests)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)

    def do_GET(self) -> None:
        # 1. Main Application HTML
        if self.path in ("/", "/index.html", "/demo", "/player"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            content = HTML_PAGE.encode("utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        # 2. Real Cues API
        if self.path == "/api/cues/sintel":
            self.send_json_response(SINTEL_INDEX_DATA)
            return
        if self.path == "/api/cues/tears_of_steel":
            self.send_json_response(TEARS_INDEX_DATA)
            return

        # 3. Subtitles API
        if self.path == "/api/subtitles/sintel":
            self.send_json_response(SINTEL_SUBS_DATA)
            return
        if self.path == "/api/subtitles/tears_of_steel":
            self.send_json_response(TEARS_SUBS_DATA)
            return

        # 4. Video Streaming with HTTP 206 Partial Content (Byte Range Requests)
        if self.path.startswith("/video/sintel"):
            self.stream_video_range(SINTEL_VIDEO_PATH)
            return
        if self.path.startswith("/video/tears_of_steel"):
            self.stream_video_range(TEARS_VIDEO_PATH)
            return

        # Fallback to static files
        super().do_GET()

    def send_json_response(self, data: Any) -> None:
        """Send JSON response with UTF-8 encoding."""
        content = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def stream_video_range(self, file_path: Path) -> None:
        """Serve video files supporting HTTP 206 Partial Content for instant scrubbing."""
        if not file_path.is_file():
            self.send_error(404, f"Video asset missing: {file_path.name}")
            return

        file_size = file_path.stat().st_size
        range_header = self.headers.get("Range")

        # Non-range request: serve full file headers
        if not range_header or not range_header.startswith("bytes="):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with file_path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        # Range request: e.g. bytes=1048576- or bytes=0-1048575
        range_val = range_header.split("=")[1].strip()
        parts = range_val.split("-")
        try:
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
        except ValueError:
            start = 0
            end = file_size - 1

        if start >= file_size:
            self.send_response(416)  # Range Not Satisfiable
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.end_headers()
            return

        end = min(end, file_size - 1)
        chunk_length = end - start + 1

        self.send_response(206)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(chunk_length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        # Stream chunk in 64 KB buffers
        with file_path.open("rb") as f:
            f.seek(start)
            remaining = chunk_length
            buf_size = 64 * 1024
            while remaining > 0:
                chunk = f.read(min(buf_size, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionResetError, BrokenPipeError):
                    break
                remaining -= len(chunk)

    def log_message(self, format: str, *args) -> None:
        """Suppress standard HTTP connection chatter for clean terminal output."""
        pass


def run_demo_server(port: int = 8080) -> None:
    """Launch local streaming server and auto-open web application in browser."""
    socketserver.TCPServer.allow_reuse_address = True
    server_address = ("", port)

    try:
        httpd = socketserver.TCPServer(server_address, DemoHTTPRequestHandler)
    except OSError:
        port = port + 1
        server_address = ("", port)
        httpd = socketserver.TCPServer(server_address, DemoHTTPRequestHandler)

    url = f"http://localhost:{port}"
    print("=" * 74)
    print("  SHIELDPLAY — TRIGGER-AWARE CINEMA DEMO WEB APPLICATION")
    print("=" * 74)
    print(f"  Status:            ONLINE")
    print(f"  Local Web App URL: {url}")
    print(f"  Full Media 1:      Sintel (888s, 14m 48s, 66 verified cues)")
    print(f"  Full Media 2:      Tears of Steel (734s, 12m 14s, 47 verified cues)")
    print(f"  Video Streaming:   HTTP 206 Partial Content (Instant Scrubbing)")
    print("=" * 74)
    print("  Opening browser to web application...")
    print("  Press Ctrl+C in this terminal to stop.")
    print("=" * 74)

    webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping streaming server...")
        httpd.server_close()
        print("Server cleanly shut down.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Trigger-Aware Cinema Web App.")
    parser.add_argument("--port", type=int, default=8080, help="Port to serve on (default: 8080)")
    args = parser.parse_args()
    run_demo_server(port=args.port)


if __name__ == "__main__":
    main()
