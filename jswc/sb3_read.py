"""Read an ``.sb3`` (or an extracted project directory) into memory."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LoadedProject:
    data: dict
    assets: dict[str, bytes] = field(default_factory=dict)
    name: str = "project"

    @property
    def targets(self) -> list[dict]:
        return self.data["targets"]

    @property
    def stage(self) -> dict:
        return next(t for t in self.targets if t.get("isStage"))

    @property
    def sprites(self) -> list[dict]:
        return [t for t in self.targets if not t.get("isStage")]


def load_sb3(path: str | Path) -> LoadedProject:
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return LoadedProject(data=data, name=path.stem)
    if path.is_dir():
        data = json.loads((path / "project.json").read_text(encoding="utf-8"))
        assets = {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()
                  and item.name != "project.json"}
        return LoadedProject(data=data, assets=assets, name=path.name)

    with zipfile.ZipFile(path) as archive:
        data = json.loads(archive.read("project.json"))
        assets = {
            name: archive.read(name)
            for name in archive.namelist()
            if name != "project.json" and not name.endswith("/")
        }
    return LoadedProject(data=data, assets=assets, name=path.stem)
