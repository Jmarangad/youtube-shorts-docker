"""LangGraph orchestration of the trending -> download -> dub -> upload pipeline.

Each node runs the matching agent inside its own container via docker exec,
then routes based on how many artifacts actually changed on the shared volumes:
  discover   -> run trending agent, write fresh report
  download   -> fetch videos listed in reports (only NEW ones)
  dub        -> dub any freshly downloaded videos
  upload     -> upload any freshly dubbed videos
Conditional edges skip downstream stages when there is nothing new to do,
so an idle tick is cheap (discover + download "skipped" only).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import StateGraph as StateGraphType  # noqa: F401

from . import agents
from .state import PipelineConfig, PipelineState

logger = logging.getLogger("orchestrator.graph")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------

def discover_node(state: PipelineState) -> dict:
    rc = agents.run_trending(state.config)
    reports = agents.report_count(state.config)
    logger.info("discover rc=%d reports=%d", rc, reports)
    return {"started_at": _now(), "reports_written": reports}


def download_node(state: PipelineState) -> dict:
    before = agents.downloaded_count(state.config)
    rc = agents.run_downloader(state.config)
    after = agents.downloaded_count(state.config)
    errors = list(state.download_errors)
    if rc != 0:
        errors.append(f"downloader exited {rc}")
    new = max(0, after - before)
    logger.info("download rc=%d new_videos=%d", rc, new)
    return {"videos_downloaded": new, "download_errors": errors}


def dub_node(state: PipelineState) -> dict:
    before = agents.dubbed_count(state.config)
    rc = agents.run_dubber(state.config)
    after = agents.dubbed_count(state.config)
    errors = list(state.dub_errors)
    if rc != 0:
        errors.append(f"dubber exited {rc}")
    new = max(0, after - before)
    logger.info("dub rc=%d new_dubbed=%d", rc, new)
    return {"videos_dubbed": new, "dub_errors": errors}


def upload_node(state: PipelineState) -> dict:
    rc = agents.run_uploader(state.config)
    errors = list(state.upload_errors)
    if rc != 0:
        errors.append(f"uploader exited {rc}")
    uploaded = len(agents.find_manifest(state.config, "upload-manifest.json"))
    logger.info("upload rc=%d uploaded_entries=%d", rc, uploaded)
    return {"videos_uploaded": uploaded, "upload_errors": errors,
            "finished_at": _now()}


# --------------------------------------------------------------------------
# Conditional routing
# --------------------------------------------------------------------------

def _route_download(state: PipelineState) -> Literal["download", "end"]:
    return "download" if state.reports_written else "end"


def _route_dub(state: PipelineState) -> Literal["dub", "end"]:
    return "dub" if state.videos_downloaded > 0 else "end"


def _route_upload(state: PipelineState) -> Literal["upload", "end"]:
    return "upload" if state.videos_dubbed > 0 else "end"


# --------------------------------------------------------------------------
# Graph construction
# --------------------------------------------------------------------------

def build_graph():
    builder = StateGraph(PipelineState)
    builder.add_node("discover", discover_node)
    builder.add_node("download", download_node)
    builder.add_node("dub", dub_node)
    builder.add_node("upload", upload_node)

    builder.add_edge(START, "discover")
    builder.add_conditional_edges("discover", _route_download,
                                  {"download": "download", "end": END})
    builder.add_conditional_edges("download", _route_dub,
                                  {"dub": "dub", "end": END})
    builder.add_conditional_edges("dub", _route_upload,
                                  {"upload": "upload", "end": END})
    builder.add_edge("upload", END)
    return builder.compile()


def run_pipeline(config: PipelineConfig | None = None) -> PipelineState:
    """Execute one full orchestration cycle and return the final state."""
    config = config or PipelineConfig()
    graph = build_graph()
    initial = PipelineState(config=config)
    raw = graph.invoke(initial)
    if isinstance(raw, PipelineState):
        final = raw
    else:
        final = PipelineState(config=config)
        for key, value in raw.items():
            if hasattr(final, key):
                setattr(final, key, value)
    logger.info("pipeline done: reports=%d downloads=%d dubbed=%d uploaded=%d",
                final.reports_written, final.videos_downloaded,
                final.videos_dubbed, final.videos_uploaded)
    return final