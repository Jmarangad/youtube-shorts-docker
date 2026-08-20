"""Run the LangGraph orchestration pipeline from the command line."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Optional

from . import __version__
from .graph import run_pipeline
from .state import PipelineConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youtube-shorts-orchestrator",
        description="LangGraph orchestrator for the youtube-shorts-docker agents.",
    )
    parser.add_argument("--once", action="store_true",
                        help="run a single cycle and exit")
    parser.add_argument("--interval", type=int, default=7200,
                        help="seconds between cycles (default 7200)")
    parser.add_argument("--top", type=int, default=8,
                        help="number of top Shorts for the trending agent")
    parser.add_argument("--language", default="non-hindi",
                        help="language filter for the trending agent")
    parser.add_argument("--recent-hours", type=int, default=24,
                        help="recency window for the trending agent")
    parser.add_argument("--whisper-model", default=None,
                        help="whisper model for downloader/dubber "
                             "(default: downloader tiny, dubber base)")
    parser.add_argument("--privacy", default="unlisted",
                        choices=["public", "unlisted", "private"],
                        help="YouTube privacy status for uploads")
    parser.add_argument("--dry-run", action="store_true",
                        help="pass --dry-run to the uploader (build metadata "
                             "but do not upload)")
    parser.add_argument("--reports-dir", default="/reports")
    parser.add_argument("--downloads-dir", default="/downloads")
    parser.add_argument("--dubbed-dir", default="/dubbed")
    parser.add_argument("--config-dir", default="/config")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser


def _config_from_env(args: argparse.Namespace) -> PipelineConfig:
    return PipelineConfig(
        reports_dir=args.reports_dir,
        downloads_dir=args.downloads_dir,
        dubbed_dir=args.dubbed_dir,
        config_dir=args.config_dir,
        top=args.top,
        language=args.language,
        recent_hours=args.recent_hours,
        whisper_model=args.whisper_model or "tiny",
        privacy=args.privacy,
        dry_run=args.dry_run,
        cookies=os.environ.get("YT_DLP_COOKIES") or None,
    )


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = _config_from_env(args)

    if args.once:
        state = run_pipeline(config)
        if state.download_errors or state.dub_errors or state.upload_errors:
            return 1
        return 0

    logger = logging.getLogger("orchestrator")
    logger.info("starting orchestrator loop; interval=%ss", args.interval)
    while True:
        try:
            run_pipeline(config)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            logger.error("cycle failed: %s", exc)
        logger.info("sleeping %ss", args.interval)
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())