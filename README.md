# YouTube Shorts Docker Deployment

Containerized deployment of three Python agents:

| Service | Container | Schedule |
|---|---|---|
| **trending-agent** | `youtube-trending-agent` | hourly (top of hour, IST) |
| **downloader** | `youtube-shorts-downloader` | daily 23:30 IST |
| **dubber** | `youtube-shorts-dubber` | daily 23:40 IST |

- `trending-agent` uses the YouTube Data API v3 to find Shorts trending in
  the current hour across the entire world, ranks them by views-per-hour
  velocity, excludes Hindi, and writes JSON reports to the shared `reports`
  volume.
- `downloader` reads the day's reports from the shared `reports` volume,
  downloads the distinct videos as MP4s to the `downloads` volume, and
  detects each video's language with Whisper.
- `dubber` re-dubs each downloaded Short into Hindi: Whisper transcribes
  the speech, Google Translate translates each segment, and edge-tts
  (`hi-IN-MadhurNeural`) synthesizes the Hindi voice, which is muxed over
  the original video track with ffmpeg into the `dubbed` volume.
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
docker compose logs dubber             # daily Hindi dubbing
docker volume ls                       # reports, downloads, whisper-cache, dubbed
```

The Whisper model downloads on the downloader's first run and is cached in
the `whisper-cache` volume. To change model size, set `WHISPER_MODEL` in
`.env` and recreate the downloader container. The dubber uses `base` by
default; set `DUB_WHISPER_MODEL` / `DUB_VOICE` in `.env` to override.

Dubbed videos land in the `dubbed` volume as `<video-id>.hindi.mp4`, with a
`dub-manifest.json` summarizing each run.

## Tear down

```bash
docker compose down
```
