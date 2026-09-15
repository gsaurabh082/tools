"""Launcher for the GitLab Focus dashboard.

Picks a free TCP port automatically (unless DASHBOARD_PORT is pinned in the
environment / .env), opens the browser at that exact port, then serves the app.
Using a random free port means the dashboard never collides with other local
apps running on a fixed port.
"""

from __future__ import annotations

import os
import socket
import threading
import webbrowser

import uvicorn
from dotenv import load_dotenv


def _free_port(host: str) -> int:
    """Ask the OS for an unused port and hand it straight to uvicorn."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def main() -> None:
    load_dotenv()
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"

    pinned = os.getenv("DASHBOARD_PORT", "").strip()
    port = int(pinned) if pinned else _free_port(host)

    url = f"http://{host}:{port}"
    print(f"[GitLab Focus] Opening {url}")

    # Open the browser shortly after the server has had time to start.
    threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
