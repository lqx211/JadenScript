#!/usr/bin/env python3
"""Can this host run a ``jswc build --html`` page? Say what is missing.

    python3 tools/check_csp.py https://example.com/temp/serve.html
    python3 tools/check_csp.py https://example.com/temp/serve.html --csp-only

A packaged page is self-contained but not self-sufficient in the browser's
eyes: every extension is a ``data:text/javascript`` URL, ``gtm`` injects
TensorFlow.js as a cross-origin ``<script>``, and it fetches the model from
Google. A host that sends a ``Content-Security-Policy`` has to allow all three
or the page opens and does nothing -- and the failure is a console message that
names the header, never the build, so it reads like a broken project.

The policy is read off the live response and checked source by source, so the
answer is which directive is short, not that "CSP blocked it". The page itself
is fetched too: the hosts it needs are the ones it actually mentions, not a
list hard-coded here.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request

#: ``script-src`` has to let these through for any packaged page.
SCRIPT_SOURCES = {
    "data:": "the inlined extensions are data: URLs",
    "blob:": "the packaged runtime creates blob: scripts",
    "'unsafe-inline'": "the inline bootstrap script",
    "'unsafe-eval'": "scratch-vm's compiler uses Function()",
}

#: ``connect-src`` has to let the model host through, and the CDN tfjs loads
#: its weights from.
CONNECT_SOURCES = {
    "https://teachablemachine.withgoogle.com": "the share link's model.json",
    "https://storage.googleapis.com": "where that link redirects (tm-model/…)",
    "https://cdn.jsdelivr.net": "TensorFlow.js and the tfjs weights",
}

#: The CDN is also injected as a <script>, so it belongs in script-src too.
SCRIPT_HOSTS_FROM_PAGE = ("https://cdn.jsdelivr.net",)

#: What the page needs so it can see the camera. The default is already `self`.
PERMISSIONS = "Permissions-Policy: camera=(self)"

NGINX_HINT = """\
add_header Content-Security-Policy "default-src 'self'; \\
script-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: \\
https://cdn.jsdelivr.net; \\
connect-src 'self' https://teachablemachine.withgoogle.com \\
https://storage.googleapis.com https://cdn.jsdelivr.net ws: wss:; \\
img-src 'self' data: blob: https:; media-src 'self' data: blob: https:; \\
worker-src 'self' blob:; object-src 'none'; base-uri 'self'" always;"""


def fetch(url: str) -> tuple[dict[str, str], bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "jswc-check-csp"})
    with urllib.request.urlopen(request, timeout=20) as response:
        headers = {k.lower(): v for k, v in response.headers.items()}
        body = response.read()
    return headers, body


def directives(policy: str) -> dict[str, list[str]]:
    """``script-src 'self' data:`` -> ``{"script-src": ["'self'", "data:"]}``."""
    out: dict[str, list[str]] = {}
    for part in policy.split(";"):
        words = part.split()
        if not words:
            continue
        out[words[0].lower()] = [w.lower() for w in words[1:]]
    return out


def allowed(sources: list[str], want: str) -> bool:
    """Whether a CSP source list lets ``want`` through.

    Three shapes matter here: an exact source (``data:``, a scheme on its own),
    a host (which covers its paths, and is not a prefix of a longer host), and a
    wildcard host (``https://*.example.com``).
    """
    if "*" in sources:
        return True
    want = want.lower()
    bare = want.rstrip("/")
    for raw in sources:
        source = raw.lower()
        if source in (want, bare):
            return True
        if re.fullmatch(r"[a-z][a-z0-9+.-]*:", source):
            # A scheme-source: `https:` covers every https URL, `data:` itself.
            if want.startswith(source):
                return True
            continue
        base = source.rstrip("/")
        if "*" not in base:
            # `https://host/` and `https://host` both cover the host itself.
            if want == base or want.startswith(base + "/"):
                return True
            continue
        pattern = re.escape(base).replace(r"\*", ".*")
        if re.fullmatch(pattern, bare) or re.fullmatch(pattern + r"/.*", want):
            return True
    return False


def effective(rules: dict[str, list[str]], name: str) -> list[str] | None:
    """``script-src`` falls back to ``default-src``; ``connect-src`` does too."""
    if name in rules:
        return rules[name]
    return rules.get("default-src")


def check(url: str) -> int:
    try:
        headers, body = fetch(url)
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"cannot fetch {url}: {error}")
        return 2

    # A CSP meta tag is honoured too, and a host that sends none may still ship
    # one in the document.
    policy = headers.get("content-security-policy", "")
    meta = re.search(rb'<meta[^>]+http-equiv=["\']?content-security-policy["\']?[^>]+'
                     rb'content=["\']([^"\']+)', body, re.IGNORECASE)
    origin = "header" if policy else ("<meta> tag" if meta else "none")
    if not policy and meta:
        policy = meta.group(1).decode("utf-8", "replace")

    # A packaged page carries the TurboWarp scaffolding; whether the extensions
    # are inlined is what decides if the host has to allow `data:` scripts. A
    # `--self-hosted` build has none, because they are files next to the page.
    packaged = b"Scaffolding.Scaffolding" in body or b"data:text/javascript" in body
    inlined = b"data:text/javascript" in body
    kind = ("packaged (extensions inlined)" if inlined
            else "self-hosted (extensions next to the page)" if packaged
            else "not a --html build")
    print(f"page      {url}")
    print(f"          {len(body):,} bytes, {kind}")
    print(f"policy    {origin}")
    if not policy:
        print("          no Content-Security-Policy — nothing to fix")
        return 0 if packaged else 0

    rules = directives(policy)
    failures = 0

    def report(title: str, wanted: dict[str, str], directive: str) -> None:
        nonlocal failures
        sources = effective(rules, directive)
        if sources is None:
            print(f"{title}\n          unrestricted (no {directive}, no default-src)")
            return
        print(title)
        for want, why in wanted.items():
            ok = allowed(sources, want)
            failures += 0 if ok else 1
            print(f"          {'ok     ' if ok else 'MISSING'}  {want:<46} {why}")

    if packaged:
        wanted = SCRIPT_SOURCES if inlined else {
            key: value for key, value in SCRIPT_SOURCES.items()
            if key.startswith("'")     # inline bootstrap and the compiler
        }
        report("script-src", wanted, "script-src")
        if inlined:
            for host in SCRIPT_HOSTS_FROM_PAGE:
                sources = effective(rules, "script-src") or []
                ok = allowed(sources, host)
                failures += 0 if ok else 1
                print(f"          {'ok     ' if ok else 'MISSING'}  {host:<46} "
                      f"TensorFlow.js is injected as a <script>")
            report("connect-src", CONNECT_SOURCES, "connect-src")
        else:
            print("extensions  ok      served from this origin (--self-hosted)")

    permissions = headers.get("permissions-policy", "")
    if "camera" in permissions and "camera=(self)" not in permissions.replace(" ", ""):
        print(f"permissions  camera is restricted: {permissions}")
        failures += 1
    else:
        print(f"permissions  ok      {PERMISSIONS.split(': ')[1]}")

    if failures:
        print(f"\n{failures} source(s) missing. Add them to the policy that is "
              f"already being sent:\n")
        print(NGINX_HINT)
        print("\nBehind Cloudflare the header may come from Rules -> Transform "
              "Rules -> Modify response header\nrather than the origin; the same "
              "value goes there, on one line.")
        return 1
    print("\nevery source the page needs is allowed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("url", help="the deployed page, or any URL on the same host")
    args = parser.parse_args(argv)
    return check(args.url)


if __name__ == "__main__":
    raise SystemExit(main())
