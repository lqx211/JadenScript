from __future__ import annotations

import json
import struct
import tempfile
import unittest
import wave
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

from jswc.codegen import Codegen
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import build_project_dict, write_sb3
from jswc.validator import validate


def png_bytes(width: int = 3, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def wav_bytes(frames: int = 100) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


class AssetImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "art").mkdir()
        (self.root / "art" / "piano.png").write_bytes(png_bytes(10, 6))
        (self.root / "art" / "hit.wav").write_bytes(wav_bytes(200))

    def tearDown(self):
        self.tmp.cleanup()

    def compile(self, source: str):
        program = Parser(tokenize(source)).parse()
        return Codegen(program, base_dir=self.root).compile()

    def test_png_becomes_a_costume_with_real_dimensions(self):
        project = self.compile(
            "class a { // Sprite: a (0,0) size=100 direction=90 show=1\n"
            '  import "art/piano.png";\n'
            "  void f(){}\n}"
        )
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual(len(target.costumes), 1)
        costume = target.costumes[0]
        self.assertEqual(costume["name"], "piano")
        self.assertEqual(costume["dataFormat"], "png")
        self.assertEqual((costume["rotationCenterX"], costume["rotationCenterY"]), (5.0, 3.0))
        self.assertIn(costume["md5ext"], project.assets)
        self.assertEqual(validate(project), [])

    def test_wav_becomes_a_sound_with_sample_metadata(self):
        project = self.compile(
            "class a { void f(){}"
            ' import "art/hit.wav" as Hit; }'
        )
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual(len(target.sounds), 1)
        sound = target.sounds[0]
        self.assertEqual(sound["name"], "Hit")
        self.assertEqual(sound["rate"], 8000)
        self.assertEqual(sound["sampleCount"], 200)

    def test_assets_are_written_into_the_zip(self):
        project = self.compile(
            'class a { import "art/piano.png"; import "art/hit.wav"; void f(){} }'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = write_sb3(project, Path(tmp) / "out.sb3")
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            for md5ext in project.assets:
                self.assertIn(md5ext, names)

    def test_missing_asset_is_a_diagnostic_not_a_crash(self):
        program = Parser(tokenize('class a { import "nope.png"; void f(){} }')).parse()
        codegen = Codegen(program, base_dir=self.root)
        project = codegen.compile()
        self.assertTrue(any("not found" in note for note in codegen.diagnostics))
        self.assertEqual(validate(project), [])

    def test_three_import_spellings(self):
        program = Parser(
            tokenize(
                'class a { import "x.png"; import "y.png" as Y; import "z.png" (as Z); void f(){} }'
            )
        ).parse()
        imports = [m for m in program.classes[0].members if type(m).__name__ == "ImportDecl"]
        self.assertEqual([(m.path, m.name) for m in imports],
                         [("x.png", None), ("y.png", "Y"), ("z.png", "Z")])

    def test_project_json_keeps_costume_references(self):
        project = self.compile('class a { import "art/piano.png"; void f(){} }')
        data, _ = build_project_dict(project)
        sprite = data["targets"][1]
        self.assertEqual(sprite["costumes"][0]["name"], "piano")


if __name__ == "__main__":
    unittest.main()
