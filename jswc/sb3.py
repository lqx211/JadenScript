"""Assemble a :class:`~jswc.ir.Project` into a TurboWarp-compatible ``.sb3``."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from .ir import Project, Target

DEFAULT_COSTUME_NAME = "costume1"
DEFAULT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2" version="1.1">'
    '<rect width="2" height="2" fill="#ffffff" fill-opacity="0"/></svg>'
).encode("utf-8")

META = {
    "semver": "3.0.0",
    "vm": "0.2.0",
    "agent": "",
    "platform": {"name": "TurboWarp", "url": "https://turbowarp.org/"},
}


def _costume(asset_id: str) -> dict:
    return {
        "name": DEFAULT_COSTUME_NAME,
        "bitmapResolution": 1,
        "dataFormat": "svg",
        "assetId": asset_id,
        "md5ext": f"{asset_id}.svg",
        "rotationCenterX": 1,
        "rotationCenterY": 1,
    }


def _blocks_json(target: Target) -> dict:
    return {bid: block.to_json() for bid, block in target.blocks.items()}


def target_json(target: Target, layer_order: int, asset_id: str,
                comments: dict | None = None) -> dict:
    data: dict = {
        "isStage": target.is_stage,
        "name": target.name,
        "variables": target.variables,
        "lists": target.lists,
        "broadcasts": target.broadcasts if target.is_stage else {},
        "blocks": _blocks_json(target),
        "comments": dict(comments or {}),
        "currentCostume": target.current_costume,
        "costumes": list(target.costumes) or [_costume(asset_id)],
        "sounds": list(target.sounds),
        "volume": target.volume,
        "layerOrder": layer_order,
    }
    if target.is_stage:
        data.update(
            {
                "tempo": target.tempo,
                "videoTransparency": target.video_transparency,
                "videoState": target.video_state,
                "textToSpeechLanguage": target.text_to_speech_language,
            }
        )
    else:
        data.update(
            {
                "visible": target.visible,
                "x": target.x,
                "y": target.y,
                "size": target.size,
                "direction": target.direction,
                "draggable": target.draggable,
                "rotationStyle": target.rotation_style,
            }
        )
    return data


def build_project_dict(project: Project) -> tuple[dict, str]:
    asset_id = hashlib.md5(DEFAULT_SVG).hexdigest()
    # layerOrder has to be unique; keep the stored order and fill the gaps.
    taken = {t.layer_order for t in project.targets if t.layer_order}
    free = [n for n in range(len(project.targets) + 1) if n not in taken]
    targets = []
    for index, target in enumerate(project.targets):
        order = target.layer_order if target.layer_order else free.pop(0)
        targets.append(
            target_json(target, order, asset_id, project.comments.get(target.name))
        )
    data: dict = {
        "targets": targets,
        "monitors": list(project.monitors),
        "extensions": list(project.extensions),
        "meta": dict(META),
    }
    if project.extension_urls:
        # TurboWarp needs the URL for every extension it does not ship with,
        # otherwise loading aborts with "Unknown extension: <id>".
        data["extensionURLs"] = dict(project.extension_urls)
    return data, asset_id


def _uses_placeholder(project: Project) -> bool:
    return any(not target.costumes for target in project.targets)


def write_sb3(project: Project, path: str | Path) -> tuple[Path, dict]:
    data, asset_id = build_project_dict(project)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        if _uses_placeholder(project):
            # Only referenced when a target has no costume of its own.
            archive.writestr(f"{asset_id}.svg", DEFAULT_SVG)
        for name, payload in project.assets.items():
            archive.writestr(name, payload)
    return path, data


def write_project_json(project: Project, path: str | Path) -> Path:
    data, _ = build_project_dict(project)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
