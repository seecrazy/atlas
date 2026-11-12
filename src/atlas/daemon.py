"""Foreground incremental indexing loop."""

from __future__ import annotations

import time

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
