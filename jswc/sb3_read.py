"""Read an ``.sb3`` (or an extracted project directory) into memory."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .tokens import JswError


@dataclass
class LoadedProject:
    data: dict
    assets: dict[str, bytes] = field(default_factory=dict)
    name: str = "project"

    @property
    def features(self) -> set[str]:
        """Language features the project itself implies.

        `project.json` names its extensions, and the pishi ones only exist in
        pishi's player -- so a project that lists any of them is a pishi
        project and decompiles to a source that asks for `pishiSupport`.
        """
        from .pishi_blocks import PISHI_BLOCKS

        declared = set(self.data.get("extensions") or ())
        return {"pishiSupport"} if declared & set(PISHI_BLOCKS) else set()

    @property
    def targets(self) -> list[dict]:
        return self.data["targets"]

    @property
    def stage(self) -> dict:
        return next(t for t in self.targets if t.get("isStage"))

    @property
    def sprites(self) -> list[dict]:
        return [t for t in self.targets if not t.get("isStage")]


class Sb3Error(JswError):
    """The file is not a project this program can read, and why."""


def _project_json_member(names: list[str]) -> str:
    """The ``project.json`` entry inside an archive, wherever it sits.

    A project zipped from a folder -- ``origin/project.json`` -- is a real thing
    to be handed, and ``ZipFile.read("project.json")`` answered it with a bare
    ``KeyError``. The entry is found by name, and its folder is the root the
    asset paths are relative to, so the archive still loads.
    """
    if "project.json" in names:
        return "project.json"
    nested = [name for name in names if name.endswith("/project.json")]
    if not nested:
        raise Sb3Error(
            "the archive has no project.json — this is not a Scratch project "
            f"(it holds {len(names)} file(s): {', '.join(sorted(names)[:3])}"
            f"{', …' if len(names) > 3 else ''})",
            code="E0089",
        )
    if len(nested) > 1:
        raise Sb3Error(
            f"the archive holds {len(nested)} projects ({', '.join(sorted(nested)[:3])}); "
            "unzip it and pass the one you want",
            code="E0089",
        )
    return nested[0]


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

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise Sb3Error(f"{path.name} is not a .sb3 archive ({error})", code="E0089") from None
    with archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        member = _project_json_member(names)
        root = member[: -len("project.json")]
        data = json.loads(archive.read(member))
        assets = {
            name[len(root):]: archive.read(name)
            for name in names
            if name != member
        }
    return LoadedProject(data=data, assets=assets, name=path.stem)
