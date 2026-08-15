"""Filtering and ranking of scraped candidates.

Ranking uses a *trend score* = view_count / hours_since_publish, so a
Short published an hour ago with 1M views ("trending in the current
hour") outranks an older video with more total views. This mirrors
YouTube's own most-popular analysis, which favours rising videos.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
        audio_language=entry.get("audio_language") or "",
    )


def _trend_score(short: Short, now_epoch: int) -> Optional[float]:
    """Views per hour since publish — higher means trending faster now."""
    published = short.published
    if not published:
        return None
    hours = max((now_epoch - published) / 3600.0, 0.02)
    if hours <= 0:
        return None
    return round(short.view_count / hours, 1)


def rank_pool(entries: list[dict], top: int, max_duration: int = 180,
              min_views: int = 0, language: str = "all",
              now: Optional[int] = None) -> list[Short]:
    if now is None:
        now = int(datetime.now(timezone.utc).timestamp())
    shorts = []
    for entry in entries:
        if not is_short(entry, max_duration):
            continue
        short = to_short(entry)
        if short and short.view_count >= min_views \
                and matches_language(short.title, language,
                                     short.audio_language):
            short.trend_score = _trend_score(short, now)
            shorts.append(short)
    shorts.sort(key=lambda s: (
        s.trend_score if s.trend_score is not None else 0.0,
        s.view_count,
    ), reverse=True)
    for i, short in enumerate(shorts[:top], start=1):
        short.rank = i
    return shorts[:top]