"""Regressions found while round-tripping a large community project.

Each case reproduces a real block shape from ``TheMast3D`` (a 13k block 3D
engine written in Scratch): TurboWarp's compiler writes ``[1, null]`` inputs,
Scratch allows duplicate and keyword state names, sprites have spaces in their
names, and extension menus store their value in a shadow block.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from jswc.codegen import Codegen
from jswc.decompile import Decompiler
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import build_project_dict
from jswc.sb3_read import LoadedProject


def compile_source(source: str, **kwargs):
    kwargs.setdefault("features", {"commentMetadata"})
    kwargs.setdefault("allow_undefined", True)
    codegen = Codegen(Parser(tokenize(source)).parse(), **kwargs)
    project = codegen.compile()
    data, _ = build_project_dict(project)
    return project, data


def decompile(data: dict) -> tuple[str, Decompiler]:
    decompiler = Decompiler(LoadedProject(data=data))
    return decompiler.decompile(), decompiler


def rebuild(source: str) -> dict:
    """Decompile ``source``'s project and compile the result again."""
    _, data = compile_source(source)
    text, _ = decompile(data)
    _, again = compile_source(text)
    return again


def any_warnings(source: str) -> list[str]:
    """Diagnostics that mention ``any`` (the compiler records them itself)."""
    gen = Codegen(Parser(tokenize(source)).parse(), features={"commentMetadata"}, allow_undefined=True)
    gen.compile()
    return [d for d in gen.diagnostics if "any" in d]


def target(data: dict, name: str) -> dict:
    return next(t for t in data["targets"] if t["name"] == name)


def opcodes(data: dict, name: str) -> list[str]:
    return [b["opcode"] for b in target(data, name)["blocks"].values() if isinstance(b, dict)]


class NullInputTests(unittest.TestCase):
    """``control_if_else`` with ``SUBSTACK2: [1, null]`` must not crash."""

    def test_empty_input_becomes_an_empty_literal(self):
        source = """
class main { // Sprite: main (0,0) size=100 direction=90 show=1
    public void greenFlag() {
        if (x > 0) { Looks.say("hi"); }
    }
}
"""
        _, data = compile_source(source)
        sprite = target(data, "main")
        for block in sprite["blocks"].values():
            if isinstance(block, dict) and block.get("opcode") == "control_if":
                block["inputs"]["SUBSTACK2"] = [1, None]
                block["inputs"]["CONDITION"] = [1, None]
        text, _ = decompile(data)          # must not raise
        self.assertIn('if ("")', text)


class SpriteNameTests(unittest.TestCase):
    def test_names_with_spaces_survive_the_round_trip(self):
        source = """
class obj_loader { // Sprite: "obj loader" (1,2) size=100 direction=90 show=1
    public void greenFlag() {
        Looks.say("hi");
    }
}
"""
        again = rebuild(source)
        names = [t["name"] for t in again["targets"]]
        self.assertIn("obj loader", names)

    def test_decompiler_quotes_names_with_spaces(self):
        _, data = compile_source(
            'class obj_loader { // Sprite: "obj loader" (0,0) size=100 show=1\n'
            "    public void greenFlag() { Looks.say(1); }\n}\n"
        )
        text, _ = decompile(data)
        self.assertIn('obj_loader: name="obj loader"', text)


class KeywordAndDuplicateNameTests(unittest.TestCase):
    def test_keyword_variable_is_renamed(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { return_ = 3; }\n}\n"
        )
        sprite = target(data, "main")
        sprite["variables"] = {"vid": ["return", 3]}
        text, _ = decompile(data)
        self.assertIn("return_", text)
        again = compile_source(text)[1]
        self.assertEqual([e[0] for e in target(again, "main")["variables"].values()], ["return_"])

    def test_duplicate_variables_both_survive(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { t = 1; }\n}\n"
        )
        sprite = target(data, "main")
        sprite["variables"] = {"a": ["t", 5], "b": ["t", 9]}
        text, decompiler = decompile(data)
        self.assertTrue(any("is used twice" in note for note in decompiler.diagnostics))
        self.assertIn("private t = 5;", text)
        self.assertIn("private t_2 = 9;", text)
        again = compile_source(text)[1]
        values = sorted(e[1] for e in target(again, "main")["variables"].values())
        self.assertEqual(values, [5, 9])


class ClassStateImportTests(unittest.TestCase):
    def test_class_level_state_file_declares_the_name(self):
        with self.subTest("jsa"):
            import tempfile

            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            root = Path(tmp.name)
            (root / "lists").mkdir()
            (root / "lists" / "data.jsa").write_text('["a","b"]', encoding="utf-8")
            source = (
                'class main { // Sprite: main (0,0) size=100 show=1\n'
                '    import "lists/data.jsa" as data;\n'
                "    public void greenFlag() { log(Operators.length(data)); }\n"
                "}\n"
            )
            project, data = compile_source(source, base_dir=root)
            lists = target(data, "main")["lists"]
            self.assertEqual([e[1] for e in lists.values()], [["a", "b"]])

    def test_list_read_as_a_value_is_the_contents_reporter(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    private data = [];\n"
            "    public void greenFlag() { log(Operators.length(data)); }\n}\n"
        )
        self.assertIn("data_listcontents", opcodes(data, "main"))
        self.assertNotIn("data_variable", opcodes(data, "main"))


class ReceiverTests(unittest.TestCase):
    def test_receiver_annotation_recreates_the_hat(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    public void work() { // proccode do work // receiver at 10,20 "my message"\n'
            '        Looks.say("hi");\n    }\n}\n'
        )
        _, data = compile_source(source)
        sprite = target(data, "main")
        hats = [b for b in sprite["blocks"].values()
                if isinstance(b, dict) and b.get("opcode") == "event_whenbroadcastreceived"]
        self.assertEqual(len(hats), 1)
        self.assertEqual(hats[0]["fields"]["BROADCAST_OPTION"][0], "my message")
        self.assertEqual((hats[0]["x"], hats[0]["y"]), (10, 20))

    def test_receiver_keeps_a_message_that_is_not_a_plain_name(self):
        # The method name is sanitised (``update_dynamic_geometry``), so the
        # broadcast text must still be written out — otherwise the hat listens
        # for the underscored name and never fires (TheMast3D's doors).
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    public void update_dynamic_geometry() { // receiver at 5,6 "update dynamic geometry"\n'
            "        Control.wait(0);\n    }\n}\n"
        )
        _, data = compile_source(source)
        text, _ = decompile(data)
        self.assertIn('"update dynamic geometry"', text)
        again = compile_source(text)[1]
        hat = next(b for b in target(again, "main")["blocks"].values()
                   if isinstance(b, dict) and b.get("opcode") == "event_whenbroadcastreceived")
        self.assertEqual(hat["fields"]["BROADCAST_OPTION"][0], "update dynamic geometry")


class DuplicateProcedureTests(unittest.TestCase):
    """Two custom blocks can flatten to the same source name.

    Scratch identifies a procedure by its whole label, JadenScript by its
    name, so ``rotate … around x axis`` and ``rotate … around z axis`` both
    become ``rotate`` and every call ran the same body.
    """

    SOURCE = (
        "class main { // Sprite: main (0,0) size=100 show=1\n"
        "    private void rotA(x, y) { // proccode rotate %s %s around x axis\n"
        "        vert = x;\n"
        "    }\n"
        "    private void rotB(x, y) { // proccode rotate %s %s around z axis\n"
        "        vert = y;\n"
        "    }\n"
        "    public void greenFlag() {\n"
        "        rotA(1, 2);\n"
        "        rotB(3, 4);\n"
        "    }\n}\n"
    )

    def test_decompiled_names_are_unique(self):
        text, decompiler = decompile(compile_source(self.SOURCE)[1])
        names = sorted(decompiler.proc_names.values())
        self.assertEqual(len(names), len(set(names)), names)
        self.assertIn("rotate", names)
        self.assertIn("rotate_2", names)

    def test_each_call_keeps_its_own_procedure(self):
        again = compile_source(decompile(compile_source(self.SOURCE)[1])[0])[1]
        proccodes = sorted(
            (b.get("mutation") or {}).get("proccode")
            for b in target(again, "main")["blocks"].values()
            if isinstance(b, dict) and b.get("opcode") == "procedures_prototype"
        )
        self.assertEqual(proccodes, ["rotate %s %s around x axis", "rotate %s %s around z axis"])
        calls = sorted(
            (b.get("mutation") or {}).get("proccode")
            for b in target(again, "main")["blocks"].values()
            if isinstance(b, dict) and b.get("opcode") == "procedures_call"
        )
        self.assertEqual(calls, ["rotate %s %s around x axis", "rotate %s %s around z axis"])


class BlockShapeTests(unittest.TestCase):
    def test_pen_colour_param_writes_the_menu_input(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    public void greenFlag() { Pen.setPenColorParamTo("transparency", 30); }\n}\n'
        )
        sprite = target(data, "main")
        block = next(b for b in sprite["blocks"].values()
                     if isinstance(b, dict) and b["opcode"] == "pen_setPenColorParamTo")
        self.assertIn("COLOR_PARAM", block["inputs"])
        shadow = sprite["blocks"][block["inputs"]["COLOR_PARAM"][1]]
        self.assertEqual(shadow["fields"]["colorParam"][0], "transparency")

    def test_sensing_of_property_follows_a_rename(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    public void greenFlag() { log(Sensing.of("x", "Stage")); }\n}\n'
        )
        _, data = compile_source(source)
        stage = target(data, "Stage")
        stage["variables"] = {"vid": ["my value", 1]}
        sprite = target(data, "main")
        block = next(b for b in sprite["blocks"].values()
                     if isinstance(b, dict) and b["opcode"] == "sensing_of")
        block["fields"]["PROPERTY"] = ["my value", None]
        text, _ = decompile(data)
        self.assertIn('Sensing.of("my_value", "Stage")', text)


class StageForkTests(unittest.TestCase):
    def test_show_variable_does_not_fork_sprite_state_onto_the_stage(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    private loading = "hi";\n'
            "    public void greenFlag() { Variables.showVariable(loading); }\n}\n"
        )
        _, data = compile_source(source)
        self.assertEqual([e[0] for e in target(data, "Stage")["variables"].values()], [])
        self.assertEqual([e[0] for e in target(data, "main")["variables"].values()], ["loading"])


class SensingTests(unittest.TestCase):
    def test_sensing_reporters_round_trip(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() {\n"
            '        log(Sensing.distanceTo("mouse-pointer"));\n'
            '        log(Sensing.touchingColor("#ff0000"));\n'
            '        log(Sensing.of("x position", "Stage"));\n'
            "    }\n}\n"
        )
        _ , data = compile_source(source)
        ops = opcodes(data, "main")
        for opcode in ("sensing_distanceto", "sensing_touchingcolor", "sensing_of"):
            self.assertIn(opcode, ops)
        text, _ = decompile(data)
        self.assertIn('Sensing.distanceTo("mouse-pointer")', text)
        self.assertIn('Sensing.touchingColor("#ff0000")', text)
        self.assertIn('Sensing.of("x position", "Stage")', text)

    def test_stop_other_scripts(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { Control.stopOtherScripts(); }\n}\n"
        )
        block = next(b for b in target(data, "main")["blocks"].values()
                     if isinstance(b, dict) and b["opcode"] == "control_stop")
        self.assertEqual(block["fields"]["STOP_OPTION"][0], "other scripts in sprite")
        text, _ = decompile(data)
        self.assertIn("Control.stopOtherScripts();", text)


class LooseBlockTests(unittest.TestCase):
    def test_loose_block_that_folds_to_a_literal_is_kept(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    loose { // at 10,20\n"
            '        join("", "");\n'
            "    }\n}\n"
        )
        project, data = compile_source(source)
        text, _ = decompile(data)
        self.assertIn('block("operator_join")', text)
        again = compile_source(text)[1]
        self.assertIn("operator_join", opcodes(again, "main"))

    def test_block_without_fields_is_accepted(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    public void greenFlag() { Looks.say(block("operator_join")); }\n}\n'
        )
        self.assertIn("operator_join", opcodes(data, "main"))


class LooksTests(unittest.TestCase):
    def test_effect_and_layer_blocks_round_trip(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() {\n"
            '        Looks.setEffect("ghost", 50);\n'
            "        Looks.clearGraphicEffects();\n"
            "        Looks.goToFront();\n"
            "        Looks.goToBack();\n"
            '        Looks.switchBackdrop("backdrop1");\n'
            "    }\n}\n"
        )
        _, data = compile_source(source)
        ops = opcodes(data, "main")
        self.assertIn("looks_seteffectto", ops)
        self.assertIn("looks_cleargraphiceffects", ops)
        self.assertEqual(ops.count("looks_gotofrontback"), 2)
        text, _ = decompile(data)
        self.assertIn('Looks.setEffect("ghost", 50);', text)
        self.assertIn("Looks.goToFront();", text)
        self.assertIn("Looks.goToBack();", text)


class VariableBeatsListTests(unittest.TestCase):
    """A global variable and a sprite list may share a name in Scratch.

    ``TheMast3D`` has a global ``f`` (focal length) and a sprite-local list
    called ``f``.  A bare ``f`` in a value position must read the variable —
    reading the (empty) list instead made every projected x/y zero, which drew
    the whole scene on top of the origin.
    """

    SOURCE = (
        "public f = 0.5;\n"
        "class main { // Sprite: main (0,0) size=100 show=1\n"
        "    private f = [1, 2, 3];\n"
        "    public void greenFlag() {\n"
        "        public projected = f * 2;\n"
        "    }\n}\n"
    )

    def test_bare_name_reads_the_variable(self):
        _, data = compile_source(self.SOURCE)
        block = next(b for b in target(data, "main")["blocks"].values()
                     if isinstance(b, dict) and b["opcode"] == "data_variable"
                     and b["fields"]["VARIABLE"][0] == "f")
        self.assertEqual(block["fields"]["VARIABLE"][0], "f")

    def test_list_methods_still_reach_the_list(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    private f = [1, 2, 3];\n"
            "    public void greenFlag() {\n"
            "        public first = f.get(1);\n"
            "        public count = f.length();\n"
            "    }\n}\n"
        )
        _, data = compile_source(source)
        ops = opcodes(data, "main")
        self.assertIn("data_itemoflist", ops)
        self.assertIn("data_lengthoflist", ops)

    def test_list_keeps_its_name_when_only_methods_are_used(self):
        # ``f`` is never read bare here, so the list keeps its name and the
        # decompiler stays quiet about it.
        source = (
            "public f = 0.5;\n"
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    private f = [1, 2, 3];\n"
            "    public void greenFlag() {\n"
            "        public first = f.get(1);\n"
            "        public projected = f * 2;\n"
            "    }\n}\n"
        )
        text, decompiler = decompile(compile_source(source)[1])
        self.assertIn("private f = [1, 2, 3];", text)
        self.assertEqual(decompiler.list_renames.get("main", {}), {})

    def test_bare_list_read_is_renamed_when_a_variable_steals_the_name(self):
        # A list contents read must stay a list read even though the class also
        # has a variable of that name, so the list gets a new name.
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    private f = [1, 2, 3];\n"
            "    public void greenFlag() {\n"
            "        public joined = string(f);\n"
            "    }\n}\n"
        )
        _, data = compile_source(source)
        main = target(data, "main")
        self.assertIn("data_listcontents", opcodes(data, "main"))
        main["variables"]["clash"] = ["f", 0]  # a variable called ``f`` appears
        text, decompiler = decompile(data)
        self.assertIn("private f_ = [1, 2, 3];", text)
        self.assertEqual(decompiler.list_renames.get("main", {}), {"f": "f_"})
        again = compile_source(text)[1]
        self.assertIn("data_listcontents", opcodes(again, "main"))


class AliasTests(unittest.TestCase):
    """Friendlier spellings that compile to the same block."""

    def test_music_stop_all_is_sound_stop_all_sounds(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { Music.stopAll(); }\n}\n"
        )
        self.assertIn("sound_stopallsounds", opcodes(data, "main"))

        _, canonical = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { Sound.stopAllSounds(); }\n}\n"
        )
        self.assertEqual(opcodes(data, "main"), opcodes(canonical, "main"))

    def test_control_clone_is_create_clone(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    public void greenFlag() { Control.clone("main"); }\n}\n'
        )
        _, data = compile_source(source)
        ops = opcodes(data, "main")
        self.assertIn("control_create_clone_of", ops)
        self.assertIn("control_create_clone_of_menu", ops)

    def test_sensing_is_key_pressed_is_key_pressed(self):
        alias = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    public void greenFlag() { if (Sensing.isKeyPressed("a")) { Looks.say("a"); } }\n}\n'
        )
        canonical = alias.replace("Sensing.isKeyPressed", "Sensing.keyPressed")
        _, a = compile_source(alias)
        _, b = compile_source(canonical)
        self.assertIn("sensing_keypressed", opcodes(a, "main"))
        self.assertEqual(opcodes(a, "main"), opcodes(b, "main"))


class AnyKeywordTests(unittest.TestCase):
    """``any`` is the "any key" dropdown option.

    It always compiles to the text ``any``; using it outside a call's argument
    list is almost certainly a typo, so the compiler warns there.
    """

    def test_any_in_a_key_menu_is_silent(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() {\n"
            "        if (Sensing.keyPressed(any)) { Looks.say(\"key\"); }\n"
            "    }\n}\n"
        )
        self.assertEqual(any_warnings(source), [])
        _, data = compile_source(source)
        sprite = target(data, "main")
        menu = next(b for b in sprite["blocks"].values()
                    if isinstance(b, dict) and b["opcode"] == "sensing_keyoptions")
        self.assertEqual(menu["fields"]["KEY_OPTION"][0], "any")

    def test_any_in_another_call_is_an_argument_too(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    private notes = [];\n"
            "    public void greenFlag() { notes.add(any); }\n}\n"
        )
        self.assertEqual(any_warnings(source), [])

    def test_any_outside_arguments_warns_but_still_compiles(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { public marker = any; }\n}\n"
        )
        warnings = any_warnings(source)
        self.assertTrue(warnings, "expected a warning")
        _, data = compile_source(source)
        stage = target(data, "Stage")
        values = {v[0]: v[1] for v in stage["variables"].values()}
        self.assertEqual(values["marker"], "any")

    def test_any_round_trips_as_text(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { if (Sensing.keyPressed(any)) { Looks.say(\"k\"); } }\n}\n"
        )
        text, _ = decompile(compile_source(source)[1])
        self.assertIn('Sensing.keyPressed("any")', text)
        again = compile_source(text)[1]
        self.assertIn("sensing_keypressed", opcodes(again, "main"))



class KeyCodeTests(unittest.TestCase):
    """``Sensing.isKeyPressed(<number>)`` takes a code point.

    TurboWarp only tracks a fixed set of key *names*, so the compiler resolves
    the number to a name at compile time rather than leaving the VM to guess.
    """

    def menus(self, source: str) -> list[str]:
        _, data = compile_source(source)
        sprite = target(data, "main")
        return [
            b["fields"]["KEY_OPTION"][0]
            for b in sprite["blocks"].values()
            if isinstance(b, dict) and b["opcode"] == "sensing_keyoptions"
        ]

    def compile_one(self, argument: str) -> str:
        return self.menus(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            f"    public void greenFlag() {{ if (Sensing.isKeyPressed({argument})) {{ Looks.say(\"x\"); }} }}\n}}\n"
        )[0]

    def test_ascii_letter(self):
        for argument in ("65", "0x41", "0b1000001"):
            with self.subTest(argument=argument):
                self.assertEqual(self.compile_one(argument), "A")

    def test_lowercase_is_upper_cased(self):
        self.assertEqual(self.compile_one("97"), "A")

    def test_control_codes_become_names(self):
        self.assertEqual(self.compile_one("32"), "space")
        self.assertEqual(self.compile_one("13"), "enter")
        self.assertEqual(self.compile_one("8"), "backspace")
        self.assertEqual(self.compile_one("127"), "delete")
        self.assertEqual(self.compile_one("27"), "escape")

    def test_more_keys_range(self):
        self.assertEqual(self.compile_one("128"), "left arrow")
        self.assertEqual(self.compile_one("140"), "page down")

    def test_non_ascii_characters(self):
        self.assertEqual(self.compile_one("0x0416"), "Ж")
        self.assertEqual(self.compile_one("233"), "É")

    def test_names_are_passed_through(self):
        self.assertEqual(self.compile_one('"left arrow"'), "left arrow")
        self.assertEqual(self.compile_one('"a"'), "a")
        self.assertEqual(self.compile_one("any"), "any")

    def test_unsupported_keys_warn(self):
        cases = ['"f1"', "141", "0", '"tab"']
        for argument in cases:
            with self.subTest(argument=argument):
                warnings = self.key_warnings(
                    "class main { // Sprite: main (0,0) size=100 show=1\n"
                    f"    public void greenFlag() {{ if (Sensing.isKeyPressed({argument})) {{ Looks.say(\"x\"); }} }}\n}}\n"
                )
                self.assertTrue(warnings, f"{argument} should warn")

    def test_key_warnings_are_coded(self):
        from jswc.ui import classify

        warnings = self.key_warnings(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    public void greenFlag() { if (Sensing.isKeyPressed("f1")) { Looks.say("x"); } }\n}\n'
        )
        self.assertTrue(warnings)
        self.assertEqual(classify(warnings[0]).code, "W0007")

    def key_warnings(self, source: str) -> list[str]:
        gen = Codegen(Parser(tokenize(source)).parse(), features={"commentMetadata"}, allow_undefined=True)
        gen.compile()
        markers = ("never fires", "not a key", "is a control code", "more-keys", "truncated to")
        return [d for d in gen.diagnostics if any(m in d for m in markers)]

    def test_numbers_are_rejected_where_a_key_is_expected(self):
        # A rejected code still compiles, but to an empty key rather than junk.
        warnings = self.key_warnings(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { if (Sensing.isKeyPressed(141)) { Looks.say(\"x\"); } }\n}\n"
        )
        self.assertTrue(warnings, "141 is not a more-keys code")

    def test_hat_hook_accepts_a_number(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void Events.keyPressed(k) { // hook: when [65] key pressed\n"
            "        Looks.say(\"A\");\n    }\n}\n"
        )
        sprite = target(data, "main")
        hat = next(b for b in sprite["blocks"].values()
                   if isinstance(b, dict) and b["opcode"] == "event_whenkeypressed")
        self.assertEqual(hat["fields"]["KEY_OPTION"][0], "A")

    def test_round_trip_keeps_a_working_key(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { if (Sensing.isKeyPressed(128)) { Looks.say(\"x\"); } }\n}\n"
        )
        text, _ = decompile(compile_source(source)[1])
        self.assertIn('Sensing.keyPressed("left arrow")', text)
        self.assertEqual(
            [b["fields"]["KEY_OPTION"][0] for b in target(compile_source(text)[1], "main")["blocks"].values()
             if isinstance(b, dict) and b["opcode"] == "sensing_keyoptions"],
            ["left arrow"],
        )


class NumericTextTests(unittest.TestCase):
    """A number stored as *text* in a block input is written without quotes.

    TurboWarp keeps ``[4, "0xFFFFFFFF"]`` — a numeric primitive holding hex
    text — so the decompiler must not turn that into ``"0xFFFFFFFF"``.
    """

    def literal(self, primitive: int, value):
        from jswc.decompile import _literal

        return _literal([primitive, value])

    def test_hex_in_a_numeric_input_is_bare(self):
        for text in ("0x1df0", "0x1DF0", "0xFFFFFFFF", "-0x10"):
            with self.subTest(text=text):
                self.assertEqual(self.literal(4, text), text)

    def test_binary_and_octal_are_bare(self):
        self.assertEqual(self.literal(4, "0b1010"), "0b1010")
        self.assertEqual(self.literal(4, "0o17"), "0o17")

    def test_decimal_is_bare(self):
        self.assertEqual(self.literal(4, "4294967296"), "4294967296")
        self.assertEqual(self.literal(10, "1.5"), "1.5")

    def test_text_that_is_not_a_number_stays_quoted(self):
        for text in ("1df0", "007", "2 apples", "", "0x", "1 2"):
            with self.subTest(text=text):
                self.assertEqual(self.literal(4 if text else 10, text), f'"{text}"' if text else '""')

    def test_hex_compiles_back_to_a_decimal_integer(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { Looks.say(0x1df0); }\n}\n"
        )
        sprite = target(data, "main")
        say = next(b for b in sprite["blocks"].values()
                   if isinstance(b, dict) and b["opcode"] == "looks_say")
        self.assertEqual(say["inputs"]["MESSAGE"][1], [10, "7664"], "hex must become decimal")

    def test_hex_survives_a_round_trip_as_the_same_number(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { Looks.say(0x1df0 - 0x10); }\n}\n"
        )
        _, first = compile_source(source)
        text, _ = decompile(first)
        self.assertNotIn('"0x', text)
        _, again = compile_source(text)
        self.assertEqual(
            sorted(opcodes(first, "main")), sorted(opcodes(again, "main"))
        )

    def test_string_valued_state_keeps_its_quotes(self):
        # A variable whose stored value is the *string* "0x6" must stay quoted:
        # Scratch does not read hex, so "0x6" is not the number 6.
        from jswc.decompile import _literal_text

        self.assertEqual(_literal_text("0x6"), '"0x6"')
        self.assertEqual(_literal_text(6), "6")


class QuotedKeyHookTests(unittest.TestCase):
    def test_a_quote_key_round_trips(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            '    public void Events.keyPressed(k) { // hook: when ["\\""] key pressed\n'
            '        Looks.say("q");\n    }\n}\n'
        )
        _, data = compile_source(source)
        hat = next(b for b in target(data, "main")["blocks"].values()
                   if isinstance(b, dict) and b["opcode"] == "event_whenkeypressed")
        self.assertEqual(hat["fields"]["KEY_OPTION"][0], '"')

    def test_digit_keys_are_names_not_codes(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void Events.keyPressed(k) { // hook: when [0] key pressed\n"
            '        Looks.say("zero");\n    }\n}\n'
        )
        hat = next(b for b in target(data, "main")["blocks"].values()
                   if isinstance(b, dict) and b["opcode"] == "event_whenkeypressed")
        self.assertEqual(hat["fields"]["KEY_OPTION"][0], "0")


class MetadataStatementTests(unittest.TestCase):
    """``metadata`` and target declarations replaced the comment spellings."""

    def compile(self, source: str):
        return compile_source(source)

    def test_position_statement_places_the_script(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    metadata greenFlag pos 539,64;\n"
            "    public void greenFlag() { Looks.say(1); }\n}\n"
        )
        sprite = target(data, "main")
        hat = next(b for b in sprite["blocks"].values()
                   if isinstance(b, dict) and b["opcode"] == "event_whenflagclicked")
        self.assertEqual((hat["x"], hat["y"]), (539, 64))

    def test_position_statement_is_optional(self):
        _, data = compile_source(
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { Looks.say(1); }\n}\n"
        )
        hat = next(b for b in target(data, "main")["blocks"].values()
                   if isinstance(b, dict) and b["opcode"] == "event_whenflagclicked")
        self.assertEqual((hat["x"], hat["y"]), (0, 0))

    def test_monitor_statement_mode_first(self):
        _, data = compile_source(
            "public score = 0;\n"
            "metadata default Stage.score at 5,6 100x24 hidden;\n"
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { score = 1; }\n}\n"
        )
        monitor = data["monitors"][0]
        self.assertEqual(monitor["params"]["VARIABLE"], "score")
        self.assertEqual(monitor["mode"], "default")
        self.assertEqual((monitor["x"], monitor["y"]), (5, 6))
        self.assertFalse(monitor["visible"])

    def test_monitor_statement_target_first_with_slider_range(self):
        _, data = compile_source(
            "public score = 0;\n"
            "metadata Stage.score slider at 7,8 120x30 shown range 0..10;\n"
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { score = 1; }\n}\n"
        )
        monitor = data["monitors"][0]
        self.assertEqual(monitor["mode"], "slider")
        self.assertEqual(monitor["sliderMax"], 10)
        self.assertTrue(monitor["visible"])

    def test_comment_spelling_still_works(self):
        _, data = compile_source(
            "public score = 0;\n"
            "// monitor Stage.score slider at 7,8 120x30 shown range 0..10\n"
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { score = 1; }\n}\n"
        )
        self.assertEqual(data["monitors"][0]["mode"], "slider")

    def test_stage_declaration_sets_the_properties(self):
        _, data = compile_source(
            "stage: costume=1, volume=80, layer=2, tempo=140, video=\"off\", videoTransparency=25;\n"
            "stage {\n"
            "    public void greenFlag() { Looks.say(1); }\n}\n"
        )
        stage = next(t for t in data["targets"] if t["isStage"])
        self.assertEqual(stage["currentCostume"], 1)
        self.assertEqual(stage["volume"], 80)
        self.assertEqual(stage["layerOrder"], 2)
        self.assertEqual(stage["tempo"], 140)
        self.assertEqual(stage["videoState"], "off")
        self.assertEqual(stage["videoTransparency"], 25)

    def test_space_separated_properties_and_bare_words(self):
        # The spelling the project uses: ``stage: costume=0 volume=100 …``
        _, data = compile_source(
            "stage: costume=0 volume=100 layer=0 tempo=60 video=on videoTransparency=50\n"
            "stage {\n"
            "    public void greenFlag() { Looks.say(1); }\n}\n"
        )
        stage = next(t for t in data["targets"] if t["isStage"])
        self.assertEqual(stage["videoState"], "on")
        self.assertEqual(stage["videoTransparency"], 50)
        self.assertEqual(stage["tempo"], 60)

    def test_sprite_declaration_names_a_sprite_with_spaces(self):
        _, data = compile_source(
            'obj_loader: name="obj loader", x=1, y=2, size=150;\n'
            "class obj_loader {\n"
            "    public void greenFlag() { Looks.say(1); }\n}\n"
        )
        sprite = target(data, "obj loader")
        self.assertEqual((sprite["x"], sprite["y"]), (1, 2))
        self.assertEqual(sprite["size"], 150)

    def test_an_unreadable_metadata_line_is_a_parse_error(self):
        from jswc.tokens import ParseError

        with self.assertRaises(ParseError) as caught:
            Parser(tokenize(
                "class main { // Sprite: main (0,0) size=100 show=1\n"
                "    metadata greenFlag wobble 1,2;\n"
                "    public void greenFlag() { Looks.say(1); }\n}\n"
            )).parse()
        self.assertIn("metadata expects", str(caught.exception))

    def test_decompiler_emits_metadata_not_comments(self):
        source = (
            "class main { // Sprite: main (0,0) size=100 show=1\n"
            "    public void greenFlag() { // at 12,34\n"
            "        Looks.say(1);\n    }\n}\n"
        )
        text, _ = decompile(compile_source(source)[1])
        self.assertIn("metadata greenFlag pos 12,34;", text)
        self.assertNotIn("// at 12,34", text)
        self.assertIn("main: ", text)
        self.assertNotIn("// Sprite:", text)

    def test_the_whole_thing_round_trips(self):
        source = (
            "public score = 0;\n"
            "metadata slider Stage.score at 7,8 120x30 shown range 0..10;\n"
            "stage: tempo=140, volume=80;\n"
            "stage {\n"
            "    metadata greenFlag pos 3,4;\n"
            "    public void greenFlag() { score = 1; }\n}\n"
        )
        first = compile_source(source)[1]
        text, _ = decompile(first)
        second = compile_source(text)[1]
        self.assertEqual(
            sorted(opcodes(first, "Stage")), sorted(opcodes(second, "Stage"))
        )
        self.assertEqual(first["monitors"], second["monitors"])


class NamespacedGlobalTests(unittest.TestCase):
    """``namespace=`` must only prefix what the class actually declared.

    A file-scope ``public i`` is a Stage variable; prefixing it inside a
    namespaced class forked it into ``Lyrics.i`` and left every read pointing
    at a second, empty copy of the name.
    """

    def stage_vars(self, data: dict) -> set[str]:
        stage = next(t for t in data["targets"] if t["isStage"])
        return {entry[0] for entry in stage["variables"].values()}

    def refs(self, data: dict) -> list[str]:
        return [
            block["fields"]["VARIABLE"][0]
            for target in data["targets"]
            for block in target["blocks"].values()
            if isinstance(block, dict) and "VARIABLE" in (block.get("fields") or {})
        ]

    def test_a_file_scope_global_keeps_its_name_inside_a_namespaced_class(self):
        source = (
            "#enable_feature<namespacedNames>;\n"
            "public i = 0;\n"
            "Lyrics: name=\"Lyrics\", x=0, y=0, namespace=\"Lyrics\";\n"
            "class Lyrics {\n"
            "    public void tick() { i += 1; }\n}\n"
        )
        _, data = compile_source(source)
        self.assertIn("i", self.stage_vars(data))
        self.assertNotIn("Lyrics.i", self.stage_vars(data))
        self.assertIn("i", self.refs(data))
        self.assertNotIn("Lyrics.i", self.refs(data))

    def test_a_class_level_public_member_is_still_namespaced(self):
        source = (
            "#enable_feature<namespacedNames>;\n"
            "Lyrics: name=\"Lyrics\", x=0, y=0, namespace=\"Lyrics\";\n"
            "class Lyrics {\n"
            "    public zh = 0;\n"
            "    public void tick() { zh += 1; }\n}\n"
        )
        _, data = compile_source(source)
        self.assertIn("Lyrics.zh", self.stage_vars(data))
        self.assertNotIn("zh", self.stage_vars(data))
        self.assertIn("Lyrics.zh", self.refs(data))

    def test_runtime_variables_stay_global_and_unprefixed(self):
        source = (
            "#enable_feature<namespacedNames>;\n"
            "#enable_feature<useTempVariables>;\n"
            "Lyrics: name=\"Lyrics\", x=0, y=0, namespace=\"Lyrics\";\n"
            "class Lyrics {\n"
            "    public void tick() {\n"
            "        Temp.setRuntimeVariable(\"_noteGroup_0\", 5);\n"
            "        Temp.setRuntimeVariable(\"Group.1\", 5);\n"
            "    }\n}\n"
        )
        _, data = compile_source(source)
        names = self.stage_vars(data)
        self.assertIn("_noteGroup_0", names)
        self.assertIn("Group.1", names)
        self.assertNotIn("Lyrics._noteGroup_0", names)
        self.assertNotIn("Lyrics.Group.1", names)
        self.assertNotIn("Lyrics._noteGroup_0", self.refs(data))


class BroadcastCallTests(unittest.TestCase):
    """``X();`` broadcasts and waits; ``call X();`` broadcasts without waiting.

    A ``// hook: broadcast`` method is a hat, not a custom block, so a call to
    it cannot be a ``procedures_call``.
    """

    def opcodes(self, data: dict) -> list[str]:
        return [
            block["opcode"]
            for target in data["targets"]
            for block in target["blocks"].values()
            if isinstance(block, dict) and block["opcode"].startswith("event_broadcast")
        ]

    def messages(self, data: dict) -> list[str]:
        names = []
        for target in data["targets"]:
            for block in target["blocks"].values():
                if isinstance(block, dict) and block["opcode"].startswith("event_broadcast"):
                    names.append(block["inputs"]["BROADCAST_INPUT"][1][1])
        return names

    def test_bare_call_waits_and_call_does_not(self):
        source = (
            "class A { // Sprite: A\n"
            "    public void Player.main() { // hook: broadcast Player.main\n"
            "        Looks.say(1);\n    }\n"
            "    public void go() {\n"
            "        Player.main();\n"
            "        call Player.main();\n"
            "    }\n}\n"
        )
        _, data = compile_source(source)
        self.assertEqual(
            self.opcodes(data), ["event_broadcastandwait", "event_broadcast"]
        )
        self.assertEqual(self.messages(data), ["Player.main", "Player.main"])

    def test_a_single_part_receiver_is_callable(self):
        source = (
            "class A { // Sprite: A\n"
            "    public void reset_it() { // hook: broadcast my message\n"
            "        Looks.say(1);\n    }\n"
            "    public void go() { reset_it(); }\n}\n"
        )
        _, data = compile_source(source)
        self.assertEqual(self.opcodes(data), ["event_broadcastandwait"])
        self.assertEqual(self.messages(data), ["my message"])

    def test_a_receiver_in_another_sprite_is_callable_by_its_full_name(self):
        source = (
            "class Lyrics { // Sprite: Lyrics\n"
            "    public void init() { // hook: broadcast Lyrics.init\n"
            "        Looks.say(1);\n    }\n}\n"
            "class Other { // Sprite: Other\n"
            "    public void go() { Lyrics.init(); call Lyrics.init(); }\n}\n"
        )
        _, data = compile_source(source)
        self.assertEqual(
            self.opcodes(data), ["event_broadcastandwait", "event_broadcast"]
        )
        self.assertEqual(self.messages(data), ["Lyrics.init", "Lyrics.init"])

    def test_the_decompiler_uses_the_call_form_and_round_trips(self):
        source = (
            "Lyrics: name=\"Lyrics\", x=0, y=0, namespace=\"Lyrics\";\n"
            "class Lyrics {\n"
            "    public void init() { // hook: broadcast Lyrics.init\n"
            "        Looks.say(1);\n    }\n"
            "    public void go() { init(); call init(); }\n}\n"
        )
        first = compile_source(source)[1]
        text, _ = decompile(first)
        self.assertIn("Lyrics.init();", text)
        self.assertIn("call Lyrics.init();", text)
        self.assertNotIn("Control.broadcast", text)
        second = compile_source(text)[1]
        self.assertEqual(self.opcodes(first), self.opcodes(second))
        self.assertEqual(self.messages(first), self.messages(second))


class JoinFoldTests(unittest.TestCase):
    """``join`` is flat in the source and a balanced tree in the project."""

    def shape(self, data: dict) -> str:
        target = next(t for t in data["targets"] if t["name"] == "A")
        blocks = target["blocks"]

        def show(bid: str) -> str:
            block = blocks.get(bid)
            if not isinstance(block, dict) or block["opcode"] != "operator_join":
                return "?"
            out = []
            for name in ("STRING1", "STRING2"):
                raw = block["inputs"].get(name)
                if not raw:
                    out.append("''")
                    continue
                value = raw[1]
                if isinstance(value, str):
                    out.append(show(value))
                else:
                    out.append(str(value[1]))
            return f"join({out[0]},{out[1]})"

        tops = [
            bid
            for bid, block in blocks.items()
            if isinstance(block, dict)
            and block["opcode"] == "operator_join"
            and not (
                isinstance(blocks.get(block.get("parent")), dict)
                and blocks[block["parent"]]["opcode"] == "operator_join"
            )
        ]
        return tops[0] and show(tops[0])

    def compile_join(self, args: str) -> str:
        source = f'class A {{ // Sprite: A\n public void go() {{ x = join({args}); }}\n}}\n'
        return self.shape(compile_source(source)[1])

    def test_eight_operands_nest_balanced(self):
        self.assertEqual(
            self.compile_join("1,2,3,4,5,6,7,8"),
            "join(join(join(1,2),join(3,4)),join(join(5,6),join(7,8)))",
        )

    def test_nine_operands_carry_the_leftover(self):
        self.assertEqual(
            self.compile_join("1,2,3,4,5,6,7,8,9"),
            "join(join(join(join(1,2),join(3,4)),join(join(5,6),join(7,8))),9)",
        )

    def test_three_operands_stay_shallow(self):
        self.assertEqual(self.compile_join("1,2,3"), "join(join(1,2),3)")

    def test_two_operands_are_a_single_block(self):
        self.assertEqual(self.compile_join("1,2"), "join(1,2)")

    def test_the_decompiler_flattens_and_round_trips(self):
        source = (
            "public a = 0;\npublic b = 0;\n"
            "class A { // Sprite: A\n"
            ' public void go() { a = join("x", a, "y", b); b = join(1, 2, 3); }\n}\n'
        )
        first = compile_source(source)[1]
        text, _ = decompile(first)
        self.assertIn('join("x", a, "y", b)', text)
        self.assertIn("join(1, 2, 3)", text)
        second = compile_source(text)[1]
        self.assertEqual(self.shape(first), self.shape(second))


class IndexAndLengthTests(unittest.TestCase):
    """``list[i]`` counts from 1, and ``lengthOf(x)`` is the length block."""

    def blocks(self, source: str, sprite: str = "A") -> list[tuple[str, dict]]:
        _, data = compile_source(source)
        target = next(t for t in data["targets"] if t["name"] == sprite)
        return [
            (block["opcode"], block)
            for block in target["blocks"].values()
            if isinstance(block, dict)
        ]

    def literal_input(self, block: dict, name: str) -> str | None:
        raw = block["inputs"].get(name)
        if raw and isinstance(raw[1], list):
            return str(raw[1][1])
        return None

    def test_reading_an_item_is_item_of_list(self):
        found = self.blocks(
            "public l = [10, 20, 30];\n"
            "class A { // Sprite: A\n public void go() { x = l[2]; }\n}\n"
        )
        item = next(b for op, b in found if op == "data_itemoflist")
        self.assertEqual(item["fields"]["LIST"][0], "l")
        self.assertEqual(self.literal_input(item, "INDEX"), "2")

    def test_writing_an_item_is_replace_item_of_list(self):
        found = self.blocks(
            "public l = [10, 20, 30];\n"
            "class A { // Sprite: A\n public void go() { l[1] = 7; }\n}\n"
        )
        replace = next(b for op, b in found if op == "data_replaceitemoflist")
        self.assertEqual(replace["fields"]["LIST"][0], "l")
        self.assertEqual(self.literal_input(replace, "INDEX"), "1")
        self.assertEqual(self.literal_input(replace, "ITEM"), "7")

    def test_a_compound_index_assignment_reads_then_writes(self):
        found = self.blocks(
            "public l = [10, 20, 30];\n"
            "class A { // Sprite: A\n public void go() { l[2] += 5; }\n}\n"
        )
        opcodes = [op for op, _ in found]
        self.assertIn("data_replaceitemoflist", opcodes)
        self.assertIn("data_itemoflist", opcodes)
        self.assertIn("operator_add", opcodes)

    def test_indexing_something_that_is_not_a_list_is_an_error(self):
        from tests.helpers import compile_source as compile_with_diagnostics

        codegen, _ = compile_with_diagnostics(
            "class A { // Sprite: A\n public void go() { x = nope[2]; }\n}\n"
        )
        self.assertTrue(
            [d for d in codegen.diagnostics if "E0043" in d],
            codegen.diagnostics,
        )

    def test_length_of_is_the_length_block(self):
        found = self.blocks(
            "class A { // Sprite: A\n"
            " public void go() { n = lengthOf(123); }\n}\n"
        )
        length = next(b for op, b in found if op == "operator_length")
        self.assertEqual(self.literal_input(length, "STRING"), "123")

    def test_the_decompiler_writes_length_of_and_keeps_list_get(self):
        source = (
            "public l = [10, 20, 30];\n"
            "class A { // Sprite: A\n"
            " public void go() { x = l[2]; n = lengthOf(x); }\n}\n"
        )
        text, _ = decompile(compile_source(source)[1])
        self.assertIn("x = l.get(2);", text)
        self.assertIn("lengthOf(x)", text)
        self.assertNotIn("Operators.length", text)


class TopLevelFunctionTests(unittest.TestCase):
    """A function with no class of its own lives on the Stage."""

    def test_a_file_scope_function_lands_on_the_stage(self):
        source = (
            "public void hello() { // hook: green flag\n"
            "    Looks.say(1);\n"
            "}\n"
            "class main { // Sprite: main\n"
            "    public void greenFlag() { }\n"
            "}\n"
        )
        _, data = compile_source(source)
        stage = next(t for t in data["targets"] if t["isStage"])
        sprite = next(t for t in data["targets"] if not t["isStage"])
        stage_ops = [b["opcode"] for b in stage["blocks"].values() if isinstance(b, dict)]
        self.assertIn("looks_say", stage_ops)
        self.assertNotIn("looks_say", [b["opcode"] for b in sprite["blocks"].values()
                                       if isinstance(b, dict)])

    def test_a_file_scope_procedure_becomes_a_stage_custom_block(self):
        source = (
            "private void helper(n) { // proccode helper %s\n"
            "    Control.wait(1);\n"
            "}\n"
        )
        _, data = compile_source(source)
        stage = next(t for t in data["targets"] if t["isStage"])
        ops = {b["opcode"] for b in stage["blocks"].values() if isinstance(b, dict)}
        self.assertIn("procedures_definition", ops)
        self.assertIn("procedures_prototype", ops)


class SwitchSyntaxTests(unittest.TestCase):
    """``switch x { case 1 { … } case _ { … } }`` — no parens, brace bodies."""

    SOURCE = (
        "public op_1 = 0;\n"
        "class main { // Sprite: main\n"
        "    public void greenFlag() {\n"
        "        switch op_1 {\n"
        "            case 0 { Looks.say(0); }\n"
        "            case 1 { Looks.say(1); }\n"
        "            case _ { Looks.say(3); }\n"
        "        }\n"
        "    }\n"
        "}\n"
    )

    def test_braces_and_no_parenthesis_parse(self):
        from tests.helpers import parse_source

        parser, program = parse_source(self.SOURCE)
        self.assertEqual(parser.diagnostics, [])
        stmt = program.classes[0].funcs[0].body[0]
        self.assertEqual(len(stmt.cases), 2)
        self.assertTrue(stmt.default)

    def test_it_lowers_to_if_else_and_leaves_nothing_out(self):
        _, data = compile_source(self.SOURCE)
        sprite = next(t for t in data["targets"] if not t["isStage"])
        ops = [b["opcode"] for b in sprite["blocks"].values() if isinstance(b, dict)]
        self.assertEqual(ops.count("control_if_else"), 2)
        self.assertEqual(ops.count("operator_equals"), 2)
        self.assertEqual(ops.count("looks_say"), 3)


class NamespacedClaimTests(unittest.TestCase):
    """``NpGraph`` namespaced ``Graph`` must claim ``Graph.v1``, not ``NpGraph.v1``.

    The Stage skipped ``Graph.*`` because the class had adopted the prefix, and
    the class matched the *sprite* name instead of the prefix, so neither side
    emitted the list: 300 items vanished from the rebuild.
    """

    def test_a_prefix_that_differs_from_the_sprite_name_claims_its_state(self):
        source = (
            "#enable_feature<namespacedNames>;\n"
            "stage: costume=0;\n"
            "stage { }\n"
            'NpGraph: name="NpGraph", x=0, y=0, namespace="Graph";\n'
            "class NpGraph {\n"
            "    public v1 = [1, 2, 3];\n"
            "    public nfont = [\"a\"];\n"
            "    private void doGraph() { // proccode Graph.doGraph // warp\n"
            "        v1.add(4);\n"
            "    }\n"
            "}\n"
        )
        first = compile_source(source)[1]
        stage = next(t for t in first["targets"] if t["isStage"])
        self.assertIn("Graph.v1", {e[0] for e in stage["lists"].values()})
        self.assertIn("Graph.nfont", {e[0] for e in stage["lists"].values()})

        text, _ = decompile(first)
        self.assertIn("public v1 = [1, 2, 3];", text)
        self.assertIn("public nfont = [\"a\"];", text)
        second = compile_source(text)[1]
        stage2 = next(t for t in second["targets"] if t["isStage"])
        self.assertEqual(
            {e[0]: e[1] for e in stage["lists"].values()},
            {e[0]: e[1] for e in stage2["lists"].values()},
        )

    def test_a_class_list_is_never_emptied(self):
        items = ", ".join(str(i) for i in range(400))
        source = (
            "#enable_feature<namespacedNames>;\n"
            'NpGraph: name="NpGraph", x=0, y=0, namespace="Graph";\n'
            "class NpGraph {\n"
            f"    public v1 = [{items}];\n"
            "    private void doGraph() { // proccode Graph.doGraph // warp\n"
            "        v1.add(1);\n"
            "    }\n"
            "}\n"
        )
        text, _ = decompile(compile_source(source)[1])
        self.assertIn("public v1 = [0, 1, 2", text)
        self.assertNotIn("kept empty", text)


class ThreadVariableLoopTests(unittest.TestCase):
    """A temp-variable loop counter must keep one name for the whole loop.

    ``useTempVariables`` renames every temp to an underscore-prefixed name and
    the body reads go through that rename, but the loop matcher used to read
    the counter's *raw* project name. For a temp called ``i`` it wrote
    ``for (i = 1; i < 130; i++)`` while the body still said ``_i`` -- so the
    rebuilt project counted up a Stage variable nothing ever read and the loop
    body never advanced. That is exactly ``Lyrics.start`` in NotePlayer.
    """

    @staticmethod
    def _project() -> dict:
        """A sprite whose loop counter is a temp variable named ``i``."""

        def blk(op, **kw):
            d = {"opcode": op, "next": None, "parent": None, "inputs": {},
                 "fields": {}, "shadow": False, "topLevel": False}
            d.update(kw)
            return d

        blocks = {
            "hat": blk("event_whenbroadcastreceived", next="set", topLevel=True,
                       x=0, y=0, fields={"BROADCAST_OPTION": ["Lyrics.start", "bid1"]}),
            "set": blk("lmsTempVars2_setThreadVariable",
                       inputs={"VAR": [1, [10, "i"]], "STRING": [1, [10, "1"]]}),
            "loop": blk("control_while", inputs={"CONDITION": [2, "lt"]}),
            "lt": blk("operator_lt", parent="loop",
                      inputs={"OPERAND1": [3, "get"], "OPERAND2": [1, [4, "130"]]}),
            "get": blk("lmsTempVars2_getThreadVariable",
                       inputs={"VAR": [1, [10, "i"]]}, parent="lt"),
            "step": blk("lmsTempVars2_changeThreadVariable", parent="loop",
                        inputs={"VAR": [1, [10, "i"]], "NUM": [1, [4, "1"]]}),
        }
        blocks["hat"]["next"] = "set"
        blocks["set"]["next"] = "loop"
        blocks["loop"]["inputs"]["SUBSTACK"] = [2, "step"]

        costume = {"name": "b", "assetId": "x", "md5ext": "x.svg", "dataFormat": "svg",
                   "rotationCenterX": 0, "rotationCenterY": 0}
        return {
            "targets": [
                {"isStage": True, "name": "Stage", "variables": {}, "lists": {},
                 "broadcasts": {"Lyrics.start": "bid1"}, "blocks": {}, "comments": {},
                 "currentCostume": 0, "costumes": [costume], "sounds": [],
                 "volume": 100, "layerOrder": 0, "tempo": 60,
                 "videoTransparency": 50, "videoState": "on",
                 "textToSpeechLanguage": None},
                {"isStage": False, "name": "Lyrics", "variables": {}, "lists": {},
                 "broadcasts": {}, "blocks": blocks, "comments": {}, "currentCostume": 0,
                 "costumes": [costume], "sounds": [], "volume": 100, "layerOrder": 1,
                 "visible": True, "x": 0, "y": 0, "size": 100, "direction": 90,
                 "draggable": False, "rotationStyle": "all around"},
            ],
            "monitors": [], "extensions": ["lmsTempVars2"],
            "meta": {"semver": "3.0.0", "vm": "0.2.0", "agent": ""},
        }

    def _decompiled(self) -> str:
        return decompile(self._project())[0]

    def test_the_renamed_counter_is_the_one_the_body_reads(self):
        text = self._decompiled()
        body = text[text.index("class Lyrics"):]
        self.assertIn("_i = 1;", body)
        self.assertIn("while (_i < 130)", body)
        self.assertIn("_i += 1;", body)

    def test_the_loop_is_not_folded_into_a_stage_variable_for(self):
        body = self._decompiled()
        self.assertNotRegex(
            body, r"for \(\s*i\s*=",
            "`i` is a renamed temp: the three-part form compiles to a Stage "
            "variable, so the loop must stay a while",
        )

    def test_no_bare_counter_name_is_left_behind(self):
        body = self._decompiled()
        # every mention of the counter must carry the underscore
        self.assertNotRegex(body, r"(?<![_\w])i(?![\w])",
                            "a bare `i` would address a different variable")

    def test_compiling_the_result_keeps_one_thread_variable(self):
        again = compile_source_shipped(self._decompiled())
        names = set()
        for b in target(again, "Lyrics")["blocks"].values():
            if isinstance(b, dict) and b["opcode"].startswith("lmsTempVars2_"):
                var = b["inputs"].get("VAR")
                if var and isinstance(var[1], list):
                    names.add(var[1][1])
        self.assertEqual(len(names), 1, f"one counter, one name; got {names}")


def compile_source_shipped(source: str) -> dict:
    """Compile decompiler output, keeping the features its header asks for."""
    gen = Codegen(
        Parser(tokenize(source)).parse(),
        features={"commentMetadata", "useTempVariables", "namespacedNames"},
        allow_undefined=True,
    )
    project = gen.compile()
    data, _ = build_project_dict(project)
    return data


if __name__ == "__main__":
    unittest.main()
