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

    def to_dict(self) -> dict:
        return {
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
        }


@dataclass
class AgentResult:
    top: list[Short] = field(default_factory=list)
    pool_size: int = 0
    strategy: str = ""
    errors: list[str] = field(default_factory=list)
