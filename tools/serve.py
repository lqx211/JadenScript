#!/usr/bin/env python3
"""A static file server for this repository, built for TurboWarp's URL loader.

    python3 tools/serve.py                 # http://localhost:8000/
    python3 tools/serve.py --port 8080
    python3 tools/serve.py --root build
    python3 tools/serve.py --root tmp --csp packaged   # preview a --html build

TurboWarp loads a project with

    https://turbowarp.org/?project_url=http://localhost:8000/build/i386/i386.sb3

and that fetch is **cross-origin** -- the page is on turbowarp.org, the file on
localhost -- so the response has to carry `Access-Control-Allow-Origin`. Python's
`http.server` does not send it, and the failure is invisible from the browser
(just "failed to load project"), which is why this exists.

The path is resolved with the query string thrown away, so TurboWarp's cache
buster (`...i386.sb3?v=903`) works: bump `v` after a rebuild instead of clearing
the browser cache.

Nothing is cached (`Cache-Control: no-store`) for the same reason -- a rebuild is
picked up on reload -- and a `.sb3` is served as `application/zip`, which is what
it is.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import sys
from pathlib import Path

TYPES = {
    ".sb3": "application/zip",
    ".sb2": "application/zip",
    ".json": "application/json",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".hex": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".jsw": "text/plain; charset=utf-8",
    ".jsa": "text/plain; charset=utf-8",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
    ".iso": "application/octet-stream",
    ".img": "application/octet-stream",
    ".bin": "application/octet-stream",
}


#: ``--csp packaged`` — the policy a ``jswc build --html`` file needs.
#:
#: A packaged page is not one script: each extension is a ``data:`` URL, and
#: ``gtm`` injects TensorFlow.js from a CDN and fetches the model from Google.
#: A host that sends ``script-src 'self'`` makes the page load and do nothing,
#: with a console error that names the header rather than the build -- so this
#: flag reproduces the header a real host has to send, to test that it works.
PACKAGED_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: "
    "https://static.cloudflareinsights.com https://www.google.com "
    "https://www.gstatic.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; media-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://cloudflareinsights.com "
    "https://teachablemachine.withgoogle.com https://storage.googleapis.com "
    "https://cdn.jsdelivr.net ws: wss:; "
    "frame-src 'self' blob: https://spessasus.github.io https://www.google.com; "
    "worker-src 'self' blob:; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'self'"
)


class Handler(http.server.SimpleHTTPRequestHandler):
    #: ``--csp packaged``; ``""`` sends no policy at all, which is the default.
    csp: str = ""

    """Static files with the headers a browser needs to fetch them cross-origin."""

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range")
        self.send_header("Cache-Control", "no-store")
        if self.csp:
            self.send_header("Content-Security-Policy", self.csp)
            self.send_header("Permissions-Policy", "camera=(self), microphone=(self)")
        super().end_headers()

    def do_OPTIONS(self) -> None:                # the CORS preflight
        self.send_response(204)
        self.end_headers()

    def guess_type(self, path: str) -> str:
        return TYPES.get(Path(path).suffix.lower()) or super().guess_type(path)

    def log_message(self, fmt: str, *args) -> None:
        # Quiet by default: a TurboWarp load is one request, and this server also
        # runs while a build is being written, when it is asked for the file
        # before it exists -- noise that looks like a failure and is not.
        if self.path.endswith((".sb3", ".html")):
            sys.stderr.write(f"  {self.path}  {args[1] if len(args) > 1 else ''}\n")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--root", default=str(root), help="directory to serve (default: the repository)")
    parser.add_argument(
        "--csp",
        choices=("off", "packaged"),
        default="off",
        help="'packaged' sends the Content-Security-Policy a --html build needs, "
             "so the header a real host will send can be tested here",
    )
    args = parser.parse_args(argv)
    if args.csp == "packaged":
        Handler.csp = PACKAGED_CSP

    directory = str(Path(args.root).expanduser().resolve())
    handler = functools.partial(Handler, directory=directory)
    with Server(("127.0.0.1", args.port), handler) as httpd:
        print(f"serving {directory}")
        print(f"  http://localhost:{args.port}/")
        if args.csp != "off":
            print(f"  csp: {args.csp} (Content-Security-Policy + Permissions-Policy)")
        print("  ctrl-c to stop", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
