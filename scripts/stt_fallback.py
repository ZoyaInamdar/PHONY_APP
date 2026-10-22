#!/usr/bin/env python3
"""Speech-to-Text Fallback and Decision Engine for Profanity Detection.

Provides optional local offline speech-to-text (STT) transcription fallback
when subtitle files (.srt) are missing, empty, malformed, or unreadable.

Contract & Architectural Principles:
1. Subtitle-First: If a valid, usable .srt file exists, it is parsed and matched.
   STT is never called unnecessarily.
2. Explicit Fallback: STT is attempted ONLY when subtitles are unusable AND
   STT fallback is explicitly enabled.
3. Offline & Local: Default backend is local offline `faster-whisper`.
   No cloud APIs or paid services are used.
4. Single Shared Matcher: Uses the exact same vocabulary (data/profanity_wordlist.txt)
   and case-insensitive word-boundary regex (\\b) logic.
5. Shared Contract Output: Emits standard 5-field records:
   {'start': float, 'end': float, 'trigger': 'profanity', 'score': 1.0, 'source': 'profanity_detector'}.
6. Clear Coverage Status: Distinguishes 'ok', 'fallback_used', 'skipped',
   'unavailable', and 'failed'. Never silently marks an unanalyzed film as clean.
"""

from __future__ import annotations

import collections
import json
import math
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_WORDLIST_PATH = REPO_ROOT / "data" / "profanity_wordlist.txt"
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
CANONICAL_PROFANITY_TRIGGER = "profanity"
CANONICAL_PROFANITY_SOURCE = "profanity_detector"
DEFAULT_STT_BACKEND = "faster-whisper"
DEFAULT_STT_MODEL_SIZE = "base.en"


class STTError(RuntimeError):
    """Base exception for speech-to-text errors."""
    pass


class STTUnavailableError(STTError):
    """Raised when the STT library/dependency (e.g. faster-whisper) is not installed."""
    pass


class STTModelNotFoundError(STTError):
    """Raised when local STT model weights are not found on disk."""
    pass


class STTTranscriptionError(STTError):
    """Raised when audio transcription execution fails."""
    pass


def load_stt_config(config_path: Union[str, Path] = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load STT fallback configuration from config.yaml."""
    cfg_p = Path(config_path)
    if not cfg_p.is_file():
        return {
            "enabled": False,
            "selected_backend": DEFAULT_STT_BACKEND,
            "local_model_size": DEFAULT_STT_MODEL_SIZE,
        }

    try:
        import yaml
        with cfg_p.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return {
            "enabled": False,
            "selected_backend": DEFAULT_STT_BACKEND,
            "local_model_size": DEFAULT_STT_MODEL_SIZE,
        }

    # Check triggers.profanity.stt_fallback, falling back to top-level profanity.stt_fallback_enabled
    prof_trig = cfg.get("triggers", {}).get("profanity", {})
    stt_sec = prof_trig.get("stt_fallback", {})
    top_prof = cfg.get("profanity", {})

    enabled = bool(
        stt_sec.get("enabled", False)
        or top_prof.get("stt_fallback_enabled", False)
    )
    backend = str(stt_sec.get("selected_backend") or DEFAULT_STT_BACKEND)
    model_size = str(stt_sec.get("local_model_size") or DEFAULT_STT_MODEL_SIZE)

    return {
        "enabled": enabled,
        "selected_backend": backend,
        "local_model_size": model_size,
    }


def transcribe_audio_faster_whisper(
    audio_path: Union[str, Path],
    model_path_or_size: str = DEFAULT_STT_MODEL_SIZE,
    device: str = "cpu",
    compute_type: str = "int8",
    word_timestamps: bool = True,
    local_files_only: bool = True,
) -> List[Dict[str, Any]]:
    """Transcribe an audio file using faster-whisper locally without cloud network calls.

    Parameters:
        audio_path: Path to the input audio file (e.g. 44.1kHz mono WAV).
        model_path_or_size: Directory path to local model weights, or model size name.
        device: Execution device ('cpu' or 'cuda').
        compute_type: Quantization compute type (e.g. 'int8', 'float32').
        word_timestamps: If True, extracts token-level word timestamps.
        local_files_only: If True, prevents downloading weights from HuggingFace at runtime.

    Returns:
        List of segment dictionaries containing 'start', 'end', 'text', and optional 'words'.

    Raises:
        STTUnavailableError: If faster-whisper is not installed.
        STTModelNotFoundError: If local model weights cannot be loaded.
        STTTranscriptionError: If audio decoding or inference fails.
    """
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file for transcription not found: {path.resolve()}")

    try:
        import faster_whisper
    except ImportError as exc:
        raise STTUnavailableError(
            "The 'faster-whisper' package is not installed in the current environment. "
            "To install offline faster-whisper: pip install faster-whisper. "
            f"Original error: {exc}"
        ) from exc

    # Attempt to load model with offline constraint
    try:
        resolved_model_target = model_path_or_size
        local_target_path = Path(model_path_or_size)
        if local_target_path.exists():
            resolved_model_target = str(local_target_path.resolve())

        model = faster_whisper.WhisperModel(
            resolved_model_target,
            device=device,
            compute_type=compute_type,
            local_files_only=local_files_only,
        )
    except Exception as exc:
        raise STTModelNotFoundError(
            f"Failed to load local faster-whisper model '{model_path_or_size}'. "
            f"Model weights must be present locally for offline operation: {exc}"
        ) from exc

    try:
        segments_gen, _info = model.transcribe(
            str(path.resolve()),
            word_timestamps=word_timestamps,
            language="en",
        )
        segments: List[Dict[str, Any]] = []
        for idx, seg in enumerate(segments_gen):
            seg_dict: Dict[str, Any] = {
                "id": idx + 1,
                "start": float(seg.start),
                "end": float(seg.end),
                "text": str(seg.text or "").strip(),
            }
            if hasattr(seg, "words") and seg.words:
                words_list: List[Dict[str, Any]] = []
                for w in seg.words:
                    words_list.append({
                        "word": str(w.word or "").strip(),
                        "start": float(w.start),
                        "end": float(w.end),
                        "probability": float(getattr(w, "probability", 1.0)),
                    })
                seg_dict["words"] = words_list
            segments.append(seg_dict)
        return segments
    except Exception as exc:
        raise STTTranscriptionError(f"Audio transcription failed on {path.name}: {exc}") from exc


def match_profanity_in_transcript(
    segments: Sequence[Dict[str, Any]],
    word_list: Sequence[str],
    source: str = CANONICAL_PROFANITY_SOURCE,
    use_word_timestamps: bool = True,
    media_duration: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Match profanity terms in transcript segments and produce canonical detection records.

    Reuses the exact same whole-word regex matching logic as the subtitle parser.
    Supports either precise word-level timestamps (when provided by STT) or
    segment-level intervals.

    Parameters:
        segments: Transcribed segments from STT.
        word_list: Sequence of profanity terms.
        source: Component identifier (defaults to 'profanity_detector').
        use_word_timestamps: If True, uses word-level bounds when available.
        media_duration: Optional media duration to clamp or drop out-of-bounds cues.

    Returns:
        List of raw detection dictionaries adhering to the shared contract:
        [{'start': float, 'end': float, 'trigger': 'profanity', 'score': 1.0, 'source': source}]
    """
    if not segments or not word_list:
        return []

    # Compile regexes using the project's exact case-insensitive word boundary contract
    matchers: List[re.Pattern[str]] = []
    for term in word_list:
        t_clean = term.strip()
        if t_clean and not t_clean.startswith("#"):
            escaped = re.escape(t_clean.lower())
            matchers.append(re.compile(rf"\b{escaped}\b", flags=re.IGNORECASE))

    if not matchers:
        return []

    raw_detections: List[Dict[str, Any]] = []

    for seg in segments:
        if not isinstance(seg, dict):
            continue

        s_start = seg.get("start")
        s_end = seg.get("end")
        text = str(seg.get("text") or "").strip()

        if s_start is None or s_end is None:
            continue

        try:
            seg_start_f = float(s_start)
            seg_end_f = float(s_end)
        except (ValueError, TypeError):
            continue

        if not (math.isfinite(seg_start_f) and math.isfinite(seg_end_f)):
            continue
        if seg_start_f < 0.0 or seg_end_f <= seg_start_f:
            continue

        if media_duration is not None and seg_start_f >= media_duration:
            continue

        # Strategy 1: Word-level matching if words list is present and requested
        words_list = seg.get("words")
        if use_word_timestamps and isinstance(words_list, list) and words_list:
            for w in words_list:
                if not isinstance(w, dict):
                    continue
                w_text = str(w.get("word") or "").strip()
                if not w_text:
                    continue

                w_start = w.get("start")
                w_end = w.get("end")
                try:
                    w_start_f = float(w_start) if w_start is not None else seg_start_f
                    w_end_f = float(w_end) if w_end is not None else seg_end_f
                except (ValueError, TypeError):
                    continue

                if not (math.isfinite(w_start_f) and math.isfinite(w_end_f)):
                    continue
                if w_start_f < 0.0:
                    w_start_f = 0.0
                if w_end_f <= w_start_f:
                    w_end_f = round(w_start_f + 0.2, 3)

                if media_duration is not None:
                    if w_start_f >= media_duration:
                        continue
                    w_end_f = min(w_end_f, media_duration)

                # Check against compiled profanity terms
                is_profane = any(p.search(w_text) for p in matchers)
                if is_profane:
                    raw_detections.append({
                        "start": round(w_start_f, 3),
                        "end": round(w_end_f, 3),
                        "trigger": CANONICAL_PROFANITY_TRIGGER,
                        "score": 1.0,
                        "source": source,
                    })
            continue

        # Strategy 2: Segment-level text matching
        if not text:
            continue

        has_match = any(p.search(text) for p in matchers)
        if has_match:
            end_clamped = seg_end_f
            if media_duration is not None:
                end_clamped = min(end_clamped, media_duration)
            if end_clamped > seg_start_f:
                raw_detections.append({
                    "start": round(seg_start_f, 3),
                    "end": round(end_clamped, 3),
                    "trigger": CANONICAL_PROFANITY_TRIGGER,
                    "score": 1.0,
                    "source": source,
                })

    return raw_detections


def detect_profanity_with_fallback(
    srt_path: Optional[Union[str, Path]] = None,
    audio_path: Optional[Union[str, Path]] = None,
    wordlist_path: Optional[Union[str, Path]] = None,
    config: Optional[Dict[str, Any]] = None,
    config_path: Union[str, Path] = DEFAULT_CONFIG_PATH,
    enable_stt: Optional[bool] = None,
    stt_backend: Optional[str] = None,
    stt_model: Optional[str] = None,
    transcriber_fn: Optional[Callable[[Union[str, Path]], List[Dict[str, Any]]]] = None,
    source: str = CANONICAL_PROFANITY_SOURCE,
    film_id: Optional[str] = None,
    media_duration: Optional[float] = None,
) -> Dict[str, Any]:
    """Execute profanity detection following the strict Subtitle-First -> STT Fallback flow.

    Decision Logic:
    Case A: Usable .srt subtitles exist -> parse with pysrt, match words, return status 'ok'.
    Case B: Subtitles missing/unusable, STT disabled -> return status 'skipped' or 'unavailable'.
    Case C: Subtitles missing/unusable, STT enabled -> transcribe audio with local STT, match words, return 'fallback_used'.
    Case D: Subtitles missing/unusable, STT enabled but audio/model/dep missing -> return 'failed' or 'unavailable'.

    Parameters:
        srt_path: Path to .srt file (optional).
        audio_path: Path to extracted/normalized audio WAV file (optional).
        wordlist_path: Path to profanity wordlist (defaults to data/profanity_wordlist.txt).
        config: Pre-loaded config dict.
        config_path: Path to config.yaml if config is not pre-loaded.
        enable_stt: Explicit STT fallback flag override.
        stt_backend: Explicit backend override ('faster-whisper').
        stt_model: Model size or local directory.
        transcriber_fn: Injected transcription callable for testing without external models.
        source: Stable component identifier.
        film_id: Media asset identifier.
        media_duration: Video/audio presentation duration in seconds.

    Returns:
        Structured result dictionary:
        {
            'provenance': {...},
            'detections': [...],
            'sub_file': pysrt.SubRipFile (if subtitles used),
            'transcript_segments': [...] (if STT used)
        }
    """
    from scripts.parse_subtitles import (
        SubtitleParseError,
        cues_to_dicts,
        generate_subtitles_provenance,
        load_wordlist,
        match_profanity_in_cues,
        parse_srt_file,
        parse_subtitle_file,
    )

    resolved_wordlist_path = Path(wordlist_path) if wordlist_path else DEFAULT_WORDLIST_PATH
    words: List[str] = []
    if resolved_wordlist_path.is_file():
        words = load_wordlist(resolved_wordlist_path)

    # Resolve STT configuration
    stt_cfg = load_stt_config(config_path) if config is None else {
        "enabled": bool(
            config.get("triggers", {}).get("profanity", {}).get("stt_fallback", {}).get("enabled", False)
            or config.get("profanity", {}).get("stt_fallback_enabled", False)
        ),
        "selected_backend": str(
            config.get("triggers", {}).get("profanity", {}).get("stt_fallback", {}).get("selected_backend")
            or DEFAULT_STT_BACKEND
        ),
        "local_model_size": str(
            config.get("triggers", {}).get("profanity", {}).get("stt_fallback", {}).get("local_model_size")
            or DEFAULT_STT_MODEL_SIZE
        ),
    }

    stt_enabled = enable_stt if enable_stt is not None else stt_cfg["enabled"]
    backend_to_use = stt_backend or stt_cfg["selected_backend"]
    model_to_use = stt_model or stt_cfg["local_model_size"]

    sub_unusable_reason: Optional[str] = None
    sub_file: Optional[Any] = None

    # -------------------------------------------------------------------------
    # 1. Attempt Subtitle Path (Subtitle-First Policy)
    # -------------------------------------------------------------------------
    if srt_path is not None:
        p_sub = Path(srt_path)
        if not p_sub.exists():
            sub_unusable_reason = f"Subtitle file not found: {p_sub.resolve()}"
        elif not p_sub.is_file():
            sub_unusable_reason = f"Subtitle path is not a file: {p_sub.resolve()}"
        elif p_sub.suffix.lower() not in (".srt", ".vtt"):
            sub_unusable_reason = f"Unsupported subtitle format '{p_sub.suffix}' (only .srt and .vtt are supported)"
        elif p_sub.stat().st_size == 0:
            sub_unusable_reason = "Subtitle file is empty (0 bytes)"
        else:
            try:
                sub_file = parse_subtitle_file(
                    p_sub,
                    wordlist_path=None,
                    source=source,
                    media_duration=media_duration,
                )
                if len(sub_file) == 0:
                    sub_unusable_reason = "Subtitle file contains 0 valid dialogue cues"
                else:
                    # Valid subtitle file successfully parsed!
                    raw_detections = match_profanity_in_cues(sub_file, words, source=source)
                    provenance = generate_subtitles_provenance(
                        source_file=str(p_sub),
                        wordlist_path=str(resolved_wordlist_path),
                    )
                    provenance.update({
                        "status": "ok",
                        "input_method": "subtitles",
                        "evidence_type": "subtitle_text",
                        "timing_type": "estimated",
                        "film_id": film_id,
                        "failure_reason": None,
                    })
                    return {
                        "provenance": provenance,
                        "detections": raw_detections,
                        "sub_file": sub_file,
                        "cues": cues_to_dicts(sub_file),
                    }
            except (SubtitleParseError, ValueError, Exception) as exc:
                sub_unusable_reason = f"Failed to parse subtitle file: {exc}"
    else:
        sub_unusable_reason = "No subtitle file provided"

    # -------------------------------------------------------------------------
    # 2. Subtitles Unusable / Missing: Check STT Fallback
    # -------------------------------------------------------------------------
    if not stt_enabled:
        # Fallback is disabled: record clear skipped/unavailable status without fake clean results
        has_sub_file = False
        try:
            has_sub_file = bool(srt_path and Path(srt_path).is_file())
        except Exception:
            has_sub_file = False
        status_val = "unavailable" if has_sub_file else "skipped"
        provenance = {
            "stage": "profanity_detector",
            "status": status_val,
            "input_method": "none",
            "evidence_type": "none",
            "timing_type": "none",
            "source_file": str(srt_path) if srt_path else None,
            "audio_file": str(audio_path) if audio_path else None,
            "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
            "failure_reason": f"Subtitles unusable ({sub_unusable_reason}) and speech-to-text fallback is disabled.",
            "subtitle_unusable_reason": sub_unusable_reason,
            "backend": None,
            "film_id": film_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "provenance": provenance,
            "detections": [],
            "sub_file": None,
        }

    # -------------------------------------------------------------------------
    # 3. STT Fallback Enabled: Check Audio File & Dependencies
    # -------------------------------------------------------------------------
    if audio_path is None or not Path(audio_path).is_file():
        provenance = {
            "stage": "profanity_detector",
            "status": "failed",
            "input_method": "none",
            "evidence_type": "none",
            "timing_type": "none",
            "source_file": str(srt_path) if srt_path else None,
            "audio_file": str(audio_path) if audio_path else None,
            "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
            "failure_reason": f"Subtitles unusable ({sub_unusable_reason}), and audio file for STT is missing: {audio_path}",
            "backend": backend_to_use,
            "film_id": film_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "provenance": provenance,
            "detections": [],
            "sub_file": None,
        }

    # Check supported backend
    if backend_to_use != DEFAULT_STT_BACKEND:
        provenance = {
            "stage": "profanity_detector",
            "status": "unavailable",
            "input_method": "speech_to_text",
            "evidence_type": "none",
            "timing_type": "none",
            "source_file": str(srt_path) if srt_path else None,
            "audio_file": str(audio_path) if audio_path else None,
            "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
            "failure_reason": f"Unsupported or cloud STT backend '{backend_to_use}'. Only local offline '{DEFAULT_STT_BACKEND}' is permitted.",
            "backend": backend_to_use,
            "film_id": film_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "provenance": provenance,
            "detections": [],
            "sub_file": None,
        }

    # -------------------------------------------------------------------------
    # 4. Execute Transcription (Injected Callable or Local faster-whisper)
    # -------------------------------------------------------------------------
    segments: List[Dict[str, Any]] = []
    try:
        if transcriber_fn is not None:
            segments = transcriber_fn(audio_path)
        else:
            segments = transcribe_audio_faster_whisper(
                audio_path=audio_path,
                model_path_or_size=model_to_use,
            )
    except STTUnavailableError as exc:
        provenance = {
            "stage": "profanity_detector",
            "status": "unavailable",
            "input_method": "speech_to_text",
            "evidence_type": "none",
            "timing_type": "none",
            "source_file": str(srt_path) if srt_path else None,
            "audio_file": str(audio_path) if audio_path else None,
            "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
            "failure_reason": str(exc),
            "backend": backend_to_use,
            "film_id": film_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "provenance": provenance,
            "detections": [],
            "sub_file": None,
        }
    except STTModelNotFoundError as exc:
        provenance = {
            "stage": "profanity_detector",
            "status": "unavailable",
            "input_method": "speech_to_text",
            "evidence_type": "none",
            "timing_type": "none",
            "source_file": str(srt_path) if srt_path else None,
            "audio_file": str(audio_path) if audio_path else None,
            "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
            "failure_reason": str(exc),
            "backend": backend_to_use,
            "film_id": film_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "provenance": provenance,
            "detections": [],
            "sub_file": None,
        }
    except Exception as exc:
        provenance = {
            "stage": "profanity_detector",
            "status": "failed",
            "input_method": "speech_to_text",
            "evidence_type": "none",
            "timing_type": "none",
            "source_file": str(srt_path) if srt_path else None,
            "audio_file": str(audio_path) if audio_path else None,
            "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
            "failure_reason": f"Audio transcription execution failed: {exc}",
            "backend": backend_to_use,
            "film_id": film_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "provenance": provenance,
            "detections": [],
            "sub_file": None,
        }

    # -------------------------------------------------------------------------
    # 5. Validate Transcript Usability Before Claiming Clean Scan
    # -------------------------------------------------------------------------
    if not segments:
        provenance = {
            "stage": "profanity_detector",
            "status": "failed",
            "input_method": "speech_to_text",
            "evidence_type": "none",
            "timing_type": "none",
            "source_file": str(srt_path) if srt_path else None,
            "audio_file": str(audio_path),
            "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
            "subtitle_unusable_reason": sub_unusable_reason,
            "failure_reason": "Speech-to-text returned 0 segments (empty transcript). Cannot verify speech content.",
            "backend": backend_to_use,
            "model": model_to_use,
            "film_id": film_id,
            "total_transcript_segments": 0,
            "total_detections": 0,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "provenance": provenance,
            "detections": [],
            "transcript_segments": [],
            "sub_file": None,
        }

    # Verify that at least one segment contains non-blank speech text
    has_usable_text = any(bool(str(s.get("text", "")).strip()) for s in segments if isinstance(s, dict))
    if not has_usable_text:
        provenance = {
            "stage": "profanity_detector",
            "status": "failed",
            "input_method": "speech_to_text",
            "evidence_type": "none",
            "timing_type": "none",
            "source_file": str(srt_path) if srt_path else None,
            "audio_file": str(audio_path),
            "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
            "subtitle_unusable_reason": sub_unusable_reason,
            "failure_reason": "Speech-to-text produced only blank text across all segments. Cannot verify speech content.",
            "backend": backend_to_use,
            "model": model_to_use,
            "film_id": film_id,
            "total_transcript_segments": len(segments),
            "total_detections": 0,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "provenance": provenance,
            "detections": [],
            "transcript_segments": segments,
            "sub_file": None,
        }

    # Verify that at least one segment has valid finite timing
    has_valid_timing = False
    for s in segments:
        if isinstance(s, dict) and "start" in s and "end" in s:
            try:
                s_st = float(s["start"])
                s_en = float(s["end"])
                if math.isfinite(s_st) and math.isfinite(s_en) and s_st >= 0.0 and s_en > s_st:
                    has_valid_timing = True
                    break
            except (ValueError, TypeError):
                continue

    if not has_valid_timing:
        provenance = {
            "stage": "profanity_detector",
            "status": "failed",
            "input_method": "speech_to_text",
            "evidence_type": "none",
            "timing_type": "none",
            "source_file": str(srt_path) if srt_path else None,
            "audio_file": str(audio_path),
            "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
            "subtitle_unusable_reason": sub_unusable_reason,
            "failure_reason": "Speech-to-text produced invalid, non-finite, or non-positive segment timestamps.",
            "backend": backend_to_use,
            "model": model_to_use,
            "film_id": film_id,
            "total_transcript_segments": len(segments),
            "total_detections": 0,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "provenance": provenance,
            "detections": [],
            "transcript_segments": segments,
            "sub_file": None,
        }

    # -------------------------------------------------------------------------
    # 6. Process Transcript with Shared Profanity Matcher
    # -------------------------------------------------------------------------
    has_words = any(isinstance(s.get("words"), list) and s.get("words") for s in segments)
    raw_detections = match_profanity_in_transcript(
        segments=segments,
        word_list=words,
        source=source,
        use_word_timestamps=has_words,
        media_duration=media_duration,
    )

    provenance = {
        "stage": "profanity_detector",
        "status": "fallback_used",
        "input_method": "speech_to_text",
        "evidence_type": "stt_word" if has_words else "stt_transcript",
        "timing_type": "exact" if has_words else "segment",
        "source_file": str(srt_path) if srt_path else None,
        "audio_file": str(audio_path),
        "wordlist_path": str(resolved_wordlist_path) if resolved_wordlist_path else None,
        "subtitle_unusable_reason": sub_unusable_reason,
        "failure_reason": None,
        "backend": backend_to_use,
        "model": model_to_use,
        "film_id": film_id,
        "total_transcript_segments": len(segments),
        "total_detections": len(raw_detections),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "provenance": provenance,
        "detections": raw_detections,
        "transcript_segments": segments,
        "sub_file": None,
    }
