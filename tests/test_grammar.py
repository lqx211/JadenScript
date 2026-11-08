"""The VS Code grammar must stay in step with the language.

``tools/gen_tm_grammar.py`` is the source of truth; the committed JSON is what
VS Code loads. A regex that does not compile (a stray ``(``) or a keyword that
never made it into a pattern breaks highlighting silently, so both are checked
here.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

from jswc.keywords import ALL_KEYWORDS, BUILTIN_NAMESPACES, FEATURES, RESERVED
from jswc.lexer import tokenize
from jswc.parser import Parser

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from gen_tm_grammar import GRAMMAR_PATH, build_grammar  # noqa: E402


def load_grammar() -> dict:
    return json.loads(GRAMMAR_PATH.read_text(encoding="utf-8"))


def walk(patterns):
    """Every rule dict in the grammar, including nested ``patterns``."""
    for rule in patterns:
        if not isinstance(rule, dict):
            continue
        yield rule
        for key in ("patterns",):
            yield from walk(rule.get(key) or [])


def rules(grammar) -> list[dict]:
    out = []
    for repo in grammar["repository"].values():
        out.extend(walk(repo.get("patterns") or []))
    return out


def scopes_for(context: str, token: str) -> set[str]:
    """Scopes the grammar would put on ``token`` inside ``context``."""
    found: set[str] = set()
    for rule in rules(load_grammar()):
        for key in ("match", "begin"):
            regex = rule.get(key)
            if not regex:
                continue
            try:
                matches = list(re.finditer(regex, context))
            except re.error:  # asserted by test_every_regex_compiles
                continue
            for m in matches:
                if rule.get("name"):
                    if m.group(0) == token:
                        found.add(rule["name"])
                    elif key == "begin" and token.startswith(m.group(0)):
                        # ``begin``/``end`` rules own everything up to their end.
                        found.add(rule["name"])
                    elif token and token in m.group(0) and rule["name"].startswith(
                        "constant.numeric"
                    ):
                        # ``311.111x154.074`` lexes as 311.111 / x154 / .074; the
                        # parts still sit inside one highlighted number.
                        found.add(rule["name"])
                captures = rule.get("beginCaptures" if key == "begin" else "captures") or {}
                for group, capture in captures.items():
                    try:
                        text = m.group(int(group))
                    except (IndexError, TypeError):
                        continue
                    if text == token and capture.get("name"):
                        found.add(capture["name"])
    return found



def scan(text: str, patterns: list | None = None) -> list[tuple[str, list[str]]]:
    """A small, order-faithful stand-in for TextMate.

    Real editors try the grammar's includes in order at every position, so a
    rule that needs ``class`` to still be there loses to the plain keyword rule
    unless it is listed first. ``scopes_for`` cannot see that; this can.
    """
    grammar = load_grammar()
    repo = grammar["repository"]
    top = patterns if patterns is not None else grammar["patterns"]
    out: list[tuple[str, list[str]]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        matched = False
        for include in top:
            for rule in repo[include["include"].lstrip("#")]["patterns"] if "include" in include else [include]:
                regex = rule.get("match")
                if regex:
                    m = re.compile(regex).match(text, i)
                    if not m:
                        continue
                    scopes = [rule["name"]] if rule.get("name") else []
                    for group, cap in (rule.get("captures") or {}).items():
                        try:
                            if m.group(int(group)):
                                scopes.append(cap["name"])
                        except (IndexError, TypeError):
                            continue
                    out.append((m.group(0), scopes))
                    i = m.end()
                else:
                    begin = rule.get("begin")
                    m = re.compile(begin).match(text, i) if begin else None
                    if not m:
                        continue
                    end = re.compile(rule["end"]).search(text, m.end())
                    stop = end.start() if end else n
                    scopes = [rule["name"]] if rule.get("name") else []
                    out.append((text[i:m.end()], scopes))
                    out.extend(scan(text[m.end():stop], rule.get("patterns") or []))
                    if end:
                        out.append((end.group(0), [rule["name"]] if rule.get("name") else []))
                    i = end.end() if end else n
                matched = True
                break
            if matched:
                break
        if not matched:
            out.append((text[i], []))
            i += 1
    return out


def scanned_scopes(text: str, needle: str) -> list[str]:
    """Scopes attached to the token(s) the scanner produced around ``needle``."""
    found: list[str] = []
    for token, scopes in scan(text):
        if needle in token:
            found.extend(scopes)
    return found

class GrammarIsCurrentTests(unittest.TestCase):
    def test_committed_file_matches_the_generator(self):
        self.assertEqual(
            json.loads(GRAMMAR_PATH.read_text(encoding="utf-8")),
            build_grammar(),
            "run `make grammar` to refresh vscode-jsw/syntaxes/jsw.tmLanguage.json",
        )

    def test_grammar_is_well_formed(self):
        grammar = load_grammar()
        self.assertEqual(grammar["scopeName"], "source.jsw")
        self.assertTrue(grammar["patterns"])
        for rule in rules(grammar):
            if "include" in rule:
                continue  # an include-only entry, resolved by its own section
            self.assertTrue(
                rule.get("match") or (rule.get("begin") and rule.get("end")),
                f"rule without match/begin+end: {rule}",
            )

    def test_every_include_resolves(self):
        # A typo in an `#include` silently disables a whole rule.
        grammar = load_grammar()
        text = json.dumps(grammar)
        for name in set(re.findall(r'"#([A-Za-z_]+)"', text)):
            self.assertIn(name, grammar["repository"], f"include #{name} has no repository entry")

    def test_every_regex_compiles(self):
        for rule in rules(load_grammar()):
            for key in ("match", "begin", "end"):
                regex = rule.get(key)
                if not regex:
                    continue
                if key == "end" and re.search(r"\\\d", regex):
                    # ``end: "\\1"`` closes with the quote the ``begin``
                    # matched. TextMate allows it; Python's re does not have a
                    # group to point at.
                    continue
                with self.subTest(regex=regex):
                    re.compile(regex)  # raises on an unbalanced group

    def test_nested_scopes_are_reachable(self):
        # The comment rule must keep its inner patterns, else `// proccode`
        # is just grey text.
        grammar = load_grammar()
        comment = grammar["repository"]["comments"]["patterns"][0]
        self.assertTrue(comment.get("patterns"))
        self.assertIn("keyword.control.directive.jsw", json.dumps(comment))


class KeywordCoverageTests(unittest.TestCase):
    def test_every_keyword_is_scoped(self):
        for keyword in sorted(ALL_KEYWORDS):
            with self.subTest(keyword=keyword):
                self.assertTrue(
                    scopes_for(f"x = {keyword} y;", keyword),
                    f"{keyword!r} is not matched by any pattern",
                )

    def test_the_state_namespaces_are_scoped(self):
        """`Variables`, `Lists`, `Clone` and `Stage` really compile now."""
        for name in ("Variables", "Lists", "Clone", "Stage"):
            with self.subTest(namespace=name):
                self.assertIn(
                    "support.class.builtin.jsw", scopes_for(f"{name}.get(1)", name)
                )

    def test_every_builtin_namespace_is_scoped(self):
        for name in sorted(BUILTIN_NAMESPACES):
            with self.subTest(namespace=name):
                self.assertIn(
                    "support.class.builtin.jsw",
                    scopes_for(f"{name}.move(1)", name),
                )

    def test_reserved_words_are_flagged(self):
        # ``in`` is the range-for separator, so it is real syntax now.
        for word in sorted(RESERVED - {"import", "in"}):
            if word in ALL_KEYWORDS and word in {"public", "private", "static", "final", "void", "true", "false", "null"}:
                continue
            with self.subTest(word=word):
                self.assertIn(
                    "invalid.illegal.reserved.jsw",
                    scopes_for(f"x = {word};", word),
                )



class OrderingTests(unittest.TestCase):
    """Rules are tried in order, so the specific one has to come first."""

    def test_class_and_void_names_are_named(self):
        self.assertIn(
            "entity.name.type.class.jsw",
            scanned_scopes("class main {", "main"),
            "the keyword rule swallowed `class` before #declarations could see it",
        )
        self.assertIn(
            "entity.name.function.jsw",
            scanned_scopes("public void greenFlag() {", "greenFlag"),
        )

    def test_numbers_beat_identifiers(self):
        self.assertEqual(scanned_scopes("x = 0b1010;", "0b1010"), ["constant.numeric.binary.jsw"])
        self.assertEqual(scanned_scopes("x = 0o17;", "0o17"), ["constant.numeric.octal.jsw"])
        self.assertEqual(scanned_scopes("x = .5;", ".5"), ["constant.numeric.jsw"])

    def test_keywords_and_builtins(self):
        self.assertEqual(scanned_scopes("if (a and b) {", "if"), ["keyword.control.jsw"])
        self.assertEqual(scanned_scopes("if (a and b) {", "and"), ["keyword.operator.word.jsw"])
        self.assertEqual(scanned_scopes('log("x");', "log"), ["support.function.builtin.jsw"])
        self.assertEqual(scanned_scopes("Pen.move(1);", "Pen"), ["support.class.builtin.jsw"])

    def test_directive_and_import_win_over_the_plain_text(self):
        scopes = scanned_scopes("#enable_feature<useTempVariables>;", "#enable_feature")
        self.assertIn("meta.preprocessor.jsw", scopes)
        scopes = scanned_scopes("#enable_feature<useTempVariables>;", "useTempVariables")
        self.assertIn("constant.language.feature.jsw", scopes)
        self.assertIn("keyword.control.import.jsw", scanned_scopes('import "a.jsa" as a;', "import"))

    def test_annotations_are_scoped_inside_the_comment(self):
        self.assertIn(
            "keyword.control.directive.jsw",
            scanned_scopes('// receiver at 10,20 "my message"', "receiver at"),
            "the comment rule must keep its inner patterns",
        )
        self.assertIn("keyword.control.directive.jsw", scanned_scopes("// proccode a %s", "proccode"))

class ConstructTests(unittest.TestCase):
    CASES = [
        ("#enable_feature<useTempVariables>;", "#enable_feature", "keyword.other.directive.jsw"),
        ("#enable_feature<useTempVariables>;", "useTempVariables", "constant.language.feature.jsw"),
        ("#enable_feature<notAFeature>;", "notAFeature", "entity.name.tag.jsw"),
        ("class main {", "main", "entity.name.type.class.jsw"),
        ("    public void greenFlag() {", "greenFlag", "entity.name.function.jsw"),
        ("private x = 1;", "private", "storage.modifier.jsw"),
        ("void run() {", "void", "storage.type.jsw"),
        ("if (a and b) {", "if", "keyword.control.jsw"),
        ("if (a and b) {", "and", "keyword.operator.word.jsw"),
        ("loose { // at 1,2", "loose", "keyword.control.jsw"),
        ("x = true;", "true", "constant.language.jsw"),
        ("x = 0x1F;", "0x1F", "constant.numeric.hex.jsw"),
        ("x = 0b1011;", "0b1011", "constant.numeric.binary.jsw"),
        ("x = 0o17;", "0o17", "constant.numeric.octal.jsw"),
        ("x = 1.5e3;", "1.5e3", "constant.numeric.jsw"),
        ('import "a.jsa" as a;', "import", "keyword.control.import.jsw"),
        ('import "a.jsa" as a;', "a", "entity.name.namespace.jsw"),
        # The decompiler writes the raw target name, so aliases are often quoted.
        ('import "a.jsa" as "img loader";', '"a.jsa"', "string.quoted.jsw"),
        ('import "a.jsa" as "img loader";', "as", "keyword.control.import.jsw"),
        ('import "a.jsa" as "img loader";', '"img loader"', "entity.name.namespace.jsw"),
        ('Empty: name="Empty", x=36;', "Empty", "entity.name.type.class.jsw"),
        ('log("hi");', "log", "support.function.builtin.jsw"),
        ("block(1);", "block", "support.function.builtin.jsw"),
        ("Pen.move(1);", "Pen", "support.class.builtin.jsw"),
        ("a && b;", "&&", "keyword.operator.logical.jsw"),
        ("a || b;", "||", "keyword.operator.logical.jsw"),
        ("a != b;", "!=", "keyword.operator.comparison.jsw"),
        ("a += 1;", "+=", "keyword.operator.assignment.jsw"),
        ("x = a ? b : c;", "?", "keyword.operator.ternary.jsw"),
        ("for i in 0..3 {", "for", "keyword.control.jsw"),
        ("for i in 0..3 {", "i", "variable.other.loop.jsw"),
        ("for i in 0..3 {", "in", "keyword.control.jsw"),
        ("for i in 0..3 {", "..", "keyword.operator.range.jsw"),
        ("for Synth.counter in 1..n {", "Synth.counter", "variable.other.loop.jsw"),
        # micro:bit: the namespace and its menu constants
        ('microbit.displayText("hi");', "microbit", "support.class.builtin.jsw"),
        ('microbit.displayText("hi");', "displayText", "entity.name.function.call.jsw"),
        ("microbit.isButtonPressed(microbit.buttonA);", "buttonA",
         "constant.language.microbit.jsw"),
        ("Looks.say(microbit.tiltAngle(microbit.front));", "front",
         "constant.language.microbit.jsw"),
        # target property keys
        ("stage: costume=0, volume=100;", "costume", "keyword.other.target-key.jsw"),
        ("stage: costume=0, volume=100;", "volume", "keyword.other.target-key.jsw"),
        ('Empty: name="Empty", x=36;', "name", "keyword.other.target-key.jsw"),
        ('Empty: name="Empty", x=36;', "x", "keyword.other.target-key.jsw"),
        ('class Lyrics(namespace="Lyrics") {', "namespace", "keyword.other.target-key.jsw"),
        # metadata
        ("metadata list Stage.x at 0,0 0x0 hidden;", "list", "constant.language.monitor-mode.jsw"),
        ("metadata large Stage.x at 0,0 0x0 hidden;", "large", "constant.language.monitor-mode.jsw"),
        ("metadata list Stage.x at 0,0 0x0 hidden;", "at", "keyword.other.metadata.jsw"),
        ("metadata list Stage.x at 0,0 0x0 hidden;", "hidden", "keyword.other.metadata.jsw"),
        ("metadata Stage.tick pos 1,2;", "pos", "keyword.other.metadata.jsw"),
        ("metadata list Stage.Lyrics.zh at 5,5 0x0 hidden;", "Stage.Lyrics.zh",
         "variable.other.member.jsw"),
        # variables
        ('    public c_en = "hi";', "c_en", "variable.other.readwrite.jsw"),
        # calls: bare custom block, underscore-prefixed, extension method
        ("        playNote(60);", "playNote", "entity.name.function.call.jsw"),
        ('        LogStatic("x");', "LogStatic", "entity.name.function.call.jsw"),
        ("        _q5(320, 75);", "_q5", "entity.name.function.call.jsw"),
        ("        Pen.setPenSize(2);", "setPenSize", "entity.name.function.call.jsw"),
        ('        Looks.say(join("a", "b"));', "join", "support.function.builtin.jsw"),
        ("    public void playNote(n) {", "playNote", "entity.name.function.jsw"),
        ("    private _i;", "_i", "variable.other.temp.jsw"),
        ("    public zh = [];", "zh", "variable.other.readwrite.jsw"),
        ("    public void start() {", "start", "entity.name.function.jsw"),
        ("    private _i;", "_i", "variable.other.temp.jsw"),
        # asset annotations
        ('import "a.svg" as a; // costume centerx=0 res=1', "costume",
         "keyword.other.annotation.jsw"),
        ('import "a.svg" as a; // costume centerx=0 res=1', "centerx",
         "variable.parameter.annotation.jsw"),
        ('import "a.wav" as b; // sound rate=48000 samples=8225', "sound",
         "keyword.other.annotation.jsw"),
        ('import "a.wav" as b; // sound rate=48000 samples=8225', "samples",
         "variable.parameter.annotation.jsw"),
        ('x = f"{a}";', 'f"{a}"', "string.interpolated.jsw"),
        ("x = 'hi';", "'hi'", "string.quoted.single.jsw"),
    ]

    def test_constructs_have_scopes(self):
        for context, token, scope in self.CASES:
            with self.subTest(token=token, scope=scope):
                self.assertIn(scope, scopes_for(context, token))

    def test_decompiler_annotations_are_highlighted(self):
        for line, word in (
            ("// proccode move camera %s %s", "proccode"),
            ('// receiver at 10,20 "my message"', "receiver at"),
            ("// monitor Stage.score default at 0,0 0x0 shown", "monitor"),
            ("// watch Player.notes list", "watch"),
            ("// comment Stage at 1,2 3x4 :", "comment"),
            ("// warp", "warp"),
            ("// Sprite: main", "Sprite:"),
            ("// at 10,20", "at"),
        ):
            with self.subTest(word=word):
                self.assertIn("keyword.control.directive.jsw", scopes_for(line, word))


class LexerCoverageTests(unittest.TestCase):
    """Every keyword and number the lexer emits must be spellable in the grammar."""

    def test_sample_sources(self):
        sources = [ROOT / "sample.jsw"]
        mast3d = ROOT / "projects" / "TheMast3D.jsw"
        if mast3d.exists():
            sources.append(mast3d)
        for path in sources:
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            for token in tokenize(text).tokens:
                kind = str(token.kind)
                if "KEYWORD" not in kind and "NUMBER" not in kind:
                    continue
                line = lines[token.line - 1] if token.line - 1 < len(lines) else ""
                with self.subTest(file=path.name, token=token.value):
                    self.assertTrue(
                        scopes_for(line, token.value),
                        f"{token.value!r} in {path.name}:{token.line} has no scope",
                    )


class SnippetTests(unittest.TestCase):
    """Snippets are inserted into real code, so they must be valid there."""

    def test_every_snippet_compiles_in_context(self):
        snippets = json.loads((ROOT / "vscode-jsw" / "snippets" / "jsw.json").read_text(encoding="utf-8"))
        self.assertTrue(snippets)
        for name, snippet in snippets.items():
            body = "\n".join(snippet["body"]).replace("\t", "    ")
            body = re.sub(r"\$\{\d+:([^}]*)\}", r"\1", body)
            body = re.sub(r"\$\{\d+\}", "", body)
            body = re.sub(r"\$\d+", "", body)
            if body.lstrip().startswith("#enable_feature"):
                feature = re.search(r"#enable_feature<([^>]*)>", body)
                with self.subTest(snippet=name):
                    self.assertIn(feature.group(1), FEATURES)
                continue
            wrappers = [
                body,
                f"class t {{ // Sprite: t (0,0) size=100 show=1\n{body}\n}}",
                f"class t {{ // Sprite: t (0,0) size=100 show=1\n    public void greenFlag() {{\n{body}\n    }}\n}}",
                # file-level snippets (target declarations) need a target below
                f"{body}\nclass t {{ // Sprite: t (0,0) size=100 show=1\n}}",
            ]
            with self.subTest(snippet=name):
                self.assertTrue(
                    any(self._parses(w) for w in wrappers),
                    f"{name} does not compile as a statement, member or fragment",
                )

    @staticmethod
    def _parses(text: str) -> bool:
        try:
            Parser(tokenize(text)).parse()
            return True
        except Exception:
            return False


if __name__ == "__main__":
    unittest.main()
