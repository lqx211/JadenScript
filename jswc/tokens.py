"""Token model shared by the lexer, parser and syntax-highlighting generator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    IDENT = "ident"
    KEYWORD = "keyword"
    NUMBER = "number"
    STRING = "string"
    FSTRING = "fstring"
    CHAR = "char"
    OP = "op"
    PUNCT = "punct"
    NEWLINE = "newline"
    EOF = "eof"


@dataclass(frozen=True, slots=True)
class Token:
    kind: Kind
    value: str
    line: int
    col: int
    raw: str = ""

    def is_(self, kind: Kind, value: str | None = None) -> bool:
        return self.kind is kind and (value is None or self.value == value)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.kind.value}:{self.value!r}@{self.line}:{self.col}"


@dataclass(frozen=True, slots=True)
class Comment:
    text: str
    line: int
    col: int
    block: bool = False


class JswError(Exception):
    """Base error carrying a source position and a catalogue code.

    The code is part of the message text (``"E0035 undefined variable 'x'"``)
    so it survives the CLI's string-based diagnostic plumbing; ``diagnostics``
    turns it back into a title and a hint when the message is rendered.
    """

    code: str = ""

    def __init__(self, message: str, line: int = 0, col: int = 0, code: str = ""):
        code = code or self.code
        self.code = code
        self.message = f"{code} {message}" if code else message
        self.line = line
        self.col = col
        suffix = f" ({line}:{col})" if line else ""
        super().__init__(f"{self.message}{suffix}")


class LexError(JswError):
    pass


class ParseError(JswError):
    pass


class CompileError(JswError):
    pass
