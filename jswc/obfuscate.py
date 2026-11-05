"""``doObfuscation``: replace every name the project exposes.

Publishing a compiled project shows its sprite names, variable names, list
names, custom block labels and broadcast names in the editor. This pass rewrites
all of them to deterministic random identifiers, so a published project reads as
blocks with no author naming left in it — including ``Sprite.function %s``
labels, which would otherwise spell out the whole call graph.

Thread variable names (``lmsTempVars2`` stores them as text) and the extension
menus that pick a list/variable by name follow along.

What is *not* renamed, because Scratch matches it by text from the asset list or
from the source file:

* costume and sound names (menus and the ``costumes``/``sounds`` arrays point
  at them),
* the names inside ``// monitor`` / ``// watch`` comments (those are source
  directives; the monitors they produce *are* renamed).

Broadcast names are renamed, so a project that builds a message at runtime
(``Control.broadcast(join(module, ".prop"))``) cannot find it any more.
``obfuscate_project`` returns the mapping and ``Codegen`` reports the computed
lookups that this breaks.

The output is deterministic: the same source compiled twice produces the same
hidden names, so diffs and golden tests keep working.
"""

from __future__ import annotations

import hashlib
import json
import re

from . import opcodes as O
from .ir import Project

_SEED = "jswc-hide-names"
_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BODY = _LETTERS + "0123456789"


def _word(digest: bytes, length: int) -> str:
    """``length`` characters, starting with a letter so it stays an identifier."""
    head = _LETTERS[digest[0] % len(_LETTERS)]
    body = "".join(_BODY[b % len(_BODY)] for b in digest[1:length])
    return head + body


class _Namer:
    def __init__(self, seed: str):
        self.seed = seed
        self.used: set[str] = set()

    def __call__(self, key: str, dotted: bool) -> str:
        attempt = 0
        while True:
            digest = hashlib.sha256(f"{self.seed}:{key}:{attempt}".encode()).digest()
            name = (
                f"{_word(digest[:6], 4)}.{_word(digest[6:12], 5)}"
                if dotted
                else _word(digest[:6], 6)
            )
            if name not in self.used:
                self.used.add(name)
                return name
            attempt += 1


_PLACEHOLDER = re.compile(r"%[sbn]")


def obfuscate_project(project: Project, seed: str = _SEED) -> dict[str, str]:
    """Rename classes, variables and lists in place.

    Returns ``original -> hidden`` so callers can report what changed (the
    mapping is many-to-one for names shared by different sprites).
    """
    namer = _Namer(seed)
    mapping: dict[str, str] = {}

    target_names = {
        target.name: namer(f"target:{target.name}", dotted=False)
        for target in project.targets
    }

    var_names: dict[str, str] = {}
    list_names: dict[str, str] = {}
    by_scope: dict[tuple[str, str, str], str] = {}
    for target in project.targets:
        for vid, entry in target.variables.items():
            original = entry[0]
            # A Stage variable keeps the two-segment ``Ns.name`` shape, so the
            # project still looks like it was written with that style.
            dotted = target.is_stage and "." in str(original)
            hidden = namer(f"var:{vid}", dotted=dotted)
            var_names[vid] = hidden
            by_scope[("var", target.name, str(original))] = hidden
            mapping[str(original)] = hidden
            entry[0] = hidden
        for lid, entry in target.lists.items():
            original = entry[0]
            dotted = target.is_stage and "." in str(original)
            hidden = namer(f"list:{lid}", dotted=dotted)
            list_names[lid] = hidden
            by_scope[("list", target.name, str(original))] = hidden
            mapping[str(original)] = hidden
            entry[0] = hidden

    # Broadcasts are names in the palette, in the hat's dropdown and in every
    # ``broadcast`` input, so all three follow the same map.
    broadcast_names: dict[str, str] = {}
    for bid, name in list(project.stage.broadcasts.items()):
        hidden = namer(f"broadcast:{bid}", dotted=False)
        broadcast_names[str(name)] = hidden
        mapping[str(name)] = hidden
        project.stage.broadcasts[bid] = hidden

    # Extensions can reference state *by name* in a dropdown or in a plain text
    # input (the JSON list picker stores a name, thread variables are strings).
    by_name: dict[str, str] = {}
    for (kind, _sprite, original), hidden in by_scope.items():
        by_name.setdefault(original, hidden)
    thread_names = _ThreadNamer(namer)

    for target in project.targets:
        for block in target.blocks.values():
            _rename_block(block, var_names, list_names, broadcast_names)
            _rename_state_picker(block, by_name)
            _rename_thread_variables(block, thread_names)
    _rename_proccodes(project, namer)
    for monitor in project.monitors:
        _rename_monitor(monitor, by_scope, target_names)

    # Comments are keyed by target name; keep that association.
    project.comments = {
        target_names.get(name, name): value for name, value in project.comments.items()
    }
    for target in project.targets:
        target.name = target_names[target.name]

    return mapping


def _rename_block(block, var_names, list_names, broadcast_names) -> None:
    fields = block.fields or {}
    if "VARIABLE" in fields and fields["VARIABLE"][1]:
        hidden = var_names.get(fields["VARIABLE"][1])
        if hidden is not None:
            fields["VARIABLE"][0] = hidden
    if "LIST" in fields and fields["LIST"][1]:
        hidden = list_names.get(fields["LIST"][1])
        if hidden is not None:
            fields["LIST"][0] = hidden
    if "BROADCAST_OPTION" in fields:
        hidden = broadcast_names.get(fields["BROADCAST_OPTION"][0])
        if hidden is not None:
            fields["BROADCAST_OPTION"][0] = hidden
    for raw in (block.inputs or {}).values():
        if not isinstance(raw, list):
            continue
        for payload in raw:
            if not isinstance(payload, list) or len(payload) < 3:
                continue
            kind, name, ident = payload[0], payload[1], payload[2]
            if kind == O.VAR:
                hidden = var_names.get(ident)
            elif kind == O.LIST:
                hidden = list_names.get(ident)
            elif kind == O.BROADCAST:
                hidden = broadcast_names.get(name)
            else:
                continue
            if hidden is not None:
                payload[1] = hidden


def _rename_proccodes(project: Project, namer: "_Namer") -> None:
    """``Sprite.move %s`` -> ``Qk3p %s``.

    Every custom block label is a name the editor shows in the palette and in
    "My Blocks"; leaving it readable spells out the call graph of the published
    project. The ``%s``/``%b``/``%n`` placeholders are kept verbatim and in
    order, because Scratch pairs them with ``argumentids`` — only the words
    around them change.
    """
    hidden: dict[str, str] = {}
    for target in project.targets:
        for block in target.blocks.values():
            mutation = getattr(block, "mutation", None)
            if not isinstance(mutation, dict):
                continue
            proccode = mutation.get("proccode")
            if not proccode:
                continue
            if proccode not in hidden:
                body = " ".join(_PLACEHOLDER.findall(proccode))
                base = namer(f"proccode:{proccode}", dotted=False)
                hidden[proccode] = f"{base} {body}".strip()
            mutation["proccode"] = hidden[proccode]
            names = mutation.get("argumentnames")
            if isinstance(names, str) and names:
                try:
                    original = json.loads(names)
                except ValueError:
                    continue
                if isinstance(original, list) and all(isinstance(n, str) for n in original):
                    mutation["argumentnames"] = json.dumps(
                        [
                            namer(f"argument:{hidden[proccode]}:{index}", dotted=False)
                            for index, _name in enumerate(original)
                        ]
                    )


class _ThreadNamer:
    """``lmsTempVars2`` keeps thread-variable names as text, not as state ids."""

    def __init__(self, namer: _Namer):
        self.namer = namer
        self.names: dict[str, str] = {}

    def __call__(self, original: str) -> str:
        if original not in self.names:
            hidden = self.namer(f"thread:{original}", dotted=False)
            # The extension's loop blocks expect the underscore convention.
            self.names[original] = "_" + hidden if original.startswith("_") else hidden
        return self.names[original]


def _rename_thread_variables(block, namer: "_ThreadNamer") -> None:
    if not block.opcode.startswith("lmsTempVars2_"):
        return
    for slot in ("VAR", "STRING", "VALUE"):
        raw = (block.inputs or {}).get(slot)
        if (
            isinstance(raw, list)
            and len(raw) > 1
            and isinstance(raw[1], list)
            and raw[1][:1] == [O.TEXT]
            and len(raw[1]) > 1
            and isinstance(raw[1][1], str)
            and raw[1][1]
            and slot == "VAR"
        ):
            raw[1][1] = namer(raw[1][1])


def _rename_state_picker(block, by_name: dict[str, str]) -> None:
    """A dropdown that names a variable/list (``…_menu_get_list``)."""
    if not block.opcode.endswith(("_get_list", "_get_var", "_menu_list", "_menu_var")):
        return
    for field, entry in (block.fields or {}).items():
        if entry and isinstance(entry[0], str) and entry[0] in by_name:
            entry[0] = by_name[entry[0]]


def _rename_monitor(monitor: dict, by_scope, target_names) -> None:
    params = monitor.get("params") or {}
    sprite = monitor.get("spriteName") or "Stage"
    if "VARIABLE" in params:
        kind, name = "var", params["VARIABLE"]
    elif "LIST" in params:
        kind, name = "list", params["LIST"]
    else:
        kind = name = None
    if name is not None:
        hidden = by_scope.get((kind, sprite, name))
        if hidden is None:
            # The monitor outlived its sprite: take the name from any scope.
            hidden = next(
                (v for (k, _s, n), v in by_scope.items() if k == kind and n == name),
                None,
            )
        if hidden is not None:
            params["VARIABLE" if kind == "var" else "LIST"] = hidden
    if monitor.get("spriteName"):
        monitor["spriteName"] = target_names.get(
            monitor["spriteName"], monitor["spriteName"]
        )
