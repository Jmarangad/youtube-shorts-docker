"""Dubs downloaded Shorts into Hindi.

Pipeline per video:
  1. transcribe speech with Whisper (segments with timestamps)
  2. translate each segment's text to Hindi (Google Translate)
  3. synthesize Hindi speech with edge-tts (hi-IN voice)
  4. place each Hindi segment at its original timestamp and replace the
     video's audio track with ffmpeg, keeping the original video stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dubber.core")

_VIDEO_EXTS = {".mp4", ".webm", ".mkv"}
_VOICE = "hi-IN-MadhurNeural"


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
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "file": self.file,
            "out": self.out,
            "segments": self.segments,
            "error": self.error,
        }


def find_videos(downloads_dir: str | Path) -> list[Path]:
    """MP4s to dub (excludes already-dubbed files)."""
    root = Path(downloads_dir)
    return [p for p in root.iterdir()
            if p.suffix.lower() in _VIDEO_EXTS and ".hindi." not in p.name]


def _transcribe(video: Path, model_name: str) -> list[Segment]:
    import whisper
    model = whisper.load_model(model_name)
    result = model.transcribe(str(video), fp16=False)
    return [Segment(seg["start"], seg["end"], seg["text"].strip())
            for seg in result.get("segments") or []
            if seg.get("text", "").strip()]


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


def _synthesize(text: str, voice: str, out_wav: Path) -> None:
    import edge_tts
    mp3 = out_wav.with_suffix(".mp3")
    communicate = edge_tts.Communicate(text, voice)
    asyncio.run(communicate.save(str(mp3)))
    _ffmpeg(["-i", str(mp3), "-ar", "48000", "-ac", "2", str(out_wav)])
    mp3.unlink(missing_ok=True)


def _build_audio(video: Path, segments: list[Segment], workdir: Path,
                 voice: str) -> Path:
    """Synthesize Hindi audio and place segments at their timestamps."""
    inputs = [str(video)]
    filter_parts = []
    delays: list[str] = []
    for i, seg in enumerate(segments):
        wav = workdir / f"seg{i:04d}.wav"
        _synthesize(_translate(seg.text), voice, wav)
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
              voice: str = _VOICE, workdir: str | Path | None = None) -> DubResult:
    res = DubResult(video_id=video.stem, file=str(video))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_path = out / f"{video.stem}.hindi.mp4"
    if out_path.exists():
        logger.info("%s: already dubbed; skipping", video.stem)
        res.out = str(out_path)
        return res
    work = Path(workdir) if workdir else out / f"{video.stem}.work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        segments = _transcribe(video, model_name)
        res.segments = len(segments)
        if not segments:
            logger.info("%s: no speech detected; skipping dub", video.stem)
            return res
        hindi = _build_audio(video, segments, work, voice)
        _replace_audio(video, hindi, out_path)
        res.out = str(out_path)
        logger.info("%s -> %s (%d segments)", video.stem, out_path.name, len(segments))
    except Exception as exc:
        res.error = str(exc)
        logger.error("dub failed for %s: %s", video.stem, exc)
    finally:
        _rmtree(work)
    return res


def run_dub(downloads_dir: str | Path, out_dir: str | Path,
            limit: Optional[int] = None, model_name: str = "base",
            voice: str = _VOICE) -> dict:
    videos = find_videos(downloads_dir)
    logger.info("videos_to_dub=%d", len(videos))
    _ensure_ffmpeg_on_path()
    results = []
    for video in (videos[:limit] if limit else videos):
        results.append(dub_video(video, out_dir, model_name=model_name,
                                 voice=voice))
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "downloads_dir": str(Path(downloads_dir)),
        "videos_found": len(videos),
        "results": [r.to_dict() for r in results],
    }
    return manifest
