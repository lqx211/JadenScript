from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.helpers import SAMPLE_SOURCE, compile_source, reference_json

from jswc.sb3 import build_project_dict, write_project_json, write_sb3
from jswc.validator import validate

SMALL = """
class player { // Sprite: player (0,0) size=100 direction=90 show=1
    public void greenFlag(){
        public Player.score = 0;
        Piano.notes.add("a");
        Pen.eraseAll();
    }
}
"""


class Sb3Tests(unittest.TestCase):
    def test_target_keys_match_the_reference_project(self):
        reference = reference_json()
        ref_stage = next(t for t in reference["targets"] if t["isStage"])
        ref_sprite = next(t for t in reference["targets"] if not t["isStage"])

        _, project = compile_source(SMALL)
        data, _ = build_project_dict(project)
        stage, sprite = data["targets"][0], data["targets"][1]

        self.assertEqual(set(stage), set(ref_stage))
        self.assertEqual(set(sprite), set(ref_sprite))
        self.assertEqual(set(sprite["costumes"][0]), set(ref_sprite["costumes"][0]))

    def test_project_metadata(self):
        _, project = compile_source(SMALL)
        data, _ = build_project_dict(project)
        self.assertEqual(data["meta"]["semver"], "3.0.0")
        self.assertEqual(data["meta"]["platform"]["name"], "TurboWarp")
        self.assertEqual(data["extensions"], ["pen"])

    def test_community_extensions_get_extension_urls(self):
        _, project = compile_source(
            "class a { void f(){ x = (1) if (c) else (2); Pen.eraseAll(); Music.playNote(60, 1); } }"
        )
        data, _ = build_project_dict(project)
        self.assertEqual(
            data["extensionURLs"],
            {"utilities": "https://extensions.turbowarp.org/utilities.js"},
            "community extensions need a URL or TurboWarp refuses to load the project",
        )
        self.assertIn("pen", data["extensions"])
        self.assertIn("music", data["extensions"])
        self.assertNotIn("pen", data["extensionURLs"])

    def test_no_extension_urls_key_when_all_extensions_are_builtin(self):
        _, project = compile_source("class a { void f(){ Pen.eraseAll(); } }")
        data, _ = build_project_dict(project)
        self.assertNotIn("extensionURLs", data)

    def test_zip_layout(self):
        _, project = compile_source(SMALL)
        with tempfile.TemporaryDirectory() as tmp:
            path, data = write_sb3(project, Path(tmp) / "out.sb3")
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                project_json = json.loads(archive.read("project.json"))
            self.assertIn("project.json", names)
            self.assertTrue(any(name.endswith(".svg") for name in names))
            self.assertEqual(project_json["targets"][1]["name"], "player")

    def test_project_json_writer(self):
        _, project = compile_source(SMALL)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_project_json(project, Path(tmp) / "project.json")
            data = json.loads(path.read_text())
            self.assertEqual(len(data["targets"]), 2)

    def test_build_is_deterministic(self):
        _, first = compile_source(SMALL)
        _, second = compile_source(SMALL)
        self.assertEqual(build_project_dict(first), build_project_dict(second))

    def test_reference_file_compiles_to_a_valid_project(self):
        _, project = compile_source(SAMPLE_SOURCE.read_text(encoding="utf-8"))
        self.assertEqual(validate(project), [])
        data, _ = build_project_dict(project)
        self.assertEqual([t["name"] for t in data["targets"]],
                         ["Stage", "graphical", "synth", "core"])
        self.assertGreater(sum(len(t["blocks"]) for t in data["targets"]), 1000)

    def test_every_block_is_reachable_from_a_top_level_block(self):
        _, project = compile_source(SAMPLE_SOURCE.read_text(encoding="utf-8"))
        for target in project.targets:
            blocks = target.blocks
            reached = set()
            stack = [b.id for b in blocks.values() if b.top_level]
            while stack:
                bid = stack.pop()
                if bid in reached:
                    continue
                reached.add(bid)
                block = blocks[bid]
                if block.next:
                    stack.append(block.next)
                for raw in block.inputs.values():
                    if isinstance(raw, list) and len(raw) > 1 and isinstance(raw[1], str):
                        stack.append(raw[1])
            self.assertEqual(set(blocks), reached, f"unreachable blocks in {target.name}")


if __name__ == "__main__":
    unittest.main()
