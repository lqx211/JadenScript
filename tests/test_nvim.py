"""The Neovim plugin must match the generator, and really highlight.

``tools/gen_vim_syntax.py`` writes ``nvim/syntax/jsw.vim`` from
``jswc.keywords``; the first test regenerates and compares, so a keyword can
never be added to the compiler without appearing in the editor. The second
runs a real ``nvim`` over a fixture and reads back the syntax group of 36
tokens, which is the only honest way to know a Vim regex actually matches.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_vim_syntax  # noqa: E402

NVIM = shutil.which("nvim")
PLUGIN = ROOT / "nvim"


class GeneratedPluginTests(unittest.TestCase):
    def test_the_plugin_files_exist(self):
        for name in ("syntax/jsw.vim", "ftdetect/jsw.vim", "ftplugin/jsw.vim"):
            with self.subTest(file=name):
                self.assertTrue((PLUGIN / name).exists(), f"run `make nvim-syntax`")

    def test_the_committed_syntax_matches_the_generator(self):
        self.assertEqual(
            (PLUGIN / "syntax" / "jsw.vim").read_text(encoding="utf-8"),
            gen_vim_syntax.syntax_file(),
            "run `make nvim-syntax` to refresh nvim/syntax/jsw.vim",
        )

    def test_the_filetype_is_detected_for_jsw(self):
        text = (PLUGIN / "ftdetect" / "jsw.vim").read_text(encoding="utf-8")
        self.assertIn("*.jsw", text)
        self.assertIn("setfiletype jsw", text)

    def test_commentstring_uses_slash_slash(self):
        text = (PLUGIN / "ftplugin" / "jsw.vim").read_text(encoding="utf-8")
        self.assertIn("commentstring=//", text)


@unittest.skipIf(NVIM is None, "neovim is not installed")
class NeovimHighlightTests(unittest.TestCase):
    def test_neovim_resolves_every_expected_group(self):
        result = subprocess.run(
            [NVIM, "--headless", "-u", "NONE", "-N", "-S",
             str(ROOT / "tools" / "check_nvim_syntax.vim")],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("nvim syntax OK", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
