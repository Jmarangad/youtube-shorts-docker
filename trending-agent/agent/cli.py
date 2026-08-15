"""CLI entrypoint for the Trending Shorts agent."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .engine import ShortScraper, get_api_key
from .models import AgentResult, Short
from .output import print_table, write_json
from .ranking import rank_pool

logger = logging.getLogger("agent.cli")

_CRON_LINE_TEMPLATE = "{schedule} cd {cwd} && {python} -m agent --top {top} --language {lang} --output {out}"


def _add_cron(schedule: str, top: int, output: str, language: str = "all") -> bool:
    cwd = shlex.quote(str(Path.cwd()))
    python = shlex.quote(sys.executable)
    out = shlex.quote(str(Path(output).resolve()))
    line = _CRON_LINE_TEMPLATE.format(schedule=schedule, cwd=cwd,
                                      python=python, top=top, out=out, lang=language)
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if line in existing:
        print("Cron entry already present; not duplicated.")
        return True
    new = existing.rstrip("\n") + "\n" + line + "\n"
    proc = subprocess.run(["crontab", "-"], input=new, text=True, capture_output=True)
    if proc.returncode != 0:
        logger.error("failed to install cron entry: %s", proc.stderr)
        return False
    print(f"Installed cron entry:\n  {line}")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youtube-trending-shorts",
        description="Find YouTube's top trending Shorts using the official "
                    "YouTube Data API v3, ranked by views.",
    )
    parser.add_argument("--top", type=int, default=5, help="number of top Shorts (default 5)")
    parser.add_argument("--source", choices=("auto", "trending", "hashtag", "search"), default="auto",
                        help="auto: search, then most popular (default)")
    parser.add_argument("--search-queries", default="trending shorts, viral shorts",
                        help="comma-separated queries for the search source (default built-in)")
    parser.add_argument("--pool-size", type=int, default=40,
                        help="candidate pool per query (max 50, default 40)")
    parser.add_argument("--recent-hours", type=int, default=24,
                        help="only consider Shorts published in the last N hours (default 24)")
    parser.add_argument("--api-key", default=None,
                        help="YouTube Data API v3 key (default: YOUTUBE_API_KEY env var)")
    parser.add_argument("--min-views", type=int, default=0,
                        help="ignore Shorts below this many views")
    parser.add_argument("--max-duration", type=int, default=180,
                        help="max Short duration in seconds (default 180)")
    parser.add_argument("--output", default="reports",
                        help="directory for JSON reports (default reports/)")
    parser.add_argument("--lang", default="",
                        help="relevance language for requests; empty = entire world (default)")
    parser.add_argument("--language", default="non-hindi",
                        help="only keep videos matching this filter (default 'non-hindi': "
                             "exclude Hindi; 'all' keeps everything; 'en' keeps English)")
    parser.add_argument("--geo-country", default=None,
                        help="geo-bypass country code, e.g. US")
    parser.add_argument("--install-cron", action="store_true",
                        help="install a crontab entry for this agent")
    parser.add_argument("--cron-schedule", default="0 * * * *",
                        help="cron schedule used with --install-cron (default hourly)")
    parser.add_argument("--json-only", action="store_true",
                        help="suppress console table")
    parser.add_argument("--download", action="store_true",
                        help="download today's reported Shorts as MP4s and detect "
                             "each video's language with Whisper")
    parser.add_argument("--reports-dir", default="reports",
                        help="report directory read by --download (default reports/)")
    parser.add_argument("--download-dir", default="downloads",
                        help="MP4 output directory for --download (default downloads/)")
    parser.add_argument("--report-date", default=None,
                        help="YYYY-MM-DD to read reports for (default today)")
    parser.add_argument("--download-limit", type=int, default=None,
                        help="max videos to download (default all distinct)")
    parser.add_argument("--whisper-model", default="tiny",
                        help="whisper model for language detection (default tiny)")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser

def find_top(args: argparse.Namespace) -> AgentResult:
    scraper = ShortScraper(pool_size=args.pool_size, lang=args.lang,
                           geo_country=args.geo_country, api_key=args.api_key,
                           recent_hours=args.recent_hours)
    if args.source == "search" and args.search_queries.strip():
        queries = [q.strip() for q in args.search_queries.split(",") if q.strip()]
        if queries:
            scraper.search_queries = tuple(queries)
    entries, strategy = scraper.fetch(source=args.source)
    shorts = rank_pool(entries, top=args.top, max_duration=args.max_duration,
                       min_views=args.min_views, language=args.language)
    return AgentResult(top=shorts, pool_size=len(entries), strategy=strategy)


def _run_download(args: argparse.Namespace) -> int:
    from datetime import date
    from .downloader import run_download

    day = None
    if args.report_date:
        try:
            day = date.fromisoformat(args.report_date)
        except ValueError:
            logger.error("invalid --report-date %r (expected YYYY-MM-DD)",
                         args.report_date)
            return 2
    manifest = run_download(args.reports_dir, args.download_dir, day=day,
                            limit=args.download_limit,
                            model_name=args.whisper_model)
    out_dir = Path(args.download_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    import json as _json
    manifest_path.write_text(_json.dumps(manifest, indent=2, ensure_ascii=False))
    ok = sum(1 for r in manifest["results"] if r["file"])
    langs = sum(1 for r in manifest["results"] if r["language"])
    print(f"downloaded {ok}/{manifest['distinct_videos']} videos to {out_dir}")
    print(f"languages detected for {langs} files (manifest: {manifest_path})")
    for r in manifest["results"]:
        status = r["language"] or r["error"] or "skipped"
        print(f"  {r['video_id']}  {status}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.install_cron:
        _add_cron(args.cron_schedule, args.top, args.output, args.language)
        return 0
    if args.download:
        return _run_download(args)

    result = find_top(args)
    write_json(result, args.output, language=args.language)
    if not args.json_only:
        print_table(result)
    logger.info("strategy=%s pool=%d top=%d report=reports/latest.json",
                result.strategy, result.pool_size, len(result.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
