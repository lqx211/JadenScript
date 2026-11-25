#!/usr/bin/env python3
"""Generate the completion database the VS Code extension uses.

TextMate grammars only colour text; they cannot answer ``Pen.`` with a list of
blocks. The extension therefore ships a JSON snapshot of everything the
compiler knows — keywords, runtime namespaces and their members, community
extensions, list methods, feature switches — and ``vscode-jsw/completion.js``
serves it.

``metadata`` gets the same treatment: the four words that can open the line
(``monitor``/``watch``/``comment``/``note``), the modes, the layout words, the
monitor states, and the target keys with the type each one takes
(``text``/``number``/``integer``/``flag``/``rotation``/``video``) plus whether
it belongs to a sprite or to the Stage. The types come from
``jswc.keywords.TARGET_KEY_TYPES``, which is the table the compiler's own
``_check_meta`` enforces.

Run ``python3 tools/gen_vscode_data.py`` (``make vscode-data``) after touching
``jswc/keywords.py``, ``jswc/opcodes.py`` or ``jswc/extensions.py``. The file is
committed so the extension works without a build step.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jswc import opcodes as O  # noqa: E402
from jswc.extension_blocks import MICROBIT_CONSTANTS  # noqa: E402
from jswc.extensions import ALIASES, EXTENSION_BLOCKS  # noqa: E402
from jswc.gtm_blocks import GTM_BLOCKS  # noqa: E402
from jswc.keysim_blocks import (  # noqa: E402
    KEYSIM_AND_WAIT,
    KEYSIM_BLOCKS,
    KEYSIM_BUTTONS,
    KEYSIM_KEYS,
    KEYSIM_KEY_LABELS,
)
from jswc.keycodes import CONTROL_CODES, MORE_KEYS, SUPPORTED_NAMES  # noqa: E402
from jswc.keywords import (  # noqa: E402
    BUILTIN_NAMESPACES,
    FEATURES,
    KEYWORDS,
    LITERALS,
    MODIFIERS,
    RESERVED,
    TARGET_KEY_SCOPES,
    TARGET_KEY_TYPES,
    TYPES,
)

DATA_PATH = ROOT / "vscode-jsw" / "data" / "jsw.json"

#: Every extension the editor should suggest, in the order it is written down.
#: ``gtm`` is hand-written; the rest are generated from the reference projects.
_ALL_EXTENSIONS = (*EXTENSION_BLOCKS.values(), *GTM_BLOCKS.values(),
                   *KEYSIM_BLOCKS.values())

# Argument-type names for the hover/completion detail line.
PRIM_NAMES = {
    O.MATH_NUM: "num",
    O.POSITIVE_NUM: "num",
    O.WHOLE_NUM: "num",
    O.INTEGER: "index",
    O.ANGLE: "angle",
    O.COLOR: "color",
    O.TEXT: "text",
    O.BOOLEAN: "bool",
    O.MENU: "menu",
    O.FIELD: "menu",
}

KEYWORD_DOCS = {
    "class": "Declares a Scratch sprite. `class main { // Sprite: main (0,0) … }`",
    "stage": "Declares the Stage script block.",
    "loose": "Keeps a block that is not attached to any hat (`loose { … }`).",
    "void": "Marks a method; every method is a custom block.",
    "public": "Visible to other sprites; `Sprite.method()` becomes a broadcast.",
    "private": "Local to the sprite (a plain custom block).",
    "if": "Compiles to the if / if-else block.",
    "elif": "Chained `else if`; compiles to nested if-else blocks.",
    "else": "The other branch of `if` / `elif`.",
    "for": "Counted loop: `for (i in 0..n)`.",
    "while": "Repeat until the condition is false.",
    "repeat": "Repeat n times.",
    "switch": "Chain of equality tests on one value.",
    "case": "One branch of a `switch`.",
    "default": "The fallback branch of a `switch`.",
    "break": "Leave the innermost loop or switch.",
    "continue": "Skip to the next iteration.",
    "return": "Stop this script (Scratch has no return values).",
    "call": "Fire-and-forget broadcast instead of a blocking call.",
    "import": "Pull a list (.jsa) / variable (.jsv) in from a side file.",
    "include": "Splice another source file in: `include \"display.jsw\"` — one "
               "quoted path, nothing else on the line. A `class` is a sprite, so "
               "this is how a project with several sprites stays in several files.",
    "as": "Names the imported value.",
    "true": "Boolean literal.",
    "false": "Boolean literal.",
    "null": "Empty value (compiles to an empty input).",
    "and": "Logical and.",
    "or": "Logical or.",
    "not": "Logical not.",
    "any": 'The "any key" menu option — compiles to the text `"any"`.',
    "metadata": "Canvas metadata: `metadata <script> pos x,y;`, monitors, notes.",
}

BUILTIN_DOCS = {
    "log": "Append a line to the TurboWarp console journal.",
    "warn": "Console warning.",
    "error": "Console error.",
    "startTimer": "Start a named console timer.",
    "stopTimer": "Stop a named console timer.",
    "breakpoint": "Marks a point in the console log.",
    "join": "Join two values (operator_join).",
    "string": "Join a value into a string.",
    "len": "Length of a string (operator_length).",
    "length": "Length of a string (operator_length).",
    "lengthOf": "Length of a value once it is text (operator_length) — `length of []`.",
    "sin": "Sine of an angle in degrees (operator_mathop).",
    "cos": "Cosine of an angle in degrees (operator_mathop).",
    "tan": "Tangent of an angle in degrees (operator_mathop).",
    "asin": "Arcsine in degrees (operator_mathop).",
    "acos": "Arccosine in degrees (operator_mathop).",
    "atan": "Arctangent in degrees (operator_mathop).",
    "sqrt": "Square root (operator_mathop).",
    "abs": "Absolute value (operator_mathop).",
    "floor": "Round down (operator_mathop).",
    "ceil": "Round up (operator_mathop) — the block calls this `ceiling`.",
    "ln": "Natural logarithm (operator_mathop).",
    "log10": "Base-10 logarithm (operator_mathop); `log(x);` is the console statement.",
    "exp": "e raised to the power (operator_mathop).",
    "pow10": "10 raised to the power (operator_mathop).",
    "float": "Force a numeric input slot.",
    "isTurboWarp": "True when running in TurboWarp.",
    "isCompiled": "True when the project was compiled.",
    "menu": "Escape hatch for a dropdown shadow.",
    "block": "Raw block escape hatch: `block(\"opcode\", …)`.",
}

LIST_METHOD_DOCS = {
    "add": "Append an item.",
    "append": "Append an item.",
    "push": "Append an item.",
    "clear": "Remove every item.",
    "set": "Replace item n.",
    "replace": "Replace item n.",
    "insert": "Insert at position n.",
    "delete": "Delete item n.",
    "remove": "Delete item n.",
    "get": "Item n (reporter).",
    "length": "Number of items (reporter).",
    "contains": "True when the list holds the value (reporter).",
    "indexOf": "Position of a value (reporter).",
}

LIST_REPORTER_ARGS = {"get": "index", "length": "", "contains": "item", "indexOf": "item"}

# Hats are methods whose name (or trailing ``// hook:`` comment) picks the hat
# opcode, so they are suggested instead of being discovered by memory.
# Supported by the compiler but implemented in codegen rather than in the
# opcode tables (the broadcast name is evaluated at runtime).
EXTRA_MEMBERS = [
    ("Control", "broadcast", "command", "broadcast(message)", "Fire a broadcast."),
    ("Control", "broadcastAndWait", "command", "broadcastAndWait(message)", "Fire a broadcast and wait."),
    # The state namespaces take the variable or list *name*, bare or quoted.
    # ``Variables`` is only the monitor pair: a variable is written ``score = 5``
    # in JadenScript, so ``set``/``get`` would just add a second spelling.
    ("Variables", "showVariable", "command", "showVariable(name)", "Show the variable monitor."),
    ("Variables", "hideVariable", "command", "hideVariable(name)", "Hide the variable monitor."),
    ("Lists", "add", "command", "add(name, item)", "Append to a list by name."),
    ("Lists", "get", "reporter", "get(name, i)", "Item of a list by name."),
    ("Lists", "length", "reporter", "length(name)", "Length of a list by name."),
    ("Lists", "contains", "reporter", "contains(name, item)", "Whether the list holds an item."),
    ("Lists", "indexOf", "reporter", "indexOf(name, item)", "Position of an item."),
    ("Lists", "set", "command", "set(name, i, item)", "Replace an item."),
    ("Lists", "insert", "command", "insert(name, i, item)", "Insert an item."),
    ("Lists", "delete", "command", "delete(name, i)", "Delete an item."),
    ("Lists", "clear", "command", "clear(name)", "Delete every item."),
    ("Lists", "showList", "command", "showList(name)", "Show the list monitor."),
    ("Lists", "hideList", "command", "hideList(name)", "Hide the list monitor."),
    ("Clone", "create", "command", "create() / create(sprite)", "Clone myself, or another sprite."),
    ("Clone", "delete", "command", "delete()", "Delete this clone."),
    ("Stage", "get", "reporter", "get(name)", "Read stage state by name."),
    ("Stage", "set", "command", "set(name, value)", "Set stage state by name."),
    ("Stage", "change", "command", "change(name, delta)", "Change stage state by name."),
    # The two blocks a decompiled project needs and the tables above spell with
    # their raw argument names.
    ("Looks", "changeEffect", "command", "changeEffect(effect, delta)",
     'Change a graphic effect by an amount: `Looks.changeEffect("GHOST", 10)`.'),
    ("Sensing", "current", "reporter", 'current("hour" | "year" | …)',
     "The clock. Takes year, month, date, day of week, hour, minute or second."),
    # keySim puts input *into* the player, so each block is worth a sentence:
    # `key pressed?` and `mouse down?` see what one of these synthesises.
    ("keySim", "press", "command", "press(key, seconds)",
     "Press and release a key; the press lasts `seconds` and the script carries on."),
    ("keySim", "pressAndWait", "command", "pressAndWait(key, seconds)",
     "Press and release a key and wait the `seconds` out."),
    ("keySim", "hold", "command", "hold(key)",
     "Hold a key down until `release`."),
    ("keySim", "release", "command", "release(key)",
     "Release a key that `hold` pressed."),
    ("keySim", "click", "command", 'click(button, x, y, seconds, "and wait")',
     "Click a mouse button at a point; `button` is 0 left, 1 middle, 2 right."),
    ("keySim", "clickLeft", "command", "clickLeft(x, y[, seconds])",
     "Click the left button for 0.1 s, or for `seconds`."),
    ("keySim", "clickMiddle", "command", "clickMiddle(x, y[, seconds])",
     "Click the middle button for 0.1 s, or for `seconds`."),
    ("keySim", "clickRight", "command", "clickRight(x, y[, seconds])",
     "Click the right button for 0.1 s, or for `seconds`."),
    ("keySim", "holdLeft", "command", "holdLeft(x, y)",
     "Hold the left button down at a point until `releaseLeft`."),
    ("keySim", "holdMiddle", "command", "holdMiddle(x, y)",
     "Hold the middle button down at a point until `releaseMiddle`."),
    ("keySim", "holdRight", "command", "holdRight(x, y)",
     "Hold the right button down at a point until `releaseRight`."),
    ("keySim", "releaseLeft", "command", "releaseLeft()",
     "Release a held left button."),
    ("keySim", "releaseMiddle", "command", "releaseMiddle()",
     "Release a held middle button."),
    ("keySim", "releaseRight", "command", "releaseRight()",
     "Release a held right button."),
    ("keySim", "holdButton", "command", "holdButton(button, x, y)",
     "Hold any button down at a point; `button` is 0 left, 1 middle, 2 right."),
    ("keySim", "releaseButton", "command", "releaseButton(button)",
     "Release a button that `holdButton` pressed."),
    ("keySim", "moveTo", "command", "moveTo(x, y)",
     "Move the pointer without clicking."),
]

HATS = [
    ("greenFlag", "greenFlag()", "when green flag clicked"),
    ("whenCloned", "whenCloned()", "when I start as a clone"),
    ("whenClicked", "whenClicked()", "when this sprite clicked"),
    ("whenStageClicked", "whenStageClicked()", "when stage clicked"),
    ("whenBackdropChanges", "whenBackdropChanges()", "when backdrop switches to …"),
    ("onSave", "onSave()", "before/after project save"),
    ("Events.keyPressed", "Events.keyPressed(k)", "when [key] key pressed"),
    ("microbit.buttonPressed", "microbit.buttonPressed()", "when [A] button pressed"),
    ("microbit.gesture", "microbit.gesture()", "when [moved] shaken or jumped"),
    ("microbit.tilted", "microbit.tilted()", "when tilted [front] back left or right"),
    ("microbit.pinConnected", "microbit.pinConnected()", "when pin [0] connected"),
]

ANNOTATIONS = [
    ("Sprite:", "`// Sprite: name (x,y) size=… costume=…` — sprite metadata."),
    ("hook:", "Turns a method into a hat: `// hook: when [space] key pressed`."),
    ("proccode", "Overrides the custom-block label: `// proccode move camera %s %s`."),
    ("receiver at", "Rebuilds a `when I receive` hat: `// receiver at x,y \"message\"`."),
    ("monitor", "Variable/list monitor: `// monitor Stage.score default at 0,0 0x0 shown`."),
    ("watch", "Alias of `// monitor`."),
    ("comment", "Workspace comment attached to a script."),
    ("warp", "Runs the block without screen refresh."),
    ("main thread", "Runs the block on the main thread."),
    ("grouped", "Marks a grouped block stack."),
    ("at", "Source position hint used when rebuilding the workspace."),
]


#: ``metadata`` modes, in the order the syntax doc lists them.
MONITOR_MODE_DOCS = [
    ("list", "Shows the list monitor for the target."),
    ("large", "A large (read-only) variable readout."),
    ("largeReadout", "The long spelling of `large`."),
    ("slider", "A slider; add `range min..max`, or write `slider(min,max)`."),
    ("normal", "The default variable readout."),
    ("default", "Same as `normal`, but deleted when its target is deleted."),
]

#: The aliases that open a metadata line. All four take a target, and
#: `monitor`/`watch` are the same statement under two names.
METADATA_FORM_DOCS = [
    (
        "pos",
        "metadata <script> pos <x>,<y>;",
        "Where a script sits on the canvas: `metadata Stage.tick pos 10,20;`. "
        "`<script>` is the method as written, e.g. `greenFlag` or "
        "`Events.keyPressed`.",
    ),
    (
        "monitor",
        "metadata monitor <Target.name> at <x>,<y> <W>x<H> <shown|hidden>;",
        "A read-out for one variable, in either order: the mode may come before "
        "or after the target. `// monitor …` is the comment spelling.",
    ),
    (
        "watch",
        "metadata watch <Target.name> at <x>,<y> <W>x<H> <shown|hidden>;",
        "Alias of `monitor`.",
    ),
    (
        "slider",
        "metadata slider(<min>,<max>) <Target.name> at <x>,<y> <W>x<H> shown;",
        "A slider. Whole-number bounds are steps, so `slider(0,2)` is a "
        "three-state switch; the decompiler writes it `range 0..2 discrete`.",
    ),
    (
        "list",
        "metadata list <Target.list> at <x>,<y> <W>x<H> <shown|hidden>;",
        "The list's contents in a monitor box.",
    ),
    (
        "large",
        "metadata large <Target.name> at <x>,<y> <W>x<H> <shown|hidden>;",
        "The big number, for one value someone is watching.",
    ),
    (
        "comment",
        "metadata comment <owner> at <x>,<y> <W>x<H>: \"text\";",
        "A workspace comment: the `// comment` spelling with a position.",
    ),
    (
        "note",
        "metadata note <owner> at <x>,<y> <W>x<H>: \"text\";",
        "Alias of `comment`.",
    ),
]

METADATA_WORD_DOCS = [
    ("pos", "Script position: `metadata Stage.tick pos 10,20;`."),
    ("at", "Monitor position and size: `at 0,0 100x24`."),
    ("range", "Slider bounds: `range 0..10`."),
    ("discrete", "The slider steps by 1 instead of sliding continuously."),
    ("continuous", "The explicit opposite of `discrete`."),
    ("watchers", "Whether more than one watcher may exist."),
    ("min", "Lower bound for `range`."),
    ("max", "Upper bound for `range`."),
]

METADATA_FLAG_DOCS = [
    ("shown", "The monitor starts visible."),
    ("hidden", "The monitor starts hidden."),
    ("minimized", "A workspace comment starts collapsed."),
]

#: The values a target key may take from a closed set, and which keys take
#: them. Scoped and completed only after `key=`.
TARGET_VALUE_DOCS = [
    ("all", ("rotation",), "Rotate in any direction (the default)."),
    ("leftright", ("rotation",), "Only mirror left and right."),
    ("none", ("rotation",), "Never rotate."),
    ("on", ("video", "videoState"), "Show the camera picture."),
    ("off", ("video", "videoState"), "No camera picture."),
    ("on-flipped", ("video", "videoState"), "Show the camera, mirrored."),
    ("1", ("show", "draggable"), "On."),
    ("0", ("show", "draggable"), "Off."),
]

TARGET_KEY_DOCS = [
    ("name", 'The name the project shows: `name="obj loader"`.'),
    ("x", "Stage position, in project units."),
    ("y", "Stage position, in project units."),
    ("size", "Sprite size percentage."),
    ("direction", "Rotation in degrees, 90 is to the right."),
    ("show", "1 visible, 0 hidden."),
    ("costume", "Index of the current costume."),
    ("volume", "Sound volume percentage."),
    ("layer", "Layer order; larger is nearer the viewer."),
    ("tempo", "Stage tempo in bpm."),
    ("video", "Camera on/off."),
    ("videoState", "The same thing as `video`."),
    ("videoTransparency", "Video transparency percentage."),
    ("rotation", 'all, leftright or none.'),
    ("draggable", "1 if the sprite can be dragged."),
    ("namespace", "Prefix for this class's public state and blocks."),
    ("ns", "Short spelling of `namespace`, used by `// Sprite:`."),
]

ASSET_WORD_DOCS = [
    ("costume", "The import is a costume."),
    ("sound", "The import is a sound."),
]

ASSET_KEY_DOCS = [
    ("centerx", "Costume rotation centre, in image pixels."),
    ("centery", "Costume rotation centre, in image pixels."),
    ("res", "Costume resolution multiplier."),
    ("rate", "Sound sample rate in Hz."),
    ("samples", "Number of sample frames."),
]


def _param_names(params) -> list[str]:
    names = []
    for param in params or ():
        names.append(param.name.lower())
    return names


def _signature(name: str, args: list[str]) -> str:
    return f"{name}({', '.join(args)})" if args else f"{name}()"


def _namespaces() -> dict:
    out: dict[str, dict] = {}

    def entry(namespace: str) -> dict:
        return out.setdefault(namespace, {"members": {}})

    for full, cmd in O.COMMANDS.items():
        namespace, _, name = full.partition(".")
        if not name:
            continue
        args = _param_names(cmd.params)
        entry(namespace)["members"][name] = {
            "kind": "command",
            "detail": _signature(name, args),
            "doc": cmd.opcode,
        }
    for full, rep in O.REPORTERS.items():
        namespace, _, name = full.partition(".")
        if not name:
            continue
        args = _param_names(rep.params)
        entry(namespace)["members"][name] = {
            "kind": "reporter",
            "detail": _signature(name, args) + " → value",
            "doc": rep.opcode,
        }
    for full, prop in O.PROPERTIES.items():
        namespace, _, name = full.partition(".")
        if not name:
            continue
        entry(namespace)["members"][name] = {
            "kind": "property",
            "detail": f"{name} (readable/writable)",
            "doc": f"{prop.get} / {prop.set}",
        }
    for namespace in BUILTIN_NAMESPACES:
        entry(namespace)

    # opcode -> kind, so an alias can say whether it is a statement or a value.
    kind_of: dict[str, str] = {}
    for meta in _ALL_EXTENSIONS:
        for block_name, block in meta["blocks"].items():
            kind_of[block_name] = "command" if block["type"] == "COMMAND" else "reporter"

    # Community extensions: the generic ``JSON.json_array_get`` spelling.
    # A block is skipped only when an alias already reaches the same opcode, so
    # every block stays completable without listing the same one twice
    # (``JSON.arrayGet`` wins over ``JSON.json_array_get``; ``microbit.displayText``
    # has no alias and therefore still shows up).
    alias_opcodes: dict[str, set[str]] = {}
    for (namespace, _method), alias in ALIASES.items():
        alias_opcodes.setdefault(namespace, set()).add(alias.opcode)
    for meta in _ALL_EXTENSIONS:
        namespace = meta["namespace"]
        covered = alias_opcodes.get(namespace, set())
        members = entry(namespace)["members"]
        for block_name, block in meta["blocks"].items():
            if block["type"] == "HAT" or block_name in covered:
                continue
            args = [a[0].lower() for a in block["args"]]
            members[block_name] = {
                "kind": "command" if block["type"] == "COMMAND" else "reporter",
                "detail": _signature(block_name, args),
                "doc": block["type"].lower(),
            }
    # Friendly aliases win over the mechanical names. A reporter with no
    # arguments is a *property* -- ``WebSocket.closingCode`` -- so it is
    # suggested without parentheses, exactly as the decompiler writes it.
    for (namespace, method), alias in ALIASES.items():
        members = entry(namespace)["members"]
        args = list(alias.params)
        kind = kind_of.get(alias.opcode, "command")
        name = method if (kind != "command" and not args) else _signature(method, args)
        members[method] = {
            "kind": kind,
            "detail": name + ("" if kind == "command" else " → value"),
            "doc": alias.opcode,
        }
    for namespace, name, kind, detail, doc in EXTRA_MEMBERS:
        entry(namespace)["members"][name] = {"kind": kind, "detail": detail, "doc": doc}
    for full, detail, doc in HATS:
        namespace, _, name = full.partition(".")
        if not name:
            continue  # a bare class-level method, listed in `hats` instead
        entry(namespace)["members"][name] = {"kind": "command", "detail": detail, "doc": doc}
    # A namespace that would suggest nothing is a dead end; drop it.
    return {
        name: {"members": sorted(data["members"].items())}
        for name, data in sorted(out.items())
        if data["members"]
    }


def _key_names() -> list[dict]:
    """Every key name and code ``Sensing.keyPressed`` accepts."""
    out = []
    for name in sorted(SUPPORTED_NAMES):
        out.append({"label": name, "kind": "key", "detail": "key name", "doc": ""})
    for code, name in sorted(MORE_KEYS.items()):
        out.append(
            {
                "label": name,
                "kind": "key",
                "detail": f"{code}",
                "doc": f"Also written as the number `{code}`.",
            }
        )
    for code, name in sorted(CONTROL_CODES.items()):
        if name not in SUPPORTED_NAMES:
            continue  # "tab" is a key, but TurboWarp never reports it
        out.append(
            {
                "label": name,
                "kind": "key",
                "detail": f"{code}",
                "doc": f"Also written as the number `{code}`.",
            }
        )
    # One entry per label: the first (plain name) wins.
    seen, unique = set(), []
    for entry in out:
        if entry["label"] in seen:
            continue
        seen.add(entry["label"])
        unique.append(entry)
    return unique


#: The keySim menu values, spelled the way the extension's dropdown spells
#: them. ``press``/``hold``/``release`` store ``KeyboardEvent.key``, so a
#: spelled-out name is translated by the compiler
#: (``KEYSIM_KEY_LABELS``) and the character is stored as itself.
KEYSIM_BUTTON_DOCS = {
    "0": "Left button.",
    "1": "Middle button.",
    "2": "Right button.",
}


def _keysim_keys() -> list[dict]:
    """What may go in `keySim.press/hold/release(…)`.

    The friendly names first — `space` is `" "` in the DOM and `up arrow` is
    `ArrowUp`, which is exactly what a reader needs to know — then the raw
    values the dropdown offers.
    """
    out = []
    for label, value in KEYSIM_KEY_LABELS.items():
        shown = "the space bar" if label == "space" else f"`{value}`"
        literal = '" "' if value == " " else f'"{value}"'
        out.append(
            {
                "label": label,
                "kind": "key",
                "detail": f"key · {shown}",
                "value": value,
                "doc": f"Compiles to `{literal}`; the extension compares "
                       f"against `KeyboardEvent.key`.",
            }
        )
    named = {entry["value"] for entry in out}
    for value in KEYSIM_KEYS:
        if value in named:
            continue
        if value == " ":
            continue  # the `space` label above is the way to write it
        out.append(
            {
                "label": value,
                "kind": "key",
                "detail": "key · the character itself",
                "value": value,
                "doc": "",
            }
        )
    return out


def _keysim_buttons() -> list[dict]:
    return [
        {
            "label": value,
            "kind": "key",
            "detail": "mouse button",
            "doc": KEYSIM_BUTTON_DOCS.get(value, ""),
        }
        for value in KEYSIM_BUTTONS
    ]


def _keysim_wait() -> list[dict]:
    return [
        {
            "label": value,
            "kind": "key",
            "detail": "and wait",
            "doc": "`and wait` holds the script until the click finishes; "
                   "`without waiting` is the *continue* item.",
        }
        for value in KEYSIM_AND_WAIT
    ]


def build() -> dict:
    keywords = []
    for word in sorted(KEYWORDS | RESERVED):
        if word in MODIFIERS:
            kind, doc = "modifier", "Access modifier."
        elif word in TYPES:
            kind, doc = "type", "Return type of a method (Scratch has none)."
        elif word in LITERALS:
            kind, doc = "literal", "Literal value."
        elif word in KEYWORDS:
            kind, doc = "keyword", ""
        else:
            kind, doc = "reserved", "Reserved for a future version — the compiler rejects it."
        keywords.append(
            {
                "label": word,
                "kind": kind,
                "detail": "keyword" if kind == "keyword" else kind,
                "doc": KEYWORD_DOCS.get(word, doc),
            }
        )
    return {
        "keywords": keywords,
        "builtins": [
            {"label": name, "kind": "function", "detail": "builtin", "doc": doc}
            for name, doc in sorted(BUILTIN_DOCS.items())
        ],
        "features": sorted(FEATURES),
        "namespaces": _namespaces(),
        # Every namespace the compiler knows, including one whose member list
        # is empty: the editor must never fall back to "it must be a list".
        "builtinNamespaces": sorted(BUILTIN_NAMESPACES),
        "listMethods": [
            {
                "label": name,
                "kind": "command" if name in O.LIST_COMMANDS else "reporter",
                "detail": _signature(
                    name,
                    [a.lower() for a in O.LIST_COMMANDS[name][1]]
                    if name in O.LIST_COMMANDS
                    else [LIST_REPORTER_ARGS.get(name, "item")],
                )
                + ("" if name in O.LIST_COMMANDS else " → value"),
                "doc": LIST_METHOD_DOCS.get(name, ""),
            }
            for name in sorted(set(O.LIST_COMMANDS) | set(O.LIST_REPORTERS))
        ],
        "annotations": [{"label": word, "doc": doc} for word, doc in ANNOTATIONS],
        "microbitConstants": [
            {"label": name, "value": value}
            for name, value in sorted(MICROBIT_CONSTANTS.items())
        ],
        "monitorModes": [
            {
                "label": word,
                "kind": "constant",
                "detail": "metadata mode",
                "doc": doc,
            }
            for word, doc in MONITOR_MODE_DOCS
        ],
        # The shapes a `metadata` line can take: offered right after the keyword
        # and shown in the hover for `metadata` itself.
        "metadataForms": [
            {
                "label": word,
                "kind": "snippet",
                "detail": "metadata · " + usage,
                "usage": usage,
                "doc": doc,
            }
            for word, usage, doc in METADATA_FORM_DOCS
        ],
        "metadataWords": [
            {
                "label": word,
                "kind": "keyword",
                "detail": "metadata word",
                "doc": doc,
            }
            for word, doc in METADATA_WORD_DOCS
        ],
        "metadataFlags": [
            {
                "label": word,
                "kind": "constant",
                "detail": "monitor state · shown|hidden|minimized",
                "doc": doc,
            }
            for word, doc in METADATA_FLAG_DOCS
        ],
        # `rotation=all` — the values, and the keys that take them. completion.js
        # only offers these after the `=`.
        "targetValues": [
            {
                "label": word,
                "kind": "enum",
                "keys": list(keys),
                "detail": "value of " + ", ".join(keys),
                "doc": doc,
            }
            for word, keys, doc in TARGET_VALUE_DOCS
        ],
        "targetKeys": [
            {
                "label": word,
                "kind": "keyword",
                "detail": f"{TARGET_KEY_TYPES.get(word, 'text')} · "
                          f"{TARGET_KEY_SCOPES.get(word, 'sprite')}",
                "type": TARGET_KEY_TYPES.get(word, "text"),
                "scope": TARGET_KEY_SCOPES.get(word, "sprite"),
                "doc": doc,
            }
            for word, doc in TARGET_KEY_DOCS
        ],
        "assetWords": [
            {"label": word, "kind": "keyword", "doc": doc}
            for word, doc in ASSET_WORD_DOCS
        ],
        "assetKeys": [
            {"label": word, "kind": "keyword", "doc": doc}
            for word, doc in ASSET_KEY_DOCS
        ],
        "keyNames": _key_names(),
        "keysimKeys": _keysim_keys(),
        "keysimButtons": _keysim_buttons(),
        "keysimWait": _keysim_wait(),
        "hats": [
            {"label": full, "detail": detail, "doc": doc}
            for full, detail, doc in HATS
        ],
    }


def main() -> int:
    data = build()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    members = sum(len(v["members"]) for v in data["namespaces"].values())
    print(
        f"wrote {DATA_PATH.relative_to(ROOT)} "
        f"({len(data['keywords'])} keywords, {len(data['namespaces'])} namespaces, "
        f"{members} members, {len(data['listMethods'])} list methods)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
