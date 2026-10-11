"""Who a failed validation names: the owner of the template that wrote the file it reports.

A project's own manifest answers for the templates it declares, not the pack; when the lines do
not tell, every owner whose templates were rendered is named.

    python3 -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kibo_project  # noqa: E402


class ValidationOwner(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pack_root = root / "pack"
        self.pack_root.mkdir()
        (self.pack_root / "features.json").write_text(json.dumps({
            "python": {"Base": {"templates": ["data.py.stg", "containers.py.stg"]},
                       "Common": {"templates": ["util.py.stg"]}}}))
        self.manifest = root / "project" / "templates" / "features.json"
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text(json.dumps({
            "python": {"Report": {"templates": ["report.py.stg"], "requires": ["Base"]},
                       "Shadow": {"templates": ["util.py.stg"]}}}))
        self.pack = kibo_project.Pack(self.pack_root, [self.manifest])
        self.target = SimpleNamespace(language="python")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def owner(self, lines: list[str], rendered: list[str]) -> str:
        return kibo_project.owner_of(lines, self.pack, self.target, rendered)

    def test_a_file_of_the_project_names_its_manifest(self) -> None:
        message = self.owner(['features/report.py:8: error: Name "_Report" already defined (possibly by an import)'],
                             ["Base", "Report"])
        self.assertIn(str(self.manifest), message)
        self.assertNotIn(str(self.pack_root), message)

    def test_a_file_of_the_pack_names_the_pack(self) -> None:
        message = self.owner(["features/demo/data.py:12: error: Incompatible return value type"],
                             ["Base", "Report"])
        self.assertIn(f"report it to their pack, {self.pack_root}", message)
        self.assertNotIn(str(self.manifest), message)

    def test_a_file_no_single_owner_wrote_names_both(self) -> None:
        message = self.owner(["features/util.py:3: error: Name \"x\" is not defined"],
                             ["Base", "Common", "Shadow"])
        self.assertIn(str(self.pack_root), message)
        self.assertIn(str(self.manifest), message)

    def test_lines_naming_no_file_name_both_when_the_project_rendered_its_own(self) -> None:
        message = self.owner(["Found 1 error in 1 file (checked 9 source files)"], ["Base", "Report"])
        self.assertIn(str(self.pack_root), message)
        self.assertIn(str(self.manifest), message)

    def test_lines_naming_no_file_name_the_pack_when_it_rendered_alone(self) -> None:
        message = self.owner(["Found 1 error in 1 file (checked 9 source files)"], ["Base"])
        self.assertIn(f"report it to their pack, {self.pack_root}", message)

    def test_a_windows_path_is_attributed_too(self) -> None:
        message = self.owner(["features\\report.py:8: error: Name \"_Report\" already defined"], ["Base", "Report"])
        self.assertIn(str(self.manifest), message)


if __name__ == "__main__":
    unittest.main()
