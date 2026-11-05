"""AST for JadenScript.

Deliberately tiny and data-only: the code generator pattern-matches on these
classes, and the ``ast`` CLI command dumps them with :func:`dataclasses.asdict`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    line: int = 0
    col: int = 0


# --------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------
@dataclass
class Expr(Node):
    pass


@dataclass
class Name(Expr):
    """A dotted path such as ``Player.notes`` or a bare local ``i``."""

    parts: tuple[str, ...] = ()

    def __str__(self) -> str:
        return ".".join(self.parts)


@dataclass
class NumberLit(Expr):
    raw: str = ""


@dataclass
class StringLit(Expr):
    value: str = ""


@dataclass
class BoolLit(Expr):
    value: bool = False


@dataclass
class NullLit(Expr):
    pass


@dataclass
class AnyLit(Expr):
    """The ``any`` keyword: the "any key" menu option.

    It compiles to the text ``any`` wherever it appears. Used as an argument it
    is almost always a key dropdown; anywhere else it is probably a typo, so
    the compiler says so.
    """


@dataclass
class Unary(Expr):
    op: str = ""
    operand: Expr | None = None


@dataclass
class Binary(Expr):
    op: str = ""
    left: Expr | None = None
    right: Expr | None = None


@dataclass
class Ternary(Expr):
    cond: Expr | None = None
    then: Expr | None = None
    otherwise: Expr | None = None


@dataclass
class Member(Expr):
    receiver: Expr | None = None
    name: str = ""


@dataclass
class FString(Expr):
    """``f"a{x}b"`` — parts are ``str`` for literal text and ``Expr`` holes."""

    parts: list[Any] = field(default_factory=list)


@dataclass
class ListLit(Expr):
    items: list[Expr] = field(default_factory=list)


@dataclass
class Index(Expr):
    receiver: Expr | None = None
    index: Expr | None = None


@dataclass
class Call(Expr):
    receiver: Expr | None = None
    name: str = ""
    args: list[Expr] = field(default_factory=list)


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------
@dataclass
class Stmt(Node):
    pass


@dataclass
class Assign(Stmt):
    target: Name | None = None
    op: str | None = "="
    value: Expr | None = None
    visibility: str | None = None


@dataclass
class ExprStmt(Stmt):
    expr: Expr | None = None


@dataclass
class IfStmt(Stmt):
    cond: Expr | None = None
    body: list[Stmt] = field(default_factory=list)
    elifs: list[tuple[Expr, list[Stmt]]] = field(default_factory=list)
    orelse: list[Stmt] | None = None


@dataclass
class RepeatStmt(Stmt):
    count: Expr | None = None
    body: list[Stmt] = field(default_factory=list)


@dataclass
class WhileStmt(Stmt):
    cond: Expr | None = None
    body: list[Stmt] = field(default_factory=list)


@dataclass
class ForStmt(Stmt):
    init: Stmt | None = None
    cond: Expr | None = None
    update: Stmt | None = None
    body: list[Stmt] = field(default_factory=list)


@dataclass
class RangeFor(Stmt):
    """``for i in 1..10 { … }`` — an inclusive ascending count.

    Lowered to a while loop: ``set i = start; repeat until i > stop { …; i += 1 }``.
    The loop variable follows whatever the name already means (a thread (temp)
    variable for ``_name``, a stage variable for a public one, otherwise a
    sprite-local).
    """

    var: tuple[str, ...] = ()
    start: Expr | None = None
    stop: Expr | None = None
    body: list[Stmt] = field(default_factory=list)


@dataclass
class Case:
    value: Expr | None = None
    body: list[Stmt] = field(default_factory=list)
    line: int = 0


@dataclass
class SwitchStmt(Stmt):
    value: Expr | None = None
    cases: list[Case] = field(default_factory=list)
    default: list[Stmt] | None = None


@dataclass
class BreakStmt(Stmt):
    pass


@dataclass
class ContinueStmt(Stmt):
    pass


@dataclass
class ReturnStmt(Stmt):
    value: Expr | None = None


@dataclass
class SubstackStmt(Stmt):
    """``Comments.commentC("why") { … }`` — a call that owns a C-block body."""

    expr: Expr | None = None
    body: list[Stmt] = field(default_factory=list)


@dataclass
class CallStmt(Stmt):
    """``call f();`` — fire and forget, lowered to a broadcast."""

    expr: Call | None = None


# --------------------------------------------------------------------------
# Declarations
# --------------------------------------------------------------------------
@dataclass
class SpriteMeta:
    name: str = ""
    x: float = 0
    y: float = 0
    size: float = 100
    direction: float = 90
    visible: bool = True
    namespace: str | None = None
    costume: int | None = None
    volume: float | None = None
    layer: int | None = None
    rotation: str | None = None
    draggable: bool | None = None


@dataclass
class ImportDecl(Stmt):
    """``import "assets/piano.png" as piano;`` — embeds a costume or sound."""

    path: str = ""
    name: str | None = None
    annotation: str | None = None


@dataclass
class LooseDecl(Node):
    """``loose { … }`` — blocks that sit on the canvas without a hat."""

    body: list[Stmt] = field(default_factory=list)
    annotation: str | None = None


@dataclass
class FuncDecl(Node):
    visibility: str = "private"
    name: tuple[str, ...] = ()
    params: list[str] = field(default_factory=list)
    body: list[Stmt] = field(default_factory=list)
    annotation: str | None = None
    is_init: bool = False

    @property
    def proccode(self) -> str:
        return self.name[-1]

    @property
    def qualified(self) -> str:
        return ".".join(self.name)


@dataclass
class SpriteMetaDecl(Stmt):
    """``player: x=0, y=0, size=100;`` — standalone target metadata.

    ``stage: costume=0, …;`` uses the same shape: Scratch keeps the same
    handful of properties on the Stage as on a sprite.
    """

    sprite: str = ""
    values: dict[str, Expr] = field(default_factory=dict)


@dataclass
class MetaDecl(Stmt):
    """``metadata <name> pos 539,64;`` — canvas metadata that has no block.

    ``kind`` is ``"pos"`` for a script position, and ``"line"`` for anything
    that maps onto a monitor / workspace comment, where ``text`` holds the
    canonical form the comment spelling used (``monitor Stage.score default
    at 0,0 100x24 shown``).
    """

    kind: str = ""
    target: str = ""
    values: dict[str, float] = field(default_factory=dict)
    text: str = ""


@dataclass
class ClassDecl(Node):
    name: str = ""
    sprite: SpriteMeta | None = None
    params: dict[str, Expr] = field(default_factory=dict)
    members: list[Any] = field(default_factory=list)

    @property
    def funcs(self) -> list[FuncDecl]:
        return [m for m in self.members if isinstance(m, FuncDecl)]


@dataclass
class StageDecl(Node):
    """``stage { … }`` — scripts that belong to the Stage itself."""

    members: list[Any] = field(default_factory=list)
    annotation: str | None = None

    @property
    def funcs(self) -> list[FuncDecl]:
        return [m for m in self.members if isinstance(m, FuncDecl)]


@dataclass
class Program(Node):
    classes: list[ClassDecl] = field(default_factory=list)
    features: set[str] = field(default_factory=set)
    globals: list[Stmt] = field(default_factory=list)      # top-level Assign
    imports: list[ImportDecl] = field(default_factory=list)  # top-level .jsv/.jsa/.jsl
    sprite_meta: list[SpriteMetaDecl] = field(default_factory=list)
    #: ``metadata`` statements, in source order.
    meta: list[MetaDecl] = field(default_factory=list)
    monitors: list[str] = field(default_factory=list)      # ``// monitor`` comments
    notes: list[str] = field(default_factory=list)         # ``// comment`` workspace notes
    stage: StageDecl | None = None
