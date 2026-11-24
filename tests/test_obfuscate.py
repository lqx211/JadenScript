"""``doObfuscation`` and the eval warnings."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.helpers import blocks_of, compile_source

from jswc.codegen import Codegen
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import build_project_dict
from jswc.validator import validate

ROOT = Path(__file__).resolve().parent.parent

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

#: State, private block and message names are numbers, and they are counted
#: from zero: `v7`, `p3`, `c0`.
COUNTED_VAR = re.compile(r"^v\d+$")
COUNTED_PROC = re.compile(r"^p\d+(?: %[sbn])*$")
COUNTED_MESSAGE = re.compile(r"^c\d+$")
#: Sprites keep word names: their number is the whole project's ordering, which
#: a reader could follow from the target list.
HIDDEN = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,}$")


def build(source: str, **kwargs):
    codegen = Codegen(Parser(tokenize(source)).parse(), allow_undefined=True, **kwargs)
    project = codegen.compile()
    data, _ = build_project_dict(project)
    return codegen, project, data


def block_dicts(data):
    for target in data["targets"]:
        for block in target["blocks"].values():
            if isinstance(block, dict):
                yield block


def proccodes(data):
    for block in block_dicts(data):
        mutation = block.get("mutation") or {}
        if "proccode" in mutation:
            yield block["opcode"], mutation["proccode"]


class ObfuscationTests(unittest.TestCase):
    def setUp(self):
        self.codegen, self.project, self.data = build(SOURCE)

    # -- names -------------------------------------------------------------
    def test_the_stage_keeps_its_name(self):
        """The sb3 schema pins ``targets[0].name`` to "Stage"; a hidden one makes
        scratch.mit.edu answer "Could not parse as a valid SB2 or SB3 project"."""
        stage = self.data["targets"][0]
        self.assertEqual(stage["name"], "Stage")
        self.assertTrue(stage["isStage"])

    def test_sprite_names_are_counted(self):
        """``s0 s1 …`` -- sprites are numbers like everything else."""
        source = """
#enable_feature<doObfuscation>;
class one { public void greenFlag() { x = 1; } }
class two { public void greenFlag() { y = 1; } }
class three { public void greenFlag() { z = 1; } }
class four { public void greenFlag() { w = 1; } }
"""
        _, _, data = build(source)
        names = [target["name"] for target in data["targets"][1:]]
        self.assertEqual(len(names), 4)
        self.assertEqual(sorted(names), ["s0", "s1", "s2", "s3"])
        # The numbers are handed out in shuffled order, like every other
        # counter: the first sprite in the target list is not `s0`.
        self.assertNotEqual(names, ["s0", "s1", "s2", "s3"])

    def test_variables_and_lists_are_numbers_from_one_counter(self):
        """``v0 v1 v2 …`` — one counter for every scope, so a Stage global and a
        sprite local sit next to each other and neither spelling says which one
        belongs to which sprite."""
        numbers = []
        for target in self.data["targets"]:
            for entry in target["variables"].values():
                self.assertTrue(COUNTED_VAR.match(entry[0]), entry[0])
                numbers.append(int(entry[0][1:]))
            for entry in target["lists"].values():
                self.assertTrue(COUNTED_VAR.match(entry[0]), entry[0])
                numbers.append(int(entry[0][1:]))
        self.assertEqual(len(numbers), len(set(numbers)), "a number was reused")
        self.assertEqual(sorted(numbers), list(range(len(numbers))),
                         "the counter does not run from 0 without gaps")

    def test_decoy_numbers_sit_among_the_real_ones(self):
        """The counter is shuffled, so a decoy gets a *low* number too.

        Ids do not change with obfuscation, so the plain build's ids name the
        real objects and everything else is a decoy -- and if the numbering were
        handed out in declaration order, every decoy would come after every real
        name.
        """
        _, _, plain = build(SOURCE.replace("#enable_feature<doObfuscation>;", ""))

        def numbers(data):
            out = {}
            for target in data["targets"]:
                for ident, entry in target["variables"].items():
                    out[("var", ident)] = int(entry[0][1:])
                for ident, entry in target["lists"].items():
                    out[("list", ident)] = int(entry[0][1:])
            return out

        def ids(data):
            out = set()
            for target in data["targets"]:
                out |= {("var", ident) for ident in target["variables"]}
                out |= {("list", ident) for ident in target["lists"]}
            return out

        real = ids(plain)
        every = numbers(self.data)
        self.assertLess(real, set(every), "no decoy state was added")
        decoys = [number for key, number in every.items() if key not in real]
        self.assertTrue(decoys)
        self.assertLess(min(decoys), max(number for key, number in every.items()
                                         if key in real),
                        "every decoy was numbered after every real name")

    def test_scatter_hands_numbers_out_in_no_order(self):
        import random

        from jswc.obfuscate import _scatter

        order = _scatter(40, random.Random("test"))
        self.assertEqual(sorted(order), list(range(40)))
        self.assertNotEqual(order, list(range(40)))

    def test_broadcast_names_are_hidden(self):
        stage = self.data["targets"][0]
        names = list(stage["broadcasts"].values())
        self.assertGreaterEqual(len(names), 1)
        for name in names:
            self.assertTrue(COUNTED_MESSAGE.match(name), name)
        self.assertNotIn("Ping", names)

    def test_custom_block_labels_are_hidden(self):
        """``Sprite.move %s`` tells the whole call graph; the label goes too."""
        labels = list(proccodes(self.data))
        self.assertTrue(labels)
        for opcode, proccode in labels:
            with self.subTest(proccode=proccode):
                self.assertTrue(COUNTED_PROC.match(proccode), proccode)

    def test_no_original_name_survives(self):
        text = repr(self.data)
        for original in ("Player.score", "Player.notes", "main", "local", "Ping"):
            self.assertNotIn(f"'{original}'", text)

    def test_every_call_has_its_definition(self):
        """A call and its prototype share one label; a decoy block has none."""
        for target in self.data["targets"]:
            declared = {
                block["mutation"]["proccode"]
                for block in target["blocks"].values()
                if isinstance(block, dict) and block["opcode"] == "procedures_prototype"
            }
            for block in target["blocks"].values():
                if isinstance(block, dict) and block["opcode"] == "procedures_call":
                    self.assertIn(block["mutation"]["proccode"], declared)
            called = {
                block["mutation"]["proccode"]
                for block in target["blocks"].values()
                if isinstance(block, dict) and block["opcode"] == "procedures_call"
            }
            self.assertTrue(declared - called,
                            "a project with no never-called block has no decoy")

    def test_argument_names_are_hidden(self):
        for block in block_dicts(self.data):
            names = (block.get("mutation") or {}).get("argumentnames")
            if isinstance(names, str) and names:
                for name in json.loads(names):
                    self.assertTrue(HIDDEN.match(name), name)

    def test_every_prototype_is_well_formed(self):
        """A prototype with mismatched placeholders/arguments stops the editor loading."""
        seen = 0
        for block in block_dicts(self.data):
            if block["opcode"] != "procedures_prototype":
                continue
            seen += 1
            mutation = block["mutation"]
            ids = json.loads(mutation["argumentids"])
            names = json.loads(mutation["argumentnames"])
            defaults = json.loads(mutation["argumentdefaults"])
            self.assertEqual(len(ids), len(names), mutation["proccode"])
            self.assertEqual(len(ids), len(defaults), mutation["proccode"])
            self.assertEqual(
                len(re.findall(r"%[sbn]", mutation["proccode"])), len(ids),
                f"{mutation['proccode']} has the wrong number of placeholders",
            )
            for arg_id in ids:
                self.assertIn(arg_id, block["inputs"], "a parameter has no socket")
        self.assertGreater(seen, 0)

    def test_argument_reporters_follow_the_renamed_parameters(self):
        """A reporter looks its parameter up by name; a rename must reach both."""
        for target in self.data["targets"]:
            declared = set()
            for block in target["blocks"].values():
                if not isinstance(block, dict):
                    continue
                names = (block.get("mutation") or {}).get("argumentnames")
                if isinstance(names, str) and names:
                    declared |= set(json.loads(names))
            for block in target["blocks"].values():
                if isinstance(block, dict) and block["opcode"].startswith("argument_reporter_"):
                    self.assertIn(block["fields"]["VALUE"][0], declared)

    def test_thread_variable_names_are_hidden(self):
        """``lmsTempVars2`` keeps the name as text, so the rename has to reach it.

        The name is not a variable id: it is the string the extension looks the
        thread variable up by. Left alone, every temp variable in a published
        project is still called what the source called it.
        """
        source = (
            "#enable_feature<doObfuscation>;\n"
            "#enable_feature<useTempVariables>;\n"
            "class main {\n"
            "    public void greenFlag() {\n"
            "        _rate = 1;\n"
            "        _rate += 2;\n"
            "    }\n"
            "}\n"
        )
        _, _, data = build(source)
        names = {
            block["inputs"]["VAR"][1][1]
            for target in data["targets"]
            for block in target["blocks"].values()
            if isinstance(block, dict) and block["opcode"].startswith("lmsTempVars2_")
            and block["inputs"].get("VAR", [None])[0] == 1
        }
        self.assertTrue(names, "no thread variable survived to be renamed")
        self.assertNotIn("_rate", names, "a thread variable kept its source name")
        for name in names:
            # The leading underscore is the extension's own convention and is
            # kept; the name behind it is not.
            self.assertTrue(re.fullmatch(r"_?[A-Za-z][A-Za-z0-9]{2,}", name), name)

    def test_broadcast_stays_consistent(self):
        stage, sprite = self.data["targets"]
        (bid, broadcast_name), = [
            (bid, name) for bid, name in stage["broadcasts"].items()
            if name == next(
                b["fields"]["BROADCAST_OPTION"][0]
                for b in sprite["blocks"].values()
                if isinstance(b, dict) and b["opcode"] == "event_whenbroadcastreceived"
            )
        ]
        hat = next(b for b in sprite["blocks"].values()
                   if isinstance(b, dict) and b["opcode"] == "event_whenbroadcastreceived")
        self.assertEqual(hat["fields"]["BROADCAST_OPTION"], [broadcast_name, bid])
        send = next(b for b in sprite["blocks"].values()
                    if isinstance(b, dict) and b["opcode"] == "event_broadcast")
        self.assertEqual(send["inputs"]["BROADCAST_INPUT"][1], [11, broadcast_name, bid])

    def test_monitors_follow_the_renamed_state(self):
        stage = self.data["targets"][0]
        monitor = self.data["monitors"][0]
        # Declaration order is shuffled on purpose, so the monitor is matched by
        # id: the name it shows has to be the name the variable carries.
        ident = monitor["id"]
        self.assertIn(ident, stage["variables"], "the monitor points at nothing")
        self.assertEqual(monitor["params"]["VARIABLE"], stage["variables"][ident][0])
        self.assertEqual(monitor["mode"], "slider")
        self.assertEqual((monitor["x"], monitor["y"]), (5, 6))

    def test_hidden_names_are_deterministic(self):
        _, _, again = build(SOURCE)
        self.assertEqual(self.data["targets"][1]["name"], again["targets"][1]["name"])
        self.assertEqual(
            list(self.data["targets"][0]["variables"].values()),
            list(again["targets"][0]["variables"].values()),
        )

    # -- the string pool ---------------------------------------------------
    def _pool_reads(self):
        reads = []
        for block in block_dicts(self.data):
            if block["opcode"] == "data_itemoflist":
                reads.append((block["fields"]["LIST"][1], int(block["inputs"]["INDEX"][1][1])))
        return reads

    def _pool_lists(self):
        """``list id -> items`` for the lists that pooled literals are read from."""
        stage = self.data["targets"][0]
        idents = {ident for ident, _ in self._pool_reads()}
        return {lid: entry[1] for lid, entry in stage["lists"].items() if lid in idents}

    def _pool_holding(self, text):
        for lid, items in self._pool_lists().items():
            if text in items:
                return lid, items
        self.fail(f"{text!r} is not in any pool")

    def test_the_pool_is_split_over_several_lists(self):
        """One list holding every string in a project is the string table."""
        calls = "\n        ".join(f'update("word{index}text");' for index in range(12))
        source = (
            "#enable_feature<doObfuscation>;\n"
            "class a {\n"
            "    public void update(n) { // proccode update dynamic %s\n"
            "    }\n"
            "    public void greenFlag() {\n"
            f"        {calls}\n"
            "    }\n"
            "}\n"
        )
        _, _, data = build(source)
        self.data = data
        pools = self._pool_lists()
        self.assertGreater(len(pools), 1, "every literal landed in one list")
        reads = {ident for ident, _ in self._pool_reads()}
        self.assertEqual(reads, set(pools), "a pool is read but not declared")

    def test_one_pool_puts_everything_in_one_list(self):
        _, _, data = build(SOURCE, obf_pools=1)
        self.data = data
        self.assertEqual(len(self._pool_lists()), 1)

    def test_text_moves_into_a_list(self):
        self.assertIn("x", self._pool_holding("x")[1], "a string is pooled")

    def test_an_integer_becomes_a_variable(self):
        """The cheapest reporter there is, and it hides the value just as well.

        Measured on the i386 machine: `item i of list` for every integer cost
        3.3x the emulated work, `--obf-ints none` cost nothing, and one variable
        per constant costs 1.4x -- for the same hiding, because a variable's
        initial value is state like a list item is.
        """
        codegen, _, _ = build(SOURCE)
        self.assertTrue(codegen.constant_variables, "no integer became a variable")
        stage = self.data["targets"][0]
        for text, ident in codegen.constant_variables.items():
            self.assertIn(ident, stage["variables"], f"{text} has no variable")
            self.assertEqual(stage["variables"][ident][1], int(text))
        # No *read* resolves to an integer any more: that is the expensive form.
        # (Junk items that look like numbers are fine -- nothing reads them.)
        pools = self._pool_lists()
        for ident, index in self._pool_reads():
            item = str(pools[ident][index - 1])
            self.assertFalse(
                re.fullmatch(r"-?\d+", item),
                f"{item} is still pooled in a list",
            )

    def test_an_integer_in_a_text_slot_keeps_its_spelling(self):
        """`say("007")` is text: seven would print `7`."""
        source = SOURCE.replace('Player.notes.add("x");', 'Player.notes.add("007");')
        codegen, _, data = build(source)
        self.assertNotIn("007", codegen.constant_variables)
        stage = data["targets"][0]
        items = [item for entry in stage["lists"].values() for item in entry[1]]
        self.assertIn("007", items, "the text integer lost its spelling")

    def test_every_pool_is_shuffled_and_buried_in_junk(self):
        index_of = {}
        for ident, index in self._pool_reads():
            index_of.setdefault(ident, set()).add(index)
        for ident, items in self._pool_lists().items():
            used = sorted(index_of[ident])
            self.assertGreater(len(items), len(used),
                               f"pool {ident} has no junk in it")
            self.assertGreater(min(used), 1, "the first real string is the first item")
            self.assertEqual(len(items), len({str(item) for item in items}),
                             "duplicate junk")

    def test_every_pool_reference_points_at_a_real_index(self):
        pools = self._pool_lists()
        for ident, index in self._pool_reads():
            self.assertIn(ident, pools, "a read points at a list that is not there")
            self.assertTrue(1 <= index <= len(pools[ident]), (ident, index))

    def test_floats_stay_inline(self):
        """A float is a number with no words in it; only integers are pooled."""
        source = SOURCE.replace("update(1);", "update(1.5);")
        _, _, data = build(source)
        stage = data["targets"][0]
        every_item = {str(item) for target in data["targets"]
                      for entry in target["lists"].values() for item in entry[1]}
        self.assertNotIn("1.5", every_item)

    def test_integers_can_stay_inline(self):
        """``--obf-ints none``: a CPU cannot afford a list lookup per literal."""
        _, _, data = build(SOURCE, obf_ints=False)
        every_item = {str(item) for target in data["targets"]
                      for entry in target["lists"].values() for item in entry[1]}
        self.assertNotIn("1", every_item)
        self.assertNotIn("2", every_item)
        self.assertIn("x", every_item, "strings are still pooled")

    # -- the rest of the workspace -----------------------------------------
    def test_obfuscation_removes_nothing(self):
        from collections import Counter

        plain_source = SOURCE.replace("#enable_feature<doObfuscation>;", "")
        _, _, plain = build(plain_source)

        def counts(data):
            return Counter(
                block["opcode"]
                for target in data["targets"]
                for block in target["blocks"].values()
                if isinstance(block, dict)
            )

        removed = counts(plain) - counts(self.data)
        self.assertEqual(removed, Counter(), "a block disappeared")

    def test_decoys_are_never_reached(self):
        """The added blocks sit in definitions nothing calls, so they cost nothing."""
        declared = set()
        used = set()
        for target in self.data["targets"]:
            for block in target["blocks"].values():
                if not isinstance(block, dict):
                    continue
                if block["opcode"] == "procedures_prototype":
                    declared.add(block["mutation"]["proccode"])
                elif block["opcode"] == "procedures_call":
                    used.add(block["mutation"]["proccode"])
        self.assertTrue(declared - used, "no custom block is left uncalled")
        # The extra lists carry text as well, so the pool is not the big one.
        stage = self.data["targets"][0]
        sizes = sorted(len(entry[1]) for entry in stage["lists"].values() if entry[1])
        self.assertGreater(len(sizes), 1)
        self.assertGreater(sizes[-1], sizes[0])

    def test_a_decoy_block_takes_arguments(self):
        """A zero-argument block nobody calls answers "which one is the decoy?"."""
        declared = {}
        used = set()
        for block in block_dicts(self.data):
            mutation = block.get("mutation") or {}
            if not mutation.get("proccode"):
                continue
            if block["opcode"] == "procedures_prototype":
                declared[mutation["proccode"]] = json.loads(mutation["argumentnames"])
            elif block["opcode"] == "procedures_call":
                used.add(mutation["proccode"])
        decoys = {code: names for code, names in declared.items() if code not in used}
        self.assertTrue(decoys, "no decoy block was added")
        self.assertTrue(
            any(names for names in decoys.values()),
            "every decoy block takes no arguments",
        )
        # The real parameters are hidden words, so a decoy's have to be too.
        for names in decoys.values():
            for name in names:
                self.assertTrue(HIDDEN.match(name), name)

    def test_no_decoy_keeps_its_working_name(self):
        """The names the pass invents for its own blocks are placeholders."""
        blob = json.dumps(self.data)
        for marker in ("__trash", "_decoy", "_state", "_spare", "Phase", "Strings"):
            self.assertNotIn(marker, blob, marker)

    def test_scrub_takes_the_editor_residue_out(self):
        source = SOURCE.replace(
            "class main {",
            '// comment Stage at 0,0 200x40: "how this works"\nclass main {',
        )
        _, plain, data = build(source, obf_scrub=True)
        self.assertFalse(
            data["targets"][0].get("comments"), "the notes are still in the file"
        )
        _, _, kept = build(source)
        self.assertTrue(
            kept["targets"][0].get("comments"), "the control case has no comments to drop"
        )

    def test_scrub_is_off_by_default(self):
        source = SOURCE.replace(
            "class main {",
            '// comment Stage at 0,0 200x40: "how this works"\nclass main {',
        )
        _, _, data = build(source)
        positions = [
            (block["x"], block["y"])
            for target in data["targets"]
            for block in target["blocks"].values()
            if isinstance(block, dict) and block.get("topLevel")
        ]
        self.assertTrue(
            data["targets"][0].get("comments"), "the comment went missing without --obf-scrub"
        )
        self.assertIn((0, 0), positions, "a block position was rewritten")

    def test_a_hat_becomes_a_state_machine(self):
        """``hat -> s0 -> s1`` becomes ``set state; if state .. { s0; set state }``.

        The guards are evaluated in one pass and each one is true when it is
        reached, so nothing is pushed on the stack and nothing loops: the
        statements run in the same frame as they did before. The numbers are
        scattered, and dead guards are mixed in among the live ones, so the
        chain order says nothing about the original order.
        """
        source = """
#enable_feature<doObfuscation>;
class a {
    public void greenFlag() {
        x = 0;
        x += 1;
        x += 2;
        x += 3;
    }
}
"""
        _, _, data = build(source)
        sprite = data["targets"][1]
        blocks = sprite["blocks"]
        hat = next(b for b in blocks.values()
                   if isinstance(b, dict) and b["opcode"] == "event_whenflagclicked")
        entry = blocks[hat["next"]]
        self.assertEqual(entry["opcode"], "data_setvariableto")
        state_id = entry["fields"]["VARIABLE"][1]

        guards = []
        current = entry.get("next")
        while current:
            guards.append(blocks[current])
            current = blocks[current].get("next")
        self.assertEqual({guard["opcode"] for guard in guards}, {"control_if"})

        def operand(raw):
            """``(is a reporter, reporter block or number)`` for a guard side."""
            if raw[0] in (2, 3):
                return True, blocks[raw[1]]
            return False, int(raw[1][1])

        # Every guard tests the state variable against a number -- in either
        # order: `<state = 4>` and `<4 = state>` are the same test to Scratch and
        # different to a reader looking for the familiar one.
        numbers = []
        for guard in guards:
            condition = blocks[guard["inputs"]["CONDITION"][1]]
            self.assertEqual(condition["opcode"], "operator_equals")
            sides = [operand(condition["inputs"][side]) for side in ("OPERAND1", "OPERAND2")]
            reporters = [value for is_block, value in sides if is_block]
            literals = [value for is_block, value in sides if not is_block]
            self.assertEqual(len(reporters), 1, "a guard compares a block and a number")
            self.assertEqual(reporters[0]["fields"]["VARIABLE"][1], state_id)
            numbers.append(literals[0])

        # Which numbers the machine can actually hold: the entry and every
        # handover. A guard on any other number is dead.
        reachable = {int(entry["inputs"]["VALUE"][1][1])}
        for guard in guards:
            body = blocks[guard["inputs"]["SUBSTACK"][1]]
            while body.get("next"):
                body = blocks[body["next"]]
            if body["opcode"] == "data_setvariableto" and body["fields"]["VARIABLE"][1] == state_id:
                reachable.add(int(body["inputs"]["VALUE"][1][1]))

        live = [guard for guard, number in zip(guards, numbers) if number in reachable]
        dead = [guard for guard, number in zip(guards, numbers) if number not in reachable]
        self.assertGreaterEqual(len(dead), 1, "no dead guard was added")
        self.assertLessEqual(len(dead), 3, "too many dead guards for a five-block script")
        self.assertEqual(len(live), len(reachable), "a state has no guard")
        self.assertEqual(len(set(numbers)), len(numbers), "a state number was reused")
        self.assertNotEqual(sorted(numbers), numbers, "the numbers read as a sequence")

        # Dead guards are never reached, so what they touch does not matter --
        # but they must not steer the machine that does run.
        for guard in dead:
            body = blocks[guard["inputs"]["SUBSTACK"][1]]
            while body:
                self.assertNotEqual(
                    (body.get("fields") or {}).get("VARIABLE", [None, None])[1], state_id,
                    "a dead guard writes the state the live ones read",
                )
                body = blocks.get(body.get("next"))

        # Nothing was pushed: no call, no loop, which is what keeps the
        # timing. A dead guard may carry a loop (it never runs) and so may a
        # decoy definition in the same sprite; the path this hat runs may not.
        unreachable = set()
        for guard in dead:
            body = guard["inputs"]["SUBSTACK"][1]
            while body:
                unreachable.add(body)
                body = blocks[body].get("next")
        opcodes, seen, stack = set(), set(), [hat["next"]]
        while stack:
            ident = stack.pop()
            if not ident or ident in seen or ident in unreachable:
                continue
            seen.add(ident)
            block = blocks[ident]
            opcodes.add(block["opcode"])
            stack.append(block.get("next"))
            for raw in block["inputs"].values():
                for payload in raw[1:]:
                    if isinstance(payload, str) and payload in blocks:
                        stack.append(payload)
        self.assertNotIn("procedures_call", opcodes)
        self.assertNotIn("control_repeat_until", opcodes)
        self.assertNotIn("control_repeat", opcodes)

    def test_a_guard_may_put_the_number_first(self):
        """Both operand orders show up: one form everywhere is a signature."""
        hooks = "\n".join(
        f"    public void step{index}() {{ // hook: broadcast M{index}\n"
        f"        v{index} = 0;\n        v{index} += 1;\n        v{index} += 2;\n"
        f"        v{index} += 3;\n    }}"
        for index in range(4)
        )
        sends = "\n        ".join(f'Control.broadcast("M{index}");' for index in range(4))
        source = (
        "#enable_feature<doObfuscation>;\n"
        "class a {\n"
        "    public void greenFlag() {\n"
        f"        {sends}\n"
        "    }\n"
        f"{hooks}\n"
        "}\n"
        )
        _, _, data = build(source)
        orders = set()
        for block in block_dicts(data):
            if block["opcode"] != "operator_equals":
                continue
            first = block["inputs"]["OPERAND1"]
            orders.add("reporter" if first[0] in (2, 3) else "number")
        self.assertEqual(orders, {"reporter", "number"})

    def test_state_variables_are_numbered_across_the_project(self):
        """Two flattened scripts on one sprite must not share a state variable."""
        source = """
#enable_feature<doObfuscation>;
class a {
    public void greenFlag() {
        Control.broadcast("A");
        Control.broadcast("B");
    }
    public void one() { // hook: broadcast A
        x = 0;
        x += 1;
        x += 2;
    }
    public void two() { // hook: broadcast B
        y = 0;
        y += 1;
        y += 2;
    }
}
"""
        _, _, data = build(source)
        blocks = data["targets"][1]["blocks"]
        states = []
        for hat in blocks.values():
            if not isinstance(hat, dict) or not hat.get("opcode", "").startswith("event_when"):
                continue
            entry = blocks.get(hat.get("next"))
            if entry and entry["opcode"] == "data_setvariableto":
                states.append(entry["fields"]["VARIABLE"])
        self.assertEqual(len(states), 2, "the two hooked scripts were not both flattened")
        self.assertNotEqual(states[0][1], states[1][1], "one state variable for two scripts")
        self.assertNotEqual(states[0][0], states[1][0], "two scripts share a state name")

    def test_flatten_can_be_switched_off(self):
        source = """
#enable_feature<doObfuscation>;
class a {
    public void greenFlag() {
        x = 0;
        x += 1;
        x += 2;
    }
}
"""
        _, _, data = build(source, obf_flatten=0)
        sprite = data["targets"][1]
        hat = next(b for b in sprite["blocks"].values()
                   if isinstance(b, dict) and b["opcode"] == "event_whenflagclicked")
        self.assertNotEqual(sprite["blocks"][hat["next"]]["opcode"], "procedures_call")

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


VM = ROOT / "scratch-vm-develop"
COMPARE = ROOT / "tools" / "obf_compare.js"

BEHAVIOUR_SOURCE = """
public score = 0;
public trail = [];
public frames = 0;

class main {
    private void bump(n) {
        score += n;
        trail.add(score);
    }
    public void greenFlag() {
        score = 0;
        for (i = 0, i < 6, i++) {
            bump(i * 2 + 1);
            if (score > 12) {
                score -= 5;
            } else {
                score += 1;
            }
        }
        while (score < 30) {
            score += 7;
        }
        repeat (4) {
            frames += 1;
            score += 1;
        }
        score = score * 2 + 1;
        trail.add(score);
        Control.createClone("twin");
        Control.broadcast("Halt");
    }
    public void onHalt() { // hook: broadcast Halt
        score += 1;
        if (score > 0) {
            Control.stopThisScript();
        }
        score += 100000;
        trail.add(score);
    }
}

class twin {
    private count = 0;
    public void whenCloned() {
        count += 1;
        count = count * 3;
        count = count + 2;
    }
}
"""

requires_vm = unittest.skipUnless(
    (VM / "src").is_dir() and COMPARE.exists(), "needs the scratch-vm checkout and node"
)


@requires_vm
class ObfuscationBehaviourTests(unittest.TestCase):
    """The obfuscated project has to *do* the same thing, not just look hidden.

    Renaming cannot change behaviour, but the state machine can: a dispatch that
    spends a frame per statement would show up as a different value at the same
    frame, and so would one that re-enters a state. So the two builds are run in
    the real VM and compared frame by frame (`tools/obf_compare.js`).
    """

    def test_every_frame_matches_the_plain_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "b.jsw"
            source.write_text(BEHAVIOUR_SOURCE, encoding="utf-8")
            for tag, extra in (("plain", []), ("hidden", ["--enable", "doObfuscation"])):
                subprocess.run(
                    [sys.executable, "-m", "jswc", "build", str(source),
                     "-o", str(folder / f"{tag}.sb3"), "--allow-undefined", *extra],
                    cwd=ROOT, check=True, capture_output=True,
                )
            result = subprocess.run(
                ["node", str(COMPARE), str(folder / "plain.sb3"),
                 str(folder / "hidden.sb3"), "45", "--per-frame"],
                cwd=ROOT, capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("same after 45 frames", result.stdout)


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


CLONE_SOURCE = """
#enable_feature<doObfuscation>;

class spawner {
    public void greenFlag() {
        Control.createClone("note");
        Motion.goto("note");
    }
}

class note {
    public void whenCloned() {
        Looks.say("x");
    }
}
"""


class SpriteMenuTests(unittest.TestCase):
    """A sprite named in a menu is a literal, so it follows the rename."""

    def setUp(self):
        _, _, self.data = build(CLONE_SOURCE)
        self.names = {target["name"] for target in self.data["targets"]}
        self.blocks = {
            block_id: block
            for target in self.data["targets"]
            for block_id, block in target["blocks"].items()
            if isinstance(block, dict)
        }

    def _menu_value(self, opcode: str, input_name: str) -> str:
        block = next(b for b in self.blocks.values() if b["opcode"] == opcode)
        shadow = self.blocks[block["inputs"][input_name][1]]
        field = next(iter(shadow["fields"].values()))
        return field[0]

    def test_a_clone_target_names_a_sprite_that_exists(self):
        value = self._menu_value("control_create_clone_of", "CLONE_OPTION")
        self.assertIn(value, self.names, "a clone menu left at the old name spawns nothing")
        self.assertNotEqual(value, "note")

    def test_go_to_names_a_sprite_that_exists(self):
        value = self._menu_value("motion_goto", "TO")
        self.assertIn(value, self.names)
        self.assertNotEqual(value, "note")


DECOY_SOURCE = (
    "#enable_feature<doObfuscation>;\n"
    "public g = 0;\n"
    + "".join(
        "class c%d { // Sprite: c%d\n"
        "    private v = 0;\n"
        "    public void greenFlag() { v = 1; }\n"
        "}\n" % (index, index)
        for index in range(6)
    )
)


class DecoyProcedureTests(unittest.TestCase):
    """A decoy must be a *valid* custom block, not just a plausible one."""

    def test_boolean_parameters_hold_boolean_reporters(self):
        # scratch-blocks checks every prototype input against the parameter
        # type it declared. A ``%b`` slot holding ``argument_reporter_string_number``
        # made TurboWarp refuse the project: "expected Boolean, found String".
        _, _, data = build(DECOY_SOURCE)
        checked = 0
        for target in data["targets"]:
            blocks = target["blocks"]
            for block in blocks.values():
                if not isinstance(block, dict) or block["opcode"] != "procedures_prototype":
                    continue
                mutation = block.get("mutation") or {}
                kinds = re.findall(r"%[bsn]", mutation.get("proccode", ""))
                ids = json.loads(mutation["argumentids"])
                defaults = json.loads(mutation["argumentdefaults"])
                for index, (kind, argument_id) in enumerate(zip(kinds, ids)):
                    raw = block["inputs"][argument_id]
                    self.assertEqual(raw[0], 1, "a prototype slot is a shadow")
                    reporter = blocks[raw[1]]
                    expected = (
                        "argument_reporter_boolean" if kind == "%b"
                        else "argument_reporter_string_number"
                    )
                    self.assertEqual(reporter["opcode"], expected, mutation["proccode"])
                    if kind == "%b":
                        self.assertEqual(defaults[index], "false")
                    checked += 1
        self.assertGreater(checked, 0, "the decoys declared no parameters")


class CorruptBlockPosTests(unittest.TestCase):
    """``corruptBlockPos`` is the broken-project form, behind a switch."""

    SOURCE = (
        "#enable_feature<corruptBlockPos>;\n"
        "public x = 0;\n"
        "class main { // Sprite: main\n"
        "    public void greenFlag() { x = 1; }\n"
        "}\n"
    )

    def _dangling(self, data):
        count = 0
        for target in data["targets"]:
            blocks = target["blocks"]
            for block in blocks.values():
                if not isinstance(block, dict):
                    continue
                for key in ("next", "parent"):
                    if isinstance(block.get(key), str) and block[key] not in blocks:
                        count += 1
                for raw in block.get("inputs", {}).values():
                    for index in (1, 2):
                        if len(raw) > index and isinstance(raw[index], str) and raw[index] not in blocks:
                            count += 1
        return count

    def test_the_planted_references_are_dangling(self):
        _, project, data = build(self.SOURCE)
        self.assertGreater(self._dangling(data), 0)
        self.assertEqual(validate(project, corrupt=True), [])
        self.assertTrue(validate(project), "the structural checks should catch the poison")

    def test_doObfuscation_alone_writes_a_valid_project(self):
        _, project, data = build(SOURCE)     # doObfuscation, no corruptBlockPos
        self.assertEqual(self._dangling(data), 0)
        self.assertEqual(validate(project), [])

    def test_the_corrupt_form_uses_a_string_reporter_for_a_boolean_parameter(self):
        source = DECOY_SOURCE.replace(
            "#enable_feature<doObfuscation>;\n",
            "#enable_feature<doObfuscation>;\n#enable_feature<corruptBlockPos>;\n",
        )
        _, _, data = build(source)
        checked = 0
        for target in data["targets"]:
            blocks = target["blocks"]
            for block in blocks.values():
                if not isinstance(block, dict) or block["opcode"] != "procedures_prototype":
                    continue
                mutation = block.get("mutation") or {}
                if mutation.get("proccode", "").startswith("!missing"):
                    continue        # the poison prototype is checked elsewhere
                kinds = re.findall(r"%[bsn]", mutation.get("proccode", ""))
                ids = json.loads(mutation["argumentids"])
                for kind, argument_id in zip(kinds, ids):
                    reporter = blocks[block["inputs"][argument_id][1]]
                    if kind == "%b":
                        self.assertEqual(
                            reporter["opcode"], "argument_reporter_string_number"
                        )
                        checked += 1
        self.assertGreater(checked, 0, "the decoys declared no boolean parameters")


if __name__ == "__main__":
    unittest.main()
