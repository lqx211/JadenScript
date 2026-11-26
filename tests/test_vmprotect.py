"""vmProtect: the protected build must keep the source behaviour."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from jswc.ir import Project
from jswc.sb3 import write_sb3
from jswc.validator import validate
from jswc.vmprotect import _HATS

from tests.helpers import ROOT, blocks_of, compile_source


def _substack_depth(blocks: dict) -> int:
    """How deeply the C-blocks nest (the deepest ``SUBSTACK`` chain)."""
    depth: dict[str, int] = {}
    for start in blocks:
        stack = [(start, False)]
        while stack:
            bid, done = stack.pop()
            if done:
                best = 0
                for name, value in (blocks[bid].get("inputs") or {}).items():
                    if not name.startswith("SUBSTACK"):
                        continue
                    if (
                        isinstance(value, list)
                        and len(value) > 1
                        and isinstance(value[1], str)
                        and value[1] in blocks
                    ):
                        best = max(best, 1 + depth.get(value[1], 0))
                depth[bid] = best
                continue
            if bid in depth:
                continue
            stack.append((bid, True))
            block = blocks[bid]
            children = [
                value[1]
                for name, value in (block.get("inputs") or {}).items()
                if name.startswith("SUBSTACK")
                and isinstance(value, list)
                and len(value) > 1
                and isinstance(value[1], str)
                and value[1] in blocks
                and value[1] not in depth
            ]
            if block.get("next") in blocks and block["next"] not in depth:
                children.append(block["next"])
            stack.extend((child, False) for child in children)
    return max(depth.values(), default=0)


def _stage_lists(project: Project) -> dict[str, list]:
    return {
        values[0]: values[1]
        for values in project.stage.lists.values()
    }



def _strip_runtime(result: dict) -> dict:
    return {
        key: value
        for key, value in result.items()
        if not key.split(".", 1)[-1].startswith("__vm_")
        and not (key.endswith(".message1") and value == "message1")
    }

def _proccodes(blocks: dict) -> list[str]:
    return [
        block.get("mutation", {}).get("proccode", "")
        for block in blocks.values()
        if isinstance(block, dict) and block.get("opcode") == "procedures_call"
    ]


SOURCE = """\
public g = 0;
class a {
    private n = 0;
    public void greenFlag() {
        n += 2;
        g += 1;
        Control.broadcast("onHit");
        Control.wait(0.01);
        n += 3;
    }
}
class b {
    private m = 0;
    public void onHit() { // hook: broadcast onHit
        m += 5;
        g += 10;
    }
}
"""


class ProtectedBuildTests(unittest.TestCase):
    def test_hat_body_becomes_a_vm_start_call(self):
        _, project = compile_source(
            "public x = 0;\nclass a { public void greenFlag() { x = 1; } }\n",
            features={"commentMetadata", "vmProtect"},
            vm_style="list",
        )
        lists = _stage_lists(project)
        self.assertIn("__vm_ops", lists)
        self.assertIn("__vm_args", lists)
        self.assertIn("__vm_k", lists)
        self.assertIn("__vm_stack", lists)
        self.assertIn("__vm_free", lists)
        self.assertIn("__vm_start %s %s", _proccodes(blocks_of(project, "a")))
        self.assertNotIn("data_setvariableto", blocks_of(project, "a"))

    def test_custom_block_bodies_are_virtualised(self):
        """One VM program per custom block and per hat, counted from the source.

        The fixture is a hand-edited sample, so the counts are derived instead
        of pinned: adding a custom block to it used to break this test without
        anything being wrong with ``vmProtect``.
        """
        source = (ROOT / "examples" / "helloworld.jsw").read_text(encoding="utf-8")
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        blocks = blocks_of(project, "core")
        calls = _proccodes(blocks)
        stubs = [name for name in calls if name.startswith("__vm_proc_start")]
        starts = [name for name in calls if name.startswith("__vm_start")]

        def proccode(definition: dict) -> str:
            prototype = blocks.get((definition.get("inputs") or {})
                                   .get("custom_block", [None, None])[1])
            return str(((prototype or {}).get("mutation") or {}).get("proccode", ""))

        authored = [
            block for block in blocks.values()
            if block.get("opcode") == "procedures_definition"
            and not proccode(block).startswith("__vm")
        ]
        # The arena reset is a green-flag script of its own and does not start
        # the VM; it is the one whose chain touches ``__vm_active``.
        def is_reset(hat: dict) -> bool:
            seen: set[str] = set()
            pending: list[str | None] = [hat.get("next")]
            while pending:
                bid = pending.pop()
                if bid is None or bid in seen or bid not in blocks:
                    continue
                seen.add(bid)
                block = blocks[bid]
                if "__vm_active" in json.dumps(block.get("fields") or {}):
                    return True
                pending.append(block.get("next"))
                for value in (block.get("inputs") or {}).values():
                    if isinstance(value, list) and len(value) > 1:
                        if isinstance(value[1], str):
                            pending.append(value[1])
                        elif isinstance(value[1], list):
                            pending.extend(
                                item for item in value[1] if isinstance(item, str)
                            )
            return False

        hats = [
            block for block in blocks.values()
            if block.get("topLevel") and block["opcode"] in _HATS
            and not is_reset(block)
        ]
        self.assertEqual(len(stubs), len(authored), sorted(proccode(b) for b in authored))
        self.assertEqual(len(starts), len(hats), calls)
        self.assertIn(
            f"I0004 vmProtect virtualised {len(authored) + len(hats)} program(s)",
            "\n".join(codegen.diagnostics),
        )

    def test_the_source_directive_turns_it_on(self):
        _, project = compile_source(
            "#enable_feature<vmProtect>;\n"
            "public x = 0;\n"
            "class a { public void greenFlag() { x = 1; } }\n",
        )
        self.assertIn("__vm_switch %s", _proccodes(blocks_of(project, "a")))
        self.assertNotIn("__vm_ops", _stage_lists(project))

    def test_switch_case_list_calls_compile_and_virtualise(self):
        # The h0-style state machine calls list methods inside switch cases.
        # Those Case nodes are not ordinary Node objects, so list inference
        # used to miss them and lower ``s.get`` as an unknown call.
        source = (
            "public out = 0;\n"
            "class a {\n"
            "  private s = [];\n"
            "  public void greenFlag() {\n"
            "    switch (1) {\n"
            '      case 1 { s.push("hello"); out = s.get(1); }\n'
            "      case _ { out = -1; }\n"
            "    }\n"
            "  }\n"
            "}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertFalse(
            [line for line in codegen.diagnostics if line.split()[1].startswith("E")],
            codegen.diagnostics,
        )
        self.assertTrue(
            any("I0004 vmProtect virtualised 1 program(s)" in line
                for line in codegen.diagnostics),
            codegen.diagnostics,
        )
        target = next(t for t in project.targets if not t.is_stage)
        self.assertIn("__vm_switch %s", _proccodes(blocks_of(project, target.name)))

    def test_frame_arena_grows_instead_of_wrapping(self):
        source = "public x = 0;\nclass a { public void greenFlag() { x = 1; } }\n"
        _, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        blocks = blocks_of(project, "a")
        grew = [
            block
            for block in blocks.values()
            if block.get("opcode") == "data_addtolist"
            and (block.get("fields", {}).get("LIST") or [""])[0] == "__vm_stack"
        ]
        self.assertTrue(grew, "the frame allocator never grows __vm_stack")

    def test_many_calls_do_not_widen_the_frame(self):
        """Scratch slots are reused by operand depth, not per call site.

        A decompiled state machine calls ``v.get(...)`` a thousand times.  Each
        call used to reserve its own slot, so the frame (the stride) grew with
        the *length* of the procedure and the whole build hit the stride limit.
        Only the maximum operand depth matters.
        """
        body = "".join(f'    v.set({index}, {index});\n' for index in range(1, 401))
        source = (
            "public x = 0;\n"
            "public v = [];\n"
            "class a { public void greenFlag() {\n"
            f"{body}"
            "    x = v.length();\n"
            "} }\n"
        )
        codegen, _ = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertIn("I0004 vmProtect virtualised 1 program(s)",
                      "\n".join(codegen.diagnostics))
        self.assertFalse(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_dispatch_depth_is_logarithmic(self):
        """The VRuntime dispatch must not nest one block per instruction.

        ``switch`` in JSW is a linear if/elif chain, so a project with a few
        thousand instructions used to nest that deep -- TurboWarp's JS compiler
        gives up on it, and every VM step walked the chain.  The dispatch
        bisects on the label instead.
        """
        body = "".join(f"    x += {index};\n" for index in range(1, 601))
        source = (
            "public x = 0;\n"
            "class a { public void greenFlag() {\n"
            f"{body}"
            "} }\n"
        )
        for style in ("h0", "list"):
            with self.subTest(style=style):
                _, project = compile_source(
                    source,
                    features={"commentMetadata", "vmProtect"},
                    vm_style=style,
                )
                target = next(t for t in project.targets if not t.is_stage)
                blocks = {bid: block.to_json() for bid, block in target.blocks.items()}
                depth = _substack_depth(blocks)
                self.assertLessEqual(depth, 24, f"{style} dispatch nests {depth} deep")

    def test_a_very_wide_frame_starts_a_small_arena(self):
        """A deep expression must not preallocate 320 of those giant frames.

        The arena is a slot budget: a wide frame means fewer starting frames and
        the allocator grows the list on demand.
        """
        deep = " + ".join(["1"] * 300)
        source = (
            "public x = 0;\n"
            f"class a {{ public void greenFlag() {{ x = {deep}; }} }}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertIn("I0004 vmProtect virtualised 1 program(s)",
                      "\n".join(codegen.diagnostics))
        stack = _stage_lists(project)["__vm_stack"]
        self.assertLessEqual(len(stack), 12_288)
        self.assertGreater(len(stack), 0)

    def test_only_the_warp_call_chain_stays_native(self):
        """Warp mode is scoped to the callee, so its *callers* stay protected.

        ``sequencer.stepToProcedure`` sets ``warpMode`` on the stack frame it
        pushes, and ``popStack`` drops it again on return.  A non-warp caller
        therefore never runs the VM loop in warp mode: only the warp procedure
        and everything it calls have to stay native.
        """
        source = (
            "public x = 0;\n"
            "class a {\n"
            "  private void fast() { // warp\n"
            "    x += 1;\n"
            "  }\n"
            "  private void caller() { fast(); }\n"
            "  public void greenFlag() { caller(); }\n"
            "}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        calls = _proccodes(blocks_of(project, "a"))
        self.assertIn("__vm_start %s %s", calls)
        self.assertTrue(
            any(name.startswith("__vm_proc_start") for name in calls),
            "the caller of a warp procedure should still be virtualised",
        )
        # ``fast`` keeps its own body: it is the one that runs in warp mode.
        warp_body = [
            block["opcode"]
            for block in blocks_of(project, "a").values()
            if isinstance(block, dict) and block["opcode"] == "data_changevariableby"
        ]
        self.assertTrue(warp_body, "the warp body was replaced by a VM stub")
        self.assertTrue(
            any("W0031" in line and "warp custom block" in line
                for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_numeric_literals_keep_scratch_types(self):
        _, project = compile_source(
            'public a = 0;\npublic b = 0;\npublic c = 0;\n'
            'class x { public void greenFlag() { a = "abc"; b = "5"; c = 5.5; } }\n',
            features={"commentMetadata", "vmProtect"},
        )
        self.assertEqual(_stage_lists(project)["__vm_k"], ["abc", 5, 5.5])

    def test_vanilla_scratch_disables_vm_protect(self):
        codegen, project = compile_source(
            "public x = 0;\nclass a { public void greenFlag() { x = 1; } }\n",
            features={"commentMetadata", "vanillaScratch", "vmProtect"},
        )
        self.assertNotIn("__vm_ops", _stage_lists(project))
        self.assertTrue(
            any("W0030" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_unsupported_script_is_left_native(self):
        codegen, project = compile_source(
            "public x = 0;\n"
            "class a {\n"
            "  public void greenFlag() {\n"
            "    private name = \"_i\";\n"
            "    Temp.forEachThreadVariable(name, 2) { x += 1; }\n"
            "  }\n"
            "}\n",
            features={"commentMetadata", "vmProtect"},
            allow_undefined=True,
        )
        self.assertNotIn("__vm_start %s %s", _proccodes(blocks_of(project, "a")))
        self.assertTrue(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_a_custom_block_call_stays_inside_the_interpreter(self):
        source = (
            "public x = 0;\n"
            "class a {\n"
            "  private void helper(n) { x += n; }\n"
            "  public void greenFlag() { x = 1; helper(2); helper(3); }\n"
            "}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertIn("__vm_start %s %s", _proccodes(blocks_of(project, "a")))
        self.assertFalse(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )
        self.assertIn("helper %s", _proccodes(blocks_of(project, "a")))

    def test_simple_commands_and_reporters_are_virtualised(self):
        source = (
            "public x = 0;\n"
            "public y = 0;\n"
            "public z = 0;\n"
            "class a {\n"
            "  public void greenFlag() {\n"
            "    Looks.hide();\n"
            "    Motion.setXY(10, 20);\n"
            "    x = Motion.xPosition;\n"
            "    y = Operators.math(\"abs\", -3) + Operators.round(2.6);\n"
            "    Looks.setEffect(\"color\", 10);\n"
            "    Looks.clearGraphicEffects();\n"
            "  }\n"
            "}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertIn("__vm_start %s %s", _proccodes(blocks_of(project, "a")))
        self.assertFalse(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_property_setters_are_virtualised(self):
        source = (
            "public x = 0;\n"
            "class a { public void greenFlag() {\n"
            "  Motion.x = 5;\n"
            "  Motion.x += 3;\n"
            "  x = Motion.x;\n"
            "} }\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertIn("__vm_start %s %s", _proccodes(blocks_of(project, "a")))
        self.assertFalse(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_list_commands_and_reporters_are_virtualised(self):
        source = (
            "public L = [];\n"
            "public n = 0;\n"
            "public at = 0;\n"
            "class a {\n"
            "  public void greenFlag() {\n"
            "    L.clear();\n"
            "    L.add(7);\n"
            "    L.add(9);\n"
            "    n = L.length();\n"
            "    at = L.indexOf(9);\n"
            "  }\n"
            "}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertIn("__vm_start %s %s", _proccodes(blocks_of(project, "a")))
        self.assertFalse(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_field_literals_do_not_share_a_native_case(self):
        source = (
            "public x = 0;\n"
            "class a { public void greenFlag() {\n"
            "  Looks.setEffect(\"color\", 10);\n"
            "  Looks.setEffect(\"brightness\", 20);\n"
            "} }\n"
        )
        _, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        effects = [
            block["fields"]["EFFECT"][0]
            for block in blocks_of(project, "a").values()
            if block.get("opcode") == "looks_seteffectto"
        ]
        self.assertEqual(sorted(effects), ["brightness", "color"])

    def test_the_interpreter_stays_non_warp_under_warp_mode(self):
        _, project = compile_source(
            "public x = 0;\nclass a { public void greenFlag() { x = 1; } }\n",
            features={"commentMetadata", "vmProtect"},
            warp=True,
        )
        warps = []
        for target in project.targets:
            for block in target.blocks.values():
                if block.opcode != "procedures_definition":
                    continue
                prototype = target.blocks[block.inputs["custom_block"][1]]
                proccode = prototype.mutation.get("proccode", "")
                if "__vm_run" in proccode or "__vm_step" in proccode:
                    warps.append(prototype.mutation.get("warp"))
        self.assertTrue(warps)
        self.assertTrue(all(warp == "false" for warp in warps), warps)

    def test_warp_mode_leaves_custom_blocks_native(self):
        source = (
            "public x = 0;\n"
            "class a {\n"
            "  private void helper(n) { x += n; }\n"
            "  public void greenFlag() { helper(2); }\n"
            "}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
            warp=True,
        )
        calls = _proccodes(blocks_of(project, "a"))
        self.assertFalse(
            [name for name in calls if name.startswith("__vm_proc_start")],
            calls,
        )
        self.assertIn("__vm_start %s %s", calls)
        self.assertTrue(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_wait_until_is_virtualised(self):
        source = (
            "public x = 0;\n"
            "class a { public void greenFlag() {\n"
            "  Control.waitUntil(x > 0);\n"
            "  x = 1;\n"
            "} }\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertIn("__vm_start %s %s", _proccodes(blocks_of(project, "a")))
        self.assertFalse(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_thread_variable_loop_is_virtualised(self):
        codegen, _ = compile_source(
            "public x = 0;\n"
            "class a {\n"
            "  public void greenFlag() {\n"
            '    Temp.forEachThreadVariable("_i", 2) { x += 1; }\n'
            "  }\n"
            "}\n",
            features={"commentMetadata", "vmProtect"},
            allow_undefined=True,
        )
        self.assertIn("I0004 vmProtect virtualised 1 program(s)",
                      "\n".join(codegen.diagnostics))
        self.assertFalse(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_a_caller_of_an_infinite_native_block_stays_native(self):
        source = (
            "public x = 0;\n"
            "class a {\n"
            "  private void spin() { while (1) { Control.wait(0); } }\n"
            "  public void greenFlag() { x = 1; spin(); }\n"
            "}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertNotIn("__vm_start %s %s", _proccodes(blocks_of(project, "a")))
        self.assertTrue(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )

    def test_obfuscation_keeps_vm_broadcasts_wired(self):
        source = (
            "public x = 0;\n"
            "class a {\n"
            "  public void greenFlag() { Control.broadcast(\"go\"); x = 1; }\n"
            "  public void onGo() { // hook: broadcast go\n"
            "    x += 1;\n"
            "  }\n"
            "}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect", "doObfuscation"},
        )
        self.assertFalse(
            [line for line in codegen.diagnostics if line.split()[1].startswith("E")],
            codegen.diagnostics,
        )
        messages = set(project.stage.broadcasts.values())
        self.assertTrue(messages)
        queued = []
        for target in project.targets:
            for block in target.blocks.values():
                if block.opcode != "procedures_call":
                    continue
                for raw in (block.inputs or {}).values():
                    if (
                        isinstance(raw, list)
                        and len(raw) > 1
                        and isinstance(raw[1], list)
                        and raw[1][0] == 10
                        and str(raw[1][1]) in messages
                    ):
                        queued.append(str(raw[1][1]))
        self.assertTrue(queued, "the queued message was not renamed")

    def test_vm_layout_is_randomized_and_seeded(self):
        source = (
            "public x = 0;\n"
            "class a { public void greenFlag(){ x = 1; repeat (2) { x += 1; } } }\n"
        )
        _, first = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
            vm_style="list",
            vm_seed=1,
        )
        _, second = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
            vm_style="list",
            vm_seed=2,
        )
        _, repeat = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
            vm_style="list",
            vm_seed=1,
        )
        first_ops = _stage_lists(first)["__vm_ops"]
        self.assertNotEqual(first_ops, _stage_lists(second)["__vm_ops"])
        self.assertEqual(first_ops, _stage_lists(repeat)["__vm_ops"])

    def test_h0_instruction_bodies_are_split_into_helpers(self):
        source = (
            "public x = 0;\n"
            "class a { public void greenFlag(){ x = 1; repeat (2) { x += 1; } } }\n"
        )
        _, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
            vm_style="h0",
            vm_split=True,
            vm_seed=1,
        )
        calls = _proccodes(blocks_of(project, "a"))
        self.assertTrue(any(name.startswith("__vm_h") for name in calls))
        self.assertIn("__vm_switch %s", calls)

    def test_h0_layout_is_randomized_and_seeded(self):
        source = (
            "public x = 0;\n"
            "class a { public void greenFlag(){ x = 1; repeat (2) { x += 1; } } }\n"
        )
        def helpers(seed: int) -> list[str]:
            _, project = compile_source(
                source,
                features={"commentMetadata", "vmProtect"},
                vm_style="h0",
                vm_seed=seed,
            )
            return sorted(
                name
                for name in _proccodes(blocks_of(project, "a"))
                if name.startswith("__vm_h")
            )
        self.assertEqual(helpers(1), helpers(1))
        self.assertNotEqual(helpers(1), helpers(2))

    def test_extension_commands_and_reporters_are_virtualised(self):
        source = (
            "public x = 0;\n"
            "class a {\n"
            "  public void greenFlag() {\n"
            '    Timer.startTimer("t");\n'
            '    x = Timer.getTimer("t");\n'
            '    if (JSON.isValid("{}")) { x += 1; }\n'
            '    console.log("hi");\n'
            '    Temp.setThreadVariable("_v", x);\n'
            '    x = Temp.getThreadVariable("_v");\n'
            '    x = Runtime.stageWidth;\n'
            "  }\n"
            "}\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
        )
        self.assertIn("I0004 vmProtect virtualised 1 program(s)",
                      "\n".join(codegen.diagnostics))
        self.assertFalse(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )
        self.assertEqual(validate(project), [])
        emitted = {
            block.opcode for block in project.targets[1].blocks.values()
            if hasattr(block, "opcode")
        }
        self.assertIn("lmsTimers_startResetTimer", emitted)
        self.assertIn("lmsTimers_valueOfTimer", emitted)
        self.assertIn("skyhigh173JSON_json_is_valid", emitted)

    def test_extension_hats_keep_their_trigger_and_virtualise_the_body(self):
        source = (
            "class a { public void h() { // hook: when tilted [right]\n"
            "    Looks.say(1);\n"
            "} }\n"
        )
        codegen, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
            allow_undefined=True,
        )
        self.assertIn("I0004 vmProtect virtualised 1 program(s)",
                      "\n".join(codegen.diagnostics))
        self.assertFalse(
            any("W0031" in line for line in codegen.diagnostics),
            codegen.diagnostics,
        )
        target = next(t for t in project.targets if not t.is_stage)
        emitted = {b.opcode for b in target.blocks.values() if hasattr(b, "opcode")}
        self.assertIn("microbit_whenTilted", emitted)
        self.assertIn("__vm_start %s %s", _proccodes(blocks_of(project, "a")))
        self.assertEqual(validate(project), [])

    def test_completed_runs_clear_the_vm_arena(self):
        _, project = compile_source(
            "public x = 0;\nclass a { public void greenFlag(){ x = 1; } }\n",
            features={"commentMetadata", "vmProtect"},
        )
        lists = _stage_lists(project)
        self.assertIn("__vm_active", lists)
        target = next(t for t in reversed(project.targets) if not t.is_stage)
        ops = [b.opcode for b in target.blocks.values() if hasattr(b, "opcode")]
        self.assertIn("event_whenflagclicked", ops)
        self.assertIn("control_if", ops)
        self.assertIn("data_replaceitemoflist", ops)
        self.assertIn("data_deletealloflist", ops)

    def test_crypt_levels_add_checksum_and_decoys(self):
        source = (
            "public x = 0;\n"
            "class a { public void greenFlag(){ x = 1; repeat (2) { x += 1; } } }\n"
        )
        sizes = {}
        for style in ("h0", "list"):
            for crypt in (1, 2, 3):
                _, project = compile_source(
                    source,
                    features={"commentMetadata", "vmProtect"},
                    vm_style=style,
                    vm_crypt=crypt,
                    vm_seed=11,
                )
                lists = _stage_lists(project)
                self.assertEqual("__vm_check" in lists, crypt >= 2)
                sizes[(style, crypt)] = sum(
                    len(t.blocks) for t in project.targets
                )
        self.assertGreater(sizes[("h0", 3)], sizes[("h0", 1)])
        self.assertGreater(sizes[("list", 3)], sizes[("list", 1)])

    def test_budget_sets_the_step_count(self):
        _, project = compile_source(
            "public x = 0;\nclass a { public void greenFlag() { x = 1; } }\n",
            features={"commentMetadata", "vmProtect"},
            vm_budget=3,
            vm_style="list",
        )
        calls = _proccodes(blocks_of(project, "a"))
        self.assertEqual(calls.count("__vm_step %s"), 3)


class DiagnosticAnchorTests(unittest.TestCase):
    """A note about one sprite names the sprite, not line 0.

    vmProtect runs on the compiled blocks, so its notes used to carry a
    hard-coded ``0:0`` — a location that does not exist, and the one thing a
    reader wants is *which* sprite stayed native.
    """

    COMPUTED_BROADCAST = (
        'public m = "hi";\n'
        "class a { public void greenFlag() { Control.broadcast(m); } }\n"
    )
    WARP_PROC = (
        "class a {\n"
        "    public void greenFlag() { tick(); }\n"
        '    private void tick() { // warp\n        Looks.say("x");\n    }\n'
        "}\n"
    )

    def test_the_only_native_program_points_at_its_class(self):
        codegen, _ = compile_source(
            self.COMPUTED_BROADCAST, features={"commentMetadata", "vmProtect"}
        )
        notes = [line for line in codegen.diagnostics if "W0031" in line]
        self.assertTrue(notes, codegen.diagnostics)
        self.assertTrue(notes[0].startswith("2:7: W0031"), notes[0])
        self.assertIn("native in 'a'", notes[0])

    def test_a_partly_protected_sprite_still_names_it(self):
        codegen, _ = compile_source(
            self.WARP_PROC, features={"commentMetadata", "vmProtect"}
        )
        notes = [line for line in codegen.diagnostics if "W0031" in line]
        self.assertTrue(notes, codegen.diagnostics)
        self.assertTrue(notes[0].startswith("1:7: W0031"), notes[0])
        self.assertIn("warp custom block", notes[0])

    def test_the_summary_note_does_not_claim_line_zero(self):
        codegen, _ = compile_source(
            "public x = 0;\nclass a { public void greenFlag() { x = 1; } }\n",
            features={"commentMetadata", "vmProtect"},
        )
        info = [line for line in codegen.diagnostics if "I0004" in line]
        self.assertTrue(info, codegen.diagnostics)
        self.assertTrue(info[0].startswith("1:1: I0004"), info)


class GeneratedRuntimeDiagnosticTests(unittest.TestCase):
    """The interpreter's own code is not the user's code.

    The ``list`` runtime flushes its broadcast queue with
    ``Control.broadcast(__vm_stack.get(...))`` — a computed name by design, and
    one the obfuscator follows into ``__vm_queue_broadcast``. It used to be
    linted as if the user had written it, at a position inside the generated
    source (``helloworld.jsw:18:42`` is ``public void greenFlag(){``).
    """

    def test_the_runtime_does_not_warn_about_its_own_broadcast(self):
        codegen, _ = compile_source(
            'class a { public void greenFlag() { Looks.say("hi"); } }',
            features={"commentMetadata", "vmProtect", "doObfuscation"},
        )
        noise = [d for d in codegen.diagnostics if "W0004" in d or "W0023" in d]
        self.assertFalse(noise, noise)

    def test_a_computed_broadcast_in_the_users_own_code_still_warns(self):
        source = (
            'public m = "x";\n'
            "class a { public void greenFlag() { m = m; Control.broadcast(m); } }\n"
        )
        codegen, _ = compile_source(
            source, features={"commentMetadata", "vmProtect", "doObfuscation"}
        )
        notes = [d for d in codegen.diagnostics if "W0023" in d]
        self.assertTrue(notes, codegen.diagnostics)
        self.assertTrue(notes[0].startswith("2:"), notes[0])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ExecutionTests(unittest.TestCase):
    """Compare the protected project with the ordinary build in scratch-vm."""

    def _run(
        self, source: str, features: set[str], budget: int,
        vm_style: str = "list", vm_split: bool = False,
        vm_crypt: int = 1, vm_seed: int | None = None,
    ) -> dict:
        codegen, _ = compile_source(
            source,
            features=features,
            vm_budget=budget,
            vm_style=vm_style,
            vm_split=vm_split,
            vm_crypt=vm_crypt,
            vm_seed=vm_seed,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = write_sb3(codegen.project, Path(tmp) / "project.sb3")
            harness = Path(tmp) / "run.js"
            harness.write_text(
                "const VM=require(process.argv[2]);\n"
                "(async()=>{\n"
                "  const fs=require('fs');\n"
                "  const vm=new VM();\n"
                "  const says=[];\n"
                "  vm.runtime.on('SAY',(t,k,m)=>says.push(String(m)));\n"
                "  await vm.loadProject(fs.readFileSync(process.argv[3]));\n"
                "  vm.start(); vm.greenFlag();\n"
                "  await new Promise(r=>setTimeout(r,+process.argv[4]));\n"
                "  const out={__says:says};\n"
                "  for (const t of vm.runtime.targets) {\n"
                "    if (!t.isOriginal) continue;\n"
                "    for (const v of Object.values(t.variables)) out[t.getName()+'.'+v.name]=v.value;\n"
                "  }\n"
                "  console.log(JSON.stringify(out)); process.exit(0);\n"
                "})().catch(e=>{console.error(e.stack);process.exit(1)});\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "node",
                    str(harness),
                    str(ROOT / "scratch-vm-develop" / "src" / "virtual-machine"),
                    str(path),
                    "700",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_queued_broadcast_reaches_a_waiting_native_helper(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public x = 0;\n"
            "class a {\n"
            "  private void helper() { Control.waitUntil(x > 0); x = 5; }\n"
            "  public void greenFlag() { Control.broadcast(\"go\"); helper(); }\n"
            "  public void onGo() { // hook: broadcast go\n"
            "    x = 1;\n"
            "  }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        for style, budget in (("list", 0), ("h0", 10)):
            with self.subTest(style=style):
                protected = _strip_runtime(
                    self._run(
                        source,
                        {"commentMetadata", "vmProtect"},
                        budget,
                        vm_style=style,
                    )
                )
                self.assertEqual(protected, normal)
        self.assertEqual(normal.get("Stage.x"), 5)

    def test_h0_switch_style_matches_the_ordinary_build(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public x = 0;\n"
            "class a {\n"
            "  public void greenFlag() {\n"
            "    x = 1;\n"
            "    repeat (3) { x += 2; }\n"
            "    if (x == 7) { x = 9; } else { x = -1; }\n"
            "  }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected = _strip_runtime(
            self._run(
                source,
                {"commentMetadata", "vmProtect"},
                10,
                vm_style="h0",
            )
        )
        self.assertEqual(normal["Stage.x"], 9)
        self.assertEqual(protected, normal)

    def test_crypt_checksum_and_decoys_still_execute(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public x = 0;\n"
            "class a { public void greenFlag(){ x = 1; repeat (2) { x += 1; } } }\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        for crypt in (2, 3):
            with self.subTest(crypt=crypt):
                protected = _strip_runtime(
                    self._run(
                        source,
                        {"commentMetadata", "vmProtect"},
                        10,
                        vm_style="h0",
                        vm_crypt=crypt,
                        vm_seed=4,
                    )
                )
                self.assertEqual(protected, normal)

    def test_a_warp_procedure_called_from_the_vm_returns_to_the_interpreter(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public x = 0;\n"
            "public y = 0;\n"
            "class a {\n"
            "  private void fast(n) { // warp\n"
            "    repeat (n) { x += 1; }\n"
            "  }\n"
            "  public void greenFlag() { fast(3); y = x + 1; }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected = _strip_runtime(
            self._run(source, {"commentMetadata", "vmProtect"}, 10)
        )
        self.assertEqual(normal["Stage.x"], 3)
        self.assertEqual(normal["Stage.y"], 4)
        self.assertEqual(protected, normal)

    def test_a_boolean_literal_survives_the_trip_through_the_vm(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public x = 0;\n"
            "class a {\n"
            "  public void greenFlag() {\n"
            "    if (true) { x = 1; }\n"
            "    if (false) { x = 2; }\n"
            "    Control.waitUntil(true);\n"
            "    x += 10;\n"
            "  }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected = _strip_runtime(
            self._run(source, {"commentMetadata", "vmProtect"}, 10)
        )
        self.assertEqual(normal["Stage.x"], 11)
        self.assertEqual(protected, normal)

    def test_h0_style_keeps_custom_block_calls_and_recursion(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public out = 0;\n"
            "class a {\n"
            "  private total = 0;\n"
            "  private void rec(n) {\n"
            "    if (n > 0) { total += n; rec(n - 1); }\n"
            "  }\n"
            "  public void greenFlag() { rec(5); out = total; }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected = _strip_runtime(
            self._run(
                source,
                {"commentMetadata", "vmProtect"},
                10,
                vm_style="h0",
            )
        )
        self.assertEqual(normal["Stage.out"], 15)
        self.assertEqual(protected, normal)

    def test_h0_style_uses_explicit_switch_cases(self):
        source = "public x = 0;\nclass a { public void greenFlag(){ x = 1; } }\n"
        _, project = compile_source(
            source,
            features={"commentMetadata", "vmProtect"},
            vm_style="h0",
        )
        calls = _proccodes(blocks_of(project, "a"))
        self.assertIn("__vm_switch %s", calls)
        self.assertNotIn("__vm_step %s", calls)
        lists = _stage_lists(project)
        self.assertNotIn("__vm_ops", lists)
        self.assertNotIn("__vm_args", lists)

    def test_protected_project_matches_the_ordinary_build(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        normal = self._run(SOURCE, {"commentMetadata"}, 1)
        protected = self._run(SOURCE, {"commentMetadata", "vmProtect"}, 0)
        protected = {
            key: value
            for key, value in protected.items()
            if not key.split(".", 1)[-1].startswith("__vm_")
        }
        self.assertEqual(protected, normal)
        self.assertEqual(normal.get("Stage.g"), 11)


    def test_helloworld_procedures_say_the_same_thing_as_the_plain_build(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (ROOT / "examples" / "helloworld.jsw").read_text(encoding="utf-8")
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected = _strip_runtime(
            self._run(source, {"commentMetadata", "vmProtect"}, 10)
        )
        self.assertEqual(normal["__says"], ["Hello world!"])
        self.assertEqual(protected, normal)

    def test_recursive_procedure_calls_reuse_frames(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public out = 0;\n"
            "class a {\n"
            "  private total = 0;\n"
            "  private void rec(n) {\n"
            "    if (n > 0) { total += n; rec(n - 1); }\n"
            "  }\n"
            "  public void greenFlag() { rec(5); out = total; }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected = _strip_runtime(
            self._run(source, {"commentMetadata", "vmProtect"}, 10)
        )
        self.assertEqual(normal["Stage.out"], 15)
        self.assertEqual(protected, normal)

    def test_deep_recursion_grows_the_frame_arena(self):
        """Nested calls past the initial arena must grow, not overwrite frames.

        The arena starts at 320 frames; a depth-330 recursion used to wrap
        ``__vm_next`` and reuse a base still owned by an outer call.
        """
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public out = 0;\n"
            "class a {\n"
            "  private void rec(n) {\n"
            "    if (n > 0) { rec(n - 1); } else { out = 42; }\n"
            "  }\n"
            "  public void greenFlag() { rec(330); }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected_raw = self._run(
            source,
            {"commentMetadata", "vmProtect"},
            10,
        )
        protected = _strip_runtime(protected_raw)
        self.assertEqual(normal["Stage.out"], 42)
        self.assertEqual(protected, normal)
        self.assertGreater(
            len(protected_raw.get("Stage.__vm_stack", [])),
            10240,
            "recursion past the initial arena did not grow __vm_stack",
        )

    def test_a_wait_inside_a_procedure_keeps_its_frame(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public out = 0;\n"
            "class a {\n"
            "  private void waitThenSet() {\n"
            "    Control.wait(0.01);\n"
            "    out = 4;\n"
            "  }\n"
            "  public void greenFlag() { waitThenSet(); }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected = _strip_runtime(
            self._run(source, {"commentMetadata", "vmProtect"}, 10)
        )
        self.assertEqual(normal["Stage.out"], 4)
        self.assertEqual(protected, normal)

    def test_unqualified_custom_block_calls_survive_namespaced_runtime_emission(self):
        """A target without ``ns=`` keeps plain labels in the VM runtime too.

        ``vmProtect`` emits ``__vm_step`` after the ordinary class pass and
        used to force ``namespacedNames`` on for that generated code.  A call
        such as ``tick()`` then became ``a.tick()`` while the definition was
        still ``tick``, so the loop silently called a missing block.
        """
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public out = 0;\n"
            "class a {\n"
            "  private void tick() { out += 1; }\n"
            "  public void greenFlag() { repeat (5) { tick(); } }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected = _strip_runtime(
            self._run(
                source,
                {"commentMetadata", "namespacedNames", "vmProtect"},
                10,
            )
        )
        self.assertEqual(normal["Stage.out"], 5)
        self.assertEqual(protected, normal)

    def test_a_native_hat_can_call_a_virtualised_procedure(self):
        if not (ROOT / "scratch-vm-develop" / "src" / "virtual-machine.js").exists():
            self.skipTest("scratch-vm-develop/ is not checked out")
        source = (
            "public out = 0;\n"
            "class a {\n"
            "  private void add(n) { out += n; }\n"
            "  public void greenFlag() {\n"
            "    Control.waitUntil(out == 0);\n"
            "    add(7);\n"
            "  }\n"
            "}\n"
        )
        normal = _strip_runtime(self._run(source, {"commentMetadata"}, 1))
        protected = _strip_runtime(
            self._run(source, {"commentMetadata", "vmProtect"}, 10)
        )
        self.assertEqual(normal["Stage.out"], 7)
        self.assertEqual(protected, normal)


if __name__ == "__main__":
    unittest.main()
