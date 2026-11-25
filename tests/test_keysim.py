"""keySim: the Key Simulation extension, from call to block.

The opcodes and the menu values come from the extension script
(``https://extensions.turbowarp.org/CubesterYT/KeySimulation.js``), not from its
documentation page, which only renders three of the seven blocks. A wrong
opcode here is a block that reads "undefined" in the editor, so the table is
checked against the script it names rather than against the prose about it.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import blocks_of, compile_source, opcodes

from jswc import cli, extensions
from jswc.keysim_blocks import (
    KEYSIM_BLOCKS,
    KEYSIM_EXTENSION,
    KEYSIM_KEYS,
    KEYSIM_KEY_LABELS,
    KEYSIM_URL,
)
from jswc.opcodes import EXTENSION_URLS

ROOT = Path(__file__).resolve().parent.parent


def build(source: str, **kwargs):
    codegen, project = compile_source(source, **kwargs)
    data, _ = __import__("jswc.sb3", fromlist=["build_project_dict"]).build_project_dict(project)
    return codegen, project, data


def menu_values(blocks: dict, block: dict) -> dict[str, str]:
    """The value each dropdown argument currently carries."""
    out = {}
    for name, entry in (block.get("inputs") or {}).items():
        if isinstance(entry, list) and len(entry) == 2 and isinstance(entry[1], str):
            shadow = blocks.get(entry[1]) or {}
            if shadow.get("shadow"):
                out[name] = next(iter(shadow.get("fields", {}).values()))[0]
    return out


class BlockTableTests(unittest.TestCase):
    def test_every_block_of_the_extension_is_in_the_table(self):
        # Read off the extension's getInfo(): seven blocks, all commands.
        named = {
            "pressKey", "holdKey", "releaseKey",
            "clickMouse", "holdMouse", "releaseMouse", "moveMouse",
        }
        self.assertEqual(set(KEYSIM_BLOCKS[KEYSIM_EXTENSION]["blocks"]), named)
        for spec in KEYSIM_BLOCKS[KEYSIM_EXTENSION]["blocks"].values():
            self.assertEqual(spec["type"], "COMMAND")

    def test_the_url_is_the_gallery_script(self):
        self.assertEqual(EXTENSION_URLS[KEYSIM_EXTENSION], KEYSIM_URL)
        self.assertTrue(KEYSIM_URL.startswith("https://extensions.turbowarp.org/"))

    def test_the_namespace_is_always_available(self):
        self.assertIn("keySim", extensions.known_namespaces())
        self.assertIn("keySim", extensions.keysim_namespaces())

    def test_the_dropdowns_are_the_extensions_own(self):
        menus = KEYSIM_BLOCKS[KEYSIM_EXTENSION]["menus"]
        self.assertEqual(menus["BUTTONS"], ("0", "1", "2"))
        self.assertEqual(menus["AND_WAIT"], ("without waiting", "and wait"))
        self.assertIn(" ", menus["KEYS"])          # the space bar
        self.assertIn("ArrowUp", menus["KEYS"])
        self.assertIn("enter", KEYSIM_KEY_LABELS)

    def test_the_generic_spelling_reaches_every_block(self):
        for name, spec in KEYSIM_BLOCKS[KEYSIM_EXTENSION]["blocks"].items():
            call = extensions.resolve("keySim", name, len(spec["args"]))
            self.assertIsNotNone(call, name)
            self.assertEqual(call.full_opcode, f"{KEYSIM_EXTENSION}_{name}")

    def test_the_editor_offers_every_key_the_extension_knows(self):
        """The completion list is generated, but it has to be complete.

        A key the extension accepts and the editor never offers is a hint
        nobody can find; a key the editor offers and the compiler rejects is
        worse, because it compiles to a block that can never fire.
        """
        data = json.loads((ROOT / "vscode-jsw" / "data" / "jsw.json").read_text("utf-8"))
        offered = {entry["label"]: entry["value"] for entry in data["keysimKeys"]}
        for label, value in KEYSIM_KEY_LABELS.items():
            with self.subTest(key=label):
                self.assertEqual(offered.get(label), value)
        for value in KEYSIM_KEYS:
            if value == " ":
                self.assertEqual(offered.get("space"), " ")  # spelled as the name
                continue
            with self.subTest(key=value):
                self.assertIn(value, offered.values())
        self.assertEqual(
            {entry["label"] for entry in data["keysimButtons"]},
            set(KEYSIM_BLOCKS[KEYSIM_EXTENSION]["menus"]["BUTTONS"]),
        )
        self.assertEqual(
            {entry["label"] for entry in data["keysimWait"]},
            set(KEYSIM_BLOCKS[KEYSIM_EXTENSION]["menus"]["AND_WAIT"]),
        )


class AliasTests(unittest.TestCase):
    def test_the_friendly_names_are_the_same_blocks(self):
        cases = {
            ("press", 2): ("pressKey", {"AND_WAIT": "without waiting"}),
            ("pressAndWait", 2): ("pressKey", {"AND_WAIT": "and wait"}),
            ("hold", 1): ("holdKey", {}),
            ("release", 1): ("releaseKey", {}),
            ("moveTo", 2): ("moveMouse", {}),
        }
        for (method, count), (opcode, pinned) in cases.items():
            with self.subTest(method=method):
                call = extensions.resolve("keySim", method, count)
                self.assertEqual(call.opcode, opcode)
                for name, value in pinned.items():
                    self.assertEqual(call.values[name], value)

    def test_each_button_has_its_own_name(self):
        for method, button in (("clickLeft", "0"), ("clickMiddle", "1"), ("clickRight", "2")):
            with self.subTest(method=method):
                call = extensions.resolve("keySim", method, 2)
                self.assertEqual(call.opcode, "clickMouse")
                self.assertEqual(call.values["BUTTON"], button)
                self.assertEqual(call.values["SECONDS"], "0.1")
        for method, button in (("holdLeft", "0"), ("holdRight", "2")):
            self.assertEqual(extensions.resolve("keySim", method, 2).values["BUTTON"], button)
        self.assertEqual(
            extensions.resolve("keySim", "releaseMiddle", 0).values["BUTTON"], "1"
        )

    def test_the_click_takes_an_optional_duration(self):
        # `clickMiddle(x, y, seconds)` is the same alias with the pin lifted.
        short = extensions.resolve("keySim", "clickMiddle", 2)
        long = extensions.resolve("keySim", "clickMiddle", 3)
        self.assertEqual(short.opcode, long.opcode)
        self.assertIn("SECONDS", short.values)
        self.assertNotIn("SECONDS", long.values)
        self.assertIsNone(extensions.resolve("keySim", "clickMiddle", 4))

    def test_a_press_takes_an_optional_duration(self):
        """`press(key)` is `press(key, 0.1)` -- the extension's own default."""
        tap = extensions.resolve("keySim", "press", 1)
        self.assertEqual(tap.values["SECONDS"], "0.1")
        self.assertEqual(tap.values["AND_WAIT"], "without waiting")
        held = extensions.resolve("keySim", "pressAndWait", 2)
        self.assertNotIn("SECONDS", held.values)
        self.assertEqual(held.values["AND_WAIT"], "and wait")
        self.assertIsNone(extensions.resolve("keySim", "press", 3))

    def test_an_alias_rejects_the_wrong_number_of_arguments(self):
        self.assertIsNone(extensions.resolve("keySim", "hold", 0))
        self.assertIsNone(extensions.resolve("JSON", "isArray", 2))


class CompileTests(unittest.TestCase):
    def test_a_press_becomes_the_block_with_its_dropdowns(self):
        codegen, project = compile_source(
            'class a { public void greenFlag() { keySim.press("space", 0.2); } }'
        )
        blocks = blocks_of(project, "a")
        block = next(b for b in blocks.values()
                     if b["opcode"] == f"{KEYSIM_EXTENSION}_pressKey")
        self.assertEqual(menu_values(blocks, block),
                         {"KEY": " ", "AND_WAIT": "without waiting"})
        self.assertEqual(block["inputs"]["SECONDS"], [1, [4, "0.2"]])
        self.assertEqual(project.extensions, [KEYSIM_EXTENSION])

    def test_a_named_key_is_translated_to_the_value_the_dropdown_stores(self):
        _, project = compile_source(
            'class a { void f(){ keySim.hold("up arrow"); } }'
        )
        blocks = blocks_of(project, "a")
        block = next(b for b in blocks.values()
                     if b["opcode"] == f"{KEYSIM_EXTENSION}_holdKey")
        self.assertEqual(menu_values(blocks, block), {"KEY": "ArrowUp"})

    def test_a_key_the_extension_does_not_have_warns(self):
        codegen, _ = compile_source('class a { void f(){ keySim.press("spce", 0.2); } }')
        self.assertTrue(
            any("W0034" in d and "spce" in d for d in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_a_computed_key_is_not_checked(self):
        # The dropdown accepts a reporter, so only a literal can be judged.
        codegen, _ = compile_source(
            'public k = "space"; class a { void f(){ keySim.press(k, 0.2); } }'
        )
        self.assertFalse([d for d in codegen.diagnostics if "W0034" in d])

    def test_the_wrong_arity_names_the_form_that_fits(self):
        codegen, _ = compile_source("class a { void f(){ keySim.hold(); } }")
        messages = [d for d in codegen.diagnostics if "E0076" in d]
        self.assertTrue(messages, codegen.diagnostics)
        self.assertIn("takes 1 argument(s), not 0", messages[0])

    def test_a_command_alias_suggests_the_longer_name(self):
        # `clickMiddle` is the 0.1 s form; the duration form is `clickLeft`'s
        # optional third argument, so a 4-argument call has nowhere to go.
        codegen, _ = compile_source(
            "class a { void f(){ keySim.clickMiddle(1, 2, 3, 4); } }"
        )
        self.assertTrue(
            any("E0076" in d and "takes 2 or 3" in d for d in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_a_scratch_build_refuses_the_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "keys.jsw"
            source.write_text(
                'class keys {\n'
                '    public void greenFlag() { keySim.hold("a"); }\n'
                '}\n',
                encoding="utf-8",
            )
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["build", str(source), "-o", str(Path(tmp) / "out.sb3"),
                                 "--target", "scratch"])
            self.assertEqual(code, 1, err.getvalue())
            self.assertIn("E0084", err.getvalue())
            self.assertFalse((Path(tmp) / "out.sb3").exists())

    def test_a_turbowarp_build_of_the_same_source_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "keys.jsw"
            source.write_text(
                'class keys {\n'
                '    public void greenFlag() { keySim.hold("a"); }\n'
                '}\n',
                encoding="utf-8",
            )
            target = Path(tmp) / "out.sb3"
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["build", str(source), "-o", str(target)])
            self.assertEqual(code, 0, err.getvalue())
            self.assertTrue(target.exists())

    def test_the_extension_url_lands_in_the_project(self):
        _, project = compile_source('class a { void f(){ keySim.moveTo(1, 2); } }')
        self.assertEqual(project.extension_urls.get(KEYSIM_EXTENSION), KEYSIM_URL)


def roundtrip(source: str) -> str:
    from jswc.decompile import Decompiler
    from jswc.sb3 import build_project_dict
    from jswc.sb3_read import LoadedProject

    _, project = compile_source(source)
    data, _ = build_project_dict(project)
    return Decompiler(LoadedProject(data=data)).decompile()


class DecompileTests(unittest.TestCase):
    def test_a_block_comes_back_as_the_short_spelling(self):
        from jswc import extensions as tables

        namespace, method, call = tables.reverse(
            f"{KEYSIM_EXTENSION}_clickMouse",
            {"BUTTON": "0", "X": "-100", "Y": "20", "SECONDS": "0.1",
             "AND_WAIT": "without waiting"},
        )
        self.assertEqual((namespace, method), ("keySim", "clickLeft"))
        self.assertEqual(call.values["BUTTON"], "0")

    def test_a_given_duration_is_written_back(self):
        # The 0.1 s pin must not swallow a call that said 0.5.
        from jswc import extensions as tables

        namespace, method, call = tables.reverse(
            f"{KEYSIM_EXTENSION}_clickMouse",
            {"BUTTON": "1", "X": "0", "Y": "0", "SECONDS": "0.5",
             "AND_WAIT": "without waiting"},
        )
        self.assertEqual((namespace, method), ("keySim", "clickMiddle"))
        self.assertNotIn("SECONDS", call.values)

    def test_the_default_duration_stays_pinned(self):
        from jswc import extensions as tables

        _, _, call = tables.reverse(
            f"{KEYSIM_EXTENSION}_clickMouse",
            {"BUTTON": "0", "X": "1", "Y": "2", "SECONDS": "0.1",
             "AND_WAIT": "without waiting"},
        )
        self.assertEqual(call.values["SECONDS"], "0.1")

    def test_a_key_comes_back_by_its_name(self):
        source = roundtrip('class a { void f(){ keySim.press("space", 0.2); } }')
        self.assertIn('keySim.press("space", 0.2)', source)

    def test_the_source_survives_a_round_trip(self):
        first = roundtrip(
            'class a { void f(){ '
            'keySim.clickMiddle(0, 0, 0.5); keySim.hold("up arrow"); } }'
        )
        second = roundtrip(first)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
