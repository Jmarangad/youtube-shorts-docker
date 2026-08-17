# youtube-shorts-downloader

Downloads the day's trending YouTube Shorts as **MP4s** and detects each
video's **language** with OpenAI Whisper.

It consumes the JSON reports written by
[`youtube-trending-agent`](../youtube-trending-agent): every report created
today is read, the distinct video IDs are collected (deduplicated), each
video is downloaded as MP4 via yt-dlp, and Whisper analyzes the audio to
identify the spoken language.

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -e .[test]
```

No API key is needed. ffmpeg comes from the bundled `static-ffmpeg` wheel;
Whisper downloads its model (`tiny` by default) on first run.

YouTube may occasionally block downloads with *"Sign in to confirm you're not
a bot"*. This image already bypasses that for most videos by rotating
`player_client`s (`android`/`ios`/`web_safari`/`web`) with a desktop Chrome
User-Agent. If a video still refuses, export your YouTube cookies and pass
them via `--cookies` (or the `YT_DLP_COOKIES` env var); in docker compose,
set `YT_DLP_COOKIES` to a path inside the container (default cookies dir is
mounted from `./config`):

```bash
export YT_DLP_COOKIES=/cookies/cookies.txt   # exported via browser extension
docker compose up -d --build downloader
```

## Usage

```bash
.venv/bin/python -m downloader                                # all of today's videos
.venv/bin/python -m downloader --download-limit 3             # limit to 3
.venv/bin/python -m downloader --report-date 2026-08-11       # a specific day
.venv/bin/python -m downloader --whisper-model base           # better language accuracy
.venv/bin/python -m downloader --reports-dir /path/to/youtube-trending-agent/reports
```

Results are written to `downloads/manifest.json` — one entry per video with
the file path, detected language, and any error.

Downloaded files are named from the trending agent's `latest.json` report
**title** (sanitized for the filesystem, with the video ID appended if a
title collides), falling back to the video ID when no title is available.

## Deploying as a daily scheduled job

Install a crontab entry that runs every day at 23:30 (after the day's hourly
trending report), downloading that day's distinct Shorts:

```bash
.venv/bin/python -m downloader --install-cron \
  --reports-dir /absolute/path/to/youtube-trending-agent/reports \
  --download-dir /absolute/path/to/downloads
```

Verify with `crontab -l`, then confirm a first run works:

```bash
.venv/bin/python -m downloader --download-limit 1
```

Note: run with the same absolute paths everywhere, because cron uses a
minimal PATH and environment.