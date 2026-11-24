"""``keySim``: synthetised keyboard and mouse input, for TurboWarp only.

Key Simulation (``cubesterKeySimulation``, by CubesterYT) puts events into the
player instead of reading them out: ``press (space) for (0.1) seconds and
(continue)`` makes ``key pressed?`` and ``when key pressed`` fire, and
``click (left) mouse button at x: (0) y: (0) …`` does the same for the mouse
and for ``mouse x``/``mouse y``. The blocks come from
``https://extensions.turbowarp.org/CubesterYT/KeySimulation.js``; the opcodes
and argument names below are read from that file, not from the documentation
page — the page only renders three of the seven blocks.

Three of the arguments are dropdowns whose values live in the extension:

``KEYS``
    18 named keys (``ArrowUp``, ``Enter``, ``Shift`` …), the space bar as a
    single space, and the printable characters; a *key* name, not a code point,
    so ``Enter`` is ``"Enter"`` and not ``13``.
``BUTTONS``
    ``0`` left, ``1`` middle, ``2`` right.
``AND_WAIT``
    ``"without waiting"`` (the *continue* item) or ``"and wait"``.

All three accept a reporter, and every block here is a command that waits out
its own duration, so a project using them runs in real time: ``--warp`` will
not make ``press … for 2 seconds`` return sooner.
"""

from __future__ import annotations

#: Extension id; blocks are ``cubesterKeySimulation_<opcode>`` in the project.
KEYSIM_EXTENSION = "cubesterKeySimulation"

#: The gallery URL. Unlike the gtm extension this one is *hosted* by
#: TurboWarp, and ``https://extensions.turbowarp.org/`` is one of the two
#: prefixes it loads unsandboxed, so a project that uses it needs no server.
KEYSIM_URL = "https://extensions.turbowarp.org/CubesterYT/KeySimulation.js"

#: The values the ``KEYS`` dropdown offers, in the extension's own order.
KEYSIM_KEYS = (
    " ", "ArrowUp", "ArrowDown", "ArrowRight", "ArrowLeft", "Enter",
    "Backspace", "Delete", "Shift", "CapsLock", "ScrollLock", "Control",
    "Escape", "Insert", "Home", "End", "PageUp", "PageDown",
    *"abcdefghijklmnopqrstuvwxyz",
    *"0123456789",
    "-", ",", ".", "`", "=", "[", "]", ";", "'", "/",
    "~", "+", "!", ":", "*", "#", "(", ")", "?", "<", ">", "@",
)

#: What each named key is *called* in the dropdown, and the value it stands
#: for. ``keySim.press("space", 0.1)`` would be a dead block -- the extension
#: compares against ``KeyboardEvent.key``, where the space bar is ``" "`` -- so
#: a literal is spelled the way the dropdown spells it and translated here.
KEYSIM_KEY_LABELS = {
    "space": " ",
    "up arrow": "ArrowUp",
    "down arrow": "ArrowDown",
    "left arrow": "ArrowLeft",
    "right arrow": "ArrowRight",
    "enter": "Enter",
    "backspace": "Backspace",
    "delete": "Delete",
    "shift": "Shift",
    "caps lock": "CapsLock",
    "scroll lock": "ScrollLock",
    "control": "Control",
    "escape": "Escape",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "page up": "PageUp",
    "page down": "PageDown",
}

#: The ``BUTTONS`` dropdown: the value is the number the DOM uses.
KEYSIM_BUTTONS = ("0", "1", "2")

#: The ``AND_WAIT`` dropdown.
KEYSIM_AND_WAIT = ("without waiting", "and wait")

#: extension id -> {namespace, blocks: {opcode: {type, args}}, menus}
KEYSIM_BLOCKS: dict[str, dict] = {
    KEYSIM_EXTENSION: {
        "namespace": "keySim",
        "blocks": {
            "pressKey": {
                "type": "COMMAND",
                "args": [
                    ("KEY", "STRING", "KEYS"),
                    ("SECONDS", "NUMBER", ""),
                    ("AND_WAIT", "STRING", "AND_WAIT"),
                ],
            },
            "holdKey": {"type": "COMMAND", "args": [("KEY", "STRING", "KEYS")]},
            "releaseKey": {"type": "COMMAND", "args": [("KEY", "STRING", "KEYS")]},
            "clickMouse": {
                "type": "COMMAND",
                "args": [
                    ("BUTTON", "STRING", "BUTTONS"),
                    ("X", "NUMBER", ""),
                    ("Y", "NUMBER", ""),
                    ("SECONDS", "NUMBER", ""),
                    ("AND_WAIT", "STRING", "AND_WAIT"),
                ],
            },
            "holdMouse": {
                "type": "COMMAND",
                "args": [
                    ("BUTTON", "STRING", "BUTTONS"),
                    ("X", "NUMBER", ""),
                    ("Y", "NUMBER", ""),
                ],
            },
            "releaseMouse": {"type": "COMMAND", "args": [("BUTTON", "STRING", "BUTTONS")]},
            "moveMouse": {
                "type": "COMMAND",
                "args": [("X", "NUMBER", ""), ("Y", "NUMBER", "")],
            },
        },
        "menus": {
            "KEYS": KEYSIM_KEYS,
            "BUTTONS": KEYSIM_BUTTONS,
            "AND_WAIT": KEYSIM_AND_WAIT,
        },
    },
}
