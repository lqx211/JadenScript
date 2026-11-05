"""Block-shape fidelity: dropdown shadows and loop lowering."""

from __future__ import annotations

import unittest

from tests.helpers import blocks_of, compile_source, opcodes

from jswc.decompile import Decompiler
from jswc.sb3 import build_project_dict
from jswc.sb3_read import LoadedProject


def roundtrip(source: str) -> str:
    _, project = compile_source(source)
    data, _ = build_project_dict(project)
    return Decompiler(LoadedProject(data=data)).decompile()


class ShadowMenuTests(unittest.TestCase):
    def test_menu_call_becomes_a_dropdown_block(self):
        _, project = compile_source(
            'class a { void f(){ x = Operators.round(menu("note", "NOTE", 66)); } }'
        )
        ops = opcodes(project, "a")
        self.assertIn("note", ops)
        self.assertIn("operator_round", ops)
        note = next(b for b in blocks_of(project, "a").values() if b["opcode"] == "note")
        self.assertTrue(note["shadow"])
        self.assertEqual(note["fields"]["NOTE"][0], "66")

    def test_menu_call_roundtrips(self):
        source = roundtrip(
            'class a { void f(){ x = Operators.round(menu("note", "NOTE", 66)); } }'
        )
        self.assertIn('menu("note", "NOTE", "66")', source)

    def test_loose_dropdown_is_a_root_block(self):
        source = (
            'class a {\n'
            '    void f(){ x = 1; }\n'
            '    loose {\n'
            '        menu("music_menu_INSTRUMENT", "INSTRUMENT", 1);\n'
            '    }\n'
            '}\n'
        )
        _, project = compile_source(source)
        blocks = blocks_of(project, "a")
        menu = next(
            (b for b in blocks.values() if b["opcode"] == "music_menu_INSTRUMENT"), None
        )
        self.assertIsNotNone(menu)
        self.assertTrue(menu["shadow"])
        self.assertTrue(menu["topLevel"])
        self.assertEqual(menu["fields"]["INSTRUMENT"][0], "1")


class LoopShapeTests(unittest.TestCase):
    def test_exclusive_for_roundtrips_as_for(self):
        _, project = compile_source("class a { void f(){ for (i=1; i<5; i++){ x = i; } } }")
        self.assertIn("control_repeat_until", opcodes(project, "a"))
        source = roundtrip("class a { void f(){ for (i=1; i<5; i++){ x = i; } } }")
        self.assertIn("for (i=1, i<5, i++)", source)
        self.assertNotIn("while", source)

    def test_inclusive_for_from_other_start_roundtrips(self):
        # An inclusive ascending count comes back as the range spelling.
        source = roundtrip("class a { void f(){ for (i=2; i<=5; i++){ x = i; } } }")
        self.assertIn("for i in 2..5 {", source)

    def test_while_becomes_control_while(self):
        _, project = compile_source("class a { void f(){ while(x<5){ x++; } } }")
        ops = opcodes(project, "a")
        self.assertIn("control_while", ops)
        self.assertNotIn("operator_not", ops)

    def test_negated_while_is_repeat_until_without_a_double_negation(self):
        _, project = compile_source("class a { void f(){ while(!(x<5)){ x++; } } }")
        ops = opcodes(project, "a")
        self.assertIn("control_repeat_until", ops)
        self.assertNotIn("operator_not", ops)

    def test_negated_while_roundtrips(self):
        source = roundtrip("class a { void f(){ while(!(x<5)){ x++; } } }")
        self.assertIn("while (!(x < 5))", source)


if __name__ == "__main__":
    unittest.main()


class RangeForTests(unittest.TestCase):
    """``for i in a..b`` — the inclusive counter form.

    It lowers to Scratch's own ``repeat n`` when the count starts at 1 and the
    counter is a real variable, otherwise to ``set`` + ``repeat until`` with a
    trailing ``change by 1``. The decompiler recognises both shapes back.
    """

    def compile_(self, body: str, **kwargs):
        source = f"class a {{ void f() {{ {body} }} }}"
        return compile_source(source, **kwargs)

    def test_literal_range_from_one_uses_the_counter_block(self):
        _, project = self.compile_("for i in 1..10 { x = i; }")
        self.assertIn("control_for_each", opcodes(project, "a"))

    def test_range_from_zero_uses_an_explicit_while(self):
        _, project = self.compile_("for i in 0..3 { x = i; }")
        ops = opcodes(project, "a")
        self.assertIn("control_repeat_until", ops)
        self.assertIn("data_setvariableto", ops)
        self.assertIn("data_changevariableby", ops)
        self.assertNotIn("control_for_each", ops)

    def test_the_counter_is_initialised_to_the_start(self):
        _, project = self.compile_("for i in 4..9 { x = i; }")
        blocks = blocks_of(project, "a")
        start = next(b for b in blocks.values() if b["opcode"] == "data_setvariableto")
        self.assertEqual(start["inputs"]["VALUE"][1], [10, "4"])

    def test_bracketed_spellings_agree(self):
        shapes = {
            bare: opcodes(self.compile_(f"for i in {bare} {{ x = i; }}")[1], "a")
            for bare in ("0..3", "(0..3)")
        }
        self.assertEqual(shapes["0..3"], shapes["(0..3)"])
        _, project = self.compile_("for (i in 0..3) { x = i; }")
        self.assertIn("control_repeat_until", opcodes(project, "a"))

    def test_expression_bounds_compile(self):
        _, project = self.compile_("for i in (a+1)..(b+1) { x = i; }")
        self.assertIn("operator_add", opcodes(project, "a"))
        _, double = self.compile_("for i in ((a+1)..(b+1)) { x = i; }")
        self.assertEqual(opcodes(project, "a"), opcodes(double, "a"))

    def test_temp_counter_uses_thread_variables_and_leaks_no_state(self):
        _, project = self.compile_("for _i in 0..3 { x = _i; }",
                                   features={"useTempVariables"})
        ops = opcodes(project, "a")
        self.assertIn("lmsTempVars2_setThreadVariable", ops)
        self.assertIn("lmsTempVars2_changeThreadVariable", ops)
        self.assertNotIn("data_changevariableby", ops)
        # A temp counter must not become a sprite variable.
        self.assertNotIn("_i", [v[0] for v in project.targets[1].variables.values()])

    def test_reusing_a_live_temp_counter_is_an_error(self):
        codegen, _ = self.compile_(
            "for _i in 0..3 { for _i in 0..2 { x = 1; } }",
            features={"useTempVariables"},
        )
        codes = [d.split(": ", 1)[-1][:5] for d in codegen.diagnostics]
        self.assertIn("E0074", codes)

    def test_two_sibling_loops_may_share_a_temp_counter(self):
        codegen, _ = self.compile_(
            "for _i in 0..3 { x = 1; } for _i in 0..2 { x = 2; }",
            features={"useTempVariables"},
        )
        self.assertEqual(
            [d for d in codegen.diagnostics if "E0074" in d], [],
            "the first loop has ended before the second starts",
        )

    def test_a_break_inside_a_loop_is_reported(self):
        """Scratch has no loop break: `break` leaves the whole function."""
        codegen, _ = self.compile_("while (a < 3) { if (a == 1) { break; } a += 1; }")
        self.assertTrue(any("W0025" in d for d in codegen.diagnostics), codegen.diagnostics)

    def test_a_break_outside_a_loop_is_the_documented_return(self):
        codegen, _ = self.compile_("if (a == 1) { break; }")
        self.assertEqual([d for d in codegen.diagnostics if "W0025" in d], [])

    def test_a_nested_loop_owns_its_own_break(self):
        codegen, _ = self.compile_("while (a < 3) { while (b < 2) { break; } a += 1; }")
        self.assertEqual(sum("W0025" in d for d in codegen.diagnostics), 1)

    def test_writing_the_counter_inside_the_body_warns(self):
        codegen, _ = self.compile_("for i in 0..3 { i = 99; }")
        self.assertTrue(any("W0021" in d for d in codegen.diagnostics))

    def test_a_nested_loop_counter_is_reported_once(self):
        codegen, _ = self.compile_("for i in 0..3 { for i in 0..2 { x = 1; } }")
        self.assertEqual(sum("W0021" in d for d in codegen.diagnostics), 1)

    def test_a_public_counter_is_the_stage_variable(self):
        _, project = compile_source(
            "public i = 0;\nclass a { void f(){ for i in 3..7 { x = i; } } }"
        )
        stage = [v[0] for v in project.stage.variables.values()]
        self.assertIn("i", stage)

    def test_a_dotted_counter_targets_the_stage(self):
        _, project = compile_source(
            "class a { void f(){ for Synth.counter in 2..4 { x = 1; } } }"
        )
        self.assertIn("Synth.counter", [v[0] for v in project.stage.variables.values()])

    def test_missing_dots_are_a_for_header_error(self):
        from jswc.tokens import JswError
        from jswc.parser import Parser
        from jswc.lexer import tokenize

        with self.assertRaises(JswError) as caught:
            Parser(tokenize("class a { void f(){ for i in 1 to 10 { } } }")).parse()
        self.assertEqual(caught.exception.code, "E0022")

    def test_counted_while_becomes_a_range(self):
        source = roundtrip("class a { void f(){ for i in 0..3 { x = i; } } }")
        self.assertIn("for i in 0..3 {", source)
        self.assertNotIn("while", source)

    def test_counter_block_becomes_a_range(self):
        source = roundtrip("class a { void f(){ for i in 1..10 { x = i; } } }")
        self.assertIn("for i in 1..10 {", source)

    def test_temp_counter_roundtrips(self):
        source = roundtrip("class a { void f(){ for _i in 2..10 { x = _i; } } }")
        self.assertIn("for _i in 2..10 {", source)

    def test_expression_bounds_roundtrip_with_parentheses(self):
        source = roundtrip("class a { void f(){ for i in 0..(n+1) { x = i; } } }")
        self.assertIn("for i in 0..(n + 1) {", source)

    def test_an_exclusive_loop_keeps_the_three_part_form(self):
        """``..`` is inclusive, so ``i < n`` must not be rewritten."""
        source = roundtrip("class a { void f(){ for (i=0; i<5; i++){ x = i; } } }")
        self.assertIn("for (i=0, i<5, i++)", source)
        self.assertNotIn("..", source)

    def test_a_temp_counter_is_never_folded_into_a_three_part_for(self):
        """The three-part ``for`` materialises its counter, a range does not.

        Folding ``_p = 0; while (_p < 4) { …; _p += 1 }`` into
        ``for (_p=0, _p<4, _p++)`` turned the temp into a sprite variable on the
        way back, so those loops keep the ``while`` spelling.
        """
        source = roundtrip(
            "#enable_feature<useTempVariables>;\n"
            "class a { void f(){ _p = 0; while (_p < 4) { x = _p; _p += 1; } } }"
        )
        self.assertIn("while (_p < 4)", source)
        self.assertNotIn("for (_p=", source)
        self.assertNotIn("private _p =", source)

    def test_dotted_counter_roundtrips(self):
        source = roundtrip("class a { void f(){ for Synth.counter in 1..n { x = 1; } } }")
        self.assertIn("for Synth.counter in 1..n {", source)
