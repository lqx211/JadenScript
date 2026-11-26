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

``self_hosted=True`` is for a host that sends a strict
``Content-Security-Policy``: a ``data:`` script and a CDN are both refused
there, so the extensions, TensorFlow.js and the model are written next to the
page and every URL in it is same-origin. See "Hosting the packaged file" in
docs/CLI.md.

The one thing this module adds on top of the packager is the reachability
check. The packager *warns* when it cannot fetch an extension and leaves the
original URL in the HTML, which produces a file that looks packaged and fails
later, elsewhere, with a message about a network error. Failing the build here,
with the URL and the fix, is worth more than a 1.9 MB file that half works.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
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


#: The libraries a packaged page loads at run time, and where they land.
#: ``@tensorflow/tfjs@1.3.1`` is the version ``gtm`` injects (it is the last one
#: that still exports ``tf.fromPixels``), and a pose model needs Teachable
#: Machine's own pose runtime on top of it.
#: Mirrored with their CDN path, not renamed: `gtm` builds a library URL as
#: ``<base>@tensorflow/tfjs@1.3.1/dist/tf.min.js``, so the copy has to answer at
#: exactly that relative path -- the base is the only thing the page changes.
VENDORED_LIBRARIES = (
    "@tensorflow/tfjs@1.3.1/dist/tf.min.js",
    "@teachablemachine/pose@0.8.3/dist/teachablemachine-pose.min.js",
)
CDN = "https://cdn.jsdelivr.net/npm/"

#: Pretrained weights a library fetches by absolute URL, keyed by the prefix the
#: page rewrites at run time (``window.GTM_MIRROR_MAP``). Only a pose model needs
#: these: the pose runtime loads posenet's own weights from Google.
MIRROR_PREFIXES = {"https://storage.googleapis.com/tfjs-models/": "lib/tfjs-models/"}
POSENET = "https://storage.googleapis.com/tfjs-models/"

#: Appended to every weight shard a mirrored build writes. A host may serve
#: static files by an extension allow-list, answering 403 to `.bin` -- and
#: `.dat`, `.pb`, `.gz`, `.txt` -- even when the file is uploaded, which the
#: page reports as nothing more than a failed fetch. The manifest has to be
#: served as `.json` for the model to load at all, so that is the one extension
#: a mirror can rely on. The original name is kept in front of it.
SHARD_SUFFIX = ".json"


def _served_shard(name: str) -> str:
    """``model.weights.bin`` -> ``model.weights.bin.json``."""
    return Path(name).name + SHARD_SUFFIX


def _mirror_shards(document: dict, base: str, directory: Path) -> list[Path]:
    """Copy every shard ``document`` names, and point the manifest at the copies.

    ``document`` is edited in place; the caller writes it out next to the
    shards. ``base`` is the URL the manifest was read from, since its ``paths``
    are relative to its own directory.
    """
    written: list[Path] = []
    for group in document.get("weightsManifest") or []:
        names = []
        for path in group.get("paths") or []:
            name = _served_shard(path)
            target = directory / name
            target.write_bytes(_fetch(base + Path(path).name))
            names.append(name)
            written.append(target)
        group["paths"] = names
    return written


def _posenet_model(metadata: dict) -> tuple[str, dict | None]:
    """The posenet manifest a pose project's runtime pulls, and its document.

    Derived from the model's own ``modelSettings.posenet``: the runtime builds
    ``mobilenet/float/<multiplier as 3 digits>/model-stride<stride>.json`` from
    it, so the mirror has to answer at exactly that path. The document is
    ``None`` when it could not be read, so the caller copies the file as it is.
    """
    settings = (metadata.get("modelSettings") or {}).get("posenet") or {}
    multiplier = settings.get("multiplier")
    stride = settings.get("outputStride")
    if multiplier is None or stride is None:
        return "", None
    folder = f"savedmodel/posenet/mobilenet/float/{round(float(multiplier) * 100):03d}"
    manifest = f"{folder}/model-stride{int(stride)}.json"
    try:
        document = json.loads(_fetch(POSENET + manifest))
    except (urllib.error.URLError, OSError, ValueError):
        return manifest, None
    return manifest, document


#: A Teachable Machine share link anywhere in the project: the model a mirrored
#: build has to copy. Only literals are found, so a computed URL is reported
#: rather than silently left pointing at the network.
_SHARE_LINK = re.compile(
    r"https://teachablemachine\.withgoogle\.com/models/([A-Za-z0-9_-]+)"
)


def _fetch(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "jswc-html"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _rewrite_extension_urls(sb3: bytes, mapping: dict[str, str]) -> bytes:
    """A copy of ``sb3`` with ``extensionURLs`` pointing at ``mapping``.

    The archive itself is left alone -- only ``project.json`` changes -- so the
    build writes the same ``.sb3`` it would have written anyway, and only the
    packaged page differs.
    """
    source = zipfile.ZipFile(io.BytesIO(sb3))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename == "project.json":
                project = json.loads(payload)
                urls = project.get("extensionURLs") or {}
                for key, value in mapping.items():
                    if key in urls:
                        urls[key] = value
                if urls:
                    project["extensionURLs"] = urls
                payload = json.dumps(project, separators=(",", ":")).encode("utf-8")
            target.writestr(item, payload)
    return buffer.getvalue()


def _mirror_model(model_id: str, directory: Path) -> list[Path]:
    """The three (or more) files of a Teachable Machine model, on disk.

    ``model.json`` names its weight files relative to itself, so they are read
    out of the manifest instead of being guessed: a sharded export has several.
    The manifest is then written back naming the copies (:data:`SHARD_SUFFIX`),
    so the mirror still loads on a host that refuses the original extension.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    base = f"https://teachablemachine.withgoogle.com/models/{model_id}/"
    for name in ("metadata.json", "model.json"):
        path = directory / name
        path.write_bytes(_fetch(base + name))
        written.append(path)
    manifest = json.loads((directory / "model.json").read_text("utf-8"))
    if manifest.get("weightsManifest"):
        written += _mirror_shards(manifest, base, directory)
        (directory / "model.json").write_text(
            json.dumps(manifest, separators=(",", ":")), "utf-8"
        )
    return written


#: The one line in the packaged page that loads an extension. It is the
#: scaffolding's own call, and it is patched because the VM insists on an
#: *absolute* URL (`new URL(u)` with no base throws, and the error is "Invalid
#: extension URL") -- while the whole point of the self-hosted mode is to name
#: the files relative to the page, so the folder can be moved.
EXTENSION_LOAD_CALL = "vm.extensionManager.loadExtensionURL(extension);"
EXTENSION_LOAD_ABSOLUTE = (
    "vm.extensionManager.loadExtensionURL(new URL(extension, document.baseURI).href);"
)


def _absolutise_extension_urls(page: str) -> str:
    """Make the page resolve its extension paths against its own location."""
    if EXTENSION_LOAD_CALL not in page:
        raise HtmlError(
            "the packaged page does not have the extension-loading call this "
            "build patches (the packager's scaffolding changed); build without "
            "--self-hosted, or pin the packager version in package.json"
        )
    return page.replace(EXTENSION_LOAD_CALL, EXTENSION_LOAD_ABSOLUTE)


def _clear_assets(assets: Path) -> None:
    """Drop what the previous build wrote there, so none of it gets uploaded.

    The folder is build output, and its contents are derived: a renamed shard
    (:data:`SHARD_SUFFIX`) or a model the project no longer names would
    otherwise sit next to the page forever -- and the old ``.bin`` names are
    exactly what a host with an extension allow-list refuses.
    """
    for owned in ("ext", "lib", "model"):
        shutil.rmtree(assets / owned, ignore_errors=True)


def _assets_js(written: list[Path], directory: Path) -> str:
    """``window.JSWC_ASSETS``: what the build wrote, as page-relative paths.

    Nothing in the page says which files the folder is supposed to contain, and
    a host can refuse one it *has* -- a static-extension allow-list answers 403
    to `.bin` however the upload went. Listing them lets a deployment be checked
    against the build instead of against a guess; ``tools/check_csp.py`` reads
    this.
    """
    paths = sorted(
        str(path.relative_to(directory)).replace(os.sep, "/") for path in written
    )
    return f"window.JSWC_ASSETS = {json.dumps(paths)};"


def self_hosted(sb3: bytes, output: str | Path, *, extension_urls=None,
                title: str | None = None, timeout: int = PACKAGE_TIMEOUT) -> dict:
    """Write the page plus its own copy of everything it loads.

    ``extension_urls`` is ``{extension id: url}``; each is fetched and written
    to ``<stem>_files/ext/<id>.js``, and the packaged project points there. The
    libraries and any Teachable Machine model the project names go to the same
    folder, and one inline script tells ``gtm`` where they are.
    """
    output = Path(output)
    assets = output.parent / (output.stem + "_files")
    assets.mkdir(parents=True, exist_ok=True)
    prefix = assets.name
    _clear_assets(assets)

    written: list[Path] = []
    mapping: dict[str, str] = {}
    for extension_id, url in (extension_urls or {}).items():
        if not url or url.startswith("data:"):
            continue
        target = assets / "ext" / f"{extension_id}.js"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_fetch(url))
        written.append(target)
        mapping[extension_id] = f"{prefix}/ext/{extension_id}.js"

    for relative in VENDORED_LIBRARIES:
        target = assets / "lib" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_fetch(CDN + relative))
        written.append(target)

    project_text = zipfile.ZipFile(io.BytesIO(sb3)).read("project.json").decode("utf-8")
    models = list(dict.fromkeys(_SHARE_LINK.findall(project_text)))
    for model_id in models:
        directory = assets / "model" / model_id
        written += _mirror_model(model_id, directory)
        metadata = json.loads((directory / "metadata.json").read_text("utf-8"))
        manifest, document = _posenet_model(metadata)
        if manifest:
            target = assets / "lib" / "tfjs-models" / manifest
            target.parent.mkdir(parents=True, exist_ok=True)
            if document is None:
                target.write_bytes(_fetch(POSENET + manifest))
            else:
                written += _mirror_shards(
                    document, POSENET + str(Path(manifest).parent) + "/", target.parent
                )
                target.write_text(
                    json.dumps(document, separators=(",", ":")), "utf-8"
                )
            written.append(target)

    mirrors = {
        remote: prefix + "/" + local for remote, local in MIRROR_PREFIXES.items()
    }
    globals_js = (
        f"window.GTM_LIB_BASE = {json.dumps(prefix + '/lib/')};"
        f"window.GTM_MODEL_MIRROR = {json.dumps(prefix + '/model/')};"
        f"window.GTM_MIRROR_MAP = {json.dumps(mirrors)};"
        f"{_assets_js(written, output.parent)}"
    )
    patched = _rewrite_extension_urls(sb3, mapping) if mapping else sb3
    pack(
        patched,
        output,
        extension_urls=(),
        title=title,
        timeout=timeout,
        bake=False,
        custom_js=globals_js,
    )
    if mapping:
        page = output.read_text("utf-8")
        output.write_text(_absolutise_extension_urls(page), "utf-8")
    return {"assets": assets, "extensions": mapping, "models": models}


def pack(sb3: bytes, output: str | Path, *, extension_urls=(), title: str | None = None,
         timeout: int = PACKAGE_TIMEOUT, bake: bool = True,
         custom_js: str | None = None) -> Path:
    """Write ``sb3`` to ``output`` as one standalone HTML file.

    ``bake=False`` keeps the project's extension URLs instead of inlining them
    as ``data:`` scripts -- the self-hosted mode in :func:`self_hosted` -- and
    ``custom_js`` is injected into the page before the project starts.
    """
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
    if not bake:
        command += ["--no-bake"]
    if custom_js:
        command += ["--custom-js", custom_js]
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
