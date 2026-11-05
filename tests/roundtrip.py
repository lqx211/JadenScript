"""Canonical form of a project, used to prove decompiler round-trips.

Everything that legitimately changes between a project and its
decompile→recompile result is erased: block ids, coordinates, parents,
generated argument ids and asset hashes. What remains is the script structure
and the initial state, which must be identical.
"""

from __future__ import annotations

import json

from jswc.sb3_read import LoadedProject, load_sb3
from jswc.sb3 import build_project_dict
from jswc import opcodes as O
from jswc.ir import Project


def canonical_target(target: dict) -> dict:
    blocks = {k: v for k, v in target.get("blocks", {}).items() if isinstance(v, dict)}

    def normalise(node: dict) -> dict:
        """Flatten ``join`` chains: string concatenation is associative and
        ``join(x, "")`` is just ``x``."""
        if not isinstance(node, dict):
            return node
        if node.get("opcode") == "operator_join":
            leaves: list = []
            _flatten_join(node, leaves)
            leaves = [leaf for leaf in leaves if leaf != ["lit", "scalar", ""]]
            if not leaves:
                return ["lit", "scalar", ""]
            if len(leaves) == 1:
                return leaves[0]
            return {"opcode": "operator_join", "fields": {}, "mutation": {},
                    "inputs": {"PARTS": leaves}}
        return node

    def _flatten_join(node: dict, leaves: list) -> None:
        inputs = node.get("inputs") or {}
        for name in ("STRING1", "STRING2"):
            child = inputs.get(name)
            if isinstance(child, dict) and child.get("opcode") == "operator_join":
                _flatten_join(child, leaves)
            elif child is not None:
                leaves.append(child)

    def node(block: dict) -> dict:
        fields = {}
        for name, field_value in (block.get("fields") or {}).items():
            if name in ("VARIABLE", "LIST", "BROADCAST_OPTION"):
                fields[name] = field_value[0]
            else:
                fields[name] = field_value[0] if isinstance(field_value, list) else field_value
        mutation = block.get("mutation") or {}
        kept_mutation = {
            key: mutation[key]
            for key in ("proccode", "argumentnames", "warp")
            if key in mutation
        }
        inputs = {}
        if block["opcode"] != "procedures_prototype":
            # A prototype's input keys are generated argument ids; the mutation
            # already describes them by name.
            for name, raw in (block.get("inputs") or {}).items():
                inputs[name] = value(raw)
        if block["opcode"].startswith("lmsTempVars2_") and "VAR" in inputs:
            # Thread variable names get an underscore prefix in JadenScript so
            # the compiler knows they are temps; the storage name is arbitrary.
            slot = inputs["VAR"]
            if isinstance(slot, list) and len(slot) == 3 and slot[1] == "scalar":
                slot = ["lit", "scalar", str(slot[2]).lstrip("_")]
                inputs["VAR"] = slot
        return normalise({
            "opcode": block["opcode"],
            "fields": fields,
            "mutation": kept_mutation,
            "inputs": inputs,
        })

    def value(raw):
        if not isinstance(raw, list) or not raw:
            return None
        if raw[0] == 1:
            payload = raw[1]
            if isinstance(payload, str):
                child = blocks.get(payload)
                return normalise(node(child)) if child else ["dangling", payload]
            return _primitive(payload)
        if isinstance(raw[1], list):
            return _primitive(raw[1])
        child = blocks.get(raw[1])
        return normalise(node(child)) if child else ["dangling", raw[1]]

    def _primitive(payload):
        """Scratch stores a variable in a slot either as a reporter block or as
        the bare primitive ``[12, name, id]``; both read the same value."""
        primitives = {O.VAR: ("data_variable", "VARIABLE"), O.LIST: ("data_listcontents", "LIST")}
        entry = primitives.get(payload[0]) if isinstance(payload, list) and payload else None
        if entry is not None:
            opcode, field_name = entry
            return {"opcode": opcode, "fields": {field_name: payload[1]},
                    "mutation": {}, "inputs": {}}
        return _scalar(payload)

    def chain(block_id):
        out = []
        while block_id:
            block = blocks[block_id]
            out.append(node(block))
            block_id = block.get("next")
        return out

    scripts = []
    for block_id, block in blocks.items():
        if block.get("topLevel"):
            scripts.append(chain(block_id))
    scripts = sorted(scripts, key=lambda script: json.dumps(script, sort_keys=True))

    return {
        "name": target["name"],
        "isStage": bool(target.get("isStage")),
        "variables": sorted((v[0], _plain(v[1])) for v in target.get("variables", {}).values()),
        "lists": sorted((v[0], [_plain(i) for i in v[1]]) for v in target.get("lists", {}).values()),
        "scripts": scripts,
    }


def canonical_project(data: dict) -> dict:
    return {
        "extensions": sorted(data.get("extensions", [])),
        "targets": [canonical_target(target) for target in data["targets"]],
    }


def canonical_from_loaded(loaded: LoadedProject) -> dict:
    return canonical_project(loaded.data)


def canonical_from_project(project: Project) -> dict:
    data, _ = build_project_dict(project)
    return canonical_project(data)


def canonical_from_sb3(path) -> dict:
    return canonical_from_loaded(load_sb3(path))


def report_difference(left: dict, right: dict) -> str:
    """Best-effort human readable diff for the first differing target."""
    left_targets = {t["name"]: t for t in left["targets"]}
    right_targets = {t["name"]: t for t in right["targets"]}
    if left["extensions"] != right["extensions"]:
        return f"extensions differ: {left['extensions']} != {right['extensions']}"
    if set(left_targets) != set(right_targets):
        return f"targets differ: {sorted(left_targets)} != {sorted(right_targets)}"
    for name, left_target in left_targets.items():
        right_target = right_targets[name]
        for key in ("variables", "lists"):
            if left_target[key] != right_target[key]:
                return f"{name}.{key} differ:\n  {left_target[key]}\n  {right_target[key]}"
        if left_target["scripts"] != right_target["scripts"]:
            left_scripts = [json.dumps(s, sort_keys=True) for s in left_target["scripts"]]
            right_scripts = [json.dumps(s, sort_keys=True) for s in right_target["scripts"]]
            for index, (a, b) in enumerate(zip(left_scripts, right_scripts)):
                if a != b:
                    return f"{name}.scripts[{index}] differ:\n  before: {a[:800]}\n  after:  {b[:800]}"
            return f"{name}.scripts count differs: {len(left_scripts)} != {len(right_scripts)}"
    return "no difference"


def _scalar(payload) -> list:
    """Canonical literal: variable/list keep their identity, scalars lose their
    primitive id (Scratch coerces numbers and text interchangeably)."""
    if not isinstance(payload, list) or not payload:
        return ["lit", None, None]
    primitive = payload[0]
    value = payload[1] if len(payload) > 1 else None
    if primitive in (12, 13):
        return ["lit", primitive, value]
    return ["lit", "scalar", "" if value is None else str(value)]


def _plain(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return value
