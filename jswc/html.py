"""``jswc build --html``: the compiled project as one self-contained file.

The heavy lifting is TurboWarp's own packager (``@turbowarp/packager``), not a
re-implementation of it. That matters for more than effort:

* the player itself is embedded, so the file runs from a USB stick with no
  network and no server;
* every asset is inlined;
* each ``extensionURLs`` entry is fetched and rewritten to a
  ``data:text/javascript,`` URL, which is what makes a project that depends on
  a *local* extension -- ``gtm``, the board extensions -- portable. A plain
  ``.sb3`` only carries the URL, and on another computer ``localhost`` is that
  computer, so the editor crashes rather than degrade (docs/GTM.md).

The one thing this module adds on top of the packager is the reachability
check. The packager *warns* when it cannot fetch an extension and leaves the
original URL in the HTML, which produces a file that looks packaged and fails
later, elsewhere, with a message about a network error. Failing the build here,
with the URL and the fix, is worth more than a 1.9 MB file that half works.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

#: The packager's Node API is explicitly unstable across minor releases, so the
#: version is pinned in ``package.json`` and only this pairing is supported.
PACKAGER_PACKAGE = "@turbowarp/packager@3.14.0"

#: The wrapper script; ``node`` resolves ``@turbowarp/packager`` from the
#: checkout's ``node_modules``, so a plain ``import`` of the Python package
#: without ``npm install`` says so instead of failing at require time.
PACKAGER_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "package_html.js"

#: Fetching a 13 MB model or a slow CDN is normal; a broken host is not.
EXTENSION_TIMEOUT = 10.0
PACKAGE_TIMEOUT = 900


class HtmlError(RuntimeError):
    """A build-time problem with a sentence the user can act on."""


def available() -> tuple[bool, str]:
    """``(usable, reason)`` — why ``--html`` cannot run here, if it cannot."""
    if shutil.which("node") is None:
        return False, "`node` is not on PATH"
    if not PACKAGER_SCRIPT.exists():
        return False, f"{PACKAGER_SCRIPT} is missing from this checkout"
    return True, ""


def unreachable_extensions(urls) -> list[str]:
    """The extension URLs that do not answer right now.

    ``data:`` URLs are already inlined and need no network. Anything else is
    fetched exactly the way the packager will fetch it, so a dead project
    server is caught before a 2 MB file is written.
    """
    missing: list[str] = []
    for url in dict.fromkeys(u for u in urls if u):
        if url.startswith("data:"):
            continue
        try:
            with urllib.request.urlopen(url, timeout=EXTENSION_TIMEOUT) as response:
                if response.status >= 400:
                    missing.append(url)
        except (urllib.error.URLError, OSError, ValueError):
            missing.append(url)
    return missing


def _hint_for(missing: list[str]) -> str:
    served = [url for url in missing if "localhost:8000" in url or "127.0.0.1:8000" in url]
    lines = ["the packaged HTML would still point at these extensions, so it would "
             "only work on this machine:"]
    lines.extend(f"  {url}" for url in missing)
    if served:
        lines.append("  start the project server (`make serve`) and build again")
    return "\n".join(lines)


def pack(sb3: bytes, output: str | Path, *, extension_urls=(), title: str | None = None,
         timeout: int = PACKAGE_TIMEOUT) -> Path:
    """Write ``sb3`` to ``output`` as one standalone HTML file."""
    usable, reason = available()
    if not usable:
        raise HtmlError(f"--html needs Node.js: {reason}")
    missing = unreachable_extensions(extension_urls)
    if missing:
        raise HtmlError(_hint_for(missing))

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["node", str(PACKAGER_SCRIPT), "--out", str(output)]
    if title:
        command += ["--title", title]
    try:
        result = subprocess.run(command, input=sb3, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HtmlError(f"packaging did not finish in {timeout}s") from None

    # The wrapper always prints one JSON object, even when it fails.
    report: dict | None = None
    for line in reversed(result.stdout.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                report = json.loads(line)
            except ValueError:
                report = None
            break
    if report is None:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        raise HtmlError(f"the packager produced no report (exit {result.returncode}):\n{detail}")
    if not report.get("ok"):
        kind = report.get("error")
        message = str(report.get("message", "")).strip()
        if kind == "missing-packager":
            raise HtmlError(
                "--html needs TurboWarp's packager, which is not installed.\n"
                f"  npm install         # adds {PACKAGER_PACKAGE} from package.json\n"
                f"  ({message.splitlines()[0] if message else 'module not found'})"
            )
        raise HtmlError(f"the packager failed:\n{message}")
    if report.get("type") != "text/html":
        raise HtmlError(f"the packager produced {report.get('type')}, not an HTML file")
    return output
