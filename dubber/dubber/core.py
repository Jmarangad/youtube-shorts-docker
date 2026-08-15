"""Dubs downloaded Shorts into Hindi.

Pipeline per video:
  1. transcribe speech with Whisper (segments with timestamps)
  2. analyse each segment's audio (pitch/gender, music, speech rate)
  3. dedupe overlapping/duplicated dialog segments
  4. songs/music segments keep the original audio (background music
     preserved); speech segments are translated to Hindi and re-synthesized
     with a gender-matched voice (male/female, animated-kids fallback)
  5. each Hindi segment's tone (rate + pitch) is tuned to match the
     original speaker, placed at its original timestamp, and the video's
     audio track is replaced with ffmpeg (original video stream kept).
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dubber.core")

_VIDEO_EXTS = {".mp4", ".webm", ".mkv"}
_SAMPLE_RATE = 16000

VOICES = {
    "male": {"name": "hi-IN-MadhurNeural", "natural_f0": 120.0},
    "female": {"name": "hi-IN-SwaraNeural", "natural_f0": 210.0},
    "kids": {"name": "hi-IN-SwaraNeural", "natural_f0": 260.0},
}


def _ffmpeg_bin_dir() -> str:
    try:
        from static_ffmpeg import run
        ffmpeg_path, _ = run.get_or_fetch_platform_executables_else_raise()
        return os.path.dirname(ffmpeg_path)
    except Exception:
        return ""


def _ensure_ffmpeg_on_path() -> str:
    bin_dir = _ffmpeg_bin_dir()
    if bin_dir and bin_dir not in os.environ["PATH"]:
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]
    return bin_dir


def _ffmpeg(cmd: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *cmd],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[:500]}")


def _rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def _probe_duration(path: str) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class DubResult:
    video_id: str
    file: str
    out: Optional[str] = None
    segments: int = 0
    music_segments: int = 0
    voice_counts: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "file": self.file,
            "out": self.out,
            "segments": self.segments,
            "music_segments": self.music_segments,
            "voice_counts": self.voice_counts,
            "error": self.error,
        }


def find_videos(downloads_dir: str | Path) -> list[Path]:
    """MP4s to dub (excludes already-dubbed files)."""
    root = Path(downloads_dir)
    return [p for p in root.iterdir()
            if p.suffix.lower() in _VIDEO_EXTS and ".hindi." not in p.name]


def clean_dubbed(out_dir: str | Path) -> int:
    """Delete previously dubbed files so everything is re-dubbed.

    Dubbed files keep the original video's filename, so every video file
    in the out directory is a previously dubbed output.
    """
    out = Path(out_dir)
    if not out.exists():
        return 0
    removed = 0
    for p in out.iterdir():
        if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
            p.unlink(missing_ok=True)
            removed += 1
    for work in out.glob("*.work"):
        _rmtree(work)
    if removed:
        logger.info("cleaned %d previously dubbed file(s) from %s", removed, out)
    return removed


def _transcribe(video: Path, model_name: str) -> list[Segment]:
    import whisper
    model = whisper.load_model(model_name)
    result = model.transcribe(str(video), fp16=False)
    return [Segment(seg["start"], seg["end"], seg["text"].strip())
            for seg in result.get("segments") or []
            if seg.get("text", "").strip()]


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _dedupe_segments(segments: list[Segment]) -> list[Segment]:
    """Drop overlapping or near-duplicate dialog segments."""
    out: list[Segment] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        dup = False
        for prev in out:
            overlap = min(seg.end, prev.end) - max(seg.start, prev.start)
            prev_len = prev.end - prev.start
            if overlap > 0.3 * prev_len and _similar(text, prev.text) > 0.55:
                dup = True
                break
            if abs(seg.start - prev.start) < 2.5 and _similar(text, prev.text) > 0.8:
                dup = True
                break
        if not dup:
            out.append(seg)
    return out


def _translate(text: str) -> str:
    from deep_translator import GoogleTranslator
    text = text.strip()
    if not text:
        return text
    try:
        return GoogleTranslator(source="auto", target="hi").translate(text) or text
    except Exception as exc:
        logger.warning("translate failed (%s); using original text", exc)
        return text


def _synthesize(text: str, voice: str, out_wav: Path,
                rate: str = "+0%", pitch: str = "+0Hz") -> None:
    import edge_tts
    text = text.strip()
    if not text:
        raise RuntimeError("empty text for TTS")
    if not any(ch.isalnum() for ch in text):
        raise RuntimeError("no synthesizable content in text")
    mp3 = out_wav.with_suffix(".mp3")
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            asyncio.run(communicate.save(str(mp3)))
            if mp3.exists() and mp3.stat().st_size > 0:
                _ffmpeg(["-i", str(mp3), "-ar", "48000", "-ac", "2", str(out_wav)])
                mp3.unlink(missing_ok=True)
                return
            last_error = RuntimeError("edge-tts produced empty audio")
        except Exception as exc:
            last_error = exc
        logger.warning("edge-tts attempt %d failed for %r (%s)",
                       attempt + 1, text[:40], last_error)
        asyncio.run(asyncio.sleep(2.0))
    mp3.unlink(missing_ok=True)
    raise RuntimeError(f"TTS failed after retries: {last_error}")


def _f0_autocorr(frame: "object", sr: int,
                 fmin: float = 70.0, fmax: float = 400.0) -> Optional[float]:
    """Fundamental frequency of a voiced frame via autocorrelation."""
    import numpy as np
    n = len(frame)
    if n < 64:
        return None
    x = frame - float(np.mean(frame))
    r = np.correlate(x, x, "full")[n - 1:]
    lag_min = int(sr / fmax)
    lag_max = int(sr / fmin)
    if lag_max >= len(r) or lag_min >= lag_max:
        return None
    r_slice = r[lag_min:lag_max + 1]
    if r_slice.size == 0 or r_slice.max() <= 0:
        return None
    peak = int(np.argmax(r_slice))
    if r_slice[peak] < 0.3 * r[0]:
        return None
    return sr / (lag_min + peak)


def _analyze_segment(audio: "object", sr: int, start: float, end: float) -> dict:
    """Return {is_music, f0, voiced, active, cv} for one audio segment."""
    import numpy as np
    s0 = int(start * sr)
    s1 = int(end * sr)
    seg = audio[s0:s1]
    result = {"is_music": False, "f0": None, "voiced": 0.0,
              "active": 0.0, "cv": 1.0}
    if len(seg) < int(0.2 * sr):
        return result
    frame_len = int(0.03 * sr)
    hop = int(0.01 * sr)
    rms: list[float] = []
    f0s: list[float] = []
    i = 0
    while i + frame_len <= len(seg):
        frame = seg[i:i + frame_len]
        energy = float(np.sqrt((frame ** 2).mean()))
        rms.append(energy)
        if energy > 1e-4:
            f0 = _f0_autocorr(frame, sr)
            if f0:
                f0s.append(f0)
        i += hop
    if not rms:
        return result
    rms_arr = np.array(rms)
    active = float((rms_arr > (rms_arr.mean() * 0.3)).mean())
    cv = float(rms_arr.std() / (rms_arr.mean() + 1e-9))
    voiced = len(f0s) / len(rms) if rms else 0.0
    f0_med = float(np.median(f0s)) if f0s else None
    result.update({"is_music": active > 0.92 and cv < 0.45,
                   "f0": f0_med, "voiced": voiced, "active": active, "cv": cv})
    return result


def _gender_from_f0(f0: Optional[float]) -> str:
    if f0 is None:
        return "kids"
    if f0 >= 165:
        return "female"
    if f0 <= 150:
        return "male"
    return "kids"


def _tone_params(gender: str, seg: Segment, analysis: dict) -> tuple[str, str]:
    """edge-tts rate/pitch so the Hindi voice matches the original tone."""
    import numpy as np
    dur = max(seg.end - seg.start, 0.3)
    cps = len(seg.text) / dur
    rate_pct = int(round((cps / 14.0 - 1.0) * 100.0))
    rate_pct = max(-40, min(40, rate_pct))
    f0 = analysis.get("f0")
    natural = VOICES[gender]["natural_f0"]
    pitch_hz = int(round((f0 - natural) if f0 else 0.0))
    pitch_hz = max(-120, min(120, pitch_hz))
    if gender == "kids":
        rate_pct += 20
        pitch_hz += 30
    return f"{rate_pct:+d}%", f"{pitch_hz:+d}Hz"


def _extract_original(video: Path, start: float, end: float, out_wav: Path) -> None:
    dur = max(end - start, 0.1)
    _ffmpeg(["-i", str(video), "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
             "-vn", "-ac", "2", "-ar", "48000", str(out_wav)])


def _build_audio(video: Path, segments: list[Segment], workdir: Path,
                 voice_overrides: dict) -> Path:
    """Mix TTS (speech) + original audio (music) at original timestamps."""
    import whisper
    audio = whisper.load_audio(str(video))
    inputs = [str(video)]
    delays: list[str] = []
    filter_parts: list[str] = []
    voices = {k: dict(v) for k, v in VOICES.items()}
    for key, override in (voice_overrides or {}).items():
        if override and key in voices:
            voices[key]["name"] = override
    for i, seg in enumerate(segments):
        analysis = _analyze_segment(audio, _SAMPLE_RATE, seg.start, seg.end)
        if analysis["is_music"]:
            wav = workdir / f"seg{i:04d}.music.wav"
            _extract_original(video, seg.start, seg.end, wav)
        else:
            gender = _gender_from_f0(analysis["f0"]) \
                if analysis["voiced"] > 0.15 else "kids"
            rate, pitch = _tone_params(gender, seg, analysis)
            hindi_text = _translate(seg.text)
            if hindi_text and any(ch.isalnum() for ch in hindi_text):
                wav = workdir / f"seg{i:04d}.{gender}.wav"
                try:
                    _synthesize(hindi_text, voices[gender]["name"], wav,
                                rate=rate, pitch=pitch)
                except Exception as exc:
                    logger.warning("%s seg%d tuned TTS failed (%s); "
                                   "retrying with default params",
                                   video.stem, i, exc)
                    _synthesize(hindi_text, voices[gender]["name"], wav)
            else:
                logger.info("%s seg%d has no synthesizable text; "
                            "keeping original audio", video.stem, i)
                wav = workdir / f"seg{i:04d}.music.wav"
                _extract_original(video, seg.start, seg.end, wav)
        inputs.append(str(wav))
        delay_ms = int(seg.start * 1000)
        delays.append(f"[{i + 1}:a]adelay={delay_ms}|{delay_ms}[d{i}]")
        filter_parts.append(f"[d{i}]")
    if not delays:
        return workdir / "silent.wav"
    n = len(delays)
    filter_complex = ";".join(delays) + ";"
    filter_complex += "".join(filter_parts)
    filter_complex += f"amix=inputs={n}:duration=longest:normalize=0[a]"
    out = workdir / "hindi.m4a"
    cmd = []
    for inp in inputs:
        cmd += ["-i", inp]
    cmd += ["-filter_complex", filter_complex, "-map", "[a]",
            "-c:a", "aac", "-b:a", "160k", str(out)]
    _ffmpeg(cmd)
    return out


def _replace_audio(video: Path, hindi_audio: Path, out_path: Path) -> None:
    dur = _probe_duration(str(video))
    cmd = ["-i", str(video), "-i", str(hindi_audio)]
    if dur > 0:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-shortest", str(out_path)]
    _ffmpeg(cmd)


def dub_video(video: Path, out_dir: str | Path, model_name: str = "base",
              workdir: str | Path | None = None,
              voice_overrides: Optional[dict] = None) -> DubResult:
    res = DubResult(video_id=video.stem, file=str(video))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_path = out / video.name
    if out_path.exists():
        logger.info("%s: already dubbed; skipping", video.stem)
        res.out = str(out_path)
        return res
    work = Path(workdir) if workdir else out / f"{video.stem}.work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        segments = _dedupe_segments(_transcribe(video, model_name))
        res.segments = len(segments)
        if not segments:
            logger.info("%s: no speech detected; skipping dub", video.stem)
            return res
        import whisper
        audio = whisper.load_audio(str(video))
        music = 0
        voice_counts: dict = {}
        for seg in segments:
            analysis = _analyze_segment(audio, _SAMPLE_RATE, seg.start, seg.end)
            if analysis["is_music"]:
                music += 1
            else:
                gender = _gender_from_f0(analysis["f0"]) \
                    if analysis["voiced"] > 0.15 else "kids"
                voice_counts[gender] = voice_counts.get(gender, 0) + 1
        res.music_segments = music
        res.voice_counts = voice_counts
        hindi = _build_audio(video, segments, work, voice_overrides)
        _replace_audio(video, hindi, out_path)
        res.out = str(out_path)
        logger.info("%s -> %s (%d segments, %d music, voices=%s)",
                    video.stem, out_path.name, len(segments), music, voice_counts)
    except Exception as exc:
        res.error = str(exc)
        logger.error("dub failed for %s: %s", video.stem, exc)
    finally:
        _rmtree(work)
    return res


def run_dub(downloads_dir: str | Path, out_dir: str | Path,
            limit: Optional[int] = None, model_name: str = "base",
            clean: bool = False,
            voice_overrides: Optional[dict] = None) -> dict:
    _ensure_ffmpeg_on_path()
    if clean:
        clean_dubbed(out_dir)
    videos = find_videos(downloads_dir)
    logger.info("videos_to_dub=%d", len(videos))
    results = []
    for video in (videos[:limit] if limit else videos):
        results.append(dub_video(video, out_dir, model_name=model_name,
                                 voice_overrides=voice_overrides))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "downloads_dir": str(Path(downloads_dir)),
        "videos_found": len(videos),
        "results": [r.to_dict() for r in results],
    }
    return manifest