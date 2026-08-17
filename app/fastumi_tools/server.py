"""Local-only FastUMI Tools HTTP server."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from . import __version__
from .catalog import Catalog, CatalogError
from .operations import OperationError, OperationManager
from .system_info import APP_ROOT, collect_status


STATIC_ROOT = APP_ROOT / "static"
TOKEN_FILE = Path(os.environ.get("FASTUMI_TOKEN_FILE", "/run/fastumi-tools/access-token"))
STATUS_CACHE_SECONDS = 4


class Application:
    def __init__(self) -> None:
        self.catalog = Catalog()
        self.operations = OperationManager(self.catalog)
        self.token = secrets.token_urlsafe(32)
        self.status_lock = threading.Lock()
        self.status_value: Optional[Dict[str, Any]] = None
        self.status_time = 0.0
        self.write_token()

    def write_token(self) -> None:
        try:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(self.token + "\n", encoding="utf-8")
            TOKEN_FILE.chmod(0o644)
        except OSError:
            pass

    def status(self, force: bool = False) -> Dict[str, Any]:
        now = time.monotonic()
        with self.status_lock:
            if force or self.status_value is None or now - self.status_time > STATUS_CACHE_SECONDS:
                self.status_value = collect_status(__version__)
                self.status_time = now
            return self.status_value


class Handler(BaseHTTPRequestHandler):
    server_version = "FastUMITools/%s" % __version__

    @property
    def app(self) -> Application:
        return self.server.app  # type: ignore[attr-defined]

    def security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'",
        )

    def send_payload(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_payload(status, "application/json; charset=utf-8", payload)

    def api_error(self, status: int, message: str) -> None:
        self.send_json(status, {"ok": False, "error": message})

    def safe_static_path(self, request_path: str) -> Optional[Path]:
        relative = "index.html" if request_path == "/" else request_path.lstrip("/")
        candidate = (STATIC_ROOT / relative).resolve()
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self.send_json(200, {"ok": True, "version": __version__})
            return
        if parsed.path == "/api/status":
            force = parsed.query == "refresh=1"
            self.send_json(200, {"ok": True, "data": self.app.status(force=force)})
            return
        if parsed.path == "/api/catalog":
            self.send_json(200, {"ok": True, "data": self.app.catalog.public()})
            return
        if parsed.path == "/api/operation":
            self.send_json(200, {"ok": True, "data": self.app.operations.snapshot()})
            return
        if parsed.path == "/api/history":
            self.send_json(200, {"ok": True, "data": self.app.operations.history()})
            return
        static = self.safe_static_path(parsed.path)
        if static:
            content_type = mimetypes.guess_type(str(static))[0] or "application/octet-stream"
            try:
                self.send_payload(200, content_type, static.read_bytes())
            except OSError as exc:
                self.api_error(500, str(exc))
            return
        self.api_error(404, "not found")

    def authorized(self) -> bool:
        address = self.client_address[0]
        if address not in ("127.0.0.1", "::1"):
            return False
        supplied = self.headers.get("X-FastUMI-Token", "")
        return bool(supplied) and secrets.compare_digest(supplied, self.app.token)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/operations":
            self.api_error(404, "not found")
            return
        if not self.authorized():
            self.api_error(403, "操作授权失败，请从桌面入口重新打开 FastUMI Tools。")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 2 or length > 65536:
            self.api_error(400, "请求大小不正确。")
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("body must be an object")
            action = str(body.pop("action", ""))
            operation = self.app.operations.start(action, body)
            self.send_json(202, {"ok": True, "data": operation})
        except (ValueError, json.JSONDecodeError) as exc:
            self.api_error(400, "请求格式错误：%s" % exc)
        except (OperationError, CatalogError) as exc:
            self.api_error(409, str(exc))

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: Tuple[str, int], app: Application):
        super().__init__(address, Handler)
        self.app = app


def main() -> None:
    parser = argparse.ArgumentParser(description="FastUMI Tools local web console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    app = Application()
    server = Server((args.host, args.port), app)
    print("FastUMI Tools %s: http://%s:%d" % (__version__, args.host, args.port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
