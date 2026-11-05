"""Semantic checks that need the statement tree, not the block graph.

Each lint is a small, precise pattern that only fires when the code is almost
certainly wrong, so a project can be built with a clean log. The checks run for
both ``jswc build`` and ``jswc check`` — which is what the editor reads.
"""

from __future__ import annotations

from . import ast_nodes as A

#: Calls that mean "this clone hands its work to another clone".
_HANDOFF = "Control.createClone"
_STOP = "Control.stopThisScript"
_DELETE = "Control.deleteThisClone"


def _call_name(expr) -> str | None:
    if not isinstance(expr, A.Call):
        return None
    receiver = expr.receiver
    if not isinstance(receiver, A.Name):
        return None
    return ".".join(receiver.parts) + "." + expr.name


def _statement_lists(body: list[A.Stmt]):
    """Yield every statement list in ``body``, including nested bodies."""
    yield body
    for stmt in body:
        for child in _child_bodies(stmt):
            yield from _statement_lists(child)


def _child_bodies(stmt) -> list[list[A.Stmt]]:
    if isinstance(stmt, A.IfStmt):
        bodies = [stmt.body] + [block for _cond, block in stmt.elifs]
        if stmt.orelse:
            bodies.append(stmt.orelse)
        return bodies
    if isinstance(stmt, (A.WhileStmt, A.RepeatStmt, A.ForStmt, A.RangeFor)):
        return [stmt.body]
    if isinstance(stmt, (A.SubstackStmt, A.LooseDecl)):
        return [stmt.body]
    if isinstance(stmt, A.SwitchStmt):
        bodies = [case.body for case in stmt.cases]
        if stmt.default:
            bodies.append(stmt.default)
        return bodies
    return []


def _functions(program: A.Program):
    for cls in program.classes:
        for fn in cls.funcs:
            yield cls.name, fn
    stage = program.stage
    if stage is not None:
        for fn in stage.funcs:
            yield "Stage", fn


def clone_leaks(program: A.Program) -> list[str]:
    """``createClone(X); stopThisScript();`` abandons the *current* clone.

    ``stop this script`` ends the script, not the clone: the target stays in the
    project for ever, invisible and inert, and ``Player.instances``-style
    counters never see it. A song that hands 2,591 notes to another sprite this
    way leaves 2,591 zombie clones behind — well past Scratch's 300-clone cap,
    at which point ``create clone`` silently does nothing and the sound stops.
    """
    notes: list[str] = []
    for owner, fn in _functions(program):
        for body in _statement_lists(fn.body):
            for index, stmt in enumerate(body):
                if _call_name(getattr(stmt, "expr", None)) != _HANDOFF:
                    continue
                following = body[index + 1 :]
                if not following or _call_name(getattr(following[0], "expr", None)) != _STOP:
                    continue
                notes.append(
                    f"{following[0].line}:{following[0].col}: W0024 {owner}.{fn.proccode}: "
                    "createClone then stopThisScript abandons this clone "
                    "(it is never deleted); write deleteThisClone() instead"
                )
    return notes


#: Every check, in the order their notes are reported.
CHECKS = (clone_leaks,)


def run(program: A.Program) -> list[str]:
    notes: list[str] = []
    for check in CHECKS:
        notes.extend(check(program))
    return notes
