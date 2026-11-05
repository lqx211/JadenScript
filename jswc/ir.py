"""Scratch block IR.

The IR mirrors the sb3 ``project.json`` structures closely (that is the point:
the backend should be a dumb serializer), while the value objects keep the
generator from having to remember the ``[3, id, [4, ""]]`` tuple encoding.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import opcodes as O

_B62 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class IdGen:
    """Deterministic id generator.

    Deterministic ids mean two builds of the same source produce byte-identical
    ``project.json`` files, which makes golden tests and diffs practical.
    """

    def __init__(self, seed: str = "jswc"):
        self.seed = seed
        self.counter = 0

    def next(self, hint: str = "") -> str:
        self.counter += 1
        digest = hashlib.sha1(f"{self.seed}:{hint}:{self.counter}".encode()).digest()
        number = int.from_bytes(digest, "big")
        out = []
        for _ in range(20):
            number, rem = divmod(number, 62)
            out.append(_B62[rem])
        return "".join(out)


# ---------------------------------------------------------------------------
# Values that can be plugged into an input slot
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Lit:
    prim: int
    value: str


@dataclass(frozen=True)
class Ref:
    """A reporter block, optionally with its obscured shadow.

    ``block`` may also be a raw primitive payload (``[12, "x", "id"]``), which
    Scratch uses when a variable sits in a slot that also has an obscured
    shadow; ``shadow`` may be an ``Inline`` for primitives that carry an id.
    """

    block: "Block | list"
    shadow: "Lit | Inline | None" = None


@dataclass(frozen=True)
class Inline:
    """A raw primitive such as ``[11, "Song name", "broadcastId"]``."""

    primitive: list


@dataclass(frozen=True)
class ShadowRef:
    """``[1, id]`` — a shadow block occupying an input slot (dropdowns)."""

    block: "Block"


Value = Lit | Ref | Inline | ShadowRef


def serialize_input(value: Value) -> list:
    if isinstance(value, Lit):
        return [1, [value.prim, value.value]]
    if isinstance(value, Inline):
        return [1, value.primitive]
    if isinstance(value, ShadowRef):
        return [1, value.block.id]
    if isinstance(value, Ref):
        head = value.block.id if isinstance(value.block, Block) else list(value.block)
        if value.shadow is None:
            return [3, head, [O.TEXT, ""]] if isinstance(head, list) else [2, head]
        if isinstance(value.shadow, Inline):
            return [3, head, value.shadow.primitive]
        return [3, head, [value.shadow.prim, value.shadow.value]]
    raise TypeError(f"not an input value: {value!r}")


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------
@dataclass
class Block:
    id: str
    opcode: str
    inputs: dict[str, list] = field(default_factory=dict)
    fields: dict[str, list] = field(default_factory=dict)
    next: str | None = None
    parent: str | None = None
    top_level: bool = False
    shadow: bool = False
    x: int = 0
    y: int = 0
    mutation: dict | None = None

    def set_input(self, name: str, value: Value) -> "Block":
        self.inputs[name] = serialize_input(value)
        return self

    def set_field(self, name: str, value: str, ident: str | None = None) -> "Block":
        self.fields[name] = [value, ident]
        return self

    def to_json(self) -> dict:
        out: dict = {
            "opcode": self.opcode,
            "next": self.next,
            "parent": self.parent,
            "inputs": self.inputs,
            "fields": self.fields,
            "shadow": self.shadow,
            "topLevel": self.top_level,
        }
        if self.top_level:
            out["x"] = self.x
            out["y"] = self.y
        if self.mutation is not None:
            out["mutation"] = self.mutation
        return out


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
class Target:
    def __init__(self, name: str, is_stage: bool = False):
        self.name = name
        self.is_stage = is_stage
        self.variables: dict[str, list] = {}  # var id -> [name, value]
        self.lists: dict[str, list] = {}  # list id -> [name, [items]]
        self.broadcasts: dict[str, str] = {}  # broadcast id -> name
        self.costumes: list[dict] = []
        self.sounds: list[dict] = []
        self.blocks: dict[str, Block] = {}
        self.x = 0
        self.y = 0
        self.size = 100
        self.direction = 90
        self.visible = True
        self.current_costume = 0
        self.volume = 100.0
        self.layer_order = 0
        self.rotation_style = "all around"
        self.draggable = False
        self.tempo = 60.0
        self.video_state = "on"
        self.video_transparency = 50.0
        self.text_to_speech_language = None
        self._var_ids: dict[str, str] = {}
        self._list_ids: dict[str, str] = {}
        self._idgen = IdGen(name)

    # -- variables ---------------------------------------------------------
    def declare_variable(self, name: str, value: object = 0) -> str:
        if name in self._var_ids:
            return self._var_ids[name]
        vid = IdGen(f"var:{self.name}:{name}").next("var")
        self._var_ids[name] = vid
        self.variables[vid] = [name, value]
        return vid

    def var_id(self, name: str) -> str | None:
        return self._var_ids.get(name)

    def declare_list(self, name: str, items: list | None = None) -> str:
        if name in self._list_ids:
            return self._list_ids[name]
        lid = IdGen(f"list:{self.name}:{name}").next("list")
        self._list_ids[name] = lid
        self.lists[lid] = [name, items or []]
        return lid

    def list_id(self, name: str) -> str | None:
        return self._list_ids.get(name)

    def declare_broadcast(self, name: str) -> str:
        for bid, bname in self.broadcasts.items():
            if bname == name:
                return bid
        bid = IdGen(f"broadcast:{self.name}:{name}").next("bcast")
        self.broadcasts[bid] = name
        return bid

    # -- blocks ------------------------------------------------------------
    def add(self, block: Block) -> Block:
        if block.id in self.blocks:  # pragma: no cover - guard
            raise ValueError(f"duplicate block id {block.id}")
        self.blocks[block.id] = block
        return block

    def new_block(self, opcode: str) -> Block:
        block = Block(id=self.idgen.next(opcode), opcode=opcode)
        return self.add(block)

    # ``idgen`` is injected by the code generator so ids stay project-wide.
    @property
    def idgen(self) -> IdGen:
        return self._idgen

    @idgen.setter
    def idgen(self, value: IdGen) -> None:
        self._idgen = value


class Project:
    def __init__(self, seed: str = "jswc"):
        self.idgen = IdGen(seed)
        self.stage = Target("Stage", is_stage=True)
        self.stage.idgen = self.idgen
        self.targets: list[Target] = [self.stage]
        self.extensions: list[str] = []
        self.extension_urls: dict[str, str] = {}
        self.assets: dict[str, bytes] = {}  # "<md5>.<ext>" -> bytes
        self.monitors: list[dict] = []
        self.comments: dict[str, dict] = {}  # target name -> {id: comment}

    def add_target(self, name: str) -> Target:
        target = Target(name)
        target.idgen = self.idgen
        self.targets.append(target)
        return target

    def use_extension(self, name: str) -> None:
        if not name:
            return
        if name not in self.extensions:
            self.extensions.append(name)
        url = O.EXTENSION_URLS.get(name)
        if url is not None:
            self.extension_urls.setdefault(name, url)

    def normalize(self) -> None:
        """Recompute every ``parent`` link from ``next``/input references.

        Emitting blocks out of order is normal (a reporter is created before
        the block that consumes it), so parent links are derived once at the
        end instead of being maintained by hand.
        """
        for target in self.targets:
            blocks = target.blocks
            for block in blocks.values():
                block.parent = None
                if block.top_level:
                    block.x = block.x if block.x else 0
            for block in blocks.values():
                if block.next is not None:
                    child = blocks.get(block.next)
                    if child is not None:
                        child.parent = block.id
                for raw in block.inputs.values():
                    if not isinstance(raw, list) or not raw:
                        continue
                    if raw[0] in (2, 3) and len(raw) > 1 and isinstance(raw[1], str):
                        child = blocks.get(raw[1])
                        if child is not None:
                            child.parent = block.id
                    elif raw[0] == 1 and len(raw) > 1 and isinstance(raw[1], str):
                        child = blocks.get(raw[1])
                        if child is not None:
                            child.parent = block.id


# ---------------------------------------------------------------------------
# Small construction helpers shared by the generator
# ---------------------------------------------------------------------------
def lit_number(text: str) -> Lit:
    return Lit(O.MATH_NUM, text)


def lit_text(text: str) -> Lit:
    return Lit(O.TEXT, text)


def num(value: int | float | str) -> Lit:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return Lit(O.MATH_NUM, str(value))


def colour(value: str) -> Lit:
    return Lit(O.COLOR, value)
