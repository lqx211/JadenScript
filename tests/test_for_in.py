"""``for v in <list>`` and ``for l in a, b``: walking a list, not a count.

Scratch has no for-each block, so both forms are lowered:

* one list name -- the loop variable takes each *item*, through a generated
  index and ``item (i) of``;
* several names -- the body runs once per list with the loop variable standing
  for that list, because a list cannot be held in a variable;
* ``1..n`` -- still the counting form, which is a different node.
"""

from __future__ import annotations

import unittest

from tests.helpers import blocks_of, compile_source, opcodes


def compile_(body: str, **kwargs):
    source = (
        "class a {\n"
        "    private notes = [];\n"
        "    private other = [];\n"
        f"    void f() {{ {body} }}\n"
        "}\n"
    )
    return compile_source(source, **kwargs)


class ItemLoopTests(unittest.TestCase):
    def test_walking_the_items_is_an_index_into_the_list(self):
        _, project = compile_("for v in notes { x = v; }")
        ops = opcodes(project, "a")
        for opcode in ("data_lengthoflist", "control_repeat", "data_itemoflist",
                       "data_setvariableto", "data_changevariableby"):
            self.assertIn(opcode, ops)

    def test_the_counter_is_set_to_one_before_the_loop(self):
        _, project = compile_("for v in notes { x = v; }")
        blocks = blocks_of(project, "a")
        counters = [
            b for b in blocks.values()
            if b["opcode"] == "data_setvariableto"
            and b["fields"]["VARIABLE"][0].startswith("_forItem")
        ]
        self.assertEqual(len(counters), 1)
        self.assertEqual(counters[0]["inputs"]["VALUE"], [1, [10, "1"]])

    def test_the_loop_variable_receives_an_item_not_an_index(self):
        _, project = compile_("for v in notes { x = v; }")
        blocks = blocks_of(project, "a")
        item = next(b for b in blocks.values() if b["opcode"] == "data_itemoflist")
        self.assertEqual(item["fields"]["LIST"][0], "notes")
        assignments = [
            b for b in blocks.values()
            if b["opcode"] == "data_setvariableto"
            and b["fields"]["VARIABLE"][0] == "v"
        ]
        self.assertEqual(len(assignments), 1)
        reader = blocks[assignments[0]["inputs"]["VALUE"][1]]
        self.assertEqual(reader["opcode"], "data_itemoflist")

    def test_the_count_is_read_once_at_the_top(self):
        # `repeat` evaluates its count once, so deleting inside the body cannot
        # make the loop run forever.
        _, project = compile_("for v in notes { notes.delete(1); }")
        blocks = blocks_of(project, "a")
        repeat = next(b for b in blocks.values() if b["opcode"] == "control_repeat")
        count = blocks[repeat["inputs"]["TIMES"][1]]
        self.assertEqual(count["opcode"], "data_lengthoflist")

    def test_a_nested_loop_gets_its_own_counter(self):
        _, project = compile_(
            "for v in notes { for w in notes { x = w; } }"
        )
        counters = {
            b["fields"]["VARIABLE"][0]
            for b in blocks_of(project, "a").values()
            if b["opcode"] == "data_setvariableto"
            and b["fields"]["VARIABLE"][0].startswith("_forItem")
        }
        self.assertEqual(len(counters), 2, counters)


class ListLoopTests(unittest.TestCase):
    def test_each_list_gets_its_own_copy_of_the_body(self):
        _, project = compile_("for l in notes, other { l.clear(); }")
        blocks = blocks_of(project, "a")
        cleared = sorted(
            b["fields"]["LIST"][0] for b in blocks.values()
            if b["opcode"] == "data_deletealloflist"
        )
        self.assertEqual(cleared, ["notes", "other"])

    def test_the_loop_variable_becomes_the_list_itself(self):
        _, project = compile_("for l in notes, other { while (l.length() > 1) { l.delete(1); } }")
        blocks = blocks_of(project, "a")
        lengths = sorted(
            b["fields"]["LIST"][0] for b in blocks.values()
            if b["opcode"] == "data_lengthoflist"
        )
        self.assertEqual(lengths, ["notes", "other"])

    def test_no_counter_is_emitted_for_the_list_form(self):
        _, project = compile_("for l in notes, other { l.clear(); }")
        blocks = blocks_of(project, "a")
        self.assertFalse([
            b for b in blocks.values()
            if b["opcode"] == "data_setvariableto"
            and b["fields"]["VARIABLE"][0].startswith("_forItem")
        ])


class DiagnosticTests(unittest.TestCase):
    def test_a_name_that_is_not_a_list_is_refused(self):
        codegen, _ = compile_("for v in missing { x = v; }")
        self.assertTrue(
            any("E0083" in d and "not a declared list" in d for d in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_the_message_points_at_the_counting_form(self):
        codegen, _ = compile_("for v in missing { x = v; }")
        messages = [d for d in codegen.diagnostics if "E0083" in d]
        self.assertTrue(messages, codegen.diagnostics)
        self.assertIn("1..n", messages[0])

    def test_changing_the_list_inside_the_body_warns(self):
        codegen, _ = compile_("for v in notes { notes.delete(1); }")
        self.assertTrue(
            any("W0033" in d for d in codegen.diagnostics), codegen.diagnostics
        )

    def test_reading_the_list_inside_the_body_does_not_warn(self):
        codegen, _ = compile_("for v in notes { x = notes.length(); }")
        self.assertFalse([d for d in codegen.diagnostics if "W0033" in d])

    def test_break_still_stops_the_script(self):
        codegen, _ = compile_("for v in notes { break; }")
        self.assertTrue(
            any("W0025" in d for d in codegen.diagnostics), codegen.diagnostics
        )

    def test_a_nested_loop_reusing_a_temp_variable_is_refused(self):
        source = (
            "#enable_feature<useTempVariables>;\n"
            "class a { private n = []; void f(){ for _v in n { for _v in n { x = 1; } } } }"
        )
        codegen, _ = compile_source(
            source, features={"commentMetadata", "useTempVariables"}
        )
        self.assertTrue(
            any("E0074" in d for d in codegen.diagnostics), codegen.diagnostics
        )

    def test_two_loops_one_after_the_other_may_share_the_variable(self):
        source = (
            "#enable_feature<useTempVariables>;\n"
            "class a { private n = []; void f(){ for _v in n { x = 1; } for _v in n { y = 2; } } }"
        )
        codegen, _ = compile_source(
            source, features={"commentMetadata", "useTempVariables"}
        )
        self.assertFalse([d for d in codegen.diagnostics if "E0074" in d])

    def test_a_range_is_still_a_range(self):
        _, project = compile_("for i in 1..10 { x = i; }")
        self.assertIn("control_for_each", opcodes(project, "a"))


if __name__ == "__main__":
    unittest.main()
