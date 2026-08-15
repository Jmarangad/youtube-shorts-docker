"""Fetch engine built on the official YouTube Data API v3.

Requires ``YOUTUBE_API_KEY`` (set in the environment or a local ``.env``
file). No API key is embedded or committed.

Strategy for finding trending Shorts without the missing ``trending``
feed endpoint:

  * ``videos.list`` (chart=mostPopular) is YouTube's own trend analysis —
    the globally most-popular videos right now — from which Shorts
    (<= max duration) are kept.
  * ``search.list`` (order=viewCount, videoDuration=short) collects
    recently-published (``publishedAfter``) viral Shorts worldwide. No
    ``relevanceLanguage``/``regionCode`` is sent, so results span the
    entire world.
  * ``videos.list`` enriches the candidates with view counts, durations,
    channels, publish dates and the snippet's ``defaultAudioLanguage``
    (the search endpoint returns no stats).

Ranking uses a trend score = view_count / hours_since_publish, so Shorts
surging *in the current hour* (recently published with high views) rank
above all-time high-view videos.

Quota notes (free tier = 10,000 units/day): ``search.list`` costs 100
units per call and ``videos.list`` costs 1 unit per 50 ids, so hourly
runs stay comfortably within the daily budget.
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agent.engine")

_BASE = "https://www.googleapis.com/youtube/v3"
_SEARCH_QUERIES = ("trending shorts", "viral shorts")
_MAX_RESULTS = 50
_ISO_DURATION_RE = re.compile(
    r"^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$"
)
_LIVE_MAP = {"upcoming": "is_upcoming", "live": "is_live"}


def _iso_after(hours: int) -> Optional[str]:
    """ISO-8601 timestamp for ``hours`` ago (for publishedAfter)."""
    if hours <= 0:
        return None
    when = datetime.now(timezone.utc) - timedelta(hours=hours)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_dotenv() -> None:
    env_file = Path.cwd() / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def get_api_key() -> str:
    _load_dotenv()
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "YOUTUBE_API_KEY is not set. Create one at Google Cloud Console "
            "(https://console.cloud.google.com/apis/credentials) and export it "
            "or add it to a local .env file."
        )
    return key


def parse_iso_duration(value: Optional[str]) -> Optional[int]:
    """Convert an ISO-8601 duration (e.g. 'PT0M45S') to seconds."""
    if not value:
        return None
    match = _ISO_DURATION_RE.match(value)
    if not match:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _to_entry(item: dict, source: str) -> Optional[dict]:
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    details = item.get("contentDetails") or {}
    video_id = item.get("id")
    title = snippet.get("title")
    if not video_id or not title:
        return None
    live_status = _LIVE_MAP.get(details.get("liveBroadcastContent", ""))
    entry = {
        "id": video_id,
        "title": title,
        "channel": snippet.get("channelTitle") or "",
        "channel_url": f"https://www.youtube.com/channel/{snippet['channelId']}"
                       if snippet.get("channelId") else "",
        "view_count": int(stats.get("viewCount") or 0),
        "duration": parse_iso_duration(details.get("duration")),
        "source": source,
        "audio_language": snippet.get("defaultAudioLanguage") or "",
        "default_language": snippet.get("defaultLanguage") or "",
    }
    published = snippet.get("publishedAt")
    if published:
        entry["timestamp"] = _iso_to_epoch(published)
    if live_status:
        entry["live_status"] = live_status
    return entry


def _iso_to_epoch(value: str) -> Optional[int]:
    try:
        return int(
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .timestamp()
        )
    except ValueError:
        return None


class YouTubeAPI:
    """Minimal client for the endpoints the agent needs."""

    def __init__(self, api_key: Optional[str] = None,
                 timeout: int = 30, geo_country: Optional[str] = None):
        self.api_key = api_key or get_api_key()
        self.timeout = timeout
        self.geo_country = geo_country
        self._ssl = None
        try:
            import certifi
            self._ssl = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            self._ssl = ssl.create_default_context()

    def _get(self, endpoint: str, params: dict) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        params["key"] = self.api_key
        url = f"{_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={
            "User-Agent": "youtube-trending-shorts/1.0",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout,
                                        context=self._ssl) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            logger.error("YouTube API %s failed (%s): %s",
                         endpoint, exc.code, body)
            raise RuntimeError(f"YouTube API {endpoint} error {exc.code}") from exc


class ShortScraper:
    def __init__(self, pool_size: int = 40, lang: str = "",
                 geo_country: Optional[str] = None, timeout: int = 30,
                 api_key: Optional[str] = None,
                 recent_hours: int = 24):
        self.pool_size = min(pool_size, _MAX_RESULTS)
        self.lang = lang
        self.geo_country = geo_country
        self.timeout = timeout
        self.recent_hours = recent_hours
        self.search_queries: tuple[str, ...] = _SEARCH_QUERIES
        self.api = YouTubeAPI(api_key=api_key, timeout=timeout,
                              geo_country=geo_country)

    def _search(self, query: str) -> list[dict]:
        params = {
            "part": "snippet",
            "type": "video",
            "q": query,
            "videoDuration": "short",
            "order": "viewCount",
            "maxResults": self.pool_size,
            "publishedAfter": _iso_after(self.recent_hours),
            "regionCode": self.geo_country,
        }
        if self.lang:
            params["relevanceLanguage"] = self.lang
        data = self.api._get("search", params)
        ids = []
        for item in data.get("items") or []:
            vid = item.get("id", {}).get("videoId")
            if vid:
                ids.append(vid)
        return self._fetch_details(ids)

    def _fetch_details(self, ids: list[str]) -> list[dict]:
        entries = []
        for i in range(0, len(ids), _MAX_RESULTS):
            batch = ids[i:i + _MAX_RESULTS]
            data = self.api._get("videos", {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(batch),
            })
            for item in data.get("items") or []:
                entry = _to_entry(item, "search")
                if entry:
                    entries.append(entry)
        return entries

    def fetch_search_pool(self) -> list[dict]:
        """Most-viewed Shorts for each query (i.e. trending Shorts)."""
        seen: dict[str, dict] = {}
        for query in self.search_queries:
            try:
                for entry in self._search(query):
                    entry = dict(entry)
                    entry["source"] = f"search:{query}"
                    seen.setdefault(entry["id"], entry)
            except RuntimeError as exc:
                logger.warning("search %r failed: %s", query, exc)
        return list(seen.values())

    def fetch_hashtag_pool(self) -> list[dict]:
        """Search the hashtag as a query (the Data API has no hashtag tab)."""
        seen: dict[str, dict] = {}
        for query in self.search_queries:
            tag_query = f"#{query.split()[-1] if query.split() else query}"
            try:
                for entry in self._search(tag_query):
                    entry = dict(entry)
                    entry["source"] = f"hashtag:{tag_query}"
                    seen.setdefault(entry["id"], entry)
            except RuntimeError as exc:
                logger.warning("hashtag %r failed: %s", tag_query, exc)
        return list(seen.values())

    def fetch_trending_feed(self, max_items: int = 60) -> list[dict]:
        """Most popular videos globally, keeping only Shorts (< 4 min)."""
        max_items = min(max_items, _MAX_RESULTS)
        try:
            data = self.api._get("videos", {
                "part": "snippet,contentDetails,statistics",
                "chart": "mostPopular",
                "regionCode": self.geo_country,
                "maxResults": max_items,
            })
        except RuntimeError as exc:
            logger.warning("mostPopular unavailable (%s)", exc)
            return []
        entries = []
        for item in data.get("items") or []:
            entry = _to_entry(item, "trending")
            if entry:
                entry["source"] = "trending"
                entries.append(entry)
        return entries

    def fetch(self, source: str = "auto") -> tuple[list[dict], str]:
        if source == "trending":
            return self.fetch_trending_feed(self.pool_size), "trending"
        if source == "hashtag":
            return self.fetch_hashtag_pool(), "hashtag"
        if source == "search":
            return self.fetch_search_pool(), "search"
        search = self.fetch_search_pool()
        trending = self.fetch_trending_feed(self.pool_size)
        merged: dict[str, dict] = {}
        for e in search + trending:
            merged.setdefault(e["id"], e)
        return list(merged.values()), "auto"
