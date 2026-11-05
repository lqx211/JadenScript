"""Hand-written scanner for JadenScript.

The language is newline-sensitive (statements may end with ``;`` or a line
break) but newlines inside ``(...)`` or ``[...]`` are ignored so that long
block calls can be wrapped, exactly like the reference ``sample.jsw``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .keywords import ALL_KEYWORDS, OPERATORS, PUNCTUATION
from .tokens import Comment, Kind, LexError, Token

_OPERATORS = sorted(OPERATORS, key=len, reverse=True)
_PUNCTUATION = set(PUNCTUATION)
_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$")
_IDENT_BODY = _IDENT_START | set("0123456789")
_DIGITS = set("0123456789")
_HEX = set("0123456789abcdefABCDEF")

_SIMPLE_ESCAPES = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "0": "\0",
    "\\": "\\",
    '"': '"',
    "'": "'",
}


@dataclass
class LexResult:
    tokens: list[Token] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    #: The text that was tokenised. ``metadata`` statements carry free-form
    #: tails (``at 0,0 100x24 shown``), which the parser slices back out.
    source: str = ""


def tokenize(source: str) -> LexResult:
    result = _Lexer(source).run()
    result.source = source
    return result


class _Lexer:
    def __init__(self, source: str):
        self.src = source
        self.n = len(source)
        self.i = 0
        self.line = 1
        self.col = 1
        self.depth = 0
        self.result = LexResult()

    # -- low level helpers -------------------------------------------------
    def _peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.src[j] if j < self.n else ""

    def _advance(self, count: int = 1) -> str:
        chunk = self.src[self.i : self.i + count]
        for ch in chunk:
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        self.i += count
        return chunk

    def _emit(self, kind: Kind, value: str, line: int, col: int, raw: str = "") -> None:
        self.result.tokens.append(Token(kind, value, line, col, raw or value))

    def _error(self, code: str, message: str, line: int | None = None,
               col: int | None = None):
        raise LexError(message, line or self.line, col or self.col, code=code)

    # -- scanner -----------------------------------------------------------
    def run(self) -> LexResult:
        while self.i < self.n:
            ch = self._peek()

            if ch in " \t\f\v":
                self._advance()
                continue

            if ch == "\r":
                self._advance()
                continue

            if ch == "\n":
                line, col = self.line, self.col
                self._advance()
                # Inside parentheses a line break is just whitespace.
                if self.depth == 0:
                    prev = self.result.tokens[-1] if self.result.tokens else None
                    if prev is not None and prev.kind is not Kind.NEWLINE:
                        self._emit(Kind.NEWLINE, "\n", line, col)
                continue

            if ch == "\\" and self._peek(1) in "\r\n":
                # Explicit line continuation: swallow the backslash and the
                # newline so the expression keeps going.
                self._advance()
                if self._peek() == "\r":
                    self._advance()
                if self._peek() == "\n":
                    self._advance()
                continue

            if ch == "/" and self._peek(1) == "/":
                self._line_comment()
                continue

            if ch == "/" and self._peek(1) == "*":
                self._block_comment()
                continue

            if ch in "fF" and self._peek(1) in "\"'":
                self._fstring()
                continue

            if ch == '"':
                self._string('"', Kind.STRING)
                continue

            if ch == "'":
                self._string("'", Kind.CHAR)
                continue

            if ch in _DIGITS or (ch == "." and self._peek(1) in _DIGITS):
                self._number()
                continue

            if ch in _IDENT_START:
                self._ident()
                continue

            if ch in "([":
                self.depth += 1
                line, col = self.line, self.col
                self._advance()
                self._emit(Kind.PUNCT, ch, line, col)
                continue

            if ch in ")]":
                self.depth = max(0, self.depth - 1)
                line, col = self.line, self.col
                self._advance()
                self._emit(Kind.PUNCT, ch, line, col)
                continue

            if self._operator_or_punct():
                continue

            self._error(
                "E0001",
                f"unexpected character {ch!r}",
            )

        self._emit(Kind.EOF, "", self.line, self.col)
        return self.result

    def _line_comment(self) -> None:
        line, col = self.line, self.col
        start = self.i
        while self.i < self.n and self._peek() != "\n":
            self._advance()
        text = self.src[start + 2 : self.i].strip()
        self.result.comments.append(Comment(text, line, col))

    def _block_comment(self) -> None:
        line, col = self.line, self.col
        start = self.i
        self._advance(2)
        while self.i < self.n and not (self._peek() == "*" and self._peek(1) == "/"):
            self._advance()
        if self.i >= self.n:
            self._error("E0004", "block comment opened here is never closed", line, col)
        self._advance(2)
        text = self.src[start + 2 : self.i - 2].strip()
        self.result.comments.append(Comment(text, line, col, block=True))

    def _string(self, quote: str, kind: Kind) -> None:
        line, col = self.line, self.col
        self._advance()
        out: list[str] = []
        while True:
            if self.i >= self.n:
                self._error("E0002", "string literal opened here is never closed", line, col)
            ch = self._peek()
            if ch == quote:
                self._advance()
                break
            if ch == "\n":
                self._error(
                    "E0002",
                    "string literal runs to the end of the line without a closing quote",
                    line,
                    col,
                )
            if ch == "\\":
                out.append(self._escape())
                continue
            out.append(self._advance())
        self._emit(kind, "".join(out), line, col, raw=quote)

    def _fstring(self) -> None:
        """``f"text {expr} more"`` — keeps the raw text for the parser."""
        line, col = self.line, self.col
        self._advance()  # the leading f
        quote = self._peek()
        self._advance()
        start = self.i
        depth = 0
        while True:
            if self.i >= self.n:
                self._error("E0003", "f-string opened here is never closed", line, col)
            ch = self._peek()
            if ch == "\\":
                self._advance(2)
                continue
            if ch == "\n":
                self._error(
                    "E0003",
                    "f-string runs to the end of the line without a closing quote",
                    line,
                    col,
                )
            if ch == "{":
                if self._peek(1) == "{":
                    self._advance(2)
                    continue
                depth += 1
            elif ch == "}":
                if self._peek(1) == "}" and depth == 0:
                    self._advance(2)
                    continue
                depth -= 1
            elif ch == quote and depth == 0:
                text = self.src[start : self.i]
                self._advance()
                self._emit(Kind.FSTRING, text, line, col, raw=quote)
                return
            self._advance()

    def _escape(self) -> str:
        line, col = self.line, self.col
        self._advance()  # backslash
        ch = self._advance()
        if ch in _SIMPLE_ESCAPES:
            return _SIMPLE_ESCAPES[ch]
        if ch == "x":
            digits = self._advance(2)
            if len(digits) != 2 or any(d not in _HEX for d in digits):
                self._error(
                    "E0005",
                    f"\\x takes two hex digits, found {digits!r}",
                    line,
                    col,
                )
            return chr(int(digits, 16))
        if ch == "u":
            digits = self._advance(4)
            if len(digits) != 4 or any(d not in _HEX for d in digits):
                self._error(
                    "E0006",
                    f"\\u takes four hex digits, found {digits!r}",
                    line,
                    col,
                )
            return chr(int(digits, 16))
        # Unknown escapes keep the raw character, matching JS behaviour.
        return ch

    def _number(self) -> None:
        line, col = self.line, self.col
        start = self.i
        if self._peek() == "0" and self._peek(1) and self._peek(1) in "xXbBoO":
            prefix = self._peek(1).lower()
            digits = {"x": _HEX, "b": "01", "o": "01234567"}[prefix]
            self._advance(2)
            first_digit = self.i
            while self._peek() in digits:
                self._advance()
            if self.i == first_digit:
                self._error(
                    "E0007",
                    f"0{prefix} is a base prefix and needs at least one digit"
                    + (" (0-9, a-f)" if prefix == "x" else ""),
                    line,
                    col,
                )
            self._emit(Kind.NUMBER, self.src[start : self.i], line, col)
            return
        while self._peek() in _DIGITS:
            self._advance()
        if self._peek() == "." and self._peek(1) in _DIGITS:
            self._advance()
            while self._peek() in _DIGITS:
                self._advance()
        if self._peek() in "eE":
            save_i, save_col = self.i, self.col
            self._advance()
            if self._peek() in "+-":
                self._advance()
            if self._peek() in _DIGITS:
                while self._peek() in _DIGITS:
                    self._advance()
            else:  # not an exponent after all
                self.i, self.col = save_i, save_col
        self._emit(Kind.NUMBER, self.src[start : self.i], line, col)

    def _ident(self) -> None:
        line, col = self.line, self.col
        start = self.i
        while self._peek() in _IDENT_BODY:
            self._advance()
        word = self.src[start : self.i]
        kind = Kind.KEYWORD if word in ALL_KEYWORDS else Kind.IDENT
        self._emit(kind, word, line, col)

    def _operator_or_punct(self) -> bool:
        for op in _OPERATORS:
            if self.src.startswith(op, self.i):
                line, col = self.line, self.col
                self._advance(len(op))
                self._emit(Kind.OP, op, line, col)
                return True
        ch = self._peek()
        if ch in _PUNCTUATION:
            line, col = self.line, self.col
            self._advance()
            self._emit(Kind.PUNCT, ch, line, col)
            return True
        return False
