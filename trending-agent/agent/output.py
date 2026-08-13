"""Console and JSON output for agent results."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import AgentResult, Short

_URL_RE = re.compile(r"(https?://\S+)")


def _strip_urls(text: str) -> str:
    return _URL_RE.sub("", text).strip()


def print_table(result: AgentResult) -> None:
    shorts = result.top
    if not shorts:
        print("No trending Shorts found.")
        return
    width = max(len(_strip_urls(s.title)) for s in shorts) + 2
    header = f"{'#':>2}  {'Views':>14}  {'Title':<{width}}  Channel"
    print(header)
    print("-" * (len(header) + 8))
    for short in shorts:
        title = _strip_urls(short.title)
        print(f"{short.rank:>2}  {short.view_count:>14,}  {title:<{width}}  {short.channel}")
    print()
    for short in shorts:
        print(f"[{short.rank}] {short.title} — {short.channel}")
        print(f"    {short.url}")


def write_json(result: AgentResult, output_dir: str | Path,
               language: str = "all") -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    report = {
        "generated_at": now.isoformat(),
        "strategy": result.strategy,
        "language_filter": language,
        "pool_size": result.pool_size,
        "top_count": len(result.top),
        "errors": result.errors,
        "top": [s.to_dict() for s in result.top],
    }
    latest = out / "latest.json"
    latest.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    stamped = out / f"trending-shorts-{now:%Y%m%d-%H%M%S}.json"
    stamped.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return latest


def maybe_print_short(url: str) -> None:
    print(f"\nOpening: {url}")
