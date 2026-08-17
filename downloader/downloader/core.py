"""Download today's trending Shorts and detect each video's language.

Reads the JSON reports written to ``reports/`` on the given day, collects
the distinct video IDs across all of them, downloads each as an MP4 with
yt-dlp, then runs OpenAI Whisper's ``detect_language`` on the audio to
identify the spoken language.

The bundled static-ffmpeg binaries are put on PATH so both yt-dlp (for the
MP4 merge) and whisper (for audio decode) can find ``ffmpeg``/``ffprobe``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("downloader.core")

_REPORT_GLOB = "trending-shorts-*.json"


def _bootstrap_certificates() -> None:
    """Point stdlib/urllib SSL at certifi's CA bundle (macOS Pythons lack it)."""
    try:
        import certifi
    except ImportError:
        return
    ssl_path = certifi.where()
    if "SSL_CERT_FILE" not in os.environ:
        os.environ["SSL_CERT_FILE"] = ssl_path
    if "REQUESTS_CA_BUNDLE" not in os.environ:
        os.environ["REQUESTS_CA_BUNDLE"] = ssl_path


_bootstrap_certificates()
ssl.create_default_context  # keep ssl imported for cert bootstrap usage


@dataclass
class DownloadResult:
    video_id: str
    title: str
    url: str
    file: Optional[str] = None
    language: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "url": self.url,
            "file": self.file,
            "language": self.language,
            "error": self.error,
        }


_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _cookies_file() -> Optional[str]:
    """Path to a yt-dlp cookies file from ``YT_DLP_COOKIES`` if it exists.

    The downloader runs headless in a container, so ``--cookies-from-browser``
    is not an option. When the user exports their YouTube cookies to a text
    file and points ``YT_DLP_COOKIES`` at it, yt-dlp authenticates with it
    and the "Sign in to confirm you're not a bot" block goes away.
    """
    raw = os.environ.get("YT_DLP_COOKIES")
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.exists():
        logger.warning("YT_DLP_COOKIES set but %s does not exist", path)
        return None
    return str(path)


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


def report_files(reports_dir: str | Path, day: Optional[date] = None) -> list[Path]:
    """Report JSON files created on ``day`` (default: today, local time)."""
    day = day or date.today()
    root = Path(reports_dir)
    files = []
    for path in root.glob(_REPORT_GLOB):
        created = datetime.fromtimestamp(path.stat().st_mtime).date()
        if created == day:
            files.append(path)
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def collect_videos(files: list[Path]) -> list[dict]:
    """Distinct videos (id, title, url) across the given reports."""
    seen: dict[str, dict] = {}
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("skipping unreadable report %s: %s", path, exc)
            continue
        for item in data.get("top") or []:
            video_id = item.get("video_id")
            if not video_id:
                continue
            seen.setdefault(video_id, {
                "id": video_id,
                "title": item.get("title") or video_id,
                "url": item.get("url")
                       or f"https://www.youtube.com/watch?v={video_id}",
            })
    return list(seen.values())


def latest_titles(reports_dir: str | Path) -> dict[str, str]:
    """video_id -> title from the trending agent's latest.json report."""
    path = Path(reports_dir) / "latest.json"
    titles: dict[str, str] = {}
    if not path.exists():
        return titles
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("skipping unreadable latest.json %s: %s", path, exc)
        return titles
    for item in data.get("top") or []:
        video_id = item.get("video_id")
        title = item.get("title")
        if video_id and title:
            titles[video_id] = title
    return titles


def _safe_filename(title: str, fallback: str) -> str:
    """Turn a video title into a filesystem-safe base name."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip()
    name = re.sub(r"\s+", " ", name)
    name = name.strip(". ")
    if not name:
        return fallback
    return name[:120].strip(". ") or fallback


def download_mp4s(videos: list[dict], out_dir: str | Path,
                  limit: Optional[int] = None,
                  titles: Optional[dict[str, str]] = None) -> list[DownloadResult]:
    """Download each distinct video as an MP4, returning results.

    Files are named from the trending agent's ``latest.json`` title
    (falling back to the video ID when no title is available).
    """
    from yt_dlp import YoutubeDL

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _ensure_ffmpeg_on_path()
    titles = titles or {}

    base_opts = {
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "socket_timeout": 30,
        "ffmpeg_location": _ffmpeg_bin_dir(),
        "retries": 3,
        "fragment_retries": 3,
        "extractor_args": {
            "youtube": {"player_client": ["android", "ios", "web_safari", "web"]},
        },
        "http_headers": {
            "User-Agent": _BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    }

    cookies = _cookies_file()
    if cookies:
        base_opts["cookiefile"] = cookies
        logger.info("using YouTube cookies from %s", cookies)
    else:
        logger.info(
            "no YT_DLP_COOKIES set; using android/ios player clients to avoid bot checks"
        )

    results: list[DownloadResult] = []
    used_bases: dict[str, int] = {}
    for video in videos[:limit] if limit else videos:
        res = DownloadResult(video_id=video["id"], title=video["title"],
                             url=video["url"])
        base = _safe_filename(titles.get(video["id"]) or video["title"],
                              video["id"])
        if base in used_bases:
            used_bases[base] += 1
            base = f"{base[:100].rstrip()} ({video['id']})"
        else:
            used_bases[base] = 1
        opts = dict(base_opts)
        opts["outtmpl"] = str(out / f"{base}.%(ext)s")
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video["url"], download=True)
            if not info:
                raise RuntimeError("download failed (unavailable or blocked)")
            ext = info.get("ext") or "mp4"
            candidate = out / f"{base}.{ext}"
            mp4 = out / f"{base}.mp4"
            if candidate.exists():
                res.file = str(candidate)
            elif mp4.exists():
                res.file = str(mp4)
            else:
                raise RuntimeError("download produced no output file")
        except Exception as exc:
            res.error = str(exc)
            logger.warning("failed to download %s: %s", video["url"], exc)
        results.append(res)
    return results


def detect_languages(results: list[DownloadResult],
                     model_name: str = "tiny") -> None:
    """Run whisper language detection on each downloaded video (in place)."""
    import whisper

    model = None
    for res in results:
        if not res.file or not Path(res.file).exists():
            continue
        try:
            if model is None:
                logger.info("loading whisper model %r (first run downloads it)",
                            model_name)
                model = whisper.load_model(model_name)
            audio = whisper.load_audio(res.file)
            audio = whisper.pad_or_trim(audio)
            mel = whisper.log_mel_spectrogram(audio).to(model.device)
            _, language_probs = model.detect_language(mel)
            language = max(language_probs, key=language_probs.get)
            res.language = language
            logger.info("%s -> language %s", res.video_id, language)
        except Exception as exc:
            logger.warning("language detection failed for %s: %s",
                           res.video_id, exc)


def _already_downloaded(out_dir: str | Path) -> set[str]:
    """video_ids that already have a downloaded file (from manifest.json)."""
    path = Path(out_dir) / "manifest.json"
    ids: set[str] = set()
    if not path.exists():
        return ids
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ids
    for r in data.get("results") or []:
        if r.get("file") and r.get("video_id"):
            ids.add(r["video_id"])
    return ids


def run_download(reports_dir: str | Path, out_dir: str | Path,
                 day: Optional[date] = None, limit: Optional[int] = None,
                 model_name: str = "tiny") -> dict:
    """Full pipeline: read reports, download MP4s, detect languages.

    Videos already present in ``out_dir/manifest.json`` are skipped so each
    scheduled run only fetches NEW Shorts (every-2h cycle stays incremental).
    """
    files = report_files(reports_dir, day)
    videos = collect_videos(files)
    titles = latest_titles(reports_dir)
    done = _already_downloaded(out_dir)
    if done:
        fresh = [v for v in videos if v["id"] not in done]
        logger.info("skipping %d already-downloaded videos (%d to download)",
                    len(videos) - len(fresh), len(fresh))
        videos = fresh
    logger.info("reports=%d distinct_videos=%d titles_from_latest=%d",
                len(files), len(videos), len(titles))
    results = download_mp4s(videos, out_dir, limit=limit, titles=titles)
    detect_languages(results, model_name=model_name)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reports_dir": str(Path(reports_dir)),
        "report_files": [str(p) for p in files],
        "distinct_videos": len(videos),
        "results": [r.to_dict() for r in results],
    }
    return manifest