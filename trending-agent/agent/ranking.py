"""Filtering and ranking of scraped candidates."""

from __future__ import annotations

from typing import Optional

from .language import matches_language
from .models import Short


def is_short(entry: dict, max_duration: int) -> bool:
    live = entry.get("live_status") or ""
    if "is_live" in live or "is_upcoming" in live:
        return False
    duration = entry.get("duration")
    if duration is None:
        return True
    return 0 < duration <= max_duration


def to_short(entry: dict) -> Optional[Short]:
    video_id = entry.get("id")
    title = entry.get("title") or ""
    view_count = entry.get("view_count")
    if not video_id or not title or view_count is None:
        return None
    return Short(
        video_id=video_id,
        title=title,
        channel=entry.get("channel") or entry.get("uploader") or "",
        url=f"https://www.youtube.com/watch?v={video_id}",
        view_count=int(view_count),
        duration=entry.get("duration"),
        channel_url=entry.get("channel_url") or "",
        published=entry.get("timestamp"),
        source=entry.get("source", ""),
    )


def rank_pool(entries: list[dict], top: int, max_duration: int = 180,
              min_views: int = 0, language: str = "all") -> list[Short]:
    shorts = []
    for entry in entries:
        if not is_short(entry, max_duration):
            continue
        short = to_short(entry)
        if short and short.view_count >= min_views \
                and matches_language(short.title, language):
            shorts.append(short)
    shorts.sort(key=lambda s: s.view_count, reverse=True)
    for i, short in enumerate(shorts[:top], start=1):
        short.rank = i
    return shorts[:top]
