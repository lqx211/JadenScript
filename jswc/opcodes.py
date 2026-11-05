"""Scratch 3 primitives and the JadenScript → opcode registry.

Only opcodes verified against ``scratch-vm`` sources (``src/blocks`` and the
pen/music extensions) or against ``sample_np4.26_sb3/project.json`` are listed
here. Anything else produces a compile diagnostic instead of silently wrong
blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Input "primitive" ids used by the sb3 JSON format.
# ---------------------------------------------------------------------------
MATH_NUM = 4
POSITIVE_NUM = 5
WHOLE_NUM = 6
INTEGER = 7
ANGLE = 8
COLOR = 9
TEXT = 10
BROADCAST = 11
VAR = 12
LIST = 13
BOOLEAN = 2  # not a real primitive: marks a boolean-shaped input slot
MENU = 100  # internal: input filled by a shadow dropdown block
FIELD = 101  # internal: the argument is written into a block field (dropdown)

DEFAULT_SHADOWS: dict[int, str] = {
    MATH_NUM: "",
    POSITIVE_NUM: "",
    WHOLE_NUM: "",
    INTEGER: "",
    ANGLE: "",
    COLOR: "#a2a2a2",  # value the reference project uses for pen colour
    TEXT: "",
}

_COLOR_HEX_RE = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def color_text(value: str) -> str:
    """Normalise a colour input to the ``#rrggbb`` text the sb3 schema requires.

    Scratch stores a colour primitive as ``#rrggbb``; a *number* is what the pen
    block accepts at run time (24-bit RGB), so a literal written that way —
    ``0x24304a`` or ``2371658`` — is converted here. Without this the build
    emits ``[9, "2371658"]``, which reads fine in the editor but makes the
    project fail sb3 schema validation. Unreadable text falls back to the
    default grey instead of writing an invalid primitive.
    """
    text = str(value).strip()
    match = _COLOR_HEX_RE.match(text)
    if match is not None:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(char * 2 for char in digits)
        return "#" + digits.lower()
    try:
        number = int(text, 0)
    except ValueError:
        try:
            number = int(float(text))
        except (TypeError, ValueError):
            return DEFAULT_SHADOWS[COLOR]
    return "#%06x" % (number & 0xFFFFFF)


@dataclass(frozen=True)
class Param:
    """One input slot of a block."""

    name: str
    prim: int
    menu: str | None = None  # shadow block used for a dropdown
    field: str | None = None  # field name inside that shadow block


@dataclass(frozen=True)
class Command:
    opcode: str
    params: tuple[Param, ...] = ()
    fields: tuple[tuple[str, str], ...] = ()
    extension: str = ""


@dataclass(frozen=True)
class Reporter:
    opcode: str
    params: tuple[Param, ...] = ()
    fields: tuple[tuple[str, str], ...] = ()
    extension: str = ""


@dataclass(frozen=True)
class Property:
    """A settable pseudo-variable such as ``Motion.x``.

    ``set_input`` and ``change_input`` differ for motion: ``motion_setx`` takes
    ``X`` while ``motion_changexby`` takes ``DX``.
    """

    get: str
    set: str
    change: str
    set_input: str
    change_input: str | None = None
    prim: int = MATH_NUM
    extension: str = ""

    @property
    def change_slot(self) -> str:
        return self.change_input or self.set_input


COMMANDS: dict[str, Command] = {
    # -- pen ---------------------------------------------------------------
    "Pen.eraseAll": Command("pen_clear", extension="pen"),
    "Pen.clear": Command("pen_clear", extension="pen"),
    "Pen.penDown": Command("pen_penDown", extension="pen"),
    "Pen.penUp": Command("pen_penUp", extension="pen"),
    "Pen.stamp": Command("pen_stamp", extension="pen"),
    "Pen.setPenSize": Command("pen_setPenSizeTo", (Param("SIZE", MATH_NUM),), extension="pen"),
    "Pen.changePenSize": Command(
        "pen_changePenSizeBy", (Param("SIZE", MATH_NUM),), extension="pen"
    ),
    "Pen.setPenColor": Command(
        "pen_setPenColorToColor", (Param("COLOR", COLOR),), extension="pen"
    ),
    "Pen.setPenColorToColor": Command(
        "pen_setPenColorToColor", (Param("COLOR", COLOR),), extension="pen"
    ),
    "Pen.setPenColorParamTo": Command(
        "pen_setPenColorParamTo",
        (
            Param("COLOR_PARAM", MENU, "pen_menu_colorParam", "colorParam"),
            Param("VALUE", MATH_NUM),
        ),
        extension="pen",
    ),
    "Pen.changePenColorParam": Command(
        "pen_changePenColorParamBy",
        (
            Param("COLOR_PARAM", MENU, "pen_menu_colorParam", "colorParam"),
            Param("VALUE", MATH_NUM),
        ),
        extension="pen",
    ),
    # -- music -------------------------------------------------------------
    "Music.playNote": Command(
        "music_playNoteForBeats",
        # NOTE is a dropdown shadow block (``note``), so a literal note keeps
        # its own block instead of collapsing into a bare number.
        (Param("NOTE", MENU, "note", "NOTE"), Param("BEATS", MATH_NUM)),
        extension="music",
    ),
    "Music.playDrum": Command(
        "music_playDrumForBeats",
        (Param("DRUM", MENU, "music_menu_DRUM", "DRUM"), Param("BEATS", MATH_NUM)),
        extension="music",
    ),
    "Music.restForBeats": Command(
        "music_restForBeats", (Param("BEATS", MATH_NUM),), extension="music"
    ),
    "Music.setInstr": Command(
        "music_setInstrument",
        (Param("INSTRUMENT", MENU, "music_menu_INSTRUMENT", "INSTRUMENT"),),
        extension="music",
    ),
    "Music.setInstrument": Command(
        "music_setInstrument",
        (Param("INSTRUMENT", MENU, "music_menu_INSTRUMENT", "INSTRUMENT"),),
        extension="music",
    ),
    "Music.setTempo": Command("music_setTempo", (Param("TEMPO", MATH_NUM),), extension="music"),
    "Music.changeTempo": Command(
        "music_changeTempo", (Param("TEMPO", MATH_NUM),), extension="music"
    ),
    # -- sound -------------------------------------------------------------
    "Sound.playSound": Command(
        "sound_play", (Param("SOUND_MENU", MENU, "sound_sounds_menu", "SOUND_MENU"),)
    ),
    "Sound.playSoundUntilDone": Command(
        "sound_playuntildone", (Param("SOUND_MENU", MENU, "sound_sounds_menu", "SOUND_MENU"),)
    ),
    "Sound.stopAllSounds": Command("sound_stopallsounds"),
    # ``Music.stopAll()`` reads naturally next to the other Music blocks; it is
    # the same block, so it compiles to the same opcode.
    "Music.stopAll": Command("sound_stopallsounds"),
    "Music.stopAllSounds": Command("sound_stopallsounds"),
    "Sound.setVolume": Command("sound_setvolumeto", (Param("VOLUME", MATH_NUM),)),
    "Sound.changeVolume": Command("sound_changevolumeby", (Param("VOLUME", MATH_NUM),)),
    # -- control -----------------------------------------------------------
    "Control.wait": Command("control_wait", (Param("DURATION", POSITIVE_NUM),)),
    "Control.waitUntil": Command("control_wait_until", (Param("CONDITION", BOOLEAN),)),
    "Control.stopAll": Command("control_stop", fields=(("STOP_OPTION", "all"),)),
    "Control.stopThisScript": Command("control_stop", fields=(("STOP_OPTION", "this script"),)),
    "Control.stopOtherScripts": Command(
        "control_stop", fields=(("STOP_OPTION", "other scripts in sprite"),)
    ),
    "Control.deleteThisClone": Command("control_delete_this_clone"),
    # -- sensing -----------------------------------------------------------
    "Sensing.ask": Command("sensing_askandwait", (Param("QUESTION", TEXT),)),
    "Sensing.resetTimer": Command("sensing_resettimer"),
    # -- looks -------------------------------------------------------------
    "Looks.show": Command("looks_show"),
    "Looks.hide": Command("looks_hide"),
    "Looks.nextCostume": Command("looks_nextcostume"),
    "Looks.setSize": Command("looks_setsizeto", (Param("SIZE", MATH_NUM),)),
    "Looks.changeSize": Command("looks_changesizeby", (Param("CHANGE", MATH_NUM),)),
    "Looks.say": Command("looks_say", (Param("MESSAGE", TEXT),)),
    "Looks.sayForSecs": Command(
        "looks_sayforsecs", (Param("MESSAGE", TEXT), Param("SECS", MATH_NUM))
    ),
    "Looks.think": Command("looks_think", (Param("MESSAGE", TEXT),)),
    # -- motion ------------------------------------------------------------
    "Motion.move": Command("motion_movesteps", (Param("STEPS", MATH_NUM),)),
    "Motion.turnRight": Command("motion_turnright", (Param("DEGREES", MATH_NUM),)),
    "Motion.turnLeft": Command("motion_turnleft", (Param("DEGREES", MATH_NUM),)),
    "Motion.goto": Command("motion_goto", (Param("TO", MENU, "motion_goto_menu", "TO"),)),
    "Motion.setXY": Command("motion_gotoxy", (Param("X", MATH_NUM), Param("Y", MATH_NUM))),
    "Looks.switchCostume": Command(
        "looks_switchcostumeto", (Param("COSTUME", MENU, "looks_costume", "COSTUME"),)
    ),
    "Looks.switchBackdrop": Command(
        "looks_switchbackdropto", (Param("BACKDROP", MENU, "looks_backdrops", "BACKDROP"),)
    ),
    "Looks.switchBackdropAndWait": Command(
        "looks_switchbackdroptoandwait",
        (Param("BACKDROP", MENU, "looks_backdrops", "BACKDROP"),),
    ),
    "Looks.setEffect": Command(
        "looks_seteffectto", (Param("EFFECT", FIELD), Param("VALUE", MATH_NUM))
    ),
    "Looks.clearGraphicEffects": Command("looks_cleargraphiceffects"),
    "Looks.goToFront": Command("looks_gotofrontback", fields=(("FRONT_BACK", "front"),)),
    "Looks.goToBack": Command("looks_gotofrontback", fields=(("FRONT_BACK", "back"),)),
    "Sound.setEffect": Command(
        "sound_seteffectto",
        (Param("EFFECT", FIELD), Param("VALUE", MATH_NUM)),
    ),
    "Control.createClone": Command(
        "control_create_clone_of",
        (Param("CLONE_OPTION", MENU, "control_create_clone_of_menu", "CLONE_OPTION"),),
    ),
    "Control.clone": Command(
        "control_create_clone_of",
        (Param("CLONE_OPTION", MENU, "control_create_clone_of_menu", "CLONE_OPTION"),),
    ),
    "Motion.glideTo": Command(
        "motion_glidesecstoxy",
        (Param("SECS", MATH_NUM), Param("X", MATH_NUM), Param("Y", MATH_NUM)),
    ),
}

REPORTERS: dict[str, Reporter] = {
    # -- sensing -----------------------------------------------------------
    "Sensing.timer": Reporter("sensing_timer"),
    "Sensing.answer": Reporter("sensing_answer"),
    "Sensing.isOnline": Reporter("sensing_online"),
    "Sensing.loudness": Reporter("sensing_loud"),
    "Sensing.mouseX": Reporter("sensing_mousex"),
    "Sensing.mouseY": Reporter("sensing_mousey"),
    "Sensing.daysSince2000": Reporter("sensing_dayssince2000"),
    "Sensing.username": Reporter("sensing_username"),
    # -- music -------------------------------------------------------------
    "Music.tempo": Reporter("music_getTempo", extension="music"),
    # -- looks -------------------------------------------------------------
    "Looks.size": Reporter("looks_size"),
    "Looks.costumeNumber": Reporter(
        "looks_costumenumbername", fields=(("NUMBER_NAME", "number"),)
    ),
    "Looks.costumeName": Reporter(
        "looks_costumenumbername", fields=(("NUMBER_NAME", "name"),)
    ),
    "Looks.backdropNumber": Reporter(
        "looks_backdropnumbername", fields=(("NUMBER_NAME", "number"),)
    ),
    "Looks.backdropName": Reporter(
        "looks_backdropnumbername", fields=(("NUMBER_NAME", "name"),)
    ),
    # -- motion ------------------------------------------------------------
    "Motion.xPosition": Reporter("motion_xposition"),
    "Motion.yPosition": Reporter("motion_yposition"),
    # -- sound -------------------------------------------------------------
    "Sound.volume": Reporter("sound_volume"),
    # -- operators ---------------------------------------------------------
    "Operators.round": Reporter("operator_round", (Param("NUM", MATH_NUM),)),
    "Operators.math": Reporter(
        "operator_mathop", (Param("OPERATOR", FIELD), Param("NUM", MATH_NUM))
    ),
    "Sensing.touching": Reporter(
        "sensing_touchingobject",
        (Param("TOUCHINGOBJECTMENU", MENU, "sensing_touchingobjectmenu", "TOUCHINGOBJECTMENU"),),
    ),
    "Sensing.keyPressed": Reporter(
        "sensing_keypressed",
        (Param("KEY_OPTION", MENU, "sensing_keyoptions", "KEY_OPTION"),),
    ),
    "Sensing.isKeyPressed": Reporter(
        "sensing_keypressed",
        (Param("KEY_OPTION", MENU, "sensing_keyoptions", "KEY_OPTION"),),
    ),
    "Sensing.mouseDown": Reporter("sensing_mousedown"),
    "Sensing.touchingColor": Reporter("sensing_touchingcolor", (Param("COLOR", COLOR),)),
    "Sensing.distanceTo": Reporter(
        "sensing_distanceto",
        (Param("DISTANCETOMENU", MENU, "sensing_distancetomenu", "DISTANCETOMENU"),),
    ),
    "Sensing.of": Reporter(
        "sensing_of",
        (
            Param("PROPERTY", FIELD),
            Param("OBJECT", MENU, "sensing_of_object_menu", "OBJECT"),
        ),
    ),
    "Operators.length": Reporter("operator_length", (Param("STRING", TEXT),)),
    "Operators.letterOf": Reporter(
        "operator_letter_of", (Param("LETTER", MATH_NUM), Param("STRING", TEXT))
    ),
    "Operators.contains": Reporter(
        "operator_contains", (Param("STRING1", TEXT), Param("STRING2", TEXT))
    ),
    "Operators.random": Reporter(
        "operator_random", (Param("FROM", MATH_NUM), Param("TO", MATH_NUM))
    ),
}

PROPERTIES: dict[str, Property] = {
    "Motion.x": Property(
        "motion_xposition", "motion_setx", "motion_changexby", "X", "DX"
    ),
    "Motion.y": Property(
        "motion_yposition", "motion_sety", "motion_changeyby", "Y", "DY"
    ),
    "Motion.direction": Property(
        "motion_direction",
        "motion_pointindirection",
        "motion_turnright",
        "DIRECTION",
        "DEGREES",
        ANGLE,
    ),
    "Sound.volume": Property(
        "sound_volume", "sound_setvolumeto", "sound_changevolumeby", "VOLUME"
    ),
    "Looks.size": Property("looks_size", "looks_setsizeto", "looks_changesizeby", "SIZE"),
}

# Community extensions are not built into the TurboWarp editor: the project
# must list them in ``extensionURLs`` or loading fails with
# "Unknown extension: <id>". Built-ins (pen, music, …) need no URL.
EXTENSION_URLS: dict[str, str] = {
    "utilities": "https://extensions.turbowarp.org/utilities.js",
    "lmsTempVars2": "https://extensions.turbowarp.org/Lily/TempVariables2.js",
    "skyhigh173JSON": "https://extensions.turbowarp.org/Skyhigh173/json.js",
    "lmsTimers": "https://extensions.turbowarp.org/Lily/MoreTimers.js",
    "lmscomments": "https://extensions.turbowarp.org/Lily/CommentBlocks.js",
    "sipcconsole": "https://extensions.turbowarp.org/-SIPC-/consoles.js",
    "runtimeoptions": "https://extensions.turbowarp.org/runtime-options.js",
    "gsaWebsocket": "https://extensions.turbowarp.org/godslayerakp/ws.js",
    "lmsMoreEvents": "https://extensions.turbowarp.org/Lily/MoreEvents.js",
    "pointerlock": "https://extensions.turbowarp.org/pointerlock.js",
    # the board's own memory card, served by the project server.
    # The port matters: TurboWarp only loads an extension without asking for
    # permission from `https://extensions.turbowarp.org/` and `localhost:8000`;
    # any other URL blocks the project behind a modal that can deadlock the
    # editor, which looks like "Downloading assets (2/2) …" forever.
    "rv64mem": "http://localhost:8000/tools/rv64mem.js",
}

#: Extension URL prefixes TurboWarp loads without a permission prompt.
TRUSTED_EXTENSION_PREFIXES: tuple[str, ...] = (
    "https://extensions.turbowarp.org/",
    "http://localhost:8000/",
)

# Global helper functions available without a namespace.
GLOBAL_FUNCS: frozenset[str] = frozenset(
    {"string", "sleep", "len", "float", "join", "repeat", "isTurboWarp", "isCompiled"}
)

LIST_METHODS: frozenset[str] = frozenset(
    {
        "add",
        "append",
        "push",
        "clear",
        "get",
        "set",
        "replace",
        "insert",
        "delete",
        "remove",
        "length",
        "contains",
        "indexOf",
    }
)

# name -> (opcode, input names) for list methods that are statements
LIST_COMMANDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "add": ("data_addtolist", ("ITEM",)),
    "append": ("data_addtolist", ("ITEM",)),
    "push": ("data_addtolist", ("ITEM",)),
    "clear": ("data_deletealloflist", ()),
    "set": ("data_replaceitemoflist", ("INDEX", "ITEM")),
    "replace": ("data_replaceitemoflist", ("INDEX", "ITEM")),
    "insert": ("data_insertatlist", ("INDEX", "ITEM")),
    "delete": ("data_deleteoflist", ("INDEX",)),
    "remove": ("data_deleteoflist", ("INDEX",)),
}

LIST_REPORTERS: dict[str, str] = {
    "get": "data_itemoflist",
    "length": "data_lengthoflist",
    "contains": "data_listcontainsitem",
    "indexOf": "data_itemnumoflist",
}
