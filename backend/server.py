from __future__ import annotations

import os
import signal
import threading
from http.server import ThreadingHTTPServer

from .app import ApiHandler


class ApiServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    host = os.environ.get("GEO_API_HOST", "0.0.0.0")
    port = int(os.environ.get("GEO_API_PORT", "8000"))
    server = ApiServer((host, port), ApiHandler)
    stopping = threading.Event()

    def shutdown(*_: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print(f"Aperture GEO API listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        print("Aperture GEO API stopped", flush=True)


if __name__ == "__main__":
    main()
