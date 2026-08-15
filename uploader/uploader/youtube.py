"""Thin wrapper around the YouTube Data API v3 videos.insert endpoint."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("uploader.youtube")


def upload_video(video: Path, title: str, description: str, tags: list[str],
                 credentials, privacy: str = "unlisted") -> str:
    """Upload ``video`` to YouTube and return the new video id."""
    import googleapiclient.discovery
    import googleapiclient.http

    youtube = googleapiclient.discovery.build(
        "youtube", "v3", credentials=credentials)
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = googleapiclient.http.MediaFileUpload(
        str(video), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media)
    response = request.execute()
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"upload returned no video id: {response}")
    return video_id
