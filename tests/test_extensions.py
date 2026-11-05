"""Community-extension calls, stage blocks and huge-state externalization."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import blocks_of, compile_source, opcodes

from jswc.decompile import Decompiler
from jswc.extensions import EXTENSION_BLOCKS, resolve, reverse
from jswc.ir import Project
from jswc.sb3 import build_project_dict
from jswc.sb3_read import LoadedProject


def roundtrip(source: str):
    _, project = compile_source(source)
    data, _ = build_project_dict(project)
    source2 = Decompiler(LoadedProject(data=data)).decompile()
    return project, source2


class ExtensionTests(unittest.TestCase):
    def test_catalogue_covers_the_extensions_we_map(self):
        for extension in ("skyhigh173JSON", "lmsTimers", "sipcconsole", "runtimeoptions"):
            self.assertIn(extension, EXTENSION_BLOCKS)
            self.assertGreater(len(EXTENSION_BLOCKS[extension]["blocks"]), 5)

    def test_json_isarray_is_a_pinned_menu_argument(self):
        call = resolve("JSON", "isArray", 1)
        self.assertIsNotNone(call)
        self.assertEqual(call.full_opcode, "skyhigh173JSON_json_is")
        self.assertEqual(call.values, {"types": "Array"})
        self.assertTrue(call.is_reporter)

    def test_json_alias_compiles_to_the_real_block(self):
        _, project = compile_source("class a { void f(){ x = JSON.isArray(Piano.notes); } }")
        blocks = blocks_of(project, "a")
        is_block = next(b for b in blocks.values() if b["opcode"] == "skyhigh173JSON_json_is")
        self.assertIn("json", is_block["inputs"])
        menu = blocks[is_block["inputs"]["types"][1]]
        self.assertEqual(menu["opcode"], "skyhigh173JSON_menu_types")
        self.assertEqual(menu["fields"]["types"], ["Array", None])
        self.assertIn("skyhigh173JSON", project.extensions)

    def test_generic_extension_call_without_alias(self):
        _, project = compile_source(
            'class a { void f(){ x = JSON.json_array_get(Piano.notes, 1); } }'
        )
        self.assertIn("skyhigh173JSON_json_array_get", opcodes(project, "a"))

    def test_extension_commands(self):
        _, project = compile_source(
            'class a { void f(){ Timer.startTimer("t"); console.log("hi"); } }'
        )
        ops = opcodes(project, "a")
        self.assertIn("lmsTimers_startResetTimer", ops)
        self.assertIn("sipcconsole_Journal", ops)
        self.assertEqual(
            set(project.extensions) & {"lmsTimers", "sipcconsole"}, {"lmsTimers", "sipcconsole"}
        )

    def test_extension_blocks_roundtrip(self):
        source = (
            'class a { void f(){ x = JSON.isArray(Piano.notes);'
            ' y = Timer.getTimer("Player.timer");'
            ' Timer.startTimer("t"); console.warn("w"); } }'
        )
        _, source2 = roundtrip(source)
        self.assertIn("JSON.isArray(", source2)
        self.assertIn("Timer.getTimer(", source2)
        self.assertIn("Timer.startTimer(", source2)
        # console statements come back as the language's own form
        self.assertIn('warn("w")', source2)

    def test_unknown_member_of_a_known_extension_is_a_diagnostic(self):
        from jswc.codegen import Codegen
        from jswc.lexer import tokenize
        from jswc.parser import Parser

        codegen = Codegen(
            Parser(tokenize("class a { void f(){ JSON.notAThing(1); } }")).parse(),
            allow_undefined=True,
        )
        codegen.compile()
        self.assertTrue(any("not a block of the JSON extension" in n for n in codegen.diagnostics))

    def test_extension_menu_is_used_for_boolean_blocks(self):
        _, project = compile_source("class a { void f(){ if (Runtime.turboMode()) { x = 1; } } }")
        blocks = blocks_of(project, "a")
        enabled = next(b for b in blocks.values() if b["opcode"] == "runtimeoptions_getEnabled")
        menu = blocks[enabled["inputs"]["thing"][1]]
        self.assertEqual(menu["fields"]["thing"], ["turbo mode", None])


class StageTests(unittest.TestCase):
    def test_stage_block_emits_stage_scripts(self):
        source = """
        stage {
            public void whenStageClicked() { // hook: stage click
                Player.toggle();
            }
        }
        class a { void f(){} }
        """
        _, project = compile_source(source)
        stage = project.stage
        hats = [b for b in stage.blocks.values() if b.opcode == "event_whenstageclicked"]
        self.assertEqual(len(hats), 1)

    def test_stage_scripts_roundtrip(self):
        source = """
        stage {
            public void whenStageClicked() { // hook: stage click
                Player.toggle();
            }
        }
        class a { void f(){} }
        """
        project, source2 = roundtrip(source)
        self.assertIn("stage {", source2)
        self.assertIn("hook: stage click", source2)


class HugeStateTests(unittest.TestCase):
    def test_large_list_in_a_sprite_becomes_a_file_import(self):
        items = ", ".join(str(i) for i in range(200))
        source = (
            "class a { // Sprite: a (0,0) size=100 direction=90 show=1\n"
            f"    private big = [{items}];\n"
            "    void f(){ n = big.length(); }\n"
            "}\n"
        )
        _, project = compile_source(source)
        data, _ = build_project_dict(project)
        decompiler = Decompiler(LoadedProject(data=data), max_inline=50)
        text = decompiler.decompile()
        self.assertIn('import "assets/lists/big.jsa" as big;', text)
        self.assertIn("lists/big.jsa", decompiler.extra_files)

    def test_small_state_stays_inline(self):
        _, project = compile_source("class a { void f(){ Piano.n.add(1); } }")
        data, _ = build_project_dict(project)
        text = Decompiler(LoadedProject(data=data)).decompile()
        self.assertIn("Piano.n = [];", text)


if __name__ == "__main__":
    unittest.main()


class NamingStyleTests(unittest.TestCase):
    """`string()` noise, redundant `Class.` prefixes and the ns= round trip."""

    def test_join_with_empty_string_is_just_the_value(self):
        from jswc.codegen import Codegen
        from jswc.lexer import tokenize
        from jswc.parser import Parser

        project = Codegen(
            Parser(tokenize('class a { void f(){ c = "#ff00a5"; d = string(c); } }')).parse(),
            allow_undefined=True,
        ).compile()
        data, _ = build_project_dict(project)
        text = Decompiler(LoadedProject(data=data)).decompile()
        self.assertIn('c = "#ff00a5";', text)
        self.assertIn("d = c;", text)
        self.assertNotIn("string(", text)

    def test_class_prefix_is_stripped_and_restored(self):
        source = (
            "#enable_feature<namespacedNames>;\n"
            "#enable_feature<commentMetadata>;\n"
            "class graph { // Sprite: graph (0,0) size=100 direction=90 show=1 ns=Graph\n"
            "    public dpoint_x = 0;\n"
            "    public void doGraph(){ dpoint_x = 1; }\n"
            "}\n"
        )
        project, text = roundtrip(source)
        stage_vars = {v[0] for v in project.stage.variables.values()}
        self.assertEqual(stage_vars, {"Graph.dpoint_x"})
        self.assertEqual(list(project.stage.broadcasts.values()), ["Graph.doGraph"])
        # the source itself uses the short names
        self.assertIn("public dpoint_x = 0;", text)
        self.assertIn("void doGraph()", text)
        self.assertNotIn("Graph.dpoint_x", text)
        self.assertIn('namespace="Graph"', text)

    def test_namespace_is_written_even_when_it_matches_the_sprite(self):
        """``Lyrics`` owning ``Lyrics.zh`` must still declare ``namespace=``.

        The prefix is only re-added for classes that spell it out, so leaving it
        off when the sprite happens to be called ``Lyrics`` silently flattens
        every ``Lyrics.zh`` in the project.
        """
        source = (
            "#enable_feature<namespacedNames>;\n"
            'class Lyrics(namespace="Lyrics") {\n'
            "    public c_en = \"x\";\n"
            "}\n"
        )
        project, text = roundtrip(source)
        self.assertIn('namespace="Lyrics"', text)
        self.assertEqual([v[0] for v in project.stage.variables.values()], ["Lyrics.c_en"])

    def test_another_class_can_read_a_namespaced_member(self):
        source = (
            "#enable_feature<namespacedNames>;\n"
            'Lyrics: name="Lyrics", namespace="Lyrics";\n'
            "class Lyrics {\n"
            '    public c_en = "hi";\n'
            "    public void init() { c_en = \"yo\"; }\n"
            "}\n"
            "class NpCore {\n"
            "    public void tick() { Looks.say(Lyrics.c_en); }\n"
            "}\n"
        )
        codegen, project = compile_source(source, features={"namespacedNames"},
                                          allow_undefined=False)
        self.assertEqual(codegen.diagnostics, [])
        names = {v[0] for v in project.stage.variables.values()}
        self.assertIn("Lyrics.c_en", names)
        reads = [
            b.fields["VARIABLE"][0]
            for sprite in project.targets
            for b in sprite.blocks.values()
            if b.opcode == "data_variable"
        ]
        self.assertIn("Lyrics.c_en", reads)

    def test_prefix_is_not_applied_without_the_feature(self):
        _, project = compile_source(
            "class graph { // Sprite: graph (0,0) size=100 direction=90 show=1 ns=Graph\n"
            "    public x = 0;\n}\n"
        )
        self.assertEqual([v[0] for v in project.stage.variables.values()], ["x"])

    def test_mixed_naming_is_left_alone(self):
        # custom blocks use the short name, so the class is not namespaced
        project, text = roundtrip(
            "class player { // Sprite: player (0,0) size=100 direction=90 show=1\n"
            "    public Player.score = 0;\n"
            "    public void Player.reset(){ Player.score = 0; }\n"
            "}\n"
        )
        stage_vars = {v[0] for v in project.stage.variables.values()}
        self.assertIn("Player.score", stage_vars)
        self.assertIn("Player.score", text)

    def test_proccode_label_is_preserved(self):
        source = (
            "class a { void f(){ create_note(1, 2); }\n"
            "  private void create_note(t, n) { // proccode create note %s in tick %s\n"
            "    x = t + n;\n  }\n}\n"
        )
        project, _ = roundtrip(source)
        proccodes = {
            (b.mutation or {}).get("proccode")
            for t in project.targets
            for b in t.blocks.values()
            if b.opcode == "procedures_prototype"
        }
        self.assertIn("create note %s in tick %s", proccodes)


class FStringTests(unittest.TestCase):
    def test_fstring_compiles_to_nested_joins(self):
        _, project = compile_source(
            'class a { void f(){ x = f"GCl:{Sensing.timer()} T:{Tick.tick}/{Player.total}"; } }'
        )
        joins = [b for b in blocks_of(project, "a").values() if b["opcode"] == "operator_join"]
        self.assertEqual(len(joins), 5)  # six pieces ("GCl:", timer, " T:", tick, "/", total)

    def test_fstring_roundtrips(self):
        source = 'class a { void f(){ x = f"a{Tick.tick}b{Sensing.timer()}c"; } }'
        _, text = roundtrip(source)
        self.assertIn('f"a{Tick.tick}b{Sensing.timer()}c"', text)

    def test_short_joins_stay_joins(self):
        _, text = roundtrip('class a { void f(){ x = join("a", Tick.tick); } }')
        self.assertIn('join("a", Tick.tick)', text)
        self.assertNotIn('f"', text)

    def test_braces_are_escaped_in_fstring_text(self):
        _, project = compile_source('class a { void f(){ x = f"{{config}} {Tick.tick}"; } }')
        data, _ = build_project_dict(project)
        text = Decompiler(LoadedProject(data=data)).decompile()
        self.assertIn("{config}", text)
        # recompiling must not treat the braces as holes
        _, again = compile_source(text)
        self.assertIn("operator_join", opcodes(again, "a"))


class TempVariableTests(unittest.TestCase):
    def test_underscore_declaration_needs_no_value(self):
        from jswc.codegen import Codegen
        from jswc.lexer import tokenize
        from jswc.parser import Parser

        project = Codegen(
            Parser(tokenize(
                "#enable_feature<useTempVariables>;\n"
                "class a { private _t; void f(){ _t = 1; _t += 2; } }"
            )).parse(),
            allow_undefined=True,
        ).compile()
        ops = [b.opcode for t in project.targets for b in t.blocks.values()]
        self.assertIn("lmsTempVars2_setThreadVariable", ops)
        self.assertIn("lmsTempVars2_changeThreadVariable", ops)
        self.assertIn("lmsTempVars2", project.extensions)

    def test_decompile_restores_temp_declarations(self):
        source = (
            "#enable_feature<useTempVariables>;\n"
            "class a { private _t; void f(){ _t = 1; x = _t; } }"
        )
        _, text = roundtrip(source)
        self.assertIn("private _t;", text)


class TurboWarpPredicateTests(unittest.TestCase):
    def test_predicates_are_direct_calls(self):
        _, project = compile_source(
            "class a { void f(){ if (isTurboWarp()) { x = 1; } y = isCompiled(); } }"
        )
        ops = opcodes(project, "a")
        self.assertIn("control_if", ops)
        self.assertTrue(project.extensions == [] or "pen" not in project.extensions)

    def test_predicates_can_be_overridden_by_a_variable(self):
        _, project = compile_source(
            "class a { public isCompiled = 0; void f(){ y = isCompiled(); } }"
        )
        self.assertIn("data_variable", opcodes(project, "a"))


class StateNamespaceTests(unittest.TestCase):
    """`Variables.*`, `Lists.*`, `Clone.*` and the `Stage.` prefix."""

    def test_variables_set_and_get_are_plain_blocks(self):
        _, project = compile_source(
            'public score = 0;\n'
            'class a { void f(){ Variables.set(score, 5); Looks.say(Variables.get("score")); } }'
        )
        ops = opcodes(project, "a")
        self.assertIn("data_setvariableto", ops)
        self.assertIn("data_variable", ops)
        reads = [
            b["fields"]["VARIABLE"][0]
            for b in blocks_of(project, "a").values()
            if b["opcode"] == "data_variable"
        ]
        self.assertEqual(reads, ["score"])

    def test_variables_change_uses_change_by(self):
        _, project = compile_source(
            'public score = 0;\nclass a { void f(){ Variables.change(score, 2); } }'
        )
        self.assertIn("data_changevariableby", opcodes(project, "a"))

    def test_lists_methods_take_the_list_name_first(self):
        _, project = compile_source(
            'public items = [];\n'
            'class a { void f(){ Lists.add(items, 1); x = Lists.get("items", 1);'
            ' y = Lists.length(items); } }'
        )
        ops = opcodes(project, "a")
        for opcode in ("data_addtolist", "data_itemoflist", "data_lengthoflist"):
            self.assertIn(opcode, ops)
        add = next(b for b in blocks_of(project, "a").values()
                   if b["opcode"] == "data_addtolist")
        self.assertEqual(add["fields"]["LIST"][0], "items")

    def test_lists_insert_and_delete_take_an_index(self):
        _, project = compile_source(
            'public items = [];\n'
            'class a { void f(){ Lists.insert(items, 1, 7); Lists.delete(items, 2);'
            ' Lists.clear(items); } }'
        )
        ops = opcodes(project, "a")
        for opcode in ("data_insertatlist", "data_deleteoflist", "data_deletealloflist"):
            self.assertIn(opcode, ops)

    def test_variable_monitor_visibility(self):
        for method, expected in (("show", "data_showvariable"), ("hide", "data_hidevariable")):
            with self.subTest(method=method):
                _, project = compile_source(
                    f'public score = 0;\nclass a {{ void f(){{ Variables.{method}(score); }} }}'
                )
                self.assertIn(expected, opcodes(project, "a"))

    def test_clone_of_self_and_of_another_sprite(self):
        _, project = compile_source(
            'class a { void f(){ Clone.create(); Clone.delete(); } }'
        )
        ops = opcodes(project, "a")
        self.assertIn("control_create_clone_of", ops)
        self.assertIn("control_delete_this_clone", ops)
        menu = next(b for b in blocks_of(project, "a").values()
                    if b["opcode"] == "control_create_clone_of_menu")
        self.assertEqual(menu["fields"]["CLONE_OPTION"][0], "_myself_")

        _, named = compile_source('class a { void f(){ Clone.create("b"); } }')
        menu = next(b for b in blocks_of(named, "a").values()
                    if b["opcode"] == "control_create_clone_of_menu")
        self.assertEqual(menu["fields"]["CLONE_OPTION"][0], "b")

    def test_the_stage_prefix_reaches_stage_state(self):
        _, project = compile_source(
            'public Lyrics.zh = ["a"];\n'
            'class a { void f(){ Looks.say(Stage.Lyrics.zh.length()); } }'
        )
        self.assertIn("data_lengthoflist", opcodes(project, "a"))
        names = [list_[0] for list_ in project.stage.lists.values()]
        self.assertEqual(names, ["Lyrics.zh"])

    def test_a_variable_literally_called_stage_x_is_left_alone(self):
        _, project = compile_source(
            'public Stage.x = 1;\nclass a { void f(){ Looks.say(Stage.x); } }'
        )
        names = [v[0] for v in project.stage.variables.values()]
        self.assertIn("Stage.x", names)


class RuntimeVariableTests(unittest.TestCase):
    """``Temp.setRuntimeVariable`` becomes a real stage variable."""

    SOURCE = (
        "class a { void f(){\n"
        '    Temp.setRuntimeVariable("_tps", 0);\n'
        '    Temp.changeRuntimeVariable("_tps", 1);\n'
        "    Looks.say(Temp.getRuntimeVariable(\"_tps\"));\n"
        "} }"
    )

    def test_the_call_becomes_a_stage_variable(self):
        _, project = self.convert(self.SOURCE)
        names = {v[0]: v[1] for v in project.stage.variables.values()}
        self.assertIn("_tps", names)
        self.assertEqual(names["_tps"], 0, "runtime variables start at 0")

    def test_no_runtime_variable_blocks_survive(self):
        _, project = self.compile_(self.SOURCE)
        leftovers = [
            b.opcode
            for target in project.targets
            for b in target.blocks.values()
            if b.opcode.startswith("lmsTempVars2_") and "Runtime" in b.opcode
        ]
        self.assertEqual(leftovers, [])

    def test_green_flag_zeroes_every_converted_variable(self):
        _, project = self.convert(self.SOURCE)
        stage = project.stage
        hats = [b for b in stage.blocks.values() if b.opcode == "event_whenflagclicked"]
        self.assertEqual(len(hats), 1, "one reset script on the Stage")
        sets = [b for b in stage.blocks.values() if b.opcode == "data_setvariableto"]
        self.assertEqual([b.fields["VARIABLE"][0] for b in sets], ["_tps"])
        self.assertEqual(sets[0].inputs["VALUE"][1], [10, "0"])

    def test_thread_variables_are_not_converted(self):
        _, project = self.compile_(
            "class a { void f(){\n"
            '    Temp.setThreadVariable("_i", 1);\n'
            "} }",
            features={"useTempVariables"},
        )
        ops = opcodes(project, "a")
        self.assertIn("lmsTempVars2_setThreadVariable", ops)
        self.assertNotIn("data_setvariableto", ops)

    def test_a_computed_name_keeps_the_extension_call(self):
        _, project = self.compile_(
            "class a { void f(){\n"
            '    Temp.setRuntimeVariable(name, 1);\n'
            "} }"
        )
        ops = opcodes(project, "a")
        self.assertIn("lmsTempVars2_setRuntimeVariable", ops)

    def test_a_project_that_deletes_them_keeps_the_extension(self):
        """Deleting depends on the extension's own store, so nothing converts."""
        source = (
            "class a { void f(){\n"
            '    Temp.setRuntimeVariable("_t", 1);\n'
            '    Temp.deleteRuntimeVariable("_t");\n'
            "} }"
        )
        _, project = self.compile_(source)
        ops = opcodes(project, "a")
        self.assertIn("lmsTempVars2_setRuntimeVariable", ops)
        self.assertIn("lmsTempVars2_deleteRuntimeVariable", ops)
        self.assertNotIn("event_whenflagclicked", {
            b.opcode for t in project.targets for b in t.blocks.values()
        })

    def test_the_reset_script_only_appears_when_needed(self):
        _, project = self.compile_("class a { void f(){ Looks.say(1); } }")
        self.assertNotIn(
            "event_whenflagclicked",
            {b.opcode for t in project.targets for b in t.blocks.values()},
        )

    # -- helpers ---------------------------------------------------------
    def convert(self, source: str):
        codegen, project = compile_source(source)
        self.assertEqual(
            [d for d in codegen.diagnostics if d.startswith(("E", "W"))], [],
            "conversion must not produce diagnostics",
        )
        return codegen, project

    def compile_(self, source: str, **kwargs):
        return compile_source(source, **kwargs)
