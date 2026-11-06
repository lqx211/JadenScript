"""``// include "file.jsw"``: modules that are still one project.

A class is a sprite, so splitting the machine into CPU / display / network means
three classes -- but nobody wants a three-thousand-line file. An include is a
line directive: the line is replaced by the named file, relative to the file that
mentioned it. Diagnostics still have to name the module the reader wrote.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from jswc import include

ROOT = Path(__file__).resolve().parent.parent


class ConditionalTests(unittest.TestCase):
    """``#ifdef`` before parsing: one source, two builds.

    A machine that has to run in Scratch and in TurboWarp cannot use the same
    memory: Scratch has lists, TurboWarp can load an extension. The parts that
    differ are marked in the source rather than kept in two files.
    """

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    def lines(self, source: str, defines: set[str] | None = None) -> list[str]:
        path = self.write("a.jsw", source)
        got = include.expand(path, defines=defines).text.splitlines()
        return [line.strip() for line in got if line.strip()]

    def test_a_false_branch_is_not_compiled(self) -> None:
        self.assertEqual(self.lines("""
            #ifdef TURBOWARP
                tw
            #else
                scratch
            #endif
        """), ["scratch"])

    def test_the_defined_name_takes_the_first_branch(self) -> None:
        self.assertEqual(self.lines("""
            #ifdef TURBOWARP
                tw
            #else
                scratch
            #endif
        """, {"TURBOWARP"}), ["tw"])

    def test_ifndef_is_the_other_way_round(self) -> None:
        self.assertEqual(self.lines("""
            #ifndef TURBOWARP
                listed
            #endif
        """), ["listed"])
        self.assertEqual(self.lines("""
            #ifndef TURBOWARP
                listed
            #endif
        """, {"TURBOWARP"}), [])

    def test_conditionals_nest(self) -> None:
        source = """
            #ifdef TURBOWARP
                #ifdef DEBUG
                    traced
                #else
                    quiet
                #endif
            #endif
        """
        self.assertEqual(self.lines(source, {"TURBOWARP"}), ["quiet"])
        self.assertEqual(self.lines(source, {"TURBOWARP", "DEBUG"}), ["traced"])
        self.assertEqual(self.lines(source, {"DEBUG"}), [])

    def test_define_in_the_source_sets_a_flag(self) -> None:
        self.assertEqual(self.lines("""
            #define FAST
            #ifdef FAST
                fast
            #endif
        """), ["fast"])

    def test_a_define_inside_a_dead_branch_does_not_apply(self) -> None:
        self.assertEqual(self.lines("""
            #ifdef NOPE
                #define FAST
            #endif
            #ifdef FAST
                fast
            #endif
        """), [])

    def test_an_unbalanced_directive_is_an_error(self) -> None:
        path = self.write("b.jsw", "#ifdef X\n  one\n")
        with self.assertRaises(include.IncludeError):
            include.expand(path)
        path = self.write("c.jsw", "#endif\n")
        with self.assertRaises(include.IncludeError):
            include.expand(path)

    def test_line_numbers_still_point_at_the_file(self) -> None:
        """A hidden branch must not shift the diagnostics of what follows."""
        path = self.write("d.jsw", """
            #ifdef NOPE
            hidden
            #endif
            visible
        """)
        source = include.expand(path)
        self.assertEqual(source.text.splitlines()[1], "visible")
        file, line = source.origin(2)      # the line "visible" sits on
        self.assertEqual((Path(file).name, line), ("d.jsw", 5))


class IncludeExpansionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    def test_a_line_is_replaced_by_the_module(self) -> None:
        self.write("m.jsw", "public X.m = 1;\n")
        host = self.write("h.jsw", '// include "m.jsw"\nclass A { void f() {} }\n')
        source = include.expand(host)
        self.assertIn("public X.m = 1;", source.text)
        self.assertIn("class A", source.text)
        self.assertLess(source.text.index("X.m"), source.text.index("class A"))

    def test_nesting_is_expanded_depth_first(self) -> None:
        self.write("c.jsw", "class C { void c() {} }\n")
        self.write("b.jsw", '// include "c.jsw"\nclass B { void b() {} }\n')
        host = self.write("a.jsw", '// include "b.jsw"\nclass A { void a() {} }\n')
        source = include.expand(host)
        order = [source.text.index(f"class {n} ") for n in "CBA"]
        self.assertEqual(order, sorted(order))

    def test_every_line_knows_where_it_came_from(self) -> None:
        self.write("m.jsw", "line-one\nline-two\n")
        host = self.write("h.jsw", '// include "m.jsw"\nhost-line\n')
        source = include.expand(host)
        self.assertEqual(source.origin(1)[0].endswith("m.jsw"), True)
        self.assertEqual(source.origin(1)[1], 1)
        self.assertEqual(source.origin(2)[1], 2)
        self.assertEqual(source.origin(3)[0].endswith("h.jsw"), True)

    def test_a_cycle_is_an_error_not_a_hang(self) -> None:
        self.write("a.jsw", '// include "b.jsw"\n')
        self.write("b.jsw", '// include "a.jsw"\n')
        with self.assertRaises(include.IncludeError) as caught:
            include.expand(self.dir / "a.jsw")
        self.assertIn("cycle", str(caught.exception))

    def test_a_missing_module_says_which_one(self) -> None:
        host = self.write("h.jsw", '// include "nope.jsw"\n')
        with self.assertRaises(include.IncludeError) as caught:
            include.expand(host)
        self.assertIn("nope.jsw", str(caught.exception))

    def test_a_diagnostic_names_the_module_not_the_joined_text(self) -> None:
        self.write("m.jsw", "class M { void f() { x = ; } }\n")
        host = self.write("h.jsw", '// include "m.jsw"\nclass A { void f() {} }\n')
        result = subprocess.run(
            [sys.executable, "-m", "jswc", "check", str(host)],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertIn("m.jsw", result.stdout + result.stderr)
        self.assertNotIn("h.jsw:1:", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
