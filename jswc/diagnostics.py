"""The diagnostic catalogue: every code ``jswc`` can print, with a hint.

Codes are a plain one-line-per-problem list — ``E0001``, ``E0002``, … for errors,
``W0001``, ``W0002``, … for warnings and ``I0001``, ``I0002``, … for information.
There is no meaning in the number: it is an identifier to grep for, not a
category. New problems append the next free number, so an existing code never
changes once it has shipped.

Every message is stamped with its code at the raise site
(``ParseError(..., code="E0011")``), so classification is an exact lookup rather
than a guess. The regex table at the bottom exists for messages that still
arrive as plain text — plugin code, older call sites — and is the last resort.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Message:
    """One catalogued diagnostic."""

    code: str
    severity: str  # "error" | "warning" | "info"
    title: str
    hint: str = ""



#: ``E``/``W``/``I`` + four digits at the start of a message body.
CODE_PREFIX = re.compile(r"^([EWI]\d{4})(?:[:\s]\s*|\s+|$)")

#: ``--loglevel`` names, lowest first. ``warn`` is the default: errors and
#: warnings print, information waits to be asked for.
LEVELS: tuple[str, ...] = ("error", "warn", "info")
LEVEL_ALIASES: dict[str, str] = {
    "error": "error", "errors": "error", "quiet": "error",
    "warn": "warn", "warning": "warn", "warnings": "warn", "default": "warn",
    "info": "info", "all": "info", "debug": "info",
}
#: Severity -> the lowest log level that displays it.
SEVERITY_LEVEL: dict[str, str] = {"error": "error", "warning": "warn", "info": "info"}
_ORDER: dict[str, int] = {"error": 0, "warn": 1, "info": 2}


def normalise_level(name: str) -> str:
    return LEVEL_ALIASES.get((name or "").strip().lower(), "warn")


def shows(severity: str, level: str = "warn") -> bool:
    """Whether a diagnostic of ``severity`` is printed at ``level``."""
    return _ORDER.get(SEVERITY_LEVEL.get(severity, "warn"), 1) <= _ORDER[level]


def split_code(text: str) -> tuple[str | None, str]:
    """``"E0012 expected ';'"`` -> ``("E0012", "expected ';'")``."""
    match = CODE_PREFIX.match(text)
    if not match:
        return None, text
    return match.group(1), text[match.end():]


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------
MESSAGES: tuple[Message, ...] = (
    # -- lexical: the scanner could not read the bytes ---------------------
    Message("E0001", "error", "unexpected character",
            "Identifiers may hold letters, digits, ``_`` and ``$``; strings need quotes."),
    Message("E0002", "error", "unterminated string literal",
            'Add the closing quote, or escape the one inside as \\".'),
    Message("E0003", "error", "unterminated f-string",
            "An f-string ends with the same quote it started with."),
    Message("E0004", "error", "unterminated block comment",
            "``/*`` needs its closing ``*/``; ``//`` comments run to the end of the line."),
    Message("E0005", "error", "invalid \\x escape",
            "``\\x`` takes exactly two hex digits, for example ``\\x41``."),
    Message("E0006", "error", "invalid \\u escape",
            "``\\u`` takes exactly four hex digits, for example ``\\u2192``."),
    Message("E0007", "error", "malformed numeric literal",
            "``0x``/``0b``/``0o`` need at least one digit after the prefix."),

    # -- syntax: the tokens do not fit the grammar -------------------------
    Message("E0008", "error", "expected a token",
            "The parser needed a specific token here and found something else."),
    Message("E0009", "error", "expected a name",
            "A name starts with a letter, ``_`` or ``$``."),
    Message("E0010", "error", "expected ';'",
            "Statements end with ``;`` or a line break; a block opener needs ``{``."),
    Message("E0011", "error", "expected '{'",
            "Bodies open with ``{`` and close with ``}``."),
    Message("E0012", "error", "expected '}'",
            "Every ``{`` needs a matching ``}``."),
    Message("E0013", "error", "expected '('",
            "Arguments are written inside ``( ... )``."),
    Message("E0014", "error", "expected ')'",
            "Every ``(`` needs a matching ``)``."),
    Message("E0015", "error", "expected '['",
            "Index and list syntax are written inside ``[ ... ]``."),
    Message("E0016", "error", "expected ']'",
            "Every ``[`` needs a matching ``]``."),
    Message("E0017", "error", "unexpected token",
            "The grammar has no rule for this token in this position."),
    Message("E0018", "error", "unterminated block",
            "Close the block with ``}`` before the end of the file."),
    Message("E0019", "error", "unterminated class body",
            "The ``class`` opened here is still open at the end of the file."),
    Message("E0020", "error", "unterminated stage body",
            "The ``stage {`` opened here is still open at the end of the file."),
    Message("E0021", "error", "unterminated f-string hole",
            "An ``{expression}`` hole needs its closing ``}`` on the same line."),
    Message("E0022", "error", "malformed 'for' header",
            "``for i = 0; i < n; i += 1`` needs both ``;`` separators."),
    Message("E0023", "error", "malformed 'switch'",
            "A ``switch`` body holds ``case`` and ``default`` labels only."),
    Message("E0024", "error", "missing asset path",
            "``import`` needs a quoted path: ``import \"assets/a.svg\" as a;``."),
    Message("E0025", "error", "'call' without a call",
            "``call`` runs a custom block: ``call Move(x, y);``."),
    Message("E0026", "error", "invalid assignment target",
            "Only a name can be assigned; ``list[0] = x`` is ``list.set(0, x)``."),
    Message("E0027", "error", "invalid increment target",
            "``+=`` and ``++`` apply to a variable name, not an expression."),
    Message("E0028", "error", "unexpected token in an f-string hole",
            "A hole holds a single expression; move statements out of the string."),
    Message("E0029", "error", "no class declarations",
            "A project needs at least one ``class`` or a ``stage`` block."),
    Message("E0030", "error", "bad directive",
            "The only directive is ``#enable_feature<name>;``."),
    Message("E0031", "error", "metadata needs a target",
            "``metadata`` names what it describes: ``metadata Stage.tick pos 1,2;``."),
    Message("E0032", "error", "malformed metadata",
            "``pos``, a monitor mode, or ``monitor|watch|comment|note`` comes first."),
    Message("E0033", "error", "expected 'init'",
            "Green-flag setup is a ``public void init()`` block."),
    Message("E0034", "error", "unexpected top-level statement",
            "Only declarations may appear outside a ``class`` or ``stage``."),

    # -- names, scope and types --------------------------------------------
    Message("E0035", "error", "undefined variable",
            "Declare it (``public x = 0;`` or ``Global.x = 0;``) or pass ``--allow-undefined``."),
    Message("E0036", "error", "unknown function",
            "Declare the custom block, or check the extension block name."),
    Message("E0037", "error", "unknown reporter",
            "The name is not a reporter, a variable or a custom block."),
    Message("E0038", "error", "unknown command",
            "The name is not a stack block this compiler knows."),
    Message("E0039", "error", "statement used as a value",
            "Stack blocks have no return value; call it without using the result."),
    Message("E0040", "error", "not a block of that extension",
            "Check the extension id and the block name in its documentation."),
    Message("E0041", "error", "private to another sprite",
            "``private`` variables are reachable from their own sprite only."),
    Message("E0042", "error", "cannot assign to a parameter",
            "Block parameters are read-only; copy it into a variable first."),
    Message("E0043", "error", "cannot assign to a list",
            "Use ``list.set(i, x)``, ``list.add(x)`` or ``list.replace(...)``."),
    Message("E0044", "error", "wrong number of arguments",
            "Count the ``%s`` slots in the block's proccode."),
    Message("E0045", "error", "expected a literal value",
            "Menus and metadata accept strings, numbers and ``true``/``false`` only."),
    Message("E0046", "error", "expected a variable name",
            "``showVariable``/``hideVariable`` take a bare or quoted variable name."),
    Message("E0047", "error", "expected a list name",
            "``showList``/``hideList`` take a bare or quoted list name."),
    Message("E0048", "error", "expected a message name",
            "``broadcast`` takes the name of a message, or a variable holding it."),
    Message("E0049", "error", "expected a block opcode",
            "``block()`` and ``menu()`` take the opcode as a quoted string."),
    Message("E0050", "error", "does not take a block body",
            "Only custom blocks may own ``{ ... }``; drop the braces."),
    Message("E0051", "error", "custom block returns a value",
            "Scratch procedures cannot report; set a variable instead."),
    Message("E0052", "error", "initial value is not a literal",
            "A declaration's value must be a constant; assign it later instead."),
    Message("E0053", "error", "unsupported construct",
            "This is a compiler gap, not a program bug."),
    Message("E0054", "error", "unsupported syntax",
            "Rewrite it with the block calls listed in syntax.md."),
    Message("E0055", "error", "call target could not be lowered",
            "The callee exists but could not be emitted; the call is dropped."),
    Message("E0056", "error", "proccode mismatch",
            "The ``%s``/``%n``/``%b`` slots must match the declared parameters."),
    Message("E0057", "error", "expected a field name",
            "``menu()``/``block()`` take the field name as a quoted string."),

    # -- project structure and assets --------------------------------------
    Message("E0058", "error", "asset not found",
            "Paths are relative to the ``.jsw`` file that imports them."),
    Message("E0059", "error", "invalid JSON",
            "``.jsa`` files hold a JSON array of list items."),
    Message("E0060", "error", "expected a JSON array",
            "``.jsa`` files hold ``[ ... ]``, one item per entry."),
    Message("E0061", "error", "unsupported asset type",
            "Use ``.jsv`` (value), ``.jsa`` (JSON array) or ``.jsl`` (lines)."),
    Message("E0062", "error", "dangling block reference",
            "A generated link points at a block that was never emitted."),
    Message("E0063", "error", "malformed block structure",
            "A block's parent/next/top-level flags disagree."),
    Message("E0064", "error", "malformed input",
            "A block input is not the ``[type, value]`` shape the VM expects."),
    Message("E0065", "error", "unknown variable id",
            "The block references a variable that no target declares."),
    Message("E0066", "error", "unknown list id",
            "The block references a list that no target declares."),
    Message("E0067", "error", "unknown broadcast id",
            "The block references a message that no target broadcasts."),
    Message("E0068", "error", "unknown opcode",
            "The opcode is not in the scratch-vm catalogue and is not an extension block."),

    # -- directives, metadata and comments ---------------------------------
    Message("E0069", "error", "unknown feature",
            "Run ``jswc features`` for the list of flags."),
    Message("E0070", "error", "unreadable monitor comment",
            "The ``//`` monitor annotation does not parse; see ``jswc decompile`` output."),
    Message("E0071", "error", "no such sprite or class",
            "The annotation names a target that does not exist in this file."),
    Message("E0072", "error", "unknown monitor state",
            "A monitor is ``visible``/``hidden``, ``normal``/``large``/``slider``."),
    Message("E0073", "error", "metadata needs a literal value",
            "``metadata`` values are constants, not expressions."),
    Message("E0076", "error", "wrong number of arguments",
            "Some extension commands have several forms; the argument count picks "
            "the block. ``WebSocket.closeConnection`` takes none, a code, or a "
            "code and a reason -- nothing else."),
    Message("E0074", "error", "temp loop variable already live",
            "Two nested loops cannot share one ``_name`` counter; rename the inner one."),


    # -- recovered by the parser -------------------------------------------
    Message("W0001", "warning", "ignored an extra token",
            "An extra ``)`` has no matching ``(``; the parser skipped it."),
    Message("W0002", "warning", "auto-closed bracket",
            "The parser closed the unclosed ``(`` at the end of the statement."),

    # -- semantics the compiler cannot prove -------------------------------
    Message("I0003", "info", "large list written inline",
            "A list owned by a class is written as a literal so no item is "
            "lost; a file-scope list of the same size moves to a ``.jsa`` side "
            "file instead."),
    Message("I0002", "info", "obsolete feature name",
            "``hideObjectNames`` became ``doObfuscation``; it now hides custom "
            "block labels and broadcast names too."),
    Message("I0001", "info", "renamed identifier",
            "Scratch names hold no spaces or punctuation and stop at 20 characters; "
            "the renamed spelling is what the project uses."),
    Message("W0004", "warning", "computed at runtime",
            "The VM looks the name up when the block runs, so typos fail silently."),
    Message("W0005", "warning", "undeclared argument",
            "The block keeps the argument as an extra input."),
    Message("W0006", "warning", "keyword outside an argument",
            "``any`` is a menu option; write ``\"any\"`` when you mean the text."),
    Message("W0007", "warning", "key the VM cannot report",
            "The VM only reports single characters and the named keys."),
    Message("W0008", "warning", "dropped or degraded",
            "The construct has no Scratch equivalent and is kept as a comment."),
    Message("W0009", "warning", "'continue' ignored",
            "Scratch loops have no ``continue``; skip the rest with an ``if``."),
    Message("W0010", "warning", "class-level statement skipped",
            "Only declarations and functions live directly inside a ``class``."),
    Message("W0011", "warning", "duplicate visibility",
            "A name declared both private and public is public."),
    Message("W0012", "warning", "initial value not literal",
            "The variable starts at 0 and the expression is ignored."),
    Message("W0013", "warning", "expression statement has no effect",
            "Scratch evaluates nothing here; store or drop the value."),
    Message("W0014", "warning", "duplicate import",
            "The import is ignored because the name already exists."),

    # -- annotations the compiler had to skip ------------------------------
    Message("W0015", "warning", "comment directive ignored",
            "Add the matching ``#enable_feature`` to switch it on."),
    Message("W0016", "warning", "comment for an unknown target",
            "The ``// Sprite:`` annotation names a sprite this file lacks."),
    Message("W0017", "warning", "comment text is not a string literal",
            "Comments round-trip through a string; unescaped quotes break them."),
    Message("W0018", "warning", "unreadable note comment",
            "The note annotation could not be parsed and is dropped."),
    Message("W0019", "warning", "monitor annotation ignored",
            "The monitor annotation is kept as a comment only."),
    Message("W0020", "warning", "duplicate metadata",
            "Two declarations describe the same target; the last one wins."),
    Message("W0022", "warning", "display matrix",
            "The 5x5 display takes 25 cells of 0 and 1; a binary literal is "
            "converted for you."),
    Message("W0021", "warning", "loop counter reassigned",
            "Writing the counter inside the loop changes when it stops; use "
            "another variable for the body's bookkeeping."),
    Message("W0024", "warning", "clone abandoned by stopThisScript",
            "``stop this script`` ends the script but keeps the clone alive for "
            "ever; put ``Control.deleteThisClone()`` after the ``createClone`` "
            "that handed the work on. Scratch caps a project at 300 clones, and "
            "past that ``create clone`` silently does nothing."),
    Message("W0023", "warning", "broadcast name built at runtime",
            "``doObfuscation`` renames every broadcast, so a message assembled "
            "with ``join`` no longer matches any hat; pass a literal instead."),
    Message("W0025", "warning", "'break' inside a loop",
            "Scratch has no loop break: ``break`` lowers to ``stop this script``, "
            "which leaves the whole function, not just the loop. Set a flag and "
            "test it in the loop condition instead."),

    Message("W0026", "warning", "unknown math operator",
            "``Operators.math(user, x)`` writes the operator name straight into "
            "the ``[ ... ] of ( )`` menu. A name that menu does not have -- "
            "``round`` is one -- evaluates to 0 for ever, with no error at run "
            "time. The menu is abs, floor, ceiling, sqrt, sin, cos, tan, asin, "
            "acos, atan, ln, log, e ^ and 10 ^; use ``Operators.round(x)`` for "
            "rounding."),

    Message("E9999", "error", "unclassified",
            "No catalogue entry matched; please report the message verbatim."),
)

BY_CODE: dict[str, Message] = {m.code: m for m in MESSAGES}
FALLBACK = BY_CODE["E9999"]

def message(code: str) -> Message:
    return BY_CODE.get(code, FALLBACK)


# ---------------------------------------------------------------------------
# Fallback classification for messages that arrive without a code
# ---------------------------------------------------------------------------
# Ordered: the first match wins, so specific shapes come first.  Each pattern
# points at the code that the corresponding raise site now stamps explicitly;
# the table keeps older text (and plugin diagnostics) pointing at the same hint.
LEGACY: tuple[tuple[str, str], ...] = (
    # Structure first: ``invalid project: ...`` also reads like a syntax error.
    (r"^invalid project|^dangling|^duplicate|^negative|^missing|"
     r"^unknown (variable|list|target)|^cycle", "E0062"),
    # Warnings carry distinctive wording, so they win over the generic shapes.
    (r"renamed to", "I0001"),
    (r"computed at runtime", "W0004"),
    (r"is not declared here", "W0005"),
    (r"declared both private and public|already a public variable", "W0011"),
    (r"menu option|keyword .* outside", "W0006"),
    (r"never fires|is not a key name|not one of the more-keys|is a control code", "W0007"),
    (r"class-level statements are not supported", "W0010"),
    (r"not supported yet|skipped|kept as a comment|unsupported block|"
     r"kept as a raw block|kept as block|cannot recover|auto-closed|"
     r"ignored an extra", "W0008"),
    (r"ignored; add #enable_feature", "W0015"),
    (r"has parameters", "W0019"),
    (r"unreadable monitor comment", "E0070"),
    (r"no such sprite/class", "E0071"),
    (r"unknown state", "E0072"),
    (r"unknown feature", "E0069"),
    # Names and scope.
    (r"undefined variable", "E0035"),
    (r"unknown reporter", "E0037"),
    (r"unknown command", "E0038"),
    (r"unknown function", "E0036"),
    (r"cannot resolve", "E0037"),
    (r"is not a block of the", "E0040"),
    (r"is a statement, not a value", "E0039"),
    (r"private to sprite", "E0041"),
    (r"cannot assign to", "E0042"),
    (r"cannot be assigned|is read-only", "E0043"),
    (r"takes \d+ argument|takes no argument", "E0044"),
    (r"needs a literal|needs the field name", "E0045"),
    (r"needs a variable name", "E0046"),
    (r"needs a list name", "E0047"),
    (r"needs a message name", "E0048"),
    (r"takes a block opcode|needs an opcode", "E0049"),
    (r"cannot return values", "E0051"),
    (r"has no initial value|is not a literal", "E0052"),
    # Assets.
    (r"not found|cannot import|unsupported asset type", "E0058"),
    (r"valid JSON", "E0059"),
    (r"expected a JSON array", "E0060"),
    # Syntax, last: these shapes are the least specific.
    (r"^unterminated ", "E0018"),
    (r"^(stray|unknown escape)", "E0001"),
    (r"^invalid ", "E0001"),
    (r"^expected ", "E0008"),
    (r"^unexpected ", "E0017"),
)

_LEGACY: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), code) for pattern, code in LEGACY
)


#: Which token was missing decides the syntax code, here and in the parser.
EXPECT_CODES = {
    ";": "E0010",
    "{": "E0011",
    "}": "E0012",
    "(": "E0013",
    ")": "E0014",
    "[": "E0015",
    "]": "E0016",
}


def classify(text: str) -> Message:
    """The catalogue entry for a diagnostic string.

    A stamped code wins; anything else falls back to shape matching so a
    message written by an older call site still renders with a sensible hint.
    """
    body = re.sub(r"^\d+:\d+:\s*", "", text)
    code, rest = split_code(body)
    if code and code in BY_CODE:
        return BY_CODE[code]
    haystack = rest if code else body
    expected = re.match(r"expected '(.+?)'", haystack)
    if expected and expected.group(1) in EXPECT_CODES:
        return message(EXPECT_CODES[expected.group(1)])
    for pattern, fallback in _LEGACY:
        if pattern.search(haystack):
            return message(fallback)
    return FALLBACK
