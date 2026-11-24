"""``public`` is a broadcast, ``private`` is a My Block, and what that implies.

* A public function is how another sprite triggers code, and Scratch's only
  cross-sprite trigger is a broadcast — so every public function with no
  parameters gets a ``when I receive "<name>"`` hat that runs it. The custom
  block is still there, because that is how the owning sprite calls it.
* A broadcast carries no arguments, so a public function *with* parameters is
  a contradiction: E0086. Declare it without ``public`` and it is a My Block,
  callable inside its class with arguments.
* A Boolean slot only accepts a Boolean reporter. scratch-blocks refuses a
  String/Number one ("Input CONDITION expected Boolean, found String") and
  drops the whole script, so a condition that is not a Boolean is wrapped in
  the VM's own truthiness test, ``not <(x) = 0>``.
"""

from __future__ import annotations

import unittest

from tests.helpers import blocks_of, compile_source, opcodes


def receivers(project, sprite: str) -> list[str]:
    return sorted(
        b["fields"]["BROADCAST_OPTION"][0]
        for b in blocks_of(project, sprite).values()
        if b["opcode"] == "event_whenbroadcastreceived"
    )


class PublicIsBroadcastTests(unittest.TestCase):
    def test_a_public_function_gets_a_receiver_hat(self):
        _, project = compile_source("class a { public void start() { Looks.show(); } }")
        self.assertEqual(["start"], receivers(project, "a"))
        self.assertEqual(["start"], list(project.stage.broadcasts.values()))

    def test_the_hat_calls_the_custom_block(self):
        _, project = compile_source("class a { public void start() { Looks.show(); } }")
        ops = opcodes(project, "a")
        self.assertIn("procedures_definition", ops)
        self.assertIn("procedures_call", ops)

    def test_a_private_function_stays_a_my_block(self):
        _, project = compile_source("class a { void work(x) { Looks.show(); } }")
        self.assertEqual([], receivers(project, "a"))
        self.assertEqual([], list(project.stage.broadcasts.values()))
        self.assertIn("procedures_definition", opcodes(project, "a"))

    def test_a_public_function_with_arguments_is_an_error(self):
        codegen, _ = compile_source("class a { public void move(x, y) { Looks.show(); } }")
        self.assertTrue(
            any("E0086" in d and "'move'" in d for d in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_a_hat_named_function_with_arguments_is_not_a_broadcast(self):
        codegen, project = compile_source(
            "class a { public void Events.keyPressed(k) { // hook: when [a] key pressed\n"
            " Looks.show(); } }"
        )
        self.assertEqual([], [d for d in codegen.diagnostics if "E0086" in d])
        self.assertIn("event_whenkeypressed", opcodes(project, "a"))


class BooleanSlotTests(unittest.TestCase):
    def condition_of(self, source: str, sprite: str = "a") -> dict:
        blocks = blocks_of_value(source, sprite)
        while_block = next(
            b for b in blocks.values() if b["opcode"] == "control_while"
        )
        filled = while_block["inputs"]["CONDITION"]
        return blocks, filled[1]

    def test_a_variable_condition_is_wrapped_in_a_boolean(self):
        blocks, child = self.condition_of("class a { void f() { while (active) { Looks.show(); } } }")
        self.assertIn(child, blocks)
        self.assertIn(blocks[child]["opcode"], ("operator_not", "operator_equals"))

    def test_a_comparison_is_left_alone(self):
        blocks, child = self.condition_of(
            "class a { void f() { while (Sensing.timer() > 1) { Looks.show(); } } }"
        )
        self.assertEqual("operator_gt", blocks[child]["opcode"])

    def test_an_and_of_variables_is_wrapped_on_both_sides(self):
        blocks, child = self.condition_of(
            "class a { void f() { while (one && two) { Looks.show(); } } }"
        )
        self.assertEqual("operator_and", blocks[child]["opcode"])
        for operand in ("OPERAND1", "OPERAND2"):
            filled = blocks[child]["inputs"][operand][1]
            self.assertEqual("operator_not", blocks[filled]["opcode"])

    def test_a_boolean_extension_reporter_is_used_as_it_is(self):
        # An extension block declares no output check, so a Boolean slot takes
        # it: wrapping ``is it Cat?`` in ``not <… = 0>`` would be correct but
        # would add two blocks and change the source a decompile produces.
        _, project = compile_source(
            'class a { void f() { if (gtm.isLabel("Cat")) { Looks.show(); } } }'
        )
        blocks = blocks_of(project, "a")
        branch = next(b for b in blocks.values() if b["opcode"] == "control_if")
        child = blocks[branch["inputs"]["CONDITION"][1]]
        self.assertEqual("gtm_isLabel", child["opcode"])

    def test_a_list_item_condition_is_wrapped(self):
        blocks, child = self.condition_of(
            "class a { private l = []; void f() { while (l.get(1)) { Looks.show(); } } }"
        )
        self.assertEqual("operator_not", blocks[child]["opcode"])


def blocks_of_value(source: str, sprite: str) -> dict:
    _, project = compile_source(source)
    return blocks_of(project, sprite)


if __name__ == "__main__":
    unittest.main()
