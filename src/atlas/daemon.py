"""Foreground and OS-event incremental indexing loops."""

from __future__ import annotations

import time
from pathlib import Path

from .config import ProjectConfig
from .indexer import index_project
from .operations import (
    acquire_daemon_lock,
    heartbeat_daemon_lock,
    new_trace_id,
    record_metric,
    release_daemon_lock,
)


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
    lock_token = acquire_daemon_lock(config)
    cycles = []
    iteration = 0
    consecutive_failures = 0
    current_interval = max(0.0, float(interval))
    try:
        while max_iterations is None or iteration < max_iterations:
            trace_id = new_trace_id()
            started = time.perf_counter()
            status = "completed"
            try:
                stats = index_project(
                    config,
                    full=full,
                    include_workspace=include_workspace,
                )
                error = None
                consecutive_failures = 0
                current_interval = max(0.0, float(interval))
            except Exception as exc:
                stats = None
                error = str(exc)
                status = "failed"
                consecutive_failures += 1
                # Exponential backoff is capped at five minutes.
                current_interval = min(300.0, max(interval, 2.0 ** min(consecutive_failures, 9)))
            duration_ms = (time.perf_counter() - started) * 1000
            heartbeat_daemon_lock(config, lock_token)
            cycle = {
                "trace_id": trace_id,
                "iteration": iteration + 1,
                "duration_seconds": round(time.perf_counter() - started, 3),
                "stats": stats,
                "error": error,
                "consecutive_failures": consecutive_failures,
                "next_interval_seconds": round(current_interval, 3),
            }
            cycles.append(cycle)
            if on_cycle:
                on_cycle(cycle)
            from .db import connect
            conn = connect(config.db_path)
            try:
                record_metric(
                    conn,
                    trace_id,
                    "daemon.cycle",
                    actor="daemon",
                    status=status,
                    duration_ms=duration_ms,
                    result_count=int((stats or {}).get("files_changed", 0)) if stats else 0,
                    payload={
                        "iteration": iteration + 1,
                        "mode": (stats or {}).get("mode"),
                        "error": error,
                        "consecutive_failures": consecutive_failures,
                    },
                )
                conn.commit()
            finally:
                conn.close()
            iteration += 1
            if max_iterations is None or iteration < max_iterations:
                time.sleep(current_interval)
        return {"cycles": cycles, "iteration_count": iteration}
    finally:
        release_daemon_lock(config, lock_token)


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

    lock_token = acquire_daemon_lock(config)
    paths = [config.project_root]
    events_seen = 0
    cycles = []
    try:
        for changes in watch(*paths, debounce=int(debounce_ms), step=50, rust_timeout=5000):
            if not changes:
                continue
            trace_id = new_trace_id()
            events_seen += len(changes)
            started = time.perf_counter()
            status = "completed"
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
                status = "failed"
            duration_ms = (time.perf_counter() - started) * 1000
            heartbeat_daemon_lock(config, lock_token)
            cycle = {
                "trace_id": trace_id,
                "event_count": len(changes),
                "events": [{"change": kind.value, "path": str(path)} for kind, path in changes],
                "duration_seconds": round(time.perf_counter() - started, 3),
                "stats": stats,
                "error": error,
            }
            cycles.append(cycle)
            if on_cycle:
                on_cycle(cycle)
            from .db import connect
            conn = connect(config.db_path)
            record_metric(
                conn,
                trace_id,
                "daemon.watch_cycle",
                actor="daemon",
                status=status,
                duration_ms=duration_ms,
                result_count=len(changes),
                payload={"mode": (stats or {}).get("mode"), "error": error},
            )
            conn.close()
            if max_events is not None and events_seen >= max_events:
                break
        return {"cycles": cycles, "event_count": events_seen}
    finally:
        release_daemon_lock(config, lock_token)
