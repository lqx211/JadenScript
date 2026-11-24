"""``install.sh``: the script that puts `jswc` on PATH.

An installer is easy to get wrong in ways only a real run shows, so the tests
do three things: check the script parses under a POSIX shell, exercise the
read-only ``--check`` path against a throwaway HOME (it must write nothing), and
perform one genuine install into a temp prefix and run the resulting command.

The real install is skipped when the environment cannot support it (no ``sh``,
no Python 3.11+); it never touches the machine: ``--prefix``,
``XDG_DATA_HOME`` and ``HOME`` all point inside the test's temp directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "install.sh"
SH = shutil.which("sh")


def run(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [SH, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


def isolated_env(home: Path, data: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_DATA_HOME": str(data),
        # A developer shell that exports PYTHON must not decide what the test
        # installs with.
        "PYTHON": sys.executable,
        "LANG": "C",
    }
    return env


@unittest.skipUnless(SH, "sh is not available")
class ScriptTests(unittest.TestCase):
    def test_the_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), "chmod +x install.sh")

    def test_it_parses_as_posix_sh(self):
        # `sh -n` is a syntax check with no side effects; it catches the class
        # of bug (an unbalanced quote) that only shows up mid-install.
        result = subprocess.run([SH, "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_lists_every_mode(self):
        result = run(["--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--check", "--venv", "--prefix", "--uninstall"):
            self.assertIn(flag, result.stdout, flag)

    def test_an_unknown_option_is_an_error(self):
        result = run(["--not-a-thing"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option", result.stderr)

    def test_check_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            data = Path(tmp) / "data"
            home.mkdir()
            result = run(["--check"], env=isolated_env(home, data))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("check ok; nothing was changed", result.stdout)
            self.assertIn("python", result.stdout)
            self.assertEqual(list(home.iterdir()), [], "a check must not create anything")
            self.assertFalse(data.exists(), "a check must not create the venv")

    def test_check_reports_an_existing_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            data = Path(tmp) / "data"
            prefix = Path(tmp) / "bin"
            home.mkdir()
            prefix.mkdir()
            env = isolated_env(home, data)
            env["PREFIX"] = str(prefix)
            installed = run(["--prefix", str(prefix)], env=env)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            checked = run(["--check", "--prefix", str(prefix)], env=env)
            self.assertIn("state      installed: jswc", checked.stdout)


@unittest.skipUnless(SH, "sh is not available")
class InstallTests(unittest.TestCase):
    def test_install_then_run_then_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            data = Path(tmp) / "data"
            prefix = Path(tmp) / "bin"
            home.mkdir()
            env = isolated_env(home, data)

            installed = run(["--prefix", str(prefix)], env=env)
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            launcher = prefix / "jswc"
            self.assertTrue(launcher.exists(), installed.stdout)

            version = subprocess.run(
                [str(launcher), "--version"], capture_output=True, text=True, timeout=120
            )
            self.assertEqual(version.returncode, 0, version.stderr)
            self.assertIn("jswc", version.stdout)

            # The console script has to work outside the checkout -- that is
            # the whole point of installing it.
            worked = subprocess.run(
                [str(launcher), "codes", "--search", "E0081"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(worked.returncode, 0, worked.stderr)
            self.assertIn("E0081", worked.stdout)

            removed = run(["--prefix", str(prefix), "--uninstall"], env=env)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(launcher.exists(), "the launcher must be gone")
            self.assertFalse((data / "jswc" / "venv").exists(), "the private venv must be gone")


if __name__ == "__main__":
    unittest.main()
