"""Console statements (``log``/``warn``/``error``/``breakpoint``/timers)."""

from __future__ import annotations

import unittest

from tests.helpers import compile_source, opcodes

from jswc import ast_nodes as A
from jswc.codegen import Codegen
from jswc.decompile import Decompiler
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import build_project_dict
from jswc.sb3_read import LoadedProject

CONSOLE_SOURCE = """class a {
    void f() {
        log("hello");
        warn("careful");
        error("broken");
        startTimer("Player");
        stopTimer("Player");
        breakpoint;
    }
}
"""


def roundtrip(source: str) -> str:
    _, project = compile_source(source)
    data, _ = build_project_dict(project)
    return Decompiler(LoadedProject(data=data)).decompile()


def with_zero_width_call(proccode: str, extra: str = "") -> dict:
    """A dangling custom-block call, relabelled to a console add-on name.

    The add-on stores its hidden logging as ``procedures_call`` blocks whose
    proccode is a zero-width-space name and that have no definition.
    """
    source = (
        "class a { void f(){ ghost(\"x\"); " + extra + " }\n"
        "    private void ghost(arg1) { // undefined\n    }\n}\n"
    )
    _, project = compile_source(source)
    target = next(t for t in project.targets if t.name == "a")
    block = next(b for b in target.blocks.values() if b.opcode == "procedures_call")
    block.mutation = dict(block.mutation or {})
    block.mutation["proccode"] = proccode
    # The call must still resolve: the decompiler only rewrites a call whose
    # proccode has a definition (a dangling one never ran in the original).
    prototype = next(
        b for b in target.blocks.values() if b.opcode == "procedures_prototype"
    )
    prototype.mutation = dict(prototype.mutation or {})
    prototype.mutation["proccode"] = proccode
    return build_project_dict(project)[0]


class DanglingConsoleCallTests(unittest.TestCase):
    def test_a_call_without_a_definition_is_not_rewritten(self):
        """It never ran, so the rebuild must not start printing it."""
        source = (
            "class a { void f(){ ghost(\"x\"); }\n"
            "    private void ghost(arg1) { }\n}\n"
        )
        _, project = compile_source(source)
        target = next(t for t in project.targets if t.name == "a")
        call = next(b for b in target.blocks.values() if b.opcode == "procedures_call")
        call.mutation = dict(call.mutation or {})
        call.mutation["proccode"] = "\u200blog\u200b\u200b %s"
        prototype = next(
            b for b in target.blocks.values() if b.opcode == "procedures_prototype"
        )
        prototype.mutation = dict(prototype.mutation or {})
        prototype.mutation["proccode"] = "ghost %s"  # the call no longer resolves
        text = Decompiler(LoadedProject(data=build_project_dict(project)[0])).decompile()
        self.assertNotRegex(text, r"(?m)^\s*log\(")
        self.assertIn("// dropped: log()", text)


class ConsoleStatementTests(unittest.TestCase):
    def test_statements_compile_to_the_consoles_extension(self):
        _, project = compile_source(CONSOLE_SOURCE)
        ops = opcodes(project, "a")
        for opcode in (
            "sipcconsole_Journal",
            "sipcconsole_Warning",
            "sipcconsole_Error",
            "sipcconsole_Timeron",
            "sipcconsole_Timeroff",
            "sipcconsole_debug",
        ):
            self.assertIn(opcode, ops)
        self.assertIn("sipcconsole", project.extensions)

    def test_breakpoint_is_a_statement_not_a_variable(self):
        program = Parser(tokenize("class a { void f(){ breakpoint; } }")).parse()
        codegen = Codegen(program)
        project = codegen.compile()
        self.assertEqual(codegen.diagnostics, [])
        self.assertNotIn("breakpoint", {v.name for v in project.targets[1].variables})
        self.assertIsInstance(program.classes[0].funcs[0].body[0], A.ExprStmt)

    def test_statements_decompile_back(self):
        source = roundtrip(CONSOLE_SOURCE)
        self.assertIn('log("hello");', source)
        self.assertIn('warn("careful");', source)
        self.assertIn('error("broken");', source)
        self.assertIn('startTimer("Player");', source)
        self.assertIn('stopTimer("Player");', source)
        self.assertIn("breakpoint;", source)

    def test_breakpoint_marker_reaches_the_logs_tab(self):
        _, project = compile_source("class a { void f(){ breakpoint; } }")
        target = next(t for t in project.targets if t.name == "a")
        block = next(b for b in target.blocks.values() if b.opcode == "sipcconsole_debug")
        self.assertEqual(block.inputs["string"][1][1], "@breakpoint")

    def test_zero_width_calls_become_console_statements(self):
        """The console add-on hides its calls behind zero-width proccodes."""
        data = with_zero_width_call("\u200blog\u200b\u200b %s")
        text = Decompiler(LoadedProject(data=data)).decompile()
        self.assertIn('log("x");', text)
        self.assertNotIn("__log__", text)

    def test_zero_width_breakpoint_is_not_an_undefined_procedure(self):
        data = with_zero_width_call("\u200bbreakpoint\u200b\u200b")
        decompiler = Decompiler(LoadedProject(data=data))
        text = decompiler.decompile()
        self.assertIn("breakpoint;", text)
        self.assertFalse([n for n in decompiler.diagnostics if "__breakpoint__" in n])

    def test_hidden_call_plus_real_block_is_marked(self):
        data = with_zero_width_call("\u200blog\u200b\u200b %s", 'log("x");')
        text = Decompiler(LoadedProject(data=data)).decompile()
        self.assertEqual(text.count('log("x");'), 2)
        self.assertIn("logged twice in the original project", text)


if __name__ == "__main__":
    unittest.main()
