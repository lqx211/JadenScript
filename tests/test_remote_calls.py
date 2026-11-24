"""``object.mu()`` — calling a function that lives in another class.

A Scratch custom block belongs to one sprite, so a call that crosses a class
boundary cannot be a ``procedures_call``. It lowers to a broadcast
(``broadcast and wait`` for ``object.mu()``; plain ``broadcast`` for
``~object.mu()``) and the target sprite gets a ``when I receive "object.mu"``
hat whose body calls its own custom block. That hat is the only thing that can
run code in another sprite, so it is generated whenever such a call exists.

The lookup is by ``(class, function)``, so the order the classes are declared
in does not matter, and it does not depend on ``namespacedNames``.
"""

from __future__ import annotations

import unittest

from jswc.codegen import Codegen
from jswc.decompile import Decompiler
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import build_project_dict
from jswc.sb3_read import LoadedProject
from tests.helpers import blocks_of, compile_source, opcodes

CALLER = (
    "class model {\n"
    "    void go() { %s }\n"
    "}\n"
)
TARGET = (
    "class object {\n"
    "    public void mu() { Motion.y += 10; }\n"
    "}\n"
)


def build(call: str, *, target: str = TARGET, before: bool = True, **kwargs):
    """Compile ``target`` next to a class that makes ``call``."""
    caller = CALLER % call
    source = caller + target if before else target + caller
    return compile_source(source, **kwargs)


def broadcast_names(project, sprite: str) -> set[str]:
    names = set()
    for b in blocks_of(project, sprite).values():
        if b["opcode"] == "event_whenbroadcastreceived":
            names.add(b["fields"]["BROADCAST_OPTION"][0])
        elif b["opcode"] in ("event_broadcast", "event_broadcastandwait"):
            filled = b["inputs"].get("BROADCAST_INPUT")
            if filled and filled[1][0] == 11:
                names.add(filled[1][1])
    return names


def hat_options(project, sprite: str) -> list[str]:
    return [
        b["fields"]["BROADCAST_OPTION"][0]
        for b in blocks_of(project, sprite).values()
        if b["opcode"] == "event_whenbroadcastreceived"
    ]


class CallTests(unittest.TestCase):
    def test_a_call_into_another_class_waits_for_it(self):
        codegen, project = build("object.mu();")
        self.assertEqual([], [d for d in codegen.diagnostics if "E00" in d])
        self.assertIn("event_broadcastandwait", opcodes(project, "model"))
        self.assertEqual({"object.mu"}, broadcast_names(project, "model"))

    def test_the_target_gets_a_receiver_for_the_broadcast(self):
        _, project = build("object.mu();")
        self.assertEqual(["object.mu"], hat_options(project, "object"))
        self.assertIn("procedures_call", opcodes(project, "object"))

    def test_the_call_does_not_depend_on_declaration_order(self):
        _, forward = build("object.mu();", before=True)
        _, backward = build("object.mu();", before=False)
        self.assertEqual(hat_options(forward, "object"), hat_options(backward, "object"))
        self.assertEqual(
            sorted(opcodes(forward, "model")), sorted(opcodes(backward, "model"))
        )

    def test_fire_and_forget_does_not_wait(self):
        _, project = build("call object.mu();")
        ops = opcodes(project, "model")
        self.assertIn("event_broadcast", ops)
        self.assertNotIn("event_broadcastandwait", ops)

    def test_a_dotted_call_from_the_same_class_stays_a_custom_block(self):
        source = (
            "class object {\n"
            "    public void mu() { Motion.y += 10; }\n"
            "    public void go() { object.mu(); }\n"
            "}\n"
        )
        codegen, project = compile_source(source)
        self.assertEqual([], [d for d in codegen.diagnostics if "E00" in d])
        self.assertEqual([], hat_options(project, "object"))
        self.assertNotIn("event_broadcastandwait", opcodes(project, "object"))

    def test_namespaced_names_builds_the_same_call(self):
        _, plain = build("object.mu();")
        _, namespaced = build(
            "object.mu();", features={"commentMetadata", "namespacedNames"}
        )
        self.assertIn("object.mu", broadcast_names(namespaced, "model"))
        self.assertIn("object.mu", broadcast_names(plain, "model"))

    def test_a_private_function_is_still_an_error(self):
        codegen, _ = build(
            "object.mu();", target="class object { void mu() { } }\n"
        )
        self.assertTrue(
            any("E0041" in d and "private" in d for d in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_a_missing_function_names_the_sprite(self):
        codegen, _ = build("object.nope();")
        self.assertTrue(
            any(
                "E0038" in d and "sprite 'object'" in d and "'nope'" in d
                for d in codegen.diagnostics
            ),
            codegen.diagnostics,
        )

    def test_a_parameterised_function_cannot_be_reached_across_sprites(self):
        codegen, _ = build(
            "object.mu(2);", target="class object { public void mu(n) { } }\n"
        )
        self.assertTrue(
            any("E0085" in d for d in codegen.diagnostics), codegen.diagnostics
        )

    def test_argument_count_is_checked_against_the_other_class(self):
        codegen, _ = build("object.mu(1);")
        self.assertTrue(
            any("E0076" in d and "takes 0" in d for d in codegen.diagnostics),
            codegen.diagnostics,
        )


class RoundTripTests(unittest.TestCase):
    def test_decompile_keeps_the_message_and_the_call(self):
        _, project = build("object.mu();")
        data, _ = build_project_dict(project)
        text = Decompiler(LoadedProject(data=data)).decompile()
        # The hat lives in ``object`` but the call is spelled ``object.mu()``,
        # so the message has to be written out; a plain ``mu`` would come back
        # asking the sprite to receive something nobody sends.
        self.assertIn('"object.mu"', text)
        second = Codegen(
            Parser(tokenize(text)).parse(),
            features={"commentMetadata"},
            allow_undefined=True,
        )
        rebuilt = second.compile()
        self.assertEqual([], [d for d in second.diagnostics if "E00" in d])
        self.assertIn("object.mu", broadcast_names(rebuilt, "model"))
        self.assertEqual(["object.mu"], hat_options(rebuilt, "object"))


if __name__ == "__main__":
    unittest.main()
