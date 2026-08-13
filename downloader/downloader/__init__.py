"""Standalone downloader for YouTube Trending Shorts.

Reads the day's JSON reports produced by youtube-trending-agent, downloads
the distinct videos as MP4s, and detects each video's language with Whisper.
"""

from .core import (DownloadResult, collect_videos, detect_languages,
                   download_mp4s, report_files, run_download)

__version__ = "1.0.0"

__all__ = [
    "DownloadResult",
    "collect_videos",
    "detect_languages",
    "download_mp4s",
    "report_files",
    "run_download",
]