"""Tests for the CLI surface: exit codes, diagnostic codes and colour handling."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from jswc import cli, ui

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "examples" / "hello.jsw"

GOOD = """
class main {
    public void greenFlag() {
        Looks.say("hi");
    }
}
"""

TWO_EXTENSIONS = """
class main {
    public void greenFlag() {
        Pen.eraseAll();
        log("hello");
    }
}
"""

WARNS = """
class main {
    public void greenFlag() {
        Looks.switchCostume(join("cos", "tume1"));
    }
}
"""

BROKEN = """
class main {
    public void greenFlag() {
"""

RECOVERED = """
class main {
    public void greenFlag() {
        Looks.say(Operators.join("a", "b");
    }
}
"""


def run(argv: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run ``jswc`` with captured streams; returns ``(code, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    keys = ("NO_COLOR", "FORCE_COLOR", "TERM")
    saved = {key: os.environ.get(key) for key in keys}
    # Start from a clean colour environment: a developer shell that exports
    # NO_COLOR=1 (or TERM=dumb) must not decide whether a colour test passes.
    for key in keys:
        os.environ.pop(key, None)
    if env is not None:
        os.environ.update(env)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ui.configure()  # StringIO is not a tty, so colour turns itself off
            code = cli.main(argv)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        ui.configure()
    return code, out.getvalue(), err.getvalue()


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def source(self, text: str, name: str = "test.jsw") -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path


class BuildTests(CliTestCase):
    def test_build_writes_project(self) -> None:
        out = self.tmp / "out.sb3"
        code, stdout, _ = run(["build", str(self.source(GOOD)), "-o", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        self.assertIn("Compiled", stdout)
        self.assertIn("blocks", stdout)

    def test_build_reports_coded_warning(self) -> None:
        out = self.tmp / "out.sb3"
        code, _, stderr = run(["build", str(self.source(WARNS)), "-o", str(out)])
        self.assertEqual(code, 0)
        self.assertIn("W0004", stderr)
        self.assertIn("test.jsw:", stderr)

    def test_strict_turns_warnings_into_failure(self) -> None:
        out = self.tmp / "out.sb3"
        code, _, stderr = run(["build", str(self.source(WARNS)), "-o", str(out), "--strict"])
        self.assertEqual(code, 1)
        self.assertIn("W0004", stderr)
        self.assertFalse(out.exists())

    def test_hints_are_off_by_default(self) -> None:
        out = self.tmp / "out.sb3"
        code, _, stderr = run(["build", str(self.source(BROKEN)), "-o", str(out)])
        self.assertEqual(code, 2)
        self.assertIn("E0018", stderr)  # the innermost block that is never closed
        self.assertNotIn("help:", stderr)

    def test_explain_adds_the_hint(self) -> None:
        out = self.tmp / "out.sb3"
        code, _, stderr = run(["--explain", "build", str(self.source(BROKEN)), "-o", str(out)])
        self.assertEqual(code, 2)
        self.assertIn("help:", stderr)

    def test_explain_works_after_the_command(self) -> None:
        out = self.tmp / "out.sb3"
        code, _, stderr = run(["build", str(self.source(BROKEN)), "-o", str(out), "--explain"])
        self.assertEqual(code, 2)
        self.assertIn("help:", stderr)

    def test_syntax_error_shows_code_and_caret(self) -> None:
        out = self.tmp / "out.sb3"
        code, _, stderr = run(["build", str(self.source(BROKEN)), "-o", str(out)])
        self.assertEqual(code, 2)
        self.assertIn("E0018", stderr)
        self.assertIn("^", stderr)

    def test_extensions_are_comma_separated(self) -> None:
        out = self.tmp / "out.sb3"
        code, stdout, _ = run(["build", str(self.source(TWO_EXTENSIONS)), "-o", str(out)])
        self.assertEqual(code, 0)
        self.assertIn("extensions pen, sipcconsole", stdout)
        self.assertNotIn("extensions pen ·", stdout)

    def test_verbose_prints_phase_timings(self) -> None:
        out = self.tmp / "out.sb3"
        code, _, stderr = run(["build", str(self.source(GOOD)), "-o", str(out), "-v"])
        self.assertEqual(code, 0)
        self.assertIn("generate", stderr)


class DecompileTests(CliTestCase):
    def test_round_trip_writes_source(self) -> None:
        sb3 = self.tmp / "hello.sb3"
        code, _, _ = run(["build", str(HELLO), "-o", str(sb3)])
        self.assertEqual(code, 0)
        jsw = self.tmp / "back.jsw"
        code, stdout, _ = run(["decompile", str(sb3), "-o", str(jsw)])
        self.assertEqual(code, 0)
        self.assertTrue(jsw.exists())
        self.assertIn("Decompiled", stdout)
        self.assertIn("sprites", stdout)


class CheckTests(CliTestCase):
    def test_check_clean_file(self) -> None:
        code, stdout, _ = run(["check", str(self.source(GOOD))])
        self.assertEqual(code, 0)
        self.assertIn("parsed", stdout)
        self.assertIn("main", stdout)

    def test_check_strict_fails_on_recovered_diagnostics(self) -> None:
        code, _, stderr = run(["check", str(self.source(RECOVERED)), "--strict"])
        self.assertEqual(code, 1)
        self.assertIn("W0002", stderr)
        self.assertIn("strict", stderr)

    def test_check_is_lenient_by_default(self) -> None:
        code, _, stderr = run(["check", str(self.source(RECOVERED))])
        self.assertEqual(code, 0)
        self.assertIn("W0002", stderr)

    def test_check_efm_format_is_one_line_per_diagnostic(self) -> None:
        path = self.source("class A {\n  public void go() { x = ; }\n}\n")
        code, stdout, stderr = run(["check", str(path), "--format=efm"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        line = stdout.strip()
        self.assertRegex(line, r"^.+:\d+:\d+: [EWI]: .+ \(E\d+\)$")
        self.assertIn(f"{path}:2:", line)

    def test_check_json_format_is_structured(self) -> None:
        path = self.source("class A {\n  public void go() { x = ; }\n}\n")
        code, stdout, _ = run(["check", str(path), "--format=json"])
        self.assertEqual(code, 0)
        rows = json.loads(stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "E0017")
        self.assertEqual(rows[0]["severity"], "error")
        self.assertEqual(rows[0]["line"], 2)
        self.assertEqual(rows[0]["file"], str(path))

    def test_check_efm_says_nothing_for_a_clean_file(self) -> None:
        code, stdout, stderr = run(["check", str(self.source(GOOD)), "--format=efm"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")


class IntrospectionTests(CliTestCase):
    def test_codes_lists_registry(self) -> None:
        code, stdout, _ = run(["codes"])
        self.assertEqual(code, 0)
        for message in ui.MESSAGES:
            if message.code != "E9999":
                self.assertIn(message.code, stdout)

    def test_codes_can_filter_severity(self) -> None:
        code, stdout, _ = run(["codes", "--severity", "warning"])
        self.assertEqual(code, 0)
        self.assertIn("W0001", stdout)
        self.assertNotIn("E0011", stdout)

    def test_codes_are_flat_and_ordered(self) -> None:
        code, stdout, _ = run(["codes"])
        self.assertEqual(code, 0)
        found = re.findall(r"^  ([EW]\d{4})  ", stdout, re.MULTILINE)
        self.assertEqual(found, sorted(found), "codes print in numeric order")
        self.assertNotIn("lexical", stdout, "no family headings")
        self.assertNotIn("syntax  ", stdout)

    def test_codes_hide_hints_until_asked(self) -> None:
        code, stdout, _ = run(["codes"])
        self.assertEqual(code, 0)
        self.assertNotIn("help:", stdout)
        _, explained, _ = run(["codes", "--explain"])
        self.assertIn("    ", explained)
        self.assertGreater(len(explained), len(stdout))
        self.assertIn("Statements end with", explained)

    def test_codes_search(self) -> None:
        code, stdout, _ = run(["codes", "-s", "unterminated string"])
        self.assertEqual(code, 0)
        self.assertIn("E0002", stdout)
        self.assertNotIn("E0011", stdout)

    def test_features_lists_flags(self) -> None:
        code, stdout, _ = run(["features"])
        self.assertEqual(code, 0)
        self.assertIn("doObfuscation", stdout)

    def test_no_arguments_prints_help(self) -> None:
        code, stdout, _ = run([])
        self.assertEqual(code, 0)
        self.assertIn("usage", stdout)


class ColourTests(CliTestCase):
    def test_no_colour_flag_strips_escapes(self) -> None:
        code, stdout, stderr = run(["--no-color", "check", str(self.source(GOOD))],
                                  env={"FORCE_COLOR": "1"})
        self.assertEqual(code, 0)
        self.assertNotIn("\033[", stdout + stderr)

    def test_forced_colour_emits_escapes(self) -> None:
        code, stdout, _ = run(["check", str(self.source(GOOD))], env={"FORCE_COLOR": "1"})
        self.assertEqual(code, 0)
        self.assertIn("\033[", stdout)

    def test_no_colour_also_works_after_the_command(self) -> None:
        code, stdout, stderr = run(["check", str(self.source(GOOD)), "--no-color"],
                                   env={"FORCE_COLOR": "1"})
        self.assertEqual(code, 0)
        self.assertNotIn("\033[", stdout + stderr)

    def test_plain_mode_is_ascii(self) -> None:
        code, _, stderr = run(["--plain", "build", str(self.source(BROKEN)), "-o",
                               str(self.tmp / "never.sb3")])
        self.assertEqual(code, 2)
        self.assertTrue(stderr.isascii(), stderr)


class ClassificationTests(unittest.TestCase):
    """Every catalogued code must be reachable from a real message shape."""

    SAMPLES = {
        # bare text, classified by shape (the fallback path)
        "E0008": "3:5: expected a token, found '}'",
        "E0010": "3:5: expected ';', found '}'",
        "E0037": "12:9: unknown reporter 'Operators.join'",
        "E0040": "5:5: 'Pen.foo' is not a block of the pen extension",
        "E0058": "9:9: asset 'cat.svg' not found (looked in /tmp)",
        "E0041": "7:7: variable 'x' is private to sprite 'Notes'",
        "E0071": "11:11: monitor A.x: no such sprite/class 'A'",
        "E0042": "3:3: cannot assign to parameter 'i'",
        "E0062": "invalid project: dangling block 123",
        "I0001": "12:9: variable 'Foo Bar' renamed to 'Foo_Bar'",
        "W0004": "34:5: eval: the broadcast name is computed at runtime",
        "W0010": "9:9: class-level statements are not supported yet",
        "W0005": "11:1: argument 'x' is not declared here; kept as a raw block",
        "W0015": "7:7: '// Sprite:' comment ignored; add #enable_feature<commentMetadata>",
        "W0011": "3:3: 'x' is declared both private and public",
        "E0045": "5:5: menu() needs a literal field value",
        # stamped by the raise site: the code wins over the shape
        "E0024": "1:8: E0024 expected a quoted asset path after 'import', found ';'",
        "E0002": "3:9: E0002 string literal opened here is never closed",
        "W0007": "4:1: W0007 'f1' is not a key name the VM reports",
    }

    def test_samples_classify(self) -> None:
        for expected, text in self.SAMPLES.items():
            with self.subTest(code=expected):
                self.assertEqual(ui.classify(text).code, expected)

    def test_unknown_message_falls_back_to_error(self) -> None:
        self.assertEqual(ui.classify("something nobody wrote down").code, "E9999")

    def test_position_is_split_off(self) -> None:
        self.assertEqual(ui.split_position("12:9: boom"), (12, 9, "boom"))
        self.assertEqual(ui.split_position("boom"), (None, None, "boom"))


if __name__ == "__main__":
    unittest.main()


class LogLevelTests(CliTestCase):
    RENAMED = (
        "#enable_feature<commentMetadata>;\n"
        'class main { // Sprite: main (0,0) ns=Main\n'
        "    public void greenFlag() { Looks.say(1); }\n}\n"
    )

    def test_info_is_hidden_by_default(self):
        code, _, stderr = run(["--no-color", "check", str(self.source(self.RENAMED))])
        self.assertEqual(code, 0)
        self.assertNotIn("I0001", stderr)

    def test_loglevel_info_shows_it(self):
        """``I0001`` comes from the decompiler, so drive the reporter directly."""
        note = "12:9: I0001 variable 'Foo Bar' renamed to 'Foo_Bar'"
        code, _, stderr = run(["--no-color", "check", str(self.source(self.RENAMED))])
        self.assertEqual(code, 0)
        self.assertNotIn("I0001", stderr)

        from jswc import ui

        ui.configure(color=False, loglevel="warn")
        self.assertFalse(ui.visible(note))
        ui.configure(color=False, loglevel="info")
        self.assertTrue(ui.visible(note))
        printed = ui.format_diagnostic(note, None, "x.jsw")
        self.assertIn("I0001", printed)
        ui.configure(color=False)

    def test_loglevel_error_silences_warnings(self):
        code, _, stderr = run(["--no-color", "--loglevel", "error",
                               "check", str(self.source(WARNS))])
        self.assertEqual(code, 0)
        self.assertNotIn("W0004", stderr)

    def test_loglevel_after_the_command(self):
        code, _, stderr = run(["--no-color", "check", str(self.source(WARNS)), "-l", "error"])
        self.assertEqual(code, 0)
        self.assertNotIn("W0004", stderr)

    def test_info_does_not_break_a_build(self):
        out = self.tmp / "out.sb3"
        code, stdout, _ = run(["--no-color", "-l", "info", "build",
                               str(self.source(self.RENAMED)), "-o", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())

    def test_codes_lists_info(self):
        code, stdout, _ = run(["--no-color", "codes", "--severity", "info"])
        self.assertEqual(code, 0)
        self.assertIn("I0001", stdout)
        self.assertNotIn("W0001", stdout)
