#!/usr/bin/env python3
"""Generate the Neovim/Vim syntax file from ``jswc.keywords``.

Run ``python3 tools/gen_vim_syntax.py`` (``make nvim-syntax``) after touching the
keyword tables; the generated files are committed so the plugin works without a
build step.

Vim cannot do what a TextMate grammar does line-by-line, so the rules are
ordered the way ``:syn`` resolves ties: the item defined **last** wins when two
of them start at the same column. Generic identifiers therefore come first and
every specific word group after them.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from jswc.extensions import highlight_members, highlight_namespaces  # noqa: E402
from jswc.keywords import (  # noqa: E402
    BUILTIN_NAMESPACES,
    FLAG_VALUES,
    HOOK_MENUS,
    KEYWORDS,
    LITERALS,
    METADATA_ANNOTATIONS,
    METADATA_FLAGS,
    METADATA_WORDS,
    MODIFIERS,
    MONITOR_MODES,
    PROCCODE_PLACEHOLDERS,
    RESERVED,
    ROTATION_VALUES,
    TYPES,
    VIDEO_VALUES,
)
from gen_tm_grammar import BUILTIN_FUNCTIONS  # noqa: E402

PLUGIN = ROOT / "nvim"

#: Control flow, split so a colourscheme can tell the three apart.
CONDITIONAL = {"if", "elif", "else", "switch", "case", "default"}
#: ``in`` closes a ``for`` header, so it reads as control flow; the compiler
#: keeps it in RESERVED only so a variable cannot take the name.
REPEAT = {"for", "while", "repeat", "break", "continue", "in"}
#: ``loose`` wraps raw blocks the decompiler could not map: it is not control
#: flow, so it gets a group of its own (TextMate calls it
#: ``keyword.other.loose.jsw`` for the same reason).
STATEMENT = {"return", "call"}
LOOSE = {"loose"}
DECLARATION = {"stage", "import", "include", "as"}

OPERATORS = (
    "**", "..", "==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=",
    "%=", "++", "--", "+", "-", "*", "/", "%", "<", ">", "=", "!",
)
WORD_OPERATORS = {"and", "or", "not"}
#: Everything left over once the specialised buckets are removed.
GENERIC_KEYWORDS = (
    KEYWORDS
    - CONDITIONAL
    - REPEAT
    - STATEMENT
    - DECLARATION
    - MODIFIERS
    - TYPES
    - LITERALS
    - WORD_OPERATORS
)

#: Monitoring and metadata words: colours for the words the decompiler writes.
#: Split by type so a colourscheme can tell a mode (``slider``) from the
#: monitor's state (``shown``) from the layout words (``at``/``range``).
METADATA_KEYWORD = set(METADATA_WORDS)
METADATA_MODES = set(MONITOR_MODES)
METADATA_STATES = set(METADATA_FLAGS)
METADATA_VERBS = set(METADATA_ANNOTATIONS)
#: What a ``// comment`` annotation may start with.
ANNOTATION_WORDS = (
    "proccode", "receiver at", "hook:", "monitor", "watch", "comment",
    "warp", "main thread", "grouped", "at", "Sprite:", "Stage:",
    "unsupported", "undefined", "callee", "temp variables",
)

NS_NAMES = sorted(set(BUILTIN_NAMESPACES) | set(highlight_namespaces()) | {"microbit", "Comments", "MoreEvents", "WebSocket", "sound", "pen"})
#: Extension members are highlighted by name so ``WebSocket.closingCode`` (a
#: property, no parentheses) looks the same as ``WebSocket.sendMessage(…)``.
#: the spellings people write: friendly aliases and camelCase block names.
#: The snake_case block names are accepted by the compiler but never highlighted,
#: they would only add noise.
EXTENSION_MEMBERS = sorted(name for name in highlight_members() if "_" not in name)


def _words(words) -> str:
    return " ".join(sorted(words))


def _alternation(words) -> str:
    # a total order: longest first so a prefix cannot win, the name to break ties
    ordered = sorted(words, key=lambda word: (-len(word), word))
    return r"\|".join(word.replace(" ", r"\ ") for word in ordered)


#: Vim refuses a ``:syn match`` pattern past roughly a kilobyte (``E53``, then
#: ``E475``), and the extension member list is well past that now that the
#: pishi blocks are in it. Each chunk is a separate rule with the same
#: namespace alternation, so the group is unchanged from the outside.
PATTERN_BUDGET = 480


def _alternation_chunks(words, budget: int = PATTERN_BUDGET) -> list[str]:
    ordered = sorted(words, key=lambda word: (-len(word), word))
    chunks: list[list[str]] = [[]]
    used = 0
    for word in ordered:
        escaped = word.replace(" ", r"\ ")
        cost = len(escaped) + 3          # the ``\|`` separator and slack
        if chunks[-1] and used + cost > budget:
            chunks.append([])
            used = 0
        chunks[-1].append(escaped)
        used += cost
    return [r"\|".join(chunk) for chunk in chunks if chunk]


def _extension_member_rule(chunk: str) -> str:
    r"""One ``jswExtensionMember`` rule for a chunk of member names.

    ``\@<=`` matters twice over: the member only counts after a known namespace
    (so ``Pen.status`` stays a plain member), and starting the match *after* the
    dot is what lets it win over ``jswMember`` -- the namespace itself is a
    keyword, and a keyword beats any match that starts on it (``:syn-priority``).
    """
    return (
        "syn match jswExtensionMember /"
        + r"\%(\%(" + _alternation(highlight_namespaces()) + r"\)\.\)\@<="
        + r"\%(" + chunk + r"\)\>/"
    )


def _annotation_pattern(words) -> str:
    """``\\<word\\>`` normally, but a word ending in ``:`` has no boundary."""
    parts = []
    for word in words:
        body = r"\<" + word.replace(" ", r"\ ")
        parts.append(body + (r"\>" if word[-1].isalnum() else ""))
    return r"\%(" + r"\|".join(sorted(parts, key=len, reverse=True)) + r"\)"


def _literal_alternation(words) -> str:
    """``\\V`` (very nomagic) alternation: only the delimiter needs escaping."""
    body = r"\|".join(word.replace("/", r"\/") for word in sorted(words, key=len, reverse=True))
    return r"\V" + body


def syntax_file() -> str:
    lines = [
        '" JadenScript syntax for Vim/Neovim.',
        '" Generated by tools/gen_vim_syntax.py - do not edit by hand.',
        "",
        "if exists('b:current_syntax')",
        "  finish",
        "endif",
        "",
        "syn case match",
        "syn sync fromstart",
        "",
        '" Vim resolves a tie by "defined last wins", so the generic tokens come',
        '" first and every specialised rule after them.',
        r"syn match jswIdentifier /\<[A-Za-z_$][A-Za-z0-9_$]*\>/",
        "",
        '" -- numbers -----------------------------------------------------',
        r"syn match jswNumber /\<0[xX][0-9a-fA-F_]\+\>/",
        r"syn match jswNumber /\<0[bB][01_]\+\>/",
        r"syn match jswNumber /\<0[oO][0-7_]\+\>/",
        r"syn match jswNumber /\<[0-9][0-9_]*\%(\.[0-9][0-9_]*\)\=\%([eE][-+]\=[0-9]\+\)\=\>/",
        r"syn match jswNumber /\%(\<[0-9][0-9_]*\)\@<=\.[0-9][0-9_]*\>/",
        "",
        '" -- operators (before the regions, so ``//`` is a comment) ---------',
        "syn match jswOperator /" + _literal_alternation(OPERATORS) + "/",
        r"syn match jswDelimiter /[(){}\[\];,]/",
        '" A trailing backslash joins the next line; the lexer swallows both.',
        r"syn match jswContinuation /\\$/",
        '" ``?`` is not a language operator: the ternary is ``a if c else b`` (E0087).',
        r"syn match jswNoTernary /?/",
        "",
        '" -- comments and strings ----------------------------------------',
        r"syn match jswAnnotationWord /" + _annotation_pattern(ANNOTATION_WORDS) + r"/ contained",
        # ``// hook: when [timer] > 10`` - the bracketed menu, and the ``%s``
        # placeholders of a ``// proccode`` label.
        r"syn match jswHookMenu /\%(\[\zs" + _alternation(HOOK_MENUS) + r"\ze\]\)/ contained",
        r"syn match jswProccodeHole /" + _alternation(PROCCODE_PLACEHOLDERS)
        + r"/ contained",
        r'syn region jswComment start="//" end="$" contains=jswAnnotationWord,jswTodo,'
        r'jswHookMenu,jswProccodeHole',
        r'syn region jswComment start="/\*" end="\*/" contains=jswAnnotationWord,jswTodo',
        r"syn keyword jswTodo contained TODO FIXME XXX BUG",
        "",
        r"syn match jswEscape /\\./ contained",
        r'syn region jswFString start=/\v<[fF]"/ skip=/\\./ end=/"/ contains=jswHole,jswEscape',
        r"syn region jswFString start=/\v<[fF]'/ skip=/\\./ end=/'/ contains=jswHole,jswEscape",
        r"syn match jswHole /{[^}]*}/ contained contains=jswIdentifier,jswNumber,jswString,jswTemp,jswBuiltin,jswNamespace",
        r'syn region jswString start=/"/ skip=/\\./ end=/"/ contains=jswEscape,jswHole',
        r"syn region jswChar start=/'/ skip=/\\./ end=/'/ contains=jswEscape",
        "",
        '" -- directives --------------------------------------------------',
        r"syn match jswDirective /^\s*#\h\w*/",
        r"syn region jswFeature matchgroup=jswDirective start=/#enable_feature</ end=/>/ oneline",
        "",
        '" -- metadata lines: ``stage:``, ``Sprite:``, ``Name: name="..."`` -',
        r"syn region jswMetadataLine matchgroup=jswDeclaration start=/^\s*metadata\>/ end=/;/ oneline"
        r" contains=jswMetadataVerb,jswMonitorMode,jswMetadataFlag,jswMetadata,jswTarget,jswTargetKey,"
        r"jswTargetValue,jswString,jswChar,jswNumber,jswComment,jswIdentifier,jswNamespace,jswFunctionCall",
        r"syn keyword jswMetadata contained " + _words(METADATA_KEYWORD),
        r"syn keyword jswMonitorMode contained " + _words(METADATA_MODES),
        r"syn keyword jswMetadataFlag contained " + _words(METADATA_STATES),
        r"syn keyword jswMetadataVerb contained " + _words(METADATA_VERBS),
        r"syn match jswTarget /^\s*\h\w*\ze:.\{-}\<name=/",
        # A lookbehind, not a literal prefix: a match cannot *start* inside an
        # earlier one, so ``[(:,]\s*\zs`` lost every key after a comma (the
        # delimiter had already claimed it). ``\@<=`` puts the start on the key.
        r"syn match jswTargetKey /\%([(:,]\s*\)\@<=\h\w*\ze=[^=]/",
        '" The value of a target key: ``rotation=all``, ``video=on``, ``show=1``.',
        '" They are only values in that position - ``all`` is a fine variable name.',
        r"syn match jswTargetValue /\%(rotation\s*=\s*\)\@<=\%(" + _alternation(ROTATION_VALUES) + r"\)\>/",
        r"syn match jswTargetValue /\%(video\%(State\)\=\s*=\s*\)\@<=\%(" + _alternation(VIDEO_VALUES) + r"\)\>/",
        r"syn match jswTargetValue /\%(show\|draggable\)\s*=\s*\zs\%(" + _alternation(FLAG_VALUES) + r"\)\>/",
        "",
        '" -- the words -----------------------------------------------------',
        r"syn match jswTemp /\<_[A-Za-z0-9_$]\+\>/",
        "syn keyword jswBuiltin " + _words(BUILTIN_FUNCTIONS),
        "syn keyword jswNamespace " + _words(NS_NAMES),
        "syn keyword jswLiteral " + _words(LITERALS),
        "syn keyword jswConditional " + _words(CONDITIONAL),
        "syn keyword jswRepeat " + _words(REPEAT),
        "syn keyword jswStatement " + _words(STATEMENT),
        "syn keyword jswLoose " + _words(LOOSE),
        "syn keyword jswDeclaration " + _words(DECLARATION),
        "syn keyword jswModifier " + _words(MODIFIERS),
        "syn keyword jswType " + _words(TYPES),
        "syn keyword jswWordOperator " + _words(WORD_OPERATORS),
        # A match, not a keyword: ``l.delete(1)`` is a list method, and a
        # keyword beats a match at the same column regardless of rule order, so
        # the lookbehind has to be able to veto the word entirely.
        r"syn match jswReserved /\%(\.\)\@<!\<\%("
        + _alternation(RESERVED - KEYWORDS - MODIFIERS - TYPES - REPEAT)
        + r"\)\>/",
        "",
        '" A call is the name before the parenthesis, a class is the name after',
        '" ``class`` (the region keeps the keyword itself a declaration).',
        r"syn match jswMember /\.\zs[A-Za-z_$][A-Za-z0-9_$]*/",
        *[_extension_member_rule(chunk) for chunk in _alternation_chunks(EXTENSION_MEMBERS)],
        r"syn match jswFunctionCall /\<[A-Za-z_$][A-Za-z0-9_$]*\ze\s*(/",
        # ``void take(a, b)`` - the names between the parentheses are
        # parameters, not the variables they read as everywhere else.
        # A lookbehind rather than a region: Vim never let a region begin at a
        # ``\zs(`` (the start was zero-width and the end matched at once) and a
        # region beginning at the name lost to jswFunctionCall, which has to be
        # defined first so the name keeps its call colour.
        r"syn match jswParameter /\%(\<void\s\+[A-Za-z_$][A-Za-z0-9_$.]*\s*(\s*\)\@<=[A-Za-z_$][A-Za-z0-9_$]*/",
        r"syn match jswParameter /\%(\<void\s\+[A-Za-z_$][A-Za-z0-9_$.]*\s*([^)]*,\s*\)\@<=[A-Za-z_$][A-Za-z0-9_$]*/",
        # ``void take(a, b)`` - the names between the parentheses are
        # parameters, not the variables they read as everywhere else.
        r"syn region jswClassName matchgroup=jswDeclaration start=/\<class\s\+/ end=/[^A-Za-z0-9_$]/me=e-1 oneline",
        "",
        '" -- highlight groups ----------------------------------------------',
        "hi def link jswIdentifier Identifier",
        "hi def link jswMember Identifier",
        "hi def link jswExtensionMember Function",
        "hi def link jswTemp Identifier",
        "hi def link jswNamespace Structure",
        "hi def link jswClassName Type",
        "hi def link jswFunctionCall Function",
        "hi def link jswBuiltin Function",
        "hi def link jswConditional Conditional",
        "hi def link jswRepeat Repeat",
        "hi def link jswStatement Statement",
        "hi def link jswLoose PreProc",
        "hi def link jswParameter Identifier",
        "hi def link jswHookMenu Constant",
        "hi def link jswProccodeHole SpecialChar",
        "hi def link jswDeclaration Keyword",
        "hi def link jswModifier StorageClass",
        "hi def link jswType Type",
        "hi def link jswWordOperator Operator",
        "hi def link jswLiteral Constant",
        "hi def link jswReserved Keyword",
        "hi def link jswNumber Number",
        "hi def link jswString String",
        "hi def link jswChar String",
        "hi def link jswEscape SpecialChar",
        "hi def link jswFString String",
        "hi def link jswHole Special",
        "hi def link jswOperator Operator",
        "hi def link jswDelimiter Delimiter",
        "hi def link jswContinuation SpecialChar",
        "hi def link jswNoTernary Error",
        "hi def link jswComment Comment",
        "hi def link jswAnnotationWord SpecialComment",
        "hi def link jswTodo Todo",
        "hi def link jswDirective PreProc",
        "hi def link jswFeature PreProc",
        "hi def link jswTarget PreProc",
        "hi def link jswTargetKey Identifier",
        "hi def link jswTargetValue Constant",
        "hi def link jswMetadata Statement",
        "hi def link jswMonitorMode Constant",
        "hi def link jswMetadataFlag Constant",
        "hi def link jswMetadataVerb Keyword",
        "",
        "let b:current_syntax = 'jsw'",
        "",
    ]
    return "\n".join(lines)


FTDETECT = """\
\" JadenScript: Neovim's filetype detection, generated by tools/gen_vim_syntax.py.
augroup jsw_filetype
  autocmd!
  autocmd BufRead,BufNewFile *.jsw setfiletype jsw
augroup END
"""

FTPLUGIN = """\
\" JadenScript file settings, generated by tools/gen_vim_syntax.py.
setlocal commentstring=//\\ %s
setlocal comments=://
setlocal suffixesadd=.jsw,.jsa,.jsl,.jsv
setlocal iskeyword+=$,_,.
let b:undo_ftplugin = 'setlocal commentstring< comments< suffixesadd< iskeyword<'
"""


def main() -> int:
    files = {
        PLUGIN / "syntax" / "jsw.vim": syntax_file(),
        PLUGIN / "ftdetect" / "jsw.vim": FTDETECT,
        PLUGIN / "ftplugin" / "jsw.vim": FTPLUGIN,
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(f"wrote {len(files)} files under {PLUGIN.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
