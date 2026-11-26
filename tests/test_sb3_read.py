"""Reading an archive that is not quite the shape ``.sb3`` should be.

A project zipped from outside gives ``origin/project.json``, and a file that
merely ends in ``.sb3`` may not be a zip at all. Both used to surface as a bare
``KeyError``/``BadZipFile`` with no file name in it, which reads as a bug in the
decompiler rather than in the input.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from jswc.sb3_read import Sb3Error, load_sb3

PROJECT = {"targets": [{"isStage": True, "name": "Stage", "variables": {}, "lists": {},
                        "blocks": {}, "costumes": [], "sounds": [], "broadcasts": {},
                        "comments": {}, "currentCostume": 0, "volume": 100,
                        "layerOrder": 0, "tempo": 60, "videoTransparency": 50,
                        "videoState": "off", "textToSpeechLanguage": None}],
           "extensions": [], "monitors": []}


def archive(tmp: Path, members: dict[str, bytes]) -> Path:
    path = tmp / "p.sb3"
    with zipfile.ZipFile(path, "w") as out:
        for name, payload in members.items():
            out.writestr(name, payload)
    return path


class FolderWrappedArchiveTests(unittest.TestCase):
    def test_a_project_zipped_from_a_folder_loads(self):
        # `zip -r` on a folder puts everything under it, which is what a
        # shared project often looks like.
        payload = json.dumps(PROJECT).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = archive(Path(tmp), {
                "origin/project.json": payload,
                "origin/backdrop.svg": b"<svg/>",
            })
            loaded = load_sb3(path)
        self.assertEqual(len(loaded.targets), 1)
        # Asset names are relative to the project, not to the archive root, so
        # the decompiler's imports still resolve.
        self.assertEqual(sorted(loaded.assets), ["backdrop.svg"])

    def test_two_projects_in_one_archive_are_reported(self):
        payload = json.dumps(PROJECT).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = archive(Path(tmp), {"a/project.json": payload, "b/project.json": payload})
            with self.assertRaises(Sb3Error) as caught:
                load_sb3(path)
        self.assertIn("2 projects", str(caught.exception))

    def test_an_archive_without_a_project_names_what_it_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = archive(Path(tmp), {"readme.txt": b"hello"})
            with self.assertRaises(Sb3Error) as caught:
                load_sb3(path)
        self.assertIn("readme.txt", str(caught.exception))

    def test_a_file_that_is_not_a_zip_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "renamed.sb3"
            path.write_bytes(b"not a zip")
            with self.assertRaises(Sb3Error) as caught:
                load_sb3(path)
        self.assertIn("renamed.sb3", str(caught.exception))
        self.assertIn("not a .sb3 archive", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
