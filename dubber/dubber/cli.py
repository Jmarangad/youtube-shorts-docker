"""CLI entrypoint for the Hindi dubber."""

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
from .core import run_dub

logger = logging.getLogger("dubber.cli")

_CRON_TEMPLATE = ("{schedule} cd {cwd} && {python} -m dubber "
                  "--downloads-dir {downloads} --out-dir {out}")


def _add_cron(schedule: str, downloads_dir: str, out_dir: str) -> bool:
    cwd = shlex.quote(str(Path.cwd()))
    python = shlex.quote(sys.executable)
    downloads = shlex.quote(str(Path(downloads_dir).resolve()))
    out = shlex.quote(str(Path(out_dir).resolve()))
    line = _CRON_TEMPLATE.format(schedule=schedule, cwd=cwd, python=python,
                                 downloads=downloads, out=out)
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
        prog="youtube-shorts-dubber",
        description="Analyze downloaded Shorts and re-dub their voice track in Hindi.",
    )
    parser.add_argument("--downloads-dir", default="downloads",
                        help="directory of downloaded MP4s")
    parser.add_argument("--out-dir", default="dubbed",
                        help="output directory for Hindi-dubbed videos")
    parser.add_argument("--limit", type=int, default=None,
                        help="max videos to dub (default all)")
    parser.add_argument("--whisper-model", default="base",
                        help="whisper model for transcription (default base)")
    parser.add_argument("--voice", default="hi-IN-MadhurNeural",
                        help="deprecated single voice; use the per-gender flags")
    parser.add_argument("--male-voice", default=None,
                        help="edge-tts Hindi voice for male segments "
                             "(default hi-IN-MadhurNeural)")
    parser.add_argument("--female-voice", default=None,
                        help="edge-tts Hindi voice for female segments "
                             "(default hi-IN-SwaraNeural)")
    parser.add_argument("--kids-voice", default=None,
                        help="edge-tts voice for undetermined-gender segments "
                             "(default hi-IN-SwaraNeural pitched up)")
    parser.add_argument("--clean", action="store_true",
                        help="delete previously dubbed videos before dubbing")
    parser.add_argument("--install-cron", action="store_true",
                        help="install a daily (23:40) crontab entry")
    parser.add_argument("--cron-schedule", default="40 23 * * *",
                        help="cron schedule used with --install-cron (default daily 23:40)")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.install_cron:
        return 0 if _add_cron(args.cron_schedule, args.downloads_dir,
                              args.out_dir) else 1

    voice_overrides = {
        "male": args.male_voice,
        "female": args.female_voice,
        "kids": args.kids_voice,
    }
    manifest = run_dub(args.downloads_dir, args.out_dir, limit=args.limit,
                       model_name=args.whisper_model, clean=args.clean,
                       voice_overrides=voice_overrides)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dub-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False))
    done = sum(1 for r in manifest["results"]
               if r["out"] and r["error"] is None and r["segments"] > 0)
    skipped = sum(1 for r in manifest["results"]
                  if r["out"] and r["error"] is None and r["segments"] == 0)
    print(f"dubbed {done} new video(s), {skipped} already done, "
          f"{manifest['videos_found']} total found")
    for r in manifest["results"]:
        status = r["out"] or r["error"] or "no speech"
        print(f"  {r['video_id']}  {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())