"""Launcher for the Jenkins Pipeline Chain dashboard.

Picks a free TCP port automatically (unless DASHBOARD_PORT is pinned in the
environment / .env), opens the browser at that exact port, then serves the app.
"""

from __future__ import annotations

import os
import socket
import threading
import webbrowser

import uvicorn
from dotenv import load_dotenv


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def main() -> None:
    load_dotenv()
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"

    pinned = os.getenv("DASHBOARD_PORT", "").strip()
    port = int(pinned) if pinned else _free_port(host)

    url = f"http://{host}:{port}"
    print(f"[Jenkins Chain] Opening {url}")

    threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
