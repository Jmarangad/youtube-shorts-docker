"""Dashboard backend: aggregates agent status + activity for the web UI."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template

ROOT = Path(__file__).resolve().parent
REPORTS = Path(os.environ.get("REPORTS_DIR", "/reports"))
DOWNLOADS = Path(os.environ.get("DOWNLOADS_DIR", "/downloads"))
DUBBED = Path(os.environ.get("DUBBED_DIR", "/dubbed"))

_RE_DUR = re.compile(r"(\d+):(\d+):(\d+)")

app = Flask(__name__)


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _parse_duration(s: str) -> int:
    m = _RE_DUR.search(s or "")
    if not m:
        return 0
    h, mm, sec = (int(x) for x in m.groups())
    return h * 3600 + mm * 60 + sec


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path) -> dict | None:
    try:
        if path.exists() and path.stat().st_size:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _trending_summary() -> dict:
    latest = _read_json(REPORTS / "latest.json")
    files = sorted(REPORTS.glob("trending-shorts-*.json")) if REPORTS.exists() else []
    last_ts = None
    if files:
        mtime = files[-1].stat().st_mtime
        last_ts = _iso(mtime)
    pool = latest.get("pool_size") if latest else None
    top = latest.get("top", []) if latest else []
    return {
        "latest_at": last_ts,
        "pool_size": pool,
        "top_count": len(top),
        "top_titles": [t.get("title") for t in top],
    }


def _downloader_summary() -> dict:
    m = _read_json(DOWNLOADS / "manifest.json")
    gen = m.get("generated_at") if m else None
    results = m.get("results", []) if m else []
    ok = [r for r in results if r.get("file")]
    err = [r for r in results if r.get("error")]
    return {
        "latest_at": gen,
        "distinct_videos": m.get("distinct_videos") if m else None,
        "downloaded": len(ok),
        "failed": len(err),
        "errors": [r["error"] for r in err],
        "languages": {r.get("language"): 1 for r in ok},
    }


def _dubber_summary() -> dict:
    m = _read_json(DUBBED / "dub-manifest.json")
    gen = m.get("generated_at") if m else None
    results = m.get("results", []) if m else []
    dubbed = [r for r in results if r.get("out")]
    skipped = [r for r in results if not r.get("out") and not r.get("error")]
    err = [r for r in results if r.get("error")]
    segs = sum(int(r.get("segments") or 0) for r in dubbed)
    return {
        "latest_at": gen,
        "videos_found": m.get("videos_found") if m else None,
        "dubbed": len(dubbed),
        "skipped": len(skipped),
        "failed": len(err),
        "segments": segs,
        "errors": [r["error"] for r in err],
    }


def _uploader_summary() -> dict:
    m = _read_json(DUBBED / "upload-manifest.json")
    gen = m.get("generated_at") if m else None
    uploaded = m.get("uploaded", {}) if m else {}
    uploaded_count = m.get("uploaded_count") if m else len(uploaded)
    return {
        "latest_at": gen,
        "uploaded_count": uploaded_count,
        "uploaded": [Path(k).name for k in uploaded.keys()],
    }


AGENTS = [
    {"name": "trending-agent", "container": "youtube-trending-agent",
     "log_file": "/var/log/agent.log", "summary": _trending_summary},
    {"name": "downloader", "container": "youtube-shorts-downloader",
     "log_file": "/var/log/downloader.log", "summary": _downloader_summary},
    {"name": "dubber", "container": "youtube-shorts-dubber",
     "log_file": "/var/log/dubber.log", "summary": _dubber_summary},
    {"name": "uploader", "container": "youtube-shorts-uploader",
     "log_file": "/var/log/uploader.log", "summary": _uploader_summary},
]


def _container_status(container_name: str) -> dict | None:
    try:
        import docker
    except Exception:
        return None
    try:
        client = docker.from_env()
        c = client.containers.get(container_name)
        status = c.status
        started = c.attrs.get("State", {}).get("StartedAt") or None
        finished = c.attrs.get("State", {}).get("FinishedAt") or None
        return {"running": status == "running", "status": status, "started_at": started,
                "finished_at": finished}
    except Exception:
        return None


def _read_log(path: str, lines: int = 30) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readlines()[-lines:]
    except Exception:
        return []


def _agent_card(agent: dict) -> dict:
    container = _container_status(agent["container"])
    summary = agent["summary"]()
    log = _read_log(agent["log_file"])

    last_ts = summary.get("latest_at")
    status = "unknown"
    if last_ts:
        try:
            dt = datetime.fromisoformat(last_ts)
            age = (_utcnow() - dt).total_seconds()
            status = "ok" if age < 3600 * 26 else "stale"
        except Exception:
            status = "unknown"

    return {
        "name": agent["name"],
        "container": agent["container"],
        "container_status": container,
        "last_execution": last_ts,
        "status": status,
        "summary": summary,
        "recent_log": log,
    }


@app.route("/api/agents")
def api_agents():
    cards = []
    for agent in AGENTS:
        try:
            cards.append(_agent_card(agent))
        except Exception as exc:
            cards.append({"name": agent["name"], "error": str(exc)})
    return jsonify({"agents": cards, "generated_at": _utcnow().isoformat()})


@app.route("/")
def index():
    return render_template("index.html")


def main() -> None:
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "80")), threaded=True)


if __name__ == "__main__":
    main()