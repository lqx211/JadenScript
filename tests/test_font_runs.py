"""The console font, decoded from the rv64 bitmap into pen strokes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.font_runs import HEIGHT, LAST, FIRST, WIDTH, decode, runs_of_glyph, write

RV64_FONT = Path("userspace/rv64/assets/lists/font.jsa")


def blank_font() -> list[int]:
    return [0] * (256 * HEIGHT)


def set_glyph(font: list[int], code: int, rows: list[int]) -> None:
    """`rows` is written the way the table stores it: bottom row first."""
    font[code * HEIGHT:(code + 1) * HEIGHT] = rows


class RunsTests(unittest.TestCase):
    def test_a_full_row_becomes_one_run(self):
        rows = [0] * HEIGHT
        rows[0] = 0xFF
        self.assertEqual(runs_of_glyph(rows), [(0, HEIGHT - 1, WIDTH)])

    def test_y_is_measured_from_the_top_not_the_storage_order(self):
        """The table is bottom row first; a pen draws from the top down."""
        rows = [0] * HEIGHT
        rows[0] = 0x80          # the table's first row is the glyph's *bottom*
        rows[HEIGHT - 1] = 0x01  # and its last row is the top
        self.assertEqual(runs_of_glyph(rows), [(7, 0, 1), (0, HEIGHT - 1, 1)])

    def test_gaps_split_a_row(self):
        rows = [0] * HEIGHT
        rows[2] = 0b10010010 >> 0
        runs = runs_of_glyph(rows)
        self.assertEqual(runs, [(0, HEIGHT - 3, 1), (3, HEIGHT - 3, 1), (6, HEIGHT - 3, 1)])


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.font = blank_font()
        self.glyph_rows = [0] * HEIGHT
        self.glyph_rows[HEIGHT - 1] = 0x7E      # a 6 wide bar on the glyph's top
        self.glyph_rows[HEIGHT - 2] = 0x10      # a stem under it

    def test_offsets_are_one_based_so_scratch_can_get_them(self):
        set_glyph(self.font, FIRST + 1, self.glyph_rows)   # '!'
        data = decode(self.font)
        self.assertEqual(data["start"][0], 1)              # the space, empty
        self.assertEqual(data["start"][1], 1)              # '!' starts at 1 too
        self.assertEqual(data["start"][2], 7)              # two runs, x y len each

    def test_the_last_glyph_has_an_end(self):
        set_glyph(self.font, LAST, self.glyph_rows)
        data = decode(self.font)
        self.assertEqual(len(data["start"]), LAST - FIRST + 2)
        self.assertEqual(data["start"][-1], len(data["runs"]) + 1)

    def test_a_space_draws_nothing(self):
        data = decode(self.font)
        self.assertEqual(data["chars"][0], " ")
        self.assertEqual(data["start"][1], data["start"][0])

    def test_every_character_of_the_range_is_covered(self):
        data = decode(self.font)
        self.assertEqual(len(data["chars"]), LAST - FIRST + 1)
        self.assertEqual("".join(data["chars"]), "".join(chr(c) for c in range(FIRST, LAST + 1)))

    def test_write_round_trips(self):
        set_glyph(self.font, FIRST, self.glyph_rows)
        with tempfile.TemporaryDirectory() as tmp:
            write(decode(self.font), Path(tmp))
            for name in ("Font.chars", "Font.start", "Font.runs"):
                self.assertTrue((Path(tmp) / f"{name}.jsa").exists(), name)
            chars = json.loads((Path(tmp) / "Font.chars.jsa").read_text())
            self.assertEqual(len(chars), LAST - FIRST + 1)


@unittest.skipUnless(RV64_FONT.exists(), "the rv64 font is not in this checkout")
class RealFontTests(unittest.TestCase):
    def test_the_t_bar_is_on_top(self):
        """The whole point of the row flip: 'T' has its crossbar at the top."""
        data = decode(json.loads(RV64_FONT.read_text(encoding="utf-8")))
        g = data["chars"].index("T") + 1
        runs = data["runs"][data["start"][g - 1] - 1:data["start"][g] - 1]
        first = tuple(runs[:3])
        self.assertEqual(first, (0, 3, 7))

    def test_the_same_glyph_is_read_the_same_way_twice(self):
        font = json.loads(RV64_FONT.read_text(encoding="utf-8"))
        self.assertEqual(decode(font), decode(font))


if __name__ == "__main__":
    unittest.main()
