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
_MIX_RATE = 48000

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
    """edge-tts rate/pitch so the Hindi voice matches the original tone.

    Clamped to gentle ranges so the dubbed speech stays natural instead of
    sounding chipmunk-fast or monotone-booming.
    """
    import numpy as np
    dur = max(seg.end - seg.start, 0.3)
    cps = len(seg.text) / dur
    rate_pct = int(round((cps / 14.0 - 1.0) * 100.0))
    rate_pct = max(-28, min(28, rate_pct))
    f0 = analysis.get("f0")
    natural = VOICES[gender]["natural_f0"]
    pitch_hz = int(round((f0 - natural) if f0 else 0.0))
    pitch_hz = max(-60, min(60, pitch_hz))
    if gender == "kids":
        rate_pct += 10
        pitch_hz += 20
    return f"{rate_pct:+d}%", f"{pitch_hz:+d}Hz"


def _read_wav(path: Path) -> tuple["np.ndarray", int]:
    """Read a PCM wav into float32 samples in [-1, 1]."""
    import numpy as np
    import wave
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    data = data.reshape(-1, nch) if nch > 1 else data[:, None]
    return data.astype(np.float32) / 32768.0, sr


def _write_wav(path: Path, arr: "np.ndarray", sr: int = _MIX_RATE) -> None:
    """Write float32 samples back out as a stereo PCM wav."""
    import numpy as np
    import wave
    pcm = np.clip(arr, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _place_clip(mix: "np.ndarray", clip: "np.ndarray", s0: int,
                fade_in: float = 0.03, fade_out: float = 0.04) -> None:
    """Add a clip into the timeline mix with smooth in/out fades."""
    import numpy as np
    if clip.shape[0] == 0 or s0 >= mix.shape[0]:
        return
    T = min(clip.shape[0], mix.shape[0] - s0)
    w = np.ones(T)
    fi = min(int(fade_in * _MIX_RATE), T // 2)
    fo = min(int(fade_out * _MIX_RATE), T // 2)
    if fi > 0:
        w[:fi] = np.linspace(0.0, 1.0, fi, endpoint=False)
    if fo > 0:
        w[-fo:] = np.linspace(1.0, 0.0, fo, endpoint=False)
    mix[s0:s0 + T] += clip[:T] * w[:, None]


def _postprocess_tts(wav_in: Path, wav_out: Path) -> None:
    """EQ + gentle compression + subtle reverb + limiter for a natural voice."""
    af = (
        "highpass=f=75,lowpass=f=13000,"
        "acompressor=threshold=-22dB:ratio=2.5:attack=12:release=180:makeup=4dB,"
        "aecho=0.8:0.85:55|85:0.12|0.06,"
        "alimiter=limit=0.95"
    )
    _ffmpeg(["-i", str(wav_in), "-af", af, "-c:a", "pcm_s16le", str(wav_out)])


def _smooth_mask(mask: "np.ndarray", window: float = 0.05,
                 tau: float = 0.25) -> "np.ndarray":
    """Window-averaged + convolved smoothing of a per-sample boolean mask."""
    import numpy as np
    n = mask.shape[0]
    w = max(int(window * _MIX_RATE), 1)
    nw = (n + w - 1) // w
    padded = np.pad(mask, (0, nw * w - n))
    env = padded.reshape(nw, w).mean(axis=1)
    kern_len = max(int(tau / window), 1)
    kern = np.ones(kern_len) / kern_len
    env_s = np.convolve(env, kern, mode="same")
    return np.repeat(env_s, w)[:n].astype(np.float32)


def _scene_envelope(orig: "np.ndarray", window: float = 0.5) -> "np.ndarray":
    """Normalized loudness envelope (0..1) of the original audio."""
    import numpy as np
    n = orig.shape[0]
    w = int(window * _MIX_RATE)
    mono = orig.mean(axis=1)
    nw = (n + w - 1) // w
    padded = np.pad(mono ** 2, (0, nw * w - n))
    rms = np.sqrt(padded.reshape(nw, w).mean(axis=1))
    rms /= (float(np.percentile(rms, 95)) + 1e-9)
    rms = np.clip(rms, 0.0, 1.2)
    kern = np.ones(3) / 3
    rms_s = np.convolve(rms, kern, mode="same")
    return np.repeat(rms_s, w)[:n].astype(np.float32)


def _make_music_bed(total: int, chord_dur: float = 4.0) -> "np.ndarray":
    """Soft mellow ambient pad that blends with any scene (numpy synth)."""
    import numpy as np
    sr = _MIX_RATE
    chords = [
        [57, 60, 64, 67],
        [53, 57, 60, 64],
        [48, 52, 55, 59],
        [55, 59, 62, 67],
    ]
    bed = np.zeros(total, dtype=np.float64)
    n_chords = int(np.ceil(total / sr / chord_dur)) + 1
    phase = 0.0
    for c in range(n_chords):
        t0 = c * chord_dur
        t1 = min((c + 1) * chord_dur, total / sr)
        i0 = int(t0 * sr)
        i1 = min(int(t1 * sr), total)
        if i1 <= i0:
            break
        tt = np.arange(i1 - i0) / sr
        attack = 1.2
        release = min(2.0, chord_dur - 0.4)
        env = np.minimum(tt / attack + 0.35, (tt[-1] - tt) / release + 0.35)
        env = np.clip(env, 0.0, 1.0) ** 1.4
        tone = np.zeros(tt.shape[0])
        for midi in chords[c % len(chords)]:
            freq = 440.0 * 2 ** ((midi - 69) / 12.0)
            tone += np.sin(2 * np.pi * freq * tt + phase)
            tone += 0.45 * np.sin(2 * np.pi * freq * 2.0 * tt + phase)
            tone += 0.30 * np.sin(2 * np.pi * freq * 1.004 * tt + 0.4)
        bed[i0:i1] += env * tone
        phase += 0.35
    peak = float(np.max(np.abs(bed))) or 1.0
    bed = bed / peak * 0.9
    delay = int(0.008 * sr)
    right = np.roll(bed, delay)
    right[:delay] = 0.0
    return np.stack([bed, right * 0.85], axis=1).astype(np.float32)


def _build_audio(video: Path, segments: list[Segment], workdir: Path,
                 voice_overrides: dict) -> Path:
    """Build the dubbed soundtrack in a single numpy timeline mix.

    Speech is dubbed with a natural, processed Hindi voice; songs keep the
    original audio; silent gaps get a soft ambient music bed shaped by the
    scene's loudness. Crossfades on every clip smooth all transitions.
    """
    import numpy as np
    import whisper
    sr = _MIX_RATE
    orig_wav = workdir / "orig.wav"
    _ffmpeg(["-i", str(video), "-vn", "-ac", "2", "-ar", str(sr),
             str(orig_wav)])
    orig, _ = _read_wav(orig_wav)
    total = orig.shape[0]
    if total == 0:
        return workdir / "silent.wav"

    audio = whisper.load_audio(str(video))
    mix = np.zeros((total, 2), dtype=np.float32)
    speech_mask = np.zeros(total, dtype=bool)
    scene_mask = np.zeros(total, dtype=bool)

    voices = {k: dict(v) for k, v in VOICES.items()}
    for key, override in (voice_overrides or {}).items():
        if override and key in voices:
            voices[key]["name"] = override

    for i, seg in enumerate(segments):
        s0 = int(seg.start * sr)
        s1 = min(int(seg.end * sr), total)
        if s1 <= s0:
            continue
        analysis = _analyze_segment(audio, _SAMPLE_RATE, seg.start, seg.end)
        if analysis["is_music"]:
            clip = np.ascontiguousarray(orig[s0:s1], dtype=np.float32)
            _place_clip(mix, clip, s0)
            scene_mask[s0:s1] = True
            continue
        gender = _gender_from_f0(analysis["f0"]) \
            if analysis["voiced"] > 0.15 else "kids"
        rate, pitch = _tone_params(gender, seg, analysis)
        hindi_text = _translate(seg.text)
        clip = None
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
            proc = workdir / f"seg{i:04d}.proc.wav"
            _postprocess_tts(wav, proc)
            clip, _ = _read_wav(proc)
        if clip is not None and clip.shape[0] > 0:
            _place_clip(mix, clip, s0)
            c1 = min(total, s0 + clip.shape[0])
            speech_mask[s0:c1] = True
        else:
            logger.info("%s seg%d has no synthesizable text; "
                        "keeping original audio", video.stem, i)
            clip = np.ascontiguousarray(orig[s0:s1], dtype=np.float32)
            _place_clip(mix, clip, s0)
            scene_mask[s0:s1] = True

    if segments:
        bed = _make_music_bed(total)
        env = _scene_envelope(orig)
        free = _smooth_mask((~speech_mask & ~scene_mask).astype(np.float32),
                            tau=0.15)
        bed_gain = 0.85 * (0.65 + 0.35 * env) * (0.25 + 0.75 * free)
        mix += bed * bed_gain[:, None]
        logger.info(
            "%s: bed_rms=%.4f bed_gain=[%.2f,%.2f] env=[%.2f,%.2f] "
            "free=[%.2f,%.2f] speech_cover=%.0f%% scene_cover=%.0f%%",
            video.stem, float(np.sqrt(np.mean((bed * bed_gain[:, None]) ** 2))),
            float(bed_gain.min()), float(bed_gain.max()),
            float(env.min()), float(env.max()),
            float(free.min()), float(free.max()),
            100 * float(speech_mask.mean()), 100 * float(scene_mask.mean()))

    peak = float(np.max(np.abs(mix))) or 1.0
    if peak > 0.97:
        mix *= 0.97 / peak
    out_wav = workdir / "hindi.wav"
    _write_wav(out_wav, mix, sr)
    out = workdir / "hindi.m4a"
    _ffmpeg(["-i", str(out_wav), "-c:a", "aac", "-b:a", "160k", str(out)])
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
        _remove_source(video)
    except Exception as exc:
        res.error = str(exc)
        logger.error("dub failed for %s: %s", video.stem, exc)
    finally:
        _rmtree(work)
    return res


def _remove_source(video: Path) -> None:
    """Delete the original video from the downloader directory after dubbing.

    The dubbed MP4 in the out dir is the only copy we need going forward, so
    remove the source to keep the downloader directory from filling up.
    """
    try:
        video.unlink()
        logger.info("%s: deleted original from downloads", video.name)
    except OSError as exc:
        logger.warning("failed to delete original %s: %s", video, exc)


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