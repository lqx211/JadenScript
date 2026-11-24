"""``jswc build --html``: one self-contained file, via TurboWarp's packager.

The packager is an npm dependency, so the integration test is skipped when
``node`` or ``node_modules/@turbowarp/packager`` is missing -- but the parts
that decide *whether* to run it (the reachability check and the two "this
cannot work here" messages) are pure Python and always run.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
import unittest
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
