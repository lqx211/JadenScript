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
from jswc.extensions import highlight_members, highlight_namespaces  # noqa: E402
from jswc.keywords import (  # noqa: E402
    ALL_KEYWORDS,
    ASSET_COMMENT_KEYS,
    ASSET_COMMENT_WORDS,
    BUILTIN_NAMESPACES,
    FEATURES,
    FLAG_VALUES,
    HOOK_MENUS,
    PROCCODE_PLACEHOLDERS,
    KEYWORDS,
    LITERALS,
    METADATA_ANNOTATIONS,
    METADATA_FLAGS,
    METADATA_WORDS,
    MODIFIERS,
    MONITOR_MODES,
    RESERVED,
    ROTATION_VALUES,
    TARGET_KEYS,
    TYPES,
    VIDEO_VALUES,
)

GRAMMAR_PATH = ROOT / "vscode-jsw" / "syntaxes" / "jsw.tmLanguage.json"

CONTROL = {
    "if", "elif", "else", "for", "while", "repeat", "switch", "case",
    "default", "break", "continue", "return", "call",
}
WORD_OPERATORS = {"and", "or", "not"}
DECLARATIONS = KEYWORDS - CONTROL - WORD_OPERATORS - LITERALS - MODIFIERS - TYPES - {"import", "include", "as"}

# ``log(x)``, ``join("a", b)`` ... - lowered to blocks by the compiler.
BUILTIN_FUNCTIONS = (
    "log", "warn", "error", "startTimer", "stopTimer", "breakpoint",
    "join", "string", "len", "length", "lengthOf", "float",
    "abs", "floor", "ceil", "ceiling", "sqrt", "sin", "cos", "tan",
    "asin", "acos", "atan", "ln", "log10", "exp", "pow10",
    "isTurboWarp", "isCompiled", "menu", "block",
)

# The comments the decompiler writes: ``// proccode``, ``// monitor``, ...
# ``unsupported``/``undefined`` mark a block the rebuild could not carry, and
# ``callee``/``temp variables`` mark a repaired call and the thread-variable
# build, so all of them are worth a different colour than prose.
ANNOTATION_WORDS = (
    "Sprite:", "Stage", "hook:", "proccode", "receiver at", "monitor", "watch",
    "comment", "warp", "main thread", "grouped", "at",
    "unsupported", "undefined", "callee", "temp variables",
)


#: A quoted string in *match* form (not a region), so escapes have to be part of
#: the pattern. ``import "a.svg" as "\"";`` is a real alias: the decompiler
#: writes the sprite's own name, and that may be a quote. Without the escape
#: branch the trailing quote opened a string region that ran to the next quote
#: in the file, painting everything between the two as a string.
QUOTED = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''


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


def _target_value_rules() -> list:
    """``rotation=all``, ``video=on``, ``show=1`` — the value of a target key.

    These are matched as a ``key=value`` pair rather than as keywords: ``all``
    is a fine variable name and ``on`` is a fine word, so the value only reads
    as a value in that one position.
    """

    def pair(key: str, values, comment: str, scope: str) -> dict:
        # The whole ``key=value`` is one match, so the ``=`` has to be captured
        # too: a token the rule matches but does not capture keeps the enclosing
        # region's scope, which made `video=on` the only line where `=` was not
        # an assignment.
        return {
            "comment": comment,
            "match": rf"\b({key})\s*(=)\s*(" + _word_re(values) + ")",
            "captures": {
                "1": {"name": "keyword.other.target-key.jsw"},
                "2": {"name": "keyword.operator.assignment.jsw"},
                "3": {"name": scope},
            },
        }

    return [
        pair("rotation", ROTATION_VALUES, "rotation=all|leftright|none",
             "constant.language.rotation-style.jsw"),
        pair("video(?:State)?", VIDEO_VALUES, "video=on|off|on-flipped",
             "constant.language.video-state.jsw"),
        pair("show|draggable", FLAG_VALUES, "show=0|1, draggable=0|1",
             "constant.language.metadata-flag.jsw"),
    ]


def _sprite_comment_rule() -> dict:
    """``// Sprite: name (12,34) size=100 ns=Np`` — the comment spelling.

    The decompiler writes target metadata as a comment, so the keys and their
    values have to read the same there as on a ``name: …;`` line. Only lines
    that open with the tag get this rule; prose stays prose.
    """
    return {
        "name": "comment.line.double-slash.jsw",
        # ``Sprite:`` ends in punctuation, so ``\b`` would never match after it.
        "begin": r"//\s*(?=(?:Sprite:|Stage:))",
        "end": r"$",
        "patterns": [
            {
                "name": "keyword.control.directive.jsw",
                "match": _annotation_re({"Sprite:", "Stage"}),
            },
            *_target_value_rules(),
            {
                "name": "keyword.other.target-key.jsw",
                "match": _word_re(TARGET_KEYS) + r"(?=\s*=)",
            },
            {"name": "constant.numeric.jsw", "match": r"-?\b\d+(?:\.\d+)?\b"},
            {"name": "keyword.operator.assignment.jsw", "match": "="},
            {"name": "punctuation.separator.jsw", "match": r"[,:]"},
        ],
    }


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
            # Before #strings: the key argument of a keySim block is a quoted
            # string, but it is a key name, not prose.
            {"include": "#keysim"},
            {"include": "#strings"},
            {"include": "#includes"},
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
                "comment": "#enable_feature<name>; and the #ifdef family, which "
                           "the preprocessor resolves before lexing.",
                "patterns": [
                    {
                        "comment": "#ifdef NAME / #ifndef NAME / #define NAME",
                        "name": "meta.preprocessor.jsw",
                        "match": r"^(\s*)(#(?:ifdef|ifndef|define))\b\s*([A-Za-z_]\w*)?",
                        "captures": {
                            "1": {"name": "punctuation.whitespace.leading.jsw"},
                            "2": {"name": "keyword.other.directive.jsw"},
                            "3": {"name": "entity.name.tag.jsw"},
                        },
                    },
                    {
                        # ``#else`` would otherwise be scoped by the ``else``
                        # keyword rule as plain control flow.
                        "comment": "#else / #endif",
                        "name": "meta.preprocessor.jsw",
                        "match": r"^(\s*)(#(?:else|endif))\b",
                        "captures": {
                            "1": {"name": "punctuation.whitespace.leading.jsw"},
                            "2": {"name": "keyword.other.directive.jsw"},
                        },
                    },
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
                    _sprite_comment_rule(),
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
                                # ``// hook: when [timer] > 10`` / ``when tilted [front]``
                                "comment": "the bracketed menu of a hook annotation",
                                "name": "constant.language.hook-menu.jsw",
                                "match": r"(?<=\[)(?:" + "|".join(sorted(
                                    HOOK_MENUS, key=lambda w: (-len(w), w))) + r")(?=\])",
                            },
                            {
                                # ``// proccode Decalage %s %s %s``
                                "comment": "the %-placeholders of a proccode",
                                "name": "variable.parameter.proccode.jsw",
                                "match": "|".join(sorted(
                                    PROCCODE_PLACEHOLDERS, key=lambda w: (-len(w), w))
                                ).replace("%", r"\%"),
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
            "keysim": {
                "comment": "keySim.press(\"space\", 0.2) — the key argument is a "
                           "name the extension compares against KeyboardEvent.key.",
                "patterns": [
                    {
                        # Each part is captured so the namespace, the dot, the
                        # member and the paren read exactly as they do in every
                        # other call; only the key gets a scope of its own.
                        "match": (
                            r"(\b(keySim)(\s*\.\s*)(press|pressAndWait|hold|release)"
                            r"(\s*\(\s*))(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')"
                        ),
                        "captures": {
                            "2": {"name": "support.class.builtin.jsw"},
                            "3": {"name": "punctuation.accessor.jsw"},
                            "4": {"name": "support.function.extension.jsw"},
                            "5": {"name": "punctuation.section.parens.jsw"},
                            "6": {"name": "string.quoted.keysim-key.jsw"},
                        },
                    }
                ],
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
            "includes": {
                "comment": 'include "display.jsw" -- a module spliced in before lexing.',
                "patterns": [
                    {
                        # A whole line: the preprocessor replaces it with the file,
                        # so nothing else may share it. Quoted and single-quoted
                        # are both accepted, the way import accepts them.
                        "match": r"^(\s*)(include)\s+(" + QUOTED + r")",
                        "captures": {
                            "1": {"name": "punctuation.whitespace.leading.jsw"},
                            "2": {"name": "keyword.control.include.jsw"},
                            "3": {"name": "string.quoted.jsw"},
                        },
                    }
                ],
            },
            "imports": {
                "comment": "import \"assets/lists/notes.jsa\" as notes;",
                "patterns": [
                    {
                        # The decompiler writes the raw target name, so the alias may
                        # be quoted: import "assets/x.svg" as "img loader";
                        "match": r"\b(import)\s+(" + QUOTED + r")(?:\s+(as)\s+(" + QUOTED
                                 + r"|[A-Za-z_$][\w$]*))?",
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
                        # ``in`` is the range-for separator, not a future keyword,
                        # and ``l.delete(1)`` is a list method: a reserved word
                        # after a dot is a member name.
                        "match": r"(?<!\.)"
                        + _word_re(RESERVED - MODIFIERS - TYPES - LITERALS - KEYWORDS - {"in"}),
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
                        "match": r"\b(public|private|cloud)\s+(_[A-Za-z0-9_$][\w$]*)\s*(?=[=;])",
                        "captures": {
                            "1": {"name": "storage.modifier.jsw"},
                            "2": {"name": "variable.other.temp.jsw"},
                        },
                    },
                    {
                        "comment": "public b = 0; / cloud score = 0; — the declaration names the state.",
                        "match": r"\b(public|private|cloud)\s+(?!void\b)([A-Za-z$][\w$]*)\s*(?=[=;\[])",
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
                        "comment": "void Namespace.name(a, b) — the declaration, "
                                   "and the parameters inside the parens.",
                        "name": "meta.function-declaration.jsw",
                        "begin": r"\b(void)\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*(\()",
                        "beginCaptures": {
                            "1": {"name": "storage.type.jsw"},
                            "2": {"name": "entity.name.function.jsw"},
                            "3": {"name": "punctuation.section.parens.jsw"},
                        },
                        "end": r"\)",
                        "endCaptures": {"0": {"name": "punctuation.section.parens.jsw"}},
                        "patterns": [
                            # A parameter is a name in a scope of its own: it
                            # reads as a variable everywhere else, but a call's
                            # argument and a sprite variable are different
                            # things and the theme colours them apart.
                            {"name": "variable.parameter.jsw", "match": r"[A-Za-z_$][\w$]*"},
                            {"name": "punctuation.separator.jsw", "match": ","},
                        ],
                    },
                    {
                        "comment": "loose { … } - raw blocks the decompiler could "
                                   "not map. Not control flow: nothing is tested.",
                        "name": "keyword.other.loose.jsw",
                        "match": r"\b(loose)\b",
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
                                "comment": "monitor / watch / comment / note - the "
                                           "annotation that opens the line. The "
                                           "parser folds case here, so SLIDER "
                                           "compiles just like slider.",
                                "name": "keyword.other.metadata-annotation.jsw",
                                "match": r"(?i:" + _word_re(METADATA_ANNOTATIONS) + ")",
                            },
                            {
                                "comment": "list / large / slider / normal / default",
                                "name": "constant.language.monitor-mode.jsw",
                                "match": r"(?i:" + _word_re(MONITOR_MODES) + ")",
                            },
                            {
                                "comment": "shown / hidden / minimized - the monitor's "
                                           "state, not its layout.",
                                "name": "constant.language.metadata-flag.jsw",
                                "match": _word_re(METADATA_FLAGS),
                            },
                            {
                                "comment": "pos / at / range / watchers / discrete …",
                                "name": "keyword.other.metadata.jsw",
                                "match": _word_re(METADATA_WORDS),
                            },
                            {
                                "comment": "Stage.Lyrics.zh - the annotated target.",
                                "name": "variable.other.member.jsw",
                                "match": r"\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+",
                            },
                            {
                                # ``metadata note "obj loader".tip at …`` - the
                                # decompiler quotes a name with a space in it.
                                "comment": 'a quoted target name: "obj loader".tip',
                                "match": r'("[^"]*")(\.)([A-Za-z_$][\w$]*)',
                                "captures": {
                                    "1": {"name": "string.quoted.double.jsw"},
                                    "2": {"name": "punctuation.accessor.jsw"},
                                    # A part of the match that is not captured
                                    # keeps the region's scope, so the member
                                    # after the dot needs its own group.
                                    "3": {"name": "variable.other.member.jsw"},
                                },
                            },
                            {
                                # ``metadata note s2 at …`` / ``metadata note Stage at …``:
                                # a bare owner. It comes after the modes and words
                                # above, so ``list`` and ``shown`` keep their own
                                # colour and only a real name lands here.
                                "comment": "A bare sprite or class name: the thing the monitor watches.",
                                "name": "entity.name.type.class.jsw",
                                "match": r"(?<=\s)[A-Za-z_$][\w$]*(?=\s|$)",
                            },
                            {
                                "name": "string.quoted.double.jsw",
                                "begin": '"',
                                "end": '"',
                                "patterns": [{"name": "constant.character.escape.jsw", "match": r"\\."}],
                            },
                            {"name": "constant.numeric.jsw", "match": r"\b0[xX][0-9a-fA-F]+\b"},
                            # ``100x24`` - a monitor's size: the numbers either
                            # side, and the ``x`` between them, which otherwise
                            # reads as a variable called x.
                            {"name": "constant.numeric.jsw", "match": r"\b\d+(?:\.\d+)?(?=[x×])"},
                            {"name": "constant.numeric.jsw", "match": r"(?<=[x×])\d+(?:\.\d+)?\b"},
                            {
                                "name": "punctuation.separator.dimension.jsw",
                                "match": r"(?<=\d)[x×](?=\d)",
                            },
                            {"name": "constant.numeric.jsw", "match": r"\b\d+(?:\.\d+)?\b"},
                            {"name": "keyword.operator.range.jsw", "match": r"\.\."},
                            {"name": "keyword.operator.assignment.jsw", "match": "="},
                            {"name": "punctuation.section.parens.jsw", "match": r"[()]"},
                            {"name": "punctuation.separator.jsw", "match": r"[,:]"},
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
                            *_target_value_rules(),
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
                        # ``lu.clear()``, ``log.append(x)``, ``obj.field``: the
                        # receiver of a member access. Only capitalised names
                        # (``Motion.x``) and known extension namespaces
                        # (``gtm.load``) were claimed by #namespaces, which runs
                        # earlier; without this rule the receiver and its dot
                        # fell through unmatched and read as plain text.
                        "name": "variable.other.object.jsw",
                        "match": r"\b[A-Za-z_$][\w$]*(?=\s*\.)",
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
                        # The ``x`` of ``352x212``: a size separator, not a
                        # variable that happens to be called x.
                        "name": "punctuation.separator.dimension.jsw",
                        "match": r"(?<=\d)[x×](?=\d)",
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
                        # ``(?<!\.)``: ``lst.length()`` is the list's method, not
                        # the ``lengthOf`` builtin.
                        "name": "support.function.builtin.jsw",
                        "match": r"(?<!\.)" + _word_re(BUILTIN_FUNCTIONS) + r"(?=\s*\()",
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
                        # without a call's parentheses. `gtm.load(…)` is here for
                        # the same reason as `Arduino.high(13)` -- the pishi names
                        # are coloured whether or not the build enables them.
                        "comment": "A known extension member: WebSocket.closingCode, Pointerlock.setPointerLock, gtm.load",
                        # Capture 2 is the bare namespace, so the name ends up a
                        # token of its own (coloured like capture 1's dot) and
                        # `microbit.displayText` reads as namespace + member.
                        "match": r"((\b(?:"
                        + "|".join(sorted(highlight_namespaces()))
                        + r"))\s*\.\s*)((?:"
                        + "|".join(sorted(
                            (name for name in highlight_members() if "_" not in name),
                            key=lambda name: (-len(name), name),   # total order: the
                        ))                                        # set has no order
                        + r")\b)",
                        "captures": {
                            "1": {"name": "support.class.builtin.jsw"},
                            "2": {"name": "support.class.builtin.jsw"},
                            "3": {"name": "support.function.extension.jsw"},
                        },
                    },
                    {
                        "comment": "Known runtime namespace: Motion, Pen, Music, microbit, ...",
                        "name": "support.class.builtin.jsw",
                        "match": _word_re(BUILTIN_NAMESPACES | highlight_namespaces())
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
                        # ``(?<!\.\.)``: ``for i in 1..n`` puts an ``n`` right
                        # after a dot, and it is the range's upper bound, not a
                        # property of ``1.``.
                        "comment": "Method or property after a single dot.",
                        "name": "entity.name.function.call.jsw",
                        "match": r"(?<!\.\.)(?<=\.)[A-Za-z_$][\w$]*(?=\s*\()",
                    },
                    {
                        "name": "variable.other.property.jsw",
                        "match": r"(?<!\.\.)(?<=\.)[A-Za-z_$][\w$]*",
                    },
                ]
            },
            "operators": {
                "patterns": [
                    # Comparison first: ``==`` also starts with ``=``, and
                    # Oniguruma alternation takes the first branch that fits, so
                    # an assignment-first table scoped ``==`` as two ``=``.
                    {"name": "keyword.operator.comparison.jsw", "match": r"(==|!=|<=|>=|<|>)"},
                    {"name": "keyword.operator.assignment.jsw", "match": r"(\+=|-=|\*=|/=|%=|=)"},
                    {"name": "keyword.operator.logical.jsw", "match": r"(&&|\|\||!)"},
                    {"name": "keyword.operator.arithmetic.jsw", "match": r"(\*\*|\+|-|\*|/|%|\+\+|--)"},
                    {"name": "keyword.operator.range.jsw", "match": r"\.\."},
                    # A trailing backslash joins this line to the next; the
                    # lexer swallows both characters.
                    {"name": "constant.character.escape.line-continuation.jsw",
                     "match": r"\\$"},
                    # ``?`` is not an operator the parser has: the ternary is
                    # ``a if c else b`` (E0087). Lexed so the error can say so,
                    # and flagged here so the editor says it first.
                    {"name": "invalid.illegal.no-ternary.jsw", "match": r"\?"},
                    # A member accessor. ``..`` is claimed above, and a number's
                    # dot is inside the #numbers rules, so what is left is ``.``.
                    {"name": "punctuation.accessor.jsw", "match": r"\."},
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
