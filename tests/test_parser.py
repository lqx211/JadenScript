from __future__ import annotations

import unittest

from tests.helpers import SAMPLE_SOURCE, parse_source

from jswc import ast_nodes as A
from jswc.tokens import ParseError


class ParserTests(unittest.TestCase):
    def test_class_and_sprite_annotation(self):
        _, program = parse_source(
            "class player { // Sprite: player (-196,121) size=100 direction=90 show=1\n"
            "  public void greenFlag(){\n  }\n}\n"
        )
        cls = program.classes[0]
        self.assertEqual(cls.name, "player")
        self.assertEqual(cls.sprite.name, "player")
        self.assertEqual((cls.sprite.x, cls.sprite.y), (-196, 121))
        self.assertTrue(cls.sprite.visible)

    def test_function_signature_and_annotation(self):
        _, program = parse_source(
            "class a { public void Events.keyPressed(k){ // hook: when [space] key pressed\n } }"
        )
        fn = program.classes[0].funcs[0]
        self.assertEqual(fn.visibility, "public")
        self.assertEqual(fn.name, ("Events", "keyPressed"))
        self.assertEqual(fn.params, ["k"])
        self.assertEqual(fn.annotation, "hook: when [space] key pressed")

    def test_if_elif_else(self):
        _, program = parse_source(
            "class a { void f(){ if (x){ y=1; } elif (z){ y=2; } else { y=3; } } }"
        )
        stmt = program.classes[0].funcs[0].body[0]
        self.assertIsInstance(stmt, A.IfStmt)
        self.assertEqual(len(stmt.elifs), 1)
        self.assertIsNotNone(stmt.orelse)

    def test_for_with_commas_and_semicolons(self):
        _, program = parse_source(
            "class a { void f(){ for (i=1, i<=10, i++){ x++; } for (j=0; j<3; j++){ y++; } } }"
        )
        body = program.classes[0].funcs[0].body
        self.assertEqual(len(body), 2)
        self.assertIsInstance(body[0], A.ForStmt)
        self.assertIsInstance(body[1], A.ForStmt)

    def test_switch_with_default_underscore(self):
        _, program = parse_source(
            'class a { void f(){ switch(k){ case "p": x=1; break; case _: x=2; } } }'
        )
        stmt = program.classes[0].funcs[0].body[0]
        self.assertIsInstance(stmt, A.SwitchStmt)
        self.assertEqual(len(stmt.cases), 1)
        self.assertIsNotNone(stmt.default)
        self.assertIsInstance(stmt.cases[0].body[-1], A.BreakStmt)

    def test_postfix_ternary(self):
        _, program = parse_source("class a { void f(){ x = (1) if (c) else (2); } }")
        assign = program.classes[0].funcs[0].body[0]
        self.assertIsInstance(assign.value, A.Ternary)

    def test_call_statement_is_async(self):
        _, program = parse_source("class a { void f(){ call Player.main(); } }")
        stmt = program.classes[0].funcs[0].body[0]
        self.assertIsInstance(stmt, A.CallStmt)
        self.assertEqual(stmt.expr.name, "main")

    def test_dotted_chain_and_expression_call(self):
        _, program = parse_source("class a { void f(){ x = Player.Notes.get(i).get(3); } }")
        value = program.classes[0].funcs[0].body[0].value
        self.assertIsInstance(value, A.Call)
        self.assertIsInstance(value.receiver, A.Call)
        self.assertEqual(value.name, "get")

    def test_unbalanced_parentheses_are_repaired_in_lenient_mode(self):
        source = "class a { void f(){ x = 4+((y+3==z*4); } }"
        parser, program = parse_source(source)
        self.assertTrue(parser.diagnostics)
        self.assertIsNotNone(program)

    def test_strict_mode_rejects_unbalanced_parentheses(self):
        from jswc.lexer import tokenize
        from jswc.parser import Parser

        with self.assertRaises(ParseError):
            Parser(tokenize("class a { void f(){ x = ((y); } }"), lenient=False).parse()

    def test_reference_file_parses(self):
        parser, program = parse_source(SAMPLE_SOURCE.read_text(encoding="utf-8"))
        self.assertEqual([c.name for c in program.classes], ["graphical", "synth", "core"])
        self.assertEqual(sum(len(c.funcs) for c in program.classes), 44)
        self.assertEqual(len(parser.diagnostics), 2)


if __name__ == "__main__":
    unittest.main()
