"""Upload dubbed Shorts to YouTube.

Reads the Hindi-dubbed MP4s produced by ``youtube-shorts-dubber``, builds a
YouTube title by translating the source title (taken from the trending
agent's output, carried in the file name) into Hindi, and copies the
trending hashtags into the video's tags and description.

Authentication uses the YouTube Data API v3 OAuth 2.0 flow. A Google Cloud
OAuth client secret and a stored token must be mounted at the config dir
(see ``uploader/README.md``).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("uploader.core")

_TAG_RE = re.compile(r"#\w+", re.UNICODE)


def find_dubbed(dubbed_dir: str | Path) -> list[Path]:
    """Dubbed MP4s ready to upload (excludes work dirs and manifests)."""
    out = Path(dubbed_dir)
    if not out.exists():
        return []
    files = [
        p for p in out.iterdir()
        if p.is_file() and p.suffix.lower() == ".mp4" and ".work" not in p.name
    ]
    files.sort()
    return files


def extract_hashtags(text: str) -> list[str]:
    """Hashtags (``#token``) present in the trending agent's output title."""
    return _TAG_RE.findall(text or "")


def title_text_from_name(name: str) -> str:
    """Descriptive part of a file name with hashtags and separators stripped."""
    stem = Path(name).stem
    stem = _TAG_RE.sub("", stem)
    stem = stem.replace("_", " ")
    stem = re.sub(r"\s+", " ", stem).strip(" -_")
    return stem


def translate_to_hindi(text: str) -> str:
    """Translate free text to Hindi via Google Translate (deep-translator)."""
    if not text:
        return ""
    try:
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source="auto", target="hi").translate(text)
    except Exception as exc:
        logger.warning("translation failed (%s); using source text", exc)
        return ""


def _load_trending_titles(reports_dir: Optional[str]) -> dict[str, str]:
    if not reports_dir:
        return {}
    path = Path(reports_dir) / "latest.json"
    titles: dict[str, str] = {}
    if not path.exists():
        return titles
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cannot read latest.json: %s", exc)
        return titles
    for item in data.get("top") or []:
        vid = item.get("video_id")
        if vid and item.get("title"):
            titles[vid] = item["title"]
    return titles


def _load_downloader_map(downloads_dir: Optional[str]) -> dict[str, str]:
    """Map of downloaded file name -> YouTube video id (from manifest.json)."""
    if not downloads_dir:
        return {}
    path = Path(downloads_dir) / "manifest.json"
    mapping: dict[str, str] = {}
    if not path.exists():
        return mapping
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return mapping
    for r in data.get("results") or []:
        f = r.get("file")
        if f and r.get("video_id"):
            mapping[Path(f).name] = r["video_id"]
    return mapping


def build_metadata(file: Path, reports_dir: Optional[str] = None,
                   downloads_dir: Optional[str] = None) -> dict:
    """Derive the YouTube title, tags and description for a dubbed file."""
    name = file.name
    trending = _load_trending_titles(reports_dir)
    dl_map = _load_downloader_map(downloads_dir)
    video_id = dl_map.get(name)

    # Prefer the trending agent's canonical title when we can match it.
    canonical = trending.get(video_id) if video_id else None
    source = canonical or name

    tags = extract_hashtags(source)
    desc_text = title_text_from_name(source)
    hi_title = translate_to_hindi(desc_text)
    yt_title = (hi_title or desc_text or file.stem).strip()

    extra = ["Hindi", "Shorts"]
    if not any(t.lower() in {"shorts", "#shorts"} for t in tags):
        extra.insert(0, "Shorts")
    seen: set[str] = set()
    ordered: list[str] = []
    for t in tags + extra:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(t)
    yt_tags = ordered

    yt_desc = (desc_text + "\n\n" + " ".join(yt_tags)) if desc_text else " ".join(yt_tags)
    return {
        "video_id": video_id,
        "title": yt_title,
        "tags": yt_tags,
        "description": yt_desc,
        "source_title": source,
    }


def run_upload(dubbed_dir: str | Path,
               reports_dir: Optional[str] = None,
               downloads_dir: Optional[str] = None,
               config_dir: str = "config",
               privacy: str = "unlisted",
               dry_run: bool = False,
               limit: Optional[int] = None) -> dict:
    """Upload every dubbed video not already uploaded (or dry-run)."""
    from .auth import get_credentials
    from .youtube import upload_video

    files = find_dubbed(dubbed_dir)
    manifest_path = Path(dubbed_dir) / "upload-manifest.json"
    uploaded: dict[str, str] = {}
    if manifest_path.exists():
        try:
            uploaded = json.loads(manifest_path.read_text()).get("uploaded", {})
        except (OSError, json.JSONDecodeError):
            uploaded = {}

    creds = None
    if not dry_run:
        try:
            creds = get_credentials(config_dir)
        except Exception as exc:
            logger.error("YouTube authentication failed: %s", exc)
            logger.error("Place client_secret.json + token.json in %s "
                         "(see uploader/README.md). Aborting upload.", config_dir)
            return _write_manifest(manifest_path, uploaded, files, [])

    results: list[dict] = []
    for f in (files[:limit] if limit else files):
        key = f.name
        if key in uploaded:
            logger.info("%s already uploaded (%s); skipping", key, uploaded[key])
            continue
        meta = build_metadata(f, reports_dir=reports_dir,
                              downloads_dir=downloads_dir)
        if dry_run:
            yt_id = "DRY_RUN"
            logger.info("[dry-run] %s -> title=%r tags=%s", key, meta["title"],
                        meta["tags"])
        else:
            try:
                yt_id = upload_video(f, meta["title"], meta["description"],
                                     meta["tags"], creds, privacy)
                logger.info("uploaded %s -> youtube %s", key, yt_id)
            except Exception as exc:
                logger.error("upload failed for %s: %s", key, exc)
                results.append({"file": key, "youtube_id": None,
                                "error": str(exc)})
                continue
        uploaded[key] = yt_id
        results.append({"file": key, "youtube_id": yt_id,
                        "title": meta["title"], "tags": meta["tags"]})

    return _write_manifest(manifest_path, uploaded, files, results)


def _write_manifest(manifest_path: Path, uploaded: dict,
                    files: list[Path], results: list[dict]) -> dict:
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "videos_found": len(files),
        "uploaded_count": len(uploaded),
        "uploaded": uploaded,
        "results": results,
    }
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    except OSError as exc:
        logger.warning("could not write upload manifest: %s", exc)
    return manifest
