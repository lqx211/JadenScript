"""Structural validation for generated projects.

This is intentionally *not* a full sb3 schema validator; it checks the parts a
code generator can get wrong: dangling parents/next links, phantom input
blocks, unknown variable/list ids, and blocks missing their top-level position.
"""

from __future__ import annotations

from .ir import Project


def validate(project: Project, known_opcodes: set[str] | None = None) -> list[str]:
    issues: list[str] = []
    stage = project.stage
    stage_vars = set(stage.variables)
    stage_lists = set(stage.lists)
    broadcast_ids = set(stage.broadcasts)

    for target in project.targets:
        blocks = target.blocks
        local_vars = set(target.variables)
        local_lists = set(target.lists)
        for block in blocks.values():
            where = f"{target.name}:{block.opcode}({block.id})"
            if block.next is not None and block.next not in blocks:
                issues.append(f"E0062 {where}: next points at missing block {block.next}")
            if block.parent is not None and block.parent not in blocks:
                issues.append(f"E0062 {where}: parent points at missing block {block.parent}")
            if block.top_level and block.parent is not None:
                issues.append(f"E0063 {where}: top-level block has a parent")
            if not block.top_level and block.parent is None:
                issues.append(f"E0063 {where}: non-top-level block has no parent")
            for name, raw in block.inputs.items():
                if not isinstance(raw, list) or not raw:
                    issues.append(f"E0064 {where}: malformed input {name}")
                    continue
                if raw[0] in (2, 3) and len(raw) > 1 and raw[1] not in blocks:
                    issues.append(f"E0062 {where}: input {name} references missing block {raw[1]}")
                elif raw[0] == 1 and len(raw) > 1 and isinstance(raw[1], str):
                    if raw[1] not in blocks:
                        issues.append(f"E0062 {where}: input {name} references missing shadow {raw[1]}")
            var_field = block.fields.get("VARIABLE")
            if var_field:
                vid = var_field[1] if len(var_field) > 1 else None
                if vid not in stage_vars and vid not in local_vars:
                    issues.append(f"E0065 {where}: unknown variable id {vid!r}")
            list_field = block.fields.get("LIST")
            if list_field:
                lid = list_field[1] if len(list_field) > 1 else None
                if lid not in stage_lists and lid not in local_lists:
                    issues.append(f"E0066 {where}: unknown list id {lid!r}")
            broadcast_field = block.fields.get("BROADCAST_OPTION")
            if broadcast_field:
                bid = broadcast_field[1] if len(broadcast_field) > 1 else None
                if bid not in broadcast_ids:
                    issues.append(f"E0067 {where}: unknown broadcast id {bid!r}")
            if known_opcodes is not None:
                if block.opcode not in known_opcodes and not _is_extension(block.opcode):
                    issues.append(f"E0068 {where}: opcode not found in scratch-vm catalog")
    return issues


def _is_extension(opcode: str) -> bool:
    """Extension opcodes are ``<extensionid>_<blockid>`` and not catalogued."""
    return "_" in opcode and not opcode.startswith(
        ("motion_", "looks_", "sound_", "event_", "control_", "sensing_", "operator_", "data_", "procedures_")
    )
