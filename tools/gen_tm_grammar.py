#!/usr/bin/env python3
"""Generate the VS Code TextMate grammar from ``jswc.keywords``.

Run ``python3 tools/gen_tm_grammar.py`` after changing the keyword tables; the
generated file is committed so the extension works without a build step.

Everything the lexer can produce has a scope here: keywords, the
``#enable_feature`` directive, ``import`` lines, the compiler's ``// proccode``
/ ``// monitor`` annotations, hex/binary/octal numbers, f-strings and the
builtin functions that are lowered to blocks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jswc.extension_blocks import MICROBIT_CONSTANTS  # noqa: E402
from jswc.extensions import known_members, known_namespaces  # noqa: E402
from jswc.keywords import (  # noqa: E402
    ALL_KEYWORDS,
    ASSET_COMMENT_KEYS,
    ASSET_COMMENT_WORDS,
    BUILTIN_NAMESPACES,
    FEATURES,
    KEYWORDS,
    LITERALS,
    METADATA_WORDS,
    MODIFIERS,
    MONITOR_MODES,
    RESERVED,
    TARGET_KEYS,
    TYPES,
)

GRAMMAR_PATH = ROOT / "vscode-jsw" / "syntaxes" / "jsw.tmLanguage.json"

CONTROL = {
    "if", "elif", "else", "for", "while", "repeat", "switch", "case",
    "default", "break", "continue", "return", "call", "loose",
}
WORD_OPERATORS = {"and", "or", "not"}
DECLARATIONS = KEYWORDS - CONTROL - WORD_OPERATORS - LITERALS - MODIFIERS - TYPES - {"import", "as"}

# ``log(x)``, ``join("a", b)`` ... - lowered to blocks by the compiler.
BUILTIN_FUNCTIONS = (
    "log", "warn", "error", "startTimer", "stopTimer", "breakpoint",
    "join", "string", "len", "length", "lengthOf", "float",
    "abs", "floor", "ceil", "ceiling", "sqrt", "sin", "cos", "tan",
    "asin", "acos", "atan", "ln", "log10", "exp", "pow10",
    "isTurboWarp", "isCompiled", "menu", "block",
)

# The comments the decompiler writes: ``// proccode``, ``// monitor``, ...
ANNOTATION_WORDS = (
    "Sprite:", "Stage", "hook:", "proccode", "receiver at", "monitor", "watch",
    "comment", "warp", "main thread", "grouped", "at",
)


def _annotation_re(words) -> str:
    """Like ``_word_re`` but labels ending in punctuation (``Sprite:``)."""
    parts = []
    for word in sorted(words, key=lambda w: (-len(w), w)):
        if word[-1:].isalnum():
            parts.append(rf"\b{word}\b")
        else:
            parts.append(rf"\b{word}")
    return "(?:" + "|".join(parts) + ")"


def _word_re(words) -> str:
    # Sorted by length then alphabetically: frozenset order is not stable
    # across interpreter runs (hash randomisation), and the grammar is committed.
    return r"\b(?:" + "|".join(sorted(words, key=lambda w: (-len(w), w))) + r")\b"


def build_grammar() -> dict:
    return {
        "$schema": "https://raw.githubusercontent.com/martinring/tmlanguage/master/tmlanguage.json",
        "name": "JadenScript",
        "scopeName": "source.jsw",
        # Order matters: TextMate tries these in turn at every position, so a
        # rule that needs ``class``/``void`` to still be there must run before
        # the bare keyword rule swallows the word.
        "patterns": [
            {"include": "#directives"},
            {"include": "#comments"},
            {"include": "#strings"},
            {"include": "#imports"},
            {"include": "#target"},
            {"include": "#metadata"},
            {"include": "#declarations"},
            {"include": "#keywords"},
            {"include": "#numbers"},
            {"include": "#calls"},
            {"include": "#namespaces"},
            {"include": "#variables"},
            {"include": "#operators"},
            {"include": "#punctuation"},
        ],
        "repository": {
            "directives": {
                "comment": "#enable_feature<name>; - the only compiler directive.",
                "patterns": [
                    {
                        "name": "meta.preprocessor.jsw",
                        "begin": r"^(\s*)(#enable_feature)",
                        "beginCaptures": {
                            "1": {"name": "punctuation.whitespace.leading.jsw"},
                            "2": {"name": "keyword.other.directive.jsw"},
                        },
                        "end": r";",
                        "endCaptures": {"0": {"name": "punctuation.terminator.jsw"}},
                        "patterns": [
                            {"name": "punctuation.definition.tag.begin.jsw", "match": r"<"},
                            {"name": "punctuation.definition.tag.end.jsw", "match": r">"},
                            {"name": "constant.language.feature.jsw", "match": _word_re(FEATURES)},
                            {"name": "entity.name.tag.jsw", "match": r"[A-Za-z_][A-Za-z0-9_]*"},
                        ],
                    }
                ],
            },
            "comments": {
                "patterns": [
                    {
                        "comment": "A decompiler annotation: the words carry meaning, so they get their own scope.",
                        "name": "comment.line.double-slash.jsw",
                        "begin": r"//",
                        "end": r"$",
                        "patterns": [
                            {
                                "name": "string.quoted.double.jsw",
                                "begin": '"',
                                "end": '"',
                                "patterns": [{"name": "constant.character.escape.jsw", "match": r"\\."}],
                            },
                            {
                                "name": "keyword.control.directive.jsw",
                                "match": _annotation_re(ANNOTATION_WORDS),
                            },
                            {
                                "comment": "// costume … / // sound …",
                                "name": "keyword.other.annotation.jsw",
                                "match": _word_re(ASSET_COMMENT_WORDS),
                            },
                            {
                                "comment": "centerx= centery= res= rate= samples=",
                                "name": "variable.parameter.annotation.jsw",
                                "match": _word_re(ASSET_COMMENT_KEYS) + r"(?=\s*=)",
                            },
                            {
                                "name": "entity.name.tag.jsw",
                                "match": r"@[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*",
                            },
                            {"name": "constant.numeric.jsw", "match": r"\b\d+(?:\.\d+)?\b"},
                        ],
                    },
                    {
                        "name": "comment.block.jsw",
                        "begin": r"/\*",
                        "end": r"\*/",
                    },
                ]
            },
            "strings": {
                "patterns": [
                    {
                        # The end is a back-reference to the opening quote, and
                        # a hole is a nested context, so `f"{a} {“x”}"` keeps its
                        # inner strings instead of ending at the first quote.
                        "comment": "f-strings may contain {expression} holes",
                        "name": "string.interpolated.jsw",
                        "begin": r"\bf(\"|')",
                        "end": r"\1",
                        "patterns": [
                            {"name": "constant.character.escape.jsw", "match": r"\{\{"},
                            {"name": "constant.character.escape.jsw", "match": r"\}\}"},
                            {
                                "name": "meta.embedded.expression.jsw",
                                "begin": r"\{",
                                "beginCaptures": {
                                    "0": {"name": "punctuation.section.embedded.begin.jsw"}
                                },
                                "end": r"\}",
                                "endCaptures": {
                                    "0": {"name": "punctuation.section.embedded.end.jsw"}
                                },
                                "patterns": [
                                    {"include": "#strings"},
                                    {"include": "#numbers"},
                                    {"include": "#namespaces"},
                                    {"include": "#variables"},
                                ],
                            },
                            {"name": "constant.character.escape.jsw", "match": r"\\."},
                        ],
                    },
                    {
                        "name": "string.quoted.double.jsw",
                        "begin": '"',
                        "end": '"',
                        "patterns": [
                            {"name": "constant.character.escape.jsw", "match": r"\\."},
                        ],
                    },
                    {
                        "name": "string.quoted.single.jsw",
                        "begin": "'",
                        "end": "'",
                        "patterns": [
                            {"name": "constant.character.escape.jsw", "match": r"\\."},
                        ],
                    },
                ]
            },
            "imports": {
                "comment": "import \"assets/lists/notes.jsa\" as notes;",
                "patterns": [
                    {
                        # The decompiler writes the raw target name, so the alias may
                        # be quoted: import "assets/x.svg" as "img loader";
                        "match": r"""\b(import)\s+("[^"]*"|'[^']*')(?:\s+(as)\s+("[^"]*"|'[^']*'|[A-Za-z_$][\w$]*))?""",
                        "captures": {
                            "1": {"name": "keyword.control.import.jsw"},
                            "2": {"name": "string.quoted.jsw"},
                            "3": {"name": "keyword.control.import.jsw"},
                            "4": {"name": "entity.name.namespace.jsw"},
                        },
                    }
                ],
            },
            "keywords": {
                "patterns": [
                    # ``import`` and ``as`` are keywords even when no path follows
                    # (``import`` alone, a half-written line, ``as`` in a paste):
                    # the ``#imports`` rule above owns the whole line only when it
                    # can see the quoted path.
                    {"name": "keyword.control.import.jsw", "match": _word_re({"import", "as"})},
                    {"name": "storage.modifier.jsw", "match": _word_re(MODIFIERS)},
                    {"name": "storage.type.jsw", "match": _word_re(TYPES)},
                    {"name": "keyword.control.jsw", "match": _word_re(CONTROL)},
                    {"name": "keyword.operator.word.jsw", "match": _word_re(WORD_OPERATORS)},
                    {"name": "constant.language.jsw", "match": _word_re(LITERALS)},
                    {"name": "keyword.declaration.jsw", "match": _word_re(DECLARATIONS)},
                    {
                        "comment": "Reserved for future versions; flagged so users avoid them.",
                        "name": "invalid.illegal.reserved.jsw",
                        # ``in`` is the range-for separator, not a future keyword.
                        "match": _word_re(RESERVED - MODIFIERS - TYPES - LITERALS - KEYWORDS - {"in"}),
                    },
                ]
            },
            "declarations": {
                "patterns": [
                    {
                        "comment": "class Lyrics(namespace=\"Lyrics\") — the same keys inline.",
                        "name": "meta.class-parameters.jsw",
                        "begin": r"\b(class)\s+([A-Za-z_$][\w$]*)\s*(\()",
                        "beginCaptures": {
                            "1": {"name": "keyword.declaration.class.jsw"},
                            "2": {"name": "entity.name.type.class.jsw"},
                            "3": {"name": "punctuation.section.parens.jsw"},
                        },
                        "end": r"\)",
                        "endCaptures": {"0": {"name": "punctuation.section.parens.jsw"}},
                        "patterns": [
                            {
                                "name": "keyword.other.target-key.jsw",
                                "match": _word_re(TARGET_KEYS) + r"(?=\s*=)",
                            },
                            {
                                "name": "string.quoted.double.jsw",
                                "begin": '"',
                                "end": '"',
                            },
                            {"name": "keyword.operator.assignment.jsw", "match": "="},
                        ],
                    },
                    {
                        "comment": "class Name / stage",
                        "match": r"\b(class)\s+([A-Za-z_$][\w$]*)|\b(stage)\b",
                        "captures": {
                            "1": {"name": "keyword.declaration.class.jsw"},
                            "2": {"name": "entity.name.type.class.jsw"},
                            "3": {"name": "keyword.declaration.stage.jsw"},
                        },
                    },
                    {
                        "comment": "Empty: name=\"Empty\", x=36, y=28, ... - a target declaration.",
                        "match": r"^(\s*)([A-Za-z_$][\w$]*)\s*(?=:\s*name=)",
                        "captures": {
                            "1": {"name": "punctuation.whitespace.leading.jsw"},
                            "2": {"name": "entity.name.type.class.jsw"},
                        },
                    },
                    {
                        "comment": "public c_en = …; — the declaration names the state.",
                        "match": r"\b(public|private)\s+(_[A-Za-z0-9_$][\w$]*)\s*(?=[=;])",
                        "captures": {
                            "1": {"name": "storage.modifier.jsw"},
                            "2": {"name": "variable.other.temp.jsw"},
                        },
                    },
                    {
                        "match": r"\b(public|private)\s+(?!void\b)([A-Za-z$][\w$]*)\s*(?=[=;\[])",
                        "captures": {
                            "1": {"name": "storage.modifier.jsw"},
                            "2": {"name": "variable.other.readwrite.jsw"},
                        },
                    },
                    {
                        "comment": "for i in a..b — the inclusive counter form.",
                        "match": r"\b(for)\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s+(in)\b",
                        "captures": {
                            "1": {"name": "keyword.control.jsw"},
                            "2": {"name": "variable.other.loop.jsw"},
                            "3": {"name": "keyword.control.jsw"},
                        },
                    },
                    {
                        "comment": "void Namespace.name(...) / void name(...)",
                        "match": r"\b(void)\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)",
                        "captures": {
                            "1": {"name": "storage.type.jsw"},
                            "2": {"name": "entity.name.function.jsw"},
                        },
                    },
                ]
            },
            "metadata": {
                "comment": "metadata list Stage.x at 0,0 100x24 hidden; - the mode words and keys are syntax.",
                "patterns": [
                    {
                        "name": "meta.metadata.jsw",
                        "begin": r"^(\s*)(metadata)\b",
                        "beginCaptures": {
                            "1": {"name": "punctuation.whitespace.leading.jsw"},
                            "2": {"name": "keyword.declaration.jsw"},
                        },
                        "end": r";|$",
                        "endCaptures": {"0": {"name": "punctuation.terminator.jsw"}},
                        "patterns": [
                            {
                                "comment": "list / large / slider / normal / default",
                                "name": "constant.language.monitor-mode.jsw",
                                "match": _word_re(MONITOR_MODES),
                            },
                            {
                                "comment": "pos / at / hidden / shown / range / watchers",
                                "name": "keyword.other.metadata.jsw",
                                "match": _word_re(METADATA_WORDS),
                            },
                            {
                                "comment": "Stage.Lyrics.zh - the annotated target.",
                                "name": "variable.other.member.jsw",
                                "match": r"\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+",
                            },
                            {
                                "name": "string.quoted.double.jsw",
                                "begin": '"',
                                "end": '"',
                                "patterns": [{"name": "constant.character.escape.jsw", "match": r"\\."}],
                            },
                            {"name": "constant.numeric.jsw", "match": r"\b\d+(?:\.\d+)?\b"},
                        ],
                    }
                ],
            },
            "target": {
                "comment": "stage: costume=0, volume=100, …; and Sprite: name=\"x\", x=0, …;",
                "patterns": [
                    {
                        "name": "meta.target-declaration.jsw",
                        "begin": r"^(\s*)(?:(stage)|([A-Za-z_$][\w$]*))\s*(:)(?=\s*[A-Za-z_$][\w$]*\s*=)",
                        "beginCaptures": {
                            "1": {"name": "punctuation.whitespace.leading.jsw"},
                            "2": {"name": "keyword.declaration.stage.jsw"},
                            "3": {"name": "entity.name.type.class.jsw"},
                            "4": {"name": "punctuation.separator.jsw"},
                        },
                        "end": r";|$",
                        "endCaptures": {"0": {"name": "punctuation.terminator.jsw"}},
                        "patterns": [
                            {
                                "comment": "costume / volume / layer / tempo / video …",
                                "name": "keyword.other.target-key.jsw",
                                "match": _word_re(TARGET_KEYS) + r"(?=\s*=)",
                            },
                            {
                                "name": "string.quoted.double.jsw",
                                "begin": '"',
                                "end": '"',
                                "patterns": [{"name": "constant.character.escape.jsw", "match": r"\\."}],
                            },
                            {"name": "constant.numeric.jsw", "match": r"\b\d+(?:\.\d+)?\b"},
                            {"name": "keyword.operator.assignment.jsw", "match": "="},
                            {"name": "punctuation.separator.jsw", "match": ","},
                        ],
                    }
                ],
            },
            "variables": {
                "comment": "A temp variable (_x) is a different kind of thing from sprite state.",
                "patterns": [
                    {
                        "name": "variable.other.temp.jsw",
                        "match": r"\b_[A-Za-z0-9_$][\w$]*\b",
                    },
                    {
                        "comment": "A bare identifier: not a call, not a member, "
                                   "but a list name before ``[`` counts.",
                        # Oniguruma nests character classes, so this is an
                        # alternation rather than ``[([.]``.
                        "name": "variable.other.jsw",
                        "match": r"\b[A-Za-z_$][\w$]*\b(?!\s*(?:\(|\.))",
                    },
                ],
            },
            "numbers": {
                "patterns": [
                    {"name": "constant.numeric.hex.jsw", "match": r"\b0[xX][0-9a-fA-F]+\b"},
                    {"name": "constant.numeric.binary.jsw", "match": r"\b0[bB][01]+\b"},
                    {"name": "constant.numeric.octal.jsw", "match": r"\b0[oO][0-7]+\b"},
                    {
                        "comment": "352x212 — a monitor size, scoped either side of the x.",
                        "name": "constant.numeric.jsw",
                        "match": r"\b\d+(?:\.\d+)?(?=[x×]\d)",
                    },
                    {
                        "name": "constant.numeric.jsw",
                        "match": r"(?<=[x×])\d+(?:\.\d+)?\b",
                    },
                    {
                        "name": "constant.numeric.jsw",
                        "match": r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b",
                    },
                    {
                        "comment": ".5 — a bare leading dot, also as the end of ``0.05..1``.",
                        "name": "constant.numeric.jsw",
                        "match": r"(?<![\w])\.\d+(?:[eE][+-]?\d+)?\b",
                    },
                ]
            },
            "calls": {
                "patterns": [
                    {
                        "comment": "log/warn/error/join/string/block ... - the compiler's builtins.",
                        "name": "support.function.builtin.jsw",
                        "match": _word_re(BUILTIN_FUNCTIONS) + r"(?=\s*\()",
                    },
                    {
                        # A custom block call — ``playNote(60)``, ``LogStatic("x")``,
                        # ``_q5(1, 2)``. Keywords are consumed by ``#keywords``
                        # and declarations by ``#declarations``, both earlier, so
                        # anything still followed by ``(`` is a call.
                        "name": "entity.name.function.call.jsw",
                        "match": r"\b[A-Za-z_$][\w$]*(?=\s*\()",
                    },
                ]
            },
            "namespaces": {
                "patterns": [
                    {
                        # ``WebSocket.closingCode`` and ``Pointerlock.setPointerLock``:
                        # a known member of a known extension namespace, with or
                        # without a call's parentheses.
                        "comment": "A known extension member: WebSocket.closingCode, Pointerlock.setPointerLock",
                        "match": r"(\b(?:"
                        + "|".join(sorted(known_namespaces()))
                        + r")\s*\.\s*)((?:"
                        + "|".join(sorted(
                            (name for name in known_members() if "_" not in name),
                            key=lambda name: (-len(name), name),   # total order: the
                        ))                                        # set has no order
                        + r")\b)",
                        "captures": {
                            "1": {"name": "support.class.builtin.jsw"},
                            "2": {"name": "support.function.extension.jsw"},
                        },
                    },
                    {
                        "comment": "Known runtime namespace: Motion, Pen, Music, microbit, ...",
                        "name": "support.class.builtin.jsw",
                        "match": _word_re(BUILTIN_NAMESPACES | known_namespaces())
                        + r"(?=\s*\.)",
                    },
                    {
                        "comment": "Any capitalised identifier used as a namespace.",
                        "name": "support.class.jsw",
                        "match": r"\b[A-Z][A-Za-z0-9_]*\b(?=\s*\.)",
                    },
                    {
                        # microbit.buttonA, microbit.front — a menu value, not a
                        # property of an object.
                        "name": "constant.language.microbit.jsw",
                        "match": r"(?<=\.)(?:" + "|".join(sorted(MICROBIT_CONSTANTS)) + r")\b",
                    },
                    {
                        "comment": "Method or property after a dot.",
                        "name": "entity.name.function.call.jsw",
                        "match": r"(?<=\.)[A-Za-z_$][\w$]*(?=\s*\()",
                    },
                    {
                        "name": "variable.other.property.jsw",
                        "match": r"(?<=\.)[A-Za-z_$][\w$]*",
                    },
                ]
            },
            "operators": {
                "patterns": [
                    {"name": "keyword.operator.assignment.jsw", "match": r"(\+=|-=|\*=|/=|%=|=)"},
                    {"name": "keyword.operator.comparison.jsw", "match": r"(==|!=|<=|>=|<|>)"},
                    {"name": "keyword.operator.logical.jsw", "match": r"(&&|\|\||!)"},
                    {"name": "keyword.operator.arithmetic.jsw", "match": r"(\*\*|\+|-|\*|/|%|\+\+|--)"},
                    {"name": "keyword.operator.range.jsw", "match": r"\.\."},
                    {"name": "keyword.operator.ternary.jsw", "match": r"\?"},
                ]
            },
            "punctuation": {
                "patterns": [
                    {"name": "punctuation.terminator.jsw", "match": ";"},
                    {"name": "punctuation.separator.jsw", "match": r"[,:]"},
                    {"name": "punctuation.section.braces.jsw", "match": r"[{}]"},
                    {"name": "punctuation.section.brackets.jsw", "match": r"[\[\]]"},
                    {"name": "punctuation.section.parens.jsw", "match": r"[()]"},
                ]
            },
        },
    }


def main() -> int:
    grammar = build_grammar()
    GRAMMAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRAMMAR_PATH.write_text(json.dumps(grammar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    missing = [kw for kw in sorted(ALL_KEYWORDS) if kw not in json.dumps(grammar)]
    if missing:  # pragma: no cover - guards the generator itself
        print(f"keywords missing from grammar: {missing}", file=sys.stderr)
        return 1
    print(f"wrote {GRAMMAR_PATH.relative_to(ROOT)} ({len(ALL_KEYWORDS)} keywords)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
