from __future__ import annotations

import unittest

from tests.helpers import blocks_of, compile_source, opcodes

from jswc.validator import validate

SOURCE = """
class player { // Sprite: player (10,20) size=80 direction=90 show=0
    public void Player.move(){
        public Player.score = 0;
        public Player.score += 1;
        private local = 2;
        local *= 3;
        Motion.x = -5;
        Motion.y += 2;
        Pen.eraseAll();
        Pen.setPenSize(4);
    }
}
"""


class CodegenTests(unittest.TestCase):
    def test_sprite_metadata(self):
        _, project = compile_source(SOURCE)
        player = next(t for t in project.targets if t.name == "player")
        self.assertEqual((player.x, player.y, player.size, player.visible), (10, 20, 80.0, False))

    def test_global_and_local_variables(self):
        _, project = compile_source(SOURCE)
        stage_names = [v[0] for v in project.stage.variables.values()]
        self.assertIn("Player.score", stage_names)
        player = next(t for t in project.targets if t.name == "player")
        self.assertIn("local", [v[0] for v in player.variables.values()])

    def test_property_and_extension_mapping(self):
        _, project = compile_source(SOURCE)
        ops = opcodes(project, "player")
        for opcode in ("motion_setx", "motion_changeyby", "pen_clear", "pen_setPenSizeTo"):
            self.assertIn(opcode, ops)
        self.assertEqual(set(project.extensions), {"pen"})

    def test_for_loop_becomes_for_each(self):
        _, project = compile_source(
            "class a { void f(){ for (i=1, i<=10, i++){ Piano.n.add(i); } } }"
        )
        self.assertIn("control_for_each", opcodes(project, "a"))
        blocks = blocks_of(project, "a")
        for_each = next(b for b in blocks.values() if b["opcode"] == "control_for_each")
        self.assertEqual(for_each["inputs"]["VALUE"], [1, [6, "10"]])
        self.assertIn("SUBSTACK", for_each["inputs"])

    def test_for_loop_with_non_one_start_uses_repeat_until(self):
        _, project = compile_source(
            "class a { void f(){ for (i=3, i<=10, i++){ Piano.n.add(i); } } }"
        )
        ops = opcodes(project, "a")
        self.assertIn("control_repeat_until", ops)
        self.assertNotIn("control_for_each", ops)

    def test_while_true_becomes_forever(self):
        _, project = compile_source("class a { void f(){ while(1){ Piano.n.add(1); break; } } }")
        self.assertIn("control_forever", opcodes(project, "a"))

    def test_while_condition_becomes_control_while(self):
        _, project = compile_source("class a { void f(){ while(x<5){ x++; } } }")
        blocks = blocks_of(project, "a")
        loop = next(b for b in blocks.values() if b["opcode"] == "control_while")
        inner = blocks[loop["inputs"]["CONDITION"][1]]
        self.assertEqual(inner["opcode"], "operator_lt")

    def test_negated_while_becomes_repeat_until_without_double_negation(self):
        _, project = compile_source("class a { void f(){ while(!(x<5)){ x++; } } }")
        blocks = blocks_of(project, "a")
        loop = next(b for b in blocks.values() if b["opcode"] == "control_repeat_until")
        inner = blocks[loop["inputs"]["CONDITION"][1]]
        self.assertEqual(inner["opcode"], "operator_lt")
        self.assertNotIn("operator_not", opcodes(project, "a"))

    def test_if_chain_nests_into_substack2(self):
        _, project = compile_source(
            "class a { void f(){ if (x){ y=1; } elif (z){ y=2; } else { y=3; } } }"
        )
        blocks = blocks_of(project, "a")
        outer = next(b for b in blocks.values() if b["opcode"] == "control_if_else")
        nested = blocks[outer["inputs"]["SUBSTACK2"][1]]
        self.assertEqual(nested["opcode"], "control_if_else")
        self.assertIn("SUBSTACK2", nested["inputs"])

    def test_if_without_else_uses_control_if(self):
        _, project = compile_source("class a { void f(){ if (x){ y=1; } } }")
        ops = opcodes(project, "a")
        self.assertIn("control_if", ops)
        self.assertNotIn("control_if_else", ops)

    def test_switch_does_not_fall_through(self):
        _, project = compile_source(
            "class a { void f(){ switch(k){ case 1: x=1; case 2: x=2; break; case _: x=3; } } }"
        )
        blocks = blocks_of(project, "a")
        sets = [b for b in blocks.values() if b["opcode"] == "data_setvariableto"]
        # one store per case, no carry-over: JadenScript never falls through
        self.assertEqual(len(sets), 3)

    def test_ternary_uses_utilities_extension(self):
        _, project = compile_source("class a { void f(){ x = (1) if (c) else (2); } }")
        self.assertIn("utilities", project.extensions)
        self.assertIn("utilities_ternaryOperator", opcodes(project, "a"))

    def test_procedure_definition_and_broadcast_receiver(self):
        _, project = compile_source(
            "class a { public void Player.tick(){ x=1; } }"
        )
        ops = opcodes(project, "a")
        self.assertIn("procedures_definition", ops)
        self.assertIn("event_whenbroadcastreceived", ops)
        self.assertIn("procedures_call", ops)
        self.assertEqual(list(project.stage.broadcasts.values()), ["Player.tick"])

    def test_private_function_is_not_broadcast(self):
        _, project = compile_source("class a { private void tick(){ x=1; } }")
        self.assertNotIn("event_whenbroadcastreceived", opcodes(project, "a"))
        self.assertEqual(project.stage.broadcasts, {})

    def test_call_keyword_emits_broadcast_while_plain_call_blocks(self):
        _, project = compile_source(
            "class a { public void Player.tick(){ x=1; }"
            " void run(){ call Player.tick(); Player.tick(); } }"
        )
        ops = opcodes(project, "a")
        self.assertIn("event_broadcast", ops)
        self.assertIn("procedures_call", ops)

    def test_key_hook_annotation_creates_hat(self):
        _, project = compile_source(
            "class a { public void Events.keyPressed(k){ // hook: when [space] key pressed\n x=1; } }"
        )
        blocks = blocks_of(project, "a")
        hat = next(b for b in blocks.values() if b["opcode"] == "event_whenkeypressed")
        self.assertEqual(hat["fields"]["KEY_OPTION"], ["space", None])

    def test_list_operations(self):
        _, project = compile_source(
            "class a { void f(){ Piano.n.add(1); Piano.n.clear(); x = Piano.n.length(); y = Piano.n.get(2); } }"
        )
        ops = opcodes(project, "a")
        for opcode in ("data_addtolist", "data_deletealloflist", "data_lengthoflist", "data_itemoflist"):
            self.assertIn(opcode, ops)
        self.assertIn("Piano.n", [v[0] for v in project.stage.lists.values()])

    def test_thread_local_mode_uses_extension(self):
        _, project = compile_source(
            "class a { void f(){ x = 1; x += 1; } }",
            locals_mode="thread",
        )
        ops = opcodes(project, "a")
        self.assertIn("lmsTempVars2_setThreadVariable", ops)
        self.assertIn("lmsTempVars2_changeThreadVariable", ops)
        self.assertIn("lmsTempVars2", project.extensions)

    def test_motion_change_uses_dx_dy_slots(self):
        _, project = compile_source("class a { void f(){ Motion.x += 1; Motion.y -= 2; } }")
        blocks = blocks_of(project, "a")
        change_x = next(b for b in blocks.values() if b["opcode"] == "motion_changexby")
        change_y = next(b for b in blocks.values() if b["opcode"] == "motion_changeyby")
        self.assertIn("DX", change_x["inputs"])
        self.assertIn("DY", change_y["inputs"])
        self.assertNotIn("X", change_x["inputs"])
        self.assertNotIn("Y", change_y["inputs"])

    def test_generated_projects_validate(self):
        for source in (
            SOURCE,
            "class a { void f(){ for (i=1, i<=3, i++){ Piano.n.add(i); } } }",
            "class a { public void Player.tick(){ if (x) { y=1; } else { y=2; } } }",
        ):
            _, project = compile_source(source)
            self.assertEqual(validate(project), [])


class ColourInputTests(unittest.TestCase):
    """A colour slot holds ``#rrggbb``; the pen's 24-bit number is a runtime value.

    Emitting the number as the primitive text would build a project that the
    official sb3 schema rejects (``should match pattern "^#[a-fA-F0-9]{6}$"``),
    even though the editor happens to open it.
    """

    def colour_input(self, body: str) -> list:
        _, project = compile_source(f"class player {{ void f(){{ {body} }} }}")
        blocks = blocks_of(project, "player")
        block = next(
            b for b in blocks.values()
            if b["opcode"] in ("pen_setPenColorToColor", "sensing_touchingcolor")
        )
        return block["inputs"]["COLOR"]

    def test_hex_literal_becomes_rrggbb(self):
        self.assertEqual(self.colour_input("Pen.setPenColor(0x24304a);"), [1, [9, "#24304a"]])

    def test_decimal_literal_becomes_rrggbb(self):
        self.assertEqual(self.colour_input("Pen.setPenColor(16711680);"), [1, [9, "#ff0000"]])

    def test_string_forms_are_normalised(self):
        self.assertEqual(self.colour_input('Pen.setPenColor("#00FF88");'), [1, [9, "#00ff88"]])
        self.assertEqual(self.colour_input('Pen.setPenColor("#F80");'), [1, [9, "#ff8800"]])

    def test_touching_colour_uses_the_same_rule(self):
        self.assertEqual(
            self.colour_input("if (Sensing.touchingColor(0xFF0000)) { x = 1; }"),
            [1, [9, "#ff0000"]],
        )

    def test_dynamic_colour_keeps_the_default_shadow(self):
        _, project = compile_source(
            "class player { public Player.c = 0; void f(){ Pen.setPenColor(Player.c); } }"
        )
        blocks = blocks_of(project, "player")
        block = next(b for b in blocks.values() if b["opcode"] == "pen_setPenColorToColor")
        head = block["inputs"]["COLOR"]
        self.assertEqual(head[0], 3)
        self.assertEqual(head[2], [9, "#a2a2a2"])


if __name__ == "__main__":
    unittest.main()


class CoreReporterPropertyTests(unittest.TestCase):
    """A zero-argument core reporter is a property, like an extension one.

    The docs say "reporters with no arguments are properties", and the
    extension path honoured it while `Sensing.timer` quietly compiled to a
    variable read -- which is a Scratch variable that does not exist, so the
    clock read 0 forever and no diagnostic was raised.
    """

    def _compiled(self, expr: str) -> list[str]:
        from tests.helpers import compile_source
        from jswc.sb3 import build_project_dict

        src = f"class T {{ public X.a = 0; public void go() {{ // hook: green flag\nX.a = {expr};\n}} }}"
        _cg, proj = compile_source(src)
        data, _ = build_project_dict(proj)
        return [
            b["opcode"]
            for t in data["targets"]
            for b in t["blocks"].values()
            if isinstance(b, dict) and b["opcode"].startswith("sensing_")
        ]

    def test_the_timer_is_a_block_not_a_variable(self) -> None:
        self.assertEqual(self._compiled("Sensing.timer"), ["sensing_timer"])

    def test_the_mouse_position_is_a_block_not_a_variable(self) -> None:
        self.assertEqual(self._compiled("Sensing.mouseX"), ["sensing_mousex"])

    def test_an_unknown_dotted_name_is_still_a_variable(self) -> None:
        self.assertEqual(self._compiled("Sensing.notAThing"), [])

class MathMenuTests(unittest.TestCase):
    """``Operators.math`` writes a *menu entry*, and a menu entry the block does
    not have evaluates to 0 for ever -- silently. ``round`` is the one that bit
    the i386 core: every divide in the machine reported a quotient of 0."""

    def test_rounding_is_lowered_to_the_round_block(self):
        _, project = compile_source(
            'class a { void f(){ x = Operators.math("round", 7 / 2); } }'
        )
        ops = opcodes(project, "a")
        self.assertIn("operator_round", ops)
        self.assertNotIn("operator_mathop", ops)

    def test_the_bare_round_builtin_does_the_same(self):
        _, project = compile_source('class a { void f(){ x = round(7 / 2); } }')
        ops = opcodes(project, "a")
        self.assertIn("operator_round", ops)
        self.assertNotIn("operator_mathop", ops)

    def test_a_menu_operator_still_becomes_a_mathop(self):
        _, project = compile_source(
            'class a { void f(){ x = Operators.math("floor", 7 / 2); } }'
        )
        block = next(
            b for b in blocks_of(project, "a").values() if b["opcode"] == "operator_mathop"
        )
        self.assertEqual(block["fields"]["OPERATOR"][0], "floor")

    def test_an_operator_the_menu_lacks_warns(self):
        codegen, _ = compile_source(
            'class a { void f(){ x = Operators.math("round up", 7 / 2); } }'
        )
        self.assertTrue(
            any("W0026" in note for note in codegen.diagnostics),
            codegen.diagnostics,
        )
