"""Dashboard backend: aggregates agent status + activity for the web UI."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template

ROOT = Path(__file__).resolve().parent
REPORTS = Path(os.environ.get("REPORTS_DIR", "/reports"))
DOWNLOADS = Path(os.environ.get("DOWNLOADS_DIR", "/downloads"))
DUBBED = Path(os.environ.get("DUBBED_DIR", "/dubbed"))
MOVIE_SHORTS_OUT = Path(os.environ.get("MOVIE_SHORTS_OUT", "/movie-shorts/output"))

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


def _movie_shorts_summary() -> dict:
    """Status of the movie-shorts-agent (reads its bind-mounted output dir)."""
    plan = _read_json(MOVIE_SHORTS_OUT / "story_plan.json")
    used = _read_json(MOVIE_SHORTS_OUT / "used_movies.json")
    used_count = len(used.get("video_ids", [])) if isinstance(used, dict) else 0
    backups_dir = MOVIE_SHORTS_OUT / "backups"
    backups = sorted(backups_dir.glob("*")) if backups_dir.exists() else []
    latest_at = None
    if backups:
        latest_at = _iso(backups[-1].stat().st_mtime)
    elif plan:
        try:
            latest_at = _iso((MOVIE_SHORTS_OUT / "story_plan.json").stat().st_mtime)
        except OSError:
            pass
    final = MOVIE_SHORTS_OUT / "final_short.mp4"
    return {
        "latest_at": latest_at,
        "source_video_id": (plan or {}).get("source_video_id"),
        "scenes": len((plan or {}).get("timestamps", [])),
        "used_count": used_count,
        "backups": len(backups),
        "final_size": final.stat().st_size if final.exists() else None,
    }


# --- schedule / next-execution helpers ---------------------------------------
def _docker_client():
    try:
        import docker

        return docker.from_env()
    except Exception:
        return None


def _cron_ints(field: str, allowed: range) -> set[int]:
    """Expand a single cron field (``*``, ``5``, ``1,15``, ``*/15``, ``0-30``)."""
    field = field.strip()
    if field == "*":
        return set(allowed)
    values: set[int] = set()
    for part in field.split(","):
        if "/" in part:
            base, step = part.split("/", 1)
            step = max(int(step), 1)
            if base in ("", "*"):
                values.update(allowed[::step])
            else:
                values.update(sorted(_cron_ints(base, allowed))[::step])
        elif "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            values.update(range(lo, hi + 1))
        else:
            values.add(int(part))
    return {v for v in values if v in allowed}


def _next_cron(fields: list[str], tz_name: str) -> str | None:
    """Next occurrence of a 5-field cron expression in the given timezone."""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    minutes = _cron_ints(fields[0], range(0, 60))
    hours = _cron_ints(fields[1], range(0, 24))
    now = datetime.now(tz)
    for days_ahead in range(0, 367):
        day = now + timedelta(days=days_ahead)
        for hour in sorted(hours):
            for minute in sorted(minutes):
                cand = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if cand > now:
                    return _iso(cand.timestamp())
    return None


def _read_cron_fields(client, container_name: str, cron_file: str, default: list[str]) -> list[str]:
    """Read the 5 cron fields from the container's cron file, if reachable."""
    if client is None:
        return default
    try:
        container = client.containers.get(container_name)
        res = container.exec_run(["cat", cron_file])
        if res.exit_code == 0:
            for line in res.output.decode(errors="replace").splitlines():
                parts = line.split()
                if parts and not parts[0].startswith("#") and len(parts) >= 5:
                    return parts[:5]
    except Exception:
        pass
    return default


def _container_log(client, container_name: str, lines: int = 30) -> list[str]:
    if client is None:
        return []
    try:
        logs = client.containers.get(container_name).logs(tail=lines).decode(errors="replace")
        return logs.splitlines()
    except Exception:
        return []


def _next_from_log(client, container_name: str, interval_hours: float, started_at: str | None) -> str | None:
    """Next run for a self-scheduled agent, from its ``next run in X h`` logs."""
    if client is None:
        return None
    try:
        logs = client.containers.get(container_name).logs(tail=4000).decode(errors="replace")
    except Exception:
        logs = ""
    last = None
    for line in logs.splitlines():
        match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*next run in ([0-9.]+) h", line)
        if match:
            last = match
    if last:
        try:
            dt = datetime.strptime(last.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            nxt = dt + timedelta(hours=float(last.group(2)))
        except Exception:
            nxt = None
        if nxt:
            # A run may be in progress past the logged start; roll forward.
            while nxt <= _utcnow():
                nxt += timedelta(hours=interval_hours)
            return _iso(nxt.timestamp())
    # Fallback: container start + interval (close enough before a run completes).
    if started_at:
        try:
            dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            nxt = dt + timedelta(hours=interval_hours)
            while nxt <= _utcnow():
                nxt += timedelta(hours=interval_hours)
            return _iso(nxt.timestamp())
        except Exception:
            return None
    return None


AGENTS = [
    {"name": "trending-agent", "container": "youtube-trending-agent",
     "log_file": "/var/log/agent.log", "summary": _trending_summary,
     "cron_file": "/etc/cron.d/agent", "cron_default": ["0", "*/2", "*", "*", "*"]},
    {"name": "downloader", "container": "youtube-shorts-downloader",
     "log_file": "/var/log/downloader.log", "summary": _downloader_summary,
     "cron_file": "/etc/cron.d/downloader", "cron_default": ["10", "*/2", "*", "*", "*"]},
    {"name": "dubber", "container": "youtube-shorts-dubber",
     "log_file": "/var/log/dubber.log", "summary": _dubber_summary,
     "cron_file": "/etc/cron.d/dubber", "cron_default": ["25", "*/2", "*", "*", "*"]},
    {"name": "uploader", "container": "youtube-shorts-uploader",
     "log_file": "/var/log/uploader.log", "summary": _uploader_summary,
     "cron_file": "/etc/cron.d/uploader", "cron_default": ["45", "*/2", "*", "*", "*"]},
    {"name": "movie-shorts-agent", "container": "movie-shorts-agent",
     "log_file": "", "summary": _movie_shorts_summary,
     "interval_hours": 3.0},
]

_AGENT_TZ = "Asia/Kolkata"


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


def _schedule_label(fields: list[str]) -> str:
    """Human-readable label for a 5-field cron expression."""
    if len(fields) < 2:
        return ""
    minute, hour = fields[0], fields[1]
    if hour == "*/2":
        return f"every 2 h at :{minute} IST"
    if hour == "*":
        return f"hourly at :{minute} IST"
    if hour.isdigit():
        return f"daily {int(hour):02d}:{int(minute):02d} IST"
    return f"cron {minute} {hour} * * *"


def _agent_card(agent: dict) -> dict:
    client = _docker_client()
    container = _container_status(agent["container"])
    summary = agent["summary"]()
    log = _read_log(agent["log_file"]) or _container_log(client, agent["container"])

    last_ts = summary.get("latest_at")
    status = "unknown"
    if last_ts:
        try:
            dt = datetime.fromisoformat(last_ts)
            age = (_utcnow() - dt).total_seconds()
            status = "ok" if age < 3600 * 26 else "stale"
        except Exception:
            status = "unknown"

    next_execution = None
    schedule = ""
    if "cron_file" in agent:
        fields = _read_cron_fields(client, agent["container"], agent["cron_file"], agent["cron_default"])
        next_execution = _next_cron(fields, _AGENT_TZ)
        schedule = _schedule_label(fields)
    elif "interval_hours" in agent:
        started = (container or {}).get("started_at")
        next_execution = _next_from_log(client, agent["container"], agent["interval_hours"], started)
        schedule = f"every {agent['interval_hours']:g} h"

    return {
        "name": agent["name"],
        "container": agent["container"],
        "container_status": container,
        "schedule": schedule,
        "last_execution": last_ts,
        "next_execution": next_execution,
        "status": status,
        "summary": summary,
        "recent_log": log,
    }


def _overview(cards: list[dict]) -> dict:
    """Aggregate pipeline health + totals for the dashboard header."""
    total = len(cards)
    running = sum(1 for c in cards if (c.get("container_status") or {}).get("running"))
    ok = sum(1 for c in cards if c.get("status") == "ok")
    stale = sum(1 for c in cards if c.get("status") == "stale")
    bad = sum(1 for c in cards if c.get("status") == "bad")
    totals = {"downloaded": 0, "dubbed": 0, "uploaded": 0,
              "pool_size": None, "backups": 0, "used": 0, "scenes": 0}
    for c in cards:
        s = c.get("summary") or {}
        totals["downloaded"] += int(s.get("downloaded") or 0)
        totals["dubbed"] += int(s.get("dubbed") or 0)
        totals["uploaded"] += int(s.get("uploaded_count") or 0)
        totals["backups"] += int(s.get("backups") or 0)
        totals["used"] += int(s.get("used_count") or 0)
        totals["scenes"] += int(s.get("scenes") or 0)
        if totals["pool_size"] is None:
            totals["pool_size"] = s.get("pool_size")
    health = "ok"
    if bad or (stale and not running):
        health = "degraded"
    if bad and not ok:
        health = "down"
    return {"total": total, "running": running, "ok": ok, "stale": stale,
            "bad": bad, "health": health, "totals": totals}


@app.route("/api/agents")
def api_agents():
    cards = []
    for agent in AGENTS:
        try:
            cards.append(_agent_card(agent))
        except Exception as exc:
            cards.append({"name": agent["name"], "error": str(exc)})
    return jsonify({"agents": cards, "overview": _overview(cards),
                    "generated_at": _utcnow().isoformat()})


@app.route("/")
def index():
    return render_template("index.html")


def main() -> None:
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "80")), threaded=True)


if __name__ == "__main__":
    main()