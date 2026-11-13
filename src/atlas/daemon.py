"""Foreground incremental indexing loop."""

from __future__ import annotations

import time
from pathlib import Path

from .config import ProjectConfig
from .indexer import index_project


def run_daemon(
    config: ProjectConfig,
    *,
    interval: float = 5.0,
    max_iterations: int | None = None,
    full: bool = False,
    include_workspace: bool = True,
    on_cycle=None,
) -> dict:
    if interval < 0:
        raise ValueError("daemon interval must be greater than or equal to zero")
    if max_iterations is not None and max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    cycles = []
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        started = time.time()
        try:
            stats = index_project(
                config,
                full=full,
                include_workspace=include_workspace,
            )
            error = None
        except Exception as exc:
            stats = None
            error = str(exc)
        cycle = {
            "iteration": iteration + 1,
            "duration_seconds": round(time.time() - started, 3),
            "stats": stats,
            "error": error,
        }
        cycles.append(cycle)
        if on_cycle:
            on_cycle(cycle)
        iteration += 1
        if max_iterations is None or iteration < max_iterations:
            time.sleep(interval)
    return {"cycles": cycles, "iteration_count": iteration}


def run_watch_daemon(
    config: ProjectConfig,
    *,
    debounce_ms: int = 300,
    max_events: int | None = None,
    full: bool = False,
    include_workspace: bool = True,
    on_cycle=None,
) -> dict:
    """Use OS-level file notifications through optional watchfiles dependency."""
    try:
        from watchfiles import watch
    except ImportError as exc:
        raise RuntimeError(
            "OS event watching requires watchfiles. Install with: pip install 'atlas-project-knowledge[watch]'"
        ) from exc

    paths = [config.project_root]
    events_seen = 0
    cycles = []
    for changes in watch(*paths, debounce=int(debounce_ms), step=50, rust_timeout=5000):
        if not changes:
            continue
        events_seen += len(changes)
        started = time.time()
        try:
            stats = index_project(
                config,
                full=full,
                include_workspace=include_workspace,
            )
            error = None
        except Exception as exc:
            stats = None
            error = str(exc)
        cycle = {
            "event_count": len(changes),
            "events": [{"change": kind.value, "path": str(path)} for kind, path in changes],
            "duration_seconds": round(time.time() - started, 3),
            "stats": stats,
            "error": error,
        }
        cycles.append(cycle)
        if on_cycle:
            on_cycle(cycle)
        if max_events is not None and events_seen >= max_events:
            break
    return {"cycles": cycles, "event_count": events_seen}
