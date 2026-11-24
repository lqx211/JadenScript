"""Block placement: the same layout TurboWarp's "Clean up blocks" produces.

Every number asserted here was read off a real TurboWarp editor: the probe
source below is compiled, opened, and each block's ``height`` recorded, so a
change to ``jswc/layout.py`` that does not come with a fresh measurement fails.
``docs/CLI.md`` explains the model.

The old placement stepped ``y += 400`` per script, which put a long script on
top of the next one; the placement tests cover that instead.
"""

from __future__ import annotations

import unittest

from tests.helpers import compile_source

from jswc import layout

#: Heights measured in TurboWarp's editor for the same shapes.
MEASURED = {
    "event_whenflagclicked": 56,
    "procedures_definition": 72,
    "looks_show": 56,
    "looks_hide": 56,
    "looks_cleargraphiceffects": 56,
    "sensing_resettimer": 56,
    "control_stop": 48,
}

PROBE = """
public Probe.i = 0;

class probe {
    public void greenFlag() {
        Looks.show();
    }
    void rows() {
        Looks.show();
        Looks.hide();
        Looks.clearGraphicEffects();
        Sensing.resetTimer();
    }
    void emptyIf() {
        if (1 == 1) { }
    }
    void filledIf() {
        if (1 == 1) { Looks.show(); }
    }
    void emptyThen() {
        if (1 == 1) { } else { Looks.show(); }
    }
    void emptyRepeat() {
        repeat (2) { }
    }
    void emptyForever() {
        while (1) { }
    }
    void stopAll() {
        Control.stopAll();
    }
    void withVar() {
        Motion.x = Probe.i + 1;
    }
}
"""


def probe_target():
    _, project = compile_source(PROBE)
    return next(t for t in project.targets if t.name == "probe")


def tops(target) -> list:
    return [block for block in target.blocks.values()
            if block.top_level and not block.shadow]


def find(target, opcode: str, **inputs):
    """The first block of ``opcode`` whose inputs have the given names."""
    for block in target.blocks.values():
        if block.opcode != opcode:
            continue
        if all(name in block.inputs for name in inputs):
            return block
    raise AssertionError(f"no {opcode} with {sorted(inputs)}")


class ModelTests(unittest.TestCase):
    """The measured geometry, one shape at a time."""

    def height(self, target, block) -> int:
        return layout.block_height(target.blocks, block, layout.reporter_ids(target))

    def test_every_single_block_height_matches_the_editor(self):
        target = probe_target()
        reporters = layout.reporter_ids(target)
        seen = {}
        for block in target.blocks.values():
            if block.shadow or block.id in reporters:
                continue
            seen.setdefault(block.opcode, layout.block_height(target.blocks, block, reporters))
        for opcode, expected in MEASURED.items():
            with self.subTest(opcode=opcode):
                self.assertIn(opcode, seen)
                self.assertEqual(seen[opcode], expected)

    def test_a_reporter_is_40_flat_and_grows_with_what_is_plugged_in(self):
        # `(1 == 1)` holds two literals: 40. `(Probe.i + 1)` holds a variable
        # pill (40), so it grows one notch to 48.
        target = probe_target()
        self.assertEqual(self.height(target, find(target, "operator_equals")), 40)
        self.assertEqual(self.height(target, find(target, "operator_add")), 48)
        self.assertEqual(self.height(target, find(target, "data_variable")), 40)

    def test_a_statement_is_56_and_grows_a_notch_per_8_px_of_input(self):
        # `set x to (Probe.i + 1)` with a 48 px operator inside: 48 + 16 = 64.
        target = probe_target()
        self.assertEqual(self.height(target, find(target, "motion_setx", X=True)), 64)
        self.assertEqual(self.height(target, find(target, "looks_show")), 56)

    def test_a_cap_block_is_a_notch_shorter(self):
        # `stop all` has no bottom notch to join with: 48, not 56.
        target = probe_target()
        self.assertEqual(self.height(target, find(target, "control_stop")), 48)

    def test_an_empty_substack_still_reserves_its_slot(self):
        # 56 header + 24 arm + 32 empty slot.
        target = probe_target()
        empty = [block for block in target.blocks.values()
                 if block.opcode in ("control_if", "control_repeat")
                 and not layout._substack(target.blocks, block, "SUBSTACK")]
        self.assertEqual(len(empty), 2)
        self.assertEqual([self.height(target, block) for block in empty], [112, 112])

    def test_forever_has_no_bottom_arm(self):
        # 56 header + 16 arm + 32 empty slot, one notch less than `if`.
        target = probe_target()
        self.assertEqual(self.height(target, find(target, "control_forever")), 104)

    def test_a_filled_substack_adds_its_rows(self):
        target = probe_target()
        filled = [block for block in target.blocks.values()
                  if block.opcode == "control_if"
                  and layout._substack(target.blocks, block, "SUBSTACK")]
        self.assertEqual([self.height(target, block) for block in filled], [136])

    def test_an_else_arm_costs_24_plus_its_rows(self):
        # `if (1 == 1) { } else { show }`: 56 + 24 + 32 (empty then) + 24 + 56.
        target = probe_target()
        self.assertEqual(self.height(target, find(target, "control_if_else")), 192)

    def test_a_substack_ending_in_a_cap_pushes_the_arm_down(self):
        # Measured on NotePlayer: an arm cannot tuck under a block with no
        # bottom notch, so it moves down one notch and the C-block is 8 taller.
        _, project = compile_source(
            "class a { void f(){ repeat (2) { Control.stopAll(); } } }"
        )
        target = next(t for t in project.targets if t.name == "a")
        repeat = find(target, "control_repeat")
        self.assertEqual(self.height(target, repeat), 56 + 24 + 8 + 48)

    def test_the_rows_of_a_script_share_their_joints(self):
        # `rows()` is a definition (72) over four plain rows, every joint
        # taking 8 px back: 72 + 4 * 56 - 4 * 8.
        target = probe_target()
        rows = find(target, "procedures_definition")
        self.assertEqual(layout.stack_height(target.blocks, rows.id), 72 + 4 * (56 - 8))


class PlacementTests(unittest.TestCase):
    def test_one_column_starts_at_turbowarps_origin(self):
        _, project = compile_source(
            "class a { public void greenFlag() { Looks.show(); } }"
        )
        target = next(t for t in project.targets if t.name == "a")
        (hat,) = tops(target)
        self.assertEqual((hat.x, hat.y), (layout.COLUMN_X, layout.COLUMN_Y))
        self.assertEqual((layout.COLUMN_X, layout.COLUMN_Y), (48, 64))

    def test_the_gap_between_scripts_is_72(self):
        _, project = compile_source(
            "class a {\n"
            "    public void greenFlag() { Looks.show(); Looks.hide(); }\n"
            "    public void whenCloned() { Looks.show(); }\n"
            "}"
        )
        target = next(t for t in project.targets if t.name == "a")
        first, second = sorted(tops(target), key=lambda block: block.y)
        height = layout.stack_height(target.blocks, first.id)
        self.assertEqual(height, 56 + 56 - 8 + 56 - 8)
        self.assertEqual(second.y - first.y, height + layout.ROW_GAP)
        self.assertEqual(layout.ROW_GAP, 72)

    def test_a_long_script_pushes_the_next_one_down(self):
        # The bug this replaced: every script was 400 px apart, so a 21-block
        # script (1032 px) had the next one drawn on top of it.
        body = "".join("        Looks.show();\n" for _ in range(20))
        _, project = compile_source(
            "class a {\n"
            "    void long() {\n" + body + "    }\n"
            "    void after() { Looks.hide(); }\n"
            "}"
        )
        target = next(t for t in project.targets if t.name == "a")
        first, second = sorted(tops(target), key=lambda block: block.y)
        height = layout.stack_height(target.blocks, first.id)
        self.assertGreater(height, 1000)
        self.assertEqual(second.y, first.y + height + layout.ROW_GAP)
        self.assertGreater(second.y, 1000, "the second script would have overlapped")

    def test_a_script_never_lands_on_the_one_above_it(self):
        source = "class a {\n" + "".join(
            f"    public void f{i}() {{ " + "Looks.show(); " * (i * 3) + "}\n"
            for i in range(5)
        ) + "}"
        _, project = compile_source(source)
        target = next(t for t in project.targets if t.name == "a")
        placed = sorted(tops(target), key=lambda block: (block.x, block.y))
        for above, below in zip(placed, placed[1:]):
            if above.x != below.x or above.y == below.y:
                continue
            height = layout.stack_height(target.blocks, above.id)
            self.assertGreaterEqual(
                below.y, above.y + height,
                f"{above.id} ({height} px) is drawn over {below.id}",
            )

    def test_explicit_positions_are_left_alone(self):
        # `// at 120,240` is the author's position, not a layout suggestion.
        _, project = compile_source(
            "class a { public void greenFlag() { // at 120,240\n"
            "    Looks.show(); } }"
        )
        target = next(t for t in project.targets if t.name == "a")
        (hat,) = tops(target)
        self.assertEqual((hat.x, hat.y), (120, 240))

    def test_columns_split_by_height(self):
        # Six scripts of 120 px each do not fit in a 600 px column, so the
        # layout continues to the right instead of growing without bound.
        source = "class a {\n" + "".join(
            f"    public void f{i}() {{ Looks.show(); }}\n" for i in range(6)
        ) + "}"
        _, project = compile_source(source, column_height=600)
        target = next(t for t in project.targets if t.name == "a")
        columns: dict[float, int] = {}
        for block in tops(target):
            columns[block.x] = columns.get(block.x, 0) + 1
        self.assertGreater(len(columns), 1, columns)
        starts = {block.y for block in tops(target) if block.y == layout.COLUMN_Y}
        self.assertEqual(len(starts), 1)

    def test_columns_do_not_touch_each_other(self):
        source = "class a {\n" + "".join(
            f"    public void f{i}() {{ Looks.show(); }}\n" for i in range(6)
        ) + "}"
        _, project = compile_source(source, column_height=400)
        target = next(t for t in project.targets if t.name == "a")
        columns = sorted({block.x for block in tops(target)})
        self.assertGreater(len(columns), 1)
        for left, right in zip(columns, columns[1:]):
            widest = max(layout.stack_width(target.blocks, block.id)
                         for block in tops(target) if block.x == left)
            self.assertGreaterEqual(right - left, widest + layout.COLUMN_GAP)

    def test_no_two_scripts_share_a_position(self):
        source = "class a {\n" + "".join(
            f"    public void f{i}() {{ Looks.show(); }}\n" for i in range(4)
        ) + "}"
        _, project = compile_source(source)
        target = next(t for t in project.targets if t.name == "a")
        placed = [(block.x, block.y) for block in tops(target)]
        self.assertEqual(len(placed), len(set(placed)))


if __name__ == "__main__":
    unittest.main()
