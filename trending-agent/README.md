# YouTube Trending Shorts Agent

Finds the Shorts **trending in the current hour, across the entire world**
using the official **YouTube Data API v3**. A single-purpose agent (Python)
that collects candidate Shorts, ranks them by *trend velocity*, excludes
Hindi, and reports the top N to the console and as JSON. Designed to run
on-demand or as a scheduled (cron) job.

## API key

A free Google Cloud API key is required:

1. https://console.cloud.google.com/apis/credentials → *Create Credentials* → *API key*
2. Enable the **YouTube Data API v3** for the project.
3. Set the key (never commit it):

```bash
cp .env.example .env        # then paste your key into .env
# or
export YOUTUBE_API_KEY=AIza...
```

The agent loads `.env` from its working directory and also honors the
`YOUTUBE_API_KEY` environment variable or `--api-key`.

## Why this approach

The official Data API avoids scraping: it is not bot-gated and needs no
cookies or anti-bot handling. The API has no dedicated "trending Shorts"
feed, so the agent:

- `videos.list` (`chart=mostPopular`) — YouTube's own trend analysis: the
  globally most-popular videos right now, keeping only Shorts
  (<= `--max-duration`).
- `search.list` (`order=viewCount`, `videoDuration=short`, `publishedAfter`)
  — recently-published viral Shorts worldwide. No `relevanceLanguage`/
  `regionCode` is sent, so candidates span the entire world.
- `videos.list` — enriches candidates with view counts, durations, channels,
  publish dates and `defaultAudioLanguage` (the search endpoint returns no
  stats).

Candidates are ranked by a **trend score** = views per hour since publish,
so a Short uploaded an hour ago with 1M views ("trending in the current
hour") outranks an older video with more total views. By default only
non-Hindi Shorts are reported (Devanagari script, `defaultAudioLanguage='hi'`
and romanized-Hindi titles are all excluded).

Free quota is 10,000 units/day (`search.list` = 100 units, `videos.list` =
1 unit), so hourly runs stay well within budget.

## Quickstart

```bash
cd youtube-trending-agent
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m agent --top 5
```

Example output:

```
#          Views          Title                                                    Channel
 1   2,232,768,114   Sister love 💕 #shorts                                          Sierra & Rhia FAM
 ...
[1] Sister love 💕 #shorts — Sierra & Rhia FAM
    https://www.youtube.com/watch?v=<videoId>
```

Reports are written to `reports/latest.json` and
`reports/trending-shorts-<timestamp>.json`.

## Download today's Shorts and detect their language

Reads every report created today, downloads the **distinct** videos as MP4s,
and runs OpenAI Whisper on each file to identify the spoken language:

```bash
.venv/bin/python -m agent --download            # all videos from today's reports
.venv/bin/python -m agent --download --download-limit 3
.venv/bin/python -m agent --download --whisper-model base --report-date 2026-08-11
```

Flags: `--reports-dir` (default `reports/`), `--download-dir` (default
`downloads/`), `--download-limit`, `--report-date YYYY-MM-DD`, and
`--whisper-model` (tiny, base, small, medium). Results are written to
`downloads/manifest.json` with one entry per video (file path, language,
error). Whisper downloads its model on first use; ffmpeg comes from the
bundled `static-ffmpeg` wheel.

## CLI

| Flag | Default | Meaning |
|---|---|---|
| `--top N` | 5 | number of top Shorts |
| `--source auto\|trending\|hashtag\|search` | auto | auto: search, then most popular |
| `--search-queries Q1,Q2,...` | built-in | comma-separated search queries for the `search` source |
| `--pool-size N` | 40 | candidate pool per query (max 50) |
| `--recent-hours N` | 24 | only consider Shorts published in the last N hours |
| `--api-key KEY` | env | YouTube Data API v3 key (default: `YOUTUBE_API_KEY` or `.env`) |
| `--min-views N` | 0 | ignore Shorts below this many views |
| `--max-duration S` | 180 | max Short duration in seconds |
| `--output DIR` | reports | JSON report directory |
| `--lang LANG` | (empty) | relevance language for requests; empty = entire world |
| `--language LANG` | non-hindi | only keep videos matching this filter (`non-hindi` excludes Hindi; `all` disables the filter; `en` keeps English) |
| `--geo-country CC` | – | region code for search/mostPopular (e.g. US) |
| `--install-cron` | – | install a crontab entry |
| `--cron-schedule SCHED` | `0 * * * *` | schedule used with `--install-cron` |
| `--json-only` | – | suppress the console table |
| `--download` | – | download today's reported Shorts as MP4s and detect languages |
| `--reports-dir DIR` | reports | report directory read by `--download` |
| `--download-dir DIR` | downloads | MP4 output directory |
| `--report-date YYYY-MM-DD` | today | day of reports to process |
| `--download-limit N` | all | max videos to download |
| `--whisper-model NAME` | tiny | whisper model for language detection |

## Deploying as a scheduled job

Install a cron entry that runs the agent hourly and writes reports:

```bash
.venv/bin/python -m agent --install-cron --cron-schedule "0 * * * *" --output /absolute/path/to/reports
```

Verify with `crontab -l`, then confirm the first run works:

```bash
.venv/bin/python -m agent --top 5
```

Notes:

- The agent auto-loads `certifi` CA certs (macOS Pythons often lack them).
- Prefer the same absolute paths everywhere; cron runs with a minimal PATH.
- `yt-dlp` updates often matter for YouTube compatibility — run
  `.venv/bin/pip install -U yt-dlp` periodically.

## Tests

```bash
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest -q
```
