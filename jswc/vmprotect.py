"""``vmProtect``: execute supported scripts through a per-sprite interpreter.

The current VM (M0–M6) virtualises the scripts whose blocks are in a small,
well-understood instruction set. Everything else is left as ordinary Scratch
blocks, so a project always builds; the pass reports how many scripts it could
not take.

What the generated project looks like
-------------------------------------
Every protected hat keeps its native hat block and replaces its body with a
call to ``__vm_start(program, __vm_frame)``. The interpreter lives in the same
sprite (Scratch custom blocks are per-target). The list style keeps the shared
instruction stream in Stage lists; h0 style embeds labels in its switch.

No thread-local extension is used. ``__vm_start`` reserves a block of slots in
a Stage list and grows that list when live calls or clones need more;
``__vm_base`` is passed to the interpreter as a custom block *parameter*, which
the VM keeps per invocation across yields. All mutable state (pc, operand
stack, temporaries) lives in that block, so two scripts in the same sprite
never share it. This also means the interpreter only needs built-in blocks,
which keeps it testable in a plain scratch-vm.

Dispatch
--------
The default ``h0`` style emits the hand-written state-machine shape from
``examples/h0.jsw``: every instruction gets a numbered ``case`` in
``__vm_switch``, and each unique body is normally a helper method.
``--vm-style list`` instead keeps the instruction stream in Stage lists and
dispatches it through one ``__vm_step`` if/elif chain. Both styles share the
compiler, frame layout and native-call lowering.

The dispatch layout is randomized per build: opcode ids, h0 labels and case
order, and helper names come from a fresh seed unless ``vm_seed`` is set. This
keeps two protected builds from sharing the same readable interpreter shape.
``vm_crypt`` adds checksum guards and unreachable decoy cases; active-frame
bookkeeping clears the allocator when a run has no live frames, and a guarded
green-flag hat resets it before the next run. Community-extension commands and
reporters use the same native-call lowering as the core vocabulary.

Speed
-----
``__vm_run`` is a normal (non-warp) custom block with a ``while (1)`` loop. The
loop itself yields once per iteration; ``--vm-budget`` places N dispatch
calls in one iteration. The default is 1 instruction per frame. The
interpreter itself is always non-warp (a warp loop would never yield back to
Scratch); `--warp` still applies to the native custom blocks it calls.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
import re
import secrets
import zlib
from dataclasses import dataclass, field

from . import ast_nodes as A
from . import opcodes as O
from .extensions import catalogue
from .opcodes import COMMANDS, FIELD, PROPERTIES, REPORTERS
from .ir import Block, Lit, Project, Ref, Target
from .keywords import ALL_KEYWORDS

#: The initial frame arena. It starts at one native Scratch thread's worth of
#: frames, but the generated allocator appends more when a project needs them;
#: a thread that never starts still costs nothing beyond these initial slots.
MAX_THREADS = 320

#: Slots at the front of every thread block:
#:   0 pc, 1 opcode, 2 argument, 3 a, 4 b, 5 sp, 6 broadcast, 7 broadcast flag
STATE_SLOTS = 8

#: The smallest useful block; a deep expression raises it.
MIN_STRIDE = 32

_IDENT = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$")

_INT = re.compile(r"[+-]?\d+\Z")


def _literal(value):
    """Scratch's text primitive stores numeric text as a number."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        if _INT.match(text):
            return int(text)
        number = float(text)
    except ValueError:
        return value
    if not math.isfinite(number):
        return value
    return int(number) if number.is_integer() else number

#: Hats whose body can be replaced by a VM start. Their own inputs/fields stay
#: native (a ``when >`` condition, a key name, a broadcast message).
_HATS = frozenset(
    {
        "event_whenflagclicked",
        "event_whenbroadcastreceived",
        "event_whenkeypressed",
        "event_whenthisspriteclicked",
        "event_whenbackdropswitchesto",
        "event_whengreaterthan",
        "control_start_as_clone",
    }
)

#: opcode -> (case name, source operator)
_BINARY = {
    "operator_add": ("add", "+"),
    "operator_subtract": ("sub", "-"),
    "operator_multiply": ("mul", "*"),
    "operator_divide": ("div", "/"),
    "operator_mod": ("mod", "%"),
    "operator_lt": ("lt", "<"),
    "operator_gt": ("gt", ">"),
    "operator_equals": ("eq", "=="),
    "operator_and": ("and", "&&"),
    "operator_or": ("or", "||"),
}

#: Native vocabulary, indexed by opcode. The interpreter calls the same JSW
#: function the ordinary build would have used; only the argument read moves
#: into the bytecode.
_COMMANDS_BY_OPCODE: dict[str, list[tuple[str, object]]] = {}
for _name, _command in COMMANDS.items():
    _COMMANDS_BY_OPCODE.setdefault(_command.opcode, []).append((_name, _command))
_REPORTERS_BY_OPCODE: dict[str, list[tuple[str, object]]] = {}
for _name, _reporter in REPORTERS.items():
    _REPORTERS_BY_OPCODE.setdefault(_reporter.opcode, []).append((_name, _reporter))
_PROPERTIES_BY_OPCODE: dict[str, str] = {}
for _name, _property in PROPERTIES.items():
    _PROPERTIES_BY_OPCODE.setdefault(_property.get, _name)
_LIST_COMMANDS_BY_OPCODE: dict[str, str] = {}
for _name, (_opcode, _params) in O.LIST_COMMANDS.items():
    _LIST_COMMANDS_BY_OPCODE.setdefault(_opcode, _name)
_LIST_REPORTERS_BY_OPCODE: dict[str, str] = {}
for _name, _opcode in O.LIST_REPORTERS.items():
    _LIST_REPORTERS_BY_OPCODE.setdefault(_opcode, _name)
_LIST_REPORTER_ARGS = {
    "get": ("INDEX",),
    "length": (),
    "contains": ("ITEM",),
    "indexOf": ("ITEM",),
}
#: Native commands that wait for something outside the current step. A queued
#: broadcast is delivered before one of these, or a helper that waits for the
#: receiver's state would wait forever.
_YIELDING_COMMANDS = {
    "sensing_askandwait",
    "sound_playuntildone",
    "motion_glidesecstoxy",
    "motion_glideto",
    "looks_switchbackdroptoandwait",
    "music_playNoteForBeats",
    "music_playDrumForBeats",
    "music_midiPlayDrumForBeats",
    "music_restForBeats",
}
_PROPERTY_SETTERS: dict[str, tuple[str, str, str]] = {}
for _name, _property in PROPERTIES.items():
    _PROPERTY_SETTERS.setdefault(
        _property.set, (_name, "=", _property.set_input)
    )
    if _property.change_input:
        _PROPERTY_SETTERS.setdefault(
            _property.change, (_name, "+=", _property.change_input)
        )
_LIST_VISIBILITY = {"data_showlist": "showList", "data_hidelist": "hideList"}
_VARIABLE_VISIBILITY = {
    "data_showvariable": "showVariable",
    "data_hidevariable": "hideVariable",
}

#: Extension opcode -> (namespace, method, kind, argument specs).  Built from
#: the same registry the ordinary code generator uses, so ``gtm``/``keySim``
#: and the feature-gated pishi blocks resolve here too.
ExtensionTable = dict[str, tuple[str, str, str, tuple]]


def extension_table(features=()) -> ExtensionTable:
    """Extension opcode -> its namespace, method, kind and arguments."""
    table: ExtensionTable = {}
    for extension_id, entry in catalogue(features).items():
        namespace = entry["namespace"]
        for opcode, spec in entry["blocks"].items():
            table[f"{extension_id}_{opcode}"] = (
                namespace,
                opcode,
                spec["type"],
                tuple(tuple(arg) for arg in spec["args"]),
            )
    return table


#: The table without pishi's feature gate; `protect_project` builds the real
#: one from the features the source asked for.
_EXTENSION_BY_OPCODE: ExtensionTable = extension_table()

#: Extension commands that can suspend the current thread.  The VM has to
#: flush queued broadcasts before them, just like a native `wait`.
_EXTENSION_YIELDING = {
    "lmsMoreEvents_broadcastToTargetAndWait",
    "lmsMoreEvents_broadcastDataAndWait",
    "lmsMoreEvents_broadcastDataToTargetAndWait",
    # The Teachable Machine extension fetches a model, opens the camera and
    # tears both down; every one of those waits on the browser.
    "gtm_load",
    "gtm_unload",
    "gtm_startCamera",
    "gtm_stopCamera",
}


def extension_hats(table: ExtensionTable) -> set[str]:
    """Extension hats and events: native trigger, virtualised body."""
    return {
        opcode
        for opcode, (_namespace, _method, kind, _args) in table.items()
        if kind in ("HAT", "EVENT")
    }


#: The hats of the default table, kept for callers that never pass features.
_EXTENSION_HATS = extension_hats(_EXTENSION_BY_OPCODE)


class Unsupported(Exception):
    def __init__(self, opcode: str):
        self.opcode = opcode


@dataclass
class _Instr:
    case: int
    arg: int = 0


@dataclass
class _Program:
    target: Target
    class_name: str
    hat: Block
    code: list[_Instr] = field(default_factory=list)
    params: int = 0
    is_procedure: bool = False
    fn: A.FuncDecl | None = None
    temps: int = 0
    max_depth: int = 0
    start: int = 1
    used: set[int] = field(default_factory=set)
    #: h0 style: one random dispatch label per instruction.
    labels: list[int] = field(default_factory=list)

    @property
    def stride(self) -> int:
        return STATE_SLOTS + self.params + self.temps + self.max_depth + 4


@dataclass
class _Case:
    key: tuple
    source: str
    ident: int


class _Compiler:
    """Compile one hat body to the current VM instruction set."""

    def __init__(
        self,
        blocks: dict[str, dict],
        cases: "_Cases",
        consts: list,
        calls: dict[str, A.FuncDecl],
        unsafe: set[str],
        pools: dict[str, list[str]],
        params: dict[str, int] | None = None,
        extensions: ExtensionTable | None = None,
    ):
        self.blocks = blocks
        self.cases = cases
        self.consts = consts
        self.calls = calls
        self.unsafe = unsafe
        self.pools = pools
        self.params = params or {}
        self.extensions = _EXTENSION_BY_OPCODE if extensions is None else extensions
        self.param_count = len(self.params)
        self.code: list[_Instr] = []
        self.temps = 0
        self.depth = 0
        self.max_depth = 0
        self.used: set[int] = set()

    # -- emission ------------------------------------------------------
    def emit(self, case: int, arg: int = 0) -> int:
        self.code.append(_Instr(case, arg))
        self.used.add(case)
        return len(self.code)  # 1-based index of this instruction

    def push(self) -> None:
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)

    def pop(self, count: int = 1) -> None:
        self.depth -= count
        if self.depth < 0:  # pragma: no cover - a compiler bug
            raise AssertionError("operand stack underflow")

    def constant(self, value) -> int:
        value = _literal(value)
        try:
            return self.consts.index(value) + 1
        except ValueError:
            self.consts.append(value)
            return len(self.consts)

    def new_temp(self) -> int:
        slot = self.param_count + self.temps
        self.temps += 1
        return slot

    # -- inputs --------------------------------------------------------
    def input(self, block: dict, name: str) -> None:
        raw = (block.get("inputs") or {}).get(name)
        self.value(raw)

    def value(self, raw) -> None:
        if raw is None:
            self.push()
            self.emit(self.cases.push_k, self.constant(""))
            return
        if not isinstance(raw, list) or not raw:
            raise Unsupported("input")
        if raw[0] == 1:
            payload = raw[1]
            if isinstance(payload, str):
                shadow = self.blocks.get(payload)
                if shadow is None:
                    self.push()
                    self.emit(self.cases.push_k, self.constant(""))
                    return
                # A menu shadow used as a value is a literal field.
                fields = shadow.get("fields") or {}
                for value in fields.values():
                    self.push()
                    self.emit(self.cases.push_k, self.constant(str(value[0])))
                    return
                self.expr(shadow)
                return
            if isinstance(payload, list):
                self.primitive(payload)
                return
            raise Unsupported("input")
        child = self.blocks.get(raw[1]) if isinstance(raw[1], str) else None
        if child is None:
            raise Unsupported("input")
        self.expr(child)

    def primitive(self, payload: list) -> None:
        kind = payload[0]
        if kind in (4, 5, 6, 7, 8, 9, 10):
            self.push()
            self.emit(self.cases.push_k, self.constant(payload[1]))
            return
        if kind == 12:  # variable reporter
            name = str(payload[1])
            self.load_var(name)
            return
        raise Unsupported(f"primitive {kind}")

    # -- expressions ---------------------------------------------------
    def expr(self, block: dict) -> None:
        opcode = block.get("opcode", "")
        if opcode in (
            "argument_reporter_string_number",
            "argument_reporter_boolean",
        ):
            name = str(((block.get("fields") or {}).get("VALUE") or [""])[0])
            slot = self.params.get(name)
            if slot is None:
                raise Unsupported(opcode)
            self.push()
            self.emit(self.cases.load_param(slot))
            return
        if opcode == "data_itemoflist":
            raw = (block.get("fields") or {}).get("LIST")
            items = self.pools.get(raw[1]) if raw else None
            index = (block.get("inputs") or {}).get("INDEX")
            if items is not None and isinstance(index, list) and index:
                payload = index[1]
                if isinstance(payload, list) and payload:
                    try:
                        number = int(float(payload[1]))
                    except (TypeError, ValueError):
                        number = 0
                    text = items[number - 1] if 1 <= number <= len(items) else ""
                    self.push()
                    self.emit(self.cases.push_k, self.constant(text))
                    return
                # A computed pool index is rare; leave the whole script native
                # rather than emit a list read whose name will be renamed.
                raise Unsupported("data_itemoflist")
        if opcode == "data_variable":
            name = ((block.get("fields") or {}).get("VARIABLE") or [""])[0]
            self.load_var(str(name))
            return
        if opcode in _BINARY:
            name, op = _BINARY[opcode]
            left, right = ("NUM1", "NUM2") if op in "+-*/%" else ("OPERAND1", "OPERAND2")
            self.input(block, left)
            self.input(block, right)
            self.pop(2)
            self.push()
            self.emit(self.cases.binary(name, op))
            return
        if opcode == "operator_not":
            self.input(block, "OPERAND")
            self.pop()
            self.push()
            self.emit(self.cases.not_)
            return
        if opcode == "operator_join":
            self.input(block, "STRING1")
            self.input(block, "STRING2")
            self.pop(2)
            self.push()
            self.emit(self.cases.join)
            return
        if self.list_reporter(block):
            return
        if self.native_reporter(block):
            return
        if self.extension_reporter(block):
            return
        raise Unsupported(opcode)

    def native_property(self, block: dict) -> bool:
        spec = _PROPERTY_SETTERS.get(block.get("opcode", ""))
        if spec is None:
            return False
        name, operator, slot_name = spec
        if slot_name not in (block.get("inputs") or {}):
            return False
        self.input(block, slot_name)
        self.pop()
        slot = self.new_temp()
        self.emit(self.cases.tmp_store_at(slot))
        self.emit(
            self.cases.native_property(
                name,
                operator,
                f"__vm_stack.get(__vm_base + 8 + {slot})",
            )
        )
        return True

    def native_reporter(self, block: dict) -> bool:
        opcode = block.get("opcode", "")
        candidates = _REPORTERS_BY_OPCODE.get(opcode, ())
        for name, spec in candidates:
            if not self._matches(block, spec):
                continue
            parts, slots = self._arguments(block, spec)
            self.push()
            self.emit(
                self.cases.native_reporter(name, ", ".join(parts), False)
            )
            return True
        name = _PROPERTIES_BY_OPCODE.get(opcode)
        if name is not None:
            self.push()
            self.emit(self.cases.native_reporter(name, "", True))
            return True
        return False

    def load_var(self, name: str) -> None:
        if not self.valid_name(name):
            raise Unsupported("variable")
        self.push()
        self.emit(self.cases.load(name))

    def valid_name(self, name: str) -> bool:
        if not _IDENT.match(name) or name.startswith("__vm"):
            return False
        last = name.rsplit(".", 1)[-1]
        return last not in ALL_KEYWORDS

    # -- statements ----------------------------------------------------
    def chain(self, first: dict | None) -> None:
        block = first
        while block is not None:
            self.stmt(block)
            block = self.blocks.get(block.get("next")) if block.get("next") else None

    def substack(self, block: dict, name: str) -> None:
        raw = (block.get("inputs") or {}).get(name)
        if raw is None or raw[0] == 1:
            return
        child = self.blocks.get(raw[1]) if isinstance(raw[1], str) else None
        if child is not None:
            self.chain(child)

    def stmt(self, block: dict) -> None:
        opcode = block.get("opcode", "")
        if opcode == "data_setvariableto":
            name = ((block.get("fields") or {}).get("VARIABLE") or [""])[0]
            if not self.valid_name(str(name)):
                raise Unsupported("variable")
            self.input(block, "VALUE")
            self.pop()
            self.emit(self.cases.store(str(name)))
            return
        if opcode == "data_changevariableby":
            name = ((block.get("fields") or {}).get("VARIABLE") or [""])[0]
            if not self.valid_name(str(name)):
                raise Unsupported("variable")
            self.input(block, "VALUE")
            self.pop()
            self.emit(self.cases.change(str(name)))
            return
        if opcode == "control_if":
            self.if_body(block)
            return
        if opcode == "control_if_else":
            self.if_body(block, otherwise="SUBSTACK2")
            return
        if opcode == "control_repeat":
            self.repeat(block)
            return
        if opcode == "control_forever":
            loop = len(self.code) + 1
            self.substack(block, "SUBSTACK")
            self.emit(self.cases.back_jmp, loop)
            return
        if opcode in ("control_repeat_until", "control_while"):
            self.while_loop(block, until=opcode == "control_repeat_until")
            return
        if opcode == "control_wait":
            self.input(block, "DURATION")
            self.pop()
            self.emit(self.cases.wait)
            return
        if opcode == "control_wait_until":
            self.wait_until(block)
            return
        if opcode in ("event_broadcast", "event_broadcastandwait"):
            message = self.broadcast_message(block)
            self.emit(self.cases.broadcast(message, opcode == "event_broadcastandwait"))
            return
        if opcode == "looks_say":
            self.input(block, "MESSAGE")
            self.pop()
            self.emit(self.cases.say)
            return
        if opcode == "looks_sayforsecs":
            self.input(block, "MESSAGE")
            self.input(block, "SECS")
            self.pop(2)
            self.emit(self.cases.say_for_secs)
            return
        if opcode == "control_stop":
            option = ((block.get("fields") or {}).get("STOP_OPTION") or [""])[0]
            if option == "this script":
                self.emit(self.cases.halt)
                return
            if self.native_command(block):
                return
            raise Unsupported(opcode)
        if opcode == "procedures_call":
            self.native_call(block)
            return
        if self.list_command(block):
            return
        if opcode == "lmscomments_commentC":
            # A comment wrapper is transparent: keeping the SUBSTACK is the
            # whole runtime effect.
            self.substack(block, "SUBSTACK")
            return
        if opcode == "lmsTempVars2_forEachThreadVariable":
            if self.foreach_thread_var(block):
                return
            raise Unsupported(opcode)
        if self.extension_command(block):
            return
        if self.native_command(block):
            return
        if self.native_property(block):
            return
        raise Unsupported(opcode)

    def foreach_thread_var(self, block: dict) -> bool:
        """Lower ``Temp.forEachThreadVariable(name, n) { body }``.

        The extension's loop binds a thread variable to 1..n.  The VM can do
        the same with a normal counting loop plus a native setter; the body
        then stays virtualized instead of making the whole program native.
        """
        raw = (block.get("inputs") or {}).get("VAR")
        name = self.literal_input(raw)
        if name is None:
            return False
        count = self.new_temp()
        index = self.new_temp()
        self.input(block, "NUM")
        self.pop()
        self.emit(self.cases.tmp_store, count)
        self.push()
        self.emit(self.cases.push_k, self.constant("1"))
        self.pop()
        self.emit(self.cases.tmp_store, index)
        loop = len(self.code) + 1
        self.emit(self.cases.tmp_load, index)
        self.push()
        self.emit(self.cases.tmp_load, count)
        self.push()
        self.pop(2)
        self.push()
        self.emit(self.cases.binary("gt", ">"))
        self.pop()
        done = self.emit(self.cases.jz, 0)
        self.emit(
            self.cases.native_procedure(
                "Temp.setThreadVariable",
                f"{json.dumps(name, ensure_ascii=False)}, "
                f"__vm_stack.get(__vm_base + 8 + {index})",
            )
        )
        self.substack(block, "SUBSTACK")
        self.emit(self.cases.tmp_load, index)
        self.push()
        self.emit(self.cases.push_k, self.constant("1"))
        self.push()
        self.pop(2)
        self.push()
        self.emit(self.cases.binary("add", "+"))
        self.pop()
        self.emit(self.cases.tmp_store, index)
        self.emit(self.cases.back_jmp, loop)
        self.patch(done, len(self.code) + 1)
        return True

    def _list_name(self, block: dict) -> str | None:
        raw = (block.get("fields") or {}).get("LIST")
        if not raw:
            return None
        name = str(raw[0])
        if not _IDENT.match(name) or name.startswith("__vm"):
            return None
        return name

    def list_command(self, block: dict) -> bool:
        opcode = block.get("opcode", "")
        if opcode in _LIST_VISIBILITY:
            name = self._list_name(block)
            if name is None:
                return False
            method = _LIST_VISIBILITY[opcode]
            literal = json.dumps(name, ensure_ascii=False)
            self.emit(
                self.cases.native_procedure(f"Lists.{method}", literal)
            )
            return True
        if opcode in _VARIABLE_VISIBILITY:
            raw = (block.get("fields") or {}).get("VARIABLE")
            if not raw:
                return False
            name = str(raw[0])
            if not _IDENT.match(name) or name.startswith("__vm"):
                return False
            method = _VARIABLE_VISIBILITY[opcode]
            literal = json.dumps(name, ensure_ascii=False)
            self.emit(
                self.cases.native_procedure(f"Variables.{method}", literal)
            )
            return True
        name = self._list_name(block)
        method = _LIST_COMMANDS_BY_OPCODE.get(opcode)
        if name is None or method is None:
            return False
        params = O.LIST_COMMANDS[method][1]
        parts: list[str] = []
        slots: list[int] = []
        for param in params:
            if param not in (block.get("inputs") or {}):
                return False
            self.input(block, param)
            self.pop()
            slot = self.new_temp()
            slots.append(slot)
            parts.append(f"__vm_stack.get(__vm_base + 8 + {slot})")
        for slot in reversed(slots):
            self.emit(self.cases.tmp_store_at(slot))
        self.emit(
            self.cases.native_procedure(f"{name}.{method}", ", ".join(parts))
        )
        return True

    def list_reporter(self, block: dict) -> bool:
        opcode = block.get("opcode", "")
        name = self._list_name(block)
        method = _LIST_REPORTERS_BY_OPCODE.get(opcode)
        if name is None or method is None:
            return False
        params = _LIST_REPORTER_ARGS.get(method, ())
        parts: list[str] = []
        slots: list[int] = []
        for param in params:
            if param not in (block.get("inputs") or {}):
                return False
            self.input(block, param)
            self.pop()
            slot = self.new_temp()
            slots.append(slot)
            parts.append(f"__vm_stack.get(__vm_base + 8 + {slot})")
        for slot in reversed(slots):
            self.emit(self.cases.tmp_store_at(slot))
        self.push()
        self.emit(
            self.cases.native_reporter(f"{name}.{method}", ", ".join(parts), False)
        )
        return True

    def literal_input(self, raw):
        """The value of a menu input, or None if it is computed at runtime.

        Extension menus are dropdowns, not ordinary inputs.  Passing a
        temporary expression to one makes the ordinary code generator create a
        menu shadow and leave the evaluated reporter orphaned.  Read the
        literal dropdown value directly instead; a genuinely dynamic menu
        value keeps the script native.
        """
        if not isinstance(raw, list) or not raw:
            return ""
        if raw[0] != 1:
            return None
        payload = raw[1]
        if isinstance(payload, str):
            shadow = self.blocks.get(payload)
            if shadow is None:
                return None
            for value in (shadow.get("fields") or {}).values():
                return str(value[0])
            return None
        if isinstance(payload, list) and payload and payload[0] in (4, 5, 6, 7, 8, 9, 10):
            return str(payload[1])
        return None

    def extension_arguments(
        self, block: dict, args: tuple
    ) -> tuple[list[str], list[int]]:
        """Lower extension inputs to temporary slots, in declaration order."""
        inputs = block.get("inputs") or {}
        parts: list[str] = []
        slots: list[int] = []
        for name, _kind, menu in args:
            if menu:
                value = self.literal_input(inputs.get(name))
                if value is None:
                    raise Unsupported("extension menu")
                parts.append(json.dumps(value, ensure_ascii=False))
                continue
            if name in inputs:
                self.input(block, name)
            else:
                self.push()
                self.emit(self.cases.push_k, self.constant(""))
            self.pop()
            slot = self.new_temp()
            slots.append(slot)
            parts.append(f"__vm_stack.get(__vm_base + 8 + {slot})")
        for slot in reversed(slots):
            self.emit(self.cases.tmp_store_at(slot))
        return parts, slots

    def extension_command(self, block: dict) -> bool:
        spec = self.extensions.get(block.get("opcode", ""))
        if spec is None or spec[2] != "COMMAND":
            return False
        namespace, method, _kind, args = spec
        parts, _slots = self.extension_arguments(block, args)
        self.emit(
            self.cases.native_procedure(
                f"{namespace}.{method}",
                ", ".join(parts),
                block.get("opcode", "") in _EXTENSION_YIELDING,
            )
        )
        return True

    def extension_reporter(self, block: dict) -> bool:
        spec = self.extensions.get(block.get("opcode", ""))
        if spec is None or spec[2] not in ("REPORTER", "BOOLEAN"):
            return False
        namespace, method, _kind, args = spec
        parts, _slots = self.extension_arguments(block, args)
        self.push()
        self.emit(
            self.cases.native_reporter(f"{namespace}.{method}", ", ".join(parts), False)
        )
        return True

    @staticmethod
    def _matches(block: dict, spec) -> bool:
        fields = block.get("fields") or {}
        for name, expected in spec.fields:
            value = fields.get(name)
            if value is None or str(value[0]) != expected:
                return False
        inputs = block.get("inputs") or {}
        for param in spec.params:
            if param.prim == FIELD:
                if param.name not in fields:
                    return False
            elif param.name not in inputs:
                return False
        return True

    def _arguments(self, block: dict, spec) -> tuple[list[str], list[int]]:
        parts: list[str] = []
        slots: list[int] = []
        for param in spec.params:
            if param.prim == FIELD:
                # A field is not an input: the compiler resolves it to a
                # literal, so it must not be handed a list-item reporter.
                value = (block.get("fields") or {})[param.name][0]
                parts.append(json.dumps(str(value), ensure_ascii=False))
                continue
            self.input(block, param.name)
            self.pop()
            slot = self.new_temp()
            slots.append(slot)
            parts.append(f"__vm_stack.get(__vm_base + 8 + {slot})")
        for slot in reversed(slots):
            self.emit(self.cases.tmp_store_at(slot))
        return parts, slots

    def native_command(self, block: dict) -> bool:
        for name, spec in _COMMANDS_BY_OPCODE.get(block.get("opcode", ""), ()):
            if not self._matches(block, spec):
                continue
            parts, slots = self._arguments(block, spec)
            self.emit(
                self.cases.native_procedure(
                    name,
                    ", ".join(parts),
                    block.get("opcode") in _YIELDING_COMMANDS,
                )
            )
            return True
        return False

    def native_call(self, block: dict) -> None:
        """Call an ordinary custom block from inside the interpreter.

        Custom-block definitions stay native, so the call itself can stay
        native too: the VM frame is simply suspended while Scratch runs the
        procedure, exactly as it would be for a reporter.  Only calls that
        resolve to a definition in this same sprite are taken; a cross-sprite
        or dangling label has no procedure to call.
        """
        mutation = block.get("mutation") or {}
        proccode = mutation.get("proccode")
        fn = self.calls.get(proccode) if isinstance(proccode, str) else None
        if fn is None or fn.qualified in self.unsafe:
            raise Unsupported("procedures_call")
        try:
            argument_ids = json.loads(mutation.get("argumentids") or "[]")
        except (TypeError, ValueError):
            raise Unsupported("procedures_call") from None
        if not isinstance(argument_ids, list) or len(argument_ids) != len(fn.params):
            raise Unsupported("procedures_call")
        inputs = block.get("inputs") or {}
        if any(argument not in inputs for argument in argument_ids):
            raise Unsupported("procedures_call")
        slots = []
        for argument in argument_ids:
            self.value(inputs[argument])
            self.pop()
            slots.append(self.new_temp())
        # The values are on the operand stack in source order; store them in
        # reverse so the first parameter ends up in the first slot.
        for slot in reversed(slots):
            self.emit(self.cases.tmp_store_at(slot))
        call = ", ".join(
            f"__vm_stack.get(__vm_base + 8 + {slot})" for slot in slots
        )
        self.emit(self.cases.native_procedure(fn.qualified, call, True))

    def wait_until(self, block: dict) -> None:
        loop = len(self.code) + 1
        self.input(block, "CONDITION")
        self.pop()
        self.emit(self.cases.wait_until, loop)

    def if_body(self, block: dict, otherwise: str | None = None) -> None:
        self.input(block, "CONDITION")
        self.pop()
        jump = self.emit(self.cases.jz, 0)
        self.substack(block, "SUBSTACK")
        if otherwise is None:
            self.patch(jump, len(self.code) + 1)
            return
        end = self.emit(self.cases.jmp, 0)
        self.patch(jump, len(self.code) + 1)
        self.substack(block, otherwise)
        self.patch(end, len(self.code) + 1)

    def repeat(self, block: dict) -> None:
        slot = self.new_temp()
        self.input(block, "TIMES")
        self.pop()
        self.emit(self.cases.tmp_store, slot)
        loop = len(self.code) + 1
        self.emit(self.cases.tmp_load, slot)
        self.push()
        self.emit(self.cases.push_k, self.constant("0"))
        self.push()
        self.pop(2)
        self.push()
        self.emit(self.cases.binary("gt", ">"))
        self.pop()
        done = self.emit(self.cases.jz, 0)
        self.substack(block, "SUBSTACK")
        self.emit(self.cases.tmp_load, slot)
        self.push()
        self.emit(self.cases.push_k, self.constant("1"))
        self.push()
        self.pop(2)
        self.push()
        self.emit(self.cases.binary("sub", "-"))
        self.pop()
        self.emit(self.cases.tmp_store, slot)
        self.emit(self.cases.back_jmp, loop)
        self.patch(done, len(self.code) + 1)

    def while_loop(self, block: dict, until: bool) -> None:
        loop = len(self.code) + 1
        self.input(block, "CONDITION")
        self.pop()
        jump = self.emit(self.cases.jnz if until else self.cases.jz, 0)
        self.substack(block, "SUBSTACK")
        self.emit(self.cases.back_jmp, loop)
        self.patch(jump, len(self.code) + 1)

    def patch(self, index: int, target: int) -> None:
        self.code[index - 1].arg = target

    def broadcast_message(self, block: dict) -> str:
        raw = (block.get("inputs") or {}).get("BROADCAST_INPUT")
        if not isinstance(raw, list) or len(raw) < 2:
            raise Unsupported("broadcast")
        payload = raw[1]
        if raw[0] == 1 and isinstance(payload, str):
            shadow = self.blocks.get(payload)
            fields = (shadow or {}).get("fields") or {}
            for value in fields.values():
                return str(value[0])
        if (
            not isinstance(payload, list)
            or len(payload) < 2
            or payload[0] not in (O.TEXT, O.BROADCAST)
        ):
            # A computed message is a reporter, not a name.  Running it through
            # the queue would broadcast the reporter's source text instead.
            raise Unsupported("broadcast")
        return str(payload[1])


class _Cases:
    """Case ids and their generated JadenScript bodies (global per build)."""

    def __init__(self, rng: random.Random | None = None):
        self._ids: dict[tuple, _Case] = {}
        self.by_id: dict[int, _Case] = {}
        self.rng = rng

    def add(self, key: tuple, source: str) -> int:
        case = self._ids.get(key)
        if case is not None:
            return case.ident
        if self.rng is None:
            ident = len(self._ids) + 1
        else:
            while True:
                ident = self.rng.randrange(1, 1 << 24)
                if ident not in self.by_id:
                    break
        case = _Case(key, source, ident)
        self._ids[key] = case
        self.by_id[ident] = case
        return ident

    #: Generic cases
    @property
    def push_k(self) -> int:
        return self.add(
            ("push_k",),
            "        __vm_push(__vm_base, __vm_k.get(__vm_stack.get(__vm_base + 2)));",
        )

    def load(self, name: str) -> int:
        return self.add(("load", name), f"        __vm_push(__vm_base, {name});")

    def load_param(self, slot: int) -> int:
        return self.add(
            ("load_param", slot),
            f"        __vm_push(__vm_base, __vm_stack.get(__vm_base + 8 + {slot}));",
        )

    def store(self, name: str) -> int:
        return self.add(
            ("store", name),
            f"        __vm_pop_into(__vm_base, 3);\n        {name} = __vm_stack.get(__vm_base + 3);",
        )

    def change(self, name: str) -> int:
        return self.add(
            ("change", name),
            f"        __vm_pop_into(__vm_base, 3);\n        {name} += __vm_stack.get(__vm_base + 3);",
        )

    def binary(self, name: str, operator: str) -> int:
        # These are JadenScript operators, so codegen lowers them to the
        # Scratch blocks: ``&&`` becomes ``operator_and`` (which casts both
        # inputs the way Scratch does) and ``==`` uses ``Cast.compare``.
        return self.add(
            ("bin", name),
            "        __vm_pop_into(__vm_base, 4);\n"
            "        __vm_pop_into(__vm_base, 3);\n"
            f"        __vm_push(__vm_base, __vm_stack.get(__vm_base + 3) {operator} "
            "__vm_stack.get(__vm_base + 4));",
        )

    @property
    def not_(self) -> int:
        return self.add(
            ("not",),
            "        __vm_pop_into(__vm_base, 3);\n"
            "        __vm_push(__vm_base, !(__vm_stack.get(__vm_base + 3)));",
        )

    @property
    def join(self) -> int:
        return self.add(
            ("join",),
            "        __vm_pop_into(__vm_base, 4);\n"
            "        __vm_pop_into(__vm_base, 3);\n"
            "        __vm_push(__vm_base, join(__vm_stack.get(__vm_base + 3), "
            "__vm_stack.get(__vm_base + 4)));",
        )

    @property
    def jmp(self) -> int:
        return self.add(
            ("jmp",), "        __vm_stack.set(__vm_base + 0, __vm_stack.get(__vm_base + 2));"
        )

    @property
    def back_jmp(self) -> int:
        # A loop back edge is a Scratch yield point, so broadcasts queued in
        # the iteration are delivered before the next pass starts.
        return self.add(
            ("back_jmp",),
            "        __vm_flush_broadcast(__vm_base);\n"
            "        __vm_stack.set(__vm_base + 0, __vm_stack.get(__vm_base + 2));",
        )

    @property
    def jz(self) -> int:
        return self.add(
            ("jz",),
            "        __vm_pop_into(__vm_base, 3);\n"
            "        if (!(__vm_stack.get(__vm_base + 3))) {\n"
            "            __vm_stack.set(__vm_base + 0, __vm_stack.get(__vm_base + 2));\n"
            "        }",
        )

    @property
    def jnz(self) -> int:
        return self.add(
            ("jnz",),
            "        __vm_pop_into(__vm_base, 3);\n"
            "        if (__vm_stack.get(__vm_base + 3)) {\n"
            "            __vm_stack.set(__vm_base + 0, __vm_stack.get(__vm_base + 2));\n"
            "        }",
        )

    @property
    def wait_until(self) -> int:
        # A false condition returns to the condition expression, which is a
        # loop back edge and therefore flushes queued broadcasts. A true
        # condition falls through to the next instruction.
        return self.add(
            ("wait_until",),
            "        __vm_pop_into(__vm_base, 3);\n"
            "        if (!(__vm_stack.get(__vm_base + 3))) {\n"
            "            __vm_flush_broadcast(__vm_base);\n"
            "            __vm_stack.set(__vm_base + 0, __vm_stack.get(__vm_base + 2));\n"
            "        }",
        )

    @property
    def tmp_load(self) -> int:
        return self.add(
            ("tmp_load",),
            "        __vm_push(__vm_base, __vm_stack.get(__vm_base + 8 + "
            "__vm_stack.get(__vm_base + 2)));",
        )

    @property
    def tmp_store(self) -> int:
        return self.add(
            ("tmp_store",),
            "        __vm_pop_into(__vm_base, 8 + __vm_stack.get(__vm_base + 2));",
        )

    def tmp_store_at(self, slot: int) -> int:
        return self.add(
            ("tmp_store_at", slot),
            f"        __vm_pop_into(__vm_base, 8 + {slot});",
        )

    def native_procedure(
        self, name: str, arguments: str, flush: bool = False
    ) -> int:
        body = f"        {name}({arguments});"
        if flush:
            body = "        __vm_flush_broadcast(__vm_base);\n" + body
        return self.add(
            ("native_procedure", name, arguments, flush),
            body,
        )

    def native_property(self, name: str, operator: str, value: str) -> int:
        return self.add(
            ("native_property", name, operator, value),
            f"        {name} {operator} {value};",
        )

    def native_reporter(
        self, name: str, arguments: str, property_read: bool
    ) -> int:
        read = name if property_read else f"{name}({arguments})"
        return self.add(
            ("native_reporter", name, arguments, property_read),
            f"        __vm_push(__vm_base, {read});",
        )

    @property
    def say(self) -> int:
        return self.add(
            ("say",),
            "        __vm_pop_into(__vm_base, 3);\n"
            "        Looks.say(__vm_stack.get(__vm_base + 3));",
        )

    @property
    def say_for_secs(self) -> int:
        return self.add(
            ("say_for_secs",),
            "        __vm_pop_into(__vm_base, 4);\n"
            "        __vm_pop_into(__vm_base, 3);\n"
            "        __vm_flush_broadcast(__vm_base);\n"
            "        Looks.sayForSecs(__vm_stack.get(__vm_base + 3), "
            "__vm_stack.get(__vm_base + 4));",
        )

    @property
    def wait(self) -> int:
        return self.add(
            ("wait",),
            "        __vm_pop_into(__vm_base, 3);\n"
            "        __vm_flush_broadcast(__vm_base);\n"
            "        Control.wait(__vm_stack.get(__vm_base + 3));",
        )

    @property
    def halt(self) -> int:
        # ``stop this script`` inside a custom block only returns to its
        # caller. The interpreter uses pc = 0 as "finished"; __vm_run sees it
        # and returns through __vm_start to the hat, which ends the thread.
        return self.add(
            ("halt",),
            "        __vm_flush_broadcast(__vm_base);\n"
            "        __vm_stack.set(__vm_base + 0, 0);\n        return;",
        )

    def broadcast(self, message: str, wait: bool) -> int:
        text = json.dumps(message, ensure_ascii=False)
        if wait:
            return self.add(
                ("broadcast", message, True),
                f"        __vm_flush_broadcast(__vm_base);\n"
                f"        Control.broadcastAndWait({text});",
            )
        return self.add(
            ("broadcast", message, False),
            f"        __vm_queue_broadcast(__vm_base, {text});",
        )


def _runtime_source(
    cases: _Cases,
    used: set[int],
    stride: int,
    arena_max: int,
    budget: int,
    max_params: int = 0,
    has_procedures: bool = False,
    style: str = "h0",
    programs: tuple = (),
    rng: random.Random | None = None,
    split: bool = True,
    crypt: int = 1,
    check: int | None = None,
) -> str:
    if style == "h0":
        return _runtime_source_switch(
            cases,
            stride,
            arena_max,
            budget,
            max_params=max_params,
            has_procedures=has_procedures,
            programs=programs,
            rng=rng,
            split=split,
            crypt=crypt,
            check=check,
        )
    steps = "\n".join(
        "        __vm_step(__vm_base);\n"
        "        if (__vm_stack.get(__vm_base + 0) == 0) { "
        "__vm_free.add(__vm_base); "
        "__vm_active.set(1, __vm_active.get(1) - 1); "
        "if (__vm_active.get(1) == 0) { __vm_next.set(1, 0); "
        "__vm_free.clear(); } return; }"
        for _ in range(max(1, budget))
    )
    lines = [
        "class __vm_runtime {",
        "    private void __vm_push(__vm_base, value) {",
        "        __vm_stack.set(__vm_base + 5, __vm_stack.get(__vm_base + 5) + 1);",
        "        __vm_stack.set(__vm_stack.get(__vm_base + 5), value);",
        "    }",
        "    private void __vm_pop_into(__vm_base, slot) {",
        "        __vm_stack.set(__vm_base + slot, __vm_stack.get(__vm_stack.get(__vm_base + 5)));",
        "        __vm_stack.set(__vm_base + 5, __vm_stack.get(__vm_base + 5) - 1);",
        "    }",
        "    private void __vm_queue_broadcast(__vm_base, message) {",
        "        __vm_flush_broadcast(__vm_base);",
        "        __vm_stack.set(__vm_base + 6, message);",
        "        __vm_stack.set(__vm_base + 7, 1);",
        "    }",
        "    private void __vm_flush_broadcast(__vm_base) {",
        "        if (__vm_stack.get(__vm_base + 7) == 1) {",
        "            __vm_stack.set(__vm_base + 7, 0);",
        "            Control.broadcast(__vm_stack.get(__vm_base + 6));",
        "        }",
        "    }",
        "    private void __vm_step(__vm_base) {",
        "        __vm_stack.set(__vm_base + 1, __vm_ops.get(__vm_stack.get(__vm_base + 0)));",
        "        __vm_stack.set(__vm_base + 2, __vm_args.get(__vm_stack.get(__vm_base + 0)));",
        "        __vm_stack.set(__vm_base + 0, __vm_stack.get(__vm_base + 0) + 1);",
    ]
    first = True
    for ident in sorted(used):
        case = cases.by_id[ident]
        keyword = "if" if first else "elif"
        first = False
        lines.append(f"        {keyword} (__vm_stack.get(__vm_base + 1) == {ident}) {{")
        lines.append(case.source)
        lines.append("        }")
    lines.append(
        "        else { __vm_stack.set(__vm_base + 0, 0); return; }"
    )
    lines.append("    }")
    lines.append("    private void __vm_run(__vm_base) {")
    lines.append("        while (1) {")
    lines.append(steps)
    lines.append("        }")
    lines.append("    }")
    # ``__vm_stack`` starts at ``arena_max`` slots, but projects such as a
    # particle field can have more live frames than that (one per clone plus
    # the hat that spawns them).  Wrapping ``__vm_next`` back to zero there
    # would reuse slots that still belong to running frames, ending their
    # scripts.  Grow the list by one frame instead.
    alloc = [
        f"        if (__vm_next.get(1) + {stride} > __vm_stack.length()) {{ "
        f"repeat ({stride}) {{ __vm_stack.add(\"\"); }} }}",
        f"        __vm_next.set(1, __vm_next.get(1) + {stride});",
        f"        __vm_slot = __vm_next.get(1) - {stride} + 1;",
    ]
    if has_procedures:
        args = ["__vm_prog", "__vm_temps", "__vm_argc"]
        args += [f"__vm_a{i}" for i in range(max_params)]
        lines.append(f"    private void __vm_proc_start({', '.join(args)}) {{")
        if check is not None:
            lines.append(f"        if (__vm_check.get(1) != {check}) {{ return; }}")
        lines.append(
            "        if (__vm_active.get(1) == 0) { "
            "__vm_next.set(1, 0); __vm_free.clear(); }"
        )
        lines.append("        if (__vm_free.length() > 0) {")
        lines.append("            __vm_slot = __vm_free.get(__vm_free.length());")
        lines.append("            __vm_free.delete(__vm_free.length());")
        lines.append("        } else {")
        for line in alloc:
            lines.append("    " + line)
        lines.append("        }")
        lines.append("        __vm_active.set(1, __vm_active.get(1) + 1);")
        lines.append("        __vm_stack.set(__vm_slot + 7, 0);")
        lines.append("        __vm_stack.set(__vm_slot, __vm_prog);")
        lines.append(
            "        __vm_stack.set(__vm_slot + 5, "
            "__vm_slot + 8 + __vm_argc + __vm_temps);"
        )
        lines.append("        __vm_stack.set(__vm_slot + 8, 0);")
        for index in range(max_params):
            lines.append(
                f"        if (__vm_argc > {index}) {{ "
                f"__vm_stack.set(__vm_slot + {8 + index}, "
                f"__vm_a{index}); }} else {{ "
                f"__vm_stack.set(__vm_slot + {8 + index}, 0); }}"
            )
        lines.append("        __vm_run(__vm_slot);")
        lines.append("    }")
    lines.append("    private void __vm_start(__vm_prog, __vm_frame) {")
    if check is not None:
        lines.append(f"        if (__vm_check.get(1) != {check}) {{ return; }}")
    lines.append(
        "        if (__vm_active.get(1) == 0) { "
        "__vm_next.set(1, 0); __vm_free.clear(); }"
    )
    lines.append("        if (__vm_free.length() > 0) {")
    lines.append("            __vm_slot = __vm_free.get(__vm_free.length());")
    lines.append("            __vm_free.delete(__vm_free.length());")
    lines.append("        } else {")
    for line in alloc:
        lines.append("    " + line)
    lines.append("        }")
    lines.append("        __vm_active.set(1, __vm_active.get(1) + 1);")
    lines.append("        __vm_stack.set(__vm_slot + 7, 0);")
    lines.append("        __vm_stack.set(__vm_slot, __vm_prog);")
    lines.append(
        "        __vm_stack.set(__vm_slot + 5, "
        "__vm_slot + 8 + __vm_frame);"
    )
    lines.append("        __vm_stack.set(__vm_slot + 8, 0);")
    lines.append("        __vm_run(__vm_slot);")
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines)


def _runtime_source_switch(
    cases: _Cases,
    stride: int,
    arena_max: int,
    budget: int,
    max_params: int = 0,
    has_procedures: bool = False,
    programs: tuple = (),
    rng: random.Random | None = None,
    split: bool = True,
    crypt: int = 1,
    check: int | None = None,
) -> str:
    """A hand-written-state-machine style backend.

    Instead of ``__vm_ops``/``__vm_args`` and one shared ``__vm_step``
    dispatch, each instruction gets its own numbered ``switch`` case.  This is
    the shape of ``examples/h0.jsw``: one state variable, explicit cases, and
    a single loop around the switch.  The frame slots and stack are unchanged,
    so all the existing control-flow and native-call lowering still applies.
    """
    jump_cases = {
        ident
        for ident, case in cases.by_id.items()
        if case.key[0] in ("jmp", "back_jmp", "jz", "jnz", "wait_until")
    }
    instructions: list[tuple[int, int, int, int, str]] = []
    for program in programs:
        labels = program.labels
        if not labels:
            labels = list(range(program.start, program.start + len(program.code)))
        for local, instr in enumerate(program.code):
            if instr.case in jump_cases:
                target = instr.arg
                arg = (
                    labels[target - 1]
                    if 0 < target <= len(labels)
                    else 0
                )
            else:
                arg = instr.arg
            pc = labels[local]
            next_pc = labels[local + 1] if local + 1 < len(labels) else 0
            instructions.append(
                (pc, arg, next_pc, instr.case, cases.by_id[instr.case].source)
            )
    if crypt >= 3 and rng is not None:
        used_labels = {item[0] for item in instructions}
        for _ in range(3):
            while True:
                label = rng.randrange(1, 1 << 24)
                if label not in used_labels:
                    used_labels.add(label)
                    break
            decoy = cases.push_k
            instructions.append((label, 0, 0, decoy, cases.by_id[decoy].source))
    if rng is not None:
        rng.shuffle(instructions)
    steps = "\n".join(
        "        __vm_switch(__vm_base);\n"
        "        if (__vm_stack.get(__vm_base + 0) == 0) { "
        "__vm_free.add(__vm_base); "
        "__vm_active.set(1, __vm_active.get(1) - 1); "
        "if (__vm_active.get(1) == 0) { __vm_next.set(1, 0); "
        "__vm_free.clear(); } return; }"
        for _ in range(max(1, budget))
    )
    lines = [
        "class __vm_runtime {",
        "    private void __vm_push(__vm_base, value) {",
        "        __vm_stack.set(__vm_base + 5, __vm_stack.get(__vm_base + 5) + 1);",
        "        __vm_stack.set(__vm_stack.get(__vm_base + 5), value);",
        "    }",
        "    private void __vm_pop_into(__vm_base, slot) {",
        "        __vm_stack.set(__vm_base + slot, __vm_stack.get(__vm_stack.get(__vm_base + 5)));",
        "        __vm_stack.set(__vm_base + 5, __vm_stack.get(__vm_base + 5) - 1);",
        "    }",
        "    private void __vm_queue_broadcast(__vm_base, message) {",
        "        __vm_flush_broadcast(__vm_base);",
        "        __vm_stack.set(__vm_base + 6, message);",
        "        __vm_stack.set(__vm_base + 7, 1);",
        "    }",
        "    private void __vm_flush_broadcast(__vm_base) {",
        "        if (__vm_stack.get(__vm_base + 7) == 1) {",
        "            __vm_stack.set(__vm_base + 7, 0);",
        "            Control.broadcast(__vm_stack.get(__vm_base + 6));",
        "        }",
        "    }",
    ]
    helper_names: dict[int, str] = {}
    if split:
        for ident in sorted({item[3] for item in instructions}):
            helper_names[ident] = f"__vm_h{ident}"
            lines.append(f"    private void {helper_names[ident]}(__vm_base) {{")
            lines.append(cases.by_id[ident].source)
            lines.append("    }")
    lines.extend([
        "    private void __vm_switch(__vm_base) {",
        "        switch (__vm_stack.get(__vm_base + 0)) {",
    ])
    for pc, arg, next_pc, ident, source in instructions:
        lines.append(f"        case {pc} {{")
        lines.append(f"            __vm_stack.set(__vm_base + 2, {arg});")
        lines.append(
            f"            __vm_stack.set(__vm_base + 0, {next_pc});"
        )
        if split:
            lines.append(f"            {helper_names[ident]}(__vm_base);")
        else:
            lines.append(source)
        lines.append("        }")
    lines.extend([
        "        case _ { __vm_stack.set(__vm_base + 0, 0); }",
        "        }",
        "    }",
        "    private void __vm_run(__vm_base) {",
        "        while (1) {",
        steps,
        "        }",
        "    }",
    ])
    alloc = [
        f"        if (__vm_next.get(1) + {stride} > __vm_stack.length()) {{ "
        f"repeat ({stride}) {{ __vm_stack.add(\"\"); }} }}",
        f"        __vm_next.set(1, __vm_next.get(1) + {stride});",
        f"        __vm_slot = __vm_next.get(1) - {stride} + 1;",
    ]
    if has_procedures:
        args = ["__vm_prog", "__vm_temps", "__vm_argc"]
        args += [f"__vm_a{i}" for i in range(max_params)]
        lines.append(f"    private void __vm_proc_start({', '.join(args)}) {{")
        if check is not None:
            lines.append(f"        if (__vm_check.get(1) != {check}) {{ return; }}")
        lines.append(
            "        if (__vm_active.get(1) == 0) { "
            "__vm_next.set(1, 0); __vm_free.clear(); }"
        )
        lines.append("        if (__vm_free.length() > 0) {")
        lines.append("            __vm_slot = __vm_free.get(__vm_free.length());")
        lines.append("            __vm_free.delete(__vm_free.length());")
        lines.append("        } else {")
        for line in alloc:
            lines.append("    " + line)
        lines.append("        }")
        lines.append("        __vm_active.set(1, __vm_active.get(1) + 1);")
        lines.append("        __vm_stack.set(__vm_slot + 7, 0);")
        lines.append("        __vm_stack.set(__vm_slot, __vm_prog);")
        lines.append(
            "        __vm_stack.set(__vm_slot + 5, "
            "__vm_slot + 8 + __vm_argc + __vm_temps);"
        )
        lines.append("        __vm_stack.set(__vm_slot + 8, 0);")
        for index in range(max_params):
            lines.append(
                f"        if (__vm_argc > {index}) {{ "
                f"__vm_stack.set(__vm_slot + {8 + index}, "
                f"__vm_a{index}); }} else {{ "
                f"__vm_stack.set(__vm_slot + {8 + index}, 0); }}"
            )
        lines.append("        __vm_run(__vm_slot);")
        lines.append("    }")
    lines.append("    private void __vm_start(__vm_prog, __vm_frame) {")
    if check is not None:
        lines.append(f"        if (__vm_check.get(1) != {check}) {{ return; }}")
    lines.append(
        "        if (__vm_active.get(1) == 0) { "
        "__vm_next.set(1, 0); __vm_free.clear(); }"
    )
    lines.append("        if (__vm_free.length() > 0) {")
    lines.append("            __vm_slot = __vm_free.get(__vm_free.length());")
    lines.append("            __vm_free.delete(__vm_free.length());")
    lines.append("        } else {")
    for line in alloc:
        lines.append("    " + line)
    lines.append("        }")
    lines.append("        __vm_active.set(1, __vm_active.get(1) + 1);")
    lines.append("        __vm_stack.set(__vm_slot + 7, 0);")
    lines.append("        __vm_stack.set(__vm_slot, __vm_prog);")
    lines.append(
        "        __vm_stack.set(__vm_slot + 5, "
        "__vm_slot + 8 + __vm_frame);"
    )
    lines.append("        __vm_stack.set(__vm_slot + 8, 0);")
    lines.append("        __vm_run(__vm_slot);")
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines)


def _collect_script(target: Target, start: str) -> set[str]:
    """Every block reachable from ``start`` (next links and input reports)."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        bid = stack.pop()
        if bid in seen or bid not in target.blocks:
            continue
        seen.add(bid)
        block = target.blocks[bid]
        if block.next:
            stack.append(block.next)
        for raw in block.inputs.values():
            if not isinstance(raw, list):
                continue
            for index in (1, 2):
                if len(raw) > index and isinstance(raw[index], str):
                    stack.append(raw[index])
    return seen


def _nodes(node):
    """Every dataclass node reachable from ``node`` (lists and tuples too)."""
    if dataclasses.is_dataclass(node):
        yield node
        for slot in dataclasses.fields(node):
            yield from _nodes(getattr(node, slot.name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _nodes(item)


def _called_names(stmts) -> set[str]:
    return {
        node.name
        for node in _nodes(stmts)
        if isinstance(node, A.Call)
    }


def _infinite(stmts) -> bool:
    for node in _nodes(stmts):
        if not isinstance(node, A.WhileStmt):
            continue
        cond = node.cond
        if isinstance(cond, A.NumberLit):
            try:
                if float(cond.raw) != 0:
                    return True
            except (TypeError, ValueError):
                pass
        if isinstance(cond, A.BoolLit) and cond.value:
            return True
    return False


def _unsafe_calls(cls: A.ClassDecl) -> set[str]:
    """Functions a VM script must not call natively.

    A native call runs the whole custom block inside one ``__vm_step``.  That
    is fine for an ordinary block, but a block with ``while (1)`` would never
    return to the interpreter, and a clone started from it could take the
    scheduler's only slot.  Such a script stays native, where Scratch already
    knows how to schedule it.
    """
    by_name = {}
    for fn in cls.funcs:
        by_name.setdefault(fn.proccode, fn)
        by_name.setdefault(fn.qualified, fn)
    unsafe = {fn.qualified for fn in cls.funcs if _infinite(fn.body)}
    changed = True
    while changed:
        changed = False
        for fn in cls.funcs:
            if fn.qualified in unsafe:
                continue
            if any(
                (called := by_name.get(name)) is not None
                and called.qualified in unsafe
                for name in _called_names(fn.body)
            ):
                unsafe.add(fn.qualified)
                changed = True
    return unsafe


def _emit_vm_reset_hat(project: Project) -> None:
    """Clear the allocator at green flag, but never under a running frame."""
    stage = project.stage
    target = next((t for t in reversed(project.targets) if not t.is_stage), None)
    if target is None:
        return
    next_list = stage.list_id("__vm_next")
    free_list = stage.list_id("__vm_free")
    active_list = stage.list_id("__vm_active")
    if next_list is None or free_list is None or active_list is None:
        return
    hat = target.new_block("event_whenflagclicked")
    hat.top_level = True
    active = target.new_block("data_itemoflist")
    active.set_field("LIST", "__vm_active", active_list)
    active.set_input("INDEX", Lit(O.MATH_NUM, "1"))
    zero = target.new_block("operator_equals")
    zero.set_input("OPERAND1", Ref(active))
    zero.set_input("OPERAND2", Lit(O.MATH_NUM, "0"))
    check = target.new_block("control_if")
    check.set_input("CONDITION", Ref(zero))
    reset_next = target.new_block("data_replaceitemoflist")
    reset_next.set_field("LIST", "__vm_next", next_list)
    reset_next.set_input("INDEX", Lit(O.MATH_NUM, "1"))
    reset_next.set_input("ITEM", Lit(O.MATH_NUM, "0"))
    clear_free = target.new_block("data_deletealloflist")
    clear_free.set_field("LIST", "__vm_free", free_list)
    hat.next = check.id
    check.set_input("SUBSTACK", Ref(reset_next))
    reset_next.next = clear_free.id
    ids = (hat.id, active.id, zero.id, check.id, reset_next.id, clear_free.id)
    target.blocks = {
        **{bid: target.blocks[bid] for bid in ids},
        **{bid: block for bid, block in target.blocks.items() if bid not in ids},
    }


def protect_project(
    project: Project,
    codegen,
    budget: int = 1,
    style: str = "h0",
    split: bool = True,
    crypt: int = 1,
) -> None:
    """Virtualise every supported hat body. Called once, before obfuscation."""
    if style not in ("list", "h0"):
        raise ValueError("style must be 'list' or 'h0'")

    def note(code: str, message: str, node=None) -> None:
        """Record a diagnostic.

        vmProtect works on the compiled blocks, so most of its notes are about
        the build rather than one line. Where a sprite is named, the note is
        anchored at that sprite's ``class`` line so the editor can show it
        somewhere useful; the rest go to line 1 rather than to a line that does
        not exist.
        """
        if node is None:
            entry = f"1:1: {code} {message}"
            if entry not in codegen.diagnostics:
                codegen.diagnostics.append(entry)
            return
        codegen.diag(node, code, message)

    if "vanillaScratch" in codegen.features:
        note(
            "W0030",
            "vmProtect skipped: a vanillaScratch build keeps its ordinary blocks",
        )
        return
    if codegen.plan is None:
        return

    class_for_target: dict[str, A.ClassDecl] = {}
    for cls in codegen.program.classes:
        real = codegen.target_name.get(cls.name, cls.name)
        class_for_target[real] = cls

    # The Stage is left as ordinary blocks in this slice: its state is what the
    # interpreter reads, and a Stage runtime would be a second copy of the same
    # code for little gain. Sprite scripts are enough to prove the mechanism.
    crypt = max(0, min(3, crypt))
    seed = getattr(codegen, "vm_seed", None)
    rng = (
        random.Random(seed if seed is not None else secrets.randbits(64))
        if crypt > 0
        else None
    )
    cases = _Cases(rng)
    ext_table = extension_table(codegen.features)
    ext_hats = extension_hats(ext_table)
    consts: list = []
    pools = {
        pool.list_id: list(pool.items)
        for pool in getattr(codegen, "_pools", ())
    }
    programs: list[_Program] = []
    skipped: dict[str, tuple[int, str]] = {}

    for target in project.targets:
        if target.is_stage:
            continue
        cls = class_for_target.get(target.name)
        if cls is None:
            continue
        blocks_json = {bid: block.to_json() for bid, block in target.blocks.items()}
        calls = {}
        for fn in cls.funcs:
            calls.setdefault(codegen._proccode(fn), fn)
        unsafe = _unsafe_calls(cls)

        # A warp custom block runs its whole body without yielding, so the VM
        # loop must never run *inside* one: `while (1)` there would spin until
        # the warp timer expires, every frame.  Warp mode is scoped to the
        # callee's stack frame (`sequencer.stepToProcedure` sets it on the
        # frame it pushes), so the rule is about the direction of the call:
        #
        #   warp proc -> anything it calls     native (inside warp mode)
        #   non-warp proc -> warp proc         virtualised (safe: the warp
        #                                      frame is popped on return)
        #
        # A caller of a warp procedure therefore keeps its protection; only the
        # warp procedure and its own callees stay native.
        by_name = {}
        for fn in cls.funcs:
            by_name.setdefault(fn.proccode, fn)
            by_name.setdefault(fn.qualified, fn)
        warp_fns = set()
        for block in target.blocks.values():
            if block.opcode != "procedures_definition":
                continue
            prototype_id = (block.inputs.get("custom_block") or [None, None])[1]
            prototype = target.blocks.get(prototype_id)
            if prototype is None or not prototype.mutation:
                continue
            if str(prototype.mutation.get("warp", "")).lower() != "true":
                continue
            fn = calls.get(prototype.mutation.get("proccode"))
            if fn is not None:
                warp_fns.add(fn.qualified)
        # Close over the *callees*: everything reachable from a warp body runs
        # in warp mode too, so it has to stay native as well.
        changed = True
        while changed:
            changed = False
            for fn in cls.funcs:
                if fn.qualified not in warp_fns:
                    continue
                for name in _called_names(fn.body):
                    called = by_name.get(name)
                    if called is not None and called.qualified not in warp_fns:
                        warp_fns.add(called.qualified)
                        changed = True

        def compile_body(block, param_names, is_procedure=False, fn=None):
            params = {name: index for index, name in enumerate(param_names)}
            compiler = _Compiler(
                blocks_json,
                cases,
                consts,
                calls,
                unsafe,
                pools,
                params,
                ext_table,
            )
            try:
                compiler.chain(blocks_json.get(block.next))
            except Unsupported as exc:
                count, first = skipped.get(target.name, (0, exc.opcode))
                skipped[target.name] = (count + 1, first)
                return
            if compiler.depth != 0:
                count, first = skipped.get(target.name, (0, "expression"))
                skipped[target.name] = (count + 1, first)
                return
            compiler.emit(cases.halt)
            programs.append(
                _Program(
                    target=target,
                    class_name=cls.name,
                    hat=block,
                    params=len(params),
                    is_procedure=is_procedure,
                    fn=fn,
                    code=compiler.code,
                    temps=compiler.temps,
                    max_depth=compiler.max_depth,
                    used=compiler.used,
                )
            )

        for block in list(target.blocks.values()):
            if (
                not block.top_level
                or (
                    block.opcode not in _HATS
                    and block.opcode not in ext_hats
                )
                or not block.next
            ):
                continue
            compile_body(block, [])

        for block in list(target.blocks.values()):
            if block.opcode != "procedures_definition" or not block.next:
                continue
            prototype_id = (block.inputs.get("custom_block") or [None, None])[1]
            prototype = target.blocks.get(prototype_id)
            if prototype is None or not prototype.mutation:
                continue
            proccode = prototype.mutation.get("proccode")
            fn = calls.get(proccode) if isinstance(proccode, str) else None
            if fn is None:
                continue
            if fn.qualified in warp_fns:
                count, first = skipped.get(target.name, (0, "warp custom block"))
                skipped[target.name] = (count + 1, first)
                continue
            compile_body(block, fn.params, True, fn)

    if not programs:
        for name, (count, opcode) in sorted(skipped.items()):
            note(
                "W0031",
                f"vmProtect left {count} program(s) native in {name!r}; "
                f"first unsupported block: {opcode}",
                class_for_target.get(name),
            )
        return
    left = sum(count for count, _ in skipped.values())
    stride = max(MIN_STRIDE, max(program.stride for program in programs))
    if stride > 512:
        note(
            "W0031",
            "vmProtect left every program native: the deepest "
            f"expression needs {stride} slots, more than the 512-slot limit",
        )
        return
    note(
        "I0004",
        f"vmProtect virtualised {len(programs)} program(s)"
        + (f"; {left} left native" if left else ""),
    )
    arena_max = MAX_THREADS * stride

    # One global instruction stream; every program gets a start index.  h0
    # assigns a random label per instruction instead, so the case order and
    # the numeric state do not disclose the source order.
    ops: list[int] = []
    code_args: list[int] = []
    jump_cases = {
        ident
        for ident, case in cases.by_id.items()
        if case.key[0] in ("jmp", "back_jmp", "jz", "jnz", "wait_until")
    }
    if style == "h0":
        used_labels: set[int] = set()
        if rng is None:
            for program in programs:
                for _ in program.code:
                    label = len(used_labels) + 1
                    used_labels.add(label)
                    program.labels.append(label)
                program.start = program.labels[0] if program.labels else 1
        else:
            for program in programs:
                for _ in program.code:
                    while True:
                        label = rng.randrange(1, 1 << 24)
                        if label not in used_labels:
                            used_labels.add(label)
                            break
                    program.labels.append(label)
                program.start = program.labels[0] if program.labels else 1
    else:
        for program in programs:
            program.start = len(ops) + 1
            offset = program.start - 1
            for instr in program.code:
                ops.append(instr.case)
                # Jump targets are local to the program; the VM executes the
                # shared instruction stream at an absolute pc.
                code_args.append(
                    instr.arg + offset if instr.case in jump_cases else instr.arg
                )

    if crypt >= 3:
        # Extra instructions are never reachable from a program start.  A
        # tamperer who edits the dispatch table still has to explain them.
        for _ in range(3):
            if style == "h0":
                while True:
                    label = rng.randrange(1, 1 << 24) if rng else len(used_labels) + 1
                    if label not in used_labels:
                        used_labels.add(label)
                        break
            else:
                ops.append(cases.push_k)
                code_args.append(0)

    check = None
    if crypt >= 2:
        values: list[object] = []
        if style == "list":
            values = [*ops, *code_args, *consts]
        else:
            values = [
                *[label for program in programs for label in program.labels],
                *[instr.arg for program in programs for instr in program.code],
                *consts,
            ]
        check = zlib.crc32(
            json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ) or 1

    # Declare the VM state on the Stage and teach codegen that these names are
    # lists, not local declarations.
    state = {
        "__vm_next": [0],
        "__vm_free": [],
        "__vm_active": [0],
    }
    if style == "list":
        state["__vm_ops"] = list(ops)
        state["__vm_args"] = list(code_args)
    # h0 style addresses instructions with switch case labels, so the
    # opcode/argument streams would be dead weight there.
    state["__vm_k"] = list(consts)
    state["__vm_stack"] = [""] * arena_max
    if check is not None:
        state["__vm_check"] = [check]
    for name, items in state.items():
        codegen.plan.lists[name] = items
        codegen.list_names.add(name)
        project.stage.declare_list(name, items)
    _emit_vm_reset_hat(project)
    # One runtime per sprite that actually has a protected script.
    by_target: dict[str, list[_Program]] = {}
    for program in programs:
        by_target.setdefault(program.target.name, []).append(program)

    saved_warp = codegen.warp
    codegen.warp = False
    for target_name, group in by_target.items():
        target = group[0].target
        cls_name = group[0].class_name
        used = set().union(*(program.used for program in group))
        procedures = [program for program in group if program.is_procedure]
        max_params = max((program.params for program in procedures), default=0)
        source = _runtime_source(
            cases,
            used,
            stride,
            arena_max,
            budget,
            max_params=max_params,
            has_procedures=bool(procedures),
            style=style,
            programs=tuple(group),
            rng=rng,
            split=split,
            crypt=crypt,
            check=check,
        )
        from .lexer import tokenize
        from .parser import Parser

        runtime = Parser(tokenize(source)).parse()
        runtime_cls = runtime.classes[0]
        # Emit the runtime as part of the *user's* class: variable and call
        # resolution uses ``class_name`` for both.
        runtime_cls.name = cls_name
        funcs = {fn.proccode: fn for fn in runtime_cls.funcs}

        # Register every runtime function before emitting any of them: the
        # helpers call each other and _emit_func resolves calls by name.
        for fn in runtime_cls.funcs:
            codegen.class_funcs.setdefault(cls_name, {})[fn.proccode] = fn
            codegen.class_qualified[(cls_name, fn.qualified)] = fn
            codegen.funcs_by_qualified.setdefault(fn.qualified, (cls_name, fn))

        from .codegen import Scope

        codegen.target = target
        codegen.class_name = cls_name
        codegen.namespace, codegen.namespaced = codegen.target_namespaces.get(
            target.name,
            (target.name, "namespacedNames" in codegen.features),
        )
        codegen.scope = Scope()
        codegen._script_y = max(
            (block.y for block in target.blocks.values() if block.top_level),
            default=-400,
        ) + 400
        for fn in runtime_cls.funcs:
            codegen._emit_func(target, runtime_cls, fn)

        for program in group:
            if program.is_procedure and program.fn is not None:
                fn = program.fn
                codegen.scope = Scope(params=set(fn.params))
                args = [
                    A.NumberLit(raw=str(program.start)),
                    A.NumberLit(raw=str(program.temps)),
                    A.NumberLit(raw=str(program.params)),
                ]
                args += [A.Name(parts=(name,)) for name in fn.params]
                args += [
                    A.StringLit(value="")
                    for _ in range(max_params - program.params)
                ]
                start = codegen._procedure_call(
                    funcs["__vm_proc_start"], args
                )
            else:
                codegen.scope = Scope()
                start = codegen._procedure_call(
                    funcs["__vm_start"],
                    [
                        A.NumberLit(raw=str(program.start)),
                        A.NumberLit(raw=str(program.temps)),
                    ],
                )
            old = program.hat.next
            if old in target.blocks:
                for bid in _collect_script(target, old):
                    target.blocks.pop(bid, None)
            start.parent = program.hat.id
            program.hat.next = start.id
    codegen.warp = saved_warp

    for name, (count, opcode) in sorted(skipped.items()):
        note(
            "W0031",
            f"vmProtect left {count} program(s) native in {name!r}; "
            f"first unsupported block: {opcode}",
            class_for_target.get(name),
        )
