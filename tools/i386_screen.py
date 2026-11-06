#!/usr/bin/env python3
"""Render what the machine is showing, without a browser.

The display card is a text plane: 80 x 30 cells of (character, attribute). This
boots the model, presses whatever keys you ask for, and turns those cells into
a PNG using the *same* glyph bitmap and the same sixteen colours the Scratch
display sprite uses -- so the picture is the machine's screen, not an artist's
idea of it.

    python3 tools/i386_screen.py                          # POST, after the boot
    python3 tools/i386_screen.py --keys 0x53 -o menu.png  # the boot menu
    python3 tools/i386_screen.py --keys 0x53,0x50,0x1c    # the setup utility
    python3 tools/i386_screen.py --keys 0x53,0x50,0x50,0x50,0x50,0x1c --keys "m 400\\n"

Keys are scan codes, exactly as tools/i386_jsw_check.py takes them.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import i386_image  # noqa: E402
import i386_ref as ref  # noqa: E402

FONT = ROOT / "userspace" / "i386" / "assets" / "lists" / "font.jsa"

#: the sixteen VGA colours, as the display sprite packs them
EGA = [0, 170, 43520, 43690, 11141120, 11141290, 11162880, 11184810,
       5592405, 5592575, 5635925, 5636095, 16733525, 16733695, 16777045,
       16777215]

#: a scan code for every character the shell and the BIOS can be asked to type
ASCII_KEYS = {
    " ": 0x39, "\n": 0x1C, "-": 0x0C, ".": 0x34, ":": 0, "/": 0x35,
    "0": 0x0B, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
    "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A,
}
for _i, _ch in enumerate("qwertyuiop"):
    ASCII_KEYS[_ch] = 0x10 + _i
for _i, _ch in enumerate("asdfghjkl"):
    ASCII_KEYS[_ch] = 0x1E + _i
for _i, _ch in enumerate("zxcvbnm"):
    ASCII_KEYS[_ch] = 0x2C + _i


def scan_codes(text: str) -> list[int]:
    """Turn what somebody typed into the scan codes the 8042 sends."""
    out = []
    for ch in text.lower():
        if ch not in ASCII_KEYS:
            raise SystemExit(f"{ch!r} has no scan code in this table")
        out.append(ASCII_KEYS[ch])
    return out


def boot(keys: list[int], limit: int, stage: tuple[int, int] = (80, 30)) -> ref.Machine:
    mach = ref.Machine()
    mach.view_cols, mach.view_rows = stage
    mach.ram[:] = i386_image.build()
    mach.keys.extend(keys)
    mach.sreg[ref.SEG_CS] = ref.RESET_CS
    mach.eip = ref.RESET_IP
    while mach.keys and mach.steps < limit:
        mach.run(limit=min(limit, mach.steps + 250_000))
    mach.run(limit=mach.steps + 400_000)
    return mach


def glyphs() -> list[list[int]]:
    rows = [int(v) for v in json.loads(FONT.read_text())]
    return [rows[i * 15:(i + 1) * 15] for i in range(256)]


def render(mach: ref.Machine, font: list[list[int]],
           stage: tuple[int, int] = (80, 30)) -> tuple[int, int, bytearray]:
    cols, rows = stage
    width = cols * 8
    height = rows * 16
    pixels = bytearray(width * height * 3)
    for row in range(rows):
        for col in range(cols):
            at = ref.TEXT_BASE + (row * ref.TEXT_COLS + col) * 2
            char = mach.read(at, 1)
            attr = mach.read(at + 1, 1)
            paper, ink = EGA[attr >> 4], EGA[attr & 15]
            x0, y0 = col * 8, row * 16
            bits = font[char]
            for g in range(1, 16):
                y = y0 + 16 - g
                if y >= height:
                    continue
                line = bits[g - 1]
                for b in range(8):
                    colour = ink if (line >> (7 - b)) & 1 else paper
                    o = (y * width + x0 + b) * 3
                    pixels[o] = (colour >> 16) & 0xFF
                    pixels[o + 1] = (colour >> 8) & 0xFF
                    pixels[o + 2] = colour & 0xFF
            y = y0
            for b in range(8):
                o = (y * width + x0 + b) * 3
                pixels[o] = (paper >> 16) & 0xFF
                pixels[o + 1] = (paper >> 8) & 0xFF
                pixels[o + 2] = paper & 0xFF
    return width, height, pixels


def write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)                            # no filter
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keys", action="append", default=[],
                    help="scan codes (0x53) or text to type (\"m 400\\n\"); repeatable")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "build/i386/screen.png")
    ap.add_argument("--limit", type=int, default=12_000_000)
    ap.add_argument("--text", action="store_true", help="print it as text as well")
    ap.add_argument("--stage", default="80x30",
                    help="cells the card can show, e.g. 60x22 for a 480x360 stage")
    args = ap.parse_args()

    codes: list[int] = []
    for spec in args.keys:
        if spec.lower().startswith("0x") or spec.isdigit():
            codes += [int(part, 0) for part in spec.split(",")]
        else:
            codes += scan_codes(spec)

    cols, rows = (int(v) for v in args.stage.lower().split("x"))
    mach = boot(codes, args.limit, (cols, rows))
    width, height, pixels = render(mach, glyphs(), (cols, rows))
    write_png(args.out, width, height, pixels)
    print(f"{args.out}  {width}x{height}  {mach.steps} instructions")
    if args.text:
        print(mach.text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
