"""Cross-check the opcode registry against the reference project's real shapes.

Every input slot named in ``jswc/opcodes.py`` must exist on the corresponding
block in a real TurboWarp export. This is the guard that would have caught
``motion_changexby`` taking ``DX`` instead of ``X``.
"""

from __future__ import annotations

import re
import unittest

from tests.helpers import ROOT

from jswc import opcodes as O

SHAPES_FILE = ROOT / "docs" / "reference" / "opcode-shapes.md"
_ROW = re.compile(r"^\| `([a-z0-9_]+)` \| \d+ \| (.*?) \| (.*?) \|$")


def load_shapes() -> dict[str, tuple[set[str], set[str]]]:
    shapes: dict[str, tuple[set[str], set[str]]] = {}
    for line in SHAPES_FILE.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        opcode, inputs, fields = match.groups()
        shapes[opcode] = (
            set(re.findall(r"`([^`]+)`", inputs)),
            set(re.findall(r"`([^`]+)`", fields)),
        )
    return shapes


class OpcodeMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shapes = load_shapes()

    def test_reference_shapes_were_parsed(self):
        self.assertGreaterEqual(len(self.shapes), 80)
        self.assertIn("motion_changexby", self.shapes)

    def test_command_inputs_exist_on_the_real_block(self):
        for name, spec in O.COMMANDS.items():
            if spec.opcode not in self.shapes:
                continue
            real_inputs, real_fields = self.shapes[spec.opcode]
            for param in spec.params:
                if param.prim == O.FIELD:
                    self.assertIn(param.name, real_fields, f"{name}: {spec.opcode} has no field {param.name}")
                    continue
                self.assertIn(param.name, real_inputs, f"{name}: {spec.opcode} has no {param.name}")
            for field_name, _ in spec.fields:
                self.assertIn(field_name, real_fields, f"{name}: {spec.opcode} has no field {field_name}")

    def test_reporter_inputs_exist_on_the_real_block(self):
        for name, spec in O.REPORTERS.items():
            if spec.opcode not in self.shapes:
                continue
            real_inputs, real_fields = self.shapes[spec.opcode]
            for param in spec.params:
                if param.prim == O.FIELD:
                    self.assertIn(param.name, real_fields, f"{name}: {spec.opcode} has no field {param.name}")
                    continue
                self.assertIn(param.name, real_inputs, f"{name}: {spec.opcode} has no {param.name}")
            for field_name, _ in spec.fields:
                self.assertIn(field_name, real_fields, f"{name}: {spec.opcode} has no field {field_name}")

    def test_property_slots_exist_on_the_real_blocks(self):
        for name, prop in O.PROPERTIES.items():
            for opcode, slot in (
                (prop.get, None),
                (prop.set, prop.set_input),
                (prop.change, prop.change_slot),
            ):
                if opcode not in self.shapes or slot is None:
                    continue
                real_inputs, _ = self.shapes[opcode]
                self.assertIn(slot, real_inputs, f"{name}: {opcode} has no {slot}")

    def test_every_mapped_opcode_is_known_to_the_vm(self):
        core_prefixes = (
            "motion_", "looks_", "sound_", "event_", "control_",
            "sensing_", "operator_", "data_", "procedures_",
        )
        for spec in list(O.COMMANDS.values()) + list(O.REPORTERS.values()):
            if spec.extension:
                self.assertTrue(spec.opcode.startswith(spec.extension + "_"),
                                f"{spec.opcode} should be prefixed by {spec.extension}")
            else:
                self.assertTrue(spec.opcode.startswith(core_prefixes),
                                f"{spec.opcode} has no extension and is not a core opcode")


if __name__ == "__main__":
    unittest.main()
