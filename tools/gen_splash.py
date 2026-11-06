#!/usr/bin/env python3
"""Draw the power-on splash, the way a Dell OptiPlex GX620 draws its own.

The machine has a text card, not a framebuffer, so a "bitmap" here means the
CP437 block characters: each cell is eight by sixteen pixels, and the half-block
glyphs split it into two square pixels of eight by eight. An eighty by thirty
screen is therefore a small 80 x 60 bitmap, which is enough for a chunky
wordmark -- and it is what the card can actually show.

This emits `userspace/i386/splash.inc`: the whole static screen as 2400
(character, attribute) pairs, which the BIOS copies into the text plane in one
rep movsw. Everything that changes -- the POST log, the step in the corner, the
countdown -- is printed on top of it by the firmware.

    python3 tools/svg_raster.py ~/Downloads/turbowarp.svg
    python3 tools/gen_splash.py

The logo is the drawing in turbowarp.svg, rasterised into one EGA colour per
pixel (`build/i386/logo.ega`) by tools/svg_raster.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "userspace" / "i386" / "assets" / "lists" / "font.jsa"
OUT = ROOT / "userspace" / "i386" / "splash.inc"
DEFAULT_RUNS = ROOT / "build" / "i386" / "logo.ega"

COLS, ROWS = 80, 30
PAPER_N = 1                  # blue, the card's colour 1
PAPER = 0x1F                 # white on blue: the whole screen
HEAD = 0x1E                  # yellow on blue: the corner notice

#: where the mark goes, and how many sub-pixel rows it gets
MARK_ROW, MARK_SUB_ROWS = 1, 52

#: CP437, for the half blocks the bitmap is made of
FULL, TOP, BOTTOM = 0xDB, 0xDF, 0xDC


def glyphs() -> list[int]:
    """The 256 glyphs, each fifteen rows, stored bottom row first."""
    rows = [int(v) for v in json.loads(FONT.read_text())]
    return [rows[i * 15:(i + 1) * 15] for i in range(256)]


def bitmaps(font: list[list[int]]) -> dict[int, list[list[int]]]:
    """Every glyph as fifteen rows of eight bits, top row first."""
    return {ch: [[(row >> (7 - x)) & 1 for x in range(8)]
                 for row in reversed(g)] for ch, g in enumerate(font)}


#: the glyphs a picture is allowed to be made of: the shading and block
#: characters, which are what a bitmap looks like in a text mode. Letters are
#: left out on purpose -- a patch that happens to look like an "H" should read
#: as the shape it is, not as the letter.
GRAPHICS = [0x20] + list(range(0xB0, 0xE0))


def closest(glyphs: dict[int, list[list[int]]], patch: list[list[int]]) -> int:
    """The glyph whose bitmap is nearest the patch, by a plain pixel count."""
    best, bestd = 0x20, sum(sum(row) for row in patch) + 1
    for ch in GRAPHICS:
        glyph = glyphs[ch]
        d = 0
        for y in range(15):
            grow, prow = glyph[y], patch[y]
            for x in range(8):
                if grow[x] != prow[x]:
                    d += 1
        if d < bestd:
            best, bestd = ch, d
    return best


def load_ega(path: Path) -> tuple[int, int, list[str]]:
    """`logo.ega` -- "163x314" and then one hex colour per pixel, '.' for page."""
    lines = path.read_text().splitlines()
    width, height = (int(v) for v in lines[0].split("x"))
    return width, height, lines[1:1 + height]


def text(cells: list[list[str]], attr: list[int], x: int, y: int, s: str) -> None:
    for i, ch in enumerate(s):
        if 0 <= x + i < COLS:
            cells[y][x + i] = ch
            attr[y * COLS + x + i] = PAPER


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo", type=Path, default=DEFAULT_RUNS)
    args = ap.parse_args()

    font = bitmaps(glyphs())
    cells = [[" "] * COLS for _ in range(ROWS)]
    attr = [PAPER] * (COLS * ROWS)

    # --- the mark, dead centre. POST's log prints over the top left of it the
    # way a real BIOS prints over whatever the OEM drew, so nothing is reserved.
    # Every cell is eight by fifteen pixels of font, so instead of
    # half blocks -- which halve the horizontal resolution again -- each cell
    # gets the glyph whose own bitmap is the closest match to the pixels of its
    # most common colour, drawn in that colour. The shading characters come out
    # as dithering along the edges, which is what makes it read as a picture
    # rather than as a staircase.
    width, height, art = load_ega(args.logo)
    rows_cells = MARK_SUB_ROWS // 2
    cols_cells = round(width * rows_cells / height)
    gw, gh = 8, 15
    x0 = (COLS - cols_cells) // 2               # centred
    for r in range(rows_cells):
        for c in range(cols_cells):
            patch = []
            seen: dict[str, int] = {}
            for py in range(gh):
                line = []
                for px in range(gw):
                    sx = int((c * gw + px) * width / (cols_cells * gw))
                    sy = int((r * gh + py) * height / (rows_cells * gh))
                    ink = art[sy][sx]
                    line.append(ink)
                    seen[ink] = seen.get(ink, 0) + 1
                patch.append(line)
            seen.pop(".", None)
            if not seen:
                continue                        # nothing of the drawing here
            ink = max(seen, key=lambda k: seen[k])
            mask = [[1 if v == ink else 0 for v in row] for row in patch]
            cells[MARK_ROW + r][x0 + c] = chr(closest(font, mask))
            # an attribute is background in the high nibble and foreground in
            # the low one; getting the two the wrong way round paints the
            # picture in blue on its own colour, which reads as an outline
            attr[(MARK_ROW + r) * COLS + x0 + c] = \
                (PAPER_N << 4) | int(ink, 16)
    centre = x0 + cols_cells // 2               # in cells

    # --- the corner notice, row 1: row 0 is the step, which the BIOS prints
    line = "DEL = Setup    M = Options"
    text(cells, attr, COLS - len(line), 1, line)
    for c in range(COLS - len(line), COLS):
        attr[1 * COLS + c] = HEAD

    # --- the label under the mark, then the model line. The countdown the
    # firmware prints takes row 27 on the left, so the labels sit below it.
    text(cells, attr, centre - len("TurboWarp") // 2, ROWS - 2, "TurboWarp")
    model = "JSW 80386 BIOS  A07"
    text(cells, attr, centre - len(model) // 2, ROWS - 1, model)

    lines = ["splash_cells:                     ; 80 x 30, from tools/gen_splash.py"]
    for r in range(ROWS):
        body = ",".join(f"0x{ord(cells[r][c]):02x},0x{attr[r * COLS + c]:02x}"
                        for c in range(COLS))
        lines.append("    db " + body)
    OUT.write_text("\n".join(lines) + "\n")
    print(f"{OUT}  {ROWS} rows x {COLS} cells, mark {cols_cells} x "
          f"{rows_cells} cells at column {x0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
