"""Tiny stdlib HTTP server for the dashboard (static page + token-protected JSON API)."""

from __future__ import annotations

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any

import structlog

from .runtime import Runtime

log = structlog.get_logger(__name__)

MAX_BODY = 64 * 1024


def dashboard_settings(env: dict[str, str]) -> dict[str, Any] | None:
    """None when DASHBOARD_TOKEN is unset (dashboard disabled)."""
    token = (env.get("DASHBOARD_TOKEN") or "").strip()
    if not token:
        return None
    if len(token) < 16:
        raise ValueError("DASHBOARD_TOKEN must be at least 16 characters")
    return {
        "token": token,
        "host": env.get("DASHBOARD_HOST") or "127.0.0.1",
        "port": int(env.get("DASHBOARD_PORT") or 8787),
    }


def _page() -> bytes:
    return resources.files(__package__).joinpath("static/index.html").read_bytes()


def make_handler(runtime: Runtime, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "bitable-bot-dashboard"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # no query strings / headers in logs
            log.debug("dashboard.http", path=self.path.split("?", 1)[0], status=args[1] if len(args) > 1 else None)

        # ------------------------------------------------ helpers
        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _authed(self) -> bool:
            h = self.headers.get("Authorization") or ""
            given = h[7:].strip() if h.lower().startswith("bearer ") else (self.headers.get("X-Dashboard-Token") or "")
            return bool(given) and hmac.compare_digest(given.encode(), token.encode())

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("body too large")
            data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(data, dict):
                raise ValueError("JSON object expected")
            return data

        def _actor(self) -> str:
            return f"dashboard@{self.client_address[0]}"

        # ------------------------------------------------ routes
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, _page(), "text/html; charset=utf-8")
                return
            if path == "/healthz":
                self._json(200, {"ok": True})
                return
            if not path.startswith("/api/"):
                self._json(404, {"error": "not found"})
                return
            if not self._authed():
                self._json(401, {"error": "unauthorized"})
                return
            if path == "/api/overview":
                self._json(200, runtime.overview())
            elif path == "/api/system":
                self._json(200, runtime.system_config())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if not self._authed():
                self._json(401, {"error": "unauthorized"})
                return
            try:
                body = self._body()
                if path == "/api/reload":
                    res = runtime.reload("manual")
                    runtime.audit("config.reload", self._actor(), ok=res.get("ok"), rows=res.get("rows"))
                    self._json(200 if res.get("ok") else 502, res)
                elif path == "/api/system":
                    self._json(200, runtime.update_system(body, self._actor()))
                elif path == "/api/secrets":
                    vals = body.get("values") or {}
                    if not isinstance(vals, dict):
                        raise ValueError("values must be an object")
                    self._json(200, runtime.update_secrets(vals, self._actor()))
                else:
                    self._json(404, {"error": "not found"})
            except Exception as e:
                log.warning("dashboard.request_failed", path=path, error=str(e)[:200])
                self._json(400, {"error": str(e)[:300]})

    return Handler


class DashboardServer:
    def __init__(self, runtime: Runtime, host: str, port: int, token: str) -> None:
        self.httpd = ThreadingHTTPServer((host, port), make_handler(runtime, token))
        self.httpd.daemon_threads = True
        self.host, self.port = host, self.httpd.server_address[1]

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="dashboard")
        t.start()
        log.info("dashboard.started", host=self.host, port=self.port)
        return t

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
