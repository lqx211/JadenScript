"""sb3 → jsw → sb3 must reproduce the same scripts and initial state.

Every case is built from source *inside* the test, so a stale artifact in
``build/`` can never mask a regression.
"""

from __future__ import annotations

import json

import tempfile
import unittest
from pathlib import Path

from tests.helpers import REFERENCE_PROJECT, ROOT, SAMPLE_SOURCE
from tests.roundtrip import (
    canonical_from_loaded,
    canonical_from_project,
    report_difference,
)

from jswc.codegen import Codegen
from jswc.decompile import Decompiler
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import write_sb3
from jswc.sb3 import build_project_dict
from jswc.sb3_read import load_sb3

EXAMPLES = sorted((ROOT / "examples").glob("*.jsw"))


def compile_source_file(source_path: Path, workdir: Path):
    source = source_path.read_text(encoding="utf-8")
    program = Parser(tokenize(source)).parse()
    codegen = Codegen(
        program, base_dir=source_path.parent, allow_undefined=True,
        features={"commentMetadata"},
    )
    return codegen, codegen.compile()


def write_side_files(decompiler: Decompiler, workdir: Path) -> None:
    asset_dir = workdir / decompiler.asset_prefix.strip("/")
    for name, payload in {**decompiler.loaded.assets, **decompiler.extra_files}.items():
        target = asset_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def roundtrip(source_path: Path, workdir: Path) -> tuple[dict, dict, list[str]]:
    _, first = compile_source_file(source_path, workdir)
    sb3_path, _ = write_sb3(first, workdir / "step1.sb3")

    loaded = load_sb3(sb3_path)
    decompiler = Decompiler(loaded)
    source = decompiler.decompile()
    write_side_files(decompiler, workdir)
    step2 = workdir / "step2.jsw"
    step2.write_text(source, encoding="utf-8")

    _, second = compile_source_file(step2, workdir)
    return canonical_from_project(first), canonical_from_project(second), decompiler.diagnostics


class RoundTripTests(unittest.TestCase):
    def assert_source_roundtrips(self, source_path: Path):
        with tempfile.TemporaryDirectory() as tmp:
            before, after, _ = roundtrip(source_path, Path(tmp))
        self.assertEqual(before, after, f"{source_path.name}: " + report_difference(before, after))

    def test_every_example_roundtrips_exactly(self):
        self.assertTrue(EXAMPLES, "no examples found")
        for source in EXAMPLES:
            with self.subTest(example=source.name):
                self.assert_source_roundtrips(source)

    def test_sample_jsw_roundtrips_exactly(self):
        self.assert_source_roundtrips(SAMPLE_SOURCE)

    def test_no_unsupported_blocks_in_examples(self):
        for source in EXAMPLES:
            with tempfile.TemporaryDirectory() as tmp:
                _, _, diagnostics = roundtrip(source, Path(tmp))
            unsupported = [d for d in diagnostics if "unsupported" in d]
            self.assertEqual(unsupported, [], f"{source.name}: {unsupported}")

    def test_large_lists_move_to_jsa_files(self):
        items = ", ".join(str(i) for i in range(120))
        source = (
            "#enable_feature<commentMetadata>;\n"
            f"public big = [{items}];\n"
            "class a { void f(){ n = big.length(); } }\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            path = workdir / "big.jsw"
            path.write_text(source, encoding="utf-8")
            _, project = compile_source_file(path, workdir)
            sb3_path, _ = write_sb3(project, workdir / "big.sb3")

            loaded = load_sb3(sb3_path)
            decompiler = Decompiler(loaded, max_inline=50)
            text = decompiler.decompile()

            self.assertIn('import "assets/lists/big.jsa";', text)
            self.assertIn("lists/big.jsa", decompiler.extra_files)
            self.assertLess(len(text), 2000, "the inline list should not be in the source")

            write_side_files(decompiler, workdir)
            (workdir / "step2.jsw").write_text(text, encoding="utf-8")
            _, second = compile_source_file(workdir / "step2.jsw", workdir)

        stage = second.stage
        self.assertEqual(len(next(iter(stage.lists.values()))[1]), 120)

    def test_reference_project_recompiles_into_a_valid_project(self):
        loaded = load_sb3(REFERENCE_PROJECT)
        decompiler = Decompiler(loaded)
        source = decompiler.decompile()
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            write_side_files(decompiler, workdir)
            path = workdir / "sample.jsw"
            path.write_text(source, encoding="utf-8")
            program = Parser(tokenize(source)).parse()
            codegen = Codegen(program, base_dir=workdir, allow_undefined=True)
            project = codegen.compile()
        self.assertGreater(sum(len(t.blocks) for t in project.targets), 2000)

    def test_reference_project_keeps_its_state(self):
        loaded = load_sb3(REFERENCE_PROJECT)
        decompiler = Decompiler(loaded)
        source = decompiler.decompile()
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            write_side_files(decompiler, workdir)
            path = workdir / "sample.jsw"
            path.write_text(source, encoding="utf-8")
            program = Parser(tokenize(source)).parse()
            project = Codegen(program, base_dir=workdir, allow_undefined=True).compile()
        before = canonical_from_loaded(loaded)
        after = canonical_from_project(project)
        self.assertEqual(_lists(before), _lists(after))
        self.assertEqual(len(before["targets"]), len(after["targets"]))

    def _rebuild_reference(self):
        # The folder form loads the binaries too, which the cost/sound and
        # backdrop checks need.
        loaded = load_sb3(REFERENCE_PROJECT.parent)
        decompiler = Decompiler(loaded, max_inline=400)
        source = decompiler.decompile()
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            write_side_files(decompiler, workdir)
            path = workdir / "sample.jsw"
            path.write_text(source, encoding="utf-8")
            program = Parser(tokenize(source)).parse()
            project = Codegen(program, base_dir=workdir, allow_undefined=True).compile()
        return loaded, source, project

    def test_reference_project_keeps_target_metadata(self):
        """Backdrop, tempo, volume and layer order decide what the stage shows."""
        loaded, _, project = self._rebuild_reference()
        data, _ = build_project_dict(project)
        before = {t["name"]: t for t in loaded.data["targets"]}
        for target in data["targets"]:
            original = before[target["name"]]
            self.assertEqual(target["currentCostume"], original["currentCostume"])
            self.assertEqual(target["volume"], original["volume"])
            self.assertEqual(target["layerOrder"], original["layerOrder"])
            for costume in target["costumes"]:
                self.assertTrue(costume.get("rotationCenterX") is not None)
            if target["isStage"]:
                self.assertEqual(target["tempo"], original["tempo"])
                self.assertEqual(
                    [c["md5ext"] for c in target["costumes"]],
                    [c["md5ext"] for c in original["costumes"]],
                )

    def test_reference_project_keeps_the_monitors(self):
        loaded, _, project = self._rebuild_reference()
        data, _ = build_project_dict(project)
        key = lambda m: (m.get("opcode"), json.dumps(m.get("params"), sort_keys=True),
                         m.get("spriteName"), m.get("mode"))
        self.assertEqual(
            sorted(key(m) for m in data["monitors"]),
            sorted(key(m) for m in loaded.data["monitors"]),
        )

    def test_reference_project_keeps_script_positions(self):
        loaded, _, project = self._rebuild_reference()
        data, _ = build_project_dict(project)
        for original, rebuilt in zip(loaded.data["targets"], data["targets"]):
            before = {
                (b.get("x"), b.get("y"), b["opcode"])
                for b in original["blocks"].values()
                if isinstance(b, dict) and b.get("topLevel")
            }
            after = {
                (float(b.get("x", 0)), float(b.get("y", 0)), b["opcode"])
                for b in rebuilt["blocks"].values()
                if isinstance(b, dict) and b.get("topLevel")
            }
            missing = {b for b in before if b not in after and b[2] != "operator_join"}
            self.assertLessEqual(
                len(missing), 3, f"{original['name']} lost {sorted(missing)[:5]}"
            )

    def test_reference_project_keeps_the_thread_variable_writes(self):
        """A dropped decrement turned the synth fade loop into a spin."""
        _, _, project = self._rebuild_reference()
        data, _ = build_project_dict(project)
        changes = [
            block
            for target in data["targets"]
            for block in target["blocks"].values()
            if isinstance(block, dict)
            and block["opcode"] == "lmsTempVars2_changeThreadVariable"
        ]
        self.assertTrue(changes)
        for block in changes:
            self.assertIn("NUM", block["inputs"], json.dumps(block["inputs"]))

    def test_reference_project_decompiles_with_a_measurable_coverage(self):
        loaded = load_sb3(REFERENCE_PROJECT)
        decompiler = Decompiler(loaded)
        source = decompiler.decompile()
        self.assertGreater(len(source.splitlines()), 400)
        self.assertIn("class graphical", source)
        self.assertIn("class core", source)
        unsupported = [d for d in decompiler.diagnostics if "unsupported" in d]
        all_blocks = sum(
            len([b for b in t["blocks"].values() if isinstance(b, dict)]) for t in loaded.targets
        )
        self.assertLess(len(unsupported), all_blocks * 0.05, "\n".join(unsupported[:10]))


def _lists(canonical: dict):
    return sorted(
        (target["name"], entry[0], len(entry[1]))
        for target in canonical["targets"]
        for entry in target["lists"]
    )


if __name__ == "__main__":
    unittest.main()
