"""Decompile an ``.sb3`` back into JadenScript.

The goal is *round-trip fidelity*: compiling the emitted ``.jsw`` must produce
the same scripts and initial state as the input project. Where a block has no
exact source construct, the decompiler emits a diagnostic and a comment rather
than inventing code that would change behaviour.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from . import opcodes as O
from .extension_blocks import MICROBIT_CONSTANT_NAMES
from .extensions import reverse as reverse_extension
from .keywords import ALL_KEYWORDS
from .sb3 import DEFAULT_SVG
from .sb3_read import LoadedProject

_IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_DEFAULT_SVG_MD5 = hashlib.md5(DEFAULT_SVG).hexdigest()

# operator opcode -> (symbol, precedence, left input, right input)
_BINARY_OPS = {
    "operator_add": ("+", 6, "NUM1", "NUM2"),
    "operator_subtract": ("-", 6, "NUM1", "NUM2"),
    "operator_multiply": ("*", 7, "NUM1", "NUM2"),
    "operator_divide": ("/", 7, "NUM1", "NUM2"),
    "operator_mod": ("%", 7, "NUM1", "NUM2"),
    "operator_lt": ("<", 5, "OPERAND1", "OPERAND2"),
    "operator_gt": (">", 5, "OPERAND1", "OPERAND2"),
    "operator_equals": ("==", 4, "OPERAND1", "OPERAND2"),
    "operator_and": ("&&", 3, "OPERAND1", "OPERAND2"),
    "operator_or": ("||", 2, "OPERAND1", "OPERAND2"),
}
# ``**`` is the only right-associative operator in the language.
_RIGHT_ASSOC = {"**"}
_NEGATED = {"operator_equals": ("!=", 4), "operator_lt": (">=", 5), "operator_gt": ("<=", 5)}

# inverse of the command registry: opcode -> (qualified name, spec)
_COMMAND_BY_OPCODE = {}
for _name, _spec in O.COMMANDS.items():
    _COMMAND_BY_OPCODE.setdefault(_spec.opcode, (_name, _spec))
_REPORTER_BY_OPCODE = {}
for _name, _spec in O.REPORTERS.items():
    _REPORTER_BY_OPCODE.setdefault(_spec.opcode, (_name, _spec))
_PROPERTY_GET = {p.get: name for name, p in O.PROPERTIES.items()}
_PROPERTY_SET = {p.set: (name, "=", p.set_input) for name, p in O.PROPERTIES.items()}
_PROPERTY_CHANGE = {p.change: (name, "+=", p.change_slot) for name, p in O.PROPERTIES.items()}


@dataclass
class ClassContext:
    sprite: dict
    variables: dict[str, str] = field(default_factory=dict)  # id -> name
    lists: dict[str, str] = field(default_factory=dict)


class Decompiler:
    def __init__(
        self,
        loaded: LoadedProject,
        asset_prefix: str = "assets/",
        max_inline: int = 50,
        include_assets: bool = True,
        fstring_threshold: int = 5,
    ):
        self.loaded = loaded
        self.asset_prefix = asset_prefix
        self.include_assets = include_assets
        self.fstring_threshold = fstring_threshold
        self.max_inline = max_inline
        self.extra_files: dict[str, bytes] = {}
        self.targets = loaded.targets
        self.stage = loaded.stage
        self.sprites = loaded.sprites
        self.diagnostics: list[str] = []
        self.level = 0
        self.uses_thread_vars = False
        self.procedures: dict[str, list[str]] = {}
        self.proc_names: dict[tuple[str, str], str] = {}
        self.proc_used: dict[str, set[str]] = {}
        self.target_procedures: dict[str, set[str]] = {}
        self.broadcast_links: dict[tuple[str, str], str] = {}
        self.broadcast_positions: dict[tuple[str, str], tuple] = {}
        self.class_context: dict[str, ClassContext] = {}
        self.param_renames: list[dict[str, str]] = []
        self.current_params: set[str] | None = None
        self.var_renames: dict[str, dict[str, str]] = {}
        self.thread_renames: dict[str, dict[str, str]] = {}
        self.list_renames: dict[str, dict[str, str]] = {}
        self.current_target = ""
        self._loose = 0
        self.temp_names: dict[str, list[str]] = {}
        self.prefix: dict[str, str] = {}      # sprite name -> namespace as written
        self.claimed: dict[str, list] = {}    # sprite name -> [(kind, tail, payload)]
        self.missing_calls: dict[str, dict[str, int]] = {}
        self.linked_broadcasts: set[str] = set()
        #: Broadcast message -> the name a ``hook: broadcast`` method gets.
        self.receiver_calls: dict[str, str] = {}
        #: Qualified names of the real custom blocks: a call to one of these
        #: stays a ``procedures_call``, so the receiver spelling would be wrong.
        self.procedure_names: set[str] = set()
        #: Sprite name -> the custom block names it declares itself.
        self.local_procedures: dict[str, set[str]] = {}
        self.state_ids: dict[str, str] = {}   # variable/list/broadcast id -> source name
        self.state_unique: dict[str, dict[str, str]] = {}  # target -> id -> unique name
        self._prepass()

    # -- indexing ----------------------------------------------------------
    def _prepass(self) -> None:
        # ``_x`` means a thread variable, so an invalid name (``@proc.var``)
        # must not be sanitised into a leading underscore unless the project
        # really uses the temp extension.
        self.uses_thread_vars = any(
            block["opcode"].startswith("lmsThreadVars2_")
            or block["opcode"].startswith("lmsTempVars2_")
            for target in self.targets
            for block in _blocks(target).values()
        )
        stage = next((t for t in self.targets if t.get("isStage")), None)
        global_vars = {v[0] for v in ((stage or {}).get("variables") or {}).values()}
        global_lists = {v[0] for v in ((stage or {}).get("lists") or {}).values()}
        for target in self.targets:
            ctx = ClassContext(sprite=target)
            for vid, entry in target.get("variables", {}).items():
                ctx.variables[vid] = entry[0]
            for lid, entry in target.get("lists", {}).items():
                ctx.lists[lid] = entry[0]
            self.class_context[target["name"]] = ctx
            taken = set(ctx.variables.values()) | set(ctx.lists.values())
            local = target["name"] != "Stage"
            self.var_renames[target["name"]] = {
                name: _sanitised_name(name, taken, local)
                for name in ctx.variables.values()
                if _needs_rename(name, local)
            }
            # A bare list name reads the list's contents, so a list that is read
            # that way and shares its name with a variable visible here would
            # resolve to the variable (``f * x`` read a list called ``f``). Only
            # such lists need a new name; ``f.get(i)`` stays unambiguous.
            visible_vars = set(ctx.variables.values()) | global_vars
            bare_lists = self._bare_list_names(target, ctx.lists)
            list_taken = taken | global_vars | global_lists
            self.list_renames[target["name"]] = {
                name: _sanitised_name(name, list_taken, local)
                for name in ctx.lists.values()
                if _needs_rename(name, local) or (name in visible_vars and name in bare_lists)
            }
            for original, renamed in self.var_renames[target["name"]].items():
                self.note("I0001", f"variable {original!r} renamed to {renamed!r} (not a valid name)")
            for original, renamed in self.list_renames[target["name"]].items():
                if _needs_rename(original, local):
                    self.note("I0001", f"list {original!r} renamed to {renamed!r} (not a valid name)")
                else:
                    self.note("I0001", 
                        f"list {original!r} renamed to {renamed!r} (a variable has that name)"
                    )

            blocks = _blocks(target)
            thread_map: dict[str, str] = {}
            seen_threads: set[str] = set()
            taken = set(ctx.variables.values()) | set(ctx.lists.values())
            for block in blocks.values():
                if not block["opcode"].startswith("lmsTempVars2_"):
                    continue
                self.uses_thread_vars = True
                raw = (block.get("inputs") or {}).get("VAR")
                if not (raw and raw[0] == 1 and isinstance(raw[1], list) and len(raw[1]) > 1):
                    continue
                thread_name = str(raw[1][1])
                if thread_name in seen_threads:
                    continue
                seen_threads.add(thread_name)
                candidate = thread_name
                if not _valid_dotted(candidate) or "." in candidate:
                    # A dotted thread variable would parse as a global.
                    candidate = _flat_identifier(candidate)
                if not candidate.startswith("_"):
                    # ``useTempVariables`` only allocates names that start with
                    # an underscore; renaming is invisible to the VM.
                    candidate = "_" + candidate
                while candidate in taken:
                    candidate += "_"
                taken.add(candidate)
                if candidate != thread_name:
                    thread_map[thread_name] = candidate
                    self.note("I0001", 
                        f"temp variable {thread_name!r} renamed to {candidate!r} "
                        "(useTempVariables naming rule)"
                    )
            self.thread_renames[target["name"]] = thread_map
            self.temp_names[target["name"]] = sorted(
                {thread_map.get(name, name) for name in seen_threads}
            )

            for block in blocks.values():
                if block["opcode"] != "procedures_definition":
                    continue
                proto = _first_block(blocks, block, "custom_block")
                if proto is None:
                    continue
                mutation = proto.get("mutation") or {}
                proccode = mutation.get("proccode", "")
                try:
                    names = json_loads(mutation.get("argumentnames", "[]"))
                except ValueError:
                    names = []
                self.procedures[proccode] = names
                self.target_procedures.setdefault(target["name"], set()).add(proccode)

        # calls whose callee is not defined in the project (custom blocks are
        # per-sprite, so a dangling proccode cannot resolve anywhere)
        for target in self.targets:
            missing = self.missing_calls.setdefault(target["name"], {})
            for block in _blocks(target).values():
                if block["opcode"] != "procedures_call":
                    continue
                proccode = (block.get("mutation") or {}).get("proccode", "")
                if not proccode or proccode in self.target_procedures.get(target["name"], set()):
                    continue
                if _console_name(proccode) is not None:
                    continue  # a console call, not a missing custom block
                try:
                    arity = len(json_loads((block.get("mutation") or {}).get("argumentids", "[]")))
                except ValueError:
                    arity = 0
                missing[proccode.split(" %")[0]] = arity

        self._compute_prefixes()

        # Ids leak into the project as text: extension menus (the JSON
        # extension's list picker, for example) store the *id* of a list and
        # resolve it by name when the id is unknown.  Recording the mapping
        # lets ``_menu_value`` write the readable name instead, which survives
        # a rebuild that allocates fresh ids.
        for target in self.targets:
            name = target["name"]
            renames = self.var_renames.get(name, {})
            list_renames = self.list_renames.get(name, {})
            unique = self._unique_state_names(target)
            for vid, entry in target.get("variables", {}).items():
                self.state_ids[vid] = unique.get(vid, renames.get(entry[0], entry[0]))
            for lid, entry in target.get("lists", {}).items():
                self.state_ids[lid] = unique.get(lid, list_renames.get(entry[0], entry[0]))
            for bid, entry in target.get("broadcasts", {}).items():
                self.state_ids.setdefault(bid, entry)

        # a receiver whose only statement is a call to a procedure names that procedure
        for target in self.targets:
            blocks = _blocks(target)
            # Calls without a definition are inert in the project; naming the
            # procedure after the broadcast would turn them into live
            # broadcast-and-wait calls instead.
            defined = {
                (proto.get("mutation") or {}).get("proccode", "")
                for proto in blocks.values()
                if proto["opcode"] == "procedures_prototype"
            }
            for block in blocks.values():
                if block["opcode"] != "event_whenbroadcastreceived":
                    continue
                raw_broadcast = str(
                    (block.get("fields", {}).get("BROADCAST_OPTION") or ["", None])[0]
                )
                broadcast = self._strip_prefix(raw_broadcast, target["name"])
                # Two spellings: the owner writes ``init();`` inside its own
                # class, another sprite has to say ``Lyrics.init();``.
                for message in (broadcast, raw_broadcast):
                    if message:
                        self.receiver_calls.setdefault(message, _identifier(message))
                body = _next_block(blocks, block)
                if body is None or body["opcode"] != "procedures_call":
                    continue
                if body.get("next") is not None:
                    continue
                proccode = (body.get("mutation") or {}).get("proccode", "")
                if proccode and proccode in defined:
                    self.broadcast_links[(target["name"], proccode)] = broadcast
                    self.broadcast_positions[(target["name"], proccode)] = (
                        block.get("x", 0),
                        block.get("y", 0),
                    )
                    self.linked_broadcasts.add(broadcast)

        # Name every custom block up front, in project order, so the dedupe in
        # ``_proc_display_name`` does not depend on which call site is visited
        # first. This runs last: the names depend on the broadcast links above.
        for target in self.targets:
            for block in _blocks(target).values():
                if block.get("opcode") != "procedures_prototype":
                    continue
                proccode = (block.get("mutation") or {}).get("proccode", "")
                if proccode:
                    name = self._proc_display_name(target, proccode)
                    self.local_procedures.setdefault(target["name"], set()).add(name)
        self.procedure_names = set(self.proc_names.values())

    # -- helpers -----------------------------------------------------------
    def _loose_counter(self) -> int:
        self._loose += 1
        return self._loose

    def _compute_prefixes(self) -> None:
        """Find ``Graph.xxx`` names that belong to the sprite called ``graph``."""
        stage_vars = [(v[0], "var", v[1]) for v in self.stage.get("variables", {}).values()]
        stage_lists = [(l[0], "list", l[1]) for l in self.stage.get("lists", {}).values()]
        for sprite in self.sprites:
            name = sprite["name"]
            found = None
            for full, kind, payload in stage_vars + stage_lists:
                head, _, tail = full.partition(".")
                if not tail or head.lower() != name.lower() or not _valid_dotted(tail):
                    continue
                if found is None:
                    found = head
                if found.lower() != head.lower():
                    found = None  # mixed prefixes: leave everything global
                    break
            proccodes = [
                (proto.get("mutation") or {}).get("proccode", "")
                for proto in sprite["blocks"].values()
                if isinstance(proto, dict) and proto.get("opcode") == "procedures_prototype"
            ]
            if found is None:
                # A sprite can be namespaced by its custom blocks alone: the
                # graph engine has no ``Graph.*`` state, only ``Graph.doGraph``
                # proccodes. Every proccode must share one prefix, and the tail
                # must be a plain name, or nothing is rewritten.
                heads = {
                    proccode.partition(".")[0]
                    for proccode in proccodes
                    if proccode
                }
                if len(heads) == 1:
                    head = heads.pop()
                    tails = [
                        proccode.partition(".")[2].split(" %")[0]
                        for proccode in proccodes
                        if proccode
                    ]
                    if (
                        "." in proccodes[0] if proccodes else False
                    ) and _valid_dotted(head) and all(_valid_dotted(t) for t in tails):
                        found = head
            if found is None:
                continue
            # Only adopt the prefix style when the sprite's custom blocks use it
            # too; otherwise a rebuild would rename them.
            if any(
                proccode
                and not proccode.lower().startswith(found.lower() + ".")
                for proccode in proccodes
            ):
                continue
            self.prefix[name] = found
            entries = []
            for full, kind, payload in stage_vars + stage_lists:
                head, _, tail = full.partition(".")
                # Compare against the *prefix* this sprite adopted, not its
                # name: ``NpGraph`` is namespaced ``Graph``, and matching the
                # name here silently dropped every ``Graph.v1`` (the Stage
                # skipped it as claimed, the class never emitted it).
                if tail and head.lower() == found.lower():
                    entries.append((kind, tail, payload))
            self.claimed[name] = entries
            for kind, tail, _ in entries:
                original = f"{found}.{tail}"
                if kind == "var":
                    self.var_renames.setdefault(name, {})[original] = tail
                else:
                    self.list_renames.setdefault(name, {})[original] = tail

    # -- monitors ----------------------------------------------------------
    def _note_lines(self) -> list[str]:
        """Workspace comments as ``// comment`` lines (they are visible notes)."""
        lines: list[str] = []
        for target in self.targets:
            owner = "Stage" if target.get("isStage") else _flat_identifier(target["name"])
            for comment in target.get("comments", {}).values():
                text = str(comment.get("text") or "")
                if not text.strip():
                    continue
                minimized = " minimized" if comment.get("minimized") else ""
                lines.append(
                    f"metadata note {owner} at {_num_text(comment.get('x', 0))},"
                    f"{_num_text(comment.get('y', 0))} "
                    f"{_num_text(comment.get('width', 200))}x{_num_text(comment.get('height', 100))}"
                    f"{minimized}: {_quote(text)};"
                )
        return lines

    def _monitor_lines(self) -> list[str]:
        """Stage monitors (slider ranges, list size, show mode) as comments."""
        entries = (self.loaded.data or {}).get("monitors") or []
        lines: list[str] = []
        for monitor in entries:
            opcode = monitor.get("opcode")
            params = monitor.get("params") or {}
            if opcode == "data_variable":
                name, kind = params.get("VARIABLE"), "var"
            elif opcode == "data_listcontents":
                name, kind = params.get("LIST"), "list"
            else:
                watch = self._watch_line(monitor, opcode)
                if watch:
                    lines.append(watch)
                continue
            if not name:
                continue
            sprite = monitor.get("spriteName") or "Stage"
            owner = "Stage" if sprite == "Stage" else _monitor_owner(sprite)
            tables = self.list_renames if kind == "list" else self.var_renames
            renames = tables.get(sprite)
            if renames is None:
                # The monitor outlived its sprite; use the map of whoever still
                # owns the state so the name matches the declaration.
                renames = next(
                    (table for table in tables.values() if name in table), {}
                )
            label = renames.get(name, name)
            if not self._valid_name(label):
                continue
            mode = monitor.get("mode", "default")
            parts = [
                f"metadata {mode} {owner}.{label}",
                f"at {_num_text(monitor.get('x', 0))},{_num_text(monitor.get('y', 0))}",
                f"{_num_text(monitor.get('width', 100))}x{_num_text(monitor.get('height', 24))}",
                "shown" if monitor.get("visible", True) else "hidden",
            ]
            slider_min = monitor.get("sliderMin", 0)
            slider_max = monitor.get("sliderMax", 100)
            discrete = monitor.get("isDiscrete", True)
            if mode != "list" and (
                mode == "slider" or (slider_min, slider_max) != (0, 100) or not discrete
            ):
                parts.append(f"range {_num_text(slider_min)}..{_num_text(slider_max)}")
                if discrete:
                    parts.append("discrete")
            lines.append(" ".join(parts) + ";")
        return lines

    def _watch_line(self, monitor: dict, opcode: str) -> str | None:
        """A monitor that shows a plain reporter (``sensing_timer`` and friends)."""
        if monitor.get("params"):
            self.note("W0008", 
                f"monitor for {opcode!r} has parameters; kept as a comment"
            )
        sprite = monitor.get("spriteName") or "Stage"
        owner = "Stage" if sprite == "Stage" else _monitor_owner(sprite)
        parts = [
            f"metadata watch {owner}.{opcode}",
            # Monitor ids are arbitrary text (``)_9Lty?!ER]Z^…``), so they
            # need quoting now that the line is code rather than a comment.
            f"id={_quote(str(monitor.get('id', '')))}",
            f"mode={monitor.get('mode', 'default')}",
            f"at {_num_text(monitor.get('x', 0))},{_num_text(monitor.get('y', 0))}",
            f"{_num_text(monitor.get('width', 0))}x{_num_text(monitor.get('height', 0))}",
            "shown" if monitor.get("visible", True) else "hidden",
        ]
        return " ".join(parts) + ";"

    def note(self, code: str, message: str) -> None:
        entry = f"{code} {message}"
        if entry not in self.diagnostics:
            self.diagnostics.append(entry)

    def indent(self) -> str:
        return "    " * self.level

    # -- entry -------------------------------------------------------------
    def decompile(self) -> str:
        header: list[str] = []
        if self.uses_thread_vars:
            header.append("#enable_feature<useTempVariables>;")
        if self.prefix:
            header.append("#enable_feature<namespacedNames>;")
        # Target properties, script positions and monitors are real syntax now
        # (``main: …;``, ``metadata …;``), so no ``commentMetadata`` feature is
        # needed. The comments are still accepted when a source has them.
        if header:
            header.append("")

        state = self._state_declarations(self.stage, public=True)
        monitors = self._note_lines() + self._monitor_lines()
        stage_source = self._emit_stage()

        class_blocks: list[str] = []
        for sprite in self.sprites:
            class_blocks.extend(self._emit_class(sprite))
            class_blocks.append("")

        lines = header + state
        if monitors:
            if state:
                lines.append("")
            lines.extend(monitors)
        if state or monitors:
            lines.append("")
        if stage_source:
            lines.extend(stage_source)
            lines.append("")
        lines.extend(class_blocks)

        compacted: list[str] = []
        for line in lines:
            if not line and compacted and not compacted[-1]:
                continue
            compacted.append(line)
        return "\n".join(compacted).rstrip() + "\n"

    def _emit_stage(self) -> list[str]:
        """Stage scripts have no sprite, so they live in a ``stage { }`` block."""
        blocks = _blocks(self.stage)
        body: list[str] = []
        for line in self._target_assets(self.stage):
            body.append(line)
        if body:
            body.append("")
        self.current_target = "Stage"
        self.level = 1
        tops = sorted(
            (b for b in blocks.values() if b.get("topLevel")),
            key=lambda b: (b.get("y", 0), b.get("x", 0)),
        )
        hats = [b for b in tops if self._is_hat(b)]
        loose = [b for b in tops if not self._is_hat(b)]
        for script in hats:
            body.extend(self._emit_placed(self.stage, blocks, script))
            body.append("")
        if loose:
            body.extend(self._emit_loose(blocks, loose))
            body.append("")
        for name, arity in self.missing_calls.get("Stage", {}).items():
            params = ", ".join(f"arg{i + 1}" for i in range(arity))
            body.append(f"    // callee {name!r} is missing from the project")
            body.append(f"    private void {_safe_name(name)}({params}) {{ // undefined")
            body.append("    }")
            body.append("")
        while body and not body[-1].strip():
            body.pop()
        if not body:
            return self._stage_meta()
        return self._stage_meta() + ["stage {"] + body + ["}"]

    def _stage_meta(self) -> list[str]:
        """``stage: …;`` — the Stage's own properties, as real syntax."""
        fields = [
            f"costume={int(self.stage.get('currentCostume', 0))}",
            f"volume={_num_text(self.stage.get('volume', 100))}",
            f"layer={int(self.stage.get('layerOrder', 0))}",
            f"tempo={_num_text(self.stage.get('tempo', 60))}",
            f"video={_quote(str(self.stage.get('videoState', 'on')))}",
            f"videoTransparency={_num_text(self.stage.get('videoTransparency', 50))}",
        ]
        return [f"stage: {', '.join(fields)};"]

    def _target_assets(self, target: dict) -> list[str]:
        """Costume and sound imports for one target."""
        lines: list[str] = []
        if not self.include_assets or not getattr(self.loaded, "assets", None):
            return lines
        for costume in target.get("costumes", []):
            asset = costume.get("md5ext", "")
            if not asset or costume.get("assetId") == _DEFAULT_SVG_MD5:
                continue
            meta = (
                f" // costume centerx={_num_text(costume.get('rotationCenterX', 0))}"
                f" centery={_num_text(costume.get('rotationCenterY', 0))}"
                f" res={_num_text(costume.get('bitmapResolution', 1))}"
            )
            lines.append(
                self._asset_import(asset, costume.get("name", "costume"), meta)
            )
        for sound in target.get("sounds", []):
            asset = sound.get("md5ext", "")
            if not asset:
                continue
            meta = (
                f" // sound rate={_num_text(sound.get('rate', 48000))}"
                f" samples={_num_text(sound.get('sampleCount', 0))}"
            )
            lines.append(self._asset_import(asset, sound.get("name", "sound"), meta))
        return lines

    def _asset_import(self, asset: str, name: str, meta: str = "") -> str:
        # Scratch matches costumes and sounds by their *name* (``switch costume
        # to [false]``), so an identifier-only label would silently rename
        # them.  Quoted labels keep the original text.
        label = _flat_identifier(name)
        text = label if label == name else _quote(name)
        return f'    import "{self.asset_prefix}{asset}" as {text};{meta}'

    def _emit_placed(self, sprite: dict, blocks: dict, script: dict) -> list[str]:
        """Emit one top-level script, remembering where it sat on the canvas."""
        lines = self._emit_script(sprite, blocks, script)
        if lines:
            placed = self._pos_line(lines[0], script)
            if placed:
                lines.insert(0, self.indent() + placed)
        return lines

    def _at(self, script: dict) -> str:
        """Inline position, for stacks that have no name (``loose { // at …``)."""
        return f" // at {_num_text(script.get('x', 0))},{_num_text(script.get('y', 0))}"

    def _pos_line(self, header: str, script: dict) -> str | None:
        """``metadata <name> pos x,y;`` for the script about to be emitted.

        The name is read back out of the header line, so it always matches what
        the compiler will look up. Loose stacks have no name, so they keep the
        inline ``// at`` comment.
        """
        position = (
            f"{_num_text(script.get('x', 0))},{_num_text(script.get('y', 0))}"
        )
        match = _HEADER_NAME_RE.search(header)
        if match is None:
            return f"// at {position}"
        return f"metadata {match.group(1)} pos {position};"

    @staticmethod
    def _bare_list_names(target: dict, names: dict[str, str]) -> set[str]:
        """List names this target reads as a value (contents reporter)."""
        bare: set[str] = set()
        for block in _blocks(target).values():
            if block.get("opcode") == "data_listcontents":
                lid = ((block.get("fields") or {}).get("LIST") or [None, None])[1]
                if lid in names:
                    bare.add(names[lid])
            for value in (block.get("inputs") or {}).values():
                if not isinstance(value, list):
                    continue
                for item in value[1:]:
                    if isinstance(item, list) and item and item[0] == 13 and len(item) > 2:
                        if item[2] in names:
                            bare.add(names[item[2]])
        return bare

    def _unique_state_names(self, target: dict) -> dict[str, str]:
        """Scratch lets two variables share a name; the source cannot.

        Blocks reference state by id, so the second ``t`` becomes ``t_2`` and
        keeps its own value instead of silently merging with the first.
        """
        unique: dict[str, str] = {}
        self.state_unique[target["name"]] = unique
        tables = (
            (target.get("variables", {}), self.var_renames.get(target["name"], {})),
            (target.get("lists", {}), self.list_renames.get(target["name"], {})),
        )
        for entries, renames in tables:
            used: set[str] = set()
            for sid, entry in entries.items():
                base = renames.get(entry[0], entry[0])
                name = base
                index = 1
                while name in used:
                    index += 1
                    name = f"{base}_{index}"
                used.add(name)
                if name != base:
                    self.note("I0001", 
                        f"{entry[0]!r} is used twice; renamed to {name!r} (duplicate name)"
                    )
                    unique[sid] = name
        return unique

    # -- initial state -----------------------------------------------------
    def _state_declarations(self, target: dict, public: bool) -> list[str]:
        lines: list[str] = []
        keyword = "public" if public else "private"
        renames = self.var_renames.get(target["name"], {})
        list_renames = self.list_renames.get(target["name"], {})
        skip_vars = {f"{self.prefix[t]}." for t in self.claimed} if public else set()
        skip_lists = skip_vars
        unique = self.state_unique.get(target["name"], {})
        for sid, entry in target.get("variables", {}).items():
            name, value = entry[0], entry[1]
            if any(name.startswith(prefix) for prefix in skip_vars):
                continue  # emitted inside its own class
            name = unique.get(sid) or renames.get(name, name)
            if not self._valid_name(name):
                self.note("W0008", f"variable {name!r} is not a valid JadenScript name; skipped")
                continue
            text = "" if value is None else str(value)
            if len(text) > self.max_inline * 20:
                filename = f"vars/{name}.jsv"
                self.extra_files[filename] = text.encode("utf-8")
                line = f'import "{self.asset_prefix}{filename}"' + (
                    "" if public else f" as {_import_alias(name)}"
                )
                line += ";"
                lines.append(line)
                continue
            lines.append(f"{keyword} {name} = {_literal_text(value)};")
        for sid, entry in target.get("lists", {}).items():
            name, items = entry[0], entry[1]
            if any(name.startswith(prefix) for prefix in skip_lists):
                continue
            name = unique.get(sid) or list_renames.get(name, name)
            if not self._valid_name(name):
                self.note("W0008", f"list {name!r} is not a valid JadenScript name; skipped")
                continue
            rendered = ", ".join(_literal_text(item) for item in items)
            if len(items) > self.max_inline or len(rendered) > 4000:
                # Large state lives in a side file: a multi-megabyte source is
                # not editable, and ``.jsa`` is exactly the import format.
                filename = f"lists/{name}.jsa"
                self.extra_files[filename] = json.dumps(items).encode("utf-8")
                line = f'import "{self.asset_prefix}{filename}"' + (
                    "" if public else f" as {_import_alias(name)}"
                )
                line += ";"
                lines.append(line)
                continue
            lines.append(f"{keyword} {name} = [{rendered}];")
        return lines

    @staticmethod
    def _valid_name(name: str) -> bool:
        return all(_IDENT_RE.match(part) for part in name.split("."))

    # -- classes -----------------------------------------------------------
    def _emit_class(self, sprite: dict) -> list[str]:
        self.current_target = sprite["name"]
        blocks = _blocks(sprite)
        name = _flat_identifier(sprite["name"])
        prefix = self.prefix.get(sprite["name"])
        rotation = _rotation_word(sprite.get("rotationStyle", "all around"))
        # The target's own properties are real declarations now; nothing about
        # them depends on a comment being understood.
        fields = [
            f"name={_quote(sprite['name'])}",
            f"x={_num_text(sprite.get('x', 0))}",
            f"y={_num_text(sprite.get('y', 0))}",
            f"size={_num_text(sprite.get('size', 100))}",
            f"direction={_num_text(sprite.get('direction', 90))}",
            f"show={1 if sprite.get('visible', True) else 0}",
            f"costume={int(sprite.get('currentCostume', 0))}",
            f"volume={_num_text(sprite.get('volume', 100))}",
            f"layer={int(sprite.get('layerOrder', 0))}",
            f'rotation={_quote(rotation)}',
            f"draggable={1 if sprite.get('draggable') else 0}",
        ]
        if prefix:
            # Always spelled out, even when the prefix equals the target name:
            # the ``namespacedNames`` feature only re-adds the prefix for
            # classes that declare ``namespace=`` explicitly, so omitting it
            # here silently un-namespaces every ``Lyrics.zh`` in the project.
            fields.append(f"namespace={_quote(prefix)}")
        # The declaration hugs its class; no blank line in between.
        lines = [f"{name}: {', '.join(fields)};", f"class {name} {{"]

        lines.extend(self._target_assets(sprite))

        prefix = self.prefix.get(sprite["name"])
        for kind, tail, payload in self.claimed.get(sprite["name"], []):
            if kind == "var":
                lines.append(f"    public {tail} = {_literal_text(payload)};")
            else:
                # Inline, whatever the size: a "kept empty" list is silent data
                # loss, and ``Graph.v1`` is read on the first frame.
                rendered = ", ".join(_literal_text(item) for item in payload)
                if len(payload) > self.max_inline:
                    self.note(
                        "I0003",
                        f"list {tail!r} has {len(payload)} items; written inline",
                    )
                lines.append(f"    public {tail} = [{rendered}];")
        if self.claimed.get(sprite["name"]):
            lines.append("")

        temp_vars = self.temp_names.get(sprite["name"], [])
        if temp_vars:
            lines.append("    // temp variables (lmsTempVars2 thread variables)")
            for name in temp_vars:
                lines.append(f"    private {name};")
            lines.append("")

        state = self._state_declarations(sprite, public=False)
        if state:
            # Imports of big state live in the class body: a top-level import
            # would create a *stage* variable instead of a sprite-local one.
            lines.extend(f"    {line}" for line in state)
        if state or sprite.get("costumes") or sprite.get("sounds"):
            lines.append("")

        loose: list[dict] = []
        for script in self._scripts(sprite):
            if self._is_hat(script):
                lines.extend(self._emit_placed(sprite, blocks, script))
                lines.append("")
            else:
                loose.append(script)
        if loose:
            lines.extend(self._emit_loose(blocks, loose))
            lines.append("")

        for name, arity in self.missing_calls.get(sprite["name"], {}).items():
            params = ", ".join(f"arg{i + 1}" for i in range(arity))
            lines.append(f"    // callee {name!r} is missing from the project")
            lines.append(f"    private void {_safe_name(name)}({params}) {{ // undefined")
            lines.append("    }")
            lines.append("")
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append("}")
        return lines

    @staticmethod
    def _scripts(sprite: dict) -> list[dict]:
        tops = [
            block
            for block in _blocks(sprite).values()
            if block.get("topLevel")
        ]
        tops.sort(key=lambda b: (b.get("y", 0), b.get("x", 0)))
        return tops

    @staticmethod
    def _is_hat(block: dict) -> bool:
        if block["opcode"] in _MICROBIT_HATS:
            return True
        return block["opcode"].startswith(
            ("event_when", "control_start_as_clone", "procedures_definition", "lmsMoreEvents_")
        )

    def _emit_loose(self, blocks: dict, loose: list[dict]) -> list[str]:
        """Blocks dragged onto the workspace: inert, but worth keeping."""
        pad = "    "
        lines: list[str] = []
        # Loose blocks belong to no script, so an argument reporter in one is
        # an orphan: evaluate it as empty instead of inventing a variable.
        previous_params = self.current_params
        self.current_params = set()
        try:
            lines = self._emit_loose_blocks(blocks, loose, pad)
        finally:
            self.current_params = previous_params
        return lines

    @staticmethod
    def _is_menu_shadow(block: dict) -> bool:
        if not block.get("shadow"):
            return False
        opcode = block["opcode"]
        return opcode in _MENU_OPS or _is_menu_opcode(opcode)

    def _emit_loose_blocks(self, blocks: dict, loose: list[dict], pad: str) -> list[str]:
        lines: list[str] = []
        for block in loose:
            if self._is_menu_shadow(block):
                lines.append(f"{pad}loose {{{self._at(block)}")
                lines.append(f"{pad}    {_menu_shadow_text(block)};")
                lines.append(f"{pad}}}")
                lines.append("")
                continue
            if self._is_reporter_block(block):
                text, _shape = self._expr(blocks, block)
                if text.lstrip().startswith("//") or not text.strip():
                    lines.append(f"{pad}// loose reporter: {block['opcode']}")
                    self.note("W0008", f"loose reporter {block['opcode']!r}; kept as a comment")
                    continue
                if _is_literal_text(text):
                    # ``join("", "")`` folds to a bare literal, which compiles to
                    # nothing: keep the block itself so the canvas is unchanged.
                    text = f'block("{block["opcode"]}")'
                    self.note(
                        "W0008",
                        f"loose {block['opcode']!r} folds to {text!r}; kept as block()",
                    )
                lines.append(f"{pad}loose {{{self._at(block)}")
                lines.append(f"{pad}    {text};")
                lines.append(f"{pad}}}")
                lines.append("")
                continue
            lines.append(f"{pad}loose {{{self._at(block)}")
            lines.append(f"{pad}    // {block['opcode']}")
            # A loose top-level block may head a whole stacked chain.
            self.level = 2
            for line in self._body(blocks, block):
                lines.append(f"{pad}{line}" if line.strip() else line)
            lines.append(f"{pad}}}")
            lines.append("")
        return lines

    def _emit_script(self, sprite: dict, blocks: dict, script: dict) -> list[str]:
        opcode = script["opcode"]
        self.level = 1
        if opcode == "procedures_definition":
            return self._emit_procedure(sprite, blocks, script)
        if opcode == "event_whenflagclicked":
            return self._hat("public void greenFlag() {", blocks, script)
        if opcode == "control_start_as_clone":
            return self._hat("public void whenCloned() {", blocks, script)
        if opcode == "event_whenthisspriteclicked":
            return self._hat("public void whenClicked() { // hook: click", blocks, script)
        if opcode in ("lmsMoreEvents_beforeSave", "lmsMoreEvents_afterSave"):
            moment = "before" if "before" in opcode else "after"
            return self._hat(
                f"public void onSave() {{ // hook: {moment} project save", blocks, script
            )
        if opcode == "event_whenstageclicked":
            return self._hat(
                "public void whenStageClicked() { // hook: stage click", blocks, script
            )
        if opcode == "event_whenbackdropswitchesto":
            backdrop = (script.get("fields", {}).get("BACKDROP") or ["", None])[0]
            return self._hat(
                f"public void whenBackdropChanges() {{ // hook: backdrop {backdrop}",
                blocks,
                script,
            )
        if opcode in _MICROBIT_HATS:
            method, hook = _MICROBIT_HATS[opcode]
            argument, menu = _MICROBIT_FIELDS[opcode]
            value = self._menu_field(blocks, script, argument, menu)
            return self._hat(
                f"public void microbit.{method}() {{ // hook: {hook.format(value=value)}",
                blocks,
                script,
            )
        if opcode == "event_whenkeypressed":
            key = (script.get("fields", {}).get("KEY_OPTION") or ["space", None])[0]
            # ``]`` cannot be written inside the brackets, so exotic keys are
            # quoted: ``hook: when ["]"] key pressed``.
            shown = _quote(key) if any(ch in key for ch in '[]"') else key
            header = f"public void Events.keyPressed(k) {{ // hook: when [{shown}] key pressed"
            return self._hat(header, blocks, script)
        if opcode == "event_whenbroadcastreceived":
            broadcast = (script.get("fields", {}).get("BROADCAST_OPTION") or ["", None])[0]
            body = _next_block(blocks, script)
            if body is not None and body["opcode"] == "procedures_call" and body.get("next") is None:
                proccode = (body.get("mutation") or {}).get("proccode", "")
                if self.broadcast_links.get((sprite["name"], proccode)) == broadcast:
                    return []  # already emitted as the public procedure
            header = f"public void {_identifier(broadcast)}() {{ // hook: broadcast {broadcast}"
            return self._hat(header, blocks, script)
        nested = self._substack(blocks, script, "SUBSTACK")
        if nested:
            # A C-block used as a wrapper (comment blocks, or blocks dragged
            # onto the workspace). It has no hat, so it never runs; the body is
            # preserved in a procedure so the code and its blocks survive.
            name = f"loose_{opcode}_{self._loose_counter()}"
            return (
                [f"{self.indent()}// loose block: {opcode}", f"{self.indent()}private void {name}() {{"]
                + nested
                + [f"{self.indent()}}}"]
            )
        return [f"{self.indent()}// loose block: {opcode}"]
        self.note("W0008", f"unsupported hat {opcode!r} in sprite {sprite['name']!r}; skipped")
        return [f"// unsupported hat: {opcode}"]

    def _hat(self, header: str, blocks: dict, script: dict) -> list[str]:
        lines = [self.indent() + header]
        body = _next_block(blocks, script)
        previous = self.current_params
        self.current_params = set()  # hats have no arguments
        try:
            lines.extend(self._body(blocks, body))
        finally:
            self.current_params = previous
        lines.append(self.indent() + "}")
        return lines

    def _proc_display_name(self, sprite: dict, proccode: str) -> str:
        """The single name this procedure gets in the source.

        Two custom blocks can flatten to the same name — ``rotate %s %s %s
        around x axis %s d`` and its ``… z axis …`` twin both become
        ``rotate`` — and the compiler resolves a call by name, so the second
        one would silently run the first one's body.
        """
        key = (sprite["name"], proccode)
        if key in self.proc_names:
            return self.proc_names[key]
        broadcast = self.broadcast_links.get(key)
        if broadcast is not None:
            name = _identifier(self._strip_prefix(broadcast, sprite["name"]))
        else:
            base = self._strip_prefix(proccode.split(" %")[0], sprite["name"])
            name = _identifier(_safe_name(base))
            if "." in name and not _valid_dotted(base):
                # ``0.Run Frame`` is a label, not a ``Namespace.Method`` call:
                # flatten it, otherwise the compiler reads ``0`` as a receiver.
                name = name.replace(".", "_")
        used = self.proc_used.setdefault(sprite["name"], set())
        unique, index = name, 1
        while unique in used:
            index += 1
            unique = f"{name}_{index}"
        if unique != name:
            self.note("I0001", f"procedure {proccode!r} renamed to {unique!r} (duplicate name)")
        used.add(unique)
        self.proc_names[key] = unique
        return unique

    def _emit_procedure(self, sprite: dict, blocks: dict, definition: dict) -> list[str]:
        proto = _first_block(blocks, definition, "custom_block")
        if proto is None:
            self.note("W0008", "procedure definition without prototype; skipped")
            return []
        mutation = proto.get("mutation") or {}
        proccode = mutation.get("proccode", "")
        try:
            argument_names = json_loads(mutation.get("argumentnames", "[]"))
        except ValueError:
            argument_names = []
        display = self._proc_display_name(sprite, proccode)
        if self.broadcast_links.get((sprite["name"], proccode)) is not None:
            header = f"public void {display}"
        else:
            header = f"private void {display}"
        body_first = _next_block(blocks, definition)
        renames = {}
        used = set(argument_names)
        # A parameter shadows sprite state, so it must not take over a name the
        # class already declares (``private cmd_string`` vs parameter
        # ``cmd string`` both flatten to ``cmd_string``).
        used |= set(self.var_renames.get(sprite["name"], {}).values())
        used |= set(self.list_renames.get(sprite["name"], {}).values())
        context = self.class_context.get(sprite["name"])
        if context is not None:
            used |= set(context.variables.values()) | set(context.lists.values())
        for name in argument_names:
            renamed = _safe_name(name)
            if renamed != name or _body_uses_variable(blocks, body_first, name):
                candidate = renamed if renamed != name else f"{name}_arg"
                if candidate in used:
                    candidate = f"{renamed}_arg"
                while candidate in used:
                    candidate += "_"
                used.add(candidate)
                renames[name] = candidate
                if renamed != name:
                    self.note("I0001", f"parameter {name!r} renamed to {candidate!r} (reserved word)")
                else:
                    self.note("I0001", f"parameter {name!r} renamed to {candidate!r} (name clash)")
        signature = ", ".join(renames.get(name, name) for name in argument_names)
        emitted = header.split("void ", 1)[-1]
        derived = " ".join([emitted] + ["%s"] * len(argument_names))
        label = f" // proccode {proccode}" if proccode != derived.strip() else ""
        warp = " // warp" if str(mutation.get("warp", "false")).lower() == "true" else ""
        receiver = ""
        position = self.broadcast_positions.get((sprite["name"], proccode))
        message = self.broadcast_links.get((sprite["name"], proccode))
        if message is not None and position:
            # The hat listens for this exact text, which is not always the
            # procedure label ("project verts" vs "project vertices"), and the
            # label itself is sanitised ("update dynamic geometry" becomes
            # ``update_dynamic_geometry``), so it has to be written out
            # whenever it differs from the emitted name.
            #
            # This must not clobber ``label``: a merged receiver can also have
            # a proccode of its own (``Draw`` behind ``Piano.draw``), and losing
            # that swaps the custom block's label on the way back.
            quoted = "" if message == emitted else f' "{message}"'
            receiver = (
                f" // receiver at {_num_text(position[0])},{_num_text(position[1])}{quoted}"
            )
        lines = [self.indent() + f"{header}({signature}) {{{label}{warp}{receiver}"]
        body = body_first
        self.param_renames.append(renames)
        previous_params = self.current_params
        self.current_params = set(argument_names)
        try:
            lines.extend(self._body(blocks, body))
        finally:
            self.param_renames.pop()
            self.current_params = previous_params
        lines.append(self.indent() + "}")
        return lines

    # -- statements --------------------------------------------------------
    def _body(self, blocks: dict, first: dict | None) -> list[str]:
        lines: list[str] = []
        seen_console: set[str] = set()
        block = first
        self.level += 1
        while block is not None:
            counted_for = self._counted_for(blocks, block)
            if counted_for is not None:
                chunk, last = counted_for
                lines.extend(chunk)
                block = _next_block(blocks, last)
                continue
            chunk = self._statement(blocks, block)
            if len(chunk) == 1 and _CONSOLE_LINE_RE.match(chunk[0]):
                key = chunk[0].strip()
                if key in seen_console:
                    # The hidden addon call and the real console block in the
                    # original both land on the same statement.
                    chunk[0] += "  // logged twice in the original project"
                seen_console.add(key)
            lines.extend(chunk)
            block = _next_block(blocks, block)
        self.level -= 1
        return lines

    @staticmethod
    def _is_reporter_block(block: dict) -> bool:
        opcode = block["opcode"]
        if opcode in _BINARY_OPS or opcode in _PROPERTY_GET:
            return True
        if opcode in _REPORTER_BY_OPCODE or opcode in _COMMAND_BY_OPCODE:
            name, spec = _REPORTER_BY_OPCODE.get(opcode) or _COMMAND_BY_OPCODE[opcode]
            return hasattr(spec, "kind") or opcode in _REPORTER_BY_OPCODE
        if opcode in ("operator_not", "operator_join", "utilities_exponent",
                      "utilities_ternaryOperator", "data_variable", "data_itemoflist",
                      "data_lengthoflist", "data_listcontainsitem", "data_itemnumoflist",
                      "argument_reporter_string_number", "argument_reporter_boolean",
                      "lmsTempVars2_getThreadVariable", "lmsTempVars2_getRuntimeVariable",
                      "note", "matrix", "data_listcontents"):
            return True
        if opcode.startswith(("sensing_", "operator_")) and not opcode.endswith(
            ("resettimer", "askandwait", "setdragmode")
        ):
            return True
        if "_menu_" in opcode or opcode.endswith(
            ("_menu", "costume", "backdropnumbername", "costumenumbername")
        ):
            return True
        extension = reverse_extension(opcode)
        if extension is not None:
            return extension[2].is_reporter
        return False

    def _substack(self, blocks: dict, block: dict, name: str) -> list[str]:
        first = _first_block(blocks, block, name)
        return self._body(blocks, first) if first is not None else []

    def _statement(self, blocks: dict, block: dict) -> list[str]:
        opcode = block["opcode"]
        pad = self.indent()

        if opcode in ("control_if", "control_if_else"):
            return self._if_chain(blocks, block)
        if opcode == "control_repeat":
            lines = [f"{pad}repeat ({self._value(blocks, block, 'TIMES')}) {{"]
            lines += self._substack(blocks, block, "SUBSTACK")
            return lines + [f"{pad}}}"]
        if opcode == "control_forever":
            lines = [f"{pad}while (1) {{"]
            lines += self._substack(blocks, block, "SUBSTACK")
            return lines + [f"{pad}}}"]
        if opcode == "control_while":
            condition = self._value(blocks, block, "CONDITION")
            lines = [f"{pad}while ({condition}) {{"]
            lines += self._substack(blocks, block, "SUBSTACK")
            return lines + [f"{pad}}}"]
        if opcode == "control_repeat_until":
            condition = self._negated(blocks, block, "CONDITION")
            lines = [f"{pad}while ({condition}) {{"]
            lines += self._substack(blocks, block, "SUBSTACK")
            return lines + [f"{pad}}}"]
        if opcode == "control_for_each":
            variable = self._variable_field(block)
            value = _range_bound(self._value(blocks, block, "VALUE"))
            lines = [f"{pad}for {variable} in 1..{value} {{"]
            lines += self._substack(blocks, block, "SUBSTACK")
            return lines + [f"{pad}}}"]
        if opcode == "control_stop":
            option = (block.get("fields", {}).get("STOP_OPTION") or ["this script", None])[0]
            if option == "all":
                return [f"{pad}Control.stopAll();"]
            if option == "this script":
                return [f"{pad}Control.stopThisScript();"]
            if option == "other scripts in sprite":
                return [f"{pad}Control.stopOtherScripts();"]
            self.note("W0008", f"unsupported control_stop option {option!r}")
            return [f"{pad}// unsupported stop: {option}"]
        if opcode == "procedures_call":
            console = self._console_name_of(block)
            if console is not None:
                return [f"{pad}{self._console_call_text(blocks, block, console)};"]
            if _console_name((block.get("mutation") or {}).get("proccode", "")) is not None:
                # A console-addon call whose custom block no longer exists: the
                # VM finds no procedure and does nothing, so writing ``log(…)``
                # here would make the rebuild print what the original never did.
                name = _console_name((block.get("mutation") or {}).get("proccode", ""))
                self.note(
                    "W0008",
                    f"dangling console call {name!r} has no definition in "
                    f"{self.current_target!r}; kept as a comment",
                )
                return [f"{pad}// dropped: {name}() — no such custom block in this sprite"]
            return [f"{pad}{self._call_text(blocks, block)};"]
        if opcode in ("event_broadcast", "event_broadcastandwait"):
            wait = opcode == "event_broadcastandwait"
            function = "Control.broadcastAndWait" if wait else "Control.broadcast"
            name = self._broadcast_name(block)
            if name is None:
                # The message is computed at runtime; keep the expression.
                return [f"{pad}{function}({self._value(blocks, block, 'BROADCAST_INPUT')});"]
            call_name = self.receiver_calls.get(name)
            if (
                call_name is not None
                and call_name not in self.local_procedures.get(self.current_target, ())
                and _valid_dotted(call_name)
                and not _is_keyword(call_name)
            ):
                return [f"{pad}{'' if wait else 'call '}{call_name}();"]
            return [f"{pad}{function}({_quote(name)});"]
        if opcode == "data_setvariableto":
            name = self._variable_field(block)
            return [f"{pad}{name} = {self._value(blocks, block, 'VALUE')};"]
        if opcode == "data_changevariableby":
            name = self._variable_field(block)
            return [f"{pad}{name} {self._delta(blocks, block, 'VALUE')};"]
        if opcode == "lmsTempVars2_setThreadVariable":
            name = self._thread_variable(block, "VAR")
            return [f"{pad}{name} = {self._value(blocks, block, 'STRING')};"]
        if opcode == "lmsTempVars2_changeThreadVariable":
            name = self._thread_variable(block, "VAR")
            return [f"{pad}{name} {self._delta(blocks, block, 'NUM')};"]
        list_map = {
            "data_addtolist": ("add", ("ITEM",)),
            "data_deletealloflist": ("clear", ()),
            "data_replaceitemoflist": ("set", ("INDEX", "ITEM")),
            "data_insertatlist": ("insert", ("INDEX", "ITEM")),
            "data_deleteoflist": ("delete", ("INDEX",)),
        }
        if opcode in list_map:
            method, inputs = list_map[opcode]
            name = self._list_field(block)
            args = ", ".join(self._value(blocks, block, key) for key in inputs)
            return [f"{pad}{name}.{method}({args});"]
        if opcode == "data_showvariable":
            return [f"{pad}Variables.showVariable({self._variable_field(block)});"]
        if opcode == "data_hidevariable":
            return [f"{pad}Variables.hideVariable({self._variable_field(block)});"]
        if opcode == "data_showlist":
            return [f"{pad}Lists.showList({self._list_field(block)});"]
        if opcode == "data_hidelist":
            return [f"{pad}Lists.hideList({self._list_field(block)});"]

        property_inverse = _PROPERTY_SET.get(opcode) or _PROPERTY_CHANGE.get(opcode)
        if property_inverse is not None:
            name, operator, slot = property_inverse
            return [f"{pad}{name} {operator} {self._value(blocks, block, slot)};"]

        if opcode == "looks_gotofrontback":
            layer = ((block.get("fields") or {}).get("FRONT_BACK") or ["front"])[0]
            name = "Looks.goToFront" if layer == "front" else "Looks.goToBack"
            return [f"{pad}{name}();"]

        command = _COMMAND_BY_OPCODE.get(opcode)
        if command is not None:
            qname, spec = command
            return [f"{pad}{qname}({self._call_arguments(blocks, block, spec)});"]

        console = _CONSOLE_OPCODES.get(opcode)
        if console is not None:
            name, slot = console
            return [f"{pad}{name}({self._value(blocks, block, slot)});"]
        if opcode == "sipcconsole_debug":
            text = self._value(blocks, block, "string")
            if text.strip('"') == "@breakpoint":
                return [f"{pad}breakpoint;"]
            return [f"{pad}console.debug({text});"]

        extension = reverse_extension(opcode, self._block_values(blocks, block))
        if extension is not None and not extension[2].is_reporter:
            namespace, method, spec = extension
            call = self._extension_call_text(blocks, block, namespace, method, spec)
            nested = self._substack(blocks, block, "SUBSTACK")
            if not nested:
                return [f"{pad}{call};"]
            # Conditional/loop extension blocks own their body: keep it inside
            # so the rebuild has the same shape (and the same behaviour).
            return [f"{pad}{call} {{"] + nested + [f"{pad}}}"]

        # Keep the subtree: an unmapped block must not swallow the statements
        # nested inside it.
        nested = self._substack(blocks, block, "SUBSTACK")
        self.note("W0008", f"unsupported block {opcode!r}")
        if nested:
            lines = [f"{pad}// unsupported block: {opcode}"]
            lines.extend(nested)
            return lines
        return [f"{pad}// unsupported block: {opcode}"]

    def _sensing_of_text(self, blocks: dict, block: dict) -> str:
        """``Sensing.of("prop", "Sprite")`` with the property name kept in sync.

        ``<[my variable] of [Sprite]>`` references the variable *by text*, so a
        renamed variable has to be rewritten here too or the block would read a
        name that no longer exists.
        """
        prop = str((block.get("fields") or {}).get("PROPERTY", [""])[0])
        spec = O.Param("OBJECT", O.MENU, "sensing_of_object_menu", "OBJECT")
        object_text = self._menu_value(blocks, block, spec)
        owner = _unquote(object_text) if object_text.startswith('"') else object_text
        prop = self.var_renames.get(owner, {}).get(prop, prop)
        prop = self.list_renames.get(owner, {}).get(prop, prop)
        return f"Sensing.of({_quote(prop)}, {object_text})"

    def _call_arguments(self, blocks: dict, block: dict, spec) -> str:
        args = []
        for param in spec.params:
            if getattr(param, "prim", None) == O.FIELD:
                field = (block.get("fields") or {}).get(param.name) or ["", None]
                args.append(_quote(str(field[0])))
            elif param.menu:
                args.append(self._menu_value(blocks, block, param))
            else:
                args.append(self._value(blocks, block, param.name))
        return ", ".join(args)

    def _if_chain(self, blocks: dict, block: dict) -> list[str]:
        branches: list[tuple[str, list[str]]] = []
        orelse: list[str] | None = None
        current = block
        while True:
            condition = self._value(blocks, current, "CONDITION")
            branches.append((condition, self._substack(blocks, current, "SUBSTACK")))
            following = _first_block(blocks, current, "SUBSTACK2")
            if following is None:
                break
            if (
                following["opcode"] in ("control_if", "control_if_else")
                and following.get("next") is None
            ):
                current = following
                continue
            orelse = self._body(blocks, following)
            break

        pad = self.indent()
        lines: list[str] = []
        for index, (condition, body) in enumerate(branches):
            keyword = "if" if index == 0 else "} elif"
            lines.append(f"{pad}{keyword} ({condition}) {{")
            lines.extend(body)
        if orelse is not None:
            lines.append(f"{pad}}} else {{")
            lines.extend(orelse)
        lines.append(f"{pad}}}")
        return lines

    def _counted_for(self, blocks: dict, block: dict):
        """``i = 1; repeat_until (not (i < n)) { …; i += 1 }`` -> ``for`` loop.

        The compiler lowers ``for (i = a; i < n; i++)`` to exactly that shape,
        so recognising it keeps the round trip stable and the source readable.
        """
        name, start_slot = self._counter_set(blocks, block)
        if name is None:
            return None
        loop = _next_block(blocks, block)
        if loop is None or loop["opcode"] not in ("control_repeat_until", "control_while"):
            return None
        if _next_block(blocks, loop) is not None:
            return None
        condition = _first_block(blocks, loop, "CONDITION")
        if condition is None:
            return None
        comparison = condition
        if condition["opcode"] == "operator_not":
            comparison = _first_block(blocks, condition, "OPERAND")
            if comparison is None or comparison["opcode"] not in _COMPARISONS:
                return None
        elif condition["opcode"] not in _COMPARISONS:
            return None
        if self._is_not_variable(blocks, comparison, "OPERAND1", name):
            return None
        body = self._substack_blocks(blocks, loop, "SUBSTACK")
        if not body or not self._counter_step(blocks, body[-1], name):
            return None

        start_text = _unquote(self._value(blocks, block, start_slot))
        stop_text = _unquote(self._value(blocks, comparison, "OPERAND2"))
        operator = comparison["opcode"]
        # ``repeat_until`` runs until its condition holds, so the condition is
        # the *exit* test; ``while`` holds the loop test directly.
        if loop["opcode"] == "control_repeat_until" and condition["opcode"] != "operator_not":
            operator = _FLIPPED_COMPARISON[operator]
        # ``operator`` is still an opcode here; ``symbol`` is how it prints.
        symbol = _COMPARISONS[operator]
        if symbol not in ("<", "<="):
            # ``..`` is inclusive and counts up; a descending or ``>=`` loop
            # keeps the explicit three-part form.
            return None
        pad = self.indent()
        if symbol == "<=":
            lines = [f"{pad}for {name} in {_range_bound(start_text)}..{_range_bound(stop_text)} {{"]
        else:
            if name.split(".")[-1].startswith("_"):
                # ``for i in a..b`` keeps its counter in a thread variable, but
                # the three-part form materialises it as a sprite variable.
                # Folding a temp into that would turn it into state on the way
                # back, so leave those loops spelled as ``while``.
                return None
            lines = [
                f"{pad}for ({name}={start_text}, "
                f"{name}{symbol}{stop_text}, {name}++) {{"
            ]
        self.level += 1
        for stmt in body[:-1]:
            lines.extend(self._statement(blocks, stmt))
        self.level -= 1
        lines.append(f"{pad}}}")
        return lines, loop

    def _substack_blocks(self, blocks: dict, block: dict, name: str) -> list[dict]:
        """The blocks of a substack as a list (the last one may carry ``next``)."""
        first = _first_block(blocks, block, name)
        out: list[dict] = []
        current = first
        while current is not None:
            out.append(current)
            current = _next_block(blocks, current)
        return out

    def _thread_name(self, raw: str) -> str:
        """The decompiled spelling of a thread (temp) variable's project name.

        ``useTempVariables`` renames every temp to an underscore-prefixed name,
        and body reads go through ``_thread_variable``. Anything that compares
        or prints a counter name has to apply the same rename, or the loop is
        written with a *sprite* variable that nothing in the body ever reads.
        """
        return self.thread_renames.get(self.current_target, {}).get(raw, raw)

    def _counter_set(self, blocks: dict, block: dict) -> tuple[str | None, str]:
        """``(counter name, input slot)`` for a ``counter = start`` statement."""
        if block["opcode"] == "data_setvariableto":
            return self._variable_field(block), "VALUE"
        if block["opcode"] == "lmsTempVars2_setThreadVariable":
            # A thread (temp) counter keeps its name in an input, not a field.
            return self._thread_name(_unquote(self._value(blocks, block, "VAR"))), "STRING"
        return None, ""

    def _counter_step(self, blocks: dict, block: dict, name: str) -> bool:
        """True when ``block`` is ``counter += 1`` for this counter."""
        if block["opcode"] == "data_changevariableby":
            return (
                self._variable_field(block) == name
                and self._value(blocks, block, "VALUE") == "1"
            )
        if block["opcode"] == "lmsTempVars2_changeThreadVariable":
            return (
                self._thread_name(_unquote(self._value(blocks, block, "VAR"))) == name
                and _unquote(self._value(blocks, block, "NUM")) == "1"
            )
        return False

    def _is_not_variable(self, blocks: dict, block: dict, name: str, variable: str) -> bool:
        """True when the operand is not a read of ``variable``."""
        inner = _first_block(blocks, block, name)
        if inner is None:
            return True
        if inner["opcode"] == "data_variable":
            return self._variable_field(inner) != variable
        if inner["opcode"] == "lmsTempVars2_getThreadVariable":
            return self._thread_name(_unquote(self._value(blocks, inner, "VAR"))) != variable
        return True

    def _negated(self, blocks: dict, block: dict, name: str) -> str:
        """Turn ``repeat_until (not c)`` back into ``while (c)``."""
        inner = _first_block(blocks, block, name)
        if inner is not None and inner["opcode"] == "operator_not":
            return self._value(blocks, inner, "OPERAND")
        condition = self._value(blocks, block, name)
        return f"!({condition})"

    # -- value helpers -----------------------------------------------------
    def _value(self, blocks: dict, block: dict, name: str) -> str:
        text, _ = self._raw(blocks, (block.get("inputs") or {}).get(name))
        return text

    def _delta(self, blocks: dict, block: dict, name: str) -> str:
        """``change x by -0.2`` reads better as ``x -= 0.2``."""
        text = self._value(blocks, block, name)
        if text.startswith("-") and len(text) > 1:
            return f"-= {text[1:]}"
        return f"+= {text}"

    def _menu_value(self, blocks: dict, block: dict, param) -> str:
        raw = (block.get("inputs") or {}).get(param.name)
        if not raw:
            return '""'
        payload = raw[1]
        if isinstance(payload, str):
            shadow = blocks.get(payload)
            if shadow is not None:
                field = shadow.get("fields", {}).get(param.field or "")
                if field:
                    # The value is often a state id (extension menus store the
                    # id and look the target up by name), so prefer the name.
                    return _literal_text(self.state_ids.get(str(field[0]), field[0]))
        return self._value(blocks, block, param.name)

    def _variable_field(self, block: dict) -> str:
        entry = block.get("fields", {}).get("VARIABLE") or ["", None]
        if len(entry) > 1:
            unique = self.state_unique.get(self.current_target, {})
            if entry[1] in unique:
                return unique[entry[1]]
        return self._rename_variable(entry[0])

    def _list_field(self, block: dict) -> str:
        entry = block.get("fields", {}).get("LIST") or ["", None]
        if len(entry) > 1:
            unique = self.state_unique.get(self.current_target, {})
            if entry[1] in unique:
                return unique[entry[1]]
        name = entry[0]
        local = self.list_renames.get(self.current_target, {})
        if name in local:
            return local[name]
        return self.list_renames.get("Stage", {}).get(name, name)

    def _rename_variable(self, name: str) -> str:
        local = self.var_renames.get(self.current_target, {})
        if name in local:
            return local[name]
        return self.var_renames.get("Stage", {}).get(name, name)

    def _thread_variable(self, block: dict, name: str) -> str:
        text, _ = self._raw(block, (block.get("inputs") or {}).get(name))
        original = text.strip('"')
        return self.thread_renames.get(self.current_target, {}).get(original, original)

    def _broadcast_name(self, block: dict) -> str | None:
        """Literal message name, or ``None`` when the message is computed."""
        raw = (block.get("inputs") or {}).get("BROADCAST_INPUT")
        if raw and raw[0] == 1:
            payload = raw[1]
            if isinstance(payload, list) and payload and payload[0] == O.BROADCAST:
                return self._strip_prefix(str(payload[1]))
        return None

    def _strip_prefix(self, name: str, target: str | None = None) -> str:
        """``Graph.doGraph`` inside sprite ``graph`` → ``doGraph``."""
        prefix = self.prefix.get(target or self.current_target)
        if not prefix:
            return name
        head, _, tail = name.partition(".")
        if tail and head.lower() == prefix.lower() and _valid_dotted(tail):
            return tail
        return name

    # -- expressions -------------------------------------------------------
    def _raw(self, blocks: dict, raw) -> tuple[str, int]:
        if not raw:
            return '""', 11
        shape = raw[0]
        if shape == 1:
            payload = raw[1]
            if isinstance(payload, str):
                shadow = blocks.get(payload)
                if shadow is None:
                    return '""', 11
                if self._is_menu_shadow(shadow):
                    # A dropdown used as a value (`round(<note 66>)`), not as a
                    # slot of the parent block: it must stay a block.
                    return _menu_shadow_text(shadow), 11
                return self._expr(blocks, shadow)
            return _literal(payload), 11
        payload = raw[1]
        if payload is None:
            # TurboWarp's compiler writes ``[1, null]`` for an empty value.
            return '""', 11
        if isinstance(payload, list):
            # Scratch allows a primitive (usually a variable) in the block slot:
            # ``[3, [12, "Tick.freeze", "id"], [10, ""]]``.
            if payload and payload[0] == O.VAR and len(payload) > 1:
                if len(payload) > 2 and payload[2] in self.state_unique.get(
                    self.current_target, {}
                ):
                    return self.state_unique[self.current_target][payload[2]], 11
                return self._rename_variable(str(payload[1])), 11
            if payload and payload[0] == O.LIST and len(payload) > 1:
                if len(payload) > 2 and payload[2] in self.state_unique.get(
                    self.current_target, {}
                ):
                    return self.state_unique[self.current_target][payload[2]], 11
                name = str(payload[1])
                local = self.list_renames.get(self.current_target, {})
                if name in local:
                    return local[name], 11
                return self.list_renames.get("Stage", {}).get(name, name), 11
            return _literal(payload), 11
        target = blocks.get(payload)
        if target is None:
            return '""', 11
        return self._expr(blocks, target)

    def _expr(self, blocks: dict, block: dict) -> tuple[str, int]:
        opcode = block["opcode"]

        if opcode == "note":
            # Shadow dropdown for a MIDI note value.
            field = (block.get("fields") or {}).get("NOTE") or ["", None]
            return _literal_text(field[0]), 11
        if opcode in (
            "sensing_touchingobjectmenu", "sensing_keyoptions", "looks_costume",
            "control_create_clone_of_menu", "sound_sounds_menu", "sound_effects_menu",
            "motion_goto_menu", "operator_mathop_menu",
        ):
            fields = block.get("fields") or {}
            if fields:
                return _literal_text(next(iter(fields.values()))[0]), 11
            return '""', 11
        if _is_menu_opcode(opcode) and block.get("fields"):
            # Extension dropdowns (``music_menu_INSTRUMENT`` …) are shadows.
            return _literal_text(next(iter(block["fields"].values()))[0]), 11
        if opcode in ("looks_costumenumbername", "looks_backdropnumbername"):
            # Two reporters share one opcode (``number`` / ``name``); the
            # reverse table can only hold one of them.
            kind = (block.get("fields", {}).get("NUMBER_NAME") or ["number", None])[0]
            what = "costume" if "costume" in opcode else "backdrop"
            method = "Name" if kind == "name" else "Number"
            return f"Looks.{what}{method}()", 11
        if opcode == "data_variable":
            return self._variable_field(block), 11
        if opcode == "data_listcontents":
            return self._list_field(block), 11
        if opcode in ("argument_reporter_string_number", "argument_reporter_boolean"):
            name = (block.get("fields", {}).get("VALUE") or ["", None])[0]
            if self.current_params is not None and name not in self.current_params:
                # A leftover block referring to an argument this block does not
                # have (``<is compiled?>``); keep the block, it reads as empty.
                self.note(
                    "W0005",
                    f"argument {name!r} is not declared here; kept as a raw block"
                )
                return f'block({_quote(opcode)}, "VALUE", {_literal_text(name)})', 11
            # Argument names are arbitrary text in a project ("is TurboWarp?");
            # declarations get the same sanitising, so both sides agree.
            if self.param_renames and name in self.param_renames[-1]:
                name = self.param_renames[-1][name]
            else:
                name = _safe_name(name)
            return name, 11
        if opcode == "lmsTempVars2_getThreadVariable":
            return self._thread_variable(block, "VAR"), 11
        if opcode == "data_itemoflist":
            name = self._list_field(block)
            return f"{name}.get({self._value(blocks, block, 'INDEX')})", 11
        if opcode == "data_lengthoflist":
            return f"{self._list_field(block)}.length()", 11
        if opcode == "data_listcontainsitem":
            name = self._list_field(block)
            return f"{name}.contains({self._value(blocks, block, 'ITEM')})", 4
        if opcode == "data_itemnumoflist":
            name = self._list_field(block)
            return f"{name}.indexOf({self._value(blocks, block, 'ITEM')})", 11

        if opcode in _BINARY_OPS:
            symbol, precedence, left_name, right_name = _BINARY_OPS[opcode]
            left, left_prec = self._raw(blocks, (block.get("inputs") or {}).get(left_name))
            right, right_prec = self._raw(blocks, (block.get("inputs") or {}).get(right_name))
            # Keep the tree shape identical: a same-precedence right child must
            # be parenthesised (``a * (b / c)`` is not ``a * b / c``).
            if left_prec < precedence:
                left = f"({left})"
            if right_prec <= precedence:
                right = f"({right})"
            return f"{left} {symbol} {right}", precedence

        if opcode == "operator_not":
            operand_block = _first_block(blocks, block, "OPERAND")
            if operand_block is not None and operand_block["opcode"] in _NEGATED:
                inner_left, _ = self._raw(blocks, (operand_block.get("inputs") or {}).get("OPERAND1"))
                inner_right, _ = self._raw(blocks, (operand_block.get("inputs") or {}).get("OPERAND2"))
                negated_symbol, negated_prec = _NEGATED[operand_block["opcode"]]
                return f"{inner_left} {negated_symbol} {inner_right}", negated_prec
            operand, precedence = self._raw(blocks, (block.get("inputs") or {}).get("OPERAND"))
            if precedence < 9:
                operand = f"({operand})"
            return f"!{operand}", 9

        if opcode == "operator_length":
            # ``length of []`` reads better as a builtin than as ``Operators.length``.
            return f"lengthOf({self._value(blocks, block, 'STRING')})", 11

        if opcode == "operator_join":
            leaves: list[tuple[str, int]] = []
            self._flatten_join(blocks, block, leaves)
            if len(leaves) >= self.fstring_threshold:
                folded = self._fold_join(blocks, block)
                if folded is not None:
                    return folded
            if len(leaves) > 2:
                # ``join(a, join(b, c))`` reads as one call; the compiler
                # rebuilds a balanced tree, which is also what a project with
                # more than a couple of operands needs.
                return f"join({', '.join(text for text, _ in leaves)})", 11
            right, _ = self._raw(blocks, (block.get("inputs") or {}).get("STRING2"))
            if right == '""':
                # ``join(x, "")`` is just ``x``: Scratch values are text anyway,
                # so ``string()`` would only add noise.
                return self._raw(blocks, (block.get("inputs") or {}).get("STRING1"))
            return (
                f"join({self._value(blocks, block, 'STRING1')}, {right})",
                11,
            )

        if opcode == "utilities_ternaryOperator":
            condition = self._value(blocks, block, "A")
            then = self._value(blocks, block, "B")
            otherwise = self._value(blocks, block, "C")
            return f"({then}) if ({condition}) else ({otherwise})", 1

        if opcode == "utilities_exponent":
            left, left_prec = self._raw(blocks, (block.get("inputs") or {}).get("A"))
            right, right_prec = self._raw(blocks, (block.get("inputs") or {}).get("B"))
            if left_prec <= 8 or left_prec == 9:
                left = f"({left})"
            if right_prec < 8:
                right = f"({right})"
            return f"{left} ** {right}", 8

        if opcode == "sensing_of":
            return self._sensing_of_text(blocks, block), 11

        property_name = _PROPERTY_GET.get(opcode)
        if property_name is not None:
            return property_name, 11

        reporter = _REPORTER_BY_OPCODE.get(opcode)
        if reporter is not None:
            qname, spec = reporter
            return f"{qname}({self._call_arguments(blocks, block, spec)})", 11

        if opcode == "procedures_call":
            return self._call_text(blocks, block), 11

        extension = reverse_extension(opcode, self._block_values(blocks, block))
        if extension is not None:
            namespace, method, spec = extension
            return self._extension_call_text(blocks, block, namespace, method, spec), 11

        self.note("W0008", f"unsupported reporter {opcode!r}")
        return f"0 /* {opcode} */", 11

    def _fold_join(self, blocks: dict, block: dict) -> tuple[str, int] | None:
        """Render a long join chain as one ``f"…{x}…"`` literal."""
        leaves: list[tuple[str, int]] = []
        if not self._flatten_join(blocks, block, leaves) or len(leaves) < self.fstring_threshold:
            return None
        out: list[str] = []
        for text, precedence in leaves:
            if precedence >= 11 and not (text.startswith('"') and text.endswith('"')):
                out.append("{" + text + "}")
            elif text.startswith('"') and text.endswith('"') and len(text) >= 2:
                out.append(_fstring_text(_unquote(text)))
            elif precedence == 11:
                out.append("{" + text + "}")
            else:
                out.append("{" + text + "}")
        return f'f"{"" .join(out)}"', 11

    def _flatten_join(self, blocks: dict, block: dict, leaves: list) -> bool:
        for name in ("STRING1", "STRING2"):
            child = _first_block(blocks, block, name)
            if child is not None and child["opcode"] == "operator_join":
                if not self._flatten_join(blocks, child, leaves):
                    return False
            else:
                text, precedence = self._raw(blocks, (block.get("inputs") or {}).get(name))
                leaves.append((text, precedence))
        return True

    def _block_values(self, blocks: dict, block: dict) -> dict[str, object]:
        """The argument values a block currently carries.

        Menus live in a shadow block, plain arguments in the input itself; both
        are flattened to ``name -> value`` so an alias whose pinned argument
        matches this block can be preferred when decompiling.
        """
        values: dict[str, object] = {}
        for name, field in (block.get("fields") or {}).items():
            values[name] = field[0] if isinstance(field, (list, tuple)) and field else field
        for name, entry in (block.get("inputs") or {}).items():
            if not isinstance(entry, list) or len(entry) != 2:
                continue
            source = entry[1]
            if isinstance(source, str):
                shadow = blocks.get(source) or {}
                for field_name, field in (shadow.get("fields") or {}).items():
                    values.setdefault(name, field[0] if isinstance(field, (list, tuple)) and field else field)
                    values.setdefault(field_name, values[name])
            elif isinstance(source, list) and len(source) >= 2:
                values.setdefault(name, source[1])
        return values

    def _extension_call_text(self, blocks: dict, block: dict, namespace, method, spec) -> str:
        args: list[str] = []
        for name, arg_type, menu in spec.args:
            if name in spec.values:
                continue  # pinned by the alias
            if menu:
                value = self._menu_field(blocks, block, name, menu)
                constant = (
                    MICROBIT_CONSTANT_NAMES.get(value) if namespace == "microbit" else None
                )
                if constant is not None:
                    # ``microbit.buttonA`` reads better than ``"A"``, and it is
                    # the same value the compiler turns back into the menu.
                    args.append(f"microbit.{constant}")
                else:
                    args.append(repr(value).replace("'", '"', 2))
            elif block["opcode"].startswith("lmsTempVars2_") and name == "VAR":
                # The loop/set/get family names a thread variable; it goes
                # through the same renaming as every other reference, or the
                # body would read a different (empty) variable.
                args.append(_quote(self._thread_variable(block, name)))
            else:
                args.append(self._value(blocks, block, name))
        return f"{namespace}.{method}({', '.join(args)})"

    def _menu_field(self, blocks: dict, block: dict, name: str, menu: str) -> str:
        raw = (block.get("inputs") or {}).get(name)
        if isinstance(raw, list) and len(raw) > 1 and isinstance(raw[1], str):
            shadow = blocks.get(raw[1])
            if shadow is not None:
                field = (shadow.get("fields") or {}).get(menu)
                if field:
                    # Menus that pick a variable or list store its id, and the
                    # extension looks it up by name; write the name instead.
                    return self.state_ids.get(str(field[0]), str(field[0]))
        return ""

    def _console_name_of(self, block: dict) -> str | None:
        """The console method a *resolvable* call stands for, else ``None``.

        A ``procedures_call`` whose proccode has no ``procedures_definition``
        anywhere in the target is a dangling block: the VM looks the procedure
        up, finds nothing and returns. Rewriting it to ``log(...)`` would make
        the rebuilt project *print* what the original never printed, so a call
        only becomes a console statement when the callee really exists.
        """
        proccode = (block.get("mutation") or {}).get("proccode", "")
        name = _console_name(proccode)
        if name is None:
            return None
        if proccode not in self.target_procedures.get(self.current_target, set()):
            return None
        return name

    def _console_call_text(self, blocks: dict, block: dict, name: str) -> str:
        """``log(x)`` from a hidden custom-block call to the console addon."""
        if name == "breakpoint":
            return "breakpoint"
        try:
            argument_ids = json_loads((block.get("mutation") or {}).get("argumentids", "[]"))
        except ValueError:
            argument_ids = []
        args = [self._value(blocks, block, arg) for arg in argument_ids]
        return f"{name}({', '.join(args)})"

    def _call_text(self, blocks: dict, block: dict) -> str:
        mutation = block.get("mutation") or {}
        proccode = mutation.get("proccode", "")
        try:
            argument_ids = json_loads(mutation.get("argumentids", "[]"))
        except ValueError:
            argument_ids = []
        name = self._proc_display_name({"name": self.current_target}, proccode)
        args = [self._value(blocks, block, arg_id) for arg_id in argument_ids]
        return f"{name}({', '.join(args)})"


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------
def json_loads(value) -> list:
    if isinstance(value, list):
        return value
    return json.loads(value or "[]")


def _blocks(target: dict) -> dict:
    return {key: value for key, value in target.get("blocks", {}).items() if isinstance(value, dict)}


def _first_block(blocks: dict, block: dict, name: str) -> dict | None:
    raw = (block.get("inputs") or {}).get(name)
    if not raw:
        return None
    if raw[0] in (2, 3):
        # ``[3, [12, …], [10, ""]]`` puts an inline primitive in the slot.
        return blocks.get(raw[1]) if isinstance(raw[1], str) else None
    if raw[0] == 1 and isinstance(raw[1], str):
        return blocks.get(raw[1])
    return None


def _body_uses_variable(blocks: dict, first: dict | None, name: str) -> bool:
    """True when a variable called ``name`` is read or written in this script."""
    seen = set()
    stack = [first] if first is not None else []
    while stack:
        block = stack.pop()
        if block is None or block.get("opcode") is None:
            continue
        key = id(block)
        if key in seen:
            continue
        seen.add(key)
        field = (block.get("fields") or {}).get("VARIABLE")
        if field and field[0] == name:
            return True
        if block["opcode"].startswith("lmsTempVars2_"):
            raw = (block.get("inputs") or {}).get("VAR")
            if raw and raw[0] == 1 and isinstance(raw[1], list) and len(raw[1]) > 1:
                if str(raw[1][1]) == name:
                    return True
        nxt = block.get("next")
        if nxt:
            stack.append(blocks.get(nxt))
        for raw in (block.get("inputs") or {}).values():
            if isinstance(raw, list) and len(raw) > 1:
                payload = raw[1]
                if isinstance(payload, str):
                    stack.append(blocks.get(payload))
    return False


def _next_block(blocks: dict, block: dict) -> dict | None:
    nxt = block.get("next")
    return blocks.get(nxt) if nxt else None


def _needs_rename(name: str, local: bool) -> bool:
    """Names JadenScript cannot spell, and dotted names of sprite-local state.

    A dot means "Stage variable" in this language, so a sprite-local project
    variable called ``buffer.glyphs`` has to lose the dot or every read would
    resolve to a (new, empty) Stage list.
    """
    if not _valid_dotted(name):
        return True
    if any(part in ALL_KEYWORDS for part in name.split(".")):
        return True
    return local and "." in name


def _sanitised_name(name: str, taken: set[str], local: bool) -> str:
    """A valid identifier for a project name that JadenScript cannot spell.

    Names are kept as close to the project's own text as possible: the
    decompiled source should read like the project.
    """
    base = name.replace(".", "_") if local else name
    candidate = _flat_identifier(base)
    while candidate in taken or candidate in ALL_KEYWORDS:
        candidate += "_"
    taken.add(candidate)
    return candidate


def _flat_identifier(name: str) -> str:
    """Single identifier (class and asset names cannot contain dots)."""
    return _safe_name(str(name or "").replace(".", "_"))


def _safe_name(name: str) -> str:
    """Identifier that is guaranteed not to collide with a keyword."""
    cleaned = _identifier(name)
    if cleaned in ALL_KEYWORDS:
        cleaned += "_"
    return cleaned


def _identifier(name: str) -> str:
    parts = []
    for part in (name or "").split("."):
        cleaned = re.sub(r"[^A-Za-z0-9_$]", "_", part)
        if not cleaned or not re.match(r"[A-Za-z_$]", cleaned[0]):
            cleaned = "_" + cleaned
        parts.append(cleaned)
    return ".".join(parts)


def _is_thread_variable(name: str) -> bool:
    return name.startswith("_")


def _is_literal_text(text: str) -> bool:
    """``""`` / ``"x"`` / ``42`` — a value that would compile to no block at all."""
    if _bare_number(text) is not None:
        return True
    return len(text) >= 2 and text[0] == '"' and text[-1] == '"'


#: ``public void Stage.startFrameCounter() {`` -> ``Stage.startFrameCounter``
_HEADER_NAME_RE = re.compile(r"void\s+([A-Za-z_$][\w$.]*)\s*\(")


def _quoted_name(name: str) -> str:
    """Sprite names are free text; quote them when they contain a space."""
    text = str(name)
    if " " in text or '"' in text:
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _valid_dotted(name: str) -> bool:
    return all(_IDENT_RE.match(part) for part in name.split("."))


def _is_keyword(name: str) -> bool:
    return name in ALL_KEYWORDS





# TurboWarp console tools: the Consoles extension blocks and the zero-width
# custom-block names the console addon intercepts both mean the same thing.
_CONSOLE_OPCODES = {
    "sipcconsole_Journal": ("log", "string"),
    "sipcconsole_Warning": ("warn", "string"),
    "sipcconsole_Error": ("error", "string"),
    "sipcconsole_Timeron": ("startTimer", "string"),
    "sipcconsole_Timeroff": ("stopTimer", "string"),
}
_CONSOLE_NAMES = {"log", "warn", "error", "startTimer", "stopTimer", "breakpoint"}

# Comparisons a lowered ``for`` loop can hold.
_COMPARISONS = {
    "operator_lt": "<",
    "operator_gt": ">",
    "operator_lte": "<=",
    "operator_gte": ">=",
}
_FLIPPED_COMPARISON = {
    "operator_lt": "operator_gte",
    "operator_gt": "operator_lte",
    "operator_lte": "operator_gt",
    "operator_gte": "operator_lt",
}

# Dropdown shadow blocks that carry their value in a single field.
_MENU_OPS = {
    "note", "looks_costume", "looks_backdrops", "sound_sounds_menu",
    "sound_effects_menu", "control_create_clone_of_menu", "motion_goto_menu",
    "motion_glideto_menu", "motion_pointtowards_menu", "sensing_touchingobjectmenu",
    "sensing_distancetomenu", "sensing_keyoptions", "sensing_of_object_menu",
    "operator_mathop_menu", "event_broadcast_menu", "argument_reporter_string_number",
    "argument_reporter_boolean",
}


_PLAIN_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _monitor_owner(sprite: str) -> str:
    """``Fancy Terminal`` -> ``"Fancy Terminal"``: a monitor outlives its sprite."""
    flat = _flat_identifier(sprite)
    return flat if flat == sprite else _quote(sprite)


def _import_alias(name: str) -> str:
    """``as buffer.glyphs`` is not an identifier, so quote dotted names."""
    return name if _PLAIN_NAME_RE.match(name) else _quote(name)


def _is_menu_opcode(opcode: str) -> bool:
    """``music_menu_INSTRUMENT``, ``pen_menu_colorParam``: a dropdown block.

    A plain ``"menu" in opcode`` test is wrong: ``looks_costu**me**`` +
    ``**nu**mbername`` contains it by accident.
    """
    return "_menu_" in opcode or opcode.endswith(("_menu", "_options"))


def _menu_shadow_text(block: dict) -> str:
    """``menu("note", "NOTE", "66")`` — a dropdown block kept as a block."""
    fields = block.get("fields") or {}
    field, value = next(iter(fields.items()), ("", ["", None]))
    return (
        f'menu({_quote(block["opcode"])}, {_quote(str(field))}, '
        f"{_literal_text(value[0])})"
    )
_CONSOLE_LINE_RE = re.compile(
    r"\s*(?:(?:log|warn|error|startTimer|stopTimer)\s*\(|breakpoint\s*;)"
)


def _console_name(proccode: str) -> str | None:
    """``\u200blog\u200b\u200b %s`` -> ``log``; ``\u200bbreakpoint\u200b\u200b`` -> ``breakpoint``."""
    text = proccode.replace("\u200b", "").split(" %")[0].strip()
    return text if text in _CONSOLE_NAMES else None


_ROTATION_WORDS = {"all around": "all", "left-right": "leftright", "don't rotate": "none"}


#: micro:bit hats: opcode -> (method name, hook comment template).
_MICROBIT_HATS = {
    "microbit_whenButtonPressed": ("buttonPressed", "when [{value}] button pressed"),
    "microbit_whenGesture": ("gesture", "when [{value}]"),
    "microbit_whenTilted": ("tilted", "when tilted [{value}]"),
    "microbit_whenPinConnected": ("pinConnected", "when pin [{value}] connected"),
}
#: Hat -> (block argument, menu name). A menu shadow keeps its value under the
#: menu name, so ``_menu_field`` needs both.
_MICROBIT_FIELDS = {
    "microbit_whenButtonPressed": ("BTN", "buttons"),
    "microbit_whenGesture": ("GESTURE", "gestures"),
    "microbit_whenTilted": ("DIRECTION", "tiltDirectionAny"),
    "microbit_whenPinConnected": ("PIN", "touchPins"),
}


def _range_bound(text: str) -> str:
    """A range bound as source: bare when atomic, parenthesised otherwise."""
    text = (text or "").strip() or "0"
    if text.startswith("(") and text.endswith(")"):
        return text
    if re.fullmatch(r"[-\w.$]+", text):
        return text
    return f"({text})"


def _rotation_word(style) -> str:
    return _ROTATION_WORDS.get(str(style), "all")


def _num_text(value) -> str:
    """Numbers as source text: ``1`` not ``1.0``, but ``0.5`` stays ``0.5``."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else repr(float(value))
    return str(value)


def _literal_text(value) -> str:
    """Preserve the stored JSON type: numbers stay numbers, strings stay quoted.

    Used where the JSON type is part of the state (variable and list values,
    dropdown fields): ``"1"`` must stay a string. Block *inputs* go through
    :func:`_literal`, which drops the quotes for numbers.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else repr(value)
    return _quote("" if value is None else str(value))


_NUMERIC_TEXT = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
# Text that can be written without quotes and read back as the same number:
# plain decimals without leading zeros, and hex/binary/octal in source spelling.
_BARE_NUMBER = re.compile(
    r"-?(?:"
    r"0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+"
    r"|(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"|\.\d+(?:[eE][+-]?\d+)?"
    r")"
)


def _bare_number(text: str) -> str | None:
    """``text`` itself when it is a number literal, else ``None``."""
    if not text or text != text.strip():
        return None
    return text if _BARE_NUMBER.fullmatch(text) else None


def _literal(payload) -> str:
    if not payload:
        return '""'
    primitive = payload[0]
    value = payload[1] if len(payload) > 1 else ""
    if primitive in (O.MATH_NUM, O.POSITIVE_NUM, O.WHOLE_NUM, O.INTEGER, O.ANGLE):
        # Scratch stores numeric primitive values as *strings*; only quote when
        # the text is not a number (``float('inf')`` accepts junk, so use a
        # strict pattern). Hex/binary/octal count as numbers: TurboWarp keeps
        # ``[4, "0xFFFFFFFF"]``, and the compiler turns it back into 4294967295.
        text = "" if value is None else str(value).strip()
        return _bare_number(text) or _literal_text(value)
    if primitive == O.TEXT:
        # ``"1"`` in a block is just 1: write it bare so the source reads like
        # the block does, and keep hex/binary/octal spelling as it was written.
        text = "" if value is None else str(value)
        return _bare_number(text) or _quote(text)
    if primitive == O.VAR and isinstance(payload, list) and len(payload) >= 2:
        return str(payload[1])
    if primitive == O.LIST and isinstance(payload, list) and len(payload) >= 2:
        return str(payload[1])
    return _quote(str(value))


def _quote(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def decompile(
    loaded: LoadedProject,
    asset_prefix: str = "assets/",
    max_inline: int = 50,
    include_assets: bool = True,
) -> tuple[str, list[str], dict[str, bytes]]:
    decompiler = Decompiler(
        loaded, asset_prefix=asset_prefix, max_inline=max_inline, include_assets=include_assets
    )
    source = decompiler.decompile()
    return source, decompiler.diagnostics, decompiler.extra_files


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        body = text[1:-1]
        out = []
        i = 0
        while i < len(body):
            if body[i] == "\\" and i + 1 < len(body):
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(body[i + 1], body[i + 1]))
                i += 2
                continue
            out.append(body[i])
            i += 1
        return "".join(out)
    return text


def _fstring_text(text: str) -> str:
    out = []
    for ch in text:
        if ch in '{}"\\':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)
