"""Shared helpers for the jswc test-suite."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from jswc.codegen import Codegen
from jswc.lexer import tokenize
from jswc.parser import Parser
from jswc.sb3 import build_project_dict

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_PROJECT = ROOT / "sample_np4.26_sb3" / "project.json"
SAMPLE_SOURCE = ROOT / "sample.jsw"


def parse_source(source: str) -> tuple[Parser, object]:
    parser = Parser(tokenize(source))
    return parser, parser.parse()


def compile_source(source: str, **kwargs):
    # Tests focus on lowering, so they opt into comment metadata and keep the
    # undefined-variable rule permissive unless a test asks for strictness.
    kwargs.setdefault("features", {"commentMetadata"})
    kwargs.setdefault("allow_undefined", True)
    _, program = parse_source(source)
    codegen = Codegen(program, **kwargs)
    project = codegen.compile()
    return codegen, project


def blocks_of(project, sprite: str) -> dict:
    target = next(t for t in project.targets if t.name == sprite)
    return {bid: block.to_json() for bid, block in target.blocks.items()}


def opcodes(project, sprite: str) -> list[str]:
    return [block["opcode"] for block in blocks_of(project, sprite).values()]


def reference_json() -> dict:
    return json.loads(REFERENCE_PROJECT.read_text(encoding="utf-8"))


CASE = unittest.TestCase()
CASE.maxDiff = None
