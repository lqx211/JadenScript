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
