# YouTube Shorts Docker Deployment

Containerized deployment of two Python agents:

| Service | Container | Schedule |
|---|---|---|
| **trending-agent** | `youtube-trending-agent` | hourly (top of hour, IST) |
| **downloader** | `youtube-shorts-downloader` | daily 23:30 IST |

- `trending-agent` uses the YouTube Data API v3 to find the top trending
  Shorts and writes JSON reports to the shared `reports` volume.
- `downloader` reads the day's reports from the shared `reports` volume,
  downloads the distinct videos as MP4s to the `downloads` volume, and
  detects each video's language with Whisper.
- Scheduling runs **inside** the containers via cron, so no host cron is
  needed.

## Prerequisites

- Docker + Docker Compose
- A YouTube Data API v3 key (https://console.cloud.google.com/apis/credentials)

## Deploy

```bash
cp .env.example .env        # paste your YOUTUBE_API_KEY
docker compose up -d --build
```

## Verify

```bash
docker compose ps
docker compose logs trending-agent     # hourly runs
docker compose logs downloader         # daily runs
docker volume ls                       # reports, downloads, whisper-cache
```

The Whisper model downloads on the downloader's first run and is cached in
the `whisper-cache` volume. To change model size, set `WHISPER_MODEL` in
`.env` and recreate the downloader container.

## Tear down

```bash
docker compose down
```
