"""``doObfuscation`` and the eval warnings."""

from __future__ import annotations

import re
import unittest

from tests.helpers import blocks_of, compile_source

from jswc.codegen import Codegen
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import build_project_dict

SOURCE = """
#enable_feature<doObfuscation>;

public Player.score = 0;
public Player.notes = [];
// monitor Stage.Player.score slider at 5,6 100x24 shown range 0..250 discrete

class main { // Sprite: main (0,0) size=100 direction=90 show=1
    private local = 0;
    public void greenFlag() { // at 0,0
        Player.score += 1;
        Player.notes.add("x");
        local = Player.score;
        Control.broadcast("Ping");
        update(1);
    }
    public void update(n) { // proccode update dynamic %s
        local = n;
    }
    public void onPing() { // hook: broadcast Ping
        local = 2;
    }
}
"""

HIDDEN = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,}$")
HIDDEN_DOTTED = re.compile(r"^[A-Za-z][A-Za-z0-9]*\.[A-Za-z][A-Za-z0-9]*$")


def build(source: str, **kwargs):
    codegen = Codegen(Parser(tokenize(source)).parse(), allow_undefined=True, **kwargs)
    project = codegen.compile()
    data, _ = build_project_dict(project)
    return codegen, project, data


class ObfuscationTests(unittest.TestCase):
    def setUp(self):
        self.codegen, self.project, self.data = build(SOURCE)

    def test_class_and_state_names_are_hidden(self):
        stage, sprite = self.data["targets"]
        self.assertTrue(HIDDEN.match(stage["name"]), stage["name"])
        self.assertTrue(HIDDEN.match(sprite["name"]), sprite["name"])
        self.assertNotIn(stage["name"], {"Stage"})
        self.assertNotIn(sprite["name"], {"main"})
        self.assertEqual(len(stage["variables"]), 1)
        name = next(iter(stage["variables"].values()))[0]
        self.assertTrue(HIDDEN_DOTTED.match(name), name)  # keeps Ns.name shape
        list_name = next(iter(stage["lists"].values()))[0]
        self.assertTrue(HIDDEN_DOTTED.match(list_name), list_name)
        local = next(iter(sprite["variables"].values()))[0]
        self.assertTrue(HIDDEN.match(local), local)  # sprite-local: one segment

    def test_no_original_name_survives(self):
        text = repr(self.data)
        for original in ("Player.score", "Player.notes", "main", "local"):
            self.assertNotIn(f"'{original}'", text)

    def test_broadcast_names_are_hidden(self):
        stage = self.data["targets"][0]
        (name,) = stage["broadcasts"].values()
        self.assertTrue(HIDDEN.match(name), name)
        self.assertNotEqual(name, "Ping")

    def test_custom_block_labels_are_hidden(self):
        """``Sprite.move %s`` tells the whole call graph; the label goes too."""
        proccodes = [
            block["mutation"]["proccode"]
            for target in self.data["targets"]
            for block in target["blocks"].values()
            if isinstance(block, dict) and "proccode" in (block.get("mutation") or {})
        ]
        self.assertTrue(proccodes)
        for proccode in proccodes:
            with self.subTest(proccode=proccode):
                self.assertNotIn(".", proccode.split(" %s")[0])
                self.assertFalse(proccode.startswith(("greenFlag", "onPing")), proccode)

    def test_a_custom_block_and_its_calls_share_one_label(self):
        labels = {}
        for target in self.data["targets"]:
            for block in target["blocks"].values():
                if not isinstance(block, dict):
                    continue
                mutation = block.get("mutation") or {}
                if "proccode" not in mutation:
                    continue
                labels.setdefault(mutation["proccode"], set()).add(block["opcode"])
        prototype = next(v for v in labels.values() if "procedures_prototype" in v)
        self.assertIn("procedures_call", prototype)

    def test_argument_names_are_hidden(self):
        import json as _json

        for target in self.data["targets"]:
            for block in target["blocks"].values():
                if not isinstance(block, dict):
                    continue
                names = (block.get("mutation") or {}).get("argumentnames")
                if isinstance(names, str) and names:
                    for name in _json.loads(names):
                        self.assertTrue(HIDDEN.match(name), name)

    def test_broadcast_stays_consistent(self):
        stage, sprite = self.data["targets"]
        (bid, broadcast_name), = stage["broadcasts"].items()
        blocks = sprite["blocks"]
        hat = next(b for b in blocks.values() if b["opcode"] == "event_whenbroadcastreceived")
        self.assertEqual(hat["fields"]["BROADCAST_OPTION"], [broadcast_name, bid])
        send = next(b for b in blocks.values() if b["opcode"] == "event_broadcast")
        self.assertEqual(send["inputs"]["BROADCAST_INPUT"][1], [11, broadcast_name, bid])

    def test_monitors_follow_the_renamed_state(self):
        stage = self.data["targets"][0]
        monitor = self.data["monitors"][0]
        variable_name, variable_id = next(iter(stage["variables"].values()))
        self.assertEqual(monitor["params"]["VARIABLE"], variable_name)
        self.assertEqual(monitor["id"], next(iter(stage["variables"])))
        self.assertEqual(monitor["mode"], "slider")
        self.assertEqual((monitor["x"], monitor["y"]), (5, 6))

    def test_hidden_names_are_deterministic(self):
        _, _, again = build(SOURCE)
        self.assertEqual(self.data["targets"][1]["name"], again["targets"][1]["name"])
        self.assertEqual(
            list(self.data["targets"][0]["variables"].values()),
            list(again["targets"][0]["variables"].values()),
        )

    def test_the_only_structural_change_is_the_string_pool(self):
        from collections import Counter

        """Pooling adds ``item of list`` reporters; nothing else may move."""
        plain_source = SOURCE.replace("#enable_feature<doObfuscation>;", "")
        _, _, plain = build(plain_source)

        def counts(data):
            return Counter(
                block["opcode"]
                for target in data["targets"]
                for block in target["blocks"].values()
                if isinstance(block, dict)
            )

        added = counts(self.data) - counts(plain)
        removed = counts(plain) - counts(self.data)
        self.assertEqual(set(added), {"data_itemoflist"})
        self.assertEqual(removed, Counter())

    def test_text_literals_move_into_one_list(self):
        stage = self.data["targets"][0]
        # Only readable text is pooled; ``1`` and ``2`` are numbers, which
        # carry nothing to hide.
        pools = {
            lid: entry[1]
            for lid, entry in stage["lists"].items()
            if entry[1] == ["x"]
        }
        self.assertEqual(len(pools), 1)
        name = next(iter(pools))
        # ... and every use is an item of that list.
        uses = [
            block
            for target in self.data["targets"]
            for block in target["blocks"].values()
            if isinstance(block, dict) and block["opcode"] == "data_itemoflist"
        ]
        self.assertTrue(uses)
        for block in uses:
            self.assertEqual(block["fields"]["LIST"][1], name)

    def test_the_old_feature_name_still_works(self):
        old = SOURCE.replace("doObfuscation", "hideObjectNames")
        parser = Parser(tokenize(old))
        parser.parse()
        self.assertTrue([d for d in parser.diagnostics if "I0002" in d], parser.diagnostics)
        _, _, data = build(old)
        self.assertEqual(data["targets"][1]["name"], self.data["targets"][1]["name"])

    def test_without_the_feature_nothing_is_hidden(self):
        _, _, data = build(SOURCE.replace("#enable_feature<doObfuscation>;", ""))
        self.assertEqual(data["targets"][0]["name"], "Stage")
        self.assertEqual(data["targets"][1]["name"], "main")
        self.assertEqual(next(iter(data["targets"][0]["variables"].values()))[0], "Player.score")


EVAL_SOURCE = """
class a { // Sprite: a (0,0)
    public void greenFlag() {
        name = "Ping";
        Control.broadcast(name);
        Control.createClone(name);
        Looks.switchCostume(name);
        x = menu("note", "NOTE", name);
    }
}
"""

STATIC_SOURCE = """
class a { // Sprite: a (0,0)
    public void greenFlag() {
        Control.broadcast("Ping");
        Control.createClone("_myself_");
        Looks.switchCostume("costume1");
        x = menu("note", "NOTE", 60);
    }
}
"""


def diagnostics(source: str) -> list[str]:
    codegen = Codegen(Parser(tokenize(source)).parse(), allow_undefined=True)
    codegen.compile()
    return [d for d in codegen.diagnostics if "eval:" in d]


class EvalWarningTests(unittest.TestCase):
    def test_runtime_names_are_reported(self):
        found = diagnostics(EVAL_SOURCE)
        self.assertEqual(len(found), 4, found)
        self.assertTrue(all("computed at runtime" in d for d in found))

    def test_literal_names_are_silent(self):
        self.assertEqual(diagnostics(STATIC_SOURCE), [])

    def test_a_variable_in_a_menu_field_is_reported(self):
        found = diagnostics(
            'class a { void f(){ n = "x"; y = menu("note", "NOTE", n); } }'
        )
        self.assertEqual(len(found), 1, found)


class CloneLeakTests(unittest.TestCase):
    """``createClone(X); stopThisScript();`` leaves the current clone behind."""

    LEAK = (
        "class A { // Sprite: A\n"
        "    public void whenCloned() {\n"
        '        Control.createClone("B");\n'
        "        Control.stopThisScript();\n"
        "    }\n}\n"
    )
    FIXED = LEAK.replace("Control.stopThisScript();", "Control.deleteThisClone();")

    def notes(self, source: str) -> list[str]:
        from jswc import lints
        from jswc.lexer import tokenize
        from jswc.parser import Parser

        return lints.run(Parser(tokenize(source)).parse())

    def test_the_handoff_is_reported(self):
        notes = self.notes(self.LEAK)
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("W0024", notes[0])

    def test_deleting_the_clone_is_not_reported(self):
        self.assertEqual(self.notes(self.FIXED), [])

    def test_a_handoff_with_work_after_it_is_not_reported(self):
        source = (
            "class A { // Sprite: A\n"
            "    public void whenCloned() {\n"
            '        Control.createClone("B");\n'
            "        Looks.say(1);\n"
            "        Control.deleteThisClone();\n"
            "    }\n}\n"
        )
        self.assertEqual(self.notes(source), [])

    def test_the_lint_runs_during_a_build(self):
        codegen, _ = compile_source(self.LEAK)
        self.assertTrue([d for d in codegen.diagnostics if "W0024" in d], codegen.diagnostics)

    def test_the_fix_replaces_stop_with_delete(self):
        _, _, data = build(self.FIXED)
        ops = [b["opcode"] for t in data["targets"] for b in t["blocks"].values()
               if isinstance(b, dict)]
        self.assertIn("control_delete_this_clone", ops)
        self.assertNotIn("control_stop", ops)


if __name__ == "__main__":
    unittest.main()
