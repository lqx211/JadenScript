"""Community-extension calls: ``JSON.isArray(x)`` → ``skyhigh173JSON_json_is``.

Two layers:

* **generic** — any extension in :data:`jswc.extension_blocks.EXTENSION_BLOCKS`
  is callable through its namespace. ``JSON.json_array_get(a, i)`` resolves to
  ``skyhigh173JSON_json_array_get`` with the declared argument names, so every
  block of every known extension is expressible without hand-written code;
* **aliases** — the friendlier names people actually write (``JSON.isArray``,
  ``Timer.startTimer``, ``console.log``…). An alias can pin arguments to fixed
  values and rename the rest.

Both directions use the same tables, so a project always decompiles to
something that compiles back to the same opcode.

The pishi.ai extensions (:data:`jswc.pishi_blocks.PISHI_BLOCKS`) are the one
family that is **not** always available: they live in pishi's own player, so a
project that uses them only loads there, and `#enable_feature<pishiSupport>;`
is what says "this build is for that player". With the feature off their
namespaces are unknown, exactly like any other extension nobody has written
down; with it on they resolve like the rest, and their names are checked
against the other extensions and the project's own names (see
:func:`pishi_namespace_conflicts`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .extension_blocks import EXTENSION_BLOCKS
from .gtm_blocks import GTM_BLOCKS
from .keysim_blocks import KEYSIM_BLOCKS
from .opcodes import BOOLEAN, COLOR, MATH_NUM, TEXT
from .pishi_blocks import PISHI_BLOCKS

#: The feature that turns the pishi.ai extensions on.
PISHI_FEATURE = "pishiSupport"


def catalogue(features=()) -> dict[str, dict]:
    """The extension table this build resolves against.

    Everything in :data:`EXTENSION_BLOCKS` and in
    :data:`jswc.gtm_blocks.GTM_BLOCKS` is always there -- a TurboWarp
    extension is fetched from a URL and works in any player. pishi's are only
    there when the source asked for them, so a project that never mentions the
    feature cannot silently depend on a player it does not name.
    """
    if PISHI_FEATURE not in features:
        return _GENERIC
    return _MERGED


_GENERIC: dict[str, dict] = {**EXTENSION_BLOCKS, **GTM_BLOCKS, **KEYSIM_BLOCKS}
_MERGED: dict[str, dict] = {**_GENERIC, **PISHI_BLOCKS}


def keysim_namespaces() -> set[str]:
    """The namespaces Key Simulation adds, so a build can refuse them too."""
    return {entry["namespace"] for entry in KEYSIM_BLOCKS.values()}


def gtm_namespaces() -> set[str]:
    """The namespaces ``gtm`` adds, so a build can refuse them by target."""
    return {entry["namespace"] for entry in GTM_BLOCKS.values()}


def pishi_namespace_conflicts() -> list[tuple[str, str, str]]:
    """``(namespace, extension id, other source)`` for names two features claim.

    A namespace is how a project *spells* a block (``Arduino.high(13)``), so two
    extensions with the same one is not a style problem: the compiler would pick
    one and the player the other. The caller reports these as errors.
    """
    ours = {entry["namespace"]: extension for extension, entry in PISHI_BLOCKS.items()}
    # Built here rather than read from the import-time `_GENERIC`: the clash is
    # between *whatever the tables hold now* (a test patching EXTENSION_BLOCKS
    # is checking this function, and an extension registered by a plugin has to
    # be considered too).
    others = {**EXTENSION_BLOCKS, **GTM_BLOCKS, **KEYSIM_BLOCKS}
    theirs = {entry["namespace"]: extension for extension, entry in others.items()}
    clashes = []
    for namespace, extension in sorted(ours.items()):
        other = theirs.get(namespace)
        if other is not None:
            clashes.append((namespace, extension, other))
    return clashes


def pishi_namespaces() -> set[str]:
    """The namespaces `pishiSupport` adds, whether or not it is on."""
    return {entry["namespace"] for entry in PISHI_BLOCKS.values()}

# ArgumentType -> sb3 input primitive.
PRIMITIVES: dict[str, int] = {
    "STRING": TEXT,
    "NUMBER": MATH_NUM,
    "BOOLEAN": BOOLEAN,
    "ANGLE": 8,
    "COLOR": COLOR,
    "NOTE": MATH_NUM,
    "MATRIX": TEXT,
    "IMAGE": TEXT,
    "COSTUME": TEXT,
    "SOUND": TEXT,
}


@dataclass(frozen=True)
class Alias:
    """A friendly call name for an extension block.

    ``params`` names the JadenScript arguments in order; ``fixed`` pins the
    remaining block arguments to literals (menus, mostly).
    """

    namespace: str
    method: str
    opcode: str
    params: tuple[str, ...] = ()
    fixed: dict[str, str] = field(default_factory=dict)
    #: arguments that may be left off the end of the call. A call that supplies
    #: one un-pins the matching entry of ``fixed``, so `clickLeft(x, y)` and
    #: `clickLeft(x, y, 0.5)` are the same alias with a different duration.
    optional: tuple[str, ...] = ()
    #: the spelling the decompiler writes back; legacy names stay accepted but
    #: do not win when several aliases share one opcode
    canonical: bool = True


# name used in JadenScript -> extension block
ALIASES: dict[tuple[str, str], Alias] = {}


def _alias(namespace: str, method: str, opcode: str, params=(), *, canonical=True,
           optional=(), **fixed) -> None:
    ALIASES[(namespace, method)] = Alias(
        namespace, method, opcode, tuple(params), fixed,
        optional=tuple(optional), canonical=canonical,
    )


# -- JSON (skyhigh173JSON) --------------------------------------------------
_alias("JSON", "isArray", "json_is", ["json"], types="Array")
_alias("JSON", "isObject", "json_is", ["json"], types="Object")
_alias("JSON", "isString", "json_is", ["json"], types="String")
_alias("JSON", "isNumber", "json_is", ["json"], types="Number")
_alias("JSON", "isBoolean", "json_is", ["json"], types="Boolean")
_alias("JSON", "isValid", "json_is_valid", ["json"])
_alias("JSON", "parse", "json_get_all", ["json"], Stype="Object")
_alias("JSON", "concatArray", "json_array_concat", ["json", "json2"])
_alias("JSON", "arrayPush", "json_array_push", ["json", "item"])
_alias("JSON", "arrayGet", "json_array_get", ["json", "index"])
_alias("JSON", "get", "json_get", ["json", "key"])
_alias("JSON", "set", "json_set", ["json", "key", "value"])
_alias("JSON", "length", "json_length", ["json"])

# -- Timer (lmsTimers) ------------------------------------------------------
_alias("Timer", "startTimer", "startResetTimer", ["TIMER"])
_alias("Timer", "getTimer", "valueOfTimer", ["TIMER"])
_alias("Timer", "pauseTimer", "pauseTimer", ["TIMER"])
_alias("Timer", "resumeTimer", "resumeTimer", ["TIMER"])
_alias("Timer", "setTimer", "setTimer", ["TIMER", "NUM"])
_alias("Timer", "changeTimer", "changeTimer", ["TIMER", "NUM"])
_alias("Timer", "removeTimer", "removeTimer", ["TIMER"])
_alias("Timer", "timerExists", "timerExists", ["TIMER"])

# -- console (sipcconsole) --------------------------------------------------
_alias("console", "log", "Journal", ["string"])
_alias("console", "info", "Information", ["string"])
_alias("console", "warn", "Warning", ["string"])
_alias("console", "error", "Error", ["string"])
_alias("console", "debug", "debug", ["string"])
_alias("console", "clear", "Emptying")
_alias("console", "startLogTimer", "Timeron", ["string"])
_alias("console", "endLogTimer", "Timeroff", ["string"])
_alias("console", "logTimer", "Timerlog", ["string"])

# -- Runtime (runtimeoptions) ----------------------------------------------
_alias("Runtime", "turboMode", "getEnabled", thing="turbo mode")
_alias("Runtime", "isTurboMode", "getEnabled", thing="turbo mode")
_alias("Runtime", "framerateLimit", "getFramerate", ["fps"])
_alias("Runtime", "cloneLimit", "getCloneLimit", ["limit"])
_alias("Runtime", "stageWidth", "getDimension", dimension="width")
_alias("Runtime", "stageHeight", "getDimension", dimension="height")
_alias("Runtime", "compilerEnabled", "getEnabled", thing="compiler")
_alias("Runtime", "setFramerate", "setFramerate", ["fps"])
_alias("Runtime", "setTurboMode", "setEnabled", ["enabled"], thing="turbo mode")

# -- WebSocket (gsaWebsocket) ----------------------------------------------
# The connection is an object with a state: `closingCode` reads like
# `Sensing.answer`, because a reporter with no arguments is a property.
_alias("WebSocket", "connectTo", "newInstance", ["URL"])
_alias("WebSocket", "receivedMessageData", "messageData")
_alias("WebSocket", "sendMessage", "sendMessage", ["PAYLOAD"])
_alias("WebSocket", "connectionErrored", "hasErrored")
_alias("WebSocket", "isConnectionClosed", "isClosed")
_alias("WebSocket", "isConnected", "isConnected")
_alias("WebSocket", "closingCode", "closeCode")
_alias("WebSocket", "closingMessage", "closeMessage")

# The three closing forms are three different blocks in the extension, so the
# argument count chooses the opcode (see ARITY_ALIASES below).
_alias("WebSocket", "closeConnection", "closeWithoutReason")
ARITY_ALIASES: dict[tuple[str, str], dict[int, Alias]] = {
    ("WebSocket", "closeConnection"): {
        0: Alias("WebSocket", "closeConnection", "closeWithoutReason"),
        1: Alias("WebSocket", "closeConnection", "closeWithCode", ("CODE",)),
        2: Alias("WebSocket", "closeConnection", "closeWithReason", ("CODE", "REASON")),
    },
    # ``gtm.updateInterval`` reads the interval, ``gtm.updateInterval(0.05)``
    # sets it: the same name in both positions, told apart by the argument count
    # the way `WebSocket.closeConnection` is.
    ("gtm", "updateInterval"): {
        0: Alias("gtm", "updateInterval", "updateInterval"),
        1: Alias("gtm", "updateInterval", "setUpdateInterval", ("SECONDS",)),
    },
}

# The extension's own block names, still accepted so that older sources and
# decompiled projects keep compiling.
for _old, _new in (
    ("newInstance", "connectTo"),
    ("messageData", "receivedMessageData"),
    ("hasErrored", "connectionErrored"),
    ("isClosed", "isConnectionClosed"),
    ("closeCode", "closingCode"),
    ("closeMessage", "closingMessage"),
    ("closeWithoutReason", "closeConnection"),
    ("closeWithCode", "closeConnection"),
    ("closeWithReason", "closeConnection"),
):
    _target = ALIASES[("WebSocket", _new)]
    _arg_count = len(_target.params)
    if _old in ("closeWithCode", "closeWithoutReason", "closeWithReason"):
        continue            # reached through closeConnection's arity table
    _alias("WebSocket", _old, _target.opcode, list(_target.params), canonical=False)
del _old, _new, _target, _arg_count

# -- TurboWarp blocks (tw) --------------------------------------------------
# `last key pressed` is the *cased* key: a single letter keeps its case, the
# rest are the names Scratch uses ("left arrow", "space", "enter", …).
_alias("TurboWarp", "lastKeyPressed", "getLastKeyPressed")
_alias("TurboWarp", "isMouseButtonDown", "getButtonIsDown", ["MOUSE_BUTTON"])
_alias("TurboWarp", "primaryButtonDown", "getButtonIsDown", MOUSE_BUTTON="0")
_alias("TurboWarp", "middleButtonDown", "getButtonIsDown", MOUSE_BUTTON="1")
_alias("TurboWarp", "secondaryButtonDown", "getButtonIsDown", MOUSE_BUTTON="2")
_alias("TurboWarp", "getLastKeyPressed", "getLastKeyPressed", canonical=False)
_alias("TurboWarp", "getButtonIsDown", "getButtonIsDown", ["MOUSE_BUTTON"], canonical=False)

# -- Key Simulation (cubesterKeySimulation) --------------------------------
# The extension's own spelling is one block per shape -- `press key for`,
# `click mouse button at` -- and it takes a menu for the button. These aliases
# are the same blocks with the button folded into the name, which is how the
# call reads: `keySim.clickLeft(-240, 0)`.
_alias("keySim", "press", "pressKey", ["KEY", "SECONDS"], AND_WAIT="without waiting")
_alias("keySim", "pressAndWait", "pressKey", ["KEY", "SECONDS"], AND_WAIT="and wait")
_alias("keySim", "hold", "holdKey", ["KEY"])
_alias("keySim", "release", "releaseKey", ["KEY"])
_alias("keySim", "moveTo", "moveMouse", ["X", "Y"])
# The general forms take the button as an argument, for a computed one.
_alias("keySim", "click", "clickMouse", ["BUTTON", "X", "Y", "SECONDS", "AND_WAIT"])
_alias("keySim", "holdButton", "holdMouse", ["BUTTON", "X", "Y"])
_alias("keySim", "releaseButton", "releaseMouse", ["BUTTON"])
BUTTON_NAMES = {"Left": "0", "Middle": "1", "Right": "2"}
for _name, _value in BUTTON_NAMES.items():
    # `clickLeft(x, y)` clicks for 0.1 s; the third argument is the duration.
    _alias("keySim", f"click{_name}", "clickMouse", ["X", "Y"],
           optional=["SECONDS"], BUTTON=_value, SECONDS="0.1",
           AND_WAIT="without waiting")
    _alias("keySim", f"hold{_name}", "holdMouse", ["X", "Y"], BUTTON=_value)
    _alias("keySim", f"release{_name}", "releaseMouse", (), BUTTON=_value)
del _name, _value

# -- Pointerlock (pointerlock) ----------------------------------------------
# `setPointerLock(n)` takes 0/1 or an expression; `isPointerLocked` is the
# boolean, spelled as a property.
_alias("Pointerlock", "setPointerLock", "setLocked", ["enabled"])
_alias("Pointerlock", "isPointerLocked", "isLocked")
_alias("Pointerlock", "setLocked", "setLocked", ["enabled"], canonical=False)
_alias("Pointerlock", "isLocked", "isLocked", canonical=False)

# -- micro:bit (microbit) ---------------------------------------------------
# The block names are the extension's own; these are the spellings the spec
# asks for. The menu arguments stay as they are, so `microbit.buttonA` and
# `"A"` both work.
_alias("microbit", "display", "displaySymbol", ["MATRIX"])
_alias("microbit", "clearDisplay", "displayClear")
_alias("microbit", "buttonPressed", "isButtonPressed", ["BTN"])
_alias("microbit", "tilted", "isTilted", ["DIRECTION"])
_alias("microbit", "angle", "getTiltAngle", ["DIRECTION"])
_alias("microbit", "tiltAngle", "getTiltAngle", ["DIRECTION"])

# -- Util (utilities) -------------------------------------------------------
_alias("Util", "ruleReplace", "regexReplace", ["STRING", "REGEX", "NEWSTRING"])
_alias("Util", "regexReplace", "regexReplace", ["STRING", "REGEX", "NEWSTRING"])
_alias("Util", "clamp", "clamp", ["n", "min", "max"])
_alias("Util", "parseJSON", "parseJSON", ["json"])
_alias("Util", "stringToBoolean", "stringToBoolean", ["string"])
_alias("Util", "pi", "pi")
_alias("Util", "now", "currentMillisecond")
_alias("Util", "newline", "newline")


@dataclass(frozen=True)
class ExtensionCall:
    """A resolved extension block."""

    extension: str
    opcode: str
    kind: str  # COMMAND / REPORTER / BOOLEAN / HAT
    args: tuple[tuple[str, str, str], ...]  # (name, ArgumentType, menu)
    values: dict[str, object]  # block argument name -> already-evaluated value
    method: str

    @property
    def full_opcode(self) -> str:
        return f"{self.extension}_{self.opcode}"

    @property
    def is_reporter(self) -> bool:
        return self.kind in ("REPORTER", "BOOLEAN")

    @property
    def prim(self) -> int:
        return BOOLEAN if self.kind == "BOOLEAN" else MATH_NUM


#: Every extension id in the catalogue, longest first, for opcode prefixes.
#: ``gtm_isLabel`` -> ``gtm``. Built once: ``_as_bool`` asks per condition.
_EXTENSION_IDS: tuple[str, ...] = ()


def is_extension_opcode(opcode: str) -> bool:
    """Whether ``opcode`` was made by an extension (``gtm_isLabel``).

    Extension blocks declare no output check, so a Boolean slot takes them as
    they are. That matters for the truthiness wrap in ``Codegen._as_bool``:
    wrapping a Boolean reporter would be correct but pointless, and it would
    change the source a decompile produces for ``if (gtm.isLabel(x))``.
    """
    global _EXTENSION_IDS
    if not _EXTENSION_IDS:
        _EXTENSION_IDS = tuple(sorted(catalogue(), key=lambda name: (-len(name), name)))
    return any(opcode.startswith(extension_id + "_") for extension_id in _EXTENSION_IDS)


def extension_for_namespace(namespace: str, features=()) -> str | None:
    for extension_id, entry in catalogue(features).items():
        if entry["namespace"] == namespace:
            return extension_id
    return None


def _snake(name: str) -> str:
    out = []
    for index, ch in enumerate(name):
        if ch.isupper() and index:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def extension_arity(namespace: str, method: str, features=()) -> int | None:
    """How many arguments ``Namespace.method`` takes, if it is a real block.

    ``resolve`` answers ``None`` for a wrong argument count, which reads as
    "there is no such block" — ``Facemesh.getX(1)`` said *"not a block of the
    Facemesh extension"* when the block exists and wants two. This is the number
    to print instead.
    """
    table = catalogue(features)
    forms = ARITY_ALIASES.get((namespace, method))
    if forms:
        return max(forms)
    alias = ALIASES.get((namespace, method))
    if alias is not None:
        entry = table.get(_extension_of(alias.opcode, namespace), {})
        spec = entry.get("blocks", {}).get(alias.opcode)
        if spec is None:
            return None
        return len(spec["args"]) - len(alias.fixed) - len(alias.optional)
    extension_id = extension_for_namespace(namespace, features)
    if extension_id is None:
        return None
    blocks = table.get(extension_id, {}).get("blocks", {})
    for candidate in (method, _snake(method)):
        spec = blocks.get(candidate)
        if spec is not None:
            return len(spec["args"])
    return None


def resolve(namespace: str, method: str, arg_count: int | None = None,
            features=()) -> ExtensionCall | None:
    """Resolve ``Namespace.method`` to an extension block, if one exists.

    ``features`` is the set the source asked for; it is what makes the pishi
    extensions resolvable (see :data:`PISHI_FEATURE`).
    """
    table = catalogue(features)
    by_arity = ARITY_ALIASES.get((namespace, method))
    if by_arity is not None:
        chosen = by_arity.get(arg_count) if arg_count is not None else by_arity.get(0)
        if chosen is None:
            return None
        alias = chosen
        entry = table.get(_extension_of(alias.opcode, namespace), {})
        spec = entry.get("blocks", {}).get(alias.opcode)
        if spec is None:
            return None
        return ExtensionCall(
            extension=_extension_of(alias.opcode, namespace),
            opcode=alias.opcode,
            kind=spec["type"],
            args=tuple(tuple(a) for a in spec["args"]),
            values=dict(alias.fixed),
            method=method,
        )
    alias = ALIASES.get((namespace, method))
    if alias is not None:
        values = dict(alias.fixed)
        if arg_count is not None:
            extra = arg_count - len(alias.params)
            if extra < 0 or extra > len(alias.optional):
                # An alias names one block with one argument list: `isArray(x,
                # y)` is not a shorter `isArray`, it is a call nobody can serve.
                return None
            for name in alias.optional[:extra]:
                # Given on the call, so the pin must not swallow the argument.
                values.pop(name, None)
        entry = table.get(_extension_of(alias.opcode, namespace), {})
        spec = entry.get("blocks", {}).get(alias.opcode)
        if spec is None:
            return None
        return ExtensionCall(
            extension=_extension_of(alias.opcode, namespace),
            opcode=alias.opcode,
            kind=spec["type"],
            args=tuple(tuple(a) for a in spec["args"]),
            values=values,
            method=method,
        )

    extension_id = extension_for_namespace(namespace, features)
    if extension_id is None:
        return None
    blocks = table[extension_id]["blocks"]
    for candidate in (method, _snake(method)):
        if candidate in blocks:
            spec = blocks[candidate]
            if arg_count is not None and len(spec["args"]) != arg_count:
                continue
            return ExtensionCall(
                extension=extension_id,
                opcode=candidate,
                kind=spec["type"],
                args=tuple(tuple(a) for a in spec["args"]),
                values={},
                method=method,
            )
    return None


def alias_arity(namespace: str, method: str) -> tuple[int, int] | None:
    """The argument counts an alias accepts, or ``None`` if it is not an alias."""
    alias = ALIASES.get((namespace, method))
    if alias is None:
        return None
    return len(alias.params), len(alias.params) + len(alias.optional)


def suggest_alias(namespace: str, method: str, arg_count: int) -> str | None:
    """Another spelling of ``method`` that takes exactly ``arg_count`` arguments.

    ``keySim.clickMiddle(x, y, seconds)`` is ``clickMiddleFor``: the shorter
    name is the fixed-duration form, and saying so is more use than "not a
    block of the keySim extension".
    """
    for (ns, candidate), alias in ALIASES.items():
        if ns != namespace or candidate == method:
            continue
        if candidate.startswith(method) and len(alias.params) == arg_count:
            return candidate
    return None


def _extension_of(opcode: str, namespace: str) -> str:
    """The extension an alias's opcode belongs to, if this namespace has one.

    Aliases are registered for the always-available extensions only, so the
    lookup is against :data:`_GENERIC` -- a namespace that is also an extension
    id (``gtm``, ``keySim``) must resolve to the id, or its blocks would be
    looked up under the namespace instead.
    """
    extension_id = extension_for_namespace(namespace)
    if extension_id is not None and opcode in _GENERIC[extension_id]["blocks"]:
        return extension_id
    return namespace


def reverse(full_opcode: str, values: dict[str, object] | None = None,
            features=()) -> tuple[str, str, ExtensionCall] | None:
    """``skyhigh173JSON_json_is`` → (``JSON``, ``isArray``, call) when aliased.

    ``values`` are the argument values the block actually carries. Several
    aliases can share one opcode and differ only by a pinned argument --
    ``Runtime.stageWidth`` and ``Runtime.stageHeight`` are both ``getDimension``
    -- so the values decide which spelling decompiles back to the same block.
    """
    if "_" not in full_opcode:
        return None
    extension_id, opcode = full_opcode.split("_", 1)
    entry = catalogue(features).get(extension_id)
    if entry is None:
        return None
    spec = entry["blocks"].get(opcode)
    if spec is None:
        return None
    namespace = entry["namespace"]
    # a method that dispatches on argument count (closeConnection) owns several
    # opcodes: decompile any of them back to that one name
    for (ns, method), table in ARITY_ALIASES.items():
        if ns != namespace:
            continue
        for arity, alias in sorted(table.items()):
            if alias.opcode == opcode:
                return namespace, method, ExtensionCall(
                    extension=extension_id,
                    opcode=opcode,
                    kind=spec["type"],
                    args=tuple(tuple(a) for a in spec["args"]),
                    values={},
                    method=method,
                )

    matching = [
        (method, alias)
        for (ns, method), alias in ALIASES.items()
        if ns == namespace and alias.opcode == opcode
    ]
    # A pinned argument that matches the block is the strongest signal; then
    # canonical spelling; then registration order (`turboMode` before
    # `compilerEnabled`), which is the order the language lists them in.
    def score(pair: tuple[str, Alias]) -> tuple:
        alias = pair[1]
        hits = sum(
            1 for key, want in alias.fixed.items()
            if values is not None and str(values.get(key, "")).lower() == str(want).lower()
        )
        complete = bool(alias.fixed) and hits == len(alias.fixed)
        return (not complete, not alias.canonical, -hits)

    matching.sort(key=score)
    for method, alias in matching[:1]:
        # An optional argument whose pin the block does not carry was given at
        # the call: write it back instead of letting the pin swallow it
        # (`clickMiddle(0, 0, 0.5)` must not come back as `clickMiddle(0, 0)`).
        chosen = dict(alias.fixed)
        if values is not None:
            for name in alias.optional:
                if name in chosen and str(values.get(name, "")).lower() != str(chosen[name]).lower():
                    del chosen[name]
        if True:
            return namespace, method, ExtensionCall(
                extension=extension_id,
                opcode=opcode,
                kind=spec["type"],
                args=tuple(tuple(a) for a in spec["args"]),
                values=chosen,
                method=method,
            )
    return namespace, opcode, ExtensionCall(
        extension=extension_id,
        opcode=opcode,
        kind=spec["type"],
        args=tuple(tuple(a) for a in spec["args"]),
        values={},
        method=opcode,
    )


def known_namespaces(features=()) -> set[str]:
    return {entry["namespace"] for entry in catalogue(features).values()}


def _members_of(tables, namespace: str | None = None) -> set[str]:
    names: set[str] = set()
    for (ns, method), alias in ALIASES.items():
        if namespace is None or ns == namespace:
            names.add(method)
            names.add(alias.opcode)
    for (ns, method), table in ARITY_ALIASES.items():
        if namespace is None or ns == namespace:
            names.add(method)
            for alias in table.values():
                names.add(alias.opcode)
    for entry in tables:
        if namespace is not None and entry["namespace"] != namespace:
            continue
        for opcode in entry["blocks"]:
            names.add(opcode)
            names.add(_camel(opcode))
    return {name for name in names if name}


def known_members(namespace: str | None = None) -> set[str]:
    """Every member name an extension namespace accepts.

    This is what the syntax highlighters colour: the friendly aliases *and* the
    extension's own block names, so a project written in either style lights up.
    """
    return _members_of(catalogue().values(), namespace)


#: Every extension a highlighter should know, whether or not a *build* may use
#: it: `pishiSupport` gates what the compiler resolves, not what a file looks
#: like while you are writing it.
HIGHLIGHT_BLOCKS: dict[str, dict] = {
    **EXTENSION_BLOCKS, **GTM_BLOCKS, **KEYSIM_BLOCKS, **PISHI_BLOCKS
}


def highlight_namespaces() -> set[str]:
    """Every namespace the grammars colour, including the feature-gated ones."""
    return {entry["namespace"] for entry in HIGHLIGHT_BLOCKS.values()}


def highlight_members(namespace: str | None = None) -> set[str]:
    """Every member name the grammars colour, including the feature-gated ones."""
    return _members_of(HIGHLIGHT_BLOCKS.values(), namespace)


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)
