"""Single source of truth for JadenScript's vocabulary.

Both the lexer and the VS Code TextMate grammar generator import this module,
so a keyword can never drift between the compiler and the editor.
"""

from __future__ import annotations

# Keywords the v0 parser understands.
KEYWORDS: frozenset[str] = frozenset(
    {
        "class",
        "stage",
        "loose",
        "void",
        "public",
        "private",
        "if",
        "elif",
        "else",
        "for",
        "while",
        "repeat",
        "switch",
        "case",
        "default",
        "break",
        "continue",
        "return",
        "call",
        "import",
        "as",
        "true",
        "false",
        "null",
        "and",
        "or",
        "not",
        "any",
        "metadata",
    }
)

# Reserved for later versions. The lexer still classifies them as keywords so
# that user code cannot silently shadow a future language feature.
RESERVED: frozenset[str] = frozenset(
    {
        "extends",
        "static",
        "final",
        "const",
        "let",
        "var",
        "new",
        "this",
        "typeof",
        "instanceof",
        "delete",
        "try",
        "catch",
        "finally",
        "throw",
        "yield",
        "await",
        "async",
        "do",
        "in",
        "of",
        "enum",
        "struct",
        "typedef",
        "import",
        "export",
    }
)

# Declarations / modifiers get their own highlight bucket.
MODIFIERS: frozenset[str] = frozenset({"public", "private", "static", "final"})
TYPES: frozenset[str] = frozenset({"void"})
LITERALS: frozenset[str] = frozenset({"true", "false", "null", "any"})

ALL_KEYWORDS: frozenset[str] = KEYWORDS | RESERVED

# Runtime namespaces that the compiler maps onto Scratch opcodes. Anything
# capitalised before a dot is also highlighted as a namespace by the grammar,
# but this list is the authoritative set.
BUILTIN_NAMESPACES: frozenset[str] = frozenset(
    {
        "Motion",
        "Looks",
        "Sound",
        "Events",
        "Control",
        "Sensing",
        "Operators",
        "Variables",
        "Lists",
        "Pen",
        "Music",
        "JSON",
        "console",
        "Timer",
        "Util",
        "Runtime",
        "Clone",
        "Stage",
    }
)

#: Extra spellings the state namespaces accept, mapped to the canonical name.
#: Shared by codegen and the completion database so a spelling can never be
#: understood by the compiler but unknown to the editor. The canonical names
#: are the ones the popup offers: ``showVariable``/``hideVariable`` for a
#: monitor and ``showList``/``hideList`` for a list.
STATE_ALIASES: dict[str, dict[str, str]] = {
    "Variables": {
        "show": "showVariable",
        "hide": "hideVariable",
    },
    "Lists": {
        "show": "showList",
        "hide": "hideList",
        "append": "add",
        "push": "add",
        "replace": "set",
        "remove": "delete",
    },
}

OPERATORS: tuple[str, ...] = (
    "**",
    "..",   # inclusive range: ``for i in 1..10``
    "==",
    "!=",
    "<=",
    ">=",
    "&&",
    "||",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "++",
    "--",
    "+",
    "-",
    "*",
    "/",
    "%",
    "!",
    "<",
    ">",
    "=",
    "?",
)

PUNCTUATION: tuple[str, ...] = ("(", ")", "{", "}", "[", "]", ",", ";", ".", ":", "#")

# ---------------------------------------------------------------------------
# Words that are not language keywords but the decompiler writes them as
# syntax: ``metadata list Stage.x at 0,0 100x24 hidden;``, ``stage: costume=0``
# and the asset comments. The grammar, the completion data and the docs all
# read these sets, so a word can never be highlighted but not completed.
# ---------------------------------------------------------------------------

#: Monitor modes: the fourth word of a ``metadata`` line.
MONITOR_MODES: frozenset[str] = frozenset(
    {"normal", "large", "slider", "list", "default"}
)

#: The structural words around a ``metadata`` line.
METADATA_WORDS: frozenset[str] = frozenset(
    {"pos", "at", "hidden", "shown", "range", "watchers", "min", "max"}
)

#: ``stage: costume=0, volume=100, …;`` and ``Sprite: name="x", x=0, …;``
TARGET_KEYS: frozenset[str] = frozenset(
    {
        "name", "x", "y", "size", "direction", "show", "costume", "volume",
        "layer", "tempo", "video", "videoTransparency", "rotation", "draggable",
        "namespace", "ns",
    }
)

#: ``// costume centerx=0 centery=0 res=1`` and
#: ``// sound rate=48000 samples=8225`` — the annotation the asset import
#: writes above an ``import`` line.
ASSET_COMMENT_WORDS: frozenset[str] = frozenset({"costume", "sound"})
ASSET_COMMENT_KEYS: frozenset[str] = frozenset(
    {"centerx", "centery", "res", "rate", "samples", "sample"}
)

# Feature switches declared with ``#enable_feature<name>;``.
FEATURES: frozenset[str] = frozenset(
    {
        "useTempVariables",
        "commentMetadata",
        "namespacedNames",
        "doObfuscation",
        # The old spelling: still accepted, reported as obsolete.
        "hideObjectNames",
    }
)

#: Obsolete feature spellings, mapped to the name that replaced them.
FEATURE_RENAMES: dict[str, str] = {"hideObjectNames": "doObfuscation"}

# Assignment forms, used by the parser and the highlighter alike.
ASSIGN_OPS: frozenset[str] = frozenset({"=", "+=", "-=", "*=", "/=", "%="})
