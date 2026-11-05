"""Recursive-descent parser for JadenScript.

Grammar (v0)::

    program     := classDecl*
    classDecl   := 'class' IDENT '{' member* '}'
    member      := funcDecl | statement
    funcDecl    := ('public'|'private')? 'void' path '(' identList? ')' block
    statement   := if | while | for | switch | jump | 'call' expr term
                 | ('public'|'private')? simple term
    simple      := path ('='|'+='|'-='|'*='|'/='|'%='|'++'|'--') expr?
    block       := '{' statement* '}'

Statements are terminated by ``;`` or a newline (automatic semicolon
insertion), which is what lets the reference ``sample.jsw`` mix both styles.
"""

from __future__ import annotations

import re

from . import ast_nodes as A
from .keywords import ASSIGN_OPS, FEATURE_RENAMES, RESERVED
from .lexer import LexResult, tokenize
from .tokens import Kind, ParseError, Token


class Parser:
    def __init__(self, lexed: LexResult, lenient: bool = True):
        self.tokens: list[Token] = lexed.tokens
        self.source: str = getattr(lexed, "source", "")
        self.comments: dict[int, list[str]] = {}
        for c in lexed.comments:
            self.comments.setdefault(c.line, []).append(c.text)
        self.pos = 0
        self.lenient = lenient
        self.diagnostics: list[str] = []
        # ``// monitor`` lines describe Scratch monitors; they are attached to
        # the target they name, so they may sit anywhere in the file.
        self.monitor_comments: list[str] = [
            text
            for c in lexed.comments
            for text in [c.text.strip()]
            if text.startswith("monitor ") or text.startswith("watch ")
        ]
        # Workspace comments (the little notes a project author leaves around).
        self.note_comments: list[str] = [
            text
            for c in lexed.comments
            for text in [c.text.strip()]
            if text.startswith("comment ") or text.startswith("note ")
        ]

    # -- helpers -----------------------------------------------------------
    def peek(self, k: int = 0) -> Token:
        idx = min(self.pos + k, len(self.tokens) - 1)
        return self.tokens[idx]

    def at(self, kind: Kind, value: str | None = None, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok.kind is kind and (value is None or tok.value == value)

    def at_keyword(self, *values: str, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok.kind is Kind.KEYWORD and tok.value in values

    def at_punct(self, value: str, k: int = 0) -> bool:
        return self.at(Kind.PUNCT, value, k)

    def at_op(self, value: str, k: int = 0) -> bool:
        return self.at(Kind.OP, value, k)

    def advance(self) -> Token:
        tok = self.peek()
        if tok.kind is not Kind.EOF:
            self.pos += 1
        return tok

    def expect(self, kind: Kind, value: str | None = None) -> Token:
        tok = self.peek()
        if not tok.is_(kind, value):
            wanted = value if value is not None else kind.value
            code = _EXPECT_CODES.get(value or "", "E0008")
            if code == "E0008" and kind is Kind.IDENT:
                code = "E0009"
            raise ParseError(
                f"expected {wanted!r}, found {_describe(tok)}", tok.line, tok.col, code=code
            )
        return self.advance()

    def expect_keyword(self, value: str) -> Token:
        return self.expect(Kind.KEYWORD, value)

    def expect_name(self) -> Token:
        """An identifier; words reserved for the future may still be used."""
        tok = self.peek()
        if tok.kind is Kind.IDENT or (tok.kind is Kind.KEYWORD and tok.value in RESERVED):
            return self.advance()
        raise ParseError(
            f"expected a name, found {_describe(tok)}", tok.line, tok.col, code="E0009"
        )

    def expect_punct(self, value: str) -> Token:
        # ``sample.jsw`` contains genuinely unbalanced parentheses (a missing
        # ``)`` before a ``;``); in lenient mode we close them implicitly and
        # record a diagnostic instead of failing the whole build.
        if value == ")" and self.lenient and self._at_stmt_boundary():
            tok = self.peek()
            self.diagnostics.append(
                f"{tok.line}:{tok.col}: W0002 auto-closed an unclosed '('"
            )
            return tok
        return self.expect(Kind.PUNCT, value)

    def _at_stmt_boundary(self) -> bool:
        tok = self.peek()
        if tok.kind in (Kind.NEWLINE, Kind.EOF):
            return True
        return tok.kind is Kind.PUNCT and tok.value in (";", "}")

    def skip_newlines(self) -> None:
        while self.at(Kind.NEWLINE):
            self.advance()

    def end_stmt(self, terminate: bool = True) -> None:
        if not terminate:
            return
        if self.at_punct(";"):
            self.advance()
        self.skip_newlines()

    def annotation(self, line: int) -> str | None:
        texts = self.comments.get(line)
        return " ".join(texts) if texts else None

    # -- entry points ------------------------------------------------------
    def parse(self) -> A.Program:
        program = A.Program(line=1, col=1)
        self.skip_newlines()
        while not self.at(Kind.EOF):
            if self.at_punct("#"):
                self.parse_feature(program)
            elif self.at_keyword("class"):
                program.classes.append(self.parse_class())
            elif self.at_keyword("stage") and not self.at_punct(":", k=1):
                program.stage = self.parse_stage()
            elif self.at_keyword("import"):
                program.imports.append(self.parse_import())
            elif self.at_keyword("public", "private") and self.at_keyword("void", k=1):
                # A function with no class of its own belongs to the Stage, the
                # backdrop target: same rules as a member, simpler placement.
                func = self.parse_func()
                if program.stage is None:
                    program.stage = A.StageDecl(line=func.line, col=func.col)
                program.stage.members.append(func)
            elif self.at_keyword("void"):
                func = self.parse_func()
                if program.stage is None:
                    program.stage = A.StageDecl(line=func.line, col=func.col)
                program.stage.members.append(func)
            elif self.at_keyword("public", "private") and not self.at_keyword("void", k=1):
                program.globals.append(self.parse_visibility_stmt())
            elif self.at_keyword("metadata"):
                program.meta.append(self.parse_metadata())
            elif (
                self.at(Kind.IDENT) or self.at_keyword("stage")
            ) and self.at_punct(":", k=1):
                program.sprite_meta.append(self.parse_sprite_meta_decl())
            else:
                tok = self.peek()
                raise ParseError(
                    "expected a declaration ('class', 'stage', 'import', "
                    f"'metadata', '#enable_feature' or a global variable), found {_describe(tok)}",
                    tok.line,
                    tok.col,
                    code="E0034",
                )
            self.skip_newlines()
        if not program.classes and program.stage is None:
            raise ParseError("this file declares no class and no stage", 1, 1, code="E0029")
        # ``metadata`` may also be written inside a target's body; the target
        # is named explicitly, so where it sits does not matter.
        for member in (program.stage.members if program.stage is not None else []):
            if isinstance(member, A.MetaDecl):
                program.meta.append(member)
        for cls in program.classes:
            for member in cls.members:
                if isinstance(member, A.MetaDecl):
                    program.meta.append(member)
        program.monitors = list(self.monitor_comments)
        program.notes = list(self.note_comments)
        return program

    def parse_feature(self, program: A.Program) -> None:
        hash_tok = self.expect_punct("#")
        name_tok = self.peek()
        if name_tok.kind is not Kind.IDENT or name_tok.value != "enable_feature":
            raise ParseError(
                f"expected '#enable_feature', found {name_tok.value!r}",
                name_tok.line,
                name_tok.col,
                code="E0030",
            )
        self.advance()
        self.expect(Kind.OP, "<")
        feature = self.expect(Kind.IDENT).value
        self.expect(Kind.OP, ">")
        self.end_stmt()
        replacement = FEATURE_RENAMES.get(feature)
        if replacement is not None:
            self.diagnostics.append(
                f"{name_tok.line}:{name_tok.col}: I0002 feature {feature!r} is now "
                f"{replacement!r}"
            )
            feature = replacement
        program.features.add(feature)

    def parse_sprite_meta_decl(self) -> A.SpriteMetaDecl:
        name_tok = self.advance() if self.at_keyword("stage") else self.expect_name()
        self.expect_punct(":")
        values = self.parse_meta_pairs()
        self.end_stmt()
        return A.SpriteMetaDecl(line=name_tok.line, col=name_tok.col,
                                sprite=name_tok.value, values=values)

    def parse_metadata(self) -> A.MetaDecl:
        """``metadata <name> pos x,y;`` and the monitor / comment spellings.

        The tail is free-form (``at 0,0 100x24 shown``), so it is sliced out of
        the source and handed to the same parser the ``// monitor`` comments
        use. Two spellings are accepted for a monitor::

            metadata Stage.score slider at 0,0 100x24 shown range 0..10;
            metadata slider Stage.score at 0,0 100x24 shown range 0..10;
        """
        kw = self.expect_keyword("metadata")
        start = self.peek()
        while not self.at_punct(";") and not self.at(Kind.EOF):
            self.advance()
        end = self.peek()
        text = self._slice(start, end).strip()
        self.expect_punct(";")
        return _parse_metadata_tail(kw, text)

    def _slice(self, start: Token, end: Token) -> str:
        """Source text between two tokens."""
        def offset(token: Token) -> int:
            line_start = 0
            for _ in range(token.line - 1):
                line_start = self.source.find("\n", line_start) + 1
            return line_start + token.col - 1

        if not self.source:
            return ""
        return self.source[offset(start):offset(end)]

    def parse_meta_pairs(self) -> dict[str, A.Expr]:
        """``x=0, y=0`` or ``x=0 y=0``; both spellings appear in the wild."""
        values: dict[str, A.Expr] = {}
        while True:
            key = self.advance().value if self.peek().kind is Kind.KEYWORD else self.expect_name().value
            self.expect(Kind.OP, "=")
            values[key] = self.parse_expr()
            if self.at_punct(","):
                self.advance()
                continue
            # Space-separated: another ``key =`` follows on the same line.
            nxt = self.peek()
            if (
                nxt.kind in (Kind.IDENT, Kind.KEYWORD)
                and self.at(Kind.OP, "=", k=1)
                and nxt.line == self.peek(-1).line
            ):
                continue
            break
        return values

    def parse_stage(self) -> A.StageDecl:
        kw = self.expect_keyword("stage")
        annotation = self.annotation(kw.line)
        self.skip_newlines()
        self.expect_punct("{")
        members: list[A.Node] = []
        self.skip_newlines()
        while not self.at_punct("}"):
            if self.at(Kind.EOF):
                raise ParseError(
                    "stage body is never closed; add '}' at the end of the file",
                    kw.line,
                    kw.col,
                    code="E0020",
                )
            members.append(self.parse_member())
            self.skip_newlines()
        self.expect_punct("}")
        self.end_stmt()
        return A.StageDecl(line=kw.line, col=kw.col, members=members,
                           annotation=annotation)

    def parse_class(self) -> A.ClassDecl:
        kw = self.expect_keyword("class")
        name_tok = self.expect_name()
        sprite = _parse_sprite_meta(self.annotation(kw.line))
        params: dict[str, A.Expr] = {}
        if self.at_punct("("):
            self.advance()
            params = self.parse_meta_pairs()
            self.expect_punct(")")
        self.skip_newlines()
        self.expect_punct("{")
        members: list[A.Node] = []
        self.skip_newlines()
        while not self.at_punct("}"):
            if self.at(Kind.EOF):
                raise ParseError(
                    "class body is never closed; add '}' at the end of the file",
                    kw.line,
                    kw.col,
                    code="E0019",
                )
            members.append(self.parse_member())
            self.skip_newlines()
        self.expect_punct("}")
        self.end_stmt()
        return A.ClassDecl(line=name_tok.line, col=name_tok.col, name=name_tok.value,
                           sprite=sprite, params=params, members=members)

    def parse_member(self) -> A.Node:
        if self.at_keyword("metadata"):
            return self.parse_metadata()
        if self.at_keyword("import"):
            return self.parse_import()
        if self.at_keyword("loose"):
            return self.parse_loose()
        if self.at_keyword("public", "private") and self.at_keyword("void", k=1):
            return self.parse_func()
        if self.at_keyword("void"):
            return self.parse_func()
        if self.at_keyword("public", "private") and self.at(Kind.IDENT, "init", k=1):
            return self.parse_init_block()
        return self.parse_statement()

    def parse_loose(self) -> A.LooseDecl:
        """``loose { … }`` — a stack of blocks with no hat, as on the palette."""
        kw = self.expect_keyword("loose")
        annotation = self.annotation(kw.line)
        body = self.parse_block()
        return A.LooseDecl(line=kw.line, col=kw.col, body=body, annotation=annotation)

    def parse_init_block(self) -> A.FuncDecl:
        vis_tok = self.advance()
        name_tok = self.expect(Kind.IDENT)
        if name_tok.value != "init":
            raise ParseError(
                f"the green-flag block is 'init', found {name_tok.value!r}",
                name_tok.line,
                name_tok.col,
                code="E0033",
            )
        body = self.parse_block()
        return A.FuncDecl(line=vis_tok.line, col=vis_tok.col, visibility=vis_tok.value,
                          name=("init",), params=[], body=body,
                          annotation=self.annotation(vis_tok.line), is_init=True)

    def parse_func(self) -> A.FuncDecl:
        visibility = "private"
        start = self.peek()
        if self.at_keyword("public", "private"):
            visibility = self.advance().value
        void_tok = self.expect_keyword("void")
        name = self.parse_path()
        params = self.parse_params()
        self.skip_newlines()
        body = self.parse_block()
        return A.FuncDecl(
            line=void_tok.line,
            col=void_tok.col,
            visibility=visibility,
            name=name,
            params=params,
            body=body,
            annotation=self.annotation(start.line) or self.annotation(void_tok.line),
        )

    def parse_path(self) -> tuple[str, ...]:
        parts = [self.expect_name().value]
        while self.at_punct(".") and self._at_member_name(k=1):
            self.advance()
            parts.append(self.advance().value)
        return tuple(parts)

    def _at_member_name(self, k: int = 0) -> bool:
        """After a dot, keywords are legal member names (``list.delete``)."""
        tok = self.peek(k)
        return tok.kind in (Kind.IDENT, Kind.KEYWORD)

    def parse_params(self) -> list[str]:
        self.expect_punct("(")
        params: list[str] = []
        if not self.at_punct(")"):
            while True:
                params.append(self.expect_name().value)
                if not self.at_punct(","):
                    break
                self.advance()
        self.expect_punct(")")
        return params

    def parse_block(self) -> list[A.Stmt]:
        self.expect_punct("{")
        body: list[A.Stmt] = []
        self.skip_newlines()
        while not self.at_punct("}"):
            if self.at(Kind.EOF):
                raise ParseError(
                    "block is never closed; add '}' before the end of the file",
                    self.peek().line,
                    self.peek().col,
                    code="E0018",
                )
            body.append(self.parse_statement())
            self.skip_newlines()
        self.expect_punct("}")
        return body

    # -- statements --------------------------------------------------------
    def parse_statement(self) -> A.Stmt:
        tok = self.peek()
        if tok.kind is Kind.KEYWORD:
            handler = {
                "if": self.parse_if,
                "while": self.parse_while,
                "repeat": self.parse_repeat,
                "for": self.parse_for,
                "switch": self.parse_switch,
                "break": self.parse_break,
                "continue": self.parse_continue,
                "return": self.parse_return,
                "call": self.parse_call_stmt,
                "import": self.parse_import,
                "public": self.parse_visibility_stmt,
                "private": self.parse_visibility_stmt,
                "void": self.parse_func,
            }.get(tok.value)
            if handler is not None:
                return handler()
        return self.parse_simple()

    def parse_if(self) -> A.IfStmt:
        kw = self.expect_keyword("if")
        cond = self.parse_paren_expr()
        body = self.parse_block()
        elifs: list[tuple[A.Expr, list[A.Stmt]]] = []
        orelse: list[A.Stmt] | None = None
        while True:
            self.skip_newlines()
            if self.at_keyword("elif"):
                self.advance()
                c = self.parse_paren_expr()
                elifs.append((c, self.parse_block()))
                continue
            if self.at_keyword("else"):
                self.advance()
                orelse = self.parse_block()
            break
        return A.IfStmt(line=kw.line, col=kw.col, cond=cond, body=body, elifs=elifs, orelse=orelse)

    def parse_while(self) -> A.WhileStmt:
        kw = self.expect_keyword("while")
        cond = self.parse_paren_expr()
        body = self.parse_block()
        return A.WhileStmt(line=kw.line, col=kw.col, cond=cond, body=body)

    def parse_repeat(self) -> A.RepeatStmt:
        kw = self.expect_keyword("repeat")
        count = self.parse_paren_expr()
        body = self.parse_block()
        return A.RepeatStmt(line=kw.line, col=kw.col, count=count, body=body)

    def _at_range_for(self) -> bool:
        """``for i in …`` or ``for (i in …)``, versus the C-style ``for (i = …``.

        The counter may be dotted (``for Synth.counter in 1..n``), which is how
        a public stage variable is written.
        """
        k = 1 if self.at_punct("(") else 0
        if not self.at(Kind.IDENT, k=k):
            return False
        k += 1
        while self.at_punct(".", k=k) and self._at_member_name(k=k + 1):
            k += 2
        return self.at_keyword("in", k=k)

    def parse_range_for(self, kw: Token) -> A.RangeFor:
        """``for i in 1..10 { … }`` — an inclusive ascending counter."""
        bracketed = False
        if self.at_punct("("):
            self.advance()
            bracketed = True
        name_tok = self.expect_name()
        parts = [name_tok.value]
        while self.at_punct(".") and self._at_member_name(k=1):
            self.advance()
            parts.append(self.advance().value)
        self.expect_keyword("in")
        start, stop = self.parse_range()
        if bracketed:
            self.expect_punct(")")
        body = self.parse_block()
        return A.RangeFor(
            line=kw.line,
            col=kw.col,
            var=tuple(parts),
            start=start,
            stop=stop,
            body=body,
        )

    def parse_range(self) -> tuple[A.Expr, A.Expr]:
        """``a..b``, ``(a..b)``, ``(a+1)..(b+1)`` and ``((a+1)..(b+1))``."""
        if self.at_punct("("):
            self.advance()
            start = self.parse_binary(0)
            if self.at_op(".."):
                self.advance()
                stop = self.parse_binary(0)
                self.expect_punct(")")
                return start, stop
            # The parentheses only grouped the first bound: ``(a+1)..b``.
            self.expect_punct(")")
        else:
            start = self.parse_binary(0)
        if not self.at_op(".."):
            tok = self.peek()
            raise ParseError(
                f"the range needs '..' between its bounds, found {_describe(tok)}",
                tok.line,
                tok.col,
                code="E0022",
            )
        self.advance()
        stop = self.parse_binary(0)
        return start, stop

    def parse_for(self) -> A.ForStmt:
        kw = self.expect_keyword("for")
        if self._at_range_for():
            return self.parse_range_for(kw)
        self.expect_punct("(")
        if self.at_punct(",") or self.at_punct(";"):
            init = None
        else:
            init = self.parse_simple(terminate=False)
        self._for_separator()
        if self.at_punct(",") or self.at_punct(";") or self.at_punct(")"):
            cond = None
        else:
            cond = self.parse_expr()
        self._for_separator()
        update = None if self.at_punct(")") else self.parse_simple(terminate=False)
        self.expect_punct(")")
        body = self.parse_block()
        return A.ForStmt(line=kw.line, col=kw.col, init=init, cond=cond, update=update, body=body)

    def _for_separator(self) -> None:
        if self.at_punct(",") or self.at_punct(";"):
            self.advance()
        else:
            tok = self.peek()
            raise ParseError(
                f"expected ',' or ';' between the parts of the for header, "
                f"found {_describe(tok)}",
                tok.line,
                tok.col,
                code="E0022",
            )

    def parse_switch(self) -> A.SwitchStmt:
        kw = self.expect_keyword("switch")
        # ``switch (x) {`` and ``switch x {`` both read fine; only the parens
        # are optional, the braces are not.
        value = self.parse_paren_expr() if self.at_punct("(") else self.parse_expr()
        self.skip_newlines()
        self.expect_punct("{")
        stmt = A.SwitchStmt(line=kw.line, col=kw.col, value=value)
        self.skip_newlines()
        while not self.at_punct("}"):
            if self.at_keyword("case"):
                case_tok = self.advance()
                self.skip_newlines()
                if self.at(Kind.IDENT, "_"):
                    self.advance()
                    is_default = True
                    case_value = None
                else:
                    is_default = False
                    case_value = self.parse_expr()
                # ``case 1:`` collects statements up to the next label, while
                # ``case 1 { … }`` owns them, so a nested switch cannot end the
                # outer case early.
                if self.at_punct("{"):
                    body = self.parse_block()
                else:
                    self.expect_punct(":")
                    body = self._parse_case_body()
                if is_default:
                    stmt.default = body
                else:
                    stmt.cases.append(A.Case(value=case_value, body=body, line=case_tok.line))
            elif self.at_keyword("default"):
                self.advance()
                if self.at_punct("{"):
                    stmt.default = self.parse_block()
                else:
                    self.expect_punct(":")
                    stmt.default = self._parse_case_body()
            else:
                tok = self.peek()
                raise ParseError(
                    f"expected 'case' or 'default', found {_describe(tok)}",
                    tok.line,
                    tok.col,
                    code="E0023",
                )
            self.skip_newlines()
        self.expect_punct("}")
        return stmt

    def _parse_case_body(self) -> list[A.Stmt]:
        body: list[A.Stmt] = []
        self.skip_newlines()
        while not (self.at_keyword("case", "default") or self.at_punct("}") or self.at(Kind.EOF)):
            body.append(self.parse_statement())
            self.skip_newlines()
        return body

    def parse_break(self) -> A.BreakStmt:
        tok = self.expect_keyword("break")
        self.end_stmt()
        return A.BreakStmt(line=tok.line, col=tok.col)

    def parse_continue(self) -> A.ContinueStmt:
        tok = self.expect_keyword("continue")
        self.end_stmt()
        return A.ContinueStmt(line=tok.line, col=tok.col)

    def parse_return(self) -> A.ReturnStmt:
        tok = self.expect_keyword("return")
        value = None
        if not (self.at_punct(";") or self.at(Kind.NEWLINE) or self.at_punct("}")):
            value = self.parse_expr()
        self.end_stmt()
        return A.ReturnStmt(line=tok.line, col=tok.col, value=value)

    def parse_import(self) -> A.ImportDecl:
        kw = self.expect_keyword("import")
        if not (self.at(Kind.STRING) or self.at(Kind.CHAR)):
            tok = self.peek()
            raise ParseError(
                f"expected a quoted asset path after 'import', found {_describe(tok)}",
                tok.line,
                tok.col,
                code="E0024",
            )
        path = self.advance().value
        name = None
        parenthesised = False
        if self.at_punct("("):
            self.advance()
            parenthesised = True
        if self.at_keyword("as"):
            self.advance()
            if self.at(Kind.STRING) or self.at(Kind.CHAR):
                # Quoted names keep the original costume/sound/variable label,
                # which Scratch matches by text ("switch costume to [false]").
                name = self.advance().value
            else:
                name = self.expect_name().value
        if parenthesised:
            self.expect_punct(")")
        annotation = self.annotation(kw.line)
        self.end_stmt()
        return A.ImportDecl(line=kw.line, col=kw.col, path=path, name=name,
                            annotation=annotation)

    def parse_call_stmt(self) -> A.CallStmt:
        tok = self.expect_keyword("call")
        expr = self.parse_expr()
        if not isinstance(expr, A.Call):
            raise ParseError(
                "'call' must be followed by a function call, for example 'call Move(1, 2);'",
                tok.line,
                tok.col,
                code="E0025",
            )
        self.end_stmt()
        return A.CallStmt(line=tok.line, col=tok.col, expr=expr)

    def parse_visibility_stmt(self) -> A.Stmt:
        vis_tok = self.advance()
        stmt = self.parse_simple(visibility=vis_tok.value)
        if isinstance(stmt, A.Assign):
            stmt.line, stmt.col = vis_tok.line, vis_tok.col
        return stmt

    def parse_simple(self, visibility: str | None = None, terminate: bool = True) -> A.Stmt:
        start = self.peek()
        expr = self.parse_expr()
        tok = self.peek()
        if (
            isinstance(expr, A.Name)
            and tok.kind is Kind.PUNCT
            and tok.value == ";"
            and visibility is not None
        ):
            # ``private _temp;`` declares without an initial value.
            self.end_stmt(terminate)
            return A.Assign(line=start.line, col=start.col, target=expr, op=None,
                            value=None, visibility=visibility)
        if tok.kind is Kind.OP and tok.value in ASSIGN_OPS:
            if not isinstance(expr, (A.Name, A.Index)):
                raise ParseError(
                    "only a variable or a list item can be assigned to",
                    tok.line,
                    tok.col,
                    code="E0026",
                )
            if isinstance(expr, A.Index) and tok.value in ("++", "--"):
                raise ParseError(
                    "a list item does not support ++/--; write list[i] += 1",
                    tok.line,
                    tok.col,
                    code="E0026",
                )
            self.advance()
            value = self.parse_expr()
            self.end_stmt(terminate)
            return A.Assign(line=start.line, col=start.col, target=expr, op=tok.value,
                            value=value, visibility=visibility)
        if tok.kind is Kind.OP and tok.value in ("++", "--"):
            if not isinstance(expr, A.Name):
                raise ParseError(
                    "'+=' and '++' need a variable name on the left",
                    tok.line,
                    tok.col,
                    code="E0027",
                )
            self.advance()
            self.end_stmt(terminate)
            delta = A.NumberLit(line=tok.line, col=tok.col, raw="1")
            op = "+=" if tok.value == "++" else "-="
            return A.Assign(line=start.line, col=start.col, target=expr, op=op,
                            value=delta, visibility=visibility)
        if terminate and self.at_punct("{"):
            # ``block { … }``: the call owns the body (extension C-blocks).
            body = self.parse_block()
            return A.SubstackStmt(line=start.line, col=start.col, expr=expr, body=body)
        self.end_stmt(terminate)
        return A.ExprStmt(line=start.line, col=start.col, expr=expr)

    def parse_paren_expr(self) -> A.Expr:
        self.expect_punct("(")
        expr = self.parse_expr()
        self.expect_punct(")")
        while self.lenient and self.at_punct(")"):
            tok = self.advance()
            self.diagnostics.append(
                f"{tok.line}:{tok.col}: W0001 ignored an extra ')'"
            )
        return expr

    # -- expressions -------------------------------------------------------
    def parse_expr(self) -> A.Expr:
        return self.parse_ternary()

    def parse_ternary(self) -> A.Expr:
        expr = self.parse_binary(0)
        while self.at_keyword("if"):
            tok = self.advance()
            cond = self.parse_binary(0)
            self.expect_keyword("else")
            otherwise = self.parse_ternary()
            expr = A.Ternary(line=tok.line, col=tok.col, cond=cond, then=expr, otherwise=otherwise)
        return expr

    _LEVELS: tuple[frozenset[str], ...] = (
        frozenset({"||", "or"}),
        frozenset({"&&", "and"}),
        frozenset({"==", "!="}),
        frozenset({"<", "<=", ">", ">="}),
        frozenset({"+", "-"}),
        frozenset({"*", "/", "%"}),
    )

    def parse_binary(self, level: int) -> A.Expr:
        if level >= len(self._LEVELS):
            return self.parse_unary()
        ops = self._LEVELS[level]
        left = self.parse_binary(level + 1)
        while True:
            tok = self.peek()
            if tok.value in ops and tok.kind in (Kind.OP, Kind.KEYWORD):
                self.advance()
                right = self.parse_binary(level + 1)
                left = A.Binary(line=tok.line, col=tok.col, op=tok.value, left=left, right=right)
            else:
                return left

    def parse_unary(self) -> A.Expr:
        tok = self.peek()
        if (tok.kind is Kind.OP and tok.value in ("!", "-", "+")) or (
            tok.kind is Kind.KEYWORD and tok.value == "not"
        ):
            self.advance()
            operand = self.parse_unary()
            return A.Unary(line=tok.line, col=tok.col, op=tok.value, operand=operand)
        return self.parse_power()

    def parse_power(self) -> A.Expr:
        base = self.parse_postfix()
        if self.at_op("**"):
            tok = self.advance()
            exponent = self.parse_unary()
            return A.Binary(line=tok.line, col=tok.col, op="**", left=base, right=exponent)
        return base

    def parse_postfix(self) -> A.Expr:
        expr = self.parse_primary()
        while True:
            if self.at_punct(".") and self._at_member_name(k=1):
                self.advance()
                name_tok = self.advance()
                if self.at_punct("("):
                    args = self.parse_args()
                    expr = A.Call(line=name_tok.line, col=name_tok.col, receiver=expr,
                                  name=name_tok.value, args=args)
                elif isinstance(expr, A.Name):
                    expr = A.Name(line=expr.line, col=expr.col, parts=expr.parts + (name_tok.value,))
                else:
                    expr = A.Member(line=name_tok.line, col=name_tok.col, receiver=expr,
                                    name=name_tok.value)
                continue
            if self.at_punct("(") and isinstance(expr, A.Name) and len(expr.parts) == 1:
                name = expr.parts[0]
                args = self.parse_args()
                expr = A.Call(line=expr.line, col=expr.col, receiver=None, name=name, args=args)
                continue
            if self.at_punct("["):
                self.advance()
                index = self.parse_expr()
                self.expect_punct("]")
                expr = A.Index(line=expr.line, col=expr.col, receiver=expr, index=index)
                continue
            return expr

    def _parse_fstring(self, tok: Token) -> A.Expr:
        parts: list = []
        buffer: list[str] = []
        text = tok.value
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "{" and text[i + 1 : i + 2] == "{":
                buffer.append("{")
                i += 2
                continue
            if ch == "}" and text[i + 1 : i + 2] == "}":
                buffer.append("}")
                i += 2
                continue
            if ch == "{":
                depth = 1
                j = i + 1
                while j < len(text) and depth:
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                    j += 1
                if depth:
                    raise ParseError(
                        "'{' opens an expression hole that is never closed in this f-string",
                        tok.line,
                        tok.col,
                        code="E0021",
                    )
                if buffer:
                    parts.append("".join(buffer))
                    buffer = []
                inner = text[i + 1 : j - 1]
                sub = Parser(tokenize(inner), lenient=self.lenient)
                expression = sub.parse_expression_only()
                parts.append(expression)
                i = j
                continue
            if ch == "\\" and i + 1 < len(text):
                buffer.append(_unescape(text[i + 1]))
                i += 2
                continue
            buffer.append(ch)
            i += 1
        if buffer:
            parts.append("".join(buffer))
        return A.FString(line=tok.line, col=tok.col, parts=parts)

    def parse_expression_only(self) -> A.Expr:
        expr = self.parse_expr()
        if not self.at(Kind.EOF):
            tok = self.peek()
            raise ParseError(
                f"an f-string hole holds one expression; found {_describe(tok)} after it",
                tok.line,
                tok.col,
                code="E0028",
            )
        return expr

    def parse_args(self) -> list[A.Expr]:
        self.expect_punct("(")
        args: list[A.Expr] = []
        if not self.at_punct(")"):
            while True:
                args.append(self.parse_expr())
                if not self.at_punct(","):
                    break
                self.advance()
        self.expect_punct(")")
        return args

    def parse_primary(self) -> A.Expr:
        tok = self.peek()
        if tok.kind is Kind.NUMBER:
            self.advance()
            return A.NumberLit(
                line=tok.line, col=tok.col, raw=_decimal_text(tok.value)
            )
        if tok.kind in (Kind.STRING, Kind.CHAR):
            self.advance()
            return A.StringLit(line=tok.line, col=tok.col, value=tok.value)
        if tok.kind is Kind.FSTRING:
            self.advance()
            return self._parse_fstring(tok)
        if tok.kind is Kind.KEYWORD and tok.value in ("true", "false"):
            self.advance()
            return A.BoolLit(line=tok.line, col=tok.col, value=tok.value == "true")
        if tok.kind is Kind.KEYWORD and tok.value == "null":
            self.advance()
            return A.NullLit(line=tok.line, col=tok.col)
        if tok.kind is Kind.KEYWORD and tok.value == "any":
            # ``any`` is the "any key" menu option; it always compiles to the
            # text ``any``. A warning is raised where that is unlikely.
            self.advance()
            return A.AnyLit(line=tok.line, col=tok.col)
        if tok.kind is Kind.KEYWORD and tok.value in RESERVED:
            # Reserved words are usable as plain identifiers so legacy projects
            # with variables called ``static`` or ``delete`` still load.
            self.advance()
            return A.Name(line=tok.line, col=tok.col, parts=(tok.value,))
        if tok.kind is Kind.IDENT:
            self.advance()
            if tok.value in ("Infinity", "NaN"):
                return A.NumberLit(line=tok.line, col=tok.col, raw="Infinity" if tok.value == "Infinity" else "NaN")
            return A.Name(line=tok.line, col=tok.col, parts=(tok.value,))
        if tok.kind is Kind.PUNCT and tok.value == "(":
            self.advance()
            expr = self.parse_expr()
            self.expect_punct(")")
            return expr
        if tok.kind is Kind.PUNCT and tok.value == "[":
            self.advance()
            items: list[A.Expr] = []
            if not self.at_punct("]"):
                while True:
                    items.append(self.parse_expr())
                    if not self.at_punct(","):
                        break
                    self.advance()
            self.expect_punct("]")
            return A.ListLit(line=tok.line, col=tok.col, items=items)
        raise ParseError(f"unexpected {_describe(tok)}", tok.line, tok.col, code="E0017")


def parse(source: str) -> A.Program:
    return Parser(tokenize(source)).parse()


_SPRITE_RE = re.compile(
    # Sprite names are free text in Scratch ("obj loader"), so they may be
    # quoted; a bare name must not contain spaces.
    r'Sprite:\s*(?:"(?P<quoted>[^"]*)"|(?P<plain>[^\s(]+))\s*'
    r"\(\s*(?P<x>-?[\d.]+)\s*,\s*(?P<y>-?[\d.]+)\s*\)"
    r"(?:\s*size=(?P<size>[\d.]+))?(?:\s*direction=(?P<direction>-?[\d.]+))?"
    r"(?:\s*show=(?P<show>[01]))?"
    r"(?:\s*costume=(?P<costume>\d+))?(?:\s*volume=(?P<volume>-?[\d.]+))?"
    r"(?:\s*layer=(?P<layer>\d+))?"
    r"(?:\s*rotation=(?P<rotation>all|leftright|none))?"
    r"(?:\s*draggable=(?P<draggable>[01]))?"
    r"(?:\s*ns=(?P<ns>[A-Za-z_][\w.]*))?"
)

#: Which token the parser was looking for decides the error code.
_EXPECT_CODES = {
    ";": "E0010",
    "{": "E0011",
    "}": "E0012",
    "(": "E0013",
    ")": "E0014",
    "[": "E0015",
    "]": "E0016",
    # Angle brackets only ever wrap a directive's feature name.
    "<": "E0030",
    ">": "E0030",
}


def _describe(tok: Token) -> str:
    """``'}'`` for a token, ``end of file`` at EOF."""
    if tok.kind is Kind.EOF:
        return "end of file"
    if tok.kind is Kind.NEWLINE:
        return "end of line"
    return repr(tok.value)


_ROTATIONS = {"all": "all around", "leftright": "left-right", "none": "don't rotate"}


_MONITOR_MODES = ("default", "large", "slider", "list")


def _parse_metadata_tail(kw: Token, text: str) -> A.MetaDecl:
    """Turn the free-form tail of a ``metadata`` statement into a node."""
    if not text:
        raise ParseError(
            "metadata needs something to describe, for example "
            "'metadata Stage.tick pos 10,20;'",
            kw.line,
            kw.col,
            code="E0031",
        )

    position = re.fullmatch(
        r"(?P<target>\S+)\s+pos\s+(?P<x>-?[\d.]+)\s*,\s*(?P<y>-?[\d.]+)", text
    )
    if position:
        return A.MetaDecl(
            line=kw.line,
            col=kw.col,
            kind="pos",
            target=position.group("target"),
            values={"x": float(position.group("x")), "y": float(position.group("y"))},
        )

    # A monitor may name the mode first (``slider Stage.score at …``) or after
    # the target (``Stage.score slider at …``); both become the comment
    # spelling ``monitor Stage.score slider at …``.
    modes = "|".join(_MONITOR_MODES)
    # The owner may be quoted (``"img list loader".score``), so it is not
    # simply "one word".
    target = r'(?:"[^"]*"\.\S+|\S+)'
    hoisted = re.match(
        rf"(?P<mode>{modes})\s+(?P<target>{target})\s*(?P<rest>.*)$", text, re.IGNORECASE
    ) or re.match(
        rf"(?P<target>{target})\s+(?P<mode>{modes})\b\s*(?P<rest>.*)$", text, re.IGNORECASE
    )
    if hoisted:
        rest = hoisted.group("rest").strip()
        text = (
            f"monitor {hoisted.group('target')} "
            f"{hoisted.group('mode').lower()} {rest}"
        ).strip()

    if not re.match(r"(monitor|watch|comment|note)\s", text, re.IGNORECASE):
        raise ParseError(
            "metadata expects 'pos x,y', a monitor mode, or "
            "'monitor|watch|comment|note …'",
            kw.line,
            kw.col,
            code="E0032",
        )
    return A.MetaDecl(line=kw.line, col=kw.col, kind="line", text=text)


def _parse_sprite_meta(annotation: str | None) -> A.SpriteMeta | None:
    if not annotation:
        return None
    match = _SPRITE_RE.search(annotation)
    if not match:
        return None
    groups = match.groupdict()
    name = groups["quoted"] if groups["quoted"] is not None else groups["plain"]
    return A.SpriteMeta(
        name=name,
        x=float(groups["x"]),
        y=float(groups["y"]),
        size=float(groups["size"] or 100),
        direction=float(groups["direction"] or 90),
        visible=groups["show"] != "0",
        namespace=groups["ns"],
        costume=int(groups["costume"]) if groups["costume"] else None,
        volume=float(groups["volume"]) if groups["volume"] else None,
        layer=int(groups["layer"]) if groups["layer"] else None,
        rotation=_ROTATIONS[groups["rotation"]] if groups["rotation"] else None,
        draggable=(groups["draggable"] == "1") if groups["draggable"] else None,
    )


def _unescape(ch: str) -> str:
    from .lexer import _SIMPLE_ESCAPES

    return _SIMPLE_ESCAPES.get(ch, ch)


def _decimal_text(raw: str) -> str:
    """``0x10`` / ``0b101`` / ``0o17`` -> the decimal integer Scratch stores."""
    if len(raw) > 2 and raw[0] == "0" and raw[1] in "xXbBoO":
        try:
            return str(int(raw, 0))
        except ValueError:
            return raw
    return raw
