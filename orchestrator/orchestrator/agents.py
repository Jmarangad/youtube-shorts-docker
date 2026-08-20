"""Thin wrappers that run each agent inside its own container via the Docker SDK."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import docker

from .state import PipelineConfig

logger = logging.getLogger("orchestrator.agents")

_client: docker.DockerClient | None = None


def client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def _exec(container: str, workdir: str, args: list[str]) -> tuple[int, str]:
    cmd = f"cd {workdir} && python -m {' '.join(args)}"
    logger.info("exec %s: %s", container, cmd)
    try:
        proc = client().containers.get(container)
        result = proc.exec_run(["sh", "-lc", cmd], tty=False)
        out = (result.output or "").decode("utf-8", "replace")
    except docker.errors.NotFound:
        logger.error("container %s not running; is compose up?", container)
        return 2, ""
    except Exception as exc:  # noqa: BLE001
        logger.error("docker exec %s failed: %s", container, exc)
        return 1, str(exc)
    for line in out.splitlines():
        logger.info("  out: %s", line)
    return result.exit_code or 0, out


def _count(path: str, pattern: str) -> int:
    p = Path(path)
    if not p.is_dir():
        return 0
    return len(list(p.glob(pattern)))


def report_count(cfg: PipelineConfig) -> int:
    return _count(cfg.reports_dir, "trending-shorts-*.json")


def run_trending(cfg: PipelineConfig) -> int:
    args = ["agent", "--top", str(cfg.top), "--language", cfg.language,
            "--recent-hours", str(cfg.recent_hours), "--output",
            cfg.reports_dir]
    return _exec(cfg.trending_container, "/app", args)[0]


def run_downloader(cfg: PipelineConfig) -> int:
    args = ["downloader", "--reports-dir", cfg.reports_dir,
            "--download-dir", cfg.downloads_dir,
            "--whisper-model", cfg.whisper_model]
    if cfg.cookies:
        args += ["--cookies", cfg.cookies]
    return _exec(cfg.downloader_container, "/app", args)[0]


def run_dubber(cfg: PipelineConfig) -> int:
    args = ["dubber", "--downloads-dir", cfg.downloads_dir,
            "--out-dir", cfg.dubbed_dir, "--whisper-model", cfg.whisper_model]
    return _exec(cfg.dubber_container, "/app", args)[0]


def run_uploader(cfg: PipelineConfig) -> int:
    args = ["uploader", "--dubbed-dir", cfg.dubbed_dir,
            "--reports-dir", cfg.reports_dir,
            "--downloads-dir", cfg.downloads_dir,
            "--config-dir", cfg.config_dir, "--privacy", cfg.privacy]
    if cfg.dry_run:
        args.append("--dry-run")
    return _exec(cfg.uploader_container, "/app", args)[0]


def downloaded_count(cfg: PipelineConfig) -> int:
    return _count(cfg.downloads_dir, "*.mp4")


def dubbed_count(cfg: PipelineConfig) -> int:
    return _count(cfg.dubbed_dir, "*.mp4")


def find_manifest(cfg: PipelineConfig, name: str) -> list[str]:
    """Return the list of youtube_ids recorded in a manifest file, if any."""
    import json

    p = Path(cfg.dubbed_dir) / name
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    uploaded = data.get("uploaded", {}) if isinstance(data, dict) else {}
    return [v for v in uploaded.values() if v and v != "DRY_RUN"]