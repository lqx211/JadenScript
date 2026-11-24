"""gtm: the Teachable Machine extension, on both sides of the boundary.

The compiler's block table and the extension script have to agree: an opcode the
compiler emits and the script does not define is a block that shows up as
"undefined" in the editor and does nothing when it runs. The two live in
different languages, so the comparison goes through `tools/check_gtm.js`, which
loads the script in a Scratch-shaped `vm` context and prints what it registered.

The rest of the script's behaviour -- the guards, the share-link parser and the
confidence arithmetic -- is checked there too, for the same reason: it is
JavaScript, and TensorFlow.js never has to be fetched to test it.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import blocks_of, compile_source, opcodes

from jswc import cli, extensions
from jswc.gtm_blocks import GTM_BLOCKS, GTM_EXTENSION

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "tools" / "check_gtm.js"
NODE = shutil.which("node")


class BlockTableTests(unittest.TestCase):
    def test_the_namespace_resolves_a_command(self):
        call = extensions.resolve("gtm", "load", 1)
        self.assertIsNotNone(call)
        self.assertEqual(call.extension, GTM_EXTENSION)
        self.assertEqual(call.full_opcode, "gtm_load")
        self.assertEqual(call.kind, "COMMAND")

    def test_a_reporter_and_a_boolean_keep_their_kind(self):
        confidence = extensions.resolve("gtm", "confidence", 1)
        self.assertEqual((confidence.opcode, confidence.kind), ("confidence", "REPORTER"))
        predicate = extensions.resolve("gtm", "isLabel", 1)
        self.assertEqual((predicate.opcode, predicate.kind), ("isLabel", "BOOLEAN"))

    def test_a_zero_argument_reporter_is_callable_without_arguments(self):
        call = extensions.resolve("gtm", "classify", 0)
        self.assertEqual(call.opcode, "classify")

    def test_the_namespace_is_always_available(self):
        # Unlike the pishi family, gtm is an ordinary URL extension: a build
        # that never mentions `--enable` can still call it.
        self.assertIn("gtm", extensions.known_namespaces())

    def test_an_unknown_member_is_a_diagnostic(self):
        codegen, _ = compile_source("class a { void f(){ gtm.notAThing(); } }")
        self.assertTrue(
            any("not a block of the gtm extension" in note for note in codegen.diagnostics),
            codegen.diagnostics,
        )


class CompileTests(unittest.TestCase):
    def test_a_load_call_becomes_the_extension_block(self):
        _, project = compile_source(
            'class a { public void f(){ '
            'gtm.load("https://teachablemachine.withgoogle.com/models/AbCdEf123_/"); } }'
        )
        self.assertIn("gtm", project.extensions)
        self.assertIn("gtm_load", opcodes(project, "a"))

    def test_arguments_reach_the_block_in_order(self):
        _, project = compile_source('class a { void f(){ gtm.setThreshold(0.8); } }')
        blocks = blocks_of(project, "a")
        block = next(b for b in blocks.values() if b["opcode"] == "gtm_setThreshold")
        # [1, [4, "0.8"]]: a shadowed number input, the same shape the editor writes.
        self.assertEqual(block["inputs"]["VALUE"], [1, [4, "0.8"]])

    def test_a_call_round_trips_through_the_decompiler(self):
        from jswc.decompile import Decompiler
        from jswc.sb3 import build_project_dict
        from jswc.sb3_read import LoadedProject

        source = (
            'class a { public void f(){ '
            'gtm.load("https://teachablemachine.withgoogle.com/models/AbCdEf123_/");'
            ' if (gtm.isLabel("Cat")) { gtm.setThreshold(0.8); } } }'
        )
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        again = Decompiler(LoadedProject(data=data)).decompile()
        self.assertIn('gtm.load("https://teachablemachine.withgoogle.com/models/AbCdEf123_/")', again)
        self.assertIn('gtm.isLabel("Cat")', again)
        self.assertIn("gtm.setThreshold(0.8)", again)

    def test_a_boolean_block_is_not_wrapped_in_an_operator(self):
        # A BOOLEAN argument slot holds the reporter directly; wrapping it in a
        # `=` operator is what a truthiness lowering would do and it changes the
        # block's shape.
        _, project = compile_source('class a { void f(){ if (gtm.isLabel("Cat")) { x = 1; } } }')
        blocks = list(project.targets[-1].blocks.values())
        self.assertIn("gtm_isLabel", [b.opcode for b in blocks])


class CliTests(unittest.TestCase):
    """A build that cannot carry the blocks has to say so, not write them."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def source(self) -> Path:
        path = self.tmp / "detector.jsw"
        path.write_text(
            'class detector {\n'
            '    public void whenFlagClicked() {   // hook: green flag\n'
            '        gtm.load("https://teachablemachine.withgoogle.com/models/AbCdEf123_/");\n'
            '    }\n'
            '}\n',
            encoding="utf-8",
        )
        return path

    def test_a_turbowarp_build_carries_the_blocks(self) -> None:
        out = self.tmp / "out.sb3"
        code, _, err = self.run_cli(["build", str(self.source()), "-o", str(out)])
        self.assertEqual(code, 0, err)
        self.assertTrue(out.exists())

    def test_a_scratch_build_with_gtm_is_refused(self) -> None:
        out = self.tmp / "out.sb3"
        code, _, err = self.run_cli(
            ["build", str(self.source()), "-o", str(out), "--target", "scratch"]
        )
        self.assertEqual(code, 1, err)
        self.assertIn("E0080", err)
        self.assertFalse(out.exists(), "a project that cannot work was still written")

    def test_the_code_is_in_the_catalogue(self) -> None:
        code, stdout, _ = self.run_cli(["codes", "--search", "E0080"])
        self.assertEqual(code, 0)
        self.assertIn("E0080", stdout)


@unittest.skipUnless(NODE, "node is not installed")
class ScriptTests(unittest.TestCase):
    """The extension script, exercised in Node (see `tools/check_gtm.js`)."""

    @classmethod
    def setUpClass(cls):
        cls.result = subprocess.run(
            [NODE, str(CHECK)], capture_output=True, text=True, timeout=120
        )

    def test_every_check_passes(self):
        self.assertEqual(
            self.result.returncode, 0, self.result.stdout + self.result.stderr
        )

    def test_the_script_registers_the_table_compiler_emits(self):
        summary = next(
            line for line in self.result.stdout.splitlines() if line.startswith("SUMMARY ")
        )
        data = json.loads(summary[len("SUMMARY ") :])
        self.assertEqual(data["id"], GTM_EXTENSION)
        self.assertEqual(
            set(GTM_BLOCKS[GTM_EXTENSION]["blocks"]),
            set(data["opcodes"]),
            "a block the compiler emits is not in the script (or the reverse)",
        )


if __name__ == "__main__":
    unittest.main()
