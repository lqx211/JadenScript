"""``jswc build --html``: one self-contained file, via TurboWarp's packager.

The packager is an npm dependency, so the integration test is skipped when
``node`` or ``node_modules/@turbowarp/packager`` is missing -- but the parts
that decide *whether* to run it (the reachability check and the two "this
cannot work here" messages) are pure Python and always run.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from jswc import cli, html

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
HAVE_PACKAGER = (ROOT / "node_modules" / "@turbowarp" / "packager").exists()
needs_packager = unittest.skipUnless(
    NODE and HAVE_PACKAGER,
    "needs node and `npm install` (@turbowarp/packager)",
)

#: No extensions, so packaging needs nothing on the network.
PLAIN = """
class main {
    public void greenFlag() {
        Looks.say("packed");
    }
}
"""


class AvailabilityTests(unittest.TestCase):
    def test_a_missing_node_is_named(self):
        original = shutil.which
        shutil.which = lambda name, *a, **k: None
        try:
            usable, reason = html.available()
        finally:
            shutil.which = original
        self.assertFalse(usable)
        self.assertIn("node", reason)

    def test_it_is_available_here(self):
        usable, reason = html.available()
        self.assertTrue(usable, reason)


class ReachabilityTests(unittest.TestCase):
    def test_data_urls_need_no_network(self):
        self.assertEqual(html.unreachable_extensions(["data:text/javascript,1"]), [])

    def test_a_dead_url_is_reported_once(self):
        dead = "http://127.0.0.1:9/nothing-here.js"
        self.assertEqual(html.unreachable_extensions([dead, dead]), [dead])

    def test_the_hint_names_the_url_and_the_server(self):
        missing = ["http://localhost:8000/userspace/school/gtm.js"]
        note = html._hint_for(missing)
        self.assertIn("http://localhost:8000/userspace/school/gtm.js", note)
        self.assertIn("make serve", note)

    def test_a_download_is_not_attempted_for_a_dead_host(self):
        # `pack` has to refuse before it writes anything: a 1.9 MB HTML with a
        # localhost URL inside is the failure this check exists to prevent.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.html"
            with self.assertRaises(html.HtmlError) as caught:
                html.pack(
                    b"not even a zip",
                    target,
                    extension_urls=["http://127.0.0.1:9/x.js"],
                )
            self.assertIn("127.0.0.1:9", str(caught.exception))
            self.assertFalse(target.exists())


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        saved = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(argv)
        finally:
            if saved is None:
                os.environ.pop("NO_COLOR", None)
            else:
                os.environ["NO_COLOR"] = saved
        return code, out.getvalue(), err.getvalue()

    def source(self, text: str = PLAIN, name: str = "main.jsw") -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path


@needs_packager
class BuildTests(_Tmp):
    def test_html_is_written_next_to_the_sb3(self):
        sb3 = self.tmp / "out.sb3"
        code, stdout, err = self.run_cli(
            ["build", str(self.source()), "-o", str(sb3), "--html"]
        )
        self.assertEqual(code, 0, err)
        page = self.tmp / "out.html"
        self.assertTrue(page.exists(), stdout)
        text = page.read_text(encoding="utf-8", errors="replace")
        # The packager writes the document in upper case (`<!DOCTYPE HTML>`).
        self.assertIn("<!DOCTYPE HTML>", text.upper())
        # The player is embedded: that is the whole point of the format.
        self.assertGreater(page.stat().st_size, 500_000)
        self.assertIn("out.html", stdout)

    def test_the_title_reaches_the_page(self):
        code, _, err = self.run_cli(
            [
                "build", str(self.source()), "-o", str(self.tmp / "t.sb3"),
                "--html", "--html-title", "Gestures",
            ]
        )
        self.assertEqual(code, 0, err)
        page = (self.tmp / "t.html").read_text(encoding="utf-8", errors="replace")
        self.assertIn("Gestures", page)

    def test_project_json_with_html_still_packs(self):
        # No archive is written on that path, so the packager has to be fed
        # bytes built in memory (``sb3_bytes``).
        code, _, err = self.run_cli(
            [
                "build", str(self.source()), "-o", str(self.tmp / "j.sb3"),
                "--project-json", "--html",
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertTrue((self.tmp / "j.json").exists())
        self.assertTrue((self.tmp / "j.html").exists())


class SelfHostedTests(unittest.TestCase):
    """The bits of the same-origin build that decide what lands where.

    The integration test needs the network (it mirrors a real CDN and model),
    so what is checked here is the transformation itself: the project the
    packager is handed, the page it gets back, and the posenet path derived
    from a model's own metadata.
    """

    def project(self, urls: dict[str, str]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "project.json",
                json.dumps({"targets": [], "extensionURLs": dict(urls)}),
            )
            archive.writestr("asset.svg", b"<svg/>")
        return buffer.getvalue()

    def test_extension_urls_become_relative_and_the_archive_keeps_the_originals(self):
        sb3 = self.project({"gtm": "http://localhost:8000/gtm.js"})
        patched = html._rewrite_extension_urls(
            sb3, {"gtm": "out_files/ext/gtm.js"}
        )
        rewritten = json.loads(zipfile.ZipFile(io.BytesIO(patched)).read("project.json"))
        self.assertEqual(rewritten["extensionURLs"], {"gtm": "out_files/ext/gtm.js"})
        # Everything else in the archive is copied through untouched.
        self.assertIn("asset.svg", zipfile.ZipFile(io.BytesIO(patched)).namelist())
        original = json.loads(zipfile.ZipFile(io.BytesIO(sb3)).read("project.json"))
        self.assertEqual(original["extensionURLs"], {"gtm": "http://localhost:8000/gtm.js"})

    def test_an_unknown_extension_id_is_left_alone(self):
        sb3 = self.project({"gtm": "http://localhost:8000/gtm.js"})
        patched = html._rewrite_extension_urls(sb3, {"other": "x/y.js"})
        self.assertEqual(
            json.loads(zipfile.ZipFile(io.BytesIO(patched)).read("project.json"))[
                "extensionURLs"
            ],
            {"gtm": "http://localhost:8000/gtm.js"},
        )

    def test_the_page_is_patched_to_resolve_relative_extension_paths(self):
        page = "      " + html.EXTENSION_LOAD_CALL + "\n"
        patched = html._absolutise_extension_urls(page)
        self.assertIn("new URL(extension, document.baseURI).href", patched)
        self.assertNotIn(html.EXTENSION_LOAD_CALL + "\n", patched)

    def test_a_page_without_the_call_fails_loudly(self):
        with self.assertRaises(html.HtmlError) as caught:
            html._absolutise_extension_urls("<html></html>")
        self.assertIn("scaffolding", str(caught.exception))

    def test_the_posenet_path_comes_from_the_models_own_settings(self):
        metadata = {
            "modelSettings": {"posenet": {"multiplier": 0.75, "outputStride": 16}}
        }
        original = html._fetch
        html._fetch = lambda url, **kw: json.dumps({"weightsManifest": [
            {"paths": ["group1-shard1of2.bin", "group1-shard2of2.bin"]}
        ]}).encode()
        try:
            manifest, document = html._posenet_model(metadata)
        finally:
            html._fetch = original
        self.assertEqual(
            manifest,
            "savedmodel/posenet/mobilenet/float/075/model-stride16.json",
        )
        self.assertEqual(
            document["weightsManifest"][0]["paths"],
            ["group1-shard1of2.bin", "group1-shard2of2.bin"],
        )

    def test_a_posenet_manifest_that_cannot_be_read_is_still_copied(self):
        metadata = {
            "modelSettings": {"posenet": {"multiplier": 0.75, "outputStride": 16}}
        }
        original = html._fetch
        html._fetch = lambda url, **kw: (_ for _ in ()).throw(OSError("offline"))
        try:
            manifest, document = html._posenet_model(metadata)
        finally:
            html._fetch = original
        self.assertTrue(manifest.endswith("model-stride16.json"))
        self.assertIsNone(document)

    def test_an_image_model_needs_no_posenet_files(self):
        self.assertEqual(html._posenet_model({"modelSettings": {}}), ("", None))
        self.assertEqual(html._posenet_model({}), ("", None))

    def test_the_page_lists_the_files_the_build_wrote(self):
        # `tools/check_csp.py` verifies a deployment against this list; without
        # it a host that refuses `.bin` cannot be told apart from a bad policy.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            written = [directory / "serve_files" / "ext" / "gtm.js",
                       directory / "serve_files" / "model" / "m" / "w.bin.json"]
            for path in written:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
            js = html._assets_js(written, directory)
        self.assertIn(
            'window.JSWC_ASSETS = ["serve_files/ext/gtm.js", '
            '"serve_files/model/m/w.bin.json"];',
            js,
        )

    def test_a_rebuild_drops_the_files_the_previous_one_wrote(self):
        # The folder is build output: a stale shard from an older naming, or a
        # model the project no longer names, must not travel with the upload.
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "serve_files"
            for owned in ("ext", "lib", "model"):
                (assets / owned / "deep").mkdir(parents=True, exist_ok=True)
                (assets / owned / "deep" / "old.bin").write_bytes(b"stale")
            (assets / "mine.txt").write_text("not the build's", "utf-8")
            html._clear_assets(assets)
            self.assertFalse((assets / "ext").exists())
            self.assertFalse((assets / "lib").exists())
            self.assertFalse((assets / "model").exists())
            self.assertTrue((assets / "mine.txt").is_file())

    def test_a_mirrored_model_names_its_shards_where_a_host_will_serve_them(self):
        # A static host may answer 403 to `.bin` even when the file is there --
        # `.json` is the extension the manifest itself needs, so the shards are
        # written under it and the manifest is rewritten to name the copies.
        pages = {
            "metadata.json": b'{"modelSettings": {}}',
            "model.json": json.dumps({
                "weightsManifest": [{"paths": ["model.weights.bin"], "weights": []}]
            }).encode(),
            "model.weights.bin": b"\x00\x01",
        }
        original = html._fetch
        html._fetch = lambda url, **kw: pages[url.rsplit("/", 1)[-1]]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / "model" / "abc"
                html._mirror_model("abc", directory)
                self.assertTrue((directory / "model.weights.bin.json").is_file())
                self.assertFalse((directory / "model.weights.bin").exists())
                manifest = json.loads((directory / "model.json").read_text("utf-8"))
        finally:
            html._fetch = original
        self.assertEqual(
            manifest["weightsManifest"][0]["paths"], ["model.weights.bin.json"]
        )

    def test_the_libraries_keep_their_cdn_path(self):
        # gtm builds `<base>@tensorflow/tfjs@1.3.1/dist/tf.min.js`, so the copy
        # has to answer there; renaming it to `tf.min.js` would 404.
        for relative in html.VENDORED_LIBRARIES:
            with self.subTest(relative=relative):
                self.assertTrue(relative.startswith("@"))
                self.assertIn("/dist/", relative)


class FailureTests(_Tmp):
    def test_an_unreachable_extension_fails_the_build_not_the_file(self):
        # A project that names an extension URL nothing is serving. The .sb3 is
        # still written -- it is valid -- but no HTML is produced, because one
        # would look packaged and only work on this machine.
        source = self.tmp / "g.jsw"
        source.write_text(PLAIN, encoding="utf-8")
        sb3 = self.tmp / "g.sb3"
        original = html.unreachable_extensions
        html.unreachable_extensions = lambda urls: ["http://localhost:8000/x.js"]
        try:
            code, _, err = self.run_cli(["build", str(source), "-o", str(sb3), "--html"])
        finally:
            html.unreachable_extensions = original
        self.assertEqual(code, 1, err)
        self.assertIn("http://localhost:8000/x.js", err)
        self.assertTrue(sb3.exists(), "the sb3 is valid and must survive")
        self.assertFalse((self.tmp / "g.html").exists())


if __name__ == "__main__":
    unittest.main()
