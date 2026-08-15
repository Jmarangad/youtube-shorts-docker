"""CLI entrypoint for the YouTube Shorts uploader."""

from __future__ import annotations

import argparse
import json
import logging
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .core import run_upload

logger = logging.getLogger("uploader.cli")

_CRON_TEMPLATE = ("{schedule} cd {cwd} && {python} -m uploader "
                  "--dubbed-dir {dubbed} --reports-dir {reports} "
                  "--downloads-dir {downloads} --config-dir {config} "
                  "--privacy {privacy}")


def _add_cron(schedule: str, dubbed: str, reports: str, downloads: str,
              config: str, privacy: str) -> bool:
    cwd = shlex.quote(str(Path.cwd()))
    python = shlex.quote(sys.executable)
    line = _CRON_TEMPLATE.format(
        schedule=schedule, cwd=cwd, python=python,
        dubbed=shlex.quote(str(Path(dubbed).resolve())),
        reports=shlex.quote(str(Path(reports).resolve())),
        downloads=shlex.quote(str(Path(downloads).resolve())),
        config=shlex.quote(str(Path(config).resolve())),
        privacy=privacy)
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
        prog="youtube-shorts-uploader",
        description="Upload Hindi-dubbed Shorts to YouTube.")
    parser.add_argument("--dubbed-dir", default="dubbed",
                        help="directory of dubbed MP4s")
    parser.add_argument("--reports-dir", default="reports",
                        help="trending agent reports (for hashtags/title)")
    parser.add_argument("--downloads-dir", default="downloads",
                        help="downloader dir (to map file -> video id)")
    parser.add_argument("--config-dir", default="config",
                        help="dir with client_secret.json + token.json")
    parser.add_argument("--privacy", default="unlisted",
                        choices=["public", "unlisted", "private"],
                        help="YouTube privacy status (default unlisted)")
    parser.add_argument("--limit", type=int, default=None,
                        help="max videos to upload (default all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="build metadata and log, but do not upload")
    parser.add_argument("--install-cron", action="store_true",
                        help="install a daily (23:50) crontab entry")
    parser.add_argument("--cron-schedule", default="50 23 * * *",
                        help="cron schedule used with --install-cron")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.install_cron:
        return 0 if _add_cron(args.cron_schedule, args.dubbed_dir,
                              args.reports_dir, args.downloads_dir,
                              args.config_dir, args.privacy) else 1

    manifest = run_upload(
        args.dubbed_dir, reports_dir=args.reports_dir,
        downloads_dir=args.downloads_dir, config_dir=args.config_dir,
        privacy=args.privacy, dry_run=args.dry_run, limit=args.limit)

    done = sum(1 for r in manifest["results"] if r.get("youtube_id"))
    failed = sum(1 for r in manifest["results"] if r.get("error"))
    mode = "dry-run" if args.dry_run else "upload"
    print(f"{mode}: {done} uploaded, {failed} failed, "
          f"{manifest['videos_found']} found")
    for r in manifest["results"]:
        status = r.get("youtube_id") or r.get("error") or "skipped"
        print(f"  {r['file']}  {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
