"""Variable planning: initial state, visibility and definition checks.

Implements the rules from ``syntax.md``:

* every public/private variable must be **defined** somewhere; reading an
  undefined variable is a compile-time error;
* the **first** ``=`` assignment in document order (top to bottom) defines the
  variable's initial value, later assignments only change it at runtime;
* ``public`` variables live on the Stage (visible to every sprite), ``private``
  ones live on their class' sprite;
* ``private _name`` inside a function is a **temp** variable when the
  ``useTempVariables`` feature is on (thread-local, released when the function
  returns);
* ``import "x.jsv"`` / ``"x.jsa"`` / ``"x.jsl"`` seeds a variable or list from a
  file.
"""

from __future__ import annotations

from typing import NoReturn

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import ast_nodes as A
from . import opcodes as O
from .keywords import BUILTIN_NAMESPACES
from .diagnostics import split_code
from .tokens import CompileError


@dataclass
class VarDef:
    name: str
    initial: object = 0
    public: bool = False
    is_temp: bool = False
    node: A.Node | None = None
    from_import: bool = False
    file_scope: bool = False


@dataclass
class VarPlan:
    globals: dict[str, VarDef] = field(default_factory=dict)
    class_vars: dict[str, dict[str, VarDef]] = field(default_factory=dict)
    lists: dict[str, list] = field(default_factory=dict)
    imported_lists: set[str] = field(default_factory=set)
    file_scope_lists: set[str] = field(default_factory=set)
    class_lists: dict[str, dict[str, list]] = field(default_factory=dict)
    #: Bare name -> namespace that declared it as ``public`` inside a class.
    #: A file-scope ``public i = 0`` is *not* in here: it is a plain Stage
    #: variable and must never grow a sprite prefix.
    namespaced: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def class_table(self, class_name: str) -> dict[str, VarDef]:
        return self.class_vars.setdefault(class_name, {})


def literal_text(expr: A.Expr | None) -> str | None:
    """Return the project.json text for a compile-time constant, else ``None``."""
    if expr is None:
        return None
    if isinstance(expr, A.NumberLit):
        return expr.raw
    if isinstance(expr, A.StringLit):
        return expr.value
    if isinstance(expr, A.BoolLit):
        return "1" if expr.value else "0"
    if isinstance(expr, A.NullLit):
        return ""
    if isinstance(expr, A.AnyLit):
        return "any"
    if isinstance(expr, A.Unary) and expr.op == "-" and isinstance(expr.operand, A.NumberLit):
        try:
            return _format_number(-float(expr.operand.raw))
        except ValueError:
            return None
    return None


def literal_value(expr: A.Expr | None):
    """Compile-time value: numbers stay numbers, everything else is text."""
    if expr is None:
        return None
    if isinstance(expr, A.NumberLit):
        try:
            number = float(expr.raw)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    if isinstance(expr, A.Unary) and expr.op == "-" and isinstance(expr.operand, A.NumberLit):
        try:
            number = -float(expr.operand.raw)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return literal_text(expr)


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else repr(value)


def _literal_items(literal: A.ListLit) -> list | None:
    items = []
    for expr in literal.items:
        if isinstance(expr, A.NumberLit):
            try:
                number = float(expr.raw)
            except ValueError:
                return None
            items.append(int(number) if number.is_integer() else number)
        elif isinstance(expr, A.BoolLit):
            items.append(1 if expr.value else 0)
        elif isinstance(expr, A.Unary) and expr.op == "-" and isinstance(expr.operand, A.NumberLit):
            try:
                items.append(-float(expr.operand.raw))
            except ValueError:
                return None
        else:
            text = literal_text(expr)
            if text is None:
                return None
            items.append(text)
    return items


def iter_statements(body):
    """Yield every statement in document order, descending into blocks."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, A.IfStmt):
            yield from iter_statements(stmt.body)
            for _, block in stmt.elifs:
                yield from iter_statements(block)
            if stmt.orelse:
                yield from iter_statements(stmt.orelse)
        elif isinstance(stmt, A.WhileStmt):
            yield from iter_statements(stmt.body)
        elif isinstance(stmt, A.ForStmt):
            if stmt.init is not None:
                yield stmt.init
            yield from iter_statements(stmt.body)
            if stmt.update is not None:
                yield stmt.update
        elif isinstance(stmt, A.RangeFor):
            yield from iter_statements(stmt.body)
        elif isinstance(stmt, A.SwitchStmt):
            for case in stmt.cases:
                yield from iter_statements(case.body)
            if stmt.default:
                yield from iter_statements(stmt.default)


def _iter_expressions(node):
    """Yield every expression node inside ``node`` (recursively)."""
    if isinstance(node, A.Expr):
        yield node
        for f in node.__dataclass_fields__.values():
            value = getattr(node, f.name)
            if isinstance(value, A.Node):
                yield from _iter_expressions(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, A.Node):
                        yield from _iter_expressions(item)
    elif isinstance(node, A.Node):
        for f in node.__dataclass_fields__.values():
            value = getattr(node, f.name)
            if isinstance(value, A.Node):
                yield from _iter_expressions(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, A.Node):
                        yield from _iter_expressions(item)
                    elif isinstance(item, tuple):
                        for sub in item:
                            if isinstance(sub, A.Node):
                                yield from _iter_expressions(sub)


def collect_list_names(program: A.Program) -> set[str]:
    """Dotted names used with list methods.

    A namespace such as ``JSON.get(...)`` looks exactly like a list call, so
    namespaces are excluded — otherwise ``JSON`` would become a list.
    """
    from .extensions import known_namespaces

    namespaces = BUILTIN_NAMESPACES | known_namespaces()
    names: set[str] = set()
    for cls in program.classes:
        for node in _all_nodes(cls):
            if isinstance(node, A.Call) and isinstance(node.receiver, A.Name):
                if node.name in O.LIST_METHODS:
                    full = ".".join(node.receiver.parts)
                    if node.receiver.parts[0] not in namespaces:
                        names.add(full)
    return names


def _all_nodes(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for f in node.__dataclass_fields__.values():
            value = getattr(node, f.name)
            if isinstance(value, A.Node):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(item for item in value if isinstance(item, A.Node))
                for item in value:
                    if isinstance(item, tuple):
                        stack.extend(sub for sub in item if isinstance(sub, A.Node))


# Console statements that read like a bare name (see jswc/codegen.py).
_CONSOLE_BARE = {"breakpoint"}


def _is_console_statement(expr) -> bool:
    return isinstance(expr, A.Name) and len(expr.parts) == 1 and expr.parts[0] in _CONSOLE_BARE


class VarPlanner:
    def __init__(
        self,
        program: A.Program,
        base_dir: Path | str | None = None,
        allow_undefined: bool = False,
        features: set[str] | None = None,
    ):
        self.program = program
        self.base_dir = Path(base_dir) if base_dir is not None else Path.cwd()
        self.allow_undefined = allow_undefined
        self.features: set[str] = set(program.features) | set(features or ())
        self.plan = VarPlan()
        self.list_names = collect_list_names(program)
        self._checking_class: str = ""

    @property
    def temp_locals(self) -> bool:
        """``_x`` means a thread (temp) variable only with ``useTempVariables``."""
        return "useTempVariables" in self.features

    # -- entry -------------------------------------------------------------
    def run(self) -> VarPlan:
        self._program_level()
        for cls in self.program.classes:
            self._class_level(cls)
        self._check_reads()
        if self.plan.errors and not self.allow_undefined:
            raise_plan_error(self.plan.errors[0])
        return self.plan

    # -- definition collection --------------------------------------------
    def _program_level(self) -> None:
        for stmt in self.program.globals:
            if (
                isinstance(stmt, A.Assign)
                and stmt.op == "="
                and isinstance(stmt.target, A.Name)
            ):
                parts = stmt.target.parts
                if isinstance(stmt.value, A.ListLit):
                    items = _literal_items(stmt.value)
                    if items is not None:
                        name = ".".join(parts)
                        self.plan.lists[name] = items
                        self.plan.file_scope_lists.add(name)
                    continue
                self._define(parts, stmt.value, public=True, node=stmt, file_scope=True)
        for decl in self.program.imports:
            self._import_global(decl)

    def _namespace_of(self, cls: A.ClassDecl) -> str:
        """The prefix the class declares with ``namespace=`` (``ns=``) or ``""``."""
        if "namespacedNames" not in self.features:
            return ""
        value = None
        sprite = getattr(cls, "sprite", None)
        if sprite is not None and "commentMetadata" in self.features:
            value = getattr(sprite, "namespace", None)
        for decl in self.program.sprite_meta:
            if decl.sprite == cls.name:
                value = decl.values.get("namespace") or value
        if value is None:
            value = cls.params.get("namespace")
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, A.StringLit):
            return value.value
        if isinstance(value, A.Name) and len(value.parts) == 1:
            return value.parts[0]
        return str(getattr(value, "raw", ""))

    def _alias_namespaced(self, cls: A.ClassDecl, name: str) -> None:
        """Also register ``Ns.name`` so another class can read it.

        Inside a namespaced class the member is written bare (``public zh``)
        and codegen re-adds the prefix when it emits the block; a *read* from
        elsewhere is written ``Lyrics.zh``, so the planner has to know the
        qualified spelling too.
        """
        namespace = self._namespace_of(cls)
        if not namespace or isinstance(name, tuple):
            return
        self.plan.namespaced.setdefault(name, namespace)
        qualified = f"{namespace}.{name}"
        if qualified in self.plan.globals or qualified in self.plan.lists:
            return
        if name in self.plan.globals and name not in self.plan.class_table(cls.name):
            self.plan.globals[qualified] = self.plan.globals[name]
        elif name in self.plan.lists:
            self.plan.lists[qualified] = self.plan.lists[name]

    def _class_level(self, cls: A.ClassDecl) -> None:
        table = self.plan.class_table(cls.name)
        for member in cls.members:
            if isinstance(member, A.ImportDecl):
                # Costumes and sounds belong to the asset importer; ``.jsv`` /
                # ``.jsa`` / ``.jsl`` imports declare state of this sprite.
                self._import_class_state(cls, member)
                continue
            if isinstance(member, A.Assign):
                self._define_in_class(cls, table, member)
            elif isinstance(member, A.FuncDecl):
                for stmt in iter_statements(member.body):
                    if isinstance(stmt, A.Assign):
                        self._define_in_class(cls, table, stmt)

    def _define_in_class(self, cls: A.ClassDecl, table: dict[str, VarDef], stmt: A.Assign) -> None:
        # ``list[i] = v`` replaces an item; it never declares anything.
        if stmt.op != "=" or not isinstance(stmt.target, A.Name):
            return
        parts = stmt.target.parts
        if isinstance(stmt.value, A.ListLit):
            items = _literal_items(stmt.value)
            if items is None:
                return
            if len(parts) > 1 or stmt.visibility == "public":
                name = ".".join(parts)
                self.plan.lists[name] = items
                if len(parts) == 1:
                    self._alias_namespaced(cls, name)
            else:
                self.plan.class_lists.setdefault(cls.name, {})[parts[0]] = items
            return
        if len(parts) > 1:
            self._define(parts, stmt.value, public=True, node=stmt)
            return
        name = parts[0]
        public = stmt.visibility == "public"
        if public:
            if name in table:
                previous = table.pop(name)
                self.plan.warnings.append(
                    f"{stmt.line}:{stmt.col}: W0011 {name!r} was declared private and public; "
                    "treating it as a global variable"
                )
                if name not in self.plan.globals:
                    self.plan.globals[name] = VarDef(
                        name=name, initial=previous.initial, public=True, node=previous.node
                    )
                self._alias_namespaced(cls, name)
                return
            self._define(parts, stmt.value, public=True, node=stmt)
            self._alias_namespaced(cls, name)
            return
        if name in self.plan.globals or name in table:
            # Either an existing public variable (plain writes just target it)
            # or an implicit re-declaration: first allocation always wins.
            if stmt.visibility == "private" and name in self.plan.globals:
                self.plan.warnings.append(
                    f"{stmt.line}:{stmt.col}: W0011 {name!r} is already a public variable; "
                    "the private declaration is ignored"
                )
            return
        # ``private _x;`` declares a local. Scratch clones copy sprite-local
        # variables, so a plain sprite variable already behaves like a local;
        # only ``useTempVariables`` upgrades it to a per-thread variable (which
        # also survives recursive calls).
        is_temp = self.temp_locals and name.startswith("_")
        initial = literal_value(stmt.value)
        if initial is None:
            initial = 0
            if not is_temp and not name.startswith("_") and stmt.op is not None:
                # Temp variables have no persistent state; only real locals need
                # a literal initial value. ``_name`` is the convention for
                # scratch across a procedure, and it is assigned before use.
                self.plan.warnings.append(
                    f"{stmt.line}:{stmt.col}: W0012 initial value of {name!r} is not a "
                    "literal; starts at 0"
                )
        table[name] = VarDef(
            name=name, initial=initial, public=False, is_temp=is_temp, node=stmt
        )

    def _define(
        self,
        parts: tuple[str, ...],
        value: A.Expr | None,
        public: bool,
        node: A.Node,
        file_scope: bool = False,
    ) -> None:
        if not parts:
            return
        name = ".".join(parts)
        if name in O.PROPERTIES or name in O.REPORTERS:
            return  # ``Motion.x = …`` writes a Scratch property, not a variable
        if len(parts) == 1 and name in self.plan.globals:
            return
        if name in self.plan.globals:
            return
        initial = literal_value(value)
        if initial is None:
            initial = 0
            self.plan.warnings.append(
                f"{node.line}:{node.col}: W0012 initial value of {name!r} is not a literal; "
                "starts at 0"
            )
        self.plan.globals[name] = VarDef(
            name=name, initial=initial, public=True, node=node, file_scope=file_scope
        )

    def _import_class_state(self, cls: A.ClassDecl, decl: A.ImportDecl) -> None:
        suffix = Path(decl.path).suffix.lower()
        if suffix not in (".jsv", ".jsa", ".jsl") or not decl.name:
            return
        path = Path(decl.path)
        if not path.is_absolute():
            path = self.base_dir / path
        name = decl.name
        if not path.exists():
            raise CompileError(
                f"cannot import {decl.path!r}: file not found (looked in {path})",
                decl.line,
                decl.col,
                code="E0058",
            )
        text = path.read_text(encoding="utf-8")
        if suffix == ".jsv":
            self.plan.class_vars.setdefault(cls.name, {})[name] = VarDef(
                name=name,
                initial=text.rstrip("\n"),
                public=False,
                node=decl,
                from_import=True,
            )
            return
        if suffix == ".jsa":
            try:
                items = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CompileError(
                    f"cannot import {decl.path!r}: invalid JSON ({exc.msg})",
                    decl.line,
                    decl.col,
                    code="E0059",
                ) from exc
            if not isinstance(items, list):
                raise CompileError(
                    f"cannot import {decl.path!r}: expected a JSON array",
                    decl.line,
                    decl.col,
                    code="E0060",
                )
        else:
            items = text.splitlines()
        self.plan.class_lists.setdefault(cls.name, {})[name] = items

    # -- imports -----------------------------------------------------------
    def _import_global(self, decl: A.ImportDecl) -> None:
        path = Path(decl.path)
        if not path.is_absolute():
            path = self.base_dir / path
        name = decl.name or path.stem
        if name in self.plan.globals or name in self.plan.lists:
            self.plan.warnings.append(
                f"{decl.line}:{decl.col}: W0014 {name!r} is already defined; the import "
                "is ignored"
            )
            return
        suffix = path.suffix.lower()
        if not path.exists():
            raise CompileError(
                f"cannot import {decl.path!r}: file not found (looked in {path})",
                decl.line,
                decl.col,
                code="E0058",
            )
        if suffix == ".jsv":
            value = path.read_text(encoding="utf-8")
            self.plan.globals[name] = VarDef(
                name=name, initial=value.rstrip("\n"), public=True, node=decl, from_import=True
            )
            return
        if suffix in (".jsa", ".jsl"):
            text = path.read_text(encoding="utf-8")
            if suffix == ".jsa":
                try:
                    items = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise CompileError(
                        f"cannot import {decl.path!r}: invalid JSON ({exc.msg})",
                        decl.line,
                        decl.col,
                        code="E0059",
                    ) from exc
                if not isinstance(items, list):
                    raise CompileError(
                        f"cannot import {decl.path!r}: expected a JSON array",
                        decl.line,
                        decl.col,
                        code="E0060",
                    )
            else:
                items = [line for line in text.splitlines()]
            self.plan.lists[name] = items
            self.plan.imported_lists.add(name)
            return
        raise CompileError(
            f"cannot import {decl.path!r}: expected .jsv, .jsa or .jsl",
            decl.line,
            decl.col,
            code="E0061",
        )

    # -- read checking -----------------------------------------------------
    def _check_reads(self) -> None:
        for stmt in self.program.globals:
            if isinstance(stmt, A.Assign):
                self._check_expr(stmt.value, None, set())
        for cls in self.program.classes:
            self._checking_class = cls.name
            table = self.plan.class_table(cls.name)
            for member in cls.members:
                if isinstance(member, A.Assign):
                    self._check_expr(member.value, table, set())
                    if isinstance(member.target, A.Index):
                        self._check_expr(member.target, table, set())
                elif isinstance(member, A.FuncDecl):
                    self._check_body(member.body, table, set(member.params))

    def _check_body(self, body, table, params) -> None:
        for stmt in body:
            if isinstance(stmt, A.Assign):
                self._check_expr(stmt.value, table, params)
                if isinstance(stmt.target, A.Index):
                    self._check_expr(stmt.target, table, params)
            elif isinstance(stmt, A.ExprStmt):
                if _is_console_statement(stmt.expr):
                    continue  # ``breakpoint;`` is a statement, not a variable
                self._check_expr(stmt.expr, table, params)
            elif isinstance(stmt, A.CallStmt):
                self._check_expr(stmt.expr, table, params)
            elif isinstance(stmt, A.ReturnStmt):
                self._check_expr(stmt.value, table, params)
            elif isinstance(stmt, A.IfStmt):
                self._check_expr(stmt.cond, table, params)
                self._check_body(stmt.body, table, params)
                for condition, block in stmt.elifs:
                    self._check_expr(condition, table, params)
                    self._check_body(block, table, params)
                if stmt.orelse:
                    self._check_body(stmt.orelse, table, params)
            elif isinstance(stmt, A.WhileStmt):
                self._check_expr(stmt.cond, table, params)
                self._check_body(stmt.body, table, params)
            elif isinstance(stmt, A.ForStmt):
                if stmt.init is not None:
                    self._check_body([stmt.init], table, params)
                self._check_expr(stmt.cond, table, params)
                if stmt.update is not None:
                    self._check_body([stmt.update], table, params)
                self._check_body(stmt.body, table, params)
            elif isinstance(stmt, A.RangeFor):
                self._check_expr(stmt.start, table, params)
                self._check_expr(stmt.stop, table, params)
                # The counter is declared by the loop itself, so the body may
                # read it without a separate declaration.
                self._check_body(stmt.body, table, params | {stmt.var[-1]})
            elif isinstance(stmt, A.SwitchStmt):
                self._check_expr(stmt.value, table, params)
                for case in stmt.cases:
                    self._check_expr(case.value, table, params)
                    self._check_body(case.body, table, params)
                if stmt.default:
                    self._check_body(stmt.default, table, params)

    def _check_expr(self, expr, table, params) -> None:
        if expr is None:
            return
        if isinstance(expr, A.Name):
            self._check_name(expr, table, params)
        elif isinstance(expr, (A.NumberLit, A.StringLit, A.BoolLit, A.NullLit)):
            return
        elif isinstance(expr, A.Unary):
            self._check_expr(expr.operand, table, params)
        elif isinstance(expr, A.Binary):
            self._check_expr(expr.left, table, params)
            self._check_expr(expr.right, table, params)
        elif isinstance(expr, A.Ternary):
            self._check_expr(expr.cond, table, params)
            self._check_expr(expr.then, table, params)
            self._check_expr(expr.otherwise, table, params)
        elif isinstance(expr, A.Call):
            # The receiver is a namespace, list or call result, never a
            # variable read, so only the arguments are checked.
            for arg in expr.args:
                self._check_expr(arg, table, params)
        elif isinstance(expr, A.Index):
            self._check_expr(expr.receiver, table, params)
            self._check_expr(expr.index, table, params)
        elif isinstance(expr, A.Member):
            self._check_expr(expr.receiver, table, params)

    def _check_name(self, node: A.Name, table, params: set[str]) -> None:
        parts = node.parts
        name = ".".join(parts)
        if len(parts) == 1:
            if name in params:
                return
            if name.startswith("_"):
                return  # thread variable, created on first use
            if table is not None and name in table:
                return
            if name in self.plan.class_lists.get(self._checking_class, ()):
                return  # a list of this sprite, read as a value
            if name in self.plan.globals:
                return
            self._undefined(node, name)
            return
        if name in O.PROPERTIES or name in O.REPORTERS:
            return
        if name in self.plan.lists or name in self.list_names:
            return
        if name in self.plan.globals:
            return
        from .extensions import known_namespaces

        if parts[0] in BUILTIN_NAMESPACES or parts[0] in known_namespaces():
            # A member of a runtime namespace; mapping is checked by codegen.
            return
        self._undefined(node, name)

    def _undefined(self, node: A.Node, name: str) -> None:
        self.plan.errors.append(
            f"{node.line}:{node.col}: E0035 undefined variable {name!r}"
        )


def raise_plan_error(entry: str) -> NoReturn:
    """Turn ``"3:5: E0035 undefined variable 'x'"`` back into a ``CompileError``."""
    code, body = split_code(entry.split(": ", 1)[-1])
    if not code:  # a plan error always carries one; stay safe if it does not
        code = ""
    raise CompileError(body, *_position(entry), code=code)


def _position(entry: str) -> tuple[int, int]:
    try:
        where = entry.split(":", 2)
        return int(where[0]), int(where[1])
    except (IndexError, ValueError):
        return 0, 0


def plan_variables(
    program: A.Program,
    base_dir: Path | str | None = None,
    allow_undefined: bool = False,
    features: set[str] | None = None,
) -> VarPlan:
    return VarPlanner(program, base_dir, allow_undefined, features).run()
