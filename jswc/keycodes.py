"""``Sensing.isKeyPressed(<number>)``: a code point, not a browser keyCode.

The numeric form is **the character's code point**, because that is what a
"number instead of a letter" means and it is stable across browsers:

| argument | key |
| --- | --- |
| `65` / `97` | `A` (the VM upper-cases letters) |
| `32` | `space` |
| `13` | `enter` |
| `8` / `127` | `backspace` / `delete` |
| `27` | `escape` |
| `0x0416` | the Cyrillic letter `Ж` |

Everything from 160 up is a character too — TurboWarp tracks any single
character as a key, including non-Latin ones, so accented, Cyrillic, Hebrew,
CJK … letters all work.

The gap **128–159** is the C1 control range: those code points have no
printable character at all, so the compiler uses them for the keys that have no
character — TurboWarp's "more keys".

Keys that have no character — the arrow keys and the modifiers — are written by
name (``isKeyPressed("left arrow")``). TurboWarp's VM adds a dozen such names
beyond Scratch's own six (``backspace``, ``delete``, ``shift``, ``caps lock``,
``scroll lock``, ``control``, ``escape``, ``insert``, ``home``, ``end``,
``page up``, ``page down``); see ``KEY_NAME`` in ``scratch-vm/src/io/keyboard.js``.

A few ASCII control codes have no key, and a few names the VM does not track
(``tab``, ``alt``, ``pause``, ``num lock``, function keys); those warn instead
of silently producing a test that can never be true.
"""

from __future__ import annotations

#: Control codes that name a real key.
CONTROL_CODES: dict[int, str] = {
    8: "backspace",
    9: "tab",
    10: "enter",
    13: "enter",
    27: "escape",
    32: "space",
    127: "delete",
}

#: Codes 128-159 hold the keys that have no character of their own.
MORE_KEYS: dict[int, str] = {
    128: "left arrow",
    129: "up arrow",
    130: "right arrow",
    131: "down arrow",
    132: "shift",
    133: "control",
    134: "caps lock",
    135: "scroll lock",
    136: "insert",
    137: "home",
    138: "end",
    139: "page up",
    140: "page down",
}

#: Key names TurboWarp's VM tracks besides single characters.
#: ``scratch-vm/src/io/keyboard.js`` -> ``KEY_NAME`` (including the ``tw:`` block).
SUPPORTED_NAMES: frozenset[str] = frozenset(
    {
        # ``getKeyIsDown`` special-cases this one: "is anything pressed".
        "any",
        "space", "left arrow", "up arrow", "right arrow", "down arrow", "enter",
        "backspace", "delete", "shift", "caps lock", "scroll lock", "control",
        "escape", "insert", "home", "end", "page up", "page down",
    }
)

#: Names that look right but that the VM never reports.
UNSUPPORTED_NAMES: frozenset[str] = frozenset({"tab", "alt", "pause", "num lock"})

MAX_CODE_POINT = 0x10FFFF


def resolve(code: int) -> tuple[str | None, str | None]:
    """Map a numeric argument to ``(key, warning)``.

    ``key`` is ``None`` when the number cannot be a key at all; ``warning`` is
    ``None`` when the mapping is exact.
    """
    if code in CONTROL_CODES:
        name = CONTROL_CODES[code]
        if name in SUPPORTED_NAMES:
            return name, None
        return name, (
            f"TurboWarp does not report the {name!r} key, so this test never fires"
        )

    if code < 32:
        return None, f"{code} is a control code, not a key"

    if code in MORE_KEYS:
        return MORE_KEYS[code], None

    if 128 <= code <= 159:
        return None, (
            f"{code} is not one of the more-keys codes "
            f"({', '.join(f'{c}={n}' for c, n in sorted(MORE_KEYS.items()))})"
        )

    if code > MAX_CODE_POINT:
        return None, f"{code} is past the last Unicode code point"

    try:
        char = chr(code)
    except ValueError:  # pragma: no cover - guarded by the range check
        return None, f"{code} is not a character"

    if char.isspace() or not char.isprintable():
        return None, f"{code} is not a printable key"

    # The VM compares upper-cased single characters, so emit the capital.
    key = char.upper() if char.isalpha() and len(char.upper()) == 1 else char
    return key, None


def name_warning(key: str) -> str | None:
    """Check a key written by name; returns a warning for ones the VM ignores."""
    if key in SUPPORTED_NAMES:
        return None
    if key in UNSUPPORTED_NAMES:
        return f"TurboWarp does not report the {key!r} key, so this test never fires"
    if len(key) > 1:
        return (
            f"{key!r} is not a key name TurboWarp knows; it is truncated to "
            f"{key[0]!r} (use a single character or one of "
            f"{', '.join(sorted(SUPPORTED_NAMES))})"
        )
    return None
