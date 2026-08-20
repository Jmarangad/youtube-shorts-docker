"""Pipeline state shared across the LangGraph nodes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PipelineConfig:
    """Where shared volumes live and which agent containers to exec into."""

    reports_dir: str = "/reports"
    downloads_dir: str = "/downloads"
    dubbed_dir: str = "/dubbed"
    config_dir: str = "/config"

    trending_container: str = "youtube-trending-agent"
    downloader_container: str = "youtube-shorts-downloader"
    dubber_container: str = "youtube-shorts-dubber"
    uploader_container: str = "youtube-shorts-uploader"

    top: int = 8
    language: str = "non-hindi"
    recent_hours: int = 24
    whisper_model: str = "tiny"
    privacy: str = "unlisted"
    dry_run: bool = False
    cookies: str | None = None


@dataclass
class PipelineState:
    """Mutable state threaded through the graph on each run."""

    config: PipelineConfig = field(default_factory=PipelineConfig)

    reports_written: int = 0
    videos_downloaded: int = 0
    videos_dubbed: int = 0
    videos_uploaded: int = 0

    download_errors: list[str] = field(default_factory=list)
    dub_errors: list[str] = field(default_factory=list)
    upload_errors: list[str] = field(default_factory=list)

    started_at: str = ""
    finished_at: str = ""