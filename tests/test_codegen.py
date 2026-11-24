from __future__ import annotations

import json
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


class ListClearTests(unittest.TestCase):
    """``list = []`` on an existing list has to empty it.

    It used to compile to nothing at all -- no block, no diagnostic -- which read
    as "clear the buffer" and did not. The NotePlayer's loader lost a whole
    feature to it: every pasted song was appended to the previous one's bytes,
    so the second load parsed the first file again.
    """

    def test_assigning_an_empty_list_clears_it(self) -> None:
        from tests.helpers import compile_source

        _, project = compile_source(
            "public L = [1, 2, 3];\n\n"
            "metadata greenFlag pos 0,0;\n"
            "public void greenFlag() {\n"
            "    L = [];\n"
            "    L.add(9);\n"
            "}\n"
        )
        stage = project.stage
        opcodes = [block.opcode for block in stage.blocks.values()
                   if getattr(block, "opcode", "") in ("data_deletealloflist", "data_addtolist")]
        self.assertIn("data_deletealloflist", opcodes, opcodes)

    def test_a_list_literal_still_declares(self) -> None:
        from tests.helpers import compile_source

        _, project = compile_source(
            "public L = [1, 2, 3];\n\n"
            "metadata greenFlag pos 0,0;\n"
            "public void greenFlag() {\n"
            "    L.add(4);\n"
            "}\n"
        )
        lid = project.stage.list_id("L")
        self.assertEqual([str(x) for x in project.stage.lists[lid][1]], ["1", "2", "3"])


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

    def test_switch_cases_are_walked_for_list_inference(self):
        codegen, project = compile_source(
            'class a { private items = ["x"]; '
            'void f(){ switch(1){ case 1 { Looks.say(items.get(1)); } } } }'
        )
        self.assertFalse(
            [line for line in codegen.diagnostics if "E0037" in line],
            codegen.diagnostics,
        )
        target = next(t for t in project.targets if t.name == "a")
        self.assertIn("items", [value[0] for value in target.lists.values()])

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

    def test_boolean_parameter_placeholder_rebuilds(self):
        # The decompiler writes ``// proccode work %b`` back; the language has
        # one untyped parameter, so the placeholder count is what has to line
        # up, and the prototype's shadow has to be a boolean reporter or
        # scratch-blocks refuses the project ("expected Boolean, found String").
        _, project = compile_source(
            "class a { private void f(x) { // proccode work %b\n Control.wait(0); } }"
        )
        blocks = blocks_of(project, "a")
        proto = next(b for b in blocks.values() if b["opcode"] == "procedures_prototype")
        self.assertEqual(proto["mutation"]["proccode"], "work %b")
        self.assertEqual(json.loads(proto["mutation"]["argumentdefaults"]), ["false"])
        argument_id = json.loads(proto["mutation"]["argumentids"])[0]
        reporter = blocks[proto["inputs"][argument_id][1]]
        self.assertEqual(reporter["opcode"], "argument_reporter_boolean")

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


class VanillaScratchTests(unittest.TestCase):
    """`#enable_feature<vanillaScratch>`: blocks scratch.mit.edu can run.

    Scratch rejects a whole project for one block it cannot execute, and
    `control_while` / `control_for_each` are TurboWarp's additions to the
    control category -- they carry a built-in prefix, so nothing about the
    opcode's shape says it will not run there. Both have an exact vanilla
    spelling, and the lowering is in the compiler because a source cannot say
    "the same loop, but the other block".
    """

    WHILE = """
        #enable_feature<vanillaScratch>;
        class a {
            void f() {
                while (i < 3) { i += 1; }
            }
        }
    """

    TEMP_VARS = """
        #enable_feature<useTempVariables>;
        #enable_feature<vanillaScratch>;
        class a {
            private _i;
            void f() {
                _i += 1;
            }
        }
    """

    def test_vanilla_beats_useTempVariables(self):
        """Thread variables *are* the lmsTempVars2 extension, so a vanilla build
        cannot have them: the project would not load on scratch.mit.edu, and the
        source would look like it asked for the opposite."""
        codegen, project = compile_source(self.TEMP_VARS)
        ops = opcodes(project, "a")
        self.assertFalse({o for o in ops if o.startswith("lmsTempVars2")}, ops)
        self.assertNotIn("lmsTempVars2", project.extensions)
        self.assertTrue(
            any("W0027" in d for d in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_without_vanilla_the_thread_variable_is_still_available(self):
        codegen, project = compile_source(self.TEMP_VARS.replace(
            "#enable_feature<vanillaScratch>;\n        ", ""))
        ops = opcodes(project, "a")
        self.assertTrue({o for o in ops if o.startswith("lmsTempVars2")}, ops)

    def test_while_becomes_repeat_until_not(self):
        _, project = compile_source(self.WHILE)
        ops = opcodes(project, "a")
        self.assertNotIn("control_while", ops, "no TurboWarp-only loop")
        self.assertIn("control_repeat_until", ops)
        self.assertIn("operator_not", ops,
                      "the condition is negated, because `while c` is "
                      "`repeat until not c`")

    def test_the_condition_is_the_negation_of_the_one_written(self):
        _, project = compile_source(self.WHILE)
        blocks = blocks_of(project, "a")
        repeat = next(b for b in blocks.values()
                      if b["opcode"] == "control_repeat_until")
        not_block = blocks[repeat["inputs"]["CONDITION"][1]]
        self.assertEqual(not_block["opcode"], "operator_not")
        less = blocks[not_block["inputs"]["OPERAND"][1]]
        self.assertEqual(less["opcode"], "operator_lt")

    def test_for_each_becomes_repeat_with_a_counter(self):
        source = """
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    for (i = 1; i <= 4; i += 1) { total += i; }
                }
            }
        """
        _, project = compile_source(source)
        ops = opcodes(project, "a")
        self.assertNotIn("control_for_each", ops)
        self.assertIn("control_repeat_until", ops)
        self.assertGreaterEqual(ops.count("data_changevariableby"), 2,
                                "the body's own increment and the counter's")

    def test_without_the_feature_the_turbowarp_blocks_are_still_used(self):
        """The flag is a choice, not a new default: a TurboWarp project keeps
        the cheaper blocks and its existing behaviour."""
        _, project = compile_source("""
            class a { void f() { while (i < 3) { i += 1; } } }
        """)
        ops = opcodes(project, "a")
        self.assertIn("control_while", ops)
        self.assertNotIn("operator_not", ops)

    def test_a_while_inside_a_while_negates_once_per_loop(self):
        source = """
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    while (i < 3) { while (j < 3) { j += 1; } i += 1; }
                }
            }
        """
        _, project = compile_source(source)
        ops = opcodes(project, "a")
        self.assertEqual(ops.count("control_repeat_until"), 2)
        self.assertEqual(ops.count("operator_not"), 2)

    def test_comment_blocks_leave_their_code_behind(self):
        """`Comments.*` is a note, not a statement.

        `commentC("why") { … }` is a label on the code under it, and
        `commentReporter("why", x)` is a label on a value. In a Scratch build
        the label goes and what it labelled stays -- dropping the body instead
        would quietly delete the program.
        """
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    Comments.commentC("label") { total += 1; }
                    Comments.commentCommand("just a note");
                    total = total + Comments.commentReporter("why", 5);
                    if (Comments.commentBoolean("ok", total > 3)) { total += 1; }
                }
            }
        """)
        ops = opcodes(project, "a")
        self.assertFalse([o for o in ops if o.startswith("lmscomments")],
                         "no comment block survives")
        self.assertIn("data_changevariableby", ops, "the labelled code is kept")
        self.assertIn("operator_add", ops, "and the labelled value is kept")
        self.assertIn("control_if", ops)
        self.assertEqual(ops.count("data_changevariableby"), 2,
                         "one inside the comment's body, one inside the if")

    def test_without_the_feature_comments_are_still_blocks(self):
        _, project = compile_source("""
            class a { void f() { Comments.commentCommand("x"); } }
        """)
        self.assertIn("lmscomments_commentCommand", opcodes(project, "a"))

    def test_the_console_becomes_a_list(self):
        """`console.log(x)` -> `add x to (Console)`, and clear empties it.

        A Scratch project cannot ship an extension, so the console is a Stage
        list. Stage, not sprite: a console is one console for the project.
        """
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    console.log("a");
                    log("b");
                    warn("c");
                    console.clear();
                }
            }
        """)
        ops = opcodes(project, "a")
        self.assertFalse([o for o in ops if o.startswith("sipcconsole")])
        self.assertEqual(ops.count("data_addtolist"), 3,
                         "log, and the bare log()/warn() spellings")
        self.assertIn("data_deletealloflist", ops)
        self.assertIn("Console", [v[0] for v in project.stage.lists.values()])
        sprites = [t for t in project.targets if not t.is_stage]
        self.assertEqual([t.lists for t in sprites], [{},] * len(sprites),
                         "the console is not any sprite's own list")

    def test_a_named_timer_becomes_two_lists(self):
        """`Timer.getTimer(n)` -> `timer - item (indexOf n) of starts`.

        The extension's per-name timers are a dictionary Scratch does not have;
        two parallel lists -- the names, and the `timer` reading when each was
        started -- are the same thing with the keys in first-use order.
        """
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    Timer.startTimer("main");
                    x = Timer.getTimer("main");
                }
            }
        """)
        ops = opcodes(project, "a")
        self.assertFalse([o for o in ops if o.startswith("lmsTimers")])
        self.assertIn("control_if_else", ops, "start is add-or-replace")
        self.assertIn("data_listcontainsitem", ops, "adding only a new name")
        self.assertIn("data_itemnumoflist", ops, "and finding it again")
        self.assertIn("operator_subtract", ops, "a timer is now minus then")
        self.assertIn("sensing_timer", ops)
        self.assertEqual(ops.count("sensing_timer"), 3,
                         "two writes on the start path, one on the read")
        names = {v[0] for v in project.stage.lists.values()}
        self.assertEqual(names, {"TimerNames", "TimerStarts"})

    def test_the_runtime_probe_answers_what_scratch_is(self):
        """A project may ask the player about itself; Scratch's answers are fixed."""
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a { void f() { w = Runtime.stageWidth(); h = Runtime.framerateLimit(); } }
        """)
        self.assertFalse([o for o in opcodes(project, "a")
                          if o.startswith("runtimeoptions")])

    def test_a_socket_is_never_connected_and_never_sends(self):
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    WebSocket.sendMessage("x");
                    if (WebSocket.isConnected()) { y = 1; }
                }
            }
        """)
        ops = opcodes(project, "a")
        self.assertFalse([o for o in ops if o.startswith("gsaWebsocket")],
                         "a Scratch project has no socket")
        self.assertIn("control_if", ops, "the guard survives; its body is dead")

    def test_a_power_becomes_ten_to_the_log(self):
        """Scratch has no power block, but `[10 ^]` and `[log]` compose one."""
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a { void f() { y = 2 ** 4; } }
        """)
        ops = opcodes(project, "a")
        self.assertNotIn("utilities_exponent", ops)
        self.assertEqual(ops.count("operator_mathop"), 2)
        maths = [b["fields"]["OPERATOR"][0] for b in blocks_of(project, "a").values()
                 if b["opcode"] == "operator_mathop"]
        self.assertEqual(sorted(maths), ["10 ^", "log"])

    def test_loose_blocks_are_left_out(self):
        """A loose stack is on the canvas, not in the program.

        It is also where a TurboWarp round trip's leftovers collect, so a
        Scratch build drops them rather than shipping blocks that can only make
        the upload fail.
        """
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                void f() { x = 1; }
                loose { WebSocket.sendMessage("leftover"); }
            }
        """)
        self.assertFalse([o for o in opcodes(project, "a")
                          if o.startswith("gsaWebsocket")])

    def test_a_save_hook_is_left_out(self):
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                metadata onSave pos 0,0;
                void onSave() { // hook: before project save
                    x = 1;
                }
            }
        """)
        self.assertFalse([o for o in opcodes(project, "a")
                          if o.startswith("lmsMoreEvents")])

    def test_a_for_each_thread_variable_becomes_a_counting_loop(self):
        """`Temp.forEachThreadVariable("_i", n)` binds 1..n, so it is a repeat."""
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    Temp.forEachThreadVariable("_i", 4) { total += _i; }
                }
            }
        """)
        ops = opcodes(project, "a")
        self.assertNotIn("lmsTempVars2_forEachThreadVariable", ops)
        self.assertIn("control_repeat", ops)
        self.assertEqual(ops.count("data_changevariableby"), 2,
                         "the counter, and the body's own change")

    def test_a_conditional_in_an_argument_is_hoisted(self):
        """Scratch's `if` is a statement, so a ternary cannot stay in an argument.

        It becomes `set _t = b; if c { set _t = a }` *before* the statement,
        and the expression itself reads `_t`. Both branches are evaluated,
        which is what the `utilities` reporter did too -- it was handed three
        already-evaluated arguments.
        """
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    r = 5 if (r == 0) else 9;
                }
            }
        """)
        ops = opcodes(project, "a")
        self.assertNotIn("utilities_ternaryOperator", ops)
        self.assertIn("control_if", ops)
        self.assertEqual(ops.count("data_setvariableto"), 3,
                         "the else value, the then value, and the assignment "
                         "itself reading the variable")
        blocks = blocks_of(project, "a")
        if_block = next(b for b in blocks.values() if b["opcode"] == "control_if")
        self.assertIsNotNone(if_block["next"],
                             "the if is followed by the statement it was "
                             "hoisted out of")
        self.assertIn("SUBSTACK", if_block["inputs"])

    def test_a_nested_conditional_inside_an_argument_works(self):
        """The inner one is evaluated while emitting the outer one's branch, so
        every name has to be allocated before any of the blocks are built."""
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    x = a if (c == 1) else (b if (d == 2) else e);
                }
            }
        """)
        ops = opcodes(project, "a")
        self.assertNotIn("utilities_ternaryOperator", ops)
        self.assertEqual(ops.count("control_if"), 2)

    def test_the_hoisted_conditional_runs_before_its_statement(self):
        """The set/if pair must come *first* or the statement reads a stale value."""
        _, project = compile_source("""
            #enable_feature<vanillaScratch>;
            class a {
                void f() {
                    s = 1;
                    t = 5 if (s == 1) else 9;
                }
            }
        """)
        blocks = blocks_of(project, "a")
        first = next(b for b in blocks.values()
                     if b.get("topLevel") and b["opcode"] == "procedures_definition")
        seen = []
        cur = first
        while cur is not None:
            seen.append(cur["opcode"])
            cur = blocks.get(cur["next"]) if cur.get("next") else None
        self.assertEqual(seen[1], "data_setvariableto", "s = 1 runs first")
        self.assertLess(seen.index("control_if"), len(seen) - 1,
                        "the if is not the last block: the statement follows it")
        self.assertEqual(seen[-1], "data_setvariableto",
                         "and the last block is the assignment that reads it")


class SetXYFusionTests(unittest.TestCase):
    """``Motion.x = a; Motion.y = b;`` is one ``motion_gotoxy`` in the reference.

    The VM's ``setX`` and ``setY`` both go through ``setXY``, so the pair clamps
    to the stage exactly like the combined block does.
    """

    def test_two_assignments_become_one_block(self) -> None:
        _, project = compile_source(
            "class main {\n"
            "    public void greenFlag() { Motion.x = 10; Motion.y = -20; }\n}\n"
        )
        self.assertEqual(opcodes(project, "main"), ["event_whenflagclicked", "motion_gotoxy"])

    def test_the_values_land_in_x_and_y(self) -> None:
        _, project = compile_source(
            "class main {\n"
            "    public void greenFlag() { Motion.x = 10; Motion.y = -20; }\n}\n"
        )
        block = next(b for b in blocks_of(project, "main").values()
                     if b["opcode"] == "motion_gotoxy")
        self.assertEqual(block["inputs"]["X"], [1, [4, "10"]])
        self.assertEqual(block["inputs"]["Y"], [1, [4, "-20"]])

    def test_a_change_and_a_set_do_not_fuse(self) -> None:
        _, project = compile_source(
            "class main {\n"
            "    public void greenFlag() { Motion.x += 3; Motion.y = 4; }\n}\n"
        )
        self.assertEqual(
            opcodes(project, "main"),
            ["event_whenflagclicked", "motion_changexby", "motion_sety"],
        )

    def test_y_then_x_does_not_fuse(self) -> None:
        _, project = compile_source(
            "class main {\n"
            "    public void greenFlag() { Motion.y = 4; Motion.x = 3; }\n}\n"
        )
        self.assertEqual(
            opcodes(project, "main"), ["event_whenflagclicked", "motion_sety", "motion_setx"]
        )

    def test_a_statement_between_them_keeps_them_apart(self) -> None:
        _, project = compile_source(
            "class main {\n"
            "    public void greenFlag() {\n"
            "        Motion.x = 1;\n        Pen.clear();\n        Motion.y = 2;\n    }\n}\n"
        )
        ops = opcodes(project, "main")
        self.assertIn("motion_setx", ops)
        self.assertIn("motion_sety", ops)
        self.assertNotIn("motion_gotoxy", ops)


class ShowVariableMonitorTests(unittest.TestCase):
    """``showVariable`` needs an entry in ``monitors[]``, not just the block.

    Without one the VM invents a monitor at a default place, and the editor
    forgets where it was on the next reload.
    """

    def compile_show(self):
        return compile_source(
            "public score = 0;\n"
            "stage {\n"
            "    public void greenFlag() { Variables.showVariable(score); }\n}\n"
        )[1]

    def test_the_monitor_is_stored(self) -> None:
        project = self.compile_show()
        self.assertEqual(len(project.monitors), 1)
        entry = project.monitors[0]
        self.assertEqual(entry["params"], {"VARIABLE": "score"})
        self.assertEqual(entry["opcode"], "data_variable")

    def test_it_starts_hidden_so_the_block_decides(self) -> None:
        project = self.compile_show()
        self.assertFalse(project.monitors[0]["visible"])

    def test_hide_variable_stores_nothing(self) -> None:
        project = compile_source(
            "public score = 0;\n"
            "stage {\n"
            "    public void greenFlag() { Variables.hideVariable(score); }\n}\n"
        )[1]
        self.assertEqual(project.monitors, [])

    def test_a_declared_monitor_is_not_duplicated(self) -> None:
        project = compile_source(
            "public score = 0;\n"
            "metadata slider Stage.score at 5,6 120x30 shown range 0..10;\n"
            "stage {\n"
            "    public void greenFlag() { Variables.showVariable(score); }\n}\n"
        )[1]
        self.assertEqual(len(project.monitors), 1, project.monitors)
        self.assertTrue(project.monitors[0]["visible"], "the declared one wins")


class MonitorStyleTests(unittest.TestCase):
    """The ``metadata`` spelling of a monitor's look.

    A plain read-out needs no mode at all, ``largeReadout`` is ``large``, and a
    slider may carry its bounds in parentheses -- which is the only place a
    range can be typed without remembering ``range a..b``.
    """

    def monitor(self, metadata: str) -> dict:
        project = compile_source(
            "public score = 0;\n"
            f"{metadata}\n"
            "stage {\n    public void greenFlag() { score = 1; }\n}\n"
        )[1]
        self.assertEqual(len(project.monitors), 1, project.monitors)
        return project.monitors[0]

    def test_a_readout_needs_no_mode(self) -> None:
        entry = self.monitor("metadata Stage.score at 5,6 100x24 shown;")
        self.assertEqual(entry["mode"], "default")
        self.assertTrue(entry["visible"])

    def test_normal_is_default(self) -> None:
        self.assertEqual(
            self.monitor("metadata normal Stage.score at 0,0 100x24;")["mode"], "default"
        )

    def test_large_readout(self) -> None:
        entry = self.monitor("metadata largeReadout Stage.score at 5,6 200x24 hidden;")
        self.assertEqual(entry["mode"], "large")
        self.assertEqual((entry["x"], entry["y"], entry["width"]), (5, 6, 200))
        self.assertFalse(entry["visible"])

    def test_slider_range_in_parentheses(self) -> None:
        for metadata in (
            "metadata slider(0,2) Stage.score at 5,140 120x24 shown;",
            "metadata Stage.score slider(0,2) at 5,140 120x24 shown;",
        ):
            entry = self.monitor(metadata)
            self.assertEqual(entry["mode"], "slider")
            self.assertEqual((entry["sliderMin"], entry["sliderMax"]), (0, 2))
            self.assertTrue(entry["isDiscrete"], "whole bounds step")

    def test_a_bare_slider_is_scratchs_default(self) -> None:
        entry = self.monitor("metadata slider Stage.score at 0,0 100x24 shown;")
        self.assertEqual((entry["sliderMin"], entry["sliderMax"]), (0, 100))
        self.assertTrue(entry["isDiscrete"])

    def test_fractional_bounds_stay_continuous(self) -> None:
        entry = self.monitor("metadata slider(0.2,4) Stage.score at 0,0 100x24 shown;")
        self.assertEqual((entry["sliderMin"], entry["sliderMax"]), (0.2, 4))
        self.assertFalse(entry["isDiscrete"], "0.2 is a rate, not a step")


class WarpAnnotationTests(unittest.TestCase):
    """``// warp`` and ``// no-warp`` decide one custom block, not the build."""

    def warps(self, source: str) -> dict[str, str]:
        _, project = compile_source(source)
        flags = {}
        for block in project.targets[1].blocks.values():
            if block.opcode != "procedures_definition":
                continue
            proto = block.inputs["custom_block"][1]
            shadow = None
            for candidate in project.targets[1].blocks.values():
                if candidate.id == proto:
                    shadow = candidate
            flags[shadow.mutation["proccode"]] = shadow.mutation["warp"]
        return flags

    SOURCE = (
        "class main {\n"
        "    public void greenFlag() { a(); b(); c(); }\n"
        "    private void a() { // warp\n        Motion.turnRight(1);\n    }\n"
        "    private void b() { // no-warp\n        Motion.turnRight(1);\n    }\n"
        "    private void c() {\n        Motion.turnRight(1);\n    }\n}\n"
    )

    def test_the_annotations_are_read(self) -> None:
        self.assertEqual(self.warps(self.SOURCE), {"a": "true", "b": "false", "c": "false"})

    def test_no_warp_beats_the_command_line(self) -> None:
        _, project = compile_source(self.SOURCE, warp=True)
        flags = {}
        for block in project.targets[1].blocks.values():
            if block.opcode == "procedures_definition":
                shadow = project.targets[1].blocks[block.inputs["custom_block"][1]]
                flags[shadow.mutation["proccode"]] = shadow.mutation["warp"]
        self.assertEqual(flags, {"a": "true", "b": "false", "c": "true"})


class MetadataValidationTests(unittest.TestCase):
    """An unknown or mistyped target metadata key says so instead of vanishing."""

    def diagnostics(self, source: str) -> list[str]:
        codegen, _ = compile_source(source)
        return codegen.diagnostics

    def test_an_unknown_sprite_key_is_an_error(self) -> None:
        notes = self.diagnostics("class main (shwo=0) {\n    public void greenFlag() { }\n}\n")
        self.assertTrue(any("E0078" in n and "'shwo'" in n for n in notes), notes)

    def test_a_non_numeric_size_is_an_error(self) -> None:
        notes = self.diagnostics('class main (size="big") {\n    public void greenFlag() { }\n}\n')
        self.assertTrue(any("E0079" in n and "'size'" in n for n in notes), notes)

    def test_a_bad_rotation_is_an_error(self) -> None:
        notes = self.diagnostics("class main (rotation=spin) {\n    public void greenFlag() { }\n}\n")
        self.assertTrue(any("E0079" in n and "'rotation'" in n for n in notes), notes)

    def test_a_bad_stage_value_is_not_applied(self) -> None:
        notes = self.diagnostics("stage: tempo=fast;\nstage {\n    public void greenFlag() { }\n}\n")
        self.assertTrue(any("E0079" in n and "'tempo'" in n for n in notes), notes)

    def test_a_good_one_still_works(self) -> None:
        codegen, _ = compile_source(
            'class main (size=80, rotation="leftright", show=0) {\n'
            "    public void greenFlag() { }\n}\n"
        )
        self.assertEqual(codegen.diagnostics, [])
        target = codegen.project.targets[1]
        self.assertEqual(target.size, 80)
        self.assertEqual(target.rotation_style, "left-right")
        self.assertFalse(target.visible)


class AskAsValueTests(unittest.TestCase):
    """``a = Sensing.ask("...")`` answers with what was typed.

    Scratch's ask block is a statement that yields, and the answer is a separate
    reporter, so the value form has to ask first and read the answer back. It
    used to compile to an empty string with no diagnostic at all.
    """

    def source(self, body: str) -> str:
        return (
            "class main {\n"
            "    private a = 0;\n"
            "    private void go() {\n"
            f"        {body}\n"
            "    }\n"
            "    public void greenFlag() { go(); }\n"
            "}\n"
        )

    def chain(self, codegen) -> list[dict]:
        """The blocks of ``go()``, in the order they run."""
        blocks = blocks_of_codegen(codegen)
        head = next(b for b in blocks.values() if b["opcode"] == "procedures_definition")
        out = []
        current = head["next"]
        while current:
            out.append(blocks[current])
            current = blocks[current]["next"]
        return out

    def test_the_question_is_asked_before_the_value_is_used(self) -> None:
        codegen, _ = compile_source(self.source('a = Sensing.ask("who");'))
        self.assertEqual(codegen.diagnostics, [])
        chain = self.chain(codegen)
        self.assertEqual(
            [b["opcode"] for b in chain],
            ["sensing_askandwait", "data_setvariableto", "data_setvariableto"],
        )
        self.assertEqual(chain[0]["inputs"]["QUESTION"][1][1], "who")
        # the temporary holds the answer, and `a` reads the temporary
        self.assertEqual(chain[1]["fields"]["VARIABLE"][0][:1], "_")
        answer = chain[1]["inputs"]["VALUE"][1]
        self.assertEqual(self.chain_block(codegen, answer)["opcode"], "sensing_answer")
        self.assertEqual(chain[2]["fields"]["VARIABLE"][0], "a")
        self.assertEqual(
            self.chain_block(codegen, chain[2]["inputs"]["VALUE"][1])["opcode"],
            "data_variable",
        )

    def test_two_questions_in_one_statement_keep_their_own_answers(self) -> None:
        codegen, _ = compile_source(
            self.source('Looks.say(join(Sensing.ask("one"), Sensing.ask("two")));')
        )
        self.assertEqual(codegen.diagnostics, [])
        asks = [b for b in blocks_of_codegen(codegen).values()
                if b["opcode"] == "sensing_askandwait"]
        self.assertEqual(len(asks), 2)
        temporaries = {
            b["fields"]["VARIABLE"][0]
            for b in blocks_of_codegen(codegen).values()
            if b["opcode"] == "data_setvariableto"
        }
        self.assertEqual(len(temporaries), 2)      # one temporary each

    def test_a_bare_ask_is_still_a_statement(self) -> None:
        codegen, _ = compile_source(self.source('Sensing.ask("bare");'))
        self.assertEqual(codegen.diagnostics, [])
        opcodes = [b["opcode"] for b in blocks_of_codegen(codegen).values()]
        self.assertIn("sensing_askandwait", opcodes)
        self.assertNotIn("data_setvariableto", opcodes)   # no temporary asked for

    def chain_block(self, codegen, block_id) -> dict:
        return blocks_of_codegen(codegen)[block_id]


def blocks_of_codegen(codegen) -> dict:
    target = codegen.project.targets[1]
    return {bid: block.to_json() for bid, block in target.blocks.items()}
