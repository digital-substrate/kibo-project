"""The real thing: the kibo jar, the template pack and dsviper, on a small model.

The jar and the pack are found as the tool finds them (KIBO_JAR and KIBO_TEMPLATES, else the
sibling checkouts); without Java, dsviper, mypy, a jar or a pack, these tests are skipped and say
which.
"""
from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from support import kibo_project

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _missing() -> str | None:
    if shutil.which("java") is None:
        return "no java on the PATH"
    try:
        import dsviper  # noqa: F401
    except ImportError:
        return "dsviper is not installed"
    if subprocess.run([sys.executable, "-m", "mypy", "--version"], capture_output=True).returncode != 0:
        return f"mypy is not installed in {sys.executable}, and the pack's validation runs it"
    try:
        pack = kibo_project.find_templates(2)
        kibo_project.find_kibo(kibo_project.Pack(pack, []).kibo_floor, None)
    except kibo_project.ProjectError as error:
        return str(error)
    return None


MISSING = _missing()

PROJECT = """\
[project]
definitions = "definitions"
infrastructure = "app"

[generator]
templates = "2"

[target.python]
features = ["Base", "Attachments"]
output = "python"
clean = true

[target.cpp]
features = ["Base"]
output = "cpp"
"""


@unittest.skipIf(MISSING, f"integration: {MISSING}")
class Integration(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="kibo-project-it-")
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def project(self, text: str = PROJECT, model: str = "app.dsm", extra: str = "") -> Path:
        (self.root / "definitions").mkdir(exist_ok=True)
        shutil.copy(FIXTURES / model, self.root / "definitions" / model)
        path = self.root / "kibo.toml"
        path.write_text(text + textwrap.dedent(extra))
        return path

    def run_tool(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = kibo_project.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def generate(self, path: Path, *options: str) -> str:
        code, out, err = self.run_tool("generate", str(path), *options)
        self.assertEqual(code, 0, out + err)
        return out

    # generate

    def test_generate_writes_every_target_and_validates_the_script_target(self) -> None:
        out = self.generate(self.project())
        self.assertIn("validated: import, build every structure", out)
        self.assertIn("validated: mypy --strict", out)
        package = self.root / "python" / "app"
        for name in ("__init__.py", "resources.py", "app/data.py", "app/attachments.py", "_codegen"):
            self.assertTrue((package / name).exists(), name)
        self.assertTrue(any(p.name.endswith("_resources.hpp") for p in (self.root / "cpp").iterdir()))
        self.assertTrue((self.root / "app.dsm.json").is_file())

    def test_every_file_says_where_it_comes_from_the_same_on_every_machine(self) -> None:
        self.generate(self.project())
        data = (self.root / "python" / "app" / "app" / "data.py").read_text()
        self.assertIn("Generated from app.dsm.json by kibo-", data.splitlines()[0])
        self.assertNotIn(str(self.root), data)
        resources = (self.root / "python" / "app" / "resources.py").read_text()
        self.assertTrue(resources.startswith(f"# Generated from app.dsm.json by kibo-project {kibo_project.VERSION}"))

    def test_definitions_given_on_the_command_line(self) -> None:
        other = self.root / "elsewhere" / "other.dsm"
        other.parent.mkdir()
        shutil.copy(FIXTURES / "other.dsm", other)
        self.generate(self.project(), "--definitions", str(other), "--target", "python")
        self.assertTrue((self.root / "python" / "app" / "other" / "data.py").is_file())
        self.assertFalse((self.root / "python" / "app" / "app").exists())

    def test_definitions_that_do_not_parse(self) -> None:
        path = self.project()
        (self.root / "definitions" / "app.dsm").write_text("namespace App { concept ; };")
        code, _, err = self.run_tool("generate", str(path))
        self.assertEqual(code, 1)
        self.assertIn("the definitions do not parse", err)

    # check

    def test_check_current_then_differs_missing_and_extra(self) -> None:
        path = self.project()
        self.generate(path, "--no-validate")
        code, out, _ = self.run_tool("check", str(path))
        self.assertEqual(code, 0, out)
        self.assertIn("[python] python: current", out)
        data = self.root / "python" / "app" / "app" / "data.py"
        data.write_text(data.read_text() + "# touched\n")
        (self.root / "python" / "app" / "resources.py").unlink()
        (self.root / "python" / "app" / "app" / "removed_type.py").write_text("")
        (self.root / "python" / "app" / "__pycache__").mkdir()
        (self.root / "python" / "app" / "__pycache__" / "x.pyc").write_text("")
        code, out, _ = self.run_tool("check", str(path))
        self.assertEqual(code, 1)
        self.assertIn("differs  app/app/data.py", out)
        self.assertIn("missing  app/resources.py", out)
        self.assertIn("extra    app/app/removed_type.py", out)
        self.assertNotIn("x.pyc", out)

    def test_check_reports_an_extra_file_of_a_cleaned_cpp_target(self) -> None:
        path = self.project(PROJECT.replace('output = "cpp"', 'output = "cpp"\nclean = true'))
        self.generate(path, "--no-validate")
        (self.root / "cpp" / "app_removed_type.hpp").write_text("")
        code, out, _ = self.run_tool("check", str(path))
        self.assertEqual(code, 1, out)
        self.assertIn("extra    app_removed_type.hpp", out)

    def test_check_writes_nothing_in_the_project(self) -> None:
        path = self.project()
        before = {p for p in self.root.rglob("*")}
        code, _, _ = self.run_tool("check", str(path))
        self.assertEqual(code, 1)
        self.assertEqual({p for p in self.root.rglob("*")}, before)

    # select

    def test_a_selection_renders_a_subset_and_says_so(self) -> None:
        path = self.project(extra='\n[select]\nattachments = ["App::User.nickname"]\n')
        out = self.generate(path, "--target", "python")
        self.assertIn("select: a subset of the model: 1 of its 2 attachments", out)
        self.assertTrue((self.root / "app.subset.dsm.json").is_file())
        resources = (self.root / "python" / "app" / "resources.py").read_text()
        self.assertIn("a subset of the model: 1 of its 2 attachments, with the types they depend on", resources)
        attachments = (self.root / "python" / "app" / "app" / "attachments.py").read_text()
        self.assertIn("nickname", attachments)
        self.assertNotIn("profile", attachments)

    def test_plan_prints_the_selection_beside_the_whole_model(self) -> None:
        path = self.project(extra='\n[select]\nattachments = ["App::User.nickname"]\n')
        code, out, err = self.run_tool("plan", str(path))
        self.assertEqual(code, 0, err)
        self.assertIn("select:         1 attachments named", out)
        self.assertRegex(out, r"attachments\s+1 of 2")

    def test_a_selected_name_the_model_lacks_is_refused_with_the_nearest(self) -> None:
        path = self.project(extra='\n[select]\nattachments = ["App::User.nick_name"]\n')
        code, _, err = self.run_tool("generate", str(path))
        self.assertEqual(code, 1)
        self.assertIn("App::User.nick_name -- declared nearby: App::User.nickname", err)
        self.assertFalse((self.root / "python").exists())

    # names

    def test_two_types_written_alike_stop_then_the_directive_settles_it(self) -> None:
        path = self.project(PROJECT.replace('features = ["Base", "Attachments"]', 'features = ["Base"]'),
                            model="collision.dsm")
        code, _, err = self.run_tool("generate", str(path), "--target", "python")
        self.assertEqual(code, 1)
        self.assertIn("2 types are written Tuple2_of_N_A_and_N_B_and_N_C", err)
        self.assertIn('"tuple<N::A_and_N_B, N::C>" = "Tuple2_of_N_A_and_N_B_and_N_C_2"', err)
        path.write_text(path.read_text() + '\n[names.types]\n"tuple<N::A_and_N_B, N::C>" = "Pair_and_C"\n')
        self.generate(path, "--target", "python")
        containers = (self.root / "python" / "app" / "containers.py").read_text()
        self.assertIn("class Pair_and_C(", containers)

    # validation

    def test_a_failed_validation_of_the_projects_own_template_names_its_manifest(self) -> None:
        templates = self.root / "templates"
        (templates / "python").mkdir(parents=True)
        (templates / "features.json").write_text(
            '{"python": {"Broken": {"templates": ["broken.py.stg"], "requires": ["Base"]}}}')
        (templates / "python" / "broken.py.stg").write_text('main(m) ::= <<\nx: int = "not an int"\n>>\n')
        path = self.project(PROJECT.replace('templates = "2"', 'templates = "2"\nmanifests = ["templates/features.json"]')
                            .replace('features = ["Base", "Attachments"]', 'features = ["Base", "Broken"]'))
        code, _, err = self.run_tool("generate", str(path), "--target", "python")
        self.assertEqual(code, 1)
        self.assertIn("broken.py", err)
        self.assertIn(f"correct the project's own templates, declared in {templates / 'features.json'}", err)

    def test_the_wheel_carries_the_package(self) -> None:
        path = self.project(PROJECT.replace('features = ["Base", "Attachments"]', 'features = ["Base", "Wheel"]')
                            .replace('infrastructure = "app"', 'infrastructure = "MetaProject"'))
        self.generate(path, "--target", "python", "--no-validate")
        wheels = self.root / "wheels"
        subprocess.run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation",
                        "-q", "-w", str(wheels)], cwd=self.root / "python", check=True, capture_output=True)
        import zipfile
        names = zipfile.ZipFile(next(wheels.glob("*.whl"))).namelist()
        self.assertIn("MetaProject/__init__.py", names)


if __name__ == "__main__":
    unittest.main()
