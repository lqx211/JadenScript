"""The completion data and the extension scripts must stay in step.

``tools/gen_vscode_data.py`` snapshots ``jswc``'s tables for the editor; this
suite regenerates the snapshot and compares, so a new block or keyword cannot
be added to the compiler without showing up in autocomplete.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from gen_vscode_data import DATA_PATH, build  # noqa: E402

EXT = ROOT / "vscode-jsw"
NODE = shutil.which("node")


def load_data() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


class DataIsCurrentTests(unittest.TestCase):
    def test_committed_data_matches_the_generator(self):
        # Round-trip through JSON: the generator returns tuples, the file lists.
        self.assertEqual(
            load_data(),
            json.loads(json.dumps(build())),
            "run `make vscode-data` to refresh vscode-jsw/data/jsw.json",
        )

    def test_every_namespace_has_members(self):
        for name, info in load_data()["namespaces"].items():
            with self.subTest(namespace=name):
                self.assertTrue(info["members"], f"{name} would suggest nothing")

    def test_every_member_has_a_kind_and_detail(self):
        for name, info in load_data()["namespaces"].items():
            for member, entry in info["members"]:
                with self.subTest(namespace=name, member=member):
                    self.assertIn(entry["kind"], {"command", "reporter", "property"})
                    self.assertTrue(entry["detail"])

    def test_keywords_and_features_are_present(self):
        data = load_data()
        labels = {k["label"] for k in data["keywords"]}
        for word in ("class", "public", "private", "void", "if", "elif", "else", "repeat"):
            with self.subTest(keyword=word):
                self.assertIn(word, labels)
        self.assertEqual(set(data["features"]), {
            "useTempVariables", "commentMetadata", "namespacedNames",
            "doObfuscation", "hideObjectNames",
        })
        self.assertTrue(data["hats"])
        self.assertTrue(data["annotations"])


class ManifestTests(unittest.TestCase):
    def test_manifest_points_at_files_that_exist(self):
        pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg["main"], "./extension.js")
        self.assertIn("onLanguage:jsw", pkg["activationEvents"])
        for rel in [pkg["main"]] + [
            c["path"] for c in pkg["contributes"]["grammars"] + pkg["contributes"]["snippets"]
        ]:
            with self.subTest(file=rel):
                self.assertTrue((EXT / rel.lstrip("./")).exists())
        self.assertTrue((EXT / "data" / "jsw.json").exists())
        self.assertTrue((EXT / "completion.js").exists())

    def test_quick_suggestions_are_on_for_jsw(self):
        pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
        defaults = pkg["contributes"]["configurationDefaults"]["[jsw]"]
        self.assertTrue(defaults["editor.quickSuggestions"]["other"])


@unittest.skipIf(NODE is None, "node is not installed")
class ColourTests(unittest.TestCase):
    """The extension ships default token colours so the new scopes are tinted."""

    @staticmethod
    def manifest() -> dict:
        return json.loads((ROOT / "vscode-jsw" / "package.json").read_text(encoding="utf-8"))

    @staticmethod
    def rules() -> dict[str, dict]:
        defaults = ColourTests.manifest()["contributes"]["configurationDefaults"]
        block = defaults["editor.tokenColorCustomizations"]["[jsw]"]
        out: dict[str, dict] = {}
        for rule in block["textMateRules"]:
            scope = rule["scope"]
            for name in [scope] if isinstance(scope, str) else scope:
                out[name] = rule["settings"]
        return out

    def test_variables_and_temp_variables_differ(self):
        rules = self.rules()
        normal = rules.get("variable.other.jsw")
        temp = rules.get("variable.other.temp.jsw")
        self.assertIsNotNone(normal, "plain state needs a colour")
        self.assertIsNotNone(temp, "temp state needs its own colour")
        self.assertNotEqual(
            normal["foreground"].lower(), temp["foreground"].lower(),
            "a temp variable must not look exactly like sprite state",
        )

    def test_public_and_private_share_one_colour(self):
        """Both are declared with ``public``/``private`` and use one scope."""
        grammar = json.loads(
            (ROOT / "vscode-jsw" / "syntaxes" / "jsw.tmLanguage.json").read_text(encoding="utf-8")
        )
        text = json.dumps(grammar)
        self.assertNotIn("variable.other.private", text)
        self.assertNotIn("variable.other.public", text)

    def test_metadata_words_are_keyword_coloured(self):
        rules = self.rules()
        for scope in (
            "keyword.other.target-key.jsw",
            "keyword.other.metadata.jsw",
            "keyword.other.annotation.jsw",
        ):
            with self.subTest(scope=scope):
                self.assertIn(scope, rules)

    #: Scopes that deliberately take the editor's own foreground: ``meta.*``
    #: only groups tokens, punctuation should look like punctuation, and an
    #: escape sequence inherits its string's colour.
    THEMED_BY_EDITOR = (
        "constant.character.escape.jsw",
    )

    @staticmethod
    def _emitted_scopes() -> set[str]:
        grammar = json.loads(
            (ROOT / "vscode-jsw" / "syntaxes" / "jsw.tmLanguage.json").read_text(encoding="utf-8")
        )
        out: set[str] = set()

        def walk(patterns) -> None:
            for rule in patterns or ():
                if not isinstance(rule, dict):
                    continue
                for key in ("name", "captures", "beginCaptures", "endCaptures"):
                    value = rule.get(key)
                    if isinstance(value, str):
                        out.add(value)
                    elif isinstance(value, dict):
                        out.update(
                            capture["name"]
                            for capture in value.values()
                            if isinstance(capture, dict) and capture.get("name")
                        )
                walk(rule.get("patterns"))

        walk(grammar["patterns"])
        for repository in grammar["repository"].values():
            walk(repository.get("patterns"))
        return out

    def test_no_scope_is_left_uncoloured_by_accident(self):
        """Every token the grammar emits gets a colour, or is knowingly themed.

        The function-call scopes were invisible for exactly this reason: the
        grammar produced them, no rule named them, and the theme had nothing to
        match. ``meta``/``punctuation`` are containers, not tokens.
        """
        covered = set(self.rules())
        missing = sorted(
            scope
            for scope in self._emitted_scopes()
            if scope not in covered
            and scope not in self.THEMED_BY_EDITOR
            and not scope.startswith(("meta.", "punctuation."))
        )
        self.assertEqual(missing, [], "scopes with no colour rule")

    def test_function_calls_are_coloured(self):
        rules = self.rules()
        for scope in (
            "entity.name.function.call.jsw",   # playNote(60), Pen.setPenSize(2)
            "entity.name.function.jsw",        # void playNote()
            "support.function.builtin.jsw",    # log(), join(), block()
        ):
            with self.subTest(scope=scope):
                self.assertIn(scope, rules)

    def test_an_underscore_call_is_a_call_not_a_temp_variable(self):
        """``_q5(1, 2)`` is a custom block whose name starts with ``_``."""
        grammar = json.loads(
            (ROOT / "vscode-jsw" / "syntaxes" / "jsw.tmLanguage.json").read_text(encoding="utf-8")
        )
        calls = grammar["repository"]["calls"]["patterns"]
        temps = grammar["repository"]["variables"]["patterns"]
        self.assertTrue(any(p.get("name") == "entity.name.function.call.jsw" for p in calls))
        self.assertTrue(any(p.get("name") == "variable.other.temp.jsw" for p in temps))
        # ``#calls`` must be tried before ``#variables``, or the temp rule wins.
        order = [p["include"] for p in grammar["patterns"] if "include" in p]
        self.assertLess(order.index("#calls"), order.index("#variables"))

    def test_every_coloured_scope_is_produced_by_the_grammar(self):
        grammar = json.dumps(
            json.loads(
                (ROOT / "vscode-jsw" / "syntaxes" / "jsw.tmLanguage.json").read_text(
                    encoding="utf-8"
                )
            )
        )
        for scope in self.rules():
            with self.subTest(scope=scope):
                self.assertIn(f'"{scope}"', grammar, "a colour rule for a scope nothing emits")


class ScriptTests(unittest.TestCase):
    def test_scripts_parse(self):
        for name in ("extension.js", "completion.js"):
            with self.subTest(file=name):
                result = subprocess.run(
                    [NODE, "--check", str(EXT / name)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_extension_activates_against_a_stub_vscode(self):
        result = subprocess.run(
            [NODE, str(ROOT / "tools" / "check_extension.js")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("extension OK", result.stdout)

    def test_completion_behaves(self):
        result = subprocess.run(
            [
                NODE,
                str(ROOT / "tools" / "check_completion.js"),
                str(ROOT / "sample.jsw"),
                str(ROOT / "build" / "mast3d" / "TheMast3D.jsw"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("completion OK", result.stdout)


class StateNamespaceHintTests(unittest.TestCase):
    """The state namespaces suggest exactly what they provide.

    ``Variables`` is only the monitor pair — a variable is written
    ``score = 5`` — and ``Lists`` is the list blocks. Every spelling that is
    suggested must compile, and every alias the compiler still accepts must be
    silent in the popup rather than show something else.
    """

    def compiles(self, namespace: str, member: str) -> bool:
        from jswc.codegen import Codegen
        from jswc.lexer import tokenize
        from jswc.parser import Parser

        call = f"{namespace}.{member}"
        arities = ("", '"v"', '"v", 1', '"v", 1, 2')
        statements = [f"{call}({args});" for args in arities]
        statements += [f"a = {call}({args});" for args in arities]
        for statement in statements:
            source = (
                "public a = 0;\npublic v = 0;\npublic l = [1];\n"
                "class A { // Sprite: A\n"
                f" public void go() {{ {statement} }}\n}}\n"
            )
            codegen = Codegen(
                Parser(tokenize(source)).parse(),
                allow_undefined=True,
                features={"commentMetadata"},
            )
            codegen.compile()
            if not [d for d in codegen.diagnostics if " E" in d]:
                return True
        return False

    def members_of(self, namespace: str) -> set[str]:
        return {name for name, _ in load_data()["namespaces"][namespace]["members"]}

    def test_variables_namespace_is_only_the_monitor_pair(self):
        self.assertEqual(self.members_of("Variables"), {"showVariable", "hideVariable"})

    def test_lists_namespace_is_the_list_blocks(self):
        self.assertEqual(
            self.members_of("Lists"),
            {
                "add", "clear", "contains", "delete", "get", "indexOf",
                "insert", "length", "set", "showList", "hideList",
            },
        )

    def test_every_suggested_member_compiles(self):
        for namespace in ("Variables", "Lists", "Clone", "Stage"):
            for name in self.members_of(namespace):
                with self.subTest(namespace=namespace, member=name):
                    self.assertTrue(
                        self.compiles(namespace, name),
                        f"{namespace}.{name} is suggested but does not compile",
                    )

    def test_aliases_compile_without_being_suggested(self):
        from jswc.keywords import STATE_ALIASES

        for namespace, aliases in STATE_ALIASES.items():
            for alias in aliases:
                with self.subTest(namespace=namespace, alias=alias):
                    self.assertTrue(self.compiles(namespace, alias))
                    self.assertNotIn(alias, self.members_of(namespace))


if __name__ == "__main__":
    unittest.main()
