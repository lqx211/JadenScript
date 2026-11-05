"""Canvas metadata: script positions, monitors and unattached blocks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import REFERENCE_PROJECT, blocks_of, compile_source, opcodes
from tests.test_roundtrip import write_side_files

from jswc.codegen import Codegen
from jswc.decompile import Decompiler
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import build_project_dict
from jswc.sb3_read import LoadedProject, load_sb3


def roundtrip(source: str):
    _, project = compile_source(source)
    data, _ = build_project_dict(project)
    source2 = Decompiler(LoadedProject(data=data)).decompile()
    return project, data, source2


class PlacementTests(unittest.TestCase):
    def test_at_comment_restores_the_script_position(self):
        source = """
        class a { // Sprite: a (0,0)
            public void greenFlag() { // at 120,240
                i = 1;
            }
        }
        """
        project, _, _ = roundtrip(source)
        target = next(t for t in project.targets if t.name == "a")
        hats = [b for b in target.blocks.values() if b.opcode == "event_whenflagclicked"]
        self.assertEqual(len(hats), 1)
        self.assertEqual((hats[0].x, hats[0].y), (120.0, 240.0))

    def test_positions_do_not_collide_with_generated_scripts(self):
        source = """
        class a { // Sprite: a (0,0)
            public void greenFlag() { // at 40,900
                i = 1;
            }
            public void whenCloned() {
                i = 2;
            }
        }
        """
        project, _, _ = roundtrip(source)
        target = next(t for t in project.targets if t.name == "a")
        placed = [(b.x, b.y) for b in target.blocks.values() if b.top_level]
        self.assertEqual(len(placed), len(set(placed)))


class MonitorTests(unittest.TestCase):
    def test_monitor_comments_are_recompiled(self):
        source = """
        public Player.score = 0;
        // monitor Stage.Player.score slider at 5,6 100x24 shown range 0..250 discrete
        class a { // Sprite: a (0,0)
            public void greenFlag() { Player.score = 1; }
        }
        """
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        monitors = data["monitors"]
        self.assertEqual(len(monitors), 1)
        monitor = monitors[0]
        self.assertEqual(monitor["opcode"], "data_variable")
        self.assertEqual(monitor["params"], {"VARIABLE": "Player.score"})
        self.assertEqual(monitor["mode"], "slider")
        self.assertEqual((monitor["x"], monitor["y"]), (5, 6))
        self.assertEqual((monitor["sliderMin"], monitor["sliderMax"]), (0, 250))
        self.assertTrue(monitor["isDiscrete"])
        self.assertTrue(monitor["visible"])

    def test_list_monitor_is_detected(self):
        source = """
        public notes = [];
        // monitor Stage.notes list at 378,0 100x74 hidden
        class a { // Sprite: a (0,0)
            public void greenFlag() { notes.add(1); }
        }
        """
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        monitor = data["monitors"][0]
        self.assertEqual(monitor["opcode"], "data_listcontents")
        self.assertEqual(monitor["mode"], "list")
        self.assertFalse(monitor["visible"])

    def test_variable_monitor_always_carries_the_slider_fields(self):
        """Scratch writes sliderMin/sliderMax/isDiscrete on every variable monitor."""
        source = """
        public Player.score = 0;
        public notes = [];
        // monitor Stage.Player.score default at 0,22 0x0 hidden
        // monitor Stage.notes list at 378,0 100x74 hidden
        class a { // Sprite: a (0,0)
            public void greenFlag() { Player.score = 1; }
        }
        """
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        var_monitor, list_monitor = data["monitors"]
        self.assertEqual(var_monitor["mode"], "default")
        self.assertEqual((var_monitor["sliderMin"], var_monitor["sliderMax"]), (0, 100))
        self.assertTrue(var_monitor["isDiscrete"])
        # list monitors do not have them at all
        self.assertNotIn("sliderMin", list_monitor)
        self.assertNotIn("isDiscrete", list_monitor)

    def test_watch_monitor_needs_no_state(self):
        source = """
        // watch Stage.sensing_timer at 94,73 0x0 shown
        class a { // Sprite: a (0,0)
            public void greenFlag() { i = 1; }
        }
        """
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        monitor = data["monitors"][0]
        self.assertEqual(monitor["opcode"], "sensing_timer")
        self.assertEqual(monitor["params"], {})
        self.assertIsNone(monitor["spriteName"])

    def test_monitors_survive_a_round_trip(self):
        source = """
        public Player.score = 0;
        // monitor Stage.Player.score slider at 5,6 100x24 shown range 0..250 discrete
        class a { // Sprite: a (0,0)
            public void greenFlag() { Player.score = 1; }
        }
        """
        project, _, source2 = roundtrip(source)
        self.assertIn("metadata slider Stage.Player.score", source2)
        _, again = compile_source(source2)
        data, _ = build_project_dict(again)
        self.assertEqual(len(data["monitors"]), 1)


class LooseBlockTests(unittest.TestCase):
    def test_loose_block_is_a_top_level_chain(self):
        source = """
        class a { // Sprite: a (0,0)
            public void greenFlag() { i = 1; }
            loose { // at 300,400
                Player.detached = 0;
                Control.wait(1);
            }
        }
        """
        project, _, _ = roundtrip(source)
        target = next(t for t in project.targets if t.name == "a")
        loose = [
            b
            for b in target.blocks.values()
            if b.top_level and b.opcode == "data_setvariableto"
        ]
        self.assertEqual(len(loose), 1)
        self.assertEqual((loose[0].x, loose[0].y), (300.0, 400.0))
        # the second statement is chained, not a separate script
        self.assertIsNotNone(loose[0].next)

    def test_loose_reporter_survives(self):
        source = """
        class a { // Sprite: a (0,0)
            public void greenFlag() { i = 1; }
            loose { // at 10,20
                len(Player.notes);
            }
        }
        """
        project, _, _ = roundtrip(source)
        target = next(t for t in project.targets if t.name == "a")
        tops = [b.opcode for b in target.blocks.values() if b.top_level]
        self.assertIn("operator_length", tops)

    def test_loose_block_decompiles(self):
        source = """
        class a { // Sprite: a (0,0)
            public void greenFlag() { i = 1; }
            loose { // at 300,400
                Player.detached = 0;
            }
        }
        """
        _, _, source2 = roundtrip(source)
        self.assertIn("loose {", source2)
        self.assertIn("at 300,400", source2)


class SubstackCallTests(unittest.TestCase):
    def test_extension_c_block_keeps_its_body(self):
        source = """
        class a { // Sprite: a (0,0)
            public void greenFlag() {
                Comments.commentC("why") {
                    i = 1;
                }
            }
        }
        """
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        target = next(t for t in data["targets"] if t["name"] == "a")
        call = next(
            b for b in target["blocks"].values()
            if isinstance(b, dict) and b["opcode"] == "lmscomments_commentC"
        )
        self.assertIn("SUBSTACK", call["inputs"])

    def test_nested_body_survives_a_round_trip(self):
        source = """
        class a { // Sprite: a (0,0)
            public void greenFlag() {
                Temp.forEachThreadVariable("i", 3) {
                    i = i;
                }
            }
        }
        """
        project, _, source2 = roundtrip(source)
        self.assertIn("forEachThreadVariable", source2)
        self.assertIn("{", source2)
        data, _ = build_project_dict(project)
        target = next(t for t in data["targets"] if t["name"] == "a")
        loop = next(
            b for b in target["blocks"].values()
            if isinstance(b, dict) and b["opcode"] == "lmsTempVars2_forEachThreadVariable"
        )
        self.assertIn("SUBSTACK", loop["inputs"])


class ThreadLoopTests(unittest.TestCase):
    def test_loop_variable_matches_the_body_reads(self):
        """The loop names a thread variable; the body must read the same one."""
        source = """
        #enable_feature<useTempVariables>;
        class a { // Sprite: a (0,0)
            public void greenFlag() {
                Temp.forEachThreadVariable("g", 3) {
                    i = _g;
                }
            }
        }
        """
        project, _, source2 = roundtrip(source)
        self.assertIn('forEachThreadVariable("_g"', source2)
        data, _ = build_project_dict(project)
        target = next(t for t in data["targets"] if t["name"] == "a")
        blocks = target["blocks"]
        loop = next(
            b for b in blocks.values()
            if isinstance(b, dict) and b["opcode"] == "lmsTempVars2_forEachThreadVariable"
        )
        self.assertEqual(loop["inputs"]["VAR"][1][1], "_g")
        reads = [
            b["inputs"]["VAR"][1][1]
            for b in blocks.values()
            if isinstance(b, dict) and b["opcode"] == "lmsTempVars2_getThreadVariable"
        ]
        self.assertIn("_g", reads)

    def test_reference_graph_loops_read_the_loop_variable(self):
        loaded = load_sb3(REFERENCE_PROJECT.parent)
        decompiler = Decompiler(loaded, max_inline=400)
        source = decompiler.decompile()
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            write_side_files(decompiler, workdir)
            (workdir / "s.jsw").write_text(source, encoding="utf-8")
            project = Codegen(
                Parser(tokenize(source)).parse(), base_dir=workdir, allow_undefined=True
            ).compile()
        data, _ = build_project_dict(project)
        graph = next(t for t in data["targets"] if t["name"] == "graph")
        blocks = graph["blocks"]
        loops = [
            b for b in blocks.values()
            if isinstance(b, dict) and b["opcode"] == "lmsTempVars2_forEachThreadVariable"
        ]
        self.assertTrue(loops)
        reads = {
            b["inputs"]["VAR"][1][1]
            for b in blocks.values()
            if isinstance(b, dict) and b["opcode"] == "lmsTempVars2_getThreadVariable"
        }
        for loop in loops:
            self.assertIn(loop["inputs"]["VAR"][1][1], reads)


class TargetMetadataTests(unittest.TestCase):
    def test_sprite_metadata_survives(self):
        source = """
        class a { // Sprite: a (12,-34) size=150 direction=-90 show=0 costume=2 volume=62.5 layer=5 rotation=leftright draggable=1
            public void greenFlag() { i = 1; }
        }
        """
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        target = next(t for t in data["targets"] if t["name"] == "a")
        self.assertEqual(target["currentCostume"], 2)
        self.assertEqual(target["volume"], 62.5)
        self.assertEqual(target["layerOrder"], 5)
        self.assertEqual(target["rotationStyle"], "left-right")
        self.assertTrue(target["draggable"])
        self.assertEqual((target["x"], target["y"]), (12, -34))
        self.assertEqual(target["size"], 150)
        self.assertFalse(target["visible"])

    def test_stage_metadata_survives(self):
        source = """
        stage { // Stage: costume=0 volume=90 layer=0 tempo=275.5 video=off videoTransparency=25
            public void whenStageClicked() { i = 1; }
        }
        class a { // Sprite: a (0,0)
            public void greenFlag() { i = 1; }
        }
        """
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        stage = data["targets"][0]
        self.assertEqual(stage["tempo"], 275.5)
        self.assertEqual(stage["volume"], 90)
        self.assertEqual(stage["videoState"], "off")
        self.assertEqual(stage["videoTransparency"], 25)

    def test_layer_order_is_unique(self):
        source = """
        class a { // Sprite: a (0,0) layer=2
            public void greenFlag() { i = 1; }
        }
        class b { // Sprite: b (0,0)
            public void greenFlag() { i = 1; }
        }
        """
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        orders = [t["layerOrder"] for t in data["targets"]]
        self.assertEqual(len(orders), len(set(orders)))


class UndefinedCalleeTests(unittest.TestCase):
    def test_undefined_callee_keeps_dangling_calls(self):
        source = """
        class a { // Sprite: a (0,0)
            public void greenFlag() { ghost(1); }
            private void ghost(arg1) { // undefined
            }
        }
        """
        _, project = compile_source(source)
        blocks = blocks_of(project, "a")
        ops = list(opcodes(project, "a"))
        self.assertIn("procedures_call", ops)
        self.assertNotIn("procedures_definition", ops)


if __name__ == "__main__":
    unittest.main()
