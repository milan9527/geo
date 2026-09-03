#!/usr/bin/env python3
"""Start the separated Aperture GEO API, public site, and admin console."""

from __future__ import annotations

import mimetypes
import signal
import threading
from http.client import HTTPConnection
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from backend.app import ApiHandler


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
API_PORT = 8000
PUBLIC_PORT = 4173
ADMIN_PORT = 4174


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def create_spa_handler(directory: Path, label: str):
    class SPAHandler(SimpleHTTPRequestHandler):
        server_version = f"ApertureGEO-{label}/2.0"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def do_GET(self) -> None:  # noqa: N802
            if self._is_api_request():
                self._proxy_api()
                return
            parsed = urlparse(self.path)
            clean_path = unquote(parsed.path).lstrip("/")
            requested = (directory / clean_path).resolve()
            if directory not in requested.parents and requested != directory:
                self.send_error(404)
                return
            if clean_path and not requested.exists():
                self.path = "/index.html"
            super().do_GET()

        def do_HEAD(self) -> None:  # noqa: N802
            if self._is_api_request():
                self._proxy_api()
                return
            parsed = urlparse(self.path)
            clean_path = unquote(parsed.path).lstrip("/")
            requested = (directory / clean_path).resolve()
            if clean_path and not requested.exists():
                self.path = "/index.html"
            super().do_HEAD()

        def do_POST(self) -> None:  # noqa: N802
            self._proxy_api()

        def do_PATCH(self) -> None:  # noqa: N802
            self._proxy_api()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._proxy_api()

        def _is_api_request(self) -> bool:
            path = urlparse(self.path).path
            return path.startswith("/api/") or path.startswith("/agent/")

        def _proxy_api(self) -> None:
            if not self._is_api_request():
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else None
            forwarded_headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower()
                in {
                    "cookie",
                    "content-type",
                    "x-admin-key",
                    "user-agent",
                    "accept",
                    "accept-language",
                }
            }
            forwarded_headers["X-Forwarded-For"] = self.client_address[0]
            connection = HTTPConnection(HOST, API_PORT, timeout=10)
            try:
                connection.request(
                    self.command,
                    self.path,
                    body=body,
                    headers=forwarded_headers,
                )
                response = connection.getresponse()
                response_body = response.read()
                self.send_response(response.status, response.reason)
                content_type = response.getheader("Content-Type")
                if content_type:
                    self.send_header("Content-Type", content_type)
                for header, value in response.getheaders():
                    if header.lower() == "set-cookie":
                        self.send_header("Set-Cookie", value)
                self.send_header("Content-Length", str(len(response_body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response_body)
            except OSError as error:
                message = (
                    '{"error":"API proxy unavailable","detail":'
                    + repr(str(error)).replace("'", '"')
                    + "}"
                ).encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)
            finally:
                connection.close()

        def end_headers(self) -> None:
            path = urlparse(self.path).path
            if not self._is_api_request():
                if path.endswith(".html") or path == "/":
                    self.send_header("Cache-Control", "no-cache")
                else:
                    self.send_header("Cache-Control", "public, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            super().end_headers()

        def guess_type(self, path: str) -> str:
            return mimetypes.guess_type(path)[0] or "application/octet-stream"

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"[{label}] {self.address_string()} - {fmt % args}")

    return SPAHandler


def main() -> None:
    public_dir = ROOT / "frontend" / "public"
    admin_dir = ROOT / "frontend" / "admin"
    servers = [
        ReusableHTTPServer((HOST, API_PORT), ApiHandler),
        ReusableHTTPServer((HOST, PUBLIC_PORT), create_spa_handler(public_dir, "public")),
        ReusableHTTPServer((HOST, ADMIN_PORT), create_spa_handler(admin_dir, "admin")),
    ]
    stop_event = threading.Event()

    def shutdown(*_: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    threads = []
    for server in servers:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)

    print("Aperture GEO services are running:")
    print(f"  Public website: http://{HOST}:{PUBLIC_PORT}")
    print(f"  Admin console:  http://{HOST}:{ADMIN_PORT}")
    print(f"  API service:    http://{HOST}:{API_PORT}/api/health")
    print("Press Ctrl+C to stop all services.")

    try:
        while not stop_event.wait(0.5):
            pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
        print("All Aperture GEO services stopped.")


if __name__ == "__main__":
    main()
