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
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .extension_blocks import EXTENSION_BLOCKS
from .opcodes import BOOLEAN, COLOR, MATH_NUM, TEXT

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
    #: the spelling the decompiler writes back; legacy names stay accepted but
    #: do not win when several aliases share one opcode
    canonical: bool = True


# name used in JadenScript -> extension block
ALIASES: dict[tuple[str, str], Alias] = {}


def _alias(namespace: str, method: str, opcode: str, params=(), *, canonical=True,
           **fixed) -> None:
    ALIASES[(namespace, method)] = Alias(
        namespace, method, opcode, tuple(params), fixed, canonical
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


def extension_for_namespace(namespace: str) -> str | None:
    for extension_id, entry in EXTENSION_BLOCKS.items():
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


def resolve(namespace: str, method: str, arg_count: int | None = None) -> ExtensionCall | None:
    """Resolve ``Namespace.method`` to an extension block, if one exists."""
    by_arity = ARITY_ALIASES.get((namespace, method))
    if by_arity is not None:
        chosen = by_arity.get(arg_count) if arg_count is not None else by_arity.get(0)
        if chosen is None:
            return None
        alias = chosen
        entry = EXTENSION_BLOCKS.get(_extension_of(alias.opcode, namespace), {})
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
        entry = EXTENSION_BLOCKS.get(_extension_of(alias.opcode, namespace), {})
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

    extension_id = extension_for_namespace(namespace)
    if extension_id is None:
        return None
    blocks = EXTENSION_BLOCKS[extension_id]["blocks"]
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


def _extension_of(opcode: str, namespace: str) -> str:
    extension_id = extension_for_namespace(namespace)
    if extension_id is not None and opcode in EXTENSION_BLOCKS[extension_id]["blocks"]:
        return extension_id
    return namespace


def reverse(full_opcode: str,
            values: dict[str, object] | None = None) -> tuple[str, str, ExtensionCall] | None:
    """``skyhigh173JSON_json_is`` → (``JSON``, ``isArray``, call) when aliased.

    ``values`` are the argument values the block actually carries. Several
    aliases can share one opcode and differ only by a pinned argument --
    ``Runtime.stageWidth`` and ``Runtime.stageHeight`` are both ``getDimension``
    -- so the values decide which spelling decompiles back to the same block.
    """
    if "_" not in full_opcode:
        return None
    extension_id, opcode = full_opcode.split("_", 1)
    entry = EXTENSION_BLOCKS.get(extension_id)
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
        if True:
            return namespace, method, ExtensionCall(
                extension=extension_id,
                opcode=opcode,
                kind=spec["type"],
                args=tuple(tuple(a) for a in spec["args"]),
                values=dict(alias.fixed),
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


def known_namespaces() -> set[str]:
    return {entry["namespace"] for entry in EXTENSION_BLOCKS.values()}


def known_members(namespace: str | None = None) -> set[str]:
    """Every member name an extension namespace accepts.

    This is what the syntax highlighters colour: the friendly aliases *and* the
    extension's own block names, so a project written in either style lights up.
    """
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
    for entry in EXTENSION_BLOCKS.values():
        if namespace is not None and entry["namespace"] != namespace:
            continue
        for opcode in entry["blocks"]:
            names.add(opcode)
            names.add(_camel(opcode))
    return {name for name in names if name}


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)
