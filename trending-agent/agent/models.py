"""Domain model for a trending Short."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Short:
    video_id: str
    title: str
    channel: str
    url: str
    view_count: int
    duration: Optional[int] = None
    channel_url: str = ""
    published: Optional[int] = None
    rank: int = 0
    source: str = ""
    audio_language: str = ""
    trend_score: Optional[float] = None

    def to_dict(self) -> dict:
        data = {
            "rank": self.rank,
            "video_id": self.video_id,
            "title": self.title,
            "channel": self.channel,
            "channel_url": self.channel_url,
            "url": self.url,
            "view_count": self.view_count,
            "duration_s": self.duration,
            "published_timestamp": self.published,
            "source": self.source,
            "audio_language": self.audio_language,
        }
        if self.trend_score is not None:
            data["trend_score"] = self.trend_score
        return data


@dataclass
class AgentResult:
    top: list[Short] = field(default_factory=list)
    pool_size: int = 0
    strategy: str = ""
    errors: list[str] = field(default_factory=list)
