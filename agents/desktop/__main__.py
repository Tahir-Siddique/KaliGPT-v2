#!/usr/bin/env python3
"""Launch HatsOff desktop chat (local API + window or browser)."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser

from .chat_store import ChatStore
from .server import create_app


def _free_port(preferred: int = 8765) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description="HatsOff local desktop chat")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-window", action="store_true", help="Print URL only; do not open UI")
    parser.add_argument("--browser", action="store_true", help="Force system browser instead of webview")
    args = parser.parse_args(argv)

    port = args.port if args.port != 8765 else _free_port(8765)
    if args.port != 8765:
        port = args.port

    store = ChatStore()
    app = create_app(store=store)
    url = f"http://{args.host}:{port}/"

    def run():
        # HTTP/1.1 keeps chunked streaming happier than HTTP/1.0 with some clients
        from werkzeug.serving import WSGIRequestHandler

        WSGIRequestHandler.protocol_version = "HTTP/1.1"
        app.run(host=args.host, port=port, threaded=True, use_reloader=False)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    # Wait briefly for server readiness
    for _ in range(50):
        try:
            with socket.create_connection((args.host, port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    print(f"㉿ HatsOff desktop → {url}")
    print(f"   Chat store: {store.db_path}")

    if args.no_window:
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nExiting.")
        return 0

    if not args.browser:
        try:
            import webview

            window = webview.create_window(
                "HatsOff · Kali",
                url,
                width=1280,
                height=860,
                maximized=True,
                resizable=True,
            )

            def _ensure_maximized():
                try:
                    if window is not None and hasattr(window, "maximize"):
                        window.maximize()
                except Exception:
                    pass

            webview.start(_ensure_maximized)
            return 0
        except Exception as exc:
            print(f"[!] pywebview unavailable ({exc}); opening system browser.")
            if sys.platform.startswith("linux"):
                print(
                    "\n    To get a real HatsOff desktop window on Kali:\n"
                    "      sudo apt install -y python3-gi python3-gi-cairo \\\n"
                    "        gir1.2-gtk-3.0 gir1.2-webkit2-4.1\n"
                    "      # recreate venv so it can import system gi:\n"
                    "      deactivate\n"
                    "      rm -rf .venv && python3 -m venv --system-site-packages .venv\n"
                    "      source .venv/bin/activate\n"
                    "      pip install -r requirements/pip-requirements.txt\n"
                    "      python -m agents.desktop\n"
                    "\n    Or keep using the browser (already opening):\n"
                    "      python -m agents.desktop --browser\n"
                )

    webbrowser.open(url)
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nExiting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
