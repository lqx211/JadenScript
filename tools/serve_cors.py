#!/usr/bin/env python3
"""Serve the repo over HTTP with the CORS header TurboWarp's ``project_url`` needs.

    python3 tools/serve_cors.py [port]        # default 8000

TurboWarp fetches the project from the page, so the response must carry
``Access-Control-Allow-Origin: *``. ``python3 -m http.server`` does not send it.
"""

from __future__ import annotations

import functools
import http.server
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s\n" % (fmt % args))


def main(argv: list[str]) -> int:
    port = int(argv[1]) if len(argv) > 1 else 8000
    handler = functools.partial(Handler, directory=str(ROOT))
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"serving {ROOT} on http://127.0.0.1:{port}/", flush=True)
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
