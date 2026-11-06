#!/usr/bin/env python3
"""Run a decompiled JSW project in Python, so a Scratch program can be tested.

    python3 tools/jsw_sim.py build/i386/i386.sb3 --entry Cpu.greenFlag --budget 200000

The input is the *decompiled* source, which is what the .sb3 actually contains:
`jswc decompile` turns blocks back into statements, so simulating it is testing
the artifact rather than the intention.

Only the subset the machine uses is implemented -- numbers, strings, the six
comparisons, `+ - * / %`, list get/set/add/clear/length, procedure calls with
parameters, `if`/`else`, `while`, and the operator blocks. That is enough to run
a CPU, which is the point: `i386_ref.py` says what an 80386 does, and this says
what the blocks do, so a disagreement is a bug in one of them.

Two Scratch facts are modelled deliberately, because both have bitten this
project:

  * `_name` variables are **thread**-local, not procedure-local -- two
    procedures on one call chain share them, which is exactly how the RISC-V
    page-table walker corrupted its caller.
  * `stop this script` inside a custom block returns from that block.

Every `Control.wait(...)` yields the thread; the scheduler round-robins, so an
infinite `while` with a `wait` inside it shows up as a live thread rather than a
hang, and an infinite `while` without one shows up as a blocked thread at a
known line -- which is the answer you were looking for anyway.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


class Yield(Exception):
    """The thread gave up the rest of its turn."""


class Return(Exception):
    """`stop this script` inside a custom block."""


class StopAll(Exception):
    pass


# ----------------------------------------------------------------------
# a tiny expression language
# ----------------------------------------------------------------------

_TOKEN = re.compile(r"""
    \s*(?:
      (?P<num>-?\d+(?:\.\d+)?)
    | (?P<str>"(?:[^"\\]|\\.)*")
    | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
    | (?P<op><=|>=|==|!=|&&|\|\||[-+*/%()\[\],<>!=])
    )""", re.VERBOSE)


def tokenize(text: str) -> list[str]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise SyntaxError(f"bad token at {text[pos:pos + 20]!r}")
        pos = m.end()
        out.append(m.group(0).strip())
    return out


class Parser:
    def __init__(self, tokens: list[str], env: "Env") -> None:
        self.t = tokens
        self.i = 0
        self.env = env

    def peek(self) -> str:
        return self.t[self.i] if self.i < len(self.t) else ""

    def take(self) -> str:
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, tok: str) -> None:
        got = self.take()
        if got != tok:
            raise SyntaxError(f"expected {tok!r}, got {got!r}")

    def expr(self):
        return self.or_()

    def or_(self):
        left = self.and_()
        while self.peek() == "||":
            self.take()
            right = self.and_()
            left = ("or", left, right)
        return left

    def and_(self):
        left = self.eq()
        while self.peek() == "&&":
            self.take()
            right = self.eq()
            left = ("and", left, right)
        return left

    def eq(self):
        left = self.rel()
        while self.peek() in ("==", "!="):
            op = self.take()
            left = (op, left, self.rel())
        return left

    def rel(self):
        left = self.add()
        while self.peek() in ("<", ">", "<=", ">="):
            op = self.take()
            left = (op, left, self.add())
        return left

    def add(self):
        left = self.mul()
        while self.peek() in ("+", "-"):
            op = self.take()
            left = (op, left, self.mul())
        return left

    def mul(self):
        left = self.unary()
        while self.peek() in ("*", "/", "%"):
            op = self.take()
            left = (op, left, self.unary())
        return left

    def unary(self):
        if self.peek() == "-":
            self.take()
            return ("neg", self.unary())
        if self.peek() == "!":
            self.take()
            return ("not", self.unary())
        return self.atom()

    def atom(self):
        tok = self.take()
        if tok.startswith('"'):
            return ("lit", tok[1:-1].replace('\\"', '"'))
        if re.fullmatch(r"-?\d+(\.\d+)?", tok):
            return ("lit", float(tok) if "." in tok else int(tok))
        if tok == "(":
            inner = self.expr()
            self.expect(")")
            return inner
        if tok == "[":
            items = []
            while self.peek() and self.peek() != "]":
                items.append(self.expr())
                if self.peek() == ",":
                    self.take()
            self.expect("]")
            return ("list", items)
        # a call or a bare name
        if self.peek() == "(":
            self.take()
            args = []
            while self.peek() and self.peek() != ")":
                args.append(self.expr())
                if self.peek() == ",":
                    self.take()
            self.expect(")")
            return ("call", tok, args)
        return ("name", tok)


# ----------------------------------------------------------------------
# the program
# ----------------------------------------------------------------------

@dataclass
class Proc:
    name: str
    params: list[str]
    body: list
    warp: bool = False


@dataclass
class Klass:
    name: str
    procs: dict[str, Proc] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)


class Env:
    """The stage: variables, lists and the sprite's procedures."""

    def __init__(self) -> None:
        self.vars: dict[str, object] = {}
        self.lists: dict[str, list] = {}
        self.procs: dict[str, Proc] = {}
        self.threads: list[dict] = [{}]         # thread-local temps, one per thread
        self.current = 0
        self.out: list[int] = []
        self.steps = 0
        self.limit = 0
        self.where = ""
        self.where_stmt = None
        self.klass = ""

    # ---- values ----
    @staticmethod
    def num(v) -> float:
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.strip()) if v.strip() else 0.0
            except ValueError:
                return 0.0
        return 0.0

    @classmethod
    def truth(cls, v) -> bool:
        if isinstance(v, str):
            if v.lower() in ("true", "false"):
                return v.lower() == "true"
            try:
                return float(v) != 0
            except ValueError:
                return bool(v)
        return bool(v)

    def get(self, name: str):
        # a procedure parameter and a `_temp` both live in the thread's frame;
        # everything else is a stage variable or list
        frame = self.threads[self.current]
        if name in frame:
            return frame[name]
        if name in self.vars:
            return self.vars[name]
        if name in self.lists:
            return self.lists[name]
        return 0

    def set(self, name: str, value) -> None:
        frame = self.threads[self.current]
        if name in frame or name.startswith("_"):
            frame[name] = value
        else:
            self.vars[name] = value


def strip_comment(line: str) -> str:
    out, in_str = [], False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_str = not in_str
        if not in_str and line.startswith("//", i):
            break
        out.append(ch)
        i += 1
    return "".join(out).rstrip()


def join_lists(text: str) -> str:
    """A list literal may span lines; a statement may not. Fold the first into
    one line so the line-based parser below never has to know."""
    out, buf, depth = [], "", 0
    for line in text.splitlines():
        depth += line.count("[") - line.count("]")
        if buf:
            buf = buf + " " + line.strip()
        else:
            buf = line
        if depth <= 0:
            out.append(buf)
            buf, depth = "", 0
    if buf:
        out.append(buf)
    return "\n".join(out)


def parse_program(text: str, env: Env) -> dict[str, Klass]:
    lines = [ln.rstrip() for ln in join_lists(text).splitlines()]
    classes: dict[str, Klass] = {}
    stack: list[Klass | Proc] = []
    current: Klass | None = None

    def parse_statements(i: int, depth: int) -> tuple[list, int]:
        body: list = []
        while i < len(lines):
            raw = lines[i]
            line = strip_comment(raw.strip())
            if not line or line.startswith("//"):
                i += 1
                continue
            if line == "}":
                return body, i + 1, False
            if line == "} else {":
                return body, i + 1, True
            if line.startswith("} elif ("):
                # an elif is an else whose body is one more if. Hand the
                # condition back to the caller, which builds the same nested
                # structure the project actually holds -- skipping this line
                # would run every branch's statements in a row.
                cond = line[line.index("(") + 1:line.rindex(")")]
                return body, i + 1, (cond,)
            indent = len(raw) - len(raw.lstrip())
            if indent != depth:
                i += 1
                continue
            if line.startswith("metadata ") or line.startswith("import "):
                i += 1
                continue
            if line.startswith("public ") or line.startswith("private "):
                i += 1
                continue
            if line.startswith("if ("):
                cond = line[line.index("(") + 1:line.rindex(")")]
                node, i = parse_if_chain(cond, lines, i + 1, depth)
                body.append(node)
                continue
            if line.startswith("while ("):
                cond = line[line.index("(") + 1:line.rindex(")")]
                inner, i, _ = parse_statements_block(lines, i + 1, depth)
                body.append(("while", cond, inner))
                continue
            if line == "return;" or line == "Control.stopThisScript();":
                body.append(("return",))
                i += 1
                continue
            m = re.match(r"^([A-Za-z_][\w.]*)\s*(\+=|-=|\*=|/=|=)\s*(.+);$", line)
            if m:
                body.append(("assign", m.group(1), m.group(2), m.group(3)))
                i += 1
                continue
            m = re.match(r"^([A-Za-z_][\w.]*)\((.*)\);$", line)
            if m:
                body.append(("call", m.group(1), m.group(2)))
                i += 1
                continue
            i += 1
        return body, i, False

    def parse_statements_block(lines: list[str], i: int, depth: int):
        return parse_statements(i, depth + 4)

    def parse_if_chain(cond, lines: list[str], i: int, depth: int):
        """One if and whatever hangs off it: nothing, an else, or an elif -- and
        an elif is itself an else wrapping another if, however many deep."""
        then, i, tail = parse_statements_block(lines, i, depth)
        if tail is True:
            # `i` already points past "} else {", so the body starts there
            other, i, _ = parse_statements_block(lines, i, depth)
            return ("if", cond, then, other), i
        if isinstance(tail, tuple):
            nested, i = parse_if_chain(tail[0], lines, i, depth)
            return ("if", cond, then, [nested]), i
        return ("if", cond, then, []), i

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("public ") or line.startswith("private "):
            # a list literal at file scope is a stage list
            m = re.match(r"^public\s+([\w.]+)\s*=\s*(.+);$", line)
            if m and "(" not in m.group(2):
                env.vars[m.group(1)] = None
            i += 1
            continue
        i += 1

    # second pass, with the real block parser
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # A declaration at file scope is a stage variable; one inside a class is
        # sprite-local in Scratch. With a single target in this simulator both
        # land in the same place, which is what one core needs.
        m = re.match(r"^(?:public|private)\s+([\w.]+)\s*=\s*(\[.*\]);$", line, re.S)
        if m:
            env.lists[m.group(1)] = list(eval_expr(Parser(tokenize(m.group(2)), env).expr(), env))
        else:
            m = re.match(r"^(?:public|private)\s+([\w.]+)\s*=\s*(.+);$", line)
            if m:
                env.vars[m.group(1)] = eval_expr(Parser(tokenize(m.group(2)), env).expr(), env)
        m = re.match(r"^class\s+(\w+)\s*\{$", line)
        if m:
            current = Klass(m.group(1))
            classes[m.group(1)] = current
            env.klass = m.group(1)
            i += 1
            continue
        m = re.match(r"^(public|private)\s+void\s+(\w+)\((.*?)\)\s*\{\s*(//\s*warp)?", line)
        if m and current is not None:
            name = m.group(2)
            params = [p.strip() for p in m.group(3).split(",") if p.strip()]
            body, i, _ = parse_statements_block(lines, i + 1, 4)
            proc = Proc(name, params, body, warp=bool(m.group(4)))
            current.procs[name] = proc
            current.order.append(name)
            continue
        i += 1
    return classes


def eval_expr(node, env: Env):
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "name":
        return env.get(node[1])
    if kind == "list":
        return [eval_expr(n, env) for n in node[1]]
    if kind == "neg":
        return -Env.num(eval_expr(node[1], env))
    if kind == "not":
        return 0.0 if Env.truth(eval_expr(node[1], env)) else 1.0
    if kind == "and":
        return 1.0 if (Env.truth(eval_expr(node[1], env)) and Env.truth(eval_expr(node[2], env))) else 0.0
    if kind == "or":
        return 1.0 if (Env.truth(eval_expr(node[1], env)) or Env.truth(eval_expr(node[2], env))) else 0.0
    if kind in ("+", "-", "*", "/", "%"):
        a = eval_expr(node[1], env)
        b = eval_expr(node[2], env)
        if kind == "+" and (isinstance(a, str) or isinstance(b, str)):
            return f"{a}{b}"
        x, y = Env.num(a), Env.num(b)
        if kind == "+":
            return x + y
        if kind == "-":
            return x - y
        if kind == "*":
            return x * y
        if kind == "/":
            return x / y if y else 0.0
        if y == 0:
            return 0.0
        return x - math.floor(x / y) * y
    if kind in ("<", ">", "<=", ">=", "==", "!="):
        a = eval_expr(node[1], env)
        b = eval_expr(node[2], env)
        if isinstance(a, str) and isinstance(b, str) and kind in ("==", "!="):
            eq = a.lower() == b.lower()
            return (1.0 if eq else 0.0) if kind == "==" else (0.0 if eq else 1.0)
        x, y = Env.num(a), Env.num(b)
        return 1.0 if {
            "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y,
            "==": x == y, "!=": x != y,
        }[kind] else 0.0
    if kind == "call":
        m = re.match(r"^([\w.]+)\.(get|length)$", node[1])
        if m:
            lst = env.lists.get(m.group(1), [])
            if m.group(2) == "length":
                return float(len(lst))
            idx = int(Env.num(eval_expr(node[2][0], env))) - 1
            return lst[idx] if 0 <= idx < len(lst) else ""
        return call_builtin(node[1], [eval_expr(a, env) for a in node[2]], env)
    raise NotImplementedError(node)


def call_builtin(name: str, args: list, env: Env):
    if name == "Operators.round":
        return float(math.floor(Env.num(args[0]) + 0.5))
    if name == "Operators.math":
        fn, x = args[0], Env.num(args[1])
        return {
            "floor": lambda v: float(math.floor(v)),
            "ceil": lambda v: float(math.ceil(v)),
            "round": lambda v: float(math.floor(v + 0.5)),
            "abs": abs,
            "sqrt": math.sqrt,
        }[fn](x)
    if name == "join":
        return "".join(str(a) for a in args)
    if name == "Control.wait":
        raise Yield()
    if name in ("Looks.hide", "Looks.show", "Pen.eraseAll", "Pen.setPenSize",
                "Pen.setPenColor", "Pen.penDown", "Pen.penUp", "Motion.setXY",
                "Control.waitUntil"):
        return 0.0
    if name == "Sensing.isKeyPressed":
        return 0.0
    if name == "Control.stopThisScript":
        raise Return()
    # a procedure
    proc = env.procs.get(name)
    if proc is None:
        return 0.0
    call(env, proc, args)
    return 0.0


def call(env: Env, proc: Proc, args: list):
    """Run a procedure as a generator so a `wait` can suspend it mid-statement."""
    frame = env.threads[env.current]
    saved = {p: frame.get(p, 0) for p in proc.params}
    for p, a in zip(proc.params, args):
        frame[p] = a
    try:
        yield from exec_block(env, proc.body)
    except Return:
        pass
    finally:
        for p, v in saved.items():
            frame[p] = v


_COMPILED: dict[str, object] = {}


def compile_expr(text: str, env: Env):
    """Parsing every statement of every instruction is the whole cost of this
    simulator, so each distinct expression is parsed once and cached."""
    node = _COMPILED.get(text)
    if node is None:
        node = Parser(tokenize(text), env).expr()
        _COMPILED[text] = node
    return node


def eval_condition(text: str, env: Env) -> bool:
    return Env.truth(eval_expr(compile_expr(text, env), env))


def split_args(text: str) -> list[str]:
    """Split a call's arguments on commas that are not inside a string or a
    nested call -- the shallow version is enough for this compiler's output."""
    parts, depth, cur, in_str = [], 0, "", False
    for ch in text:
        if ch == '"':
            in_str = not in_str
        if not in_str:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(cur.strip())
                cur = ""
                continue
        cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def exec_block(env: Env, body: list):
    for stmt in body:
        env.where_stmt = stmt
        env.steps += 1
        if env.limit and env.steps > env.limit:
            raise StopAll(f"budget exhausted in {env.where}")
        kind = stmt[0]
        if kind == "assign":
            _, lhs, op, rhs = stmt
            value = eval_expr(compile_expr(rhs, env), env)
            if op != "=":
                old = env.get(lhs)
                if op == "+=":
                    value = Env.num(old) + Env.num(value)
                elif op == "-=":
                    value = Env.num(old) - Env.num(value)
                elif op == "*=":
                    value = Env.num(old) * Env.num(value)
                elif op == "/=":
                    value = Env.num(old) / Env.num(value) if Env.num(value) else 0.0
            env.set(lhs, value)
            continue
        if kind == "if":
            _, cond, then, other = stmt
            yield from exec_block(env, then if eval_condition(cond, env) else other)
            continue
        if kind == "while":
            _, cond, inner = stmt
            while eval_condition(cond, env):
                env.steps += 1
                if env.limit and env.steps > env.limit:
                    raise StopAll(f"budget exhausted in a while in {env.where}")
                yield from exec_block(env, inner)
            continue
        if kind == "return":
            raise Return()
        if kind == "call":
            _, name, argtext = stmt
            args = []
            if argtext.strip():
                for part in split_args(argtext):
                    args.append(eval_expr(compile_expr(part, env), env))
            m = re.match(r"^([\w.]+)\.(get|set|add|clear|length)$", name)
            if m:
                lst = env.lists.get(m.group(1))
                meth = m.group(2)
                if lst is None:
                    lst = env.lists[m.group(1)] = []
                if meth == "add":
                    lst.append(args[0])
                elif meth == "clear":
                    lst.clear()
                elif meth == "set":
                    idx = int(Env.num(args[0])) - 1
                    if 0 <= idx < len(lst):
                        lst[idx] = args[1]
                continue
            if name == "Control.wait":
                yield "wait"
                continue
            if name == "Control.stopThisScript":
                raise Return()
            if name in BUILTIN_NOOP or name == "Sensing.isKeyPressed":
                continue
            proc = env.procs.get(name)
            if proc is not None:
                yield from call(env, proc, args)
            continue


BUILTIN_NOOP = {
    "Looks.hide", "Looks.show", "Pen.eraseAll", "Pen.setPenSize", "Pen.setPenColor",
    "Pen.penDown", "Pen.penUp", "Motion.setXY",
}


class Thread:
    """One green-flag script. The generator *is* the thread's stack, so a
    `wait` suspends it mid-statement and the next resume continues there."""

    def __init__(self, env: Env, proc: Proc, label: str) -> None:
        self.env = env
        self.label = label
        self.index = len(env.threads)
        env.threads.append({})
        self.gen = call(env, proc, [])
        self.done = False

    def resume(self) -> None:
        if self.done:
            return
        self.env.current = self.index
        self.env.where = self.label
        try:
            next(self.gen)
        except StopIteration:
            self.done = True
        except StopAll as exc:
            self.done = True
            print(f"[sim] {self.label}: {exc}")
            print(f"[sim]   last statement: {self.env.where_stmt}")


def run(source: str, entries: list[str], budget: int) -> Env:
    """Run every green-flag script, round-robin, the way the VM does."""
    env = Env()
    classes = parse_program(source, env)
    threads = []
    for entry in entries:
        klass_name, _, proc_name = entry.partition(".")
        klass = classes.get(klass_name)
        if klass is None:
            raise SystemExit(f"no class {klass_name}; have {list(classes)}")
        proc = klass.procs.get(proc_name)
        if proc is None:
            raise SystemExit(f"no procedure {proc_name}; have {klass.order}")
        env.procs = dict(env.procs)
        env.procs.update(klass.procs)
        threads.append((Thread(env, proc, entry), klass))
    env.limit = budget
    frames = 0
    while threads and frames < 100_000:
        frames += 1
        for thread, klass in threads:
            if thread.done:
                continue
            env.procs = klass.procs
            thread.resume()
        if all(t.done for t, _ in threads):
            break
        if env.steps > budget:
            print(f"[sim] budget {budget:,} exhausted after {frames:,} frames")
            for thread, _ in threads:
                if not thread.done:
                    print(f"[sim]   still running: {thread.label} at {thread.env.where_stmt}")
            break
    return env


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", help="a .jsw source (decompiled from a .sb3)")
    parser.add_argument("--entry", action="append",
                        help="Class.procedure to run as a green-flag script; repeatable")
    parser.add_argument("--budget", type=int, default=2_000_000)
    parser.add_argument("--dump", help="print a stage variable at the end")
    parser.add_argument("--list", help="print a stage list at the end")
    args = parser.parse_args(argv)

    entries = args.entry or ["Cpu.greenFlag", "Display.displayStart", "Input.inputStart"]
    env = run(Path(args.project).read_text(), entries, args.budget)
    print(f"[sim] {env.steps:,} statements")
    if args.dump:
        print(f"{args.dump} = {env.vars.get(args.dump)!r}")
    if args.list:
        lst = env.lists.get(args.list, [])
        print(f"{args.list} len {len(lst)} first {lst[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
