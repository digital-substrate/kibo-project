"""What `generate` asks of kibo and writes itself, kibo's calls recorded rather than run: the runs,
their arguments, the embedded definitions, the runtime, `clean`; and what the command line says."""
from __future__ import annotations

import base64
import unittest
import zlib

from support import MINIMAL, Scratch, kibo_project

TWO_TARGETS = MINIMAL.replace('features = ["Base"]', 'features = ["Base", "Wheel"]') + '''
[target.cpp]
features = ["Pool"]
output = "cpp"
'''


class Runs(Scratch):

    def setUp(self) -> None:
        super().setUp()
        self.use(self.pack(), self.jar())

    def test_one_run_per_directory_every_template_in_it(self) -> None:
        code, _, err = self.main("generate", str(self.project(TWO_TARGETS)))
        self.assertEqual(code, 0, err)
        runs = self.kibo_runs()
        self.assertEqual([self.templates_of(r) for r in runs],
                         [["__init__.py.stg", "data.py.stg"], ["pyproject.toml.stg"],
                          ["model.hpp.stg", "pool.hpp.stg"]])
        sources, root, cpp = runs
        project = self.root / "project"
        self.assertEqual(self.option(sources, "-o"), str(project / "generated" / "app"))
        self.assertEqual(self.option(root, "-o"), str(project / "generated"))
        self.assertEqual(self.option(cpp, "-o"), str(project / "cpp"))
        self.assertEqual((self.option(sources, "-c"), self.option(sources, "-n"), self.option(sources, "-d")),
                         ("python", "app", "app.dsm.json"))

    def test_an_older_kibo_runs_once_per_template(self) -> None:
        self.use(self.pack(), self.jar("jars/kibo-1.9.0.jar"))
        manifest_floor = self.root / "pack" / "features.json"
        manifest_floor.write_text(manifest_floor.read_text().replace(">=2.0.0", ">=1.9.0"))
        code, _, err = self.main("generate", str(self.project(MINIMAL)))
        self.assertEqual(code, 0, err)
        self.assertEqual([self.templates_of(r) for r in self.kibo_runs()], [["__init__.py.stg"], ["data.py.stg"]])

    def test_the_project_names_reach_every_run(self) -> None:
        project = self.project(TWO_TARGETS + '''
[names]
atoms = ["IPv4"]

[names.types]
"tuple<A::B, int8>" = "Pair"

[names.cpp.rename]
class = "klass"
''')
        self.main("generate", str(project))
        python, _, cpp = self.kibo_runs()
        for run in (python, cpp):
            self.assertIn("IPv4", run)
            self.assertIn("tuple<A::B, int8>=Pair", run)
        self.assertIn("class=klass", cpp)
        self.assertNotIn("class=klass", python)
        self.assertIn("field:wrap_value", python)

    def test_one_target_only(self) -> None:
        self.main("generate", str(self.project(TWO_TARGETS)), "--target", "cpp")
        self.assertEqual([self.option(r, "-c") for r in self.kibo_runs()], ["cpp"])


class Written(Scratch):

    def setUp(self) -> None:
        super().setUp()
        self.use(self.pack(), self.jar())

    def test_the_embedded_definitions_and_their_first_line(self) -> None:
        self.main("generate", str(self.project(TWO_TARGETS)))
        resources = (self.root / "project" / "generated" / "app" / "resources.py").read_text()
        first = f"# Generated from app.dsm.json by kibo-project {kibo_project.VERSION}. Do not edit by hand."
        self.assertTrue(resources.startswith(first), resources[:120])
        encoded = resources.split("B64_DEFINITIONS = ", 1)[1].strip()
        self.assertEqual(zlib.decompress(base64.b64decode(eval(encoded))), b"definitions")
        hpp = (self.root / "project" / "cpp" / "app_resources.hpp").read_text()
        self.assertIn("inline constexpr unsigned char app_resources_definitions[]", hpp)
        self.assertIn("0x64, 0x65, 0x66", hpp)

    def test_the_runtime_beside_the_sources_exclusions_applied(self) -> None:
        self.main("generate", str(self.project(MINIMAL)))
        runtime = self.root / "project" / "generated" / "app" / "_codegen"
        self.assertTrue((runtime / "proxy.py").is_file())
        self.assertFalse((runtime / "README.md").exists())

    def test_without_the_feature_that_reads_them_neither_definitions_nor_runtime(self) -> None:
        project = self.project(MINIMAL + '\n[target.wheel]\nlanguage = "python"\nfeatures = ["Wheel"]\n'
                               'with_requirements = false\noutput = "wheel"\n')
        code, _, err = self.main("generate", str(project), "--target", "python", "--target", "wheel")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / "project" / "wheel" / "app" / "resources.py").exists())
        self.assertFalse((self.root / "project" / "wheel" / "app" / "_codegen").exists())

    def test_clean_empties_the_sources_first(self) -> None:
        stale = self.write("project/generated/app/old_unit.py", "stale\n")
        kept = self.write("project/generated/README.md", "the project's own\n")
        self.main("generate", str(self.project(MINIMAL + "clean = true\n")))
        self.assertFalse(stale.exists())
        self.assertTrue(kept.exists())

    def test_clean_is_refused_when_the_sources_hold_the_project(self) -> None:
        text = MINIMAL.replace('output = "generated"', 'output = ".."') + "clean = true\n"
        code, _, err = self.main("generate", str(self.project(text, "project/app/kibo.toml")))
        self.assertEqual(code, 1)
        self.assertIn("clean would empty", err)
        self.assertIn("which holds the project", err)

    def test_into_keeps_each_output_in_its_place(self) -> None:
        into = self.root / "elsewhere"
        self.main("generate", str(self.project(MINIMAL)), "--into", str(into))
        self.assertTrue((into / "generated" / "app" / "resources.py").is_file())
        self.assertFalse((self.root / "project" / "generated").exists())

    def test_into_refuses_an_output_outside_the_project(self) -> None:
        text = MINIMAL.replace('output = "generated"', 'output = "../outside"')
        code, _, err = self.main("generate", str(self.project(text)), "--into", str(self.root / "elsewhere"))
        self.assertEqual(code, 1)
        self.assertIn("[target.python] writes outside the project", err)


class CommandLine(Scratch):

    def setUp(self) -> None:
        super().setUp()
        self.use(self.pack(), self.jar())

    def test_a_refusal_goes_to_stderr_with_exit_status_1(self) -> None:
        code, _, err = self.main("generate", str(self.root / "absent.toml"))
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("kibo-project: "))
        self.assertIn("no project file", err)

    def test_an_unknown_target_names_the_known_ones(self) -> None:
        code, _, err = self.main("generate", str(self.project(TWO_TARGETS)), "--target", "ts")
        self.assertEqual(code, 1)
        self.assertIn("no [target.ts] (targets: python, cpp)", err)

    def test_a_failed_kibo_run_says_on_what(self) -> None:
        code, _, err = self.main("generate", str(self.project(MINIMAL)), returncode=1, stderr="kibo says no")
        self.assertEqual(code, 1)
        self.assertIn("kibo failed on 2 templates for app", err)
        self.assertIn("kibo says no", err)

    def test_what_kibo_reports_on_a_successful_run_is_printed(self) -> None:
        code, out, _ = self.main("generate", str(self.project(MINIMAL)),
                                 stderr="kibo: data.py.stg: no such property (2 times)")
        self.assertEqual(code, 0)
        self.assertIn("   kibo: data.py.stg: no such property (2 times)", out)

    def test_validation_switched_off_by_the_project_file(self) -> None:
        code, out, _ = self.main("generate", str(self.project(MINIMAL + "validate = false\n")))
        self.assertEqual(code, 0)
        self.assertIn("validation switched off ([target.python] validate = false)", out)

    def test_validation_switched_off_by_the_command_line_says_so(self) -> None:
        code, out, _ = self.main("generate", str(self.project(MINIMAL)), "--no-validate")
        self.assertEqual(code, 0)
        self.assertIn("--no-validate", out)
        self.assertNotIn("[target.python] validate = false", out)

    def test_plan_lists_the_templates_and_where_they_land(self) -> None:
        code, out, err = self.main("plan", str(self.project(TWO_TARGETS)))
        self.assertEqual(code, 0, err)
        self.assertIn("[python] python -n app ->", out)
        self.assertIn("features: Base, Wheel", out)
        self.assertRegex(out, r"python/pyproject\.toml\.stg\s+root")
        self.assertRegex(out, r"python/data\.py\.stg\s+sources")
        self.assertEqual(self.kibo_runs(), [])

    def test_plan_with_definitions_names_them(self) -> None:
        other = self.write("other/model.dsm", "")
        code, out, _ = self.main("plan", str(self.project(MINIMAL)), "--definitions", str(other))
        self.assertEqual(code, 0)
        self.assertIn(f"definitions:    {other}", out)


if __name__ == "__main__":
    unittest.main()
