"""Minimal HTTP JSON-RPC server for ATLAS MCP-compatible tools."""

from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import load_config
from .mcp import McpServer


class AtlasHTTPHandler(BaseHTTPRequestHandler):
    server_version = "ATLAS/0.2"
    max_body_bytes = 1_000_000
    requests_per_minute = 240

    def _auth_ok(self) -> bool:
        expected = os.environ.get("ATLAS_HTTP_TOKEN")
        if not expected:
            return True
        authorization = self.headers.get("Authorization", "")
        return authorization == f"Bearer {expected}"

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _rate_limited(self) -> bool:
        bucket = getattr(self.server, "atlas_rate_bucket", None)
        if bucket is None:
            bucket = {}
            self.server.atlas_rate_bucket = bucket
        now = time.monotonic()
        window_start = now - 60.0
        client = self.headers.get("X-Forwarded-For", self.client_address[0])
        history = [item for item in bucket.get(client, []) if item >= window_start]
        history.append(now)
        bucket[client] = history
        return len(history) > self.requests_per_minute

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send_json(404, {"error": "not found"})
            return
        self._send_json(200, {"status": "ok", "server": "atlas-http"})

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self._send_json(404, {"error": "not found"})
            return
        if not self._auth_ok():
            self._send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > self.max_body_bytes:
                self._send_json(
                    413,
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request too large"}},
                )
                return
        except ValueError as exc:
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": str(exc)}})
            return
        if self._rate_limited():
            self._send_json(
                429,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "rate limit exceeded"}},
            )
            return
        try:
            message = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})
            return
        if not isinstance(message, dict):
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}})
            return
        response = self.server.atlas.handle(message)  # type: ignore[attr-defined]
        self._send_json(200, response or {"jsonrpc": "2.0", "id": message.get("id"), "result": {}})


def run_http_server(project_root: str, host: str = "127.0.0.1", port: int = 8787) -> None:
    config = load_config(project_root)
    atlas = McpServer(config)
    handler = type("BoundAtlasHTTPHandler", (AtlasHTTPHandler,), {})
    server = ThreadingHTTPServer((host, port), handler)
    server.atlas = atlas  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    finally:
        atlas.close()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-http", description="ATLAS HTTP JSON-RPC server")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    run_http_server(args.project_root, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
