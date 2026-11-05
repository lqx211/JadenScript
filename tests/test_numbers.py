"""Number literals: hex/binary/octal input, bare numbers on the way out."""

from __future__ import annotations

import unittest

from tests.helpers import blocks_of, compile_source

from jswc.decompile import Decompiler
from jswc.sb3 import build_project_dict
from jswc.sb3_read import LoadedProject

SOURCE = """
class a { // Sprite: a (0,0)
    public void greenFlag() {
        hex = 0x100000000;
        binary = 0b1010;
        octal = 0o17;
        float_ = 1.5;
        padded = "007";
        text = "abc";
    }
}
"""


def roundtrip(source: str) -> str:
    _, project = compile_source(source)
    data, _ = build_project_dict(project)
    return Decompiler(LoadedProject(data=data)).decompile()


def values(project) -> dict:
    out = {}
    for block in blocks_of(project, "a").values():
        if block["opcode"] == "data_setvariableto":
            name = block["fields"]["VARIABLE"][0]
            payload = block["inputs"]["VALUE"][1]
            out[name] = payload[1] if isinstance(payload, list) else payload
    return out


class NumberLiteralTests(unittest.TestCase):
    def test_bases_compile_to_decimal_integers(self):
        _, project = compile_source(SOURCE)
        got = values(project)
        self.assertEqual(got["hex"], "4294967296")
        self.assertEqual(got["binary"], "10")
        self.assertEqual(got["octal"], "15")
        self.assertEqual(got["float_"], "1.5")

    def test_numbers_are_written_without_quotes(self):
        source = roundtrip(SOURCE)
        self.assertIn("hex = 4294967296;", source)   # decimal, not 0x…
        self.assertIn("binary = 10;", source)
        self.assertIn("octal = 15;", source)
        self.assertIn("float_ = 1.5;", source)

    def test_text_that_is_not_a_number_stays_quoted(self):
        source = roundtrip(SOURCE)
        self.assertIn('padded = "007";', source)   # leading zeros are text
        self.assertIn('text = "abc";', source)

    def test_hex_text_keeps_its_spelling_and_compiles_to_decimal(self):
        source = """
        class a { // Sprite: a (0,0)
            public void greenFlag() { mask = "0x100000000"; }
        }
        """
        text = roundtrip(source)
        self.assertIn("mask = 0x100000000;", text)
        _, project = compile_source(text)
        self.assertEqual(values(project)["mask"], "4294967296")

    def test_empty_and_fractionless_values_stay_text(self):
        source = roundtrip(
            'class a { // Sprite: a (0,0)\n'
            '    public void greenFlag() { e = ""; dot = "2."; }\n'
            "}"
        )
        self.assertIn('e = "";', source)
        self.assertIn('dot = "2.";', source)


if __name__ == "__main__":
    unittest.main()
