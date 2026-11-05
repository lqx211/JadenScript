"""The micro:bit extension: blocks, menu constants, display matrix and hats.

Every opcode and menu item here comes from
``scratch-vm/src/extensions/scratch3_microbit/index.js``, so a project built
from this source loads in Scratch and TurboWarp unchanged.
"""

from __future__ import annotations

import unittest

from tests.helpers import blocks_of, compile_source, opcodes

from jswc.codegen import Codegen
from jswc.decompile import Decompiler
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import build_project_dict
from jswc.sb3_read import LoadedProject


def roundtrip(source: str, **kwargs):
    codegen, project = compile_source(source, **kwargs)
    data, _ = build_project_dict(project)
    text = Decompiler(LoadedProject(data=data)).decompile()
    again = Codegen(Parser(tokenize(text)).parse(), allow_undefined=kwargs.get("allow_undefined", True))
    rebuilt = again.compile()
    return codegen, project, text, again, rebuilt


def opcodes_of(project) -> list[str]:
    return sorted(b.opcode for target in project.targets for b in target.blocks.values())


class MicrobitBlockTests(unittest.TestCase):
    def test_display_and_text_and_clear(self):
        _, project = compile_source(
            'class a { void f() {\n'
            '    microbit.display("0101010101100010101000100");\n'
            '    microbit.displayText("hi");\n'
            '    microbit.clearDisplay();\n'
            "} }"
        )
        ops = opcodes(project, "a")
        for opcode in ("microbit_displaySymbol", "microbit_displayText", "microbit_displayClear"):
            self.assertIn(opcode, ops)
        self.assertIn("microbit", project.extensions)

    def test_the_matrix_is_a_25_character_string(self):
        _, project = compile_source(
            'class a { void f() { microbit.display("0101010101100010101000100"); } }'
        )
        block = next(b for b in blocks_of(project, "a").values()
                     if b["opcode"] == "microbit_displaySymbol")
        self.assertEqual(block["inputs"]["MATRIX"][1], [10, "0101010101100010101000100"])

    def test_a_binary_literal_is_the_same_picture(self):
        _, project = compile_source(
            "class a { void f() { microbit.display(0b0); } }"
        )
        block = next(b for b in blocks_of(project, "a").values()
                     if b["opcode"] == "microbit_displaySymbol")
        self.assertEqual(block["inputs"]["MATRIX"][1], [10, "0" * 25])

        _, bits = compile_source("class a { void f() { microbit.display(0b111); } }")
        block = next(b for b in blocks_of(bits, "a").values()
                     if b["opcode"] == "microbit_displaySymbol")
        self.assertEqual(block["inputs"]["MATRIX"][1], [10, "0" * 22 + "111"])

    def test_a_short_matrix_warns(self):
        codegen, _ = compile_source('class a { void f() { microbit.display("0101"); } }')
        self.assertTrue(any("W0022" in d for d in codegen.diagnostics))

    def test_a_matrix_that_is_not_binary_warns(self):
        codegen, _ = compile_source('class a { void f() { microbit.display("hello"); } }')
        self.assertTrue(any("W0022" in d for d in codegen.diagnostics))

    def test_buttons_are_a_menu(self):
        _, project = compile_source(
            'class a { void f() { if (microbit.isButtonPressed("A")) { x = 1; } } }'
        )
        self.assertIn("microbit_isButtonPressed", opcodes(project, "a"))
        menu = next(b for b in blocks_of(project, "a").values()
                    if b["opcode"] == "microbit_menu_buttons")
        self.assertTrue(menu["shadow"])
        self.assertEqual(menu["fields"]["buttons"][0], "A")

    def test_every_button_constant_maps_to_its_menu_value(self):
        from jswc.extension_blocks import MICROBIT_CONSTANTS

        for constant, value in (("buttonA", "A"), ("buttonB", "B")):
            with self.subTest(constant=constant):
                _, project = compile_source(
                    f"class a {{ void f() {{ Looks.say(microbit.{constant}); }} }}"
                )
                said = next(b for b in blocks_of(project, "a").values()
                            if b["opcode"] == "looks_say")
                self.assertEqual(said["inputs"]["MESSAGE"][1], [10, value])
        self.assertEqual(MICROBIT_CONSTANTS["buttonA"], "A")

    def test_any_is_the_keyword_or_the_string(self):
        for written in ("any", '"any"'):
            with self.subTest(written=written):
                _, project = compile_source(
                    f"class a {{ void f() {{ if (microbit.isButtonPressed({written})) {{ x = 1; }} }} }}"
                )
                menu = next(b for b in blocks_of(project, "a").values()
                            if b["opcode"] == "microbit_menu_buttons")
                self.assertEqual(menu["fields"]["buttons"][0], "any")

    def test_tilt_angle_is_a_reporter(self):
        _, project = compile_source(
            "class a { void f() { Looks.say(microbit.tiltAngle(microbit.front)); } }"
        )
        self.assertIn("microbit_getTiltAngle", opcodes(project, "a"))

    def test_tilted_uses_the_wider_menu(self):
        _, project = compile_source(
            "class a { void f() { if (microbit.isTilted(microbit.back)) { x = 1; } } }"
        )
        menu = next(b for b in blocks_of(project, "a").values()
                    if b["opcode"] == "microbit_menu_tiltDirectionAny")
        self.assertEqual(menu["fields"]["tiltDirectionAny"][0], "back")


class MicrobitHatTests(unittest.TestCase):
    HOOKS = {
        "when [A] button pressed": "microbit_whenButtonPressed",
        "when [shaken]": "microbit_whenGesture",
        "when tilted [left]": "microbit_whenTilted",
        "when pin [2] connected": "microbit_whenPinConnected",
    }

    def test_each_hook_becomes_its_hat(self):
        for hook, opcode in self.HOOKS.items():
            with self.subTest(hook=hook):
                _, project = compile_source(
                    f"class a {{ public void h() {{ // hook: {hook}\n Looks.say(1); }} }}"
                )
                self.assertIn(opcode, opcodes(project, "a"))

    def test_the_hat_carries_its_menu_value(self):
        _, project = compile_source(
            "class a { public void h() { // hook: when tilted [right]\n"
            "    Looks.say(1);\n} }"
        )
        hat = next(b for b in blocks_of(project, "a").values()
                   if b["opcode"] == "microbit_whenTilted")
        menu = next(b for b in blocks_of(project, "a").values()
                    if b["opcode"] == "microbit_menu_tiltDirectionAny")
        self.assertEqual(menu["fields"]["tiltDirectionAny"][0], "right")
        self.assertEqual(hat["inputs"]["DIRECTION"][1], menu["parent"] and
                         [bid for bid, b in blocks_of(project, "a").items()
                          if b["opcode"] == "microbit_menu_tiltDirectionAny"][0])

    def test_hats_decompile_back_to_the_hook(self):
        for hook in self.HOOKS:
            with self.subTest(hook=hook):
                _, project = compile_source(
                    f"class a {{ public void h() {{ // hook: {hook}\n Looks.say(1); }} }}"
                )
                text = Decompiler(
                    LoadedProject(data=build_project_dict(project)[0])
                ).decompile()
                self.assertIn(f"// hook: {hook}", text)
                self.assertIn("public void microbit.", text)


class MicrobitRoundTripTests(unittest.TestCase):
    SOURCE = (
        "class a {\n"
        "    public void h() { // hook: when [B] button pressed\n"
        "        if (microbit.isTilted(microbit.front)) { microbit.display(0b101); }\n"
        "        Looks.say(microbit.tiltAngle(microbit.left));\n"
        "        Looks.say(microbit.isButtonPressed(microbit.buttonB));\n"
        "        microbit.displayText(\"ok\");\n"
        "        microbit.clearDisplay();\n"
        "    }\n"
        "}\n"
    )

    def test_the_whole_surface_round_trips(self):
        codegen, project, text, again, rebuilt = roundtrip(self.SOURCE)
        self.assertEqual(codegen.diagnostics, [])
        self.assertEqual(again.diagnostics, [])
        self.assertEqual(opcodes_of(project), opcodes_of(rebuilt))

    def test_the_constants_are_written_back_as_constants(self):
        _, _, text, _, _ = roundtrip(self.SOURCE)
        self.assertIn("microbit.buttonB", text)
        self.assertIn("microbit.front", text)


if __name__ == "__main__":
    unittest.main()
