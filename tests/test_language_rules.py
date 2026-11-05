"""Tests for the rules in the user-written section of syntax.md."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import blocks_of, opcodes

from jswc.codegen import Codegen
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.tokens import CompileError


def compile_strict(source: str, **kwargs):
    program = Parser(tokenize(source)).parse()
    kwargs.setdefault("features", {"commentMetadata"})
    codegen = Codegen(program, **kwargs)
    return codegen, codegen.compile()


def compile_lenient(source: str, **kwargs):
    kwargs.setdefault("allow_undefined", True)
    kwargs.setdefault("features", {"commentMetadata"})
    program = Parser(tokenize(source)).parse()
    return Codegen(program, **kwargs).compile()


class FeatureFlagTests(unittest.TestCase):
    def test_features_are_collected(self):
        program = Parser(
            tokenize("#enable_feature<useTempVariables>;\n#enable_feature<commentMetadata>;\nclass a { void f(){} }")
        ).parse()
        self.assertEqual(program.features, {"useTempVariables", "commentMetadata"})

    def test_unknown_feature_warns(self):
        program = Parser(tokenize("#enable_feature<teleport>;\nclass a { void f(){} }")).parse()
        codegen = Codegen(program, allow_undefined=True)
        codegen.compile()
        self.assertTrue(any("unknown feature" in note for note in codegen.diagnostics))

    def test_malformed_feature_directive_is_a_parse_error(self):
        with self.assertRaises(Exception):
            Parser(tokenize("#enable_feature useTempVariables;\nclass a { void f(){} }")).parse()


class GlobalAndInitialStateTests(unittest.TestCase):
    def test_top_level_public_variable_is_a_stage_variable(self):
        codegen, project = compile_strict(
            'public abc = "Hello!";\nclass a { void f(){ Looks.say(abc); } }'
        )
        stage_vars = {v[0]: v[1] for v in project.stage.variables.values()}
        self.assertEqual(stage_vars["abc"], "Hello!")

    def test_public_variable_inside_a_function_is_global(self):
        _, project = compile_strict('class a { void f(){ public g = 15; } }')
        self.assertEqual([v[0] for v in project.stage.variables.values()], ["g"])
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual(target.variables, {})

    def test_private_variable_is_sprite_local(self):
        _, project = compile_strict("class a { void f(){ private b = 23; } }")
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual([v[0] for v in target.variables.values()], ["b"])
        self.assertEqual(list(project.stage.variables.values()), [])

    def test_first_assignment_defines_the_initial_state(self):
        _, project = compile_strict(
            """
            class helloworld {
                public c = 1145;
                private d = 1919;
                public void broadcast_1(){
                    public a = 15;
                    private b = 23;
                    public c = 69;
                }
            }
            """
        )
        stage = {v[0]: v[1] for v in project.stage.variables.values()}
        target = next(t for t in project.targets if t.name == "helloworld")
        sprite = {v[0]: v[1] for v in target.variables.values()}
        self.assertEqual(stage["c"], 1145, "later assignments must not change the initial state")
        self.assertEqual(stage["a"], 15)
        self.assertEqual(sprite["b"], 23)
        self.assertEqual(sprite["d"], 1919)

    def test_undefined_variable_is_a_compile_error(self):
        with self.assertRaises(CompileError) as caught:
            compile_strict("class a { void f(){ x = y + 1; } }")
        self.assertIn("undefined variable 'y'", str(caught.exception))

    def test_allow_undefined_keeps_legacy_sources_compiling(self):
        project = compile_lenient("class a { void f(){ x = y + 1; } }")
        self.assertTrue(project.targets)


class InitBlockTests(unittest.TestCase):
    def test_public_init_becomes_a_green_flag_script(self):
        _, project = compile_strict(
            'public abc = "Hello!";\nclass a { public init { Looks.say(abc); } }'
        )
        self.assertIn("event_whenflagclicked", opcodes(project, "a"))


class SpriteMetadataTests(unittest.TestCase):
    def test_class_parameters_set_metadata(self):
        _, project = compile_strict("class a(x=10, y=20, size=50, direction=180, show=0) { void f(){} }")
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual((target.x, target.y), (10, 20))
        self.assertEqual((target.size, target.direction, target.visible), (50.0, 180.0, False))

    def test_standalone_metadata_line(self):
        _, project = compile_strict("a: x=5, y=6, size=70, show=0;\nclass a { void f(){} }")
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual((target.x, target.y, target.size, target.visible), (5, 6, 70.0, False))

    def test_last_standalone_metadata_wins_with_a_warning(self):
        codegen, project = compile_strict(
            "a: x=1;\na: x=2;\nclass a { void f(){} }"
        )
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual(target.x, 2)
        self.assertTrue(any("metadata lines" in note for note in codegen.diagnostics))

    def test_comment_metadata_requires_the_feature(self):
        source = "class a { // Sprite: renamed (7,8) size=100 direction=90 show=1\n void f(){} }"
        codegen, project = compile_strict(source, features=set())
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual((target.x, target.y), (0, 0))
        self.assertTrue(any("commentMetadata" in note for note in codegen.diagnostics))

    def test_comment_metadata_can_rename_the_sprite(self):
        _, project = compile_strict(
            "class a { // Sprite: renamed (7,8) size=100 direction=90 show=1\n void f(){} }",
            features={"commentMetadata"},
        )
        self.assertIn("renamed", [t.name for t in project.targets])

    def test_class_parameters_override_comment_metadata(self):
        _, project = compile_strict(
            "class a(x=1) { // Sprite: a (9,9) size=100 direction=90 show=1\n void f(){} }",
            features={"commentMetadata"},
        )
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual(target.x, 1)


class TempVariableTests(unittest.TestCase):
    TEMP_SOURCE = """
    #enable_feature<useTempVariables>;
    class a {
        private void f(){
            private _temp = 0;
            _temp += 1;
            private kept = 0;
            kept += 1;
        }
    }
    """

    def test_underscore_variables_use_thread_storage_when_enabled(self):
        _, project = compile_strict(self.TEMP_SOURCE, features={"useTempVariables"})
        ops = opcodes(project, "a")
        self.assertIn("lmsTempVars2_setThreadVariable", ops)
        self.assertIn("lmsTempVars2_changeThreadVariable", ops)
        self.assertIn("lmsTempVars2", project.extensions)
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual([v[0] for v in target.variables.values()], ["kept"])

    def test_underscore_variables_are_sprite_locals_without_the_feature(self):
        # Scratch clones copy sprite-local variables, so ``_x`` does not need
        # the temp extension: it is an ordinary "for this sprite only" variable.
        _, project = compile_strict(
            "class a { void f(){ private _temp = 0; _temp += 1; } }", features=set()
        )
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual([v[0] for v in target.variables.values()], ["_temp"])
        self.assertIn("data_setvariableto", opcodes(project, "a"))
        self.assertNotIn("lmsTempVars2", project.extensions)

    def test_declaration_without_initial_value(self):
        from jswc.lexer import tokenize
        from jswc.parser import Parser

        # Without the feature an underscore local is a sprite variable, which
        # gets Scratch's default value 0.
        project = compile_strict("class a { void f(){ private _t; _t = 1; } }")[1]
        target = next(t for t in project.targets if t.name == "a")
        self.assertEqual([v[0] for v in target.variables.values()], ["_t"])
        self.assertIn("data_setvariableto", opcodes(project, "a"))
        # with the feature it is a thread variable and needs no value at all
        project = compile_strict(
            "#enable_feature<useTempVariables>;\nclass a { void f(){ private _t; _t = 1; } }"
        )[1]
        self.assertIn("lmsTempVars2_setThreadVariable", opcodes(project, "a"))
        # a non-temp declaration without a value is an error
        from jswc.codegen import Codegen

        codegen = Codegen(
            Parser(tokenize("class a { void f(){ private t; } }")).parse(),
            allow_undefined=True,
        )
        codegen.compile()
        self.assertTrue(any("no initial value" in n for n in codegen.diagnostics))


class VariableImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "greeting.jsv").write_text("Hello!\n", encoding="utf-8")
        (self.root / "scores.jsa").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        (self.root / "names.jsl").write_text("a\nb\nc\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def compile(self, source: str):
        program = Parser(tokenize(source)).parse()
        return Codegen(program, base_dir=self.root, features={"commentMetadata"}).compile()

    def test_jsv_creates_a_variable(self):
        project = self.compile('import "greeting.jsv" as hello;\nclass a { void f(){ Looks.say(hello); } }')
        self.assertEqual([v for v in project.stage.variables.values()], [["hello", "Hello!"]])

    def test_jsa_creates_a_list(self):
        project = self.compile('import "scores.jsa";\nclass a { void f(){ n = scores.length(); } }')
        self.assertEqual(list(project.stage.lists.values()), [["scores", [1, 2, 3]]])

    def test_jsl_creates_a_list_from_lines(self):
        project = self.compile('import "names.jsl";\nclass a { void f(){ n = names.length(); } }')
        self.assertEqual(list(project.stage.lists.values()), [["names", ["a", "b", "c"]]])

    def test_default_name_is_the_file_stem(self):
        project = self.compile('import "greeting.jsv";\nclass a { void f(){ Looks.say(greeting); } }')
        self.assertEqual([v[0] for v in project.stage.variables.values()], ["greeting"])

    def test_missing_file_refuses_to_compile(self):
        with self.assertRaises(CompileError) as caught:
            self.compile('import "nope.jsv";\nclass a { void f(){} }')
        self.assertIn("file not found", str(caught.exception))

    def test_invalid_json_refuses_to_compile(self):
        (self.root / "bad.jsa").write_text("{not json", encoding="utf-8")
        with self.assertRaises(CompileError) as caught:
            self.compile('import "bad.jsa";\nclass a { void f(){} }')
        self.assertIn("invalid JSON", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
