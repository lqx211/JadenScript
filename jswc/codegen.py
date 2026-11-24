"""AST → Scratch block IR.

Lowering rules worth knowing about (all verified against the reference
``sample_np4.26_sb3/project.json``):

* ``class X``                    → sprite named ``X``
* ``public void Ns.Name()``      → custom block **and** a "when I receive
  Ns.Name" receiver, so ``call Ns.Name()`` from another sprite works
* ``call f()``                   → ``event_broadcast`` (fire and forget)
* ``f()``                        → ``procedures_call`` (blocking)
* ``Name.member`` reads/writes   → stage variable ``"Name.member"``
* bare ``x``                     → sprite-local (or thread-local) variable
* ``for``                        → ``control_for_each`` / ``control_repeat_until``
* ``a if c else b``              → ``utilities_ternaryOperator``
"""

from __future__ import annotations

import collections
import functools

import dataclasses
import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import ast_nodes as A
from . import opcodes as O
from . import keycodes
from .ir import Block, IdGen, Inline, Lit, Project, Ref, ShadowRef, Target, Value
from . import layout, lints
from .obfuscate import corrupt_block_positions, obfuscate_project
from .vmprotect import protect_project
from .extension_blocks import MICROBIT_CONSTANTS
from . import extensions
from .extensions import ARITY_ALIASES, known_namespaces, resolve
from .keywords import BUILTIN_NAMESPACES, FEATURES, STATE_ALIASES
from .tokens import CompileError
from .vars import VarPlan, collect_list_names, plan_variables

TEMP_VARS_EXTENSION = "lmsTempVars2"

#: How many cloud variables a project may declare and still have them load as
#: cloud: ``Runtime.cloudOptions.limit`` is 10 in scratch-vm, and the ones past
#: it are silently loaded as ordinary stage variables.
CLOUD_VARIABLE_LIMIT = 10
_IMAGE_FORMATS = {"svg", "png", "jpg", "jpeg", "bmp", "gif", "webp"}
_SOUND_FORMATS = {"wav", "mp3", "ogg"}
_HOOK_RE = re.compile(
    # A quoted key may contain escapes, so a plain ``[^"]*`` stops too early on
    # the ``"`` key (``["\""]``).
    r"hook:\s*when\s*\[\s*(?:\"((?:[^\"\\]|\\.)*)\"|([^\]]*))\s*\]\s*key",
    re.IGNORECASE,
)
#: Text that is really a *float*, so it needs no hiding. Integers are pooled
#: like any other literal: the numbers a project runs on say what it does just
#: as much as the sentences do.
_POOL_SKIP = re.compile(r"-?(?:\d+\.\d*|\.\d+|\d+)[eE][-+]?\d+|-?\d+\.\d*|-?\.\d+")

#: Longest hat body ``doObfuscation`` turns into a state machine. Every state
#: adds a branch to a dispatch block, so a script with more blocks than this is
#: left straight -- the noise is worth less than the blocks it costs.
DEFAULT_OBF_FLATTEN = 16

#: How many lists the text literals are spread over. One list holding every
#: string in the project is the one thing in the workspace that reads as "this
#: is the string table"; three of them look like three more pieces of state, and
#: the decoy lists are grown to match. Costs nothing at run time.
DEFAULT_OBF_POOLS = 3

#: A literal that is a plain integer -- the case ``--obf-ints none`` keeps inline.
_INTEGER = re.compile(r"-?\d+")


@dataclass
class _Pool:
    """One list of collected literals, and every reporter that reads it."""

    name: str
    list_id: str
    items: dict[str, int] = field(default_factory=dict)
    lookups: list[tuple[Block, str]] = field(default_factory=list)


_BROADCAST_HOOK_RE = re.compile(r"hook:\s*broadcast\s+(.+?)\s*(?://|$)", re.IGNORECASE)
_FLAG_HOOK_RE = re.compile(r"hook:\s*(?:when\s*)?(?:green\s*flag|flag\s*clicked)", re.IGNORECASE)
_CLONE_HOOK_RE = re.compile(r"hook:\s*(?:when\s*)?clone", re.IGNORECASE)
_CLICK_HOOK_RE = re.compile(r"hook:\s*(?:when\s*)?(?:sprite\s*)?click", re.IGNORECASE)
_STAGE_CLICK_HOOK_RE = re.compile(
    r"hook:\s*(?:when\s*)?(?:stage|backdrop)\s*click", re.IGNORECASE
)
_BACKDROP_HOOK_RE = re.compile(r"hook:\s*backdrop\s+(.+?)\s*$", re.IGNORECASE)
_SAVE_HOOK_RE = re.compile(r"hook:\s*(before|after)\s*(?:project\s*)?sav", re.IGNORECASE)
#: ``// hook: WebSocket.onMessage`` — any extension EVENT block becomes a hat.
_EXT_EVENT_HOOK_RE = re.compile(r"hook:\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
# micro:bit hats carry their menu value in the brackets:
#   // hook: when [A] button pressed
#   // hook: when [moved]
#   // hook: when tilted [front]
#   // hook: when pin [0] connected
_MICROBIT_BUTTON_RE = re.compile(
    r"hook:\s*when\s*\[\s*(?P<value>[^\]]*?)\s*\]\s*button\s*pressed", re.IGNORECASE
)
_MICROBIT_GESTURE_RE = re.compile(
    r"hook:\s*when\s*\[\s*(?P<value>[^\]]*?)\s*\]\s*$", re.IGNORECASE
)
_MICROBIT_TILT_RE = re.compile(
    r"hook:\s*when\s*tilted\s*\[\s*(?P<value>[^\]]*?)\s*\]", re.IGNORECASE
)
_MICROBIT_PIN_RE = re.compile(
    r"hook:\s*when\s*pin\s*\[\s*(?P<value>[^\]]*?)\s*\]\s*connected", re.IGNORECASE
)
_PROCCODE_RE = re.compile(r"proccode\s+([^/]+?)\s*(?=//|$)", re.IGNORECASE)
_AT_RE = re.compile(r"\bat\s+(-?[\d.]+)\s*,\s*(-?[\d.]+)")
_UNDEFINED_RE = re.compile(r"\bundefined\b", re.IGNORECASE)
#: ``// warp`` runs the whole custom block without yielding; ``// no-warp``
#: keeps its frames even under ``--warp``. The negative form has to be read
#: first, or ``no-warp`` matches the word ``warp`` and means the opposite.
_NO_WARP_RE = re.compile(r"\bno[-_ ]?warp\b")
_WARP_RE = re.compile(r"(?<!no[-_ ])\bwarp\b")
# TurboWarp's console tools, exposed as plain statements. They compile to the
# "Consoles" extension (``sipcconsole``), which is what the debugger's Logs tab
# shows - not the browser's ``console.log``.
_CONSOLE_STATEMENTS: dict[str, tuple[str, str | None]] = {
    "log": ("sipcconsole_Journal", "string"),
    "warn": ("sipcconsole_Warning", "string"),
    "error": ("sipcconsole_Error", "string"),
    "startTimer": ("sipcconsole_Timeron", "string"),
    "stopTimer": ("sipcconsole_Timeroff", "string"),
    # No dedicated breakpoint block exists; mark the point in the Logs tab.
    "breakpoint": ("sipcconsole_debug", None),
}
_STATE_FORMATS = {"jsa", "jsl", "jsv"}

# Dropdowns whose value is a *number*; anything else names an object, so a
# computed value there is resolved at runtime (an "eval").
_NUMERIC_MENUS = {"note", "music_menu_INSTRUMENT", "music_menu_DRUM"}
_STATIC_LITERALS = (A.StringLit, A.NumberLit, A.BoolLit)
_RECEIVER_AT_RE = re.compile(
    r'receiver\s+at\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)(?:\s+"([^"]*)")?'
)
_ASSET_META_RE = re.compile(r"\b(centerx|centery|res|rate|samples)=(-?[\d.]+)")
_MONITOR_RE = re.compile(
    r"monitor\s+(?:\"(?P<qowner>[^\"]+)\"|(?P<owner>[^.\s]+))\."
    r"(?P<name>\S+)\s+(?P<mode>default|large|slider|list)(?P<rest>.*)$",
    re.IGNORECASE,
)
_MONITOR_POS_RE = re.compile(r"at\s+(-?[\d.]+),(-?[\d.]+)")
_MONITOR_SIZE_RE = re.compile(r"(-?[\d.]+)x(-?[\d.]+)")
_MONITOR_RANGE_RE = re.compile(r"range\s+(-?[\d.]+)\.\.(-?[\d.]+)")
_MONITOR_ID_RE = re.compile(r'\bid=(?:"([^"]*)"|(\S+))')
_NOTE_RE = re.compile(
    r"comment\s+(?P<owner>\S+?)\s+at\s+(?P<x>-?[\d.]+)\s*,\s*(?P<y>-?[\d.]+)"
    r"\s+(?P<w>-?[\d.]+)x(?P<h>-?[\d.]+)(?P<min>\s+minimized)?\s*:\s*(?P<text>\".*\")",
    re.S,
)
_WATCH_RE = re.compile(
    r"watch\s+(?:\"(?P<qowner>[^\"]+)\"|(?P<owner>[^.\s]+))\."
    r"(?P<opcode>\S+)(?P<rest>.*)$",
    re.IGNORECASE,
)
_WATCH_MODE_RE = re.compile(r"\bmode=(\w+)")

_COMPARE_OPS = {
    "==": "operator_equals",
    "<": "operator_lt",
    ">": "operator_gt",
}
_LOOP_OPS = {"<=": False, "<": True, ">=": False, ">": True}


def _unescape_key(text: str) -> str:
    """Undo the escaping the decompiler applies to a quoted hook key.

    ``// hook: when ["\""] key pressed`` is the ``"`` key; the comment is text,
    so the backslashes are literal and have to be removed here.
    """
    out: list[str] = []
    index = 0
    simple = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(simple.get(text[index + 1], text[index + 1]))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _script_name(fn) -> str:
    """The name a ``metadata … pos`` line uses for this function.

    It is what the source spells: the qualified name when there is a
    namespace (``Stage.startFrameCounter``), otherwise the plain method name.
    """
    return ".".join(fn.name) if len(fn.name) > 1 else (fn.name[0] if fn.name else "")


def _is_number_text(text: str) -> bool:
    """``65`` / ``0x41`` / ``0b1000001`` — a number, not a key name."""
    try:
        int(text, 0)
    except ValueError:
        return False
    return True


def _inside_arguments(fn):
    """Run ``fn`` with ``_arg_depth`` raised, i.e. its expressions are args."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        self._arg_depth += 1
        try:
            return fn(self, *args, **kwargs)
        finally:
            self._arg_depth -= 1

    return wrapper


@dataclass
class Scope:
    params: set[str] = field(default_factory=set)


def iter_nodes(node):
    """Yield ``node`` and every AST node below it."""
    yield node
    for f in dataclasses.fields(node):
        value = getattr(node, f.name)
        if isinstance(value, A.Node):
            yield from iter_nodes(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, A.Node):
                    yield from iter_nodes(item)
                elif isinstance(item, tuple):
                    for sub in item:
                        if isinstance(sub, A.Node):
                            yield from iter_nodes(sub)


def _mentions_ternary(node: A.Node | None) -> bool:
    """True when ``node`` holds a ``a if c else b`` somewhere."""
    if node is None:
        return False
    return any(isinstance(child, A.Ternary) for child in iter_nodes(node))


class Codegen:
    def __init__(
        self,
        program: A.Program,
        *,
        seed: str = "jswc",
        locals_mode: str = "sprite",
        warp: bool = False,
        strict: bool = False,
        base_dir: str | Path | None = None,
        allow_undefined: bool = False,
        features: set[str] | None = None,
        obf_junk: int | None = None,
        obf_flatten: int = DEFAULT_OBF_FLATTEN,
        obf_ints: str | bool = "auto",
        obf_pools: int = DEFAULT_OBF_POOLS,
        obf_scrub: bool = False,
        only_sprite: str | None = None,
        vm_budget: int = 1,
        vm_style: str = "h0",
        vm_seed: int | None = None,
        vm_split: bool = True,
        vm_crypt: int = 1,
        column_height: int = layout.COLUMN_HEIGHT,
        layout_mode: str = "auto",
    ):
        if locals_mode not in ("sprite", "thread"):
            raise ValueError("locals_mode must be 'sprite' or 'thread'")
        if vm_style not in ("list", "h0"):
            raise ValueError("vm_style must be 'list' or 'h0'")
        if layout_mode not in ("auto", "always", "never"):
            raise ValueError("layout_mode must be 'auto', 'always' or 'never'")
        self.program = program
        self.project = Project(seed)
        self.locals_mode = locals_mode
        # ``#enable_feature<useTempVariables>`` is what makes ``_x`` a per-thread
        # variable; its tests established that Scratch clones copy sprite-local
        # variables, so a plain sprite variable is already a per-clone local.
        self.thread_locals = "useTempVariables" in (
            set(program.features) | set(features or ())
        )
        self.warp = warp
        self.strict = strict
        self.base_dir = Path(base_dir) if base_dir is not None else Path.cwd()
        self.allow_undefined = allow_undefined
        self.features: set[str] = set(program.features) | set(features or ())
        # ``#enable_feature<vanillaScratch>`` compiles to blocks scratch.mit.edu
        # can run. Two constructs this language emits have an exact vanilla
        # spelling, and they are lowered here rather than in the source -- a
        # source cannot say "the same loop, but the other block".
        self.vanilla = "vanillaScratch" in self.features
        # The two switches contradict each other: ``useTempVariables`` *is* the
        # lmsTempVars2 extension, which scratch.mit.edu does not have. Vanilla
        # is the promise that the project loads there, so it wins, and the
        # author is told the thread-variable switch was ignored.
        self.vanilla_thread_switch = False
        if self.vanilla and (self.thread_locals or self.locals_mode == "thread"):
            self.thread_locals = False
            self.locals_mode = "sprite"
            self.vanilla_thread_switch = True
        # ``--enable hideObjectNames`` still works; the source spelling is
        # normalised by the parser, this covers the command line.
        if "hideObjectNames" in self.features:
            self.features.add("doObfuscation")
        self.obfuscating = "doObfuscation" in self.features
        #: First broadcast whose name is an expression: obfuscation renames
        #: every message, so this one can never be found again.
        self._computed_broadcast: A.Expr | None = None
        #: ``doObfuscation`` moves every text literal into a list, so the
        #: scripts read as blocks instead of sentences. One entry per pool; see
        #: ``obf_pools`` for why there is more than one.
        self._pools: list[_Pool] = []
        #: Text -> ``(pool, item number)``, so the same literal used twice is
        #: one item and one lookup rather than two.
        self._pool_at: dict[str, tuple[int, int]] = {}
        #: Integer literals that are used as *text* somewhere: those keep their
        #: exact spelling and stay in the pool (see `_integers_as_variables`).
        self._text_integers: set[str] = set()
        #: Literal text -> the Stage variable that now holds it. Diagnostics and
        #: tests read this; nothing else does.
        self.constant_variables: dict[str, str] = {}
        #: How many junk items each pool gets (``None`` = one per real item),
        #: how long a hat body may be to become a state machine, how many pools
        #: the literals are split over, and whether to scrub the editor residue.
        self.obf_junk = obf_junk
        self.obf_flatten = obf_flatten
        self.obf_pools = max(1, obf_pools)
        self.obf_scrub = obf_scrub
        #: ``corruptBlockPos`` writes a project the VM runs but the editor
        #: cannot open: dangling block pointers and a ``%b`` parameter holding
        #: a string reporter. Off by default; it is anti-tamper, not a name
        #: obfuscator (see ``obfuscate.corrupt_block_positions``).
        self.corrupt_block_pos = "corruptBlockPos" in self.features
        #: ``vmProtect`` compiles supported hat bodies to bytecode for a
        #: per-sprite interpreter. ``vm_budget`` instructions run per loop
        #: iteration; 1 is the deliberately slow default, and 0 is treated as
        #: 1 (the interpreter always has to run at least one step).
        self.vm_protect = "vmProtect" in self.features
        self.vm_budget = max(0, vm_budget)
        self.vm_style = vm_style
        #: A fixed seed makes the randomized VM layout reproducible.  None
        #: means a fresh architecture for every build.
        self.vm_seed = vm_seed
        #: h0 style: put each instruction's body in its own helper method.
        self.vm_split = vm_split
        #: 0 = fixed layout, 1 = randomized layout, 2 = checksum, 3 = decoys.
        self.vm_crypt = max(0, min(3, vm_crypt))
        self.column_height = max(480, column_height)
        self.layout_mode = layout_mode
        #: ``--only-sprite NAME``: emit the Stage and that one sprite. The
        #: others are dropped after their scripts are built, so a quick edit of
        #: one sprite does not have to re-serialise the whole project.
        self.only_sprite = only_sprite
        self._dropped_sprites: set[str] = set()
        #: Variables ``Variables.showVariable(x)`` names, so the project can
        #: carry a monitor entry for each one it did not already have.
        self._shown_state: list[tuple[object, str]] = []
        #: Integers are hidden like the text literals are, but as *variables*
        #: rather than list items: a variable read compiles to a property load,
        #: which is what keeps a literal-heavy machine fast (see syntax.md for
        #: the measurement). ``list`` is the older form, ``none`` keeps them
        #: inline for a build that has to keep every last frame.
        self.obf_ints = (
            "auto" if obf_ints is True else "none" if obf_ints is False else str(obf_ints)
        )
        self.plan: VarPlan | None = None
        self.diagnostics: list[str] = []
        if self.vanilla_thread_switch:
            self.diagnostics.append(
                "0:0: W0027 vanillaScratch beats useTempVariables: the local "
                "variables of this build stay sprite-local"
            )

        self.class_by_name: dict[str, A.ClassDecl] = {}
        self.class_funcs: dict[str, dict[str, A.FuncDecl]] = {}
        self.funcs_by_qualified: dict[str, tuple[str, A.FuncDecl]] = {}
        self.class_qualified: dict[tuple[str, str], A.FuncDecl] = {}
        #: Call name -> broadcast message for ``// hook: broadcast M`` hats. A
        #: receiver has no custom block behind it, so calling it can only mean
        #: "broadcast": ``M();`` waits for the receivers, ``call M();`` does not.
        self.receiver_by_name: dict[str, str] = {}
        self.list_names: set[str] = set()
        self._arg_id_cache: dict[tuple[str, str], list[str]] = {}

        self.target: Target | None = None
        self.class_name: str = ""
        self.namespace: str = ""
        self.namespaced = False
        self.scope = Scope()
        #: Block ids whose x/y came from the source; layout skips them.
        self._fixed_positions: set[str] = set()
        self._next_position: tuple[float, float] | None = None
        #: ``metadata <name> pos x,y;`` — consumed in source order.
        self._positions: dict[str, list[tuple[float, float]]] = {}
        #: Class name -> Scratch target name (``obj_loader`` -> ``obj loader``).
        self.target_name: dict[str, str] = {}
        #: Scratch target name -> the namespace style its own scripts used.
        #: ``vmProtect`` emits its runtime into the same target later, after the
        #: loop that normally sets these fields, so it has to restore the style
        #: or an unqualified custom-block call becomes a missing ``Sprite.name``.
        self.target_namespaces: dict[str, tuple[str, bool]] = {}
        #: The script currently being emitted, so ``_top_level`` can find it.
        self._script_key: str | None = None
        #: Temp (``_name``) loop variables whose loop is currently open: a
        #: nested loop reusing one would clobber the outer counter.
        self._active_loops: list[str] = []
        #: ``Temp.setRuntimeVariable("x", …)`` lowers to the stage variable
        #: ``x`` instead of the extension's own store. Disabled for a project
        #: that also deletes or enumerates them, where the two differ.
        self.convert_runtime_vars = True
        #: Names converted so far, so the Stage can reset them on green flag.
        self.runtime_vars: set[str] = set()

    # -- diagnostics -------------------------------------------------------
    def diag(self, node: A.Node, code: str, message: str) -> None:
        entry = f"{node.line}:{node.col}: {code} {message}"
        if entry not in self.diagnostics:
            self.diagnostics.append(entry)

    # -- pishiSupport -------------------------------------------------------
    def _check_pishi_names(self) -> None:
        """Two name collisions `pishiSupport` has to talk about.

        The feature hands the program eight new namespaces (`Facemesh`,
        `Arduino`, …). A namespace is how a block is *spelled*, so:

        * another extension claiming the same name is an **error** -- the
          compiler would resolve `Arduino.high(13)` to one of them and the
          player would run the other, and which one wins is an accident of
          table order;
        * a variable, list or sprite of the same name is a **warning** -- the
          declaration does not fail, but the namespace wins every time the
          name is used as a receiver, so `Arduino` the variable and `Arduino`
          the extension cannot both be spelled that way.

        Both are reported once, at the top of the file: there is no single
        node to point at, and a per-use warning would bury the file in them.
        """
        if extensions.PISHI_FEATURE not in self.features:
            return
        for namespace, extension, other in extensions.pishi_namespace_conflicts():
            self.diagnostics.append(
                f"0:0: E0077 {namespace} is the {extension} extension's namespace, "
                f"and {other!r} already provides it; one of the two has to be dropped"
            )
        for namespace in sorted(extensions.pishi_namespaces()):
            for kind, names in (
                ("variable", self.plan.globals),
                ("list", self.plan.lists),
            ):
                if namespace in names:
                    self.diagnostics.append(
                        f"0:0: W0028 {kind} {namespace!r} has the same name as the "
                        f"{namespace} extension namespace; a call to "
                        f"{namespace}.something() reaches the extension"
                    )
        for cls in self.program.classes:
            if cls.name in extensions.pishi_namespaces():
                self.diagnostics.append(
                    f"0:0: W0028 sprite {cls.name!r} has the same name as the "
                    f"{cls.name} extension namespace; a call to "
                    f"{cls.name}.something() reaches the extension"
                )

    # -- pre-pass ----------------------------------------------------------
    def _collect(self) -> None:
        for cls in self.program.classes:
            self.class_by_name[cls.name] = cls
            self.class_funcs[cls.name] = {}
            for f in cls.funcs:
                self.class_funcs[cls.name][f.proccode] = f
                # Two sprites can declare the same qualified name (a project
                # with a copied procedure); remember each one so a call from
                # inside the sprite stays local.
                self.class_qualified[(cls.name, f.qualified)] = f
                self.funcs_by_qualified.setdefault(f.qualified, (cls.name, f))
        # Class name -> the target name in the project. Declarations replaced
        # the ``// Sprite:`` comment, so a sprite called ``obj loader`` is
        # declared as ``obj_loader: name="obj loader"``.
        declared = {d.sprite: d for d in self.program.sprite_meta}
        for cls in self.program.classes:
            real = cls.name
            if cls.sprite is not None and "commentMetadata" in self.features:
                real = cls.sprite.name or cls.name
            decl = declared.get(cls.name)
            if decl is not None:
                value = _metadata_value(decl.values.get("name"))
                if isinstance(value, str) and value:
                    real = value
            self.target_name[cls.name] = real
        # List inference lives in vars.py so namespaces are excluded once,
        # for both the planner and the generator.
        self.list_names = collect_list_names(self.program)
        self._collect_receivers()
        self._collect_metadata()

    def _collect_receivers(self) -> None:
        """Register the call name of every ``// hook: broadcast`` hat.

        A receiver is a hat, not a custom block, so ``X();`` cannot be a
        ``procedures_call``: it broadcasts. A real custom block of the same
        name wins, because that is the older and more common meaning.
        """
        procedures = {
            f.qualified
            for cls in self.program.classes
            for f in cls.funcs
            if _BROADCAST_HOOK_RE.search(f.annotation or "") is None
        }
        for cls in self.program.classes:
            namespace = self._declared_namespace(cls)
            for f in cls.funcs:
                match = _BROADCAST_HOOK_RE.search(f.annotation or "")
                if match is None:
                    continue
                message = match.group(1).strip()
                if not message:
                    continue
                names = {f.qualified, f.proccode}
                if namespace:
                    # A sender in another sprite spells the owner's prefix out:
                    # ``Lyrics.init();`` must reach the ``init`` receiver.
                    names.add(f"{namespace}.{f.qualified}")
                    names.add(f"{namespace}.{f.proccode}")
                if all(re.match(r"[A-Za-z_$][\w$]*", part) for part in message.split(".")):
                    # The message itself is a legal call name, and it is the
                    # only one a sender in another sprite can use when the
                    # receiver's method lost its prefix.
                    names.add(message)
                for key in names:
                    if key and key not in procedures:
                        self.receiver_by_name.setdefault(key, message)

    def _declared_namespace(self, cls: A.ClassDecl) -> str:
        """The ``namespace=`` / ``ns=`` a class declares, or ``""``."""
        value = None
        if cls.sprite is not None and "commentMetadata" in self.features:
            value = cls.sprite.namespace
        for decl in self.program.sprite_meta:
            if decl.sprite == cls.name:
                value = _metadata_value(decl.values.get("namespace")) or value
        if value is None:
            value = _metadata_value(cls.params.get("namespace"))
        return value if isinstance(value, str) else ""

    # -- helpers -----------------------------------------------------------
    def _new(self, opcode: str) -> Block:
        assert self.target is not None
        return self.target.new_block(opcode)

    def _link(self, blocks: list[Block]) -> list[Block]:
        for first, second in zip(blocks, blocks[1:]):
            first.next = second.id
            second.parent = first.id
        return blocks

    def _top_level(self, block: Block) -> Block:
        block.top_level = True
        if self._next_position is not None:
            # ``metadata … pos`` / ``// at x,y``: the author's own position, so
            # it is kept and the layout pass leaves this block alone.
            block.x, block.y = self._next_position
            self._next_position = None
            self._fixed_positions.add(block.id)
        return block

    def _attach_substack(self, block: Block, name: str, stmts: list[A.Stmt]) -> None:
        head = self._emit_stmts(stmts)
        if head is not None:
            head.parent = block.id
            block.set_input(name, Ref(head))

    # -- entry point -------------------------------------------------------
    def compile(self) -> Project:
        self._collect()
        for feature in sorted(self.features - FEATURES):
            self.diag(
                self.program,
                "E0069",
                f"unknown feature {feature!r}; known: {', '.join(sorted(FEATURES))}",
            )
        self.convert_runtime_vars = not _uses_extension_runtime_api(self.program)
        self.plan = plan_variables(
            self.program, self.base_dir, self.allow_undefined, self.features
        )
        self.diagnostics.extend(self.plan.warnings)
        self._check_pishi_names()
        self.list_names |= set(self.plan.lists)
        # Declare planned state eagerly so variables/lists exist even when the
        # source only reads them (or never touches them at all).
        # File-scope declarations and imports have no owning class, so they are
        # declared up front; everything else declares itself where it is used.
        for name, definition in self.plan.globals.items():
            if definition.from_import or definition.file_scope or definition.cloud:
                self.project.stage.declare_variable(
                    name, definition.initial, cloud=definition.cloud
                )
        cloud_names = [n for n, d in self.plan.globals.items() if d.cloud]
        if len(cloud_names) > CLOUD_VARIABLE_LIMIT:
            # sb3.js stops honouring the flag at the runtime's limit, and the
            # leftover variables look normal in the editor -- so say it here.
            self.diagnostics.append(
                f"0:0: W0032 {len(cloud_names)} cloud variables are declared but the "
                f"player only accepts {CLOUD_VARIABLE_LIMIT}; "
                + ", ".join(cloud_names[CLOUD_VARIABLE_LIMIT:])
                + " will load as plain stage variables"
            )
        # Sorted: a set of names has no order, and the order they are declared
        # in decides the order of the Stage's list ids, which is visible in the
        # file. Two builds of one source should not differ on it.
        for name in sorted(self.plan.imported_lists | self.plan.file_scope_lists):
            self.project.stage.declare_list(name, self.plan.lists[name])
        for note in lints.run(self.program):
            if note not in self.diagnostics:
                self.diagnostics.append(note)
        if self.program.stage is not None:
            self._emit_stage(self.program.stage)
        for cls in self.program.classes:
            self._emit_class(cls)
        self._only_sprite_filter()
        self._emit_notes()
        self._emit_monitors()
        self._emit_show_monitors()
        self._emit_runtime_var_reset()
        if self.vm_protect:
            protect_project(
                self.project,
                self,
                budget=self.vm_budget,
                style=self.vm_style,
                split=self.vm_split,
                crypt=self.vm_crypt,
            )
        if self.obfuscating:
            if self.obf_ints == "auto":
                self._integers_as_variables()
            pools: list[tuple[str, list[tuple[Block, str]]]] = []
            for pool in self._pools:
                if not pool.items:
                    # Nothing landed in this pool: drop it rather than publish a
                    # list that exists only because a hash did not match it.
                    self.project.stage.lists.pop(pool.list_id, None)
                    continue
                # ``dict`` keeps insertion order, so the item number is the
                # index -- right up until obfuscation shuffles the list and
                # rewrites every index that reads it.
                self.project.stage.lists[pool.list_id] = [pool.name, list(pool.items)]
                pools.append((pool.list_id, pool.lookups))
            obfuscate_project(
                self.project,
                pool=pools,
                junk=self.obf_junk,
                flatten=self.obf_flatten,
                scrub=self.obf_scrub,
                corrupt=self.corrupt_block_pos,
            )
            if self._computed_broadcast is not None:
                self.diag(
                    self._computed_broadcast,
                    "W0023",
                    "a broadcast name built at runtime cannot survive obfuscation",
                )
        self.project.normalize()
        # After normalize, so blocks added by a later pass (vmProtect's runtime,
        # the obfuscator's state machines) are placed too, and before the
        # corrupt pass, which moves things on purpose.
        if self.layout_mode != "never":
            # `always` is TurboWarp's clean-up button: ignore the positions the
            # source recorded and arrange everything. `auto` keeps them, because
            # a decompiled project's `metadata pos` is the author's own layout.
            fixed = set() if self.layout_mode == "always" else self._fixed_positions
            layout.layout_project(
                self.project.targets, fixed=fixed, column_height=self.column_height
            )
        if self.corrupt_block_pos:
            # After ``normalize``: it recomputes every parent link, which is
            # exactly the pointer the corrupt form needs to leave dangling.
            corrupt_block_positions(self.project)
        return self.project

    def _only_sprite_filter(self) -> None:
        """``--only-sprite NAME``: keep the Stage and the named sprite."""
        wanted = self.only_sprite
        if not wanted:
            return
        keep = self._resolve_owner(wanted)
        if keep is None or keep is self.project.stage:
            self.diagnostics.append(
                f"0:0: E0071 no such sprite or class: --only-sprite {wanted!r}"
            )
            return
        for target in list(self.project.targets):
            if target is self.project.stage or target is keep:
                continue
            self._dropped_sprites.add(target.name)
            self.project.targets.remove(target)
        for cls in self.program.classes:
            if self.target_name.get(cls.name, cls.name) != keep.name:
                self._dropped_sprites.add(cls.name)
        self._warn_dropped_calls()

    def _warn_dropped_calls(self) -> None:
        """Report a kept sprite that calls a block on a sprite left out.

        The call resolves at compile time, so the block would reference a
        custom block nobody defines any more; the VM then does nothing when it
        is reached, which is a quiet wrong answer rather than a loud failure.
        """
        if not self._dropped_sprites:
            return
        prefixes = tuple(f"{name}." for name in self._dropped_sprites)
        reported: set[str] = set()

        def reached(target, name: str) -> None:
            owner = name.split(".")[0]
            if owner in reported:
                return
            reported.add(owner)
            self.diagnostics.append(
                f"0:0: W0029 --only-sprite left out {owner!r}, which "
                f"{target.name!r} still reaches"
            )

        for target in self.project.targets:
            for block in target.blocks.values():
                # A call inside the sprite is a ``procedures_call``; one into
                # another sprite is a broadcast, because that is what the VM
                # needs to leave a target.
                if block.opcode == "procedures_call" and block.mutation:
                    proccode = str(block.mutation.get("proccode", ""))
                    if proccode.startswith(prefixes):
                        reached(target, proccode)
                elif block.opcode in ("event_broadcast", "event_broadcastandwait"):
                    # The name lives in the input as a broadcast primitive
                    # (``[11, name, id]``) or, in older files, in a menu shadow.
                    stack = [block.inputs.get("BROADCAST_INPUT"),
                             block.fields.get("BROADCAST_OPTION")]
                    while stack:
                        value = stack.pop()
                        if isinstance(value, str):
                            if value.startswith(prefixes):
                                reached(target, value)
                        elif isinstance(value, list):
                            stack.extend(value)

    def _resolve_owner(self, owner: str):
        """The target a comment / monitor names, tolerating the class spelling."""
        if owner == "Stage":
            return self.project.stage
        real = self.target_name.get(owner, owner)
        for candidate in (real, owner):
            found = next((t for t in self.project.targets if t.name == candidate), None)
            if found is not None:
                return found
        return None

    def _emit_runtime_var_reset(self) -> None:
        """A Stage script that zeroes the converted runtime variables.

        The extension kept its own store, so nothing reset those names at the
        start of a run; as real variables they need the same treatment the
        project author expects from a global — ``when green flag clicked: set
        each to 0``.
        """
        if not self.runtime_vars:
            return
        stage = self.project.stage
        previous = (self.target, self.class_name, self.scope)
        self.target = stage
        self.class_name = "Stage"
        self.scope = Scope()
        try:
            hat = self._top_level(self._new("event_whenflagclicked"))
            blocks: list[Block] = [hat]
            for name in sorted(self.runtime_vars):
                block = self._new("data_setvariableto")
                block.set_input("VALUE", Lit(O.TEXT, "0"))
                block.set_field("VARIABLE", name, stage.declare_variable(name, 0))
                blocks.append(block)
            self._link(blocks)
        finally:
            self.target, self.class_name, self.scope = previous

    def _emit_notes(self) -> None:
        """``// comment Stage at x,y WxH: "text"`` -> workspace comments."""
        for index, text in enumerate(self.program.notes):
            match = _NOTE_RE.search(text)
            if not match:
                self.diagnostics.append(f"W0018 unreadable comment note: // {text[:60]}...")
                continue
            owner = match.group("owner")
            target = self._resolve_owner(owner)
            if target is None:
                self.diagnostics.append(f"W0016 comment for unknown target {owner!r}")
                continue
            try:
                body = json.loads(match.group("text"))
            except ValueError:
                self.diagnostics.append("W0017 comment text is not a valid string literal")
                continue
            comment_id = IdGen(f"comment:{owner}:{index}").next("comment")
            self.project.comments.setdefault(target.name, {})[comment_id] = {
                "blockId": None,
                "x": float(match.group("x")),
                "y": float(match.group("y")),
                "width": float(match.group("w")),
                "height": float(match.group("h")),
                "minimized": bool(match.group("min")),
                "text": body,
            }

    def _emit_monitors(self) -> None:
        """Turn ``// monitor …`` comments back into ``project.json`` monitors."""
        for text in self.program.monitors:
            if text.startswith("watch "):
                self._emit_watch(text)
                continue
            match = _MONITOR_RE.match(text)
            if not match:
                self.diagnostics.append(f"E0070 unreadable monitor comment: // {text}")
                continue
            owner = match.group("qowner") or match.group("owner")
            if owner in self._dropped_sprites:
                continue
            name = match.group("name")
            rest = match.group("rest")
            mode = match.group("mode").lower()
            stale_owner = False
            if owner == "Stage":
                target = self.project.stage
                sprite_name = None
            else:
                target = self._resolve_owner(owner)
                if target is None:
                    # A monitor can outlive the sprite it was made for; keep it
                    # with its original label so the file matches the project.
                    stale_owner = True
                    target = self.project.stage
                    sprite_name = owner
                else:
                    sprite_name = target.name
            is_list = name in target._list_ids
            if stale_owner:
                # The state lives wherever the name is declared.
                holder = next(
                    (t for t in self.project.targets
                     if name in t._list_ids or name in t._var_ids),
                    None,
                )
                if holder is not None:
                    target = holder
                    is_list = name in holder._list_ids
                sprite_name = owner
            elif not is_list and name not in target._var_ids:
                # Globals are declared on the stage; accept monitors written
                # against the sprite that uses them.
                if owner != "Stage" and name in self.project.stage._var_ids:
                    target, sprite_name = self.project.stage, None
                else:
                    self.diagnostics.append(f"E0072 monitor {owner}.{name}: unknown state")
                    continue
            position = _MONITOR_POS_RE.search(rest)
            size = _MONITOR_SIZE_RE.search(rest)
            slider = _MONITOR_RANGE_RE.search(rest)
            state_id = (
                target.list_id(name) if is_list else target.var_id(name)
            ) or IdGen(f"monitor:{owner}:{name}").next("monitor")
            entry: dict = {
                # Scratch keys a variable monitor by the *variable id*, so the
                # editor reuses the stored position and size when the project
                # shows the variable.
                "id": state_id,
                "mode": "list" if is_list else mode,
                "opcode": "data_listcontents" if is_list else "data_variable",
                "params": {"LIST" if is_list else "VARIABLE": name},
                "spriteName": sprite_name,
                "value": [] if is_list else 0,
                "width": int(float(size.group(1))) if size else 100,
                "height": int(float(size.group(2))) if size else 24,
                "x": int(float(position.group(1))) if position else 0,
                "y": int(float(position.group(2))) if position else 0,
                "visible": "hidden" not in rest.lower(),
            }
            if not is_list:
                # Scratch writes these three on every *variable* monitor, not
                # only on sliders; leaving them out makes the editor fall back
                # to its own defaults instead of the project's.
                entry["sliderMin"] = float(slider.group(1)) if slider else 0
                entry["sliderMax"] = float(slider.group(2)) if slider else 100
                entry["isDiscrete"] = ("discrete" in rest.lower()) if slider else True
            self.project.monitors.append(entry)

    def _emit_watch(self, text: str) -> None:
        """``// watch Stage.sensing_timer at 94,73 0x0 shown``."""
        match = _WATCH_RE.match(text)
        if not match:
            self.diagnostics.append(f"E0070 unreadable monitor comment: // {text}")
            return
        owner = match.group("qowner") or match.group("owner")
        if owner in self._dropped_sprites:
            return
        opcode = match.group("opcode")
        rest = match.group("rest")
        mode = _WATCH_MODE_RE.search(rest)
        sprite_name = None
        if owner != "Stage":
            cls = self.class_by_name.get(owner)
            target = next((t for t in self.project.targets if t.name == owner), None)
            if target is None and cls is not None and cls.sprite is not None:
                target = next(
                    (t for t in self.project.targets if t.name == cls.sprite.name), None
                )
            if target is None:
                # A monitor can outlive its sprite; keep the name as stored.
                target = None
                sprite_name = owner
            else:
                sprite_name = target.name
        position = _MONITOR_POS_RE.search(rest)
        size = _MONITOR_SIZE_RE.search(rest)
        stored_id = _MONITOR_ID_RE.search(rest)
        self.project.monitors.append(
            {
                "id": (stored_id.group(1) or stored_id.group(2)) if stored_id
                else IdGen(f"monitor:{owner}:{opcode}").next("monitor"),
                "mode": mode.group(1) if mode else "default",
                "opcode": opcode,
                "params": {},
                "spriteName": sprite_name,
                "value": 0,
                "width": int(float(size.group(1))) if size else 0,
                "height": int(float(size.group(2))) if size else 0,
                "x": int(float(position.group(1))) if position else 0,
                "y": int(float(position.group(2))) if position else 0,
                "visible": "hidden" not in rest.lower(),
                "sliderMin": 0,
                "sliderMax": 100,
                "isDiscrete": True,
            }
        )

    def _emit_show_monitors(self) -> None:
        """Store a monitor for every variable ``Variables.showVariable`` names.

        Hidden on purpose: the block decides when the variable appears, so a
        visible entry would put it on screen at load. What the entry buys is the
        place and size the editor remembers for that variable.
        """
        for target, name in self._shown_state:
            sprite_name = None if target is self.project.stage else target.name
            if any(
                entry["params"].get("VARIABLE") == name
                and entry.get("spriteName") == sprite_name
                for entry in self.project.monitors
            ):
                continue
            state_id = target.var_id(name)
            if state_id is None:
                continue
            self.project.monitors.append(
                {
                    "id": state_id,
                    "mode": "default",
                    "opcode": "data_variable",
                    "params": {"VARIABLE": name},
                    "spriteName": sprite_name,
                    "value": 0,
                    "width": 100,
                    "height": 24,
                    "x": 0,
                    "y": 0,
                    "visible": False,
                    "sliderMin": 0,
                    "sliderMax": 100,
                    "isDiscrete": True,
                }
            )

    def _is_reporter_expr(self, expr: A.Expr | None) -> bool:
        """True for expressions that produce a block but run no command."""
        if expr is None or isinstance(
            expr, (A.NumberLit, A.StringLit, A.BoolLit, A.NullLit)
        ):
            return False
        if not isinstance(expr, A.Call):
            return True
        receiver = expr.receiver
        if receiver is None:
            return expr.name in {
                "join", "string", "len", "length", "lengthOf", "float",
                "isTurboWarp", "isCompiled", "menu", "block", *_MATH_BUILTINS,
            }
        if not isinstance(receiver, A.Name):
            return False
        full = ".".join(receiver.parts)
        qname = f"{full}.{expr.name}"
        if qname in O.REPORTERS or qname in O.PROPERTIES:
            return True
        if full in self.list_names and expr.name in O.LIST_REPORTERS:
            return True
        extension = resolve(full, expr.name, len(expr.args), self.features)
        return bool(extension is not None and extension.is_reporter)

    def _emit_class(self, cls: A.ClassDecl) -> None:
        metadata = self._sprite_metadata(cls)
        target = self.project.add_target(metadata.pop("name", cls.name))
        self.target = target
        self.class_name = cls.name
        namespace = metadata.pop("namespace", None)
        self.namespace = namespace or target.name
        # Only classes that declare ``ns=X`` use the prefix style, so hand
        # written sources are never rewritten implicitly.
        self.namespaced = "namespacedNames" in self.features and bool(namespace)
        self.target_namespaces[target.name] = (self.namespace, self.namespaced)
        # Keep the numeric type: float positions must not be truncated.
        target.x = _whole_if_integral(metadata.get("x", 0))
        target.y = _whole_if_integral(metadata.get("y", 0))
        target.size = _whole_if_integral(metadata.get("size", 100))
        target.direction = _whole_if_integral(metadata.get("direction", 90))
        target.visible = bool(metadata.get("show", 1))
        costume = metadata.get("costume")
        if costume is not None:
            target.current_costume = int(_whole_if_integral(costume))
        volume = metadata.get("volume")
        if volume is not None:
            target.volume = float(volume)
        layer = metadata.get("layer")
        if layer is not None:
            target.layer_order = int(_whole_if_integral(layer))
        rotation = metadata.get("rotation")
        if rotation is not None:
            # The declaration spells the short word (``all``), while the
            # comment spelling already maps to the project's own text.
            target.rotation_style = _ROTATION_STYLES.get(str(rotation), str(rotation))
        draggable = metadata.get("draggable")
        if draggable is not None:
            target.draggable = bool(draggable)
        if self.plan is not None:
            for name, definition in self.plan.class_vars.get(cls.name, {}).items():
                if definition.from_import and not definition.is_temp:
                    target.declare_variable(self._qualify(name), definition.initial)
            for name, items in self.plan.class_lists.get(cls.name, {}).items():
                target.declare_list(name, items)
        for member in cls.members:
            if isinstance(member, A.FuncDecl):
                self._emit_func(target, cls, member)
            elif isinstance(member, A.ImportDecl):
                self._import_asset(target, member)
            elif isinstance(member, A.Assign) and member.op is None:
                continue  # ``private _x;`` — the thread variable needs no state
            elif isinstance(member, A.Assign) and member.op == "=":
                self._declare_class_initial(member)
            elif isinstance(member, A.LooseDecl):
                self._emit_loose(member)
            elif isinstance(member, A.MetaDecl):
                continue  # collected by ``_collect_metadata``
            else:
                self.diag(member, "W0010", "class-level statements are not supported yet")

    def _declare_list_literal(
        self,
        parts: tuple[str, ...],
        literal: A.ListLit,
        public: bool = False,
        declare: bool = False,
    ) -> None:
        items = [_list_item(item) for item in literal.items]
        if any(item is None for item in items):
            self.diag(literal, "E0045", "list literals may only contain literal values")
            items = [item for item in items if item is not None]
        name = ".".join(parts)
        assert self.target is not None
        full = self._qualify(name) if len(parts) == 1 else name
        if not declare and (
            full in self.list_names or (self.plan is not None and full in self.plan.lists)
        ):
            # The list already exists, so this is an *assignment*, not a
            # declaration: ``list = []`` has to mean "empty it". Dropping the
            # statement instead is how a buffer-clearing line became a no-op and
            # a loader that read a second file kept the first one's bytes -- the
            # list grew, nothing failed, and the only symptom was a wrong parse.
            return self._list_assign(full, items)
        if len(parts) == 1 and not public:
            self.target.declare_list(name, items)
        else:
            self.project.stage.declare_list(full, items)
        return []

    def _list_assign(self, name: str, items: list[str]) -> list[Block]:
        """``list = [...]`` on a list that exists: clear it, then fill it."""
        list_id = self._declare_list(name)
        clear = self._new("data_deletealloflist")
        clear.set_field("LIST", name, list_id)
        head = clear
        tail = clear
        for item in items:
            add = self._new("data_addtolist")
            add.set_field("LIST", name, list_id)
            add.set_input("ITEM", Lit(O.TEXT, item))
            add.parent = tail.id
            tail.next = add.id
            tail = add
        return [head]

    def _declare_class_initial(self, stmt: A.Assign) -> None:
        """``public c = 1145;`` at class level only seeds the initial state.

        Literal values live in ``project.json``; anything else is assigned from
        a generated green-flag script so the runtime behaviour is preserved.
        """
        if stmt.target is None:
            return
        name = ".".join(stmt.target.parts)
        if isinstance(stmt.value, A.ListLit):
            blocks = self._declare_list_literal(
                stmt.target.parts,
                stmt.value,
                public=stmt.visibility == "public",
                # At class level this line *is* the declaration: its items are
                # the initial state, not a reset to run at the green flag. Going
                # through the assignment path emitted a second top-level script
                # holding only `delete all of list`, which then came back from
                # the decompiler as a green flag block nobody wrote.
                declare=True,
            )
            if blocks:
                # The list already existed, so this line is an *assignment*
                # (`list = []` empties it) and the statement produced blocks
                # rather than an initial value. Dropping them left the
                # `delete all of list` with no parent, which the structural
                # check rejects -- and, worse, would have made a class-level
                # reset silently not happen. It runs at the green flag, like
                # every other class-level initial that is not a literal.
                hat = self._new("event_whenflagclicked")
                self._top_level(hat)
                blocks[0].parent = hat.id
                hat.next = blocks[0].id
            return
        is_global = self.plan is not None and name in self.plan.globals
        qualified = self._qualify(name) if is_global else name
        cloud = bool(
            self.plan is not None
            and getattr(self.plan.globals.get(name), "cloud", False)
        )
        if is_global:
            self.project.stage.declare_variable(
                qualified, self._initial_of(name), cloud=cloud
            )
        elif self.target is not None:
            self.target.declare_variable(qualified, self._initial_of(name))
        from .vars import literal_text

        if literal_text(stmt.value) is None:
            hat = self._new("event_whenflagclicked")
            self._top_level(hat)
            blocks = self._emit_stmt(stmt)
            if blocks:
                blocks[0].parent = hat.id
                hat.next = blocks[0].id

    def _collect_metadata(self) -> None:
        """Split ``metadata`` statements into positions and monitor lines."""
        for decl in self.program.meta:
            if decl.kind == "pos":
                self._positions.setdefault(decl.target, []).append(
                    (decl.values["x"], decl.values["y"])
                )
            elif decl.kind == "line":
                text = decl.text.strip()
                low = text.lower()
                if low.startswith("comment ") or low.startswith("note "):
                    # ``metadata note <owner> at …: "text"`` is the workspace
                    # comment the ``// comment`` spelling writes.
                    body = text.split(None, 1)[1] if " " in text else ""
                    self.program.notes.append("comment " + body)
                elif low.startswith("watch "):
                    self.program.monitors.append("watch " + text[6:])
                elif low.startswith("monitor "):
                    self.program.monitors.append("monitor " + text[8:])

    def _take_position(self, name: str | None) -> None:
        """Apply the next ``metadata <name> pos …;`` for this script."""
        if not name:
            return
        queued = self._positions.get(name)
        if queued:
            self._next_position = queued.pop(0)

    def _check_meta(self, node: A.Node, key: str, value, allowed: dict[str, str]) -> bool:
        """Reject a target metadata key the file format does not have.

        Unknown keys used to land in the dict and be dropped by every consumer,
        so ``shwo=0`` compiled into a visible sprite with no complaint.
        """
        kind = allowed.get(key)
        if kind is None:
            self.diag(
                node,
                "E0078",
                f"unknown metadata key {key!r}; known: {', '.join(sorted(allowed))}",
            )
            return False
        text = str(value)
        if kind == "number":
            try:
                float(text)
            except (TypeError, ValueError):
                self.diag(node, "E0079", f"metadata {key!r} takes a number, not {text!r}")
                return False
        elif kind == "integer":
            try:
                int(float(text))
            except (TypeError, ValueError):
                self.diag(node, "E0079", f"metadata {key!r} takes a whole number, not {text!r}")
                return False
        elif kind == "flag":
            if text.strip().lower() not in ("0", "1", "true", "false"):
                self.diag(node, "E0079", f"metadata {key!r} takes 0 or 1, not {text!r}")
                return False
        elif kind == "rotation":
            if text.strip().lower() not in _ROTATION_STYLES:
                self.diag(
                    node,
                    "E0079",
                    f"metadata {key!r} takes all, leftright or none, not {text!r}",
                )
                return False
        return True

    def _sprite_metadata(self, cls: A.ClassDecl) -> dict:
        """Merge default ← ``// Sprite:`` ← class parameters ← standalone lines."""
        values: dict = {
            "name": cls.name, "x": 0, "y": 0, "size": 100, "direction": 90, "show": 1,
        }
        if cls.sprite is not None:
            if "commentMetadata" in self.features:
                values.update(
                    {
                        "name": cls.sprite.name or cls.name,
                        "x": cls.sprite.x,
                        "y": cls.sprite.y,
                        "size": cls.sprite.size,
                        "direction": cls.sprite.direction,
                        "show": 1 if cls.sprite.visible else 0,
                        "namespace": cls.sprite.namespace,
                    }
                )
                for key in ("costume", "volume", "layer", "rotation", "draggable"):
                    value = getattr(cls.sprite, key, None)
                    if value is not None:
                        values[key] = value
            else:
                self.diag(
                    cls,
                    "W0015",
                    "'// Sprite:' comment ignored; add #enable_feature<commentMetadata> to enable it",
                )
        for key, expr in cls.params.items():
            value = _metadata_value(expr)
            if value is None:
                self.diag(expr, "E0073", f"metadata {key!r} needs a literal value")
                continue
            if not self._check_meta(expr, key, value, _SPRITE_META_KEYS):
                continue
            values[key] = value
        declarations = [d for d in self.program.sprite_meta if d.sprite == cls.name]
        if len(declarations) > 1:
            self.diag(
                declarations[-1],
                "W0020",
                f"sprite {cls.name!r} has {len(declarations)} metadata lines; the last one wins",
            )
        for declaration in declarations:
            for key, expr in declaration.values.items():
                value = _metadata_value(expr)
                if value is None:
                    self.diag(expr, "E0073", f"metadata {key!r} needs a literal value")
                    continue
                if not self._check_meta(expr, key, value, _SPRITE_META_KEYS):
                    continue
                values[key] = value
        return values

    def _emit_stage(self, decl: A.StageDecl) -> None:
        """Scripts that live on the Stage rather than on a sprite."""
        self.target = self.project.stage
        self.class_name = "Stage"
        self._apply_stage_meta(decl.annotation or "")
        self._apply_stage_declaration()
        # A file may hold nothing but the Stage; the context only needs *a*
        # class declaration to name the sprite for state lookup.
        stage_cls = self.program.classes[0] if self.program.classes else A.ClassDecl(
            line=decl.line, col=decl.col, name="Stage"
        )
        for member in decl.members:
            if isinstance(member, A.FuncDecl):
                self._emit_func(self.project.stage, stage_cls, member)
            elif isinstance(member, A.ImportDecl):
                self._import_asset(self.project.stage, member)
            elif isinstance(member, A.LooseDecl):
                self._emit_loose(member)
            elif isinstance(member, A.MetaDecl):
                continue  # collected by ``_collect_metadata``
            elif isinstance(member, A.Assign) and member.cloud and member.op == "=":
                # The stage block is the natural home for a cloud variable --
                # and the only declaration this block accepts, because every
                # other `public a = 0;` is a global the planner already owns.
                target = member.target
                name = ".".join(target.parts) if isinstance(target, A.Name) else ""
                if name:
                    self.project.stage.declare_variable(
                        name, self._initial_of(name), cloud=True
                    )
            else:
                self.diag(
                    member,
                    "W0010",
                    "only functions, imports and cloud variables are allowed in a stage block",
                )

    def _apply_stage_declaration(self) -> None:
        """``stage: costume=0, volume=100, tempo=120;`` — real syntax.

        The ``// Stage:`` comment does the same through a regex; the
        declaration is the spelling the decompiler writes now.
        """
        stage = self.project.stage
        for declaration in self.program.sprite_meta:
            if declaration.sprite != "stage":
                continue
            for key, expr in declaration.values.items():
                value = _metadata_value(expr)
                if value is None:
                    self.diag(expr, "E0073", f"metadata {key!r} needs a literal value")
                    continue
                if not self._check_meta(expr, key, value, _STAGE_META_KEYS):
                    continue
                self._set_stage_property(stage, key, value)

    @staticmethod
    def _set_stage_property(stage, key: str, value) -> None:
        if key == "costume":
            stage.current_costume = int(value)
        elif key == "volume":
            stage.volume = float(value)
        elif key == "layer":
            stage.layer_order = int(value)
        elif key == "tempo":
            stage.tempo = float(value)
        elif key in ("video", "videoState"):
            stage.video_state = str(value)
        elif key in ("videoTransparency", "video_transparency"):
            stage.video_transparency = float(value)

    def _apply_stage_meta(self, annotation: str) -> None:
        """``// Stage: costume=0 volume=100 tempo=120 …`` on the ``stage`` line."""
        stage = self.project.stage
        for key, value in re.findall(r"([A-Za-z]+)=([^\s]+)", annotation):
            if key == "costume":
                stage.current_costume = int(float(value))
            elif key == "volume":
                stage.volume = float(value)
            elif key == "layer":
                stage.layer_order = int(float(value))
            elif key == "tempo":
                stage.tempo = float(value)
            elif key == "video":
                stage.video_state = value
            elif key == "videoTransparency":
                stage.video_transparency = float(value)

    def _emit_loose(self, decl: A.LooseDecl) -> None:
        """``loose { … }`` — a stack of blocks that is not attached to a hat."""
        if self.vanilla:
            # A loose stack is on the canvas, not in the program: nothing can
            # run it. It is also where the leftovers from a TurboWarp round
            # trip end up, so a Scratch build drops them rather than shipping
            # blocks whose only effect is to make the upload fail.
            return
        assert self.target is not None
        placed = _AT_RE.search(decl.annotation or "")
        self._next_position = (
            (float(placed.group(1)), float(placed.group(2))) if placed else None
        )
        remaining: list[A.Stmt] = []
        for stmt in decl.body:
            if isinstance(stmt, A.ExprStmt) and self._is_reporter_expr(stmt.expr):
                value = self._eval(stmt.expr)
                if isinstance(value, Ref) and not value.block.shadow:
                    self._top_level(value.block)
                    self._next_position = None
                    continue
                if isinstance(value, ShadowRef):
                    # A dropdown dragged onto the canvas: a real (shadow) block.
                    self._top_level(value.block)
                    self._next_position = None
                    continue
            remaining.append(stmt)
        head = self._emit_stmts(remaining)
        if head is not None:
            self._top_level(head)
        self._next_position = None

    # -- asset imports -----------------------------------------------------
    def _import_asset(self, target: Target, decl: A.ImportDecl) -> None:
        path = Path(decl.path)
        if not path.is_absolute():
            path = self.base_dir / path
        if not path.exists():
            self.diag(decl, "E0058", f"asset {decl.path!r} not found (looked in {path})")
            return
        payload = path.read_bytes()
        extension = path.suffix.lower().lstrip(".")
        # ``as ""`` is a real costume name (projects have unnamed costumes),
        # so only a *missing* alias falls back to the file stem.
        name = decl.name if decl.name is not None else path.stem
        if extension in _STATE_FORMATS:
            # Big state imported inside a class belongs to that sprite; a
            # top-level import would put it on the stage instead.
            self._import_state(target, decl, payload, extension, name)
            return
        digest = hashlib.md5(payload).hexdigest()
        md5ext = f"{digest}.{extension}"
        self.project.assets[md5ext] = payload
        # ``// costume center=336,255 res=2`` / ``// sound rate=48000 samples=N``
        # carry the values Scratch stores, which cannot be derived from the
        # bytes alone (they decide where the costume is anchored).
        meta = decl.annotation or ""

        if extension in _IMAGE_FORMATS:
            width, height = _image_size(payload, extension)
            resolution = _asset_number(meta, "res", 1)
            target.costumes.append(
                {
                    "name": name,
                    "bitmapResolution": int(resolution) if resolution else 1,
                    "dataFormat": extension,
                    "assetId": digest,
                    "md5ext": md5ext,
                    "rotationCenterX": _asset_number(meta, "centerx", width / 2 / resolution),
                    "rotationCenterY": _asset_number(meta, "centery", height / 2 / resolution),
                }
            )
        elif extension in _SOUND_FORMATS:
            rate, samples = _wav_info(payload) if extension == "wav" else (48000, 0)
            target.sounds.append(
                {
                    "name": name,
                    "assetId": digest,
                    "dataFormat": extension,
                    "rate": _asset_number(meta, "rate", rate),
                    "sampleCount": _asset_number(meta, "samples", samples),
                    "md5ext": md5ext,
                }
            )
        else:
            self.diag(decl, "E0061", f"unsupported asset type .{extension}")

    def _import_state(self, target: Target, decl, payload: bytes, extension: str,
                      name: str) -> None:
        text = payload.decode("utf-8")
        if extension == "jsv":
            target.declare_variable(name, text.rstrip("\n"))
            return
        if extension == "jsa":
            try:
                items = json.loads(text)
            except json.JSONDecodeError as exc:
                self.diag(decl, "E0059", f"{decl.path!r}: invalid JSON ({exc.msg})")
                return
            if not isinstance(items, list):
                self.diag(decl, "E0060", f"{decl.path!r}: expected a JSON array")
                return
        else:
            items = text.splitlines()
        target.declare_list(name, items)

    # -- functions ---------------------------------------------------------
    def _arg_ids(self, cls_name: str, fn: A.FuncDecl) -> list[str]:
        key = (cls_name, fn.qualified)
        if key not in self._arg_id_cache:
            self._arg_id_cache[key] = [
                IdGen(f"arg:{cls_name}:{fn.qualified}:{i}").next("arg")
                for i in range(len(fn.params))
            ]
        return self._arg_id_cache[key]

    def _proccode(self, fn: A.FuncDecl) -> str:
        """Custom-block label.

        ``// proccode <text>`` wins when present: real projects often use
        human readable labels ("create note %s in tick %s"), which cannot be
        spelled as a function name.
        """
        match = _PROCCODE_RE.search(fn.annotation or "")
        if match is not None:
            text = match.group(1).strip()
            # ``%b`` and ``%n`` are parameters too. The decompiler writes the
            # original label back, and a Scratch project may declare a boolean
            # parameter; the language has one untyped parameter, so the
            # placeholder count is what has to line up.
            if len(re.findall(r"%[sbn]", text)) == len(fn.params):
                return text
            self.diag(
                fn,
                "E0056",
                f"proccode {text!r} does not match {len(fn.params)} parameter(s)",
            )
        # ``doGraph`` inside class graph becomes the custom block ``Graph.doGraph``
        base = self._qualify(fn.proccode) if len(fn.name) == 1 else fn.proccode
        return " ".join([base] + ["%s"] * len(fn.params))

    def _warp_flag(self, fn: A.FuncDecl) -> str:
        annotation = (fn.annotation or "").lower()
        if _NO_WARP_RE.search(annotation):
            # ``// no-warp`` beats ``--warp``: the command line cannot know
            # which function has to keep its frames.
            return "false"
        warp = self.warp or _WARP_RE.search(annotation) or "main thread" in annotation
        return "true" if warp else "false"

    def _emit_func(self, target: Target, cls: A.ClassDecl, fn: A.FuncDecl) -> None:
        self.class_name = cls.name
        self.scope = Scope(params=set(fn.params))
        annotation = fn.annotation or ""
        placed = _AT_RE.search(annotation)
        self._next_position = (
            (float(placed.group(1)), float(placed.group(2))) if placed else None
        )
        # ``metadata <name> pos x,y;`` names the script, so it wins over the
        # inline ``// at`` comment and needs no comment at all.
        self._take_position(_script_name(fn))
        hook = _HOOK_RE.search(annotation)

        if _UNDEFINED_RE.search(annotation):
            # The project calls this custom block but never defines it, so the
            # calls have to stay dangling exactly like the original.
            return

        broadcast_hook = _BROADCAST_HOOK_RE.search(annotation)
        if broadcast_hook is not None:
            name = broadcast_hook.group(1).strip()
            hat = self._new("event_whenbroadcastreceived")
            hat.set_field(
                "BROADCAST_OPTION", name, self.project.stage.declare_broadcast(name)
            )
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        save_hook = _SAVE_HOOK_RE.search(annotation)
        if save_hook is not None:
            if self.vanilla:
                # Scratch has no save event: the hat never fires, so neither
                # the hat nor the body it would have run can exist.
                return
            self.project.use_extension("lmsMoreEvents")
            opcode = (
                "lmsMoreEvents_beforeSave"
                if save_hook.group(1).lower() == "before"
                else "lmsMoreEvents_afterSave"
            )
            hat = self._new(opcode)
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        ext_event = _EXT_EVENT_HOOK_RE.search(annotation)
        if ext_event is not None:
            # An extension event (WebSocket.onMessage, WebSocket.onOpen, …) is a
            # hat with no inputs, so the method body hangs straight off it.
            from .extensions import resolve as _resolve_extension

            call = _resolve_extension(ext_event.group(1), ext_event.group(2))
            if call is not None and call.kind == "EVENT":
                self.project.use_extension(call.extension)
                # extension opcodes are namespaced: gsaWebsocket_onMessage, not onMessage
                hat = self._new(call.full_opcode)
                self._top_level(hat)
                self._attach_hat_body(hat, fn.body)
                return

        if _STAGE_CLICK_HOOK_RE.search(annotation):
            hat = self._new("event_whenstageclicked")
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        backdrop = _BACKDROP_HOOK_RE.search(annotation)
        if backdrop is not None:
            hat = self._new("event_whenbackdropswitchesto")
            hat.set_field("BACKDROP", backdrop.group(1).strip())
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        if _CLICK_HOOK_RE.search(annotation):
            hat = self._new("event_whenthisspriteclicked")
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        if _CLONE_HOOK_RE.search(annotation):
            hat = self._new("control_start_as_clone")
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        if (
            fn.is_init
            or fn.qualified == "greenFlag"
            or "green flag" in annotation.lower()
            or _FLAG_HOOK_RE.search(annotation)
        ):
            hat = self._new("event_whenflagclicked")
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        if fn.proccode in ("whenCloned", "whenClone"):
            hat = self._new("control_start_as_clone")
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        microbit = self._microbit_hat(annotation)
        if microbit is not None:
            opcode, field, menu_name, value = microbit
            self.project.use_extension("microbit")
            hat = self._new(f"microbit_{opcode}")
            menu = self._new(f"microbit_menu_{menu_name}")
            menu.shadow = True
            menu.parent = hat.id
            # A menu shadow stores its value under the *menu* name, exactly the
            # way the extension's own XML does (``fields: {buttons: ['A']}``).
            menu.set_field(menu_name, value)
            hat.set_input(field, ShadowRef(menu))
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        if hook is not None:
            hat = self._new("event_whenkeypressed")
            key = hook.group(1) if hook.group(1) is not None else hook.group(2)
            hat.set_field(
                "KEY_OPTION",
                self._key_value(Lit(O.TEXT, key.strip()), fn, text_is_name=True).value,
            )
            self._top_level(hat)
            self._attach_hat_body(hat, fn.body)
            return

        self._emit_procedure(fn)

        # A ``// receiver at`` annotation means the project had a
        # ``when I receive`` hat whose body was merged into this procedure, so
        # it needs the hat back even when the name is a single part.
        if (
            fn.visibility == "public"
            and not fn.params
            and (len(fn.name) > 1 or self.namespaced or _RECEIVER_AT_RE.search(annotation))
        ):
            self._emit_broadcast_receiver(target, fn)

    @staticmethod
    def _microbit_hat(annotation: str) -> tuple[str, str, str, str] | None:
        """``// hook: when [A] button pressed`` -> the hat and its menu value."""
        for regex, opcode in (
            (_MICROBIT_PIN_RE, "whenPinConnected"),
            (_MICROBIT_BUTTON_RE, "whenButtonPressed"),
            (_MICROBIT_TILT_RE, "whenTilted"),
            (_MICROBIT_GESTURE_RE, "whenGesture"),
        ):
            match = regex.search(annotation)
            if match is None:
                continue
            field, menu = _MICROBIT_MENUS[opcode]
            return opcode, field, menu, match.group("value").strip()
        return None

    def _attach_hat_body(self, hat: Block, body: list[A.Stmt]) -> None:
        head = self._emit_stmts(body)
        if head is not None:
            head.parent = hat.id
            hat.next = head.id

    def _emit_procedure(self, fn: A.FuncDecl) -> None:
        assert self.target is not None
        definition = self._new("procedures_definition")
        self._top_level(definition)
        proto = self._new("procedures_prototype")
        proto.shadow = True
        proto.parent = definition.id
        arg_ids = self._arg_ids(self.class_name, fn)
        label = self._proccode(fn)
        kinds = re.findall(r"%[sbn]", label)
        proto.mutation = {
            "tagName": "mutation",
            "children": [],
            "proccode": label,
            "argumentids": json.dumps(arg_ids),
            "argumentnames": json.dumps(list(fn.params)),
            # A boolean parameter's default is the word ``false``; a text one
            # is empty. scratch-blocks checks the slot against the placeholder.
            "argumentdefaults": json.dumps(
                ["false" if kind == "%b" else "" for kind in kinds]
            ),
            "warp": self._warp_flag(fn),
        }
        for index, (arg_id, name) in enumerate(zip(arg_ids, fn.params)):
            boolean = index < len(kinds) and kinds[index] == "%b"
            reporter = self._new(
                "argument_reporter_boolean" if boolean
                else "argument_reporter_string_number"
            )
            reporter.shadow = True
            reporter.parent = proto.id
            reporter.set_field("VALUE", name)
            proto.inputs[arg_id] = [1, reporter.id]
        definition.inputs["custom_block"] = [1, proto.id]

        head = self._emit_stmts(fn.body)
        if head is not None:
            head.parent = definition.id
            definition.next = head.id

    def _emit_broadcast_receiver(self, target: Target, fn: A.FuncDecl) -> None:
        receiver = _RECEIVER_AT_RE.search(fn.annotation or "")
        message = receiver.group(3) if receiver is not None else None
        if message:
            name = message
        elif len(fn.name) > 1:
            name = fn.qualified
        else:
            name = self._qualify(fn.proccode)
        broadcast_id = self.project.stage.declare_broadcast(name)
        hat = self._new("event_whenbroadcastreceived")
        hat.set_field("BROADCAST_OPTION", name, broadcast_id)
        if receiver is not None and self._next_position is None:
            self._next_position = (float(receiver.group(1)), float(receiver.group(2)))
        self._top_level(hat)
        call = self._new("procedures_call")
        call.mutation = {
            "tagName": "mutation",
            "children": [],
            "proccode": self._proccode(fn),
            "argumentids": json.dumps(self._arg_ids(self.class_name, fn)),
            "warp": self._warp_flag(fn),
        }
        call.parent = hat.id
        hat.next = call.id

    # -- statements --------------------------------------------------------
    def _emit_stmts(self, stmts: list[A.Stmt]) -> Block | None:
        blocks: list[Block] = []
        index = 0
        while index < len(stmts):
            fused = self._fuse_setxy(stmts[index:index + 2])
            if fused is not None:
                blocks.extend(fused)
                index += 2
                continue
            blocks.extend(self._emit_stmt(stmts[index]))
            index += 1
        self._link(blocks)
        return blocks[0] if blocks else None

    def _fuse_setxy(self, pair: list[A.Stmt]) -> list[Block] | None:
        """``Motion.x = a; Motion.y = b;`` → one ``motion_gotoxy``.

        The VM's ``setX``/``setY`` both go through ``setXY``, so the pair clamps
        to the stage exactly like the combined block, and the reference project
        writes the pair as ``gotoxy``.
        """
        if len(pair) != 2:
            return None
        first, second = pair
        if not (isinstance(first, A.Assign) and isinstance(second, A.Assign)):
            return None
        if first.op != "=" or second.op != "=":
            return None
        if not (isinstance(first.target, A.Name) and isinstance(second.target, A.Name)):
            return None
        if first.target.parts != ("Motion", "x") or second.target.parts != ("Motion", "y"):
            return None
        if self.vanilla and (
            _mentions_ternary(first.value) or _mentions_ternary(second.value)
        ):
            # A ternary needs statements hoisted before it in vanilla mode, and
            # this pair becomes a single block that has no room for them.
            return None
        block = self._new("motion_gotoxy")
        block.set_input("X", self._as_input(self._eval(first.value), O.MATH_NUM))
        block.set_input("Y", self._as_input(self._eval(second.value), O.MATH_NUM))
        return [block]

    _ternary_serial = 0

    def _prescan_ternaries(self, stmt: A.Stmt) -> list[Block]:
        """Compute every ``a if c else b`` in this statement into a variable.

        Scratch's conditional is a statement, so a conditional in an argument
        cannot stay where it is. Each one is turned into
        ``set _t = b; if c { set _t = a }`` *before* the statement runs, and the
        expression itself becomes a read of ``_t``.

        Both branches are therefore evaluated, which is exactly what the
        `utilities` reporter did -- it was handed three already-evaluated
        arguments -- so a port from that build behaves the same way.
        """
        self._ternary_vars = {}
        # Two passes: every name first, then the blocks. A ternary nested in
        # another one's branch is evaluated while that branch is being emitted,
        # so it has to already have a variable or it reads as unhoistable.
        for node in iter_nodes(stmt):
            if isinstance(node, A.Ternary):
                type(self)._ternary_serial += 1
                self._ternary_vars[id(node)] = f"_t{type(self)._ternary_serial}"

        pre: list[Block] = []
        for node in iter_nodes(stmt):
            if not isinstance(node, A.Ternary):
                continue
            name = self._ternary_vars[id(node)]
            target = A.Name(line=node.line, col=node.col, parts=(name,))
            pre.extend(self._emit_assign(A.Assign(
                line=node.line, col=node.col, target=target, op="=",
                value=node.otherwise)))
            branch = self._new("control_if")
            branch.set_input("CONDITION", self._as_bool(self._eval(node.cond)))
            body = self._emit_assign(A.Assign(
                line=node.line, col=node.col, target=target, op="=",
                value=node.then))
            self._link(body)
            if body:
                body[0].parent = branch.id
                branch.set_input("SUBSTACK", Ref(body[0]))
            pre.append(branch)
        return pre

    def _prescan_asks(self, stmt: A.Stmt) -> list[Block]:
        """Hoist an `ask and wait` that is being used as a value.

        ``Sensing.ask(question)`` is a *statement* in Scratch -- it yields until
        the answer arrives -- and the answer is a separate reporter. Written as
        a value, the two are one thing: ``name = Sensing.ask("Who?")`` asks
        first and the value is the answer, which is what every other language
        with an input block does.

        The answer is copied into a temporary immediately, so a statement with
        two questions in it does not have the first read back the second's
        answer. A bare ``Sensing.ask("...");`` line is left to the statement
        path, which emits the same block with no temporary at all.
        """
        self._ask_vars = {}
        pre: list[Block] = []
        for node in iter_nodes(stmt):
            if not isinstance(node, A.Call):
                continue
            if isinstance(stmt, A.ExprStmt) and stmt.expr is node:
                continue                      # it is a statement: nothing to hoist
            receiver = node.receiver
            if not isinstance(receiver, A.Name) or node.name != "ask":
                continue
            if ".".join(self._stage_stripped(receiver.parts)) != "Sensing":
                continue
            type(self)._ask_serial += 1
            name = f"_ask{type(self)._ask_serial}"
            self._ask_vars[id(node)] = name
            block = self._new("sensing_askandwait")
            block.set_input(
                "QUESTION",
                self._as_input(self._eval(node.args[0] if node.args else None), O.TEXT),
            )
            pre.append(block)
            target = A.Name(line=node.line, col=node.col, parts=(name,))
            pre.extend(self._emit_assign(A.Assign(
                line=node.line, col=node.col, target=target, op="=",
                value=A.Name(line=node.line, col=node.col,
                             parts=("Sensing", "answer")))))
        return pre

    def _flush_pending(self, blocks: list[Block]) -> list[Block]:
        return blocks

    def _emit_stmt(self, stmt: A.Stmt) -> list[Block]:
        # An ask used as a value is hoisted on both targets: the block exists
        # in Scratch itself, so nothing here is TurboWarp-only.
        asks = self._prescan_asks(stmt)
        if not self.vanilla:
            if not asks:
                return self._emit_stmt_inner(stmt)
            try:
                body = self._emit_stmt_inner(stmt)
            finally:
                self._ask_vars = {}
            return self._link(asks + body)
        pre = asks + self._prescan_ternaries(stmt)
        try:
            body = self._emit_stmt_inner(stmt)
        finally:
            self._ternary_vars = {}
            self._ask_vars = {}
        if not pre:
            return body
        return self._link(pre + body)

    def _emit_stmt_inner(self, stmt: A.Stmt) -> list[Block]:
        if isinstance(stmt, A.Assign):
            return self._emit_assign(stmt)
        if isinstance(stmt, A.ExprStmt):
            return self._emit_expr_stmt(stmt)
        if isinstance(stmt, A.SubstackStmt):
            return self._emit_substack_stmt(stmt)
        if isinstance(stmt, A.IfStmt):
            return self._emit_if(stmt)
        if isinstance(stmt, A.WhileStmt):
            return self._emit_while(stmt)
        if isinstance(stmt, A.RepeatStmt):
            return self._emit_repeat(stmt)
        if isinstance(stmt, A.ForStmt):
            return self._emit_for(stmt)
        if isinstance(stmt, A.RangeFor):
            return self._emit_range_for(stmt)
        if isinstance(stmt, A.SwitchStmt):
            return self._emit_switch(stmt)
        if isinstance(stmt, A.ReturnStmt):
            return [self._stop("this script")]
        if isinstance(stmt, A.BreakStmt):
            return [self._stop("this script")]
        if isinstance(stmt, A.ContinueStmt):
            self.diag(stmt, "W0009", "'continue' has no Scratch equivalent; ignored")
            return []
        if isinstance(stmt, A.CallStmt):
            blocks = self._command_call(stmt.expr, self.scope, fire_and_forget=True)
            if not blocks:
                self.diag(stmt, "E0055", "call target could not be lowered")
            return blocks
        if isinstance(stmt, A.FuncDecl):
            self.diag(stmt, "E0054", "nested function declarations are not supported")
            return []
        self.diag(stmt, "E0053", f"unsupported statement {type(stmt).__name__}")
        return []

    def _stop(self, option: str) -> Block:
        block = self._new("control_stop")
        block.set_field("STOP_OPTION", option)
        block.mutation = {
            "tagName": "mutation",
            "children": [],
            "hasnext": "true" if option == "other" else "false",
        }
        return block

    def _emit_expr_stmt(self, stmt: A.ExprStmt) -> list[Block]:
        blocks = self._emit_expr_stmt_inner(stmt)
        return self._flush_pending(blocks)

    def _emit_expr_stmt_inner(self, stmt: A.ExprStmt) -> list[Block]:
        expr = stmt.expr
        if isinstance(expr, A.Name) and len(expr.parts) == 1:
            # ``breakpoint;`` — a debugger marker, no arguments.
            console = self._emit_console_stmt(expr.parts[0], [])
            if console is not None:
                return console
        if isinstance(expr, A.Call):
            return self._command_call(expr, self.scope)
        # Bare literals such as the ``0 // do nothing`` idiom are no-ops.
        if isinstance(expr, (A.NumberLit, A.StringLit, A.BoolLit, A.NullLit)):
            return []
        self.diag(stmt, "W0013", "expression statement has no effect")
        return []

    def _emit_substack_stmt(self, stmt: A.SubstackStmt) -> list[Block]:
        """``call(...) { … }`` — a command that carries a C-block body."""
        expr = stmt.expr
        if not isinstance(expr, A.Call):
            self.diag(stmt, "E0050", "only a call can own a block body")
            return []
        if self.vanilla and self._is_comment_call(expr):
            # A comment block with a body is the code underneath a note. The
            # note is for a reader and the code is not, so the body is kept
            # and the label goes.
            return self._emit_body(stmt.body)
        if self.vanilla and self._is_foreach_thread_var(expr):
            return self._emit_foreach_thread_var(expr, stmt.body)
        blocks = self._command_call(expr, self.scope)
        if len(blocks) != 1:
            self.diag(stmt, "E0050", f"{expr.name} does not take a block body")
            return blocks
        self._attach_substack(blocks[0], "SUBSTACK", stmt.body)
        return blocks

    @staticmethod
    def _is_comment_call(expr: A.Call) -> bool:
        receiver = expr.receiver
        return isinstance(receiver, A.Name) and receiver.parts == ("Comments",)

    @staticmethod
    def _is_foreach_thread_var(expr: A.Call) -> bool:
        receiver = expr.receiver
        return (isinstance(receiver, A.Name) and receiver.parts == ("Temp",)
                and expr.name == "forEachThreadVariable")

    def _emit_foreach_thread_var(self, call: A.Call, body: list[A.Stmt]) -> list[Block]:
        """``Temp.forEachThreadVariable("_i", n) { … }`` as a counting loop.

        The extension binds the variable to 1..n and runs the body each time.
        With sprite-locals that is `set 0; repeat n { change by 1; body }`,
        which visits the same values in the same order.
        """
        if not call.args:
            self.diag(call, "E0044", "forEachThreadVariable needs a variable and a count")
            return []
        name_arg = call.args[0]
        if not isinstance(name_arg, A.StringLit):
            self.diag(call, "E0044",
                      "forEachThreadVariable needs a literal variable name")
            return []
        parts = tuple(name_arg.value.split("."))
        count = call.args[1] if len(call.args) > 1 else A.NumberLit(
            line=call.line, col=call.col, raw="0")
        target = A.Name(line=call.line, col=call.col, parts=parts)
        zero = A.NumberLit(line=call.line, col=call.col, raw="0")
        one = A.NumberLit(line=call.line, col=call.col, raw="1")
        reset = A.Assign(line=call.line, col=call.col, target=target, op="=", value=zero)
        step = A.Assign(line=call.line, col=call.col, target=target, op="+=", value=one)

        loop = self._new("control_repeat")
        loop.set_input("TIMES", self._as_input(self._eval(count), O.WHOLE_NUM))
        inner: list[Block] = self._emit_assign(step)
        inner.extend(self._emit_body(body))
        self._link(inner)
        if inner:
            inner[0].parent = loop.id
            loop.set_input("SUBSTACK", Ref(inner[0]))
        return self._link(self._emit_assign(reset) + [loop])

    def _emit_body(self, body: list[A.Stmt]) -> list[Block]:
        """Emit statements as a bare run of blocks, with nothing wrapping them."""
        out: list[Block] = []
        for child in body:
            out.extend(self._emit_stmt(child))
        return self._link(out)

    # -- vanilla lowerings for the two extensions a Scratch build cannot ship --
    #: Where a Scratch build keeps the console, and the two lists that stand in
    #: for named timers. A name in one list and its start value in the other,
    #: at the same index, is a dictionary with the keys in the order they were
    #: first used.
    CONSOLE_LIST = "Console"
    TIMER_NAMES = "TimerNames"
    TIMER_STARTS = "TimerStarts"

    def _shared_list(self, name: str) -> str:
        """A Stage list, because a console is one console for the project.

        A bare list name declared through ``_declare_list`` belongs to the
        sprite that first mentions it, which would give each sprite its own
        console and its own timers -- the opposite of what a named timer is.
        """
        return self.project.stage.declare_list(name, [])

    def _string_is_true(self, text: object) -> Block:
        """``x = "true"`` or ``x = "1"``: what ``stringToBoolean`` decides."""
        first = self._new("operator_equals")
        first.set_input("OPERAND1", self._as_input(text, O.TEXT))
        first.set_input("OPERAND2", Lit(O.TEXT, "true"))
        second = self._new("operator_equals")
        second.set_input("OPERAND1", self._as_input(text, O.TEXT))
        second.set_input("OPERAND2", Lit(O.TEXT, "1"))
        block = self._new("operator_or")
        block.set_input("OPERAND1", Ref(first))
        block.set_input("OPERAND2", Ref(second))
        return block

    def _console_add(self, text: object) -> Block:
        """``console.log(x)`` -> ``add x to (Console)``."""
        block = self._new("data_addtolist")
        block.set_field("LIST", self.CONSOLE_LIST, self._shared_list(self.CONSOLE_LIST))
        block.set_input("ITEM", self._as_input(text, O.TEXT))
        return block

    def _timer_start(self, name: object) -> list[Block]:
        """``Timer.startTimer(n)`` -> remember ``timer`` against the name.

        Two lists and no dictionary: the name is appended the first time and
        its start value is replaced every time after, so reading a timer is one
        ``indexOf`` and one ``item``.
        """
        names = self._shared_list(self.TIMER_NAMES)
        starts = self._shared_list(self.TIMER_STARTS)
        contains = self._new("data_listcontainsitem")
        contains.set_field("LIST", self.TIMER_NAMES, names)
        contains.set_input("ITEM", self._as_input(name, O.TEXT))

        not_there = self._new("operator_not")
        not_there.set_input("OPERAND", Ref(contains))

        known = self._new("control_if_else")
        known.set_input("CONDITION", Ref(not_there))
        add_name = self._new("data_addtolist")
        add_name.set_field("LIST", self.TIMER_NAMES, names)
        add_name.set_input("ITEM", self._as_input(name, O.TEXT))
        add_start = self._new("data_addtolist")
        add_start.set_field("LIST", self.TIMER_STARTS, starts)
        add_start.set_input("ITEM", Ref(self._new("sensing_timer")))
        self._link([add_name, add_start])
        add_name.parent = known.id
        known.set_input("SUBSTACK", Ref(add_name))

        index = self._new("data_itemnumoflist")
        index.set_field("LIST", self.TIMER_NAMES, names)
        index.set_input("ITEM", self._as_input(name, O.TEXT))
        replace = self._new("data_replaceitemoflist")
        replace.set_field("LIST", self.TIMER_STARTS, starts)
        replace.set_input("INDEX", Ref(index))
        replace.set_input("ITEM", Ref(self._new("sensing_timer")))
        replace.parent = known.id
        known.set_input("SUBSTACK2", Ref(replace))
        return [known]

    def _timer_read(self, name: object) -> Ref:
        """``Timer.getTimer(n)`` -> ``timer - item (indexOf n) of starts``.

        A name nobody started reads as an index of 0, and Scratch answers 0 for
        item 0 -- so an unstarted timer measures from the project start, which
        is what the extension does too.
        """
        names = self._shared_list(self.TIMER_NAMES)
        starts = self._shared_list(self.TIMER_STARTS)
        index = self._new("data_itemnumoflist")
        index.set_field("LIST", self.TIMER_NAMES, names)
        index.set_input("ITEM", self._as_input(name, O.TEXT))
        started = self._new("data_itemoflist")
        started.set_field("LIST", self.TIMER_STARTS, starts)
        started.set_input("INDEX", Ref(index))
        block = self._new("operator_subtract")
        block.set_input("NUM1", Ref(self._new("sensing_timer")))
        block.set_input("NUM2", Ref(started))
        return Ref(block)

    def _vanilla_console(self, call: A.Call) -> list[Block] | None:
        """The console's five writing blocks, as list writes."""
        if call.args:
            return [self._console_add(self._eval(call.args[-1]))]
        return [self._console_add(Lit(O.TEXT, ""))]

    def _emit_console_stmt(self, name: str, args: list[A.Expr]) -> list[Block] | None:
        """``log(x)`` / ``breakpoint;`` -> the TurboWarp Consoles extension."""
        spec = _CONSOLE_STATEMENTS.get(name)
        if spec is None:
            return None
        if self.vanilla:
            # `log`/`warn`/`error` write a line to the console list; the log
            # stopwatch and the breakpoint have nothing to act on without one.
            if name in ("log", "warn", "error"):
                if not args:
                    self.diag(self.program, "E0044", f"{name} needs one argument")
                    return []
                return [self._console_add(self._eval(args[0]))]
            return []
        opcode, slot = spec
        if slot is None:
            if args:
                self.diag(args[0], "E0044", f"{name} takes no argument")
        elif not args:
            self.diag(self.program, "E0044", f"{name} needs one argument")
            return []
        self.project.use_extension("sipcconsole")
        block = self._new(opcode)
        if slot is not None:
            block.set_input(slot, self._as_input(self._eval(args[0]), O.TEXT))
        else:
            block.set_input("string", Lit(O.TEXT, "@breakpoint"))
        return [block]

    def _emit_assign(self, stmt: A.Assign) -> list[Block]:
        blocks = self._emit_assign_inner(stmt)
        return self._flush_pending(blocks)

    def _emit_assign_inner(self, stmt: A.Assign) -> list[Block]:
        if isinstance(stmt.target, A.Index):
            return self._store_index(stmt)
        target = stmt.target
        assert target is not None
        if target.parts and target.parts[0] == "Stage":
            target = A.Name(
                line=target.line, col=target.col, parts=self._stage_stripped(target.parts)
            )
            stmt.target = target
        if stmt.op is None:
            if not target.parts[-1].startswith("_"):
                self.diag(
                    stmt,
                    "E0052",
                    f"{target} has no initial value; only temp (_name) declarations allow this",
                )
            return []
        value = stmt.value
        if isinstance(value, A.ListLit):
            return self._declare_list_literal(
                target.parts, value, public=stmt.visibility == "public"
            )
        if len(target.parts) == 1:
            name = target.parts[0]
            if name in self.scope.params:
                raise CompileError(
                    f"cannot assign to parameter {name!r}",
                    stmt.line,
                    stmt.col,
                    code="E0042",
                )
            return self._store_local(name, stmt.op, value)
        full = ".".join(target.parts)
        if full in O.PROPERTIES:
            return self._store_property(full, stmt.op, value)
        if full in self.list_names:
            raise CompileError(
                f"{full} is a list and cannot be assigned", stmt.line, stmt.col, code="E0043"
            )
        return self._store_global(full, stmt.op, value)

    def _emit_if(self, stmt: A.IfStmt) -> list[Block]:
        pairs: list[tuple[A.Expr, list[A.Stmt]]] = [(stmt.cond, stmt.body)]
        pairs.extend(stmt.elifs)
        return self._flush_pending([self._if_chain(pairs, stmt.orelse)])

    def _if_chain(
        self, pairs: list[tuple[A.Expr, list[A.Stmt]]], orelse: list[A.Stmt] | None
    ) -> Block:
        # Build the chain iteratively.  The VM runtime can have hundreds of
        # ``elif`` arms, which would otherwise hit Python's recursion limit.
        built: list[Block] = []
        for index, (cond, body) in enumerate(pairs):
            has_else = index + 1 < len(pairs) or bool(orelse)
            block = self._new("control_if_else" if has_else else "control_if")
            block.set_input("CONDITION", self._as_bool(self._eval(cond)))
            self._attach_substack(block, "SUBSTACK", body)
            built.append(block)
        if orelse:
            self._attach_substack(built[-1], "SUBSTACK2", orelse)
        for outer, inner in zip(reversed(built[:-1]), reversed(built[1:])):
            inner.parent = outer.id
            outer.set_input("SUBSTACK2", Ref(inner))
        return built[0]

    def _emit_while(self, stmt: A.WhileStmt) -> list[Block]:
        self._warn_loop_break(stmt.body)
        cond = stmt.cond
        if isinstance(cond, A.NumberLit) and _is_truthy_literal(cond.raw):
            block = self._new("control_forever")
            self._attach_substack(block, "SUBSTACK", stmt.body)
            return [block]
        if isinstance(cond, A.Unary) and cond.op in ("!", "not"):
            # ``while (!c)`` is how the decompiler spells ``repeat until (c)``;
            # negating again would add two blocks per loop.
            block = self._new("control_repeat_until")
            block.set_input("CONDITION", self._as_bool(self._eval(cond.operand)))
        else:
            if self.vanilla:
                # `repeat until <not cond>` is `while cond` exactly: the one
                # tests the same expression and leaves on the same answer.
                # `control_while` is a TurboWarp block and Scratch rejects the
                # whole project for containing one.
                block = self._new("control_repeat_until")
                block.set_input("CONDITION",
                                self._as_bool(self._eval(cond), negate=True))
            else:
                block = self._new("control_while")
                block.set_input("CONDITION", self._as_bool(self._eval(cond)))
        self._attach_substack(block, "SUBSTACK", stmt.body)
        return [block]

    def _emit_repeat(self, stmt: A.RepeatStmt) -> list[Block]:
        self._warn_loop_break(stmt.body)
        block = self._new("control_repeat")
        block.set_input("TIMES", self._as_input(self._eval(stmt.count), O.WHOLE_NUM))
        self._attach_substack(block, "SUBSTACK", stmt.body)
        return [block]

    def _emit_for(self, stmt: A.ForStmt) -> list[Block]:
        self._warn_loop_break(stmt.body)
        loop_var, start, stop, inclusive = _match_for_pattern(stmt)
        if loop_var is not None and start is not None and stop is not None:
            if _is_one(start) and inclusive and not self.vanilla:
                block = self._new("control_for_each")
                block.set_input("VALUE", self._as_input(self._eval(stop), O.WHOLE_NUM))
                self._set_variable_field(block, "VARIABLE", loop_var)
                self._attach_substack(block, "SUBSTACK", stmt.body)
                return [block]
            blocks: list[Block] = self._emit_stmt(stmt.init) if stmt.init else []
            repeat = self._new("control_repeat_until")
            condition = A.Binary(
                line=stmt.line,
                col=stmt.col,
                op=">" if inclusive else ">=",
                left=A.Name(line=stmt.line, col=stmt.col, parts=loop_var),
                right=stop,
            )
            repeat.set_input("CONDITION", self._as_bool(self._eval(condition)))
            body_blocks: list[Block] = []
            for child in stmt.body:
                body_blocks.extend(self._emit_stmt(child))
            increment = self._new("data_changevariableby")
            self._set_variable_field(increment, "VARIABLE", loop_var)
            increment.set_input("VALUE", Lit(O.MATH_NUM, "1"))
            body_blocks.append(increment)
            self._link(body_blocks)
            body_blocks[0].parent = repeat.id
            repeat.set_input("SUBSTACK", Ref(body_blocks[0]))
            blocks.append(repeat)
            return self._link(blocks)

        blocks = []
        if stmt.init is not None:
            blocks.extend(self._emit_stmt(stmt.init))
        if stmt.cond is None:
            block = self._new("control_forever")
        else:
            block = self._new("control_repeat_until")
            block.set_input("CONDITION", self._as_bool(self._eval(stmt.cond), negate=True))
        body_blocks: list[Block] = []
        for child in stmt.body:
            body_blocks.extend(self._emit_stmt(child))
        if stmt.update is not None:
            body_blocks.extend(self._emit_stmt(stmt.update))
        self._link(body_blocks)
        if body_blocks:
            body_blocks[0].parent = block.id
            block.set_input("SUBSTACK", Ref(body_blocks[0]))
        blocks.append(block)
        return self._link(blocks)

    def _emit_range_for(self, stmt: A.RangeFor) -> list[Block]:
        """``for i in a..b`` -> ``set i = a; repeat until i > b { …; i += 1 }``.

        ``repeat n`` is used when the range starts at a literal 1 and the
        counter is a real variable — that is exactly Scratch's own counter
        block, and it keeps existing projects byte-identical on a round trip.
        A thread (temp) counter cannot be bound by ``repeat n``, so it always
        takes the explicit form.
        """
        self._warn_loop_break(stmt.body)
        name = ".".join(stmt.var)
        temp = self._is_temp_name(name)
        active = temp and name.startswith("_")
        if active and name in self._active_loops:
            self.diag(
                stmt,
                "E0074",
                f"temp loop variable {name!r} is already live in an enclosing loop; "
                "the inner loop would overwrite the outer counter",
            )
        self._warn_if_counter_reassigned(stmt, name)
        target = A.Name(line=stmt.line, col=stmt.col, parts=stmt.var)
        init = A.Assign(
            line=stmt.line, col=stmt.col, target=target, op="=", value=stmt.start
        )
        one = A.NumberLit(line=stmt.line, col=stmt.col, raw="1")
        increment = A.Assign(
            line=stmt.line, col=stmt.col, target=target, op="+=", value=one
        )

        if active:
            self._active_loops.append(name)
        try:
            if _is_one(stmt.start) and not temp and not self.vanilla:
                block = self._new("control_for_each")
                block.set_input("VALUE", self._as_input(self._eval(stmt.stop), O.WHOLE_NUM))
                self._set_variable_field(block, "VARIABLE", stmt.var)
                self._attach_substack(block, "SUBSTACK", stmt.body)
                return [block]

            blocks: list[Block] = self._emit_assign(init)
            loop = self._new("control_repeat_until")
            condition = A.Binary(
                line=stmt.line,
                col=stmt.col,
                op=">",
                left=A.Name(line=stmt.line, col=stmt.col, parts=stmt.var),
                right=stmt.stop,
            )
            loop.set_input("CONDITION", self._as_bool(self._eval(condition)))
            body_blocks: list[Block] = []
            for child in stmt.body:
                body_blocks.extend(self._emit_stmt(child))
            body_blocks.extend(self._emit_assign(increment))
            self._link(body_blocks)
            if body_blocks:
                body_blocks[0].parent = loop.id
                loop.set_input("SUBSTACK", Ref(body_blocks[0]))
            blocks.append(loop)
            return self._link(blocks)
        finally:
            if active:
                self._active_loops.pop()

    def _is_temp_name(self, name: str) -> bool:
        definition = self._class_definition(name)
        if definition is not None and definition.is_temp:
            return True
        return self._is_thread_local(name)

    def _warn_if_counter_reassigned(self, stmt: A.RangeFor, name: str) -> None:
        """A write to the counter inside the body breaks the count."""
        for node, value in _writes_to(stmt.body, stmt.var):
            self.diag(
                node,
                "W0021",
                f"the loop counter {name!r} is assigned inside its own loop"
                + (f" ({value})" if value else "")
                + "; the next comparison uses the new value",
            )

    def _warn_loop_break(self, body: list[A.Stmt]) -> None:
        """``break`` is ``stop this script``: inside a loop it leaves the function."""
        for node in _break_statements(body):
            self.diag(
                node,
                "W0025",
                "'break' stops the whole script, not just this loop; there is no "
                "loop break in Scratch — keep a flag in the loop condition instead",
            )

    def _last_of(self, blocks: list[Block]) -> Block:
        assert self.target is not None
        node = blocks[-1]
        while node.next is not None:
            node = self.target.blocks[node.next]
        return node

    def _emit_switch(self, stmt: A.SwitchStmt) -> list[Block]:
        # No fall-through: a case body runs only for its own label, so a
        # trailing ``break`` is redundant (it is accepted and dropped).
        pairs: list[tuple[A.Expr, list[A.Stmt]]] = []
        for case in stmt.cases:
            body = list(case.body)
            if body and isinstance(body[-1], A.BreakStmt):
                body = body[:-1]
            condition = A.Binary(
                line=case.line, col=stmt.col, op="==", left=stmt.value, right=case.value
            )
            pairs.append((condition, body))
        if not pairs:
            return self._emit_stmts(stmt.default or [])
        return [self._if_chain(pairs, stmt.default)]

    # -- variable storage --------------------------------------------------
    def _set_variable_field(self, block: Block, field: str, parts: tuple[str, ...]) -> None:
        full = ".".join(parts)
        if len(parts) == 1:
            # Loop counters must be real variables: Scratch's for-each block
            # cannot bind a thread-local.
            global_counter = self.plan is not None and ".".join(parts) in self.plan.globals
            if global_counter:
                full = self._qualify_var(full)
            if global_counter:
                var_id = self.project.stage.declare_variable(full, self._initial_of(full))
            else:
                assert self.target is not None
                var_id = self.target.declare_variable(full, self._initial_of(full))
        else:
            var_id = self.project.stage.declare_variable(full, self._initial_of(full))
        block.set_field(field, full, var_id)

    def _store_global(self, name: str, op: str, value: A.Expr | None) -> list[Block]:
        name = self._qualify_var(name)
        var_id = self.project.stage.declare_variable(name, self._initial_of(name))
        assert self.target is not None
        if op == "=":
            block = self._new("data_setvariableto")
            block.set_input("VALUE", self._as_input(self._eval(value), O.TEXT))
        elif op == "+=":
            block = self._new("data_changevariableby")
            block.set_input("VALUE", self._as_input(self._eval(value), O.MATH_NUM))
        elif op == "-=":
            block = self._new("data_changevariableby")
            negated = _negate(value, self)
            block.set_input("VALUE", self._as_input(negated, O.MATH_NUM))
        else:
            return self._compound_store(name, op, value)
        block.set_field("VARIABLE", name, var_id)
        return [block]

    def _store_local(self, name: str, op: str, value: A.Expr | None) -> list[Block]:
        if self.plan is not None and name in self.plan.globals:
            # ``public value = 1`` inside a function declares a Stage variable.
            return self._store_global(name, op, value)
        definition = self._class_definition(name)
        if definition is not None and definition.is_temp:
            return self._store_temp(name, op, value)
        if self._is_thread_local(name):
            return self._store_temp(name, op, value)

        assert self.target is not None
        var_id = self.target.declare_variable(name, self._initial_of(name))
        if op == "=":
            block = self._new("data_setvariableto")
            block.set_input("VALUE", self._as_input(self._eval(value), O.TEXT))
        elif op == "+=":
            block = self._new("data_changevariableby")
            block.set_input("VALUE", self._as_input(self._eval(value), O.MATH_NUM))
        elif op == "-=":
            block = self._new("data_changevariableby")
            block.set_input("VALUE", self._as_input(_negate(value, self), O.MATH_NUM))
        else:
            return self._compound_local(name, op, value, temp=False)
        block.set_field("VARIABLE", name, var_id)
        return [block]

    # -- the state namespaces ---------------------------------------------
    #: ``Variables.set(x, v)`` and ``Lists.add(x, v)`` name their target the way
    #: ``showVariable`` does: a bare or quoted name, never a value.
    @staticmethod
    def _alias_name(call: A.Call, index: int = 0) -> str | None:
        if len(call.args) <= index:
            return None
        arg = call.args[index]
        if isinstance(arg, A.StringLit):
            return arg.value or None
        if isinstance(arg, A.Name):
            return ".".join(arg.parts)
        return None

    def _state_alias(self, full: str, call: A.Call) -> list[Block] | None:
        """``Variables.set`` / ``Lists.add`` / ``Clone.create``/``delete``."""
        if full == "Variables":
            method = STATE_ALIASES["Variables"].get(call.name, call.name)
            name = self._alias_name(call)
            if name is None:
                return None
            if method in ("set", "change"):
                value = call.args[1] if len(call.args) > 1 else None
                op = "=" if method == "set" else "+="
                return self._store_bare(name, op, value)
            if method in ("showVariable", "hideVariable"):
                return self._variable_visibility(call, full, show=method == "showVariable")
            return None
        if full == "Lists":
            method = STATE_ALIASES["Lists"].get(call.name, call.name)
            name = self._alias_name(call)
            if name is None:
                return None
            if method in ("showList", "hideList"):
                return self._list_visibility(call, show=method == "showList")
            method = _LIST_ALIAS_COMMANDS.get(method)
            if method is None:
                return None
            opcode, params = O.LIST_COMMANDS[method]
            block = self._new(opcode)
            block.set_field("LIST", name, self._declare_list(name))
            prims = {"INDEX": O.INTEGER, "ITEM": O.TEXT}
            for param, arg in zip(params, call.args[1:]):
                block.set_input(param, self._as_input(self._eval(arg), prims[param]))
            return [block]
        if full == "Clone":
            if call.name == "create":
                return [self._clone_of(call)]
            if call.name == "delete":
                return [self._new("control_delete_this_clone")]
            return None
        if full == "Stage" and call.name in ("set", "change"):
            # ``Stage.x = …`` is the dotted form; this is the call spelling.
            name = self._alias_name(call)
            if name is None:
                return None
            value = call.args[1] if len(call.args) > 1 else None
            return self._store_global(name, "=" if call.name == "set" else "+=", value)
        return None

    def _state_alias_query(self, full: str, call: A.Call) -> object:
        """``Variables.get(x)`` / ``Lists.get(x, i)`` and friends."""
        if full == "Variables" and call.name == "get":
            name = self._alias_name(call)
            return self._var_read(tuple(name.split("."))) if name else None
        if full == "Lists":
            name = self._alias_name(call)
            if name is None or call.name not in O.LIST_REPORTERS:
                return None
            args = call.args[1:]
            needed = _LIST_REPORTER_ARITY[call.name]
            if len(args) < needed:
                self.diag(
                    call,
                    "E0044",
                    f"Lists.{call.name} takes {needed} argument(s) after the list name",
                )
                return Lit(O.TEXT, "")
            return self._list_reporter(name, O.LIST_REPORTERS[call.name], args)
        if full == "Stage" and call.name == "get":
            name = self._alias_name(call)
            return self._var_read(tuple(name.split("."))) if name else None
        return None

    def _store_bare(self, name: str, op: str, value: A.Expr | None) -> list[Block]:
        """Store through a name written as text: exactly what ``name = v`` does."""
        if "." in name:
            return self._store_global(name, op, value)
        return self._store_local(name, op, value)

    def _clone_of(self, call: A.Call) -> Block:
        target = None
        if call.args:
            arg = call.args[0]
            if isinstance(arg, A.StringLit):
                target = arg.value
            elif isinstance(arg, A.Name):
                target = ".".join(arg.parts)
        block = self._new("control_create_clone_of")
        shadow = self._new("control_create_clone_of_menu")
        shadow.shadow = True
        shadow.parent = block.id
        shadow.set_field("CLONE_OPTION", target or "_myself_")
        block.set_input("CLONE_OPTION", Ref(shadow))
        return block

    # -- runtime variables (``Temp.setRuntimeVariable``) -------------------
    @staticmethod
    def _runtime_var_name(call: A.Call) -> str | None:
        """The literal variable name, or ``None`` when it is computed."""
        if not call.args:
            return None
        first = call.args[0]
        if isinstance(first, A.StringLit):
            return first.value
        if isinstance(first, A.Name) and len(first.parts) == 1 and first.parts[0].startswith("_"):
            # ``Temp.setRuntimeVariable(_x, …)`` is a typo for the literal, but
            # reading it as a name would silently invent a variable.
            return None
        return None

    def _is_runtime_get(self, full: str, call: A.Call) -> bool:
        return (
            self.convert_runtime_vars
            and full == "Temp"
            and call.name == "getRuntimeVariable"
            and self._runtime_var_name(call) is not None
        )

    def _is_runtime_store(self, full: str, call: A.Call) -> bool:
        return (
            self.convert_runtime_vars
            and full == "Temp"
            and call.name in ("setRuntimeVariable", "changeRuntimeVariable")
            and self._runtime_var_name(call) is not None
        )

    def _runtime_var_read(self, call: A.Call) -> Ref:
        name = self._runtime_var_name(call) or ""
        self.runtime_vars.add(name)
        block = self._new("data_variable")
        block.set_field(
            "VARIABLE", name, self.project.stage.declare_variable(name, 0)
        )
        return Ref(block)

    def _runtime_var_store(self, call: A.Call) -> Block:
        name = self._runtime_var_name(call) or ""
        self.runtime_vars.add(name)
        self.project.stage.declare_variable(name, 0)
        change = call.name == "changeRuntimeVariable"
        block = self._new("data_changevariableby" if change else "data_setvariableto")
        payload = call.args[1] if len(call.args) > 1 else None
        block.set_input(
            "VALUE",
            self._as_input(self._eval(payload), O.MATH_NUM if change else O.TEXT),
        )
        block.set_field("VARIABLE", name, self.project.stage.declare_variable(name, 0))
        return block

    def _is_thread_local(self, name: str) -> bool:
        return self.locals_mode == "thread" or (
            self.thread_locals and name.startswith("_")
        )

    def _store_temp(self, name: str, op: str, value: A.Expr | None) -> list[Block]:
        """Thread-local variable (``useTempVariables``): freed when the thread ends."""
        self.project.use_extension(TEMP_VARS_EXTENSION)
        opcode = {
            "=": "lmsTempVars2_setThreadVariable",
            "+=": "lmsTempVars2_changeThreadVariable",
            "-=": "lmsTempVars2_changeThreadVariable",
        }.get(op)
        if opcode is None:
            return self._compound_local(name, op, value, temp=True)
        block = self._new(opcode)
        block.set_input("VAR", Lit(O.TEXT, name))
        # ``changeThreadVariable`` reads its amount from ``NUM``; only
        # ``setThreadVariable`` uses ``STRING``.
        slot = "STRING" if op == "=" else "NUM"
        if op == "+=":
            block.set_input(slot, self._as_input(self._eval(value), O.MATH_NUM))
        elif op == "-=":
            block.set_input(slot, self._as_input(_negate(value, self), O.MATH_NUM))
        else:
            block.set_input(slot, self._as_input(self._eval(value), O.TEXT))
        return [block]

    def _compound_local(
        self, name: str, op: str, value: A.Expr | None, temp: bool
    ) -> list[Block]:
        binary = A.Binary(
            line=value.line if value else 0,
            col=value.col if value else 0,
            op={"/=": "/", "*=": "*", "%=": "%"}[op],
            left=A.Name(line=value.line if value else 0, col=value.col if value else 0,
                        parts=(name,)),
            right=value,
        )
        if temp:
            self.project.use_extension(TEMP_VARS_EXTENSION)
            block = self._new("lmsTempVars2_setThreadVariable")
            block.set_input("VAR", Lit(O.TEXT, name))
            block.set_input("STRING", self._as_input(self._eval(binary), O.TEXT))
            return [block]
        assert self.target is not None
        var_id = self.target.declare_variable(name, self._initial_of(name))
        block = self._new("data_setvariableto")
        block.set_field("VARIABLE", name, var_id)
        block.set_input("VALUE", self._as_input(self._eval(binary), O.TEXT))
        return [block]

    def _qualify(self, name: str) -> str:
        """``doGraph`` inside class ``graph`` → ``Graph.doGraph``.

        Only active with ``#enable_feature<namespacedNames>`` and only for
        *global* things (public variables/lists and broadcasts); sprite-local
        names stay as written.
        """
        if not self.namespaced or "." in name or not name:
            return name
        return f"{self.namespace}.{name}"

    def _is_global_name(self, name: str) -> bool:
        if self.plan is None:
            return "." in name
        return "." in name or name in self.plan.globals

    def _qualify_var(self, name: str) -> str:
        """Namespace a variable only when a class declared it ``public``.

        A bare ``public i = 0`` at file scope is a plain Stage variable; giving
        it the sprite prefix would fork it into ``Lyrics.i`` and leave every
        read pointing at a second, empty variable.
        """
        if not self.namespaced or "." in name or not name:
            return name
        if self.plan is None:
            return f"{self.namespace}.{name}"
        owner = self.plan.namespaced.get(name)
        if owner is None:
            return name
        return f"{owner}.{name}"

    def _qualify_global(self, name: str) -> str:
        return self._qualify(name) if self._is_global_name(name) else name

    def _initial_of(self, name: str):
        if self.plan is None:
            return 0
        definition = self.plan.globals.get(name)
        if definition is None:
            definition = self.plan.class_vars.get(self.class_name, {}).get(name)
        return definition.initial if definition is not None else 0

    def _compound_store(self, name: str, op: str, value: A.Expr | None) -> list[Block]:
        binary = A.Binary(
            line=value.line if value else 0,
            col=value.col if value else 0,
            op={"/=": "/", "*=": "*", "%=": "%"}[op],
            left=A.Name(line=value.line if value else 0, col=value.col if value else 0,
                        parts=tuple(name.split("."))),
            right=value,
        )
        block = self._new("data_setvariableto")
        owner = self._state_owner(name)
        block.set_field("VARIABLE", name, owner.declare_variable(name, self._initial_of(name)))
        block.set_input("VALUE", self._as_input(self._eval(binary), O.TEXT))
        return [block]

    def _store_property(self, name: str, op: str, value: A.Expr | None) -> list[Block]:
        prop = O.PROPERTIES[name]
        self.project.use_extension(prop.extension)
        if op == "=":
            block = self._new(prop.set)
            block.set_input(prop.set_input, self._as_input(self._eval(value), prop.prim))
            return [block]
        if op == "+=":
            block = self._new(prop.change)
            block.set_input(prop.change_slot, self._as_input(self._eval(value), prop.prim))
            return [block]
        if op == "-=":
            block = self._new(prop.change)
            block.set_input(prop.change_slot, self._as_input(_negate(value, self), prop.prim))
            return [block]
        binary = A.Binary(
            line=value.line if value else 0,
            col=value.col if value else 0,
            op={"/=": "/", "*=": "*", "%=": "%"}[op],
            left=A.Name(line=value.line if value else 0, col=value.col if value else 0,
                        parts=tuple(name.split("."))),
            right=value,
        )
        block = self._new(prop.set)
        block.set_input(prop.set_input, self._as_input(self._eval(binary), prop.prim))
        return [block]

    def _stage_stripped(self, parts: tuple[str, ...]) -> tuple[str, ...]:
        """``Stage.Lyrics.zh`` -> ``Lyrics.zh`` when that state exists.

        The monitor annotations name a variable the same way
        (``metadata list Stage.Lyrics.zh``), so code can spell it the same. The
        prefix is only dropped when the remainder is real state, which keeps a
        variable literally called ``Stage.x`` round-tripping unchanged.
        """
        if len(parts) < 2 or parts[0] != "Stage":
            return parts
        rest = parts[1:]
        name = ".".join(rest)
        known = self.plan.globals if self.plan is not None else {}
        if name in known or name in self.list_names:
            return rest
        return parts

    def _is_declared_name(self, parts: tuple[str, ...]) -> bool:
        """True when the source itself declares this dotted name.

        ``WebSocket.closingCode`` is the extension's reporter; a project that
        also declares a variable of that name means the variable.
        """
        name = ".".join(parts)
        if self.plan is not None and name in self.plan.globals:
            return True
        if self.target is not None:
            for target in self.project.targets:
                if name in target.variables:
                    return True
        return False

    def _var_read(self, parts: tuple[str, ...]) -> Ref:
        parts = self._stage_stripped(parts)
        name = ".".join(parts)
        if len(parts) == 1:
            if name in self.scope.params:
                block = self._new("argument_reporter_string_number")
                block.set_field("VALUE", name)
                return Ref(block)
            definition = self._class_definition(name)
            if definition is not None and definition.is_temp:
                return self._temp_read(name)
            if definition is not None:
                assert self.target is not None
                block = self._new("data_variable")
                block.set_field("VARIABLE", name, self.target.declare_variable(name, self._initial_of(name)))
                return Ref(block)
            if name in (self.plan.globals if self.plan else {}):
                name = self._qualify_var(name)
                block = self._new("data_variable")
                block.set_field("VARIABLE", name, self.project.stage.declare_variable(name, self._initial_of(name)))
                return Ref(block)
            if self._is_thread_local(name):
                return self._temp_read(name)
            if self.plan is not None and (
                name in self.plan.class_lists.get(self.class_name, {})
                or name in self.plan.lists
            ):
                # ``Operators.length(myList)``: a list read as a value is its
                # contents reporter, not a (new, empty) variable. Variables win
                # when both share a name; the decompiler renames such lists.
                return self._list_reporter(name, "data_listcontents")
            assert self.target is not None
            block = self._new("data_variable")
            block.set_field("VARIABLE", name, self.target.declare_variable(name, self._initial_of(name)))
            return Ref(block)
        if name in O.PROPERTIES:
            prop = O.PROPERTIES[name]
            self.project.use_extension(prop.extension)
            return Ref(self._new(prop.get))
        if name in self.list_names:
            return self._list_reporter(name, "data_listcontents")
        block = self._new("data_variable")
        block.set_field("VARIABLE", name, self.project.stage.declare_variable(name, self._initial_of(name)))
        return Ref(block)

    def _class_definition(self, name: str):
        if self.plan is None:
            return None
        return self.plan.class_vars.get(self.class_name, {}).get(name)

    def _temp_read(self, name: str) -> Ref:
        self.project.use_extension(TEMP_VARS_EXTENSION)
        block = self._new("lmsTempVars2_getThreadVariable")
        block.set_input("VAR", Lit(O.TEXT, name))
        return Ref(block)

    def _list_operand(self, node: A.Expr | None, stmt: A.Node) -> str | None:
        """The list a ``receiver[i]`` names, or ``None`` with a diagnostic."""
        if isinstance(node, A.Name):
            name = ".".join(self._stage_stripped(node.parts))
            if name in self.list_names or (self.plan is not None and name in self.plan.lists):
                return name
        self.diag(
            stmt,
            "E0043",
            "index syntax needs a list: write list[i], or list.get(i) for a name "
            "only known at run time",
        )
        return None

    def _eval_index(self, expr: A.Index) -> object:
        """``list[i]`` — item *i*, counting from 1 like the ``item of list`` block."""
        name = self._list_operand(expr.receiver, expr)
        if name is None:
            return Lit(O.TEXT, "")
        block = self._new("data_itemoflist")
        block.set_field("LIST", name, self._declare_list(name))
        block.set_input("INDEX", self._as_input(self._eval(expr.index), O.INTEGER))
        return Ref(block)

    def _store_index(self, stmt: A.Assign) -> list[Block]:
        """``list[i] = value`` — replace the item, like ``item of list`` + ``replace``."""
        target = stmt.target
        assert isinstance(target, A.Index)
        name = self._list_operand(target.receiver, stmt)
        if name is None:
            return []
        if stmt.op is None:
            self.diag(stmt, "E0044", "index assignment needs a value")
            return []
        value = stmt.value
        if stmt.op != "=":
            value = A.Binary(
                line=stmt.line,
                col=stmt.col,
                op={"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%"}[stmt.op],
                left=target,
                right=value,
            )
        block = self._new("data_replaceitemoflist")
        block.set_field("LIST", name, self._declare_list(name))
        block.set_input("INDEX", self._as_input(self._eval(target.index), O.INTEGER))
        block.set_input("ITEM", self._as_input(self._eval(value), O.TEXT))
        return [block]

    def _list_reporter(self, name: str, opcode: str, args: list[A.Expr] | None = None) -> Ref:
        assert self.target is not None
        if opcode == "data_listcontents":
            block = self._new("data_listcontents")
            block.set_field("LIST", name, self._declare_list(name))
            return Ref(block)
        block = self._new(opcode)
        block.set_field("LIST", name, self._declare_list(name))
        args = args or []
        if opcode == "data_itemoflist":
            block.set_input("INDEX", self._as_input(self._eval(args[0]), O.INTEGER))
        elif opcode in ("data_listcontainsitem", "data_itemnumoflist"):
            block.set_input("ITEM", self._as_input(self._eval(args[0]), O.TEXT))
        return Ref(block)

    # -- expressions -------------------------------------------------------
    #: How many call-argument lists enclose the expression being evaluated.
    #: ``any`` only means the key menu inside one of them.
    _arg_depth = 0
    #: Ternary node id -> the variable its value was computed into. Filled by
    #: `_prescan_ternaries` for one statement at a time.
    _ternary_vars: dict[int, str] = {}
    #: `Sensing.ask` call id -> the variable its answer was copied into, for the
    #: same statement. See `_prescan_asks`.
    _ask_vars: dict[int, str] = {}
    _ask_serial = 0

    def _eval(self, expr: A.Expr | None) -> object:
        if expr is None:
            return Lit(O.TEXT, "")
        if isinstance(expr, A.NumberLit):
            return Lit(O.MATH_NUM, expr.raw)
        if isinstance(expr, A.StringLit):
            return Lit(O.TEXT, expr.value)
        if isinstance(expr, A.BoolLit):
            return Lit(O.TEXT, "1" if expr.value else "0")
        if isinstance(expr, A.NullLit):
            return Lit(O.TEXT, "")
        if isinstance(expr, A.AnyLit):
            # ``any`` is the key-menu option: always the text ``any``.
            if self._arg_depth == 0:
                self.diag(
                    expr,
                    "W0006",
                    "'any' is a menu option (the \"any key\" choice) and always "
                    "becomes the text \"any\"; outside an argument list, write "
                    "\"any\" in quotes",
                )
            return Lit(O.TEXT, "any")
        if isinstance(expr, A.Name):
            if (
                len(expr.parts) == 2
                and expr.parts[0] == "microbit"
                and expr.parts[1] in MICROBIT_CONSTANTS
            ):
                return Lit(O.TEXT, MICROBIT_CONSTANTS[expr.parts[1]])
            # A reporter with no arguments is a *property*: `Sensing.answer`,
            # `WebSocket.closingCode`, `Pointerlock.isPointerLocked`. A real
            # variable of that name still wins, so declaring one keeps working.
            if len(expr.parts) == 2 and not self._is_declared_name(expr.parts):
                extension = resolve(expr.parts[0], expr.parts[1], 0, self.features)
                if extension is not None and extension.is_reporter:
                    return self._extension_reporter(extension, None)
                # ...and the core reporters too: `Sensing.timer`,
                # `Sensing.answer`, `Sensing.mouseX` take no arguments either.
                spec = O.REPORTERS.get(".".join(expr.parts))
                if spec is not None and not spec.params:
                    return self._reporter(spec, [])
            return self._var_read(expr.parts)
        if isinstance(expr, A.Unary):
            return self._eval_unary(expr)
        if isinstance(expr, A.Binary):
            return self._eval_binary(expr)
        if isinstance(expr, A.Ternary):
            if self.vanilla:
                name = self._ternary_vars.get(id(expr))
                if name is not None:
                    return self._var_read((name,))
                self.diag(expr, "E0053",
                          "a conditional here could not be hoisted; it needs to "
                          "be part of a statement")
            self.project.use_extension("utilities")
            block = self._new("utilities_ternaryOperator")
            block.set_input("A", self._as_bool(self._eval(expr.cond)))
            block.set_input("B", self._as_input(self._eval(expr.then), O.TEXT))
            block.set_input("C", self._as_input(self._eval(expr.otherwise), O.TEXT))
            return Ref(block)
        if isinstance(expr, A.FString):
            return self._eval_fstring(expr)
        if isinstance(expr, A.ListLit):
            self.diag(expr, "E0054", "list literals are only supported as variable declarations")
            return Lit(O.TEXT, "")
        if isinstance(expr, A.Call):
            return self._eval_call(expr)
        if isinstance(expr, A.Member):
            self.diag(expr, "E0054", "property access on an expression is not supported")
            return Lit(O.TEXT, "")
        if isinstance(expr, A.Index):
            return self._eval_index(expr)
        self.diag(expr, "E0053", f"unsupported expression {type(expr).__name__}")
        return Lit(O.TEXT, "")

    def _eval_unary(self, expr: A.Unary) -> object:
        operand = expr.operand
        if expr.op in ("!", "not"):
            block = self._new("operator_not")
            block.set_input("OPERAND", self._as_bool(self._eval(operand)))
            return Ref(block)
        if expr.op == "-":
            if isinstance(operand, A.NumberLit):
                return Lit(O.MATH_NUM, _format_number(-float(operand.raw)))
            block = self._new("operator_subtract")
            block.set_input("NUM1", Lit(O.MATH_NUM, "0"))
            block.set_input("NUM2", self._as_input(self._eval(operand), O.MATH_NUM))
            return Ref(block)
        return self._eval(operand)

    def _eval_binary(self, expr: A.Binary) -> object:
        op = expr.op
        if op in ("&&", "and", "||", "or"):
            block = self._new("operator_and" if op in ("&&", "and") else "operator_or")
            block.set_input("OPERAND1", self._as_bool(self._eval(expr.left)))
            block.set_input("OPERAND2", self._as_bool(self._eval(expr.right)))
            return Ref(block)
        if op == "!=":
            equals = self._new("operator_equals")
            equals.set_input("OPERAND1", self._as_input(self._eval(expr.left), O.TEXT))
            equals.set_input("OPERAND2", self._as_input(self._eval(expr.right), O.TEXT))
            negate = self._new("operator_not")
            negate.set_input("OPERAND", Ref(equals))
            return Ref(negate)
        if op in ("<=", ">="):
            inner = ">" if op == "<=" else "<"
            compare = self._new(_COMPARE_OPS[inner])
            compare.set_input("OPERAND1", self._as_input(self._eval(expr.left), O.TEXT))
            compare.set_input("OPERAND2", self._as_input(self._eval(expr.right), O.TEXT))
            negate = self._new("operator_not")
            negate.set_input("OPERAND", Ref(compare))
            return Ref(negate)
        if op in _COMPARE_OPS:
            block = self._new(_COMPARE_OPS[op])
            block.set_input("OPERAND1", self._as_input(self._eval(expr.left), O.TEXT))
            block.set_input("OPERAND2", self._as_input(self._eval(expr.right), O.TEXT))
            return Ref(block)
        arith = {
            "+": "operator_add",
            "-": "operator_subtract",
            "*": "operator_multiply",
            "/": "operator_divide",
            "%": "operator_mod",
        }
        if op in arith:
            block = self._new(arith[op])
            block.set_input("NUM1", self._as_input(self._eval(expr.left), O.MATH_NUM))
            block.set_input("NUM2", self._as_input(self._eval(expr.right), O.MATH_NUM))
            return Ref(block)
        if op == "**":
            if self.vanilla:
                # Scratch has no power block, but it has `[10 ^]` and `[log]`:
                # a^b is 10^(b * log10 a). The two round trips cost a bit or so
                # of the mantissa, which matters for a comparison and does not
                # for the tempo arithmetic this is used for.
                log_of = self._new("operator_mathop")
                log_of.set_field("OPERATOR", "log")
                log_of.set_input("NUM", self._as_input(self._eval(expr.left), O.MATH_NUM))
                scaled = self._new("operator_multiply")
                scaled.set_input("NUM1", self._as_input(self._eval(expr.right), O.MATH_NUM))
                scaled.set_input("NUM2", Ref(log_of))
                block = self._new("operator_mathop")
                block.set_field("OPERATOR", "10 ^")
                block.set_input("NUM", Ref(scaled))
                return Ref(block)
            self.project.use_extension("utilities")
            block = self._new("utilities_exponent")
            block.set_input("A", self._as_input(self._eval(expr.left), O.MATH_NUM))
            block.set_input("B", self._as_input(self._eval(expr.right), O.MATH_NUM))
            return Ref(block)
        self.diag(expr, "E0053", f"unsupported operator {op!r}")
        return Lit(O.TEXT, "")

    # -- calls -------------------------------------------------------------
    @_inside_arguments
    def _eval_call(self, call: A.Call) -> object:
        hoisted = self._ask_vars.get(id(call))
        if hoisted is not None:
            return self._var_read((hoisted,))
        receiver = call.receiver
        if self.vanilla and self._is_comment_call(call):
            # `Comments.commentReporter("why", x)` and its boolean twin are a
            # label on a value: the value is what the program means.
            if call.args:
                return self._eval(call.args[-1])
            return Lit(O.TEXT, "")
        if self.vanilla and isinstance(receiver, A.Name):
            if receiver.parts == ("Timer",) and call.name == "getTimer":
                # A named timer is two lists and an arithmetic block.
                return self._timer_read(self._eval(call.args[0]) if call.args
                                        else Lit(O.TEXT, ""))
            if receiver.parts == ("WebSocket",):
                # A Scratch project has no socket, so nothing is ever connected
                # and no message ever arrives. Reporting "not connected" is the
                # honest answer; the code that guards on it then does nothing.
                return Lit(O.MATH_NUM, "0")
            if receiver.parts == ("Util",) and call.name == "stringToBoolean":
                # "true" or "1", and Scratch's `=` folds case for strings and
                # compares numerically when both sides look like numbers.
                return Ref(self._string_is_true(
                    self._eval(call.args[0]) if call.args else Lit(O.TEXT, "")))
            if receiver.parts == ("Runtime",):
                # What a browser can be asked about its own player. Scratch's
                # answer is fixed: a 480x360 stage, 30 frames a second, 300
                # clones, and no turbo switch a project may read.
                fixed = {"turboMode": "0", "isTurboMode": "0",
                         "framerateLimit": "30", "cloneLimit": "300",
                         "stageWidth": "480", "stageHeight": "360",
                         "compilerEnabled": "0"}
                if call.name in fixed:
                    return Lit(O.MATH_NUM, fixed[call.name])
                return Lit(O.MATH_NUM, "0")
            if receiver.parts in (("console",), ("Console",)) and call.name != "logTimer":
                # `console.debug(x)` is a reporter by kind but a write by
                # meaning; a Scratch build has no console, so it is a list.
                return Ref(self._console_add(self._eval(call.args[-1]) if call.args
                                             else Lit(O.TEXT, "")))
        if receiver is None:
            if call.name == "join" and len(call.args) >= 2:
                # ``join(a, b, c)`` is a balanced chain of binary joins, so a
                # long concatenation nests log2 deep instead of one level per
                # extra operand.
                return self._join_tree([self._eval(arg) for arg in call.args])
            if call.name == "string":
                return self._join_string(call.args[0] if call.args else None)
            if call.name == "menu":
                return self._menu_shadow(call)
            if call.name == "block":
                return self._raw_block(call)
            if call.name in ("isTurboWarp", "isCompiled"):
                return self._turbowarp_flag(call)
            if call.name == "round":
                return self._round_block(call.args[0] if call.args else None, call)
            mathop = _MATH_BUILTINS.get(call.name)
            if mathop is not None:
                block = self._new("operator_mathop")
                block.set_field("OPERATOR", mathop)
                block.set_input(
                    "NUM", self._as_input(self._eval(call.args[0] if call.args else None), O.MATH_NUM)
                )
                return Ref(block)
            if call.name == "float":
                return self._as_input(self._eval(call.args[0] if call.args else None), O.MATH_NUM)
            if call.name in ("len", "length", "lengthOf") and call.args:
                # ``length of []``: the operand is turned into text first, so
                # ``lengthOf(123)`` is 3 and ``lengthOf(myList)`` counts the
                # joined list contents, exactly like the block.
                block = self._new("operator_length")
                block.set_input("STRING", self._as_input(self._eval(call.args[0]), O.TEXT))
                return Ref(block)
            fn = self.class_funcs.get(self.class_name, {}).get(call.name)
            if fn is not None:
                self.diag(call, "E0051", "custom blocks cannot return values in Scratch")
                return Lit(O.TEXT, "")
            self.diag(call, "E0036", f"unknown function {call.name!r}")
            return Lit(O.TEXT, "")

        if not isinstance(receiver, A.Name):
            self.diag(call, "E0054", "method calls on expression results are not supported yet")
            return Lit(O.TEXT, "")

        full = ".".join(self._stage_stripped(receiver.parts))
        qname = f"{full}.{call.name}"
        if qname == "Operators.math" and call.args:
            operator = call.args[0]
            name = operator.value if isinstance(operator, A.StringLit) else None
            if name == "round":
                return self._round_block(call.args[1] if len(call.args) > 1 else None, call)
            if name is not None and name not in _MATHOP_MENU:
                self.diag(operator, "W0026", f"the math menu has no {name!r}")
        if qname in O.REPORTERS:
            return self._reporter(O.REPORTERS[qname], call.args)
        if full in self.list_names and call.name in O.LIST_REPORTERS:
            return self._list_reporter(full, O.LIST_REPORTERS[call.name], call.args)
        if qname in O.PROPERTIES:
            return self._var_read(tuple(qname.split(".")))
        if self._is_runtime_get(full, call):
            return self._runtime_var_read(call)
        query = self._state_alias_query(full, call)
        if query is not None:
            return query
        extension = resolve(full, call.name, len(call.args), self.features)
        if extension is not None and extension.is_reporter:
            return self._extension_reporter(extension, call)
        if extension is not None:
            self.diag(call, "E0039", f"{qname} is a statement, not a value")
            return Lit(O.TEXT, "")
        self.diag(call, "E0037", f"unknown reporter {qname!r}")
        return Lit(O.TEXT, "")

    def _round_block(self, arg: A.Node | None, call: A.Node) -> Ref:
        """``round(x)`` / ``Operators.math("round", x)`` → ``[round] of (x)``.

        Scratch has no ``round`` in the ``[ ... ] of ( )`` menu; the menu block
        silently answers 0 for a name it does not know, so lowering to
        ``operator_mathop`` would be a bug that only shows up as arithmetic
        that never rounds.
        """
        block = self._new("operator_round")
        block.set_input("NUM", self._as_input(self._eval(arg), O.MATH_NUM))
        return Ref(block)

    def _turbowarp_flag(self, call: A.Call) -> object:
        """``isTurboWarp()`` / ``isCompiled()``.

        A project that uses TurboWarp extensions cannot load in vanilla
        Scratch, so both are 1 unless the user declares a variable with the
        same name to take over.
        """
        name = call.name
        definition = None
        if self.plan is not None:
            definition = self.plan.globals.get(name) or self.plan.class_vars.get(
                self.class_name, {}
            ).get(name)
        if definition is not None:
            return self._var_read((name,))
        return Lit(O.MATH_NUM, "1")

    def _eval_fstring(self, expr: A.FString):
        """``f"a{x}b"`` → a balanced chain of ``operator_join`` blocks."""
        pieces: list[object] = []
        for part in expr.parts:
            if isinstance(part, str):
                if part:
                    pieces.append(Lit(O.TEXT, part))
            else:
                pieces.append(self._eval(part))
        if not pieces:
            return Lit(O.TEXT, "")
        return self._join_tree(pieces)

    def _join_tree(self, pieces: list[object]) -> object:
        """Fold join operands pairwise, so the nesting depth stays log2.

        ``join(1,2,3,4,5,6,7,8)`` becomes
        ``join(join(join(1,2),join(3,4)), join(join(5,6),join(7,8)))``; an odd
        operand count carries the leftover to the next level instead of growing
        a chain.
        """
        level = list(pieces)
        while len(level) > 1:
            folded: list[object] = []
            for index in range(0, len(level) - 1, 2):
                block = self._new("operator_join")
                block.set_input("STRING1", self._as_input(level[index], O.TEXT))
                block.set_input("STRING2", self._as_input(level[index + 1], O.TEXT))
                folded.append(Ref(block))
            if len(level) % 2:
                folded.append(level[-1])
            level = folded
        return level[0]

    def _menu_shadow(self, call: A.Call) -> object:
        """``menu("looks_costume", "COSTUME", "true")`` — a bare dropdown block."""
        if len(call.args) != 3 or not isinstance(call.args[0], A.StringLit):
            self.diag(call, "E0049", "menu() takes a block opcode, a field name and a value")
            return Lit(O.TEXT, "")
        if not isinstance(call.args[1], A.StringLit):
            self.diag(call.args[1], "E0057", "menu() needs the field name as a string")
            return Lit(O.TEXT, "")
        if not isinstance(call.args[2], _STATIC_LITERALS):
            # A dropdown value is a label the VM looks up by text; feeding it a
            # runtime value is exactly the eval this warns about.
            self._check_eval(call.args[2], "a menu() field value")
            self.diag(call.args[2], "E0045", "menu() needs a literal field value")
            return Lit(O.TEXT, "")
        opcode, field, value = (self._eval(arg) for arg in call.args)
        if not isinstance(field, Lit) or not field.value:
            self.diag(call, "E0057", "menu() needs the field name as a string")
            return Lit(O.TEXT, "")
        block = self._new(opcode.value)
        block.shadow = True
        block.set_field(field.value, value.value)
        return ShadowRef(block)

    def _raw_block(self, call: A.Call) -> object:
        """``block("argument_reporter_boolean", "VALUE", "is compiled?")``.

        Some projects contain reporters the language has no name for (a call to
        a parameter the procedure never declares, for example).  ``block()``
        keeps the block instead of flattening it to a literal.
        """
        if not call.args:
            self.diag(call, "E0049", "block() takes an opcode")
            return Lit(O.TEXT, "")
        opcode = self._eval(call.args[0])
        if len(call.args) == 1:
            # ``block("operator_join")`` — the block with its default inputs.
            return Ref(self._new(opcode.value))
        field = self._eval(call.args[1])
        value = self._eval(call.args[2]) if len(call.args) > 2 else Lit(O.TEXT, "")
        if not isinstance(field, Lit) or not field.value:
            self.diag(call, "E0057", "block() needs the field name as a string")
            return Lit(O.TEXT, "")
        block = self._new(opcode.value)
        block.set_field(field.value, value.value)
        return Ref(block)

    def _join_string(self, arg: A.Expr | None) -> Ref:
        block = self._new("operator_join")
        block.set_input("STRING1", self._as_input(self._eval(arg), O.TEXT))
        block.set_input("STRING2", Lit(O.TEXT, ""))
        return Ref(block)

    def _reporter(self, spec: O.Reporter, args: list[A.Expr]) -> Ref:
        self.project.use_extension(spec.extension)
        block = self._new(spec.opcode)
        for name, value in spec.fields:
            block.set_field(name, value)
        for param, arg in zip(spec.params, args):
            if param.prim == O.FIELD:
                value = self._eval(arg)
                block.set_field(param.name, value.value if isinstance(value, Lit) else "")
            elif param.menu:
                block.set_input(
                    param.name, self._menu_input(param, self._eval(arg), block, arg)
                )
            else:
                block.set_input(param.name, self._as_input(self._eval(arg), param.prim))
        if len(args) > len(spec.params):
            self.diag(
                args[len(spec.params)],
                "E0044",
                f"{spec.opcode} takes {len(spec.params)} argument(s)",
            )
        return Ref(block)

    def _check_eval(self, node: A.Node | None, what: str) -> None:
        """Warn when a name is only known at runtime (Scratch "eval")."""
        if node is None:
            return
        if isinstance(node, _STATIC_LITERALS):
            return
        self.diag(
            node,
            "W0004",
            f"eval: {what} is computed at runtime; the VM looks the name up "
            "when the block runs, so a typo or a rename fails silently",
        )

    def _key_value(self, value, arg: A.Expr | None, *, text_is_name: bool = False):
        """Resolve a key argument to the name the VM compares against.

        ``Sensing.isKeyPressed(65)`` writes a *number*, which is a code point
        (see ``jswc.keycodes``); ``isKeyPressed("1")`` writes a string, which is
        the key itself — the digit one, not code point 1. ``text_is_name``
        forces the string reading for sources that have no number syntax (the
        ``// hook: when [0] key pressed`` comment).

        Codes and names the VM cannot report warn instead of compiling to a
        test that can never fire.
        """
        if not isinstance(value, Lit) or not isinstance(value.value, str):
            return value
        text = value.value.strip()
        if not text:
            return value
        if text_is_name:
            text = _unescape_key(text)

        numeric = isinstance(arg, A.NumberLit) or (
            text_is_name and len(text) > 1 and _is_number_text(text)
        )
        if numeric:
            code, warning = keycodes.resolve(int(text, 0)) if _is_number_text(text) else (None, None)
            if code is None and warning is None:
                return value
            if warning:
                self.diag(arg or self.program, "W0007", warning)
            return Lit(O.TEXT, "" if code is None else code)

        warning = keycodes.name_warning(text)
        if warning:
            self.diag(arg or self.program, "W0007", warning)
        return Lit(O.TEXT, text)

    def _menu_input(self, param: O.Param, value, block: Block, arg: A.Expr | None = None):
        """A dropdown input is a shadow block, unless the value is dynamic."""
        if not isinstance(value, Lit) and param.menu not in _NUMERIC_MENUS:
            self._check_eval(arg, f"the {param.menu!r} dropdown")
        if isinstance(value, Lit):
            if param.menu == "sensing_keyoptions":
                value = self._key_value(value, arg)
            menu = self._new(param.menu)
            menu.shadow = True
            menu.parent = block.id
            menu.set_field(param.field or "", value.value)
            return ShadowRef(menu)
        # ``[3, reporterId, [10, ""]]``: the reporter owns the slot.
        return Ref(value.block, Lit(O.TEXT, ""))

    @_inside_arguments
    def _command_call(
        self, call: A.Call, scope: Scope, fire_and_forget: bool = False
    ) -> list[Block]:
        if call is None:
            return []
        receiver = call.receiver
        if self.vanilla and self._is_comment_call(call):
            # `Comments.commentCommand("...")` is a note with no code in it:
            # nothing to keep, and nothing to warn about.
            return []
        if self.vanilla and isinstance(receiver, A.Name):
            namespace = receiver.parts[0]
            if (namespace, call.name) == ("Timer", "startTimer"):
                return self._timer_start(self._eval(call.args[0]) if call.args
                                         else Lit(O.TEXT, ""))
            if namespace == "WebSocket":
                # Sending is a no-op with no socket behind it. Dropping the
                # call also means its arguments are never evaluated, which is
                # what keeps the log's JSON formatting out of the build.
                return []
            if namespace in ("Events", "MoreEvents") and call.name in (
                "beforeSave", "afterSave"
            ):
                return []          # no save event exists in a Scratch project
            if namespace in ("console", "Console"):
                if call.name in ("clear", "Emptying"):
                    block = self._new("data_deletealloflist")
                    block.set_field("LIST", self.CONSOLE_LIST,
                                    self._shared_list(self.CONSOLE_LIST))
                    return [block]
                if call.name in ("log", "info", "warn", "error", "debug"):
                    return self._vanilla_console(call)
                if call.name in ("logTimer",):
                    return [self._console_add(self._eval(call.args[-1])
                                              if call.args else Lit(O.TEXT, ""))]
                # startLogTimer/endLogTimer are a stopwatch on the console
                # itself; with no console there is nothing to time.
                return []
        if receiver is None:
            console = self._emit_console_stmt(call.name, call.args)
            if console is not None:
                return console
            if call.name == "sleep":
                block = self._new("control_wait")
                block.set_input("DURATION", self._as_input(self._seconds(call.args), O.POSITIVE_NUM))
                return [block]
            # A broadcast receiver is a hat, so calling it means broadcasting.
            message = self.receiver_by_name.get(call.name)
            if message is not None:
                return [self._broadcast(message, wait=not fire_and_forget)]
            fn = self.class_funcs.get(self.class_name, {}).get(call.name)
            if fn is not None:
                if fire_and_forget and fn.visibility == "public" and len(fn.name) > 1:
                    return [self._broadcast(fn.qualified, wait=False)]
                return [self._procedure_call(fn, call.args)]
            self.diag(
                call,
                "E0036",
                f"unknown function {call.name!r}; declare it or use a mapped extension call",
            )
            return []

        if not isinstance(receiver, A.Name):
            self.diag(call, "E0054", "method calls on expression results are not supported yet")
            return []

        full = ".".join(receiver.parts)
        qname = f"{full}.{call.name}"

        if qname in ("Control.broadcast", "Control.broadcastAndWait"):
            return self._explicit_broadcast(call, wait=qname == "Control.broadcastAndWait")
        if qname in O.COMMANDS:
            spec = O.COMMANDS[qname]
            self.project.use_extension(spec.extension)
            block = self._new(spec.opcode)
            for name, value in spec.fields:
                block.set_field(name, value)
            for param, arg in zip(spec.params, call.args):
                if param.prim == O.FIELD:
                    value = self._eval(arg)
                    block.set_field(param.name, value.value if isinstance(value, Lit) else "")
                elif param.menu:
                    block.set_input(
                        param.name, self._menu_input(param, self._eval(arg), block, arg)
                    )
                else:
                    block.set_input(param.name, self._as_input(self._eval(arg), param.prim))
            return [block]
        if full in self.list_names and call.name in O.LIST_COMMANDS:
            return [self._list_command(full, call)]
        if self._is_runtime_store(full, call):
            return [self._runtime_var_store(call)]
        alias = self._state_alias(full, call)
        if alias is not None:
            return alias
        if (full, call.name) in ARITY_ALIASES and resolve(full, call.name, len(call.args), self.features) is None:
            allowed = sorted(ARITY_ALIASES[(full, call.name)])
            forms = ", ".join(str(n) for n in allowed)
            self.diag(
                call,
                "E0076",
                f"{full}.{call.name} takes {forms} arguments, not {len(call.args)}",
            )
            return []
        if full in known_namespaces(self.features) and resolve(full, call.name, len(call.args), self.features) is None:
            self.diag(call, "E0040", f"{qname} is not a block of the {full} extension")
            return []
        extension = resolve(full, call.name, len(call.args), self.features)
        if extension is not None and not extension.is_reporter:
            return [self._extension_command(extension, call)]

        # A receiver has no custom block behind it, so both a call to the one
        # declared here and to one in another sprite are broadcasts.
        message = self.receiver_by_name.get(qname)
        if message is not None:
            return [self._broadcast(message, wait=not fire_and_forget)]
        local_fn = self.class_qualified.get((self.class_name, qname))
        if local_fn is not None:
            if fire_and_forget and local_fn.visibility == "public" and len(local_fn.name) > 1:
                return [self._broadcast(local_fn.qualified, wait=False)]
            return [self._procedure_call(local_fn, call.args)]
        if qname in self.funcs_by_qualified:
            owner, fn = self.funcs_by_qualified[qname]
            if fire_and_forget and fn.visibility == "public" and len(fn.name) > 1:
                return [self._broadcast(fn.qualified, wait=False)]
            if owner == self.class_name:
                return [self._procedure_call(fn, call.args)]
            if fn.visibility == "public":
                return [self._broadcast(qname, wait=not fire_and_forget)]
            self.diag(call, "E0041", f"{qname} is private to sprite {owner!r}")
            return []
        if fire_and_forget:
            # Unknown but qualified target: assume it is a broadcast hook.
            return [self._broadcast(qname, wait=False)]
        self.diag(call, "E0038", f"unknown command {qname!r}")
        return []

    def _list_items(self, name: str) -> list | None:
        if self.plan is None:
            return None
        if "." not in name:
            local = self.plan.class_lists.get(self.class_name, {})
            if name in local:
                return local[name]
        return self.plan.lists.get(name)

    def _declare_list(self, name: str):
        """Bare names are sprite lists, dotted names are Stage lists."""
        assert self.target is not None
        bare = "." not in name
        if bare and name in (self.plan.lists if self.plan else {}):
            return self.project.stage.declare_list(self._qualify_var(name), self._list_items(name))
        if bare:
            return self.target.declare_list(name, self._list_items(name))
        return self.project.stage.declare_list(name, self._list_items(name))

    def _list_command(self, name: str, call: A.Call) -> Block:
        assert self.target is not None
        opcode, params = O.LIST_COMMANDS[call.name]
        block = self._new(opcode)
        block.set_field("LIST", name, self._declare_list(name))
        prims = {"INDEX": O.INTEGER, "ITEM": O.TEXT}
        for param, arg in zip(params, call.args):
            block.set_input(param, self._as_input(self._eval(arg), prims[param]))
        return block

    def _procedure_call(self, fn: A.FuncDecl, args: list[A.Expr]) -> Block:
        block = self._new("procedures_call")
        arg_ids = self._arg_ids(self.class_name, fn)
        block.mutation = {
            "tagName": "mutation",
            "children": [],
            "proccode": self._proccode(fn),
            "argumentids": json.dumps(arg_ids),
            "warp": self._warp_flag(fn),
        }
        for arg_id, arg in zip(arg_ids, args):
            block.set_input(arg_id, self._as_input(self._eval(arg), O.TEXT))
        return block

    # -- community extensions ----------------------------------------------
    def _extension_block(self, extension, args: list[A.Expr]) -> Block:
        """Materialise an extension block: inputs, menus and fixed arguments."""
        self.project.use_extension(extension.extension)
        block = self._new(extension.full_opcode)
        provided = list(args)
        if extension.extension == "pointerlock" and extension.opcode == "setLocked" and provided:
            # The menu's values are the strings "true"/"false"; `setPointerLock(1)`
            # is the 0/1 spelling of the same thing, so a literal is pinned to the
            # dropdown instead of being pushed through as a computed picker.
            first = provided[0]
            literal = None
            if isinstance(first, A.NumberLit):
                literal = "true" if first.raw not in ("0", "0.0") else "false"
            elif isinstance(first, A.BoolLit):
                literal = "true" if first.value else "false"
            if literal is not None:
                provided[0] = A.StringLit(line=first.line, col=first.col, value=literal)
        for name, arg_type, menu in extension.args:
            node: A.Expr | None = None
            if name == "MATRIX" and extension.extension == "microbit":
                value = self._matrix_value(args[0] if args else None)
                if args:
                    provided.pop(0)
            elif name in extension.values:
                value: object = Lit(O.TEXT, str(extension.values[name]))
            elif provided:
                node = provided.pop(0)
                value = self._eval(node)
            else:
                value = Lit(O.TEXT, "")
            if menu and not isinstance(value, Lit):
                # A list/variable picker fed with a runtime value: the VM
                # resolves the name when the block runs.
                self._check_eval(node, f"the {name!r} picker")
            if (
                name == "VAR"
                and extension.full_opcode.startswith("lmsTempVars2_")
                and extension.full_opcode.endswith("ThreadVariable")
                or (name == "VAR" and extension.full_opcode == "lmsTempVars2_threadVariableExists")
            ):
                # Thread variables are always spelled ``_name`` in the
                # language, so a loop/read written without the underscore is
                # normalised instead of silently pointing at another variable.
                if isinstance(value, Lit) and value.value and not str(value.value).startswith("_"):
                    value = Lit(O.TEXT, "_" + str(value.value))
            if menu:
                menu_block = self._new(f"{extension.extension}_menu_{menu}")
                menu_block.shadow = True
                menu_block.parent = block.id
                menu_block.set_field(menu, value.value if isinstance(value, Lit) else "")
                block.set_input(name, ShadowRef(menu_block))
            else:
                prim = O.BOOLEAN if arg_type == "BOOLEAN" else (
                    O.TEXT if arg_type in ("STRING", "MATRIX") else O.MATH_NUM
                )
                block.set_input(name, self._as_input(value, prim))
        if provided:
            self.diag(
                provided[0],
                "E0044",
                f"{extension.method} takes {len(extension.args)} argument(s)",
            )
        return block

    def _matrix_value(self, node: A.Expr | None) -> Lit:
        """The 5x5 display matrix as the 25-character string the VM wants.

        A binary literal is the same picture written as a number, so
        ``0b111`` becomes ``00000000000000000000111``.
        """
        if isinstance(node, A.NumberLit):
            try:
                number = int(float(node.raw))
            except (TypeError, ValueError):
                number = 0
            if number < 0 or number >= 1 << 25:
                self.diag(
                    node,
                    "W0022",
                    f"{number} does not fit the 5x5 display; only its low 25 bits show",
                )
                number &= (1 << 25) - 1
            return Lit(O.TEXT, format(number, "025b"))
        if isinstance(node, A.StringLit):
            text = node.value
            if len(text) != 25 or set(text) - {"0", "1"}:
                self.diag(
                    node,
                    "W0022",
                    f"the display matrix needs 25 characters of 0 and 1, "
                    f"got {len(text)} character(s)",
                )
            return Lit(O.TEXT, text)
        return self._eval(node) if node is not None else Lit(O.TEXT, "")

    def _extension_reporter(self, extension, call: A.Call | None) -> Ref:
        return Ref(self._extension_block(extension, call.args if call is not None else []))

    def _extension_command(self, extension, call: A.Call) -> Block:
        return self._extension_block(extension, call.args)

    def _explicit_broadcast(self, call: A.Call, wait: bool) -> list[Block]:
        if not call.args:
            self.diag(call, "E0048", "broadcast needs a message name")
            return []
        value = self._eval(call.args[0])
        if isinstance(value, Lit):
            return [self._broadcast(str(value.value), wait=wait)]
        self._check_eval(call.args[0], "the broadcast name")
        return [self._dynamic_broadcast(value, wait=wait, arg=call.args[0])]

    def _dynamic_broadcast(self, value: Value, wait: bool, arg: A.Expr | None = None) -> Block:
        """``Control.broadcastAndWait(<expression>)``: Scratch evaluates the
        message at runtime, the stored dropdown value is only a placeholder."""
        self._computed_broadcast = self._computed_broadcast or arg
        block = self._new("event_broadcastandwait" if wait else "event_broadcast")
        fallback = next(iter(self.project.stage.broadcasts.values()), "message1")
        shadow = Inline(
            [O.BROADCAST, fallback, self.project.stage.declare_broadcast(fallback)]
        )
        if isinstance(value, Ref):
            payload: object = value.block
        elif isinstance(value, Inline):
            payload = value.primitive
        else:
            payload = [O.TEXT, str(getattr(value, "value", ""))]
        block.set_input("BROADCAST_INPUT", Ref(payload, shadow))
        return block

    def _broadcast(self, name: str, wait: bool) -> Block:
        name = self._qualify(name)
        broadcast_id = self.project.stage.declare_broadcast(name)
        block = self._new("event_broadcastandwait" if wait else "event_broadcast")
        block.set_input("BROADCAST_INPUT", Inline([O.BROADCAST, name, broadcast_id]))
        return block

    def _state_owner(self, name: str):
        """The target that owns a bare state name.

        Sprite-local declarations must stay on the sprite: declaring them on the
        Stage as well would fork the value into two independent variables.
        """
        assert self.target is not None
        bare = "." not in name
        if bare and (
            self._class_definition(name) is not None
            or (self.plan is not None
                and name in self.plan.class_lists.get(self.class_name, {}))
        ):
            return self.target
        if bare and not (
            self.plan is not None
            and (name in self.plan.globals or name in self.plan.lists)
        ):
            return self.target
        return self.project.stage

    def _variable_visibility(self, call: A.Call, full: str, show: bool) -> list[Block]:
        name = self._alias_name(call)
        if name is None:
            self.diag(call, "E0046", "showVariable/hideVariable needs a variable name")
            return []
        block = self._new("data_showvariable" if show else "data_hidevariable")
        owner = self._state_owner(name)
        block.set_field("VARIABLE", name, owner.declare_variable(name, self._initial_of(name)))
        if show:
            # ``data_showvariable`` only flips a monitor the runtime already
            # knows about; with none stored the VM invents one at a default
            # place, and the editor forgets it after a reload. One entry per
            # shown variable keeps the position, hidden so the load looks the
            # same as it did before the block ran.
            self._shown_state.append((owner, name))
        return [block]

    def _list_visibility(self, call: A.Call, show: bool) -> list[Block]:
        name = self._alias_name(call)
        if name is None:
            self.diag(call, "E0047", "showList/hideList needs a list name")
            return []
        block = self._new("data_showlist" if show else "data_hidelist")
        owner = self._state_owner(name)
        block.set_field("LIST", name, owner.declare_list(name, self._list_items(name)))
        return [block]

    # -- input coercion ----------------------------------------------------
    def _as_input(self, value: object, prim: int) -> object:
        if prim == O.BOOLEAN:
            return self._as_bool(value)
        if isinstance(value, Lit):
            # A colour primitive must be ``#rrggbb``: the pen accepts a 24-bit
            # number at run time, but the file format does not.
            text = O.color_text(value.value) if prim == O.COLOR else value.value
            if prim in (O.TEXT, O.MATH_NUM) and self.obfuscating:
                pooled = self._pool_string(text, value.prim)
                if pooled is not None:
                    return pooled
            return Lit(prim, text)
        if isinstance(value, Ref):
            return Ref(value.block, Lit(prim, O.DEFAULT_SHADOWS.get(prim, "")))
        return value

    def _integers_as_variables(self) -> int:
        """Move every pooled integer literal into a variable; returns how many.

        A pooled literal costs a reporter at every use, and the cost is *not*
        where the literal appears in the source: a constant written once inside
        a `while` body runs a million times. Measured on the i386 project, the
        list form cost 3.3x the emulated work and no threshold fixed it -- the
        lookups that survived were always in the inner loop.

        A variable is the cheapest reporter Scratch has: the compiled runtime
        reads a property where `item i of list` also has to convert and bound
        the index. Same hiding (a variable's initial value is state, exactly
        like a list item), 1.4x instead of 3.3x. Nothing ever writes one, and a
        green flag resets variables to their declared value anyway.

        The value is stored as a *number*: a string "5" in a variable costs
        another 2x, because the compiled runtime has to convert it at every use
        (measured), while a literal in a number slot was already a number. An
        integer that is also used in a text slot -- `say("007")` -- keeps its
        spelling and stays in the pool instead.
        """
        counts = collections.Counter(
            text for pool in self._pools for _, text in pool.lookups
        )
        hidden = {
            text for text in counts
            if _INTEGER.fullmatch(text) and text not in self._text_integers
        }
        if not hidden:
            return 0
        # One Stage variable per constant, with the literal as its initial value.
        # The name is a placeholder like every other; obfuscation renames it.
        slots: dict[str, tuple[str, str]] = {}
        for text in sorted(hidden):
            name = f"Constant {len(slots) + 1}"
            slots[text] = (name, self.project.stage.declare_variable(name, int(text)))
            self.constant_variables[text] = slots[text][1]
        # The reporter has to live in the target that reads it: a block id only
        # resolves inside its own target, while the variable is Stage-global.
        reporters: dict[tuple[str, str], Block] = {}
        dropped: dict[str, str] = {}
        for pool in self._pools:
            keep: list[tuple[Block, str]] = []
            for block, text in pool.lookups:
                if text in hidden:
                    dropped[block.id] = text
                else:
                    keep.append((block, text))
            if len(keep) != len(pool.lookups):
                pool.lookups = keep
                for text in hidden:
                    pool.items.pop(text, None)
        if not dropped:
            return 0
        for target in self.project.targets:
            blocks = target.blocks
            # Creating a reporter adds to `blocks`, so iterate over a snapshot.
            for block in list(blocks.values()):
                for name, raw in list((block.inputs or {}).items()):
                    if not (
                        isinstance(raw, list)
                        and len(raw) > 1
                        and isinstance(raw[1], str)
                        and raw[1] in dropped
                    ):
                        continue
                    text = dropped[raw[1]]
                    key = (target.name, text)
                    reporter = reporters.get(key)
                    if reporter is None:
                        reporter = target.new_block("data_variable")
                        reporter.set_field("VARIABLE", slots[text][0], slots[text][1])
                        reporters[key] = reporter
                    block.inputs[name] = [2, reporter.id]
            for block_id in dropped:
                blocks.pop(block_id, None)
        return len(dropped)

    def _pool_string(self, text: str, prim: int = O.MATH_NUM) -> Ref | None:
        """``doObfuscation``: keep the text in a list, write ``item i of it``.

        The value is identical at run time — a list item is a string — but the
        script shows a reporter instead of the author's sentence, so a published
        project cannot be read by scrolling through it. The lists themselves
        still hold the text (a list is state, not a literal), which is
        documented: ``obfuscate_project`` shuffles them and buries them in junk
        strings, so even the lists give nothing away.

        Which pool a literal lands in is decided by a hash of the text, not by
        which pool happened to have room, so the same source always produces the
        same split.
        """
        # Floats carry no meaning to hide and pooling them would only bloat the
        # project. An integer is a different matter: `0` and `640` say what a
        # machine does, and a script full of bare numbers reads as arithmetic
        # with no author's intent left in it -- but only the *rare* ones are
        # worth a block each, which is what ``auto`` decides afterwards (see
        # `_depool_hot_integers`).
        # A broadcast name is renamed by the message pass, not hidden in a
        # string pool: pooling it would leave the sender holding the old text
        # while the receiver's hat got the new name.
        if text in self.project.stage.broadcasts.values():
            return None
        if not text or _POOL_SKIP.fullmatch(text):
            return None
        if self.obf_ints == "none" and _INTEGER.fullmatch(text):
            return None
        if _INTEGER.fullmatch(text) and prim != O.MATH_NUM:
            # `say("007")` is text, not seven: that one has to keep its spelling,
            # so it stays a pool item instead of becoming a numeric variable.
            # `prim` here is the primitive the *source* literal was written
            # with, not the slot it is going into.
            self._text_integers.add(text)
        known = self._pool_at.get(text)
        if known is None:
            slot = hashlib.sha1(text.encode()).digest()[0] % self.obf_pools
            while len(self._pools) <= slot:
                position = len(self._pools)
                # The names are placeholders: obfuscation renames every list, so
                # the pools end up as `v%d` like any other.
                name = "Strings" if position == 0 else f"Strings{position + 1}"
                self._pools.append(_Pool(name, self.project.stage.declare_list(name, [])))
            pool = self._pools[slot]
            known = (slot, len(pool.items) + 1)
            pool.items[text] = known[1]
            self._pool_at[text] = known
        pool = self._pools[known[0]]
        block = self._new("data_itemoflist")
        block.set_field("LIST", pool.name, pool.list_id)
        block.set_input("INDEX", Lit(O.MATH_NUM, str(known[1])))
        pool.lookups.append((block, text))
        return Ref(block)

    def _as_bool(self, value: object, negate: bool = False) -> object:
        if isinstance(value, Lit):
            truthy = value.value not in ("", "0", "false", "False")
            block = self._new("operator_equals")
            block.set_input("OPERAND1", Lit(O.TEXT, "1" if truthy else "0"))
            block.set_input("OPERAND2", Lit(O.TEXT, "0" if truthy else "1"))
            ref: object = Ref(block)
        elif isinstance(value, Ref):
            ref = Ref(value.block)
        else:
            ref = value
        if negate:
            block = self._new("operator_not")
            block.set_input("OPERAND", ref)
            return Ref(block)
        return ref

    def _seconds(self, args: list[A.Expr]) -> object:
        if not args:
            return Lit(O.MATH_NUM, "0")
        value = self._eval(args[0])
        if isinstance(value, Lit) and value.prim == O.MATH_NUM:
            try:
                return Lit(O.MATH_NUM, _format_number(float(value.value) / 1000))
            except ValueError:
                pass
        divide = self._new("operator_divide")
        divide.set_input("NUM1", self._as_input(value, O.MATH_NUM))
        divide.set_input("NUM2", Lit(O.MATH_NUM, "1000"))
        return Ref(divide)


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------
def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return repr(value)


def _is_truthy_literal(raw: str) -> bool:
    try:
        return float(raw) != 0
    except ValueError:
        return False


def _is_one(expr: A.Expr | None) -> bool:
    return isinstance(expr, A.NumberLit) and expr.raw in ("1", "1.0")


def _asset_number(annotation: str, key: str, default):
    """Pick one ``key=value`` pair out of a ``// costume``/``// sound`` note."""
    for name, value in _ASSET_META_RE.findall(annotation or ""):
        if name == key:
            try:
                return float(value)
            except ValueError:
                return default
    return default


def _negate(expr: A.Expr | None, gen: Codegen) -> object:
    if isinstance(expr, A.NumberLit):
        try:
            return Lit(O.MATH_NUM, _format_number(-float(expr.raw)))
        except ValueError:
            pass
    value = gen._eval(expr)
    if isinstance(value, Lit):
        try:
            return Lit(O.MATH_NUM, _format_number(-float(value.value)))
        except ValueError:
            return Lit(O.MATH_NUM, "0")
    block = gen._new("operator_subtract")
    block.set_input("NUM1", Lit(O.MATH_NUM, "0"))
    block.set_input("NUM2", gen._as_input(value, O.MATH_NUM))
    return Ref(block)


#: ``Temp`` members that only make sense against the extension's own store.
#: A project using any of them keeps every ``Temp.*`` call on the extension.
_EXTENSION_ONLY_RUNTIME_API = frozenset(
    {
        "deleteRuntimeVariable",
        "deleteAllRuntimeVariables",
        "runtimeVariableExists",
        "listRuntimeVariables",
    }
)


#: micro:bit hat -> (block argument, menu name).
_MICROBIT_MENUS: dict[str, tuple[str, str]] = {
    "whenButtonPressed": ("BTN", "buttons"),
    "whenGesture": ("GESTURE", "gestures"),
    "whenTilted": ("DIRECTION", "tiltDirectionAny"),
    "whenPinConnected": ("PIN", "touchPins"),
}


def _walk_calls(node) -> "Iterator[A.Call]":
    """Every ``A.Call`` inside ``node``, including nested blocks and bodies."""
    if isinstance(node, A.Call):
        yield node
    for field in getattr(node, "__dataclass_fields__", {}).values():
        value = getattr(node, field.name, None)
        if isinstance(value, A.Node):
            yield from _walk_calls(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from _walk_calls(item)


def _uses_extension_runtime_api(program: A.Program) -> bool:
    """True when the source depends on the extension's own runtime store."""
    for call in _walk_calls(program):
        if call.name not in _EXTENSION_ONLY_RUNTIME_API:
            continue
        receiver = call.receiver
        if isinstance(receiver, A.Name) and receiver.parts == ("Temp",):
            return True
    return False


#: ``sin(x)`` and friends: the ``operator_mathop`` menu under a readable name.
#: ``ceil`` spells the menu's ``ceiling``.  ``round`` is *not* here: Scratch
#: spells rounding as its own block, so it is lowered separately.
_MATH_BUILTINS: dict[str, str] = {
    "abs": "abs", "floor": "floor", "ceil": "ceiling", "ceiling": "ceiling",
    "sqrt": "sqrt", "sin": "sin", "cos": "cos", "tan": "tan",
    "asin": "asin", "acos": "acos", "atan": "atan",
    "ln": "ln", "log10": "log", "exp": "e ^", "pow10": "10 ^",
}

#: Every entry the ``[ ... ] of ( )`` menu actually has.  Anything else, written
#: through ``Operators.math(name, x)``, is a block that always reports 0.
_MATHOP_MENU: frozenset[str] = frozenset(
    {"abs", "floor", "ceiling", "sqrt", "sin", "cos", "tan", "asin",
     "acos", "atan", "ln", "log", "e ^", "10 ^"}
)

#: ``Lists.`` method names that map onto the existing list commands.
_LIST_ALIAS_COMMANDS: dict[str, str] = {
    "add": "add",
    "append": "add",
    "push": "add",
    "clear": "clear",
    "set": "set",
    "replace": "replace",
    "insert": "insert",
    "delete": "delete",
    "remove": "delete",
}

#: Arguments each ``Lists.`` reporter needs after the list name.
_LIST_REPORTER_ARITY: dict[str, int] = {"get": 1, "length": 0, "contains": 1, "indexOf": 1}


def _break_statements(body):
    """``break`` nodes owned by the loop whose body this is.

    A nested loop owns its own ``break``, so the walk stops there and the inner
    loop's emitter reports it.
    """
    for stmt in body:
        if isinstance(stmt, A.BreakStmt):
            yield stmt
        elif isinstance(stmt, (A.WhileStmt, A.ForStmt, A.RangeFor, A.RepeatStmt)):
            continue
        else:
            for child in _child_bodies(stmt):
                yield from _break_statements(child)


def _child_bodies(stmt) -> list[list[A.Stmt]]:
    """The statement lists a compound statement owns."""
    if isinstance(stmt, A.IfStmt):
        bodies = [stmt.body] + [block for _, block in stmt.elifs]
        return bodies + ([stmt.orelse] if stmt.orelse else [])
    if isinstance(stmt, A.SwitchStmt):
        bodies = [case.body for case in stmt.cases]
        return bodies + ([stmt.default] if stmt.default else [])
    if isinstance(stmt, (A.WhileStmt, A.RepeatStmt, A.ForStmt, A.RangeFor)):
        return [stmt.body]
    return []


def _writes_to(body, var: tuple[str, ...]):
    """``(node, op)`` for every statement that assigns ``var`` inside ``body``."""
    for stmt in body:
        if isinstance(stmt, A.Assign) and stmt.target is not None:
            if stmt.target.parts == var:
                yield stmt, stmt.op
        if isinstance(stmt, (A.ForStmt, A.RangeFor)):
            # A nested loop with the same counter rewrites it too.
            counter = getattr(stmt, "var", None)
            if counter == var:
                yield stmt, "loop"
        for child in _child_bodies(stmt):
            yield from _writes_to(child, var)


def _match_for_pattern(
    stmt: A.ForStmt,
) -> tuple[tuple[str, ...] | None, A.Expr | None, A.Expr | None, bool]:
    """Recognise ``for (v = a, v <= b, v++)`` and friends."""
    init, cond, update = stmt.init, stmt.cond, stmt.update
    if not isinstance(init, A.Assign) or init.op != "=":
        return None, None, None, False
    if not isinstance(update, A.Assign) or update.op != "+=":
        return None, None, None, False
    if not (isinstance(update.value, A.NumberLit) and update.value.raw in ("1", "1.0")):
        return None, None, None, False
    if not isinstance(cond, A.Binary) or cond.op not in _LOOP_OPS:
        return None, None, None, False
    var = init.target
    if var is None or not isinstance(cond.left, A.Name) or cond.left.parts != var.parts:
        return None, None, None, False
    if update.target is None or update.target.parts != var.parts:
        return None, None, None, False
    inclusive = not _LOOP_OPS[cond.op]
    return var.parts, init.value, cond.right, inclusive


# ---------------------------------------------------------------------------
# Asset inspection helpers
# ---------------------------------------------------------------------------
def _image_size(payload: bytes, extension: str) -> tuple[float, float]:
    if extension == "png" and len(payload) >= 24 and payload[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", payload[16:24])
        return float(width), float(height)
    if extension == "svg":
        head = payload[:2048].decode("utf-8", "ignore")
        width = re.search(r'width="([\d.]+)', head)
        height = re.search(r'height="([\d.]+)', head)
        if width and height:
            return float(width.group(1)), float(height.group(1))
    return 0.0, 0.0


def _wav_info(payload: bytes) -> tuple[int, int]:
    """Return ``(sample_rate, sample_count)`` for a PCM WAV file."""
    if len(payload) < 12 or payload[:4] != b"RIFF":
        return 48000, 0
    rate = 44100
    block_align = 2
    position = 12
    while position + 8 <= len(payload):
        chunk_id = payload[position : position + 4]
        chunk_size = struct.unpack("<I", payload[position + 4 : position + 8])[0]
        body = position + 8
        if chunk_id == b"fmt " and body + 16 <= len(payload):
            rate = struct.unpack("<I", payload[body + 4 : body + 8])[0]
            block_align = struct.unpack("<H", payload[body + 12 : body + 14])[0] or 1
        elif chunk_id == b"data":
            return rate, chunk_size // max(1, block_align)
        position = body + chunk_size + (chunk_size & 1)
    return rate, 0


#: ``rotation=all`` in a declaration -> the value ``project.json`` stores.
#: The keys a target accepts, and the kind of value each one takes. A typo
#: (``shwo=0``) used to be ignored in silence: the sprite came out in the
#: default state and nothing said why.
_SPRITE_META_KEYS = {
    "name": "text",
    "x": "number",
    "y": "number",
    "size": "number",
    "direction": "number",
    "show": "flag",
    "namespace": "text",
    "costume": "integer",
    "volume": "number",
    "layer": "integer",
    "rotation": "rotation",
    "draggable": "flag",
}

_STAGE_META_KEYS = {
    "costume": "integer",
    "volume": "number",
    "layer": "integer",
    "tempo": "number",
    "video": "text",
    "videoState": "text",
    "videoTransparency": "number",
}

_ROTATION_STYLES = {
    "all": "all around",
    "leftright": "left-right",
    "left-right": "left-right",
    "none": "don't rotate",
    "don't rotate": "don't rotate",
}


def _metadata_value(expr: A.Expr | None):
    """Evaluate a target metadata value (a literal: numbers, text, booleans).

    ``main: name="obj loader", rotation="all", …;`` needs text values; the
    rotation style and the video state are words, not numbers.
    """
    if isinstance(expr, A.StringLit):
        return expr.value
    if isinstance(expr, A.Name) and len(expr.parts) == 1:
        # ``video=on`` / ``rotation=all`` — a bare word is the value here, not a
        # reference to a variable.
        return expr.parts[0]
    if isinstance(expr, A.AnyLit):
        return "any"
    if isinstance(expr, A.NullLit):
        return ""
    if isinstance(expr, A.NumberLit):
        try:
            number = float(expr.raw)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    if isinstance(expr, A.BoolLit):
        return 1 if expr.value else 0
    if isinstance(expr, A.Unary) and expr.op == "-" and isinstance(expr.operand, A.NumberLit):
        try:
            return -float(expr.operand.raw)
        except ValueError:
            return None
    return None


def _list_item(expr: A.Expr | None):
    """A literal list item for a list declaration, or ``None``."""
    if isinstance(expr, A.NumberLit):
        try:
            number = float(expr.raw)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    if isinstance(expr, A.StringLit):
        return expr.value
    if isinstance(expr, A.BoolLit):
        return 1 if expr.value else 0
    if isinstance(expr, A.Unary) and expr.op == "-" and isinstance(expr.operand, A.NumberLit):
        try:
            return -float(expr.operand.raw)
        except ValueError:
            return None
    return None


def _whole_if_integral(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value
