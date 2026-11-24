"""``#enable_feature<pishiSupport>;`` -- the pishi.ai extensions.

pishi's extensions exist only in their own player, so the feature is what says
"this build is for pishi.ai". These tests pin the three promises that go with
it: the namespaces are off until the feature asks for them, every block in the
generated table resolves to itself, and the two name collisions are reported in
the right severity.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from tests import helpers
from tests.helpers import blocks_of, compile_source

from jswc import extensions
from jswc.pishi_blocks import PISHI_BLOCKS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

SOURCE = """
class main {
    public void greenFlag() {
        v = Facemesh.getXFromMenu("nose", 0);
    }
}
"""

FEATURE = {"pishiSupport"}


def diagnostics(codegen) -> list[str]:
    return list(codegen.diagnostics)


class GatingTests(unittest.TestCase):
    def test_the_namespaces_are_unknown_without_the_feature(self) -> None:
        codegen, project = compile_source(SOURCE)
        unknown = [d for d in diagnostics(codegen) if "unknown" in d.lower()]
        self.assertEqual(len(unknown), 1, diagnostics(codegen))
        self.assertIn("E0037", unknown[0])
        self.assertIn("Facemesh", unknown[0])
        self.assertEqual(project.extensions, [])

    def test_the_feature_turns_the_namespace_on(self):
        codegen, project = compile_source(SOURCE, features=FEATURE)
        self.assertFalse([d for d in diagnostics(codegen) if "unknown" in d.lower()],
                         diagnostics(codegen))
        self.assertEqual(project.extensions, ["facemesh"])

    def test_a_menu_argument_becomes_a_menu_shadow(self):
        _, project = compile_source(SOURCE, features=FEATURE)
        blocks = blocks_of(project, "main")
        call = next(b for b in blocks.values()
                    if b["opcode"] == "facemesh_getXFromMenu")
        menu_id = call["inputs"]["KEYPOINT"][1]
        menu = blocks[menu_id]
        self.assertEqual(menu["opcode"], "facemesh_menu_keypointMenu")
        self.assertTrue(menu["shadow"])
        self.assertEqual(menu["fields"]["keypointMenu"][0], "nose")
        # The other argument is a dropdown too, and its value is the one given.
        other = blocks[call["inputs"]["OBJECT_NUMBER"][1]]
        self.assertEqual(other["opcode"], "facemesh_menu_objectNumberMenu")
        self.assertEqual(other["fields"]["objectNumberMenu"][0], "0")

    def test_every_block_in_the_table_resolves_to_itself(self):
        for extension_id, entry in PISHI_BLOCKS.items():
            namespace = entry["namespace"]
            for opcode, spec in entry["blocks"].items():
                with self.subTest(f"{namespace}.{opcode}"):
                    call = extensions.resolve(
                        namespace, opcode, len(spec["args"]), FEATURE
                    )
                    self.assertIsNotNone(call)
                    self.assertEqual(call.extension, extension_id)
                    self.assertEqual(call.opcode, opcode)
                    self.assertEqual(call.kind, spec["type"])

    def test_the_wrong_arity_does_not_resolve(self):
        self.assertIsNone(
            extensions.resolve("Facemesh", "getXFromMenu", 1, FEATURE)
        )
        self.assertIsNone(
            extensions.resolve("Facemesh", "getXFromMenu", 3, FEATURE)
        )

    def test_a_pishi_namespace_is_never_mistaken_for_a_list(self):
        # ``get``/``length``/``contains`` are list methods too, so a pishi call
        # that shares the name used to invent a *list* called ``Facemesh`` and
        # never reach the namespace -- and ``Facemesh.length()`` crashed the
        # compiler with IndexError.
        codegen, project = compile_source(
            "class a { void f() { x = Facemesh.getX(1); y = Facemesh.length(); } }",
            features=FEATURE,
        )
        messages = "\n".join(diagnostics(codegen))
        self.assertIn("E0076", messages)
        self.assertIn("takes 2 argument(s)", messages)
        self.assertIn("E0040", messages)
        list_ops = {
            "data_itemoflist", "data_lengthoflist", "data_listcontainsitem",
            "data_itemnumoflist",
        }
        self.assertEqual(set(), list_ops & {b["opcode"] for b in blocks_of(project, "a").values()})

    def test_a_namespace_that_is_not_pishi_stays_unknown(self):
        self.assertIsNone(extensions.resolve("Facemash", "getX", 2, FEATURE))


class ConflictTests(unittest.TestCase):
    def test_a_user_variable_with_a_namespace_name_warns(self):
        codegen, _ = compile_source(
            "#enable_feature<pishiSupport>;\npublic Facemesh = 0;\n" + SOURCE,
            features=FEATURE,
        )
        found = [d for d in diagnostics(codegen) if d.startswith("0:0: W0028")]
        self.assertEqual(len(found), 1, diagnostics(codegen))
        self.assertIn("variable 'Facemesh'", found[0])

    def test_a_user_list_with_a_namespace_name_warns(self) -> None:
        codegen, _ = compile_source(
            "#enable_feature<pishiSupport>;\npublic Arduino = [];\nclass main { }",
            features=FEATURE,
        )
        found = [d for d in diagnostics(codegen) if "W0028" in d]
        self.assertEqual(len(found), 1, diagnostics(codegen))
        self.assertIn("list 'Arduino'", found[0])

    def test_a_sprite_with_a_namespace_name_warns(self):
        codegen, _ = compile_source(
            "class Arduino {\n    public void greenFlag() { }\n}\n",
            features=FEATURE,
        )
        found = [d for d in diagnostics(codegen) if "W0028" in d]
        self.assertEqual(len(found), 1, diagnostics(codegen))
        self.assertIn("sprite 'Arduino'", found[0])

    def test_without_the_feature_no_name_is_reserved(self):
        codegen, _ = compile_source(
            "public Facemesh = 0;\n" + SOURCE.replace("#enable_feature<pishiSupport>;", ""),
        )
        self.assertFalse([d for d in diagnostics(codegen) if "W0028" in d])

    def test_two_features_claiming_one_namespace_is_an_error(self):
        """No real extension is called `Arduino`, so the clash is made up here."""
        clash = {
            "someoneElsesArduino": {
                "namespace": "Arduino",
                "blocks": {"high": {"type": "COMMAND", "args": [["PIN", "NUMBER", ""]]}},
            }
        }
        with unittest.mock.patch.dict(extensions.EXTENSION_BLOCKS, clash):
            with unittest.mock.patch.dict(
                extensions._MERGED, clash                       # noqa: SLF001
            ):
                codegen, _ = compile_source("class main { }", features=FEATURE)
        found = [d for d in diagnostics(codegen) if "E0077" in d]
        self.assertEqual(len(found), 1, diagnostics(codegen))
        self.assertIn("Arduino", found[0])
        self.assertIn("someoneElsesArduino", found[0])


class RoundTripTests(unittest.TestCase):
    """A pishi project has to survive decompile → compile."""

    @classmethod
    def setUpClass(cls) -> None:
        from jswc.decompile import decompile
        from jswc.sb3_read import load_sb3

        cls.source, cls.diagnostics, _assets = decompile(
            load_sb3(ROOT / "examples" / "PishiExtensions.sb3"), include_assets=False
        )

    def test_the_source_asks_for_the_feature(self):
        self.assertIn("#enable_feature<pishiSupport>;", self.source)

    def test_no_block_is_left_unsupported(self):
        self.assertEqual(self.source.count("unsupported block"), 0)

    def test_a_menu_value_with_quotes_stays_a_string(self):
        """AIChatBot's model menu is JSON; unescaped it is not valid source."""
        self.assertIn('\\"provider\\"', self.source)

    def test_it_compiles_back_to_the_same_blocks(self):
        codegen, project = compile_source(self.source, allow_undefined=True)
        self.assertFalse([d for d in diagnostics(codegen)
                          if d.startswith("0:0: E") or " E0" in d[:12]],
                         diagnostics(codegen)[:6])
        self.assertEqual(
            sorted(project.extensions),
            ["aichatbot", "arduino", "facemesh", "googleteachablemachine",
             "handpose", "objectdetection", "posenet", "speechrecognition"],
        )


class GeneratedTableTests(unittest.TestCase):
    def test_the_table_matches_the_project_it_came_from(self):
        """`make`-free reproduction: the sidecar plus the .sb3 rebuild it exactly."""
        import extract_pishi

        kinds, order = extract_pishi.load_sidecar(extract_pishi.SIDECAR)
        catalogue, _ = extract_pishi.collect(
            ROOT / "examples" / "PishiExtensions.sb3", kinds, order
        )
        rendered = extract_pishi.render(catalogue, Path("examples/PishiExtensions.sb3"))
        self.assertEqual(
            rendered,
            (ROOT / "jswc" / "pishi_blocks.py").read_text(encoding="utf-8"),
            "run `python3 tools/extract_pishi.py examples/PishiExtensions.sb3 --write`",
        )

    def test_every_block_has_a_kind_the_compiler_knows(self):
        for entry in PISHI_BLOCKS.values():
            for opcode, spec in entry["blocks"].items():
                self.assertIn(spec["type"],
                              {"COMMAND", "REPORTER", "BOOLEAN", "HAT"}, opcode)
                for name, arg_type, _menu in spec["args"]:
                    self.assertIn(arg_type, extensions.PRIMITIVES, f"{opcode}.{name}")

    def test_every_menu_an_argument_names_is_declared(self):
        for entry in PISHI_BLOCKS.values():
            menus = entry.get("menus", {})
            for opcode, spec in entry["blocks"].items():
                for name, _type, menu in spec["args"]:
                    if menu:
                        self.assertIn(menu, menus, f"{opcode}.{name}")


import unittest.mock  # noqa: E402  (used by the clash test above)

if __name__ == "__main__":
    unittest.main()
