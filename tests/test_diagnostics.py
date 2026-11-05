"""The diagnostic catalogue: codes, hints and the promise that none are bare.

``E001 syntax error`` used to describe every syntax problem.  These tests keep
the replacement honest: each raise site stamps a specific four-digit code, the
catalogue knows it, and the rendered output carries a ``help:`` line.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from jswc import diagnostics as diag
from jswc import ui
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.tokens import JswError

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "jswc"
CODE = re.compile(r"^[EWI]\d{4}$")


def _raises(source: str, lenient: bool = True) -> JswError:
    """Parse ``source`` and return the error it raises."""
    try:
        Parser(tokenize(source), lenient=lenient).parse()
    except JswError as exc:
        return exc
    raise AssertionError("expected the parser to reject this file")


def _calls(path: Path) -> list[tuple[ast.Call, ast.FunctionDef | None]]:
    """``(call, enclosing function)`` for every error/diagnostic call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in {"ParseError", "LexError", "CompileError", "diag"}:
            continue
        scope, parent = None, parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = parent
                break
            parent = parents.get(parent)
        out.append((node, scope))
    return out


def _dynamic_code_ok(call: ast.Call, scope: ast.FunctionDef | None) -> bool:
    """``raise ParseError(..., code=code)`` is fine inside a helper that took ``code``."""
    if scope is None:
        return False
    known = {a.arg for a in scope.args.args + scope.args.kwonlyargs}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            known |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    codes = [kw.value for kw in call.keywords if kw.arg == "code"] or call.args[1:2]
    return any(isinstance(value, ast.Name) and value.id in known for value in codes)


class CatalogueTests(unittest.TestCase):
    def test_codes_are_well_formed_and_unique(self):
        seen: set[str] = set()
        for message in diag.MESSAGES:
            with self.subTest(code=message.code):
                self.assertRegex(message.code, CODE)
                self.assertIn(message.severity, ("error", "warning", "info"))
                self.assertNotIn(message.code, seen, "duplicate code")
                self.assertTrue(message.title, "every code needs a title")
                seen.add(message.code)

    def test_numbers_rise_without_reuse(self):
        """Numbers only ever go up: a retired code leaves a hole, never a reuse."""
        for prefix in "EWI":
            numbers = sorted(
                int(m.code[1:]) for m in diag.MESSAGES
                if m.code.startswith(prefix) and m.code != "E9999"
            )
            with self.subTest(prefix=prefix):
                self.assertTrue(numbers, "the prefix has codes")
                self.assertEqual(numbers[0], 1, "numbering starts at 1")
                self.assertEqual(len(numbers), len(set(numbers)), "no code is reused")
                self.assertEqual(numbers, sorted(numbers))

    def test_each_severity_uses_its_own_prefix(self):
        expected = {"error": "E", "warning": "W", "info": "I"}
        for message in diag.MESSAGES:
            with self.subTest(code=message.code):
                self.assertEqual(message.code[0], expected[message.severity])

    def test_hints_read_like_instructions(self):
        for message in diag.MESSAGES:
            if not message.hint:
                continue
            with self.subTest(code=message.code):
                self.assertTrue(message.hint[0].isupper() or message.hint[0] == "`")
                self.assertTrue(message.hint.endswith("."))


class CodeStampTests(unittest.TestCase):
    """Every code used in the source exists, and every raise site has one."""

    def test_every_code_in_the_source_is_catalogued(self):
        used = set()
        for path in sorted(PACKAGE.glob("*.py")):
            if path.name == "diagnostics.py":
                continue
            for literal in re.findall(r'"([EW]\d{4})"', path.read_text(encoding="utf-8")):
                used.add(literal)
        self.assertTrue(used)
        for code in sorted(used):
            with self.subTest(code=code):
                self.assertIn(code, diag.BY_CODE)

    def test_no_code_is_dead(self):
        """A catalogue entry nothing can produce is a lie in the docs."""
        used = set()
        for path in sorted(PACKAGE.glob("*.py")):
            if path.name == "diagnostics.py":
                continue
            used |= set(re.findall(r"\b([EWI]\d{4})\b", path.read_text(encoding="utf-8")))
        used |= {code for _, code in diag.LEGACY}
        used |= set(diag.EXPECT_CODES.values())
        dead = sorted(set(diag.BY_CODE) - used - {"E9999"})
        self.assertEqual(dead, [], "catalogue codes nothing raises")

    def test_every_raise_site_carries_a_code(self):
        for path in sorted(PACKAGE.glob("*.py")):
            if path.name == "diagnostics.py":
                continue
            for call, scope in _calls(path):
                name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                with self.subTest(file=path.name, line=call.lineno):
                    if _dynamic_code_ok(call, scope):
                        continue
                    if name == "diag":
                        # ``self.diag(node, "Exxxx", message)``
                        self.assertGreaterEqual(len(call.args), 3)
                        code = call.args[1]
                    else:
                        codes = [kw for kw in call.keywords if kw.arg == "code"]
                        self.assertEqual(len(codes), 1,
                                         f"{name} at {path.name}:{call.lineno} has no code=")
                        code = codes[0].value
                    self.assertIsInstance(code, ast.Constant)
                    self.assertRegex(code.value, CODE)
                    self.assertIn(code.value, diag.BY_CODE)


class ParserCodeTests(unittest.TestCase):
    CASES = [
        # lexical
        ('x = "abc;\n', "E0002"),
        ("/* never closed\n", "E0004"),
        ("x = 0x;\n", "E0007"),
        # syntax
        ("class A extends B {\n}\n", "E0011"),
        ("class A {\n", "E0019"),
        ("x = 1\n", "E0034"),
        ("metadata Stage.tick pos 1,2\nclass A {\n}\n", "E0010"),
        ("#enable_feature<pen\nclass A {\n}\n", "E0030"),
        ('class A {\n  public void tick() {\n    call 1;\n  }\n}\n', "E0025"),
        ('class A {\n  public void tick() {\n    f(1) = 2;\n  }\n}\n', "E0026"),
        ('class A {\n  public void tick() {\n    x = f"{0 1}";\n  }\n}\n', "E0028"),
        ("metadata ;\nclass A {\n}\n", "E0031"),
        ("metadata nonsense here;\nclass A {\n}\n", "E0032"),
    ]

    def test_errors_carry_the_specific_code(self):
        for source, code in self.CASES:
            with self.subTest(code=code, source=source.splitlines()[0]):
                exc = _raises(source)
                self.assertEqual(exc.code, code, exc.message)

    def test_the_message_is_not_double_stamped(self):
        for source, _ in self.CASES:
            with self.subTest(source=source.splitlines()[0]):
                exc = _raises(source)
                self.assertTrue(exc.message.startswith(exc.code))
                self.assertNotIn(exc.code, exc.message[len(exc.code):])

    def test_the_expected_token_names_the_missing_token(self):
        # In lenient mode the parser closes a dangling '(' and carries on, so
        # strict parsing is what surfaces the missing ')' itself.
        exc = _raises("class A {\n  public void tick() {\n    Looks.say((1);\n  }\n}\n",
                      lenient=False)
        self.assertEqual(exc.code, "E0014")  # a ')' was expected
        self.assertIn(")", exc.message)


class ClassifyTests(unittest.TestCase):
    def test_a_stamped_code_wins_over_the_shape(self):
        # "cannot assign to" alone would be E0042.
        text = "3:3: E0043 lst is a list and cannot be assigned"
        self.assertEqual(diag.classify(text).code, "E0043")

    def test_shapes_still_classify_for_uncoded_text(self):
        self.assertEqual(diag.classify("3:5: expected ')', found '}'").code, "E0014")
        self.assertEqual(diag.classify("undefined variable 'x'").code, "E0035")

    def test_unknown_text_is_internal(self):
        self.assertEqual(diag.classify("nobody wrote this down").code, "E9999")


class RenderingTests(unittest.TestCase):
    def test_the_hint_is_opt_in(self):
        source = 'class A {\n  public void t() {\n    x = "abc;\n  }\n}\n'
        note = "3:9: E0002 string literal opened here is never closed"

        ui.configure(color=False)
        plain = ui.format_diagnostic(note, source, "demo.jsw")
        self.assertIn("E0002", plain)
        self.assertNotIn("help:", plain, "the hint needs --explain")
        self.assertNotIn("\033[", plain)

        ui.configure(color=False, explain=True)
        explained = ui.format_diagnostic(note, source, "demo.jsw")
        self.assertIn("help:", explained)
        self.assertNotIn("\033[", explained)
        ui.configure(color=False)

    def test_every_code_renders_without_raising(self):
        ui.configure(color=False)
        for message in diag.MESSAGES:
            with self.subTest(code=message.code):
                text = ui.format_diagnostic(f"1:1: {message.code} something", None, "x.jsw")
                self.assertIn(message.code, text)


if __name__ == "__main__":
    unittest.main()
