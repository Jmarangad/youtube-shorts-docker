"""CLI entrypoint for the Shorts downloader."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from . import __version__
from .core import run_download

logger = logging.getLogger("downloader.cli")

_CRON_TEMPLATE = ("{schedule} cd {cwd} && {python} -m downloader "
                  "--reports-dir {reports} --download-dir {out}")


def _add_cron(schedule: str, reports_dir: str, download_dir: str) -> bool:
    cwd = shlex.quote(str(Path.cwd()))
    python = shlex.quote(sys.executable)
    reports = shlex.quote(str(Path(reports_dir).resolve()))
    out = shlex.quote(str(Path(download_dir).resolve()))
    line = _CRON_TEMPLATE.format(schedule=schedule, cwd=cwd, python=python,
                                 reports=reports, out=out)
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
        prog="youtube-shorts-downloader",
        description="Download the day's trending Shorts as MP4s and detect "
                    "each video's language with Whisper.",
    )
    parser.add_argument("--reports-dir", default="reports",
                        help="directory of youtube-trending-agent JSON reports")
    parser.add_argument("--download-dir", default="downloads",
                        help="MP4 output directory (default downloads/)")
    parser.add_argument("--report-date", default=None,
                        help="YYYY-MM-DD to read reports for (default today)")
    parser.add_argument("--download-limit", type=int, default=None,
                        help="max videos to download (default all distinct)")
    parser.add_argument("--whisper-model", default="tiny",
                        help="whisper model for language detection (default tiny)")
    parser.add_argument("--cookies", default=None,
                        help="path to a yt-dlp cookies.txt file (or set YT_DLP_COOKIES)")
    parser.add_argument("--install-cron", action="store_true",
                        help="install a daily (23:30) crontab entry")
    parser.add_argument("--cron-schedule", default="30 23 * * *",
                        help="cron schedule used with --install-cron (default daily 23:30)")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.install_cron:
        return 0 if _add_cron(args.cron_schedule, args.reports_dir,
                              args.download_dir) else 1

    day = None
    if args.report_date:
        try:
            day = date.fromisoformat(args.report_date)
        except ValueError:
            logger.error("invalid --report-date %r (expected YYYY-MM-DD)",
                         args.report_date)
            return 2

    if args.cookies:
        os.environ["YT_DLP_COOKIES"] = args.cookies

    manifest = run_download(args.reports_dir, args.download_dir, day=day,
                            limit=args.download_limit,
                            model_name=args.whisper_model)
    out_dir = Path(args.download_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    ok = sum(1 for r in manifest["results"] if r["file"])
    langs = sum(1 for r in manifest["results"] if r["language"])
    print(f"downloaded {ok}/{manifest['distinct_videos']} videos to {out_dir}")
    print(f"languages detected for {langs} files (manifest: {manifest_path})")
    for r in manifest["results"]:
        status = r["language"] or r["error"] or "skipped"
        print(f"  {r['video_id']}  {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())