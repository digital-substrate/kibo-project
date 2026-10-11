"""The pack and the project's own manifests: the features resolve to templates, dependencies
first, and what a manifest gets wrong is refused naming the manifest."""
from __future__ import annotations

import copy
import json
import unittest

from support import MINIMAL, PACK_MANIFEST, Scratch, kibo_project


class Features(Scratch):

    def setUp(self) -> None:
        super().setUp()
        self.pack_root = self.pack()

    def make(self, extra: list = ()) -> kibo_project.Pack:  # type: ignore[assignment]
        return kibo_project.Pack(self.pack_root, list(extra))

    def test_the_closure_puts_dependencies_first_each_once(self) -> None:
        self.assertEqual(self.make().closure("cpp", ["PoolRemote", "Base"]), ["Base", "Pool", "PoolRemote"])

    def test_an_unknown_feature_names_the_known_ones(self) -> None:
        with self.assertRaises(kibo_project.ProjectError) as raised:
            self.make().closure("python", ["Pools"])
        self.assertIn("no feature 'Pools' for python (known: Base, Pool, Wheel)", str(raised.exception))

    def test_a_cycle_is_refused_with_its_path(self) -> None:
        manifest = copy.deepcopy(PACK_MANIFEST)
        manifest["python"]["Base"]["requires"] = ["Wheel"]
        self.pack_root = self.pack("cyclic", manifest)
        with self.assertRaises(kibo_project.ProjectError) as raised:
            self.make().closure("python", ["Wheel"])
        self.assertIn("a cycle in the features: Wheel -> Base -> Wheel", str(raised.exception))

    def test_templates_in_order_each_once(self) -> None:
        pack = self.make()
        names = [t.name for t in pack.templates("python", ["Base", "Pool", "Wheel"])]
        self.assertEqual(names, ["__init__.py.stg", "data.py.stg", "pool.py.stg", "pyproject.toml.stg"])

    def test_an_absent_template_names_the_feature_and_the_folder(self) -> None:
        (self.pack_root / "python" / "pool.py.stg").unlink()
        with self.assertRaises(kibo_project.ProjectError) as raised:
            self.make().templates("python", ["Base", "Pool"])
        self.assertIn("Pool names pool.py.stg, absent from", str(raised.exception))

    def test_the_kibo_floor(self) -> None:
        self.assertEqual(self.make().kibo_floor, (2, 0, 0))
        manifest = copy.deepcopy(PACK_MANIFEST)
        manifest["generator"]["kibo"] = "2.0.0"
        self.pack_root = self.pack("cofloor", manifest)
        with self.assertRaises(kibo_project.ProjectError) as raised:
            _ = self.make().kibo_floor
        self.assertIn("generator.kibo does not declare the kibo floor", str(raised.exception))

    def test_validation_steps_must_have_a_command(self) -> None:
        manifest = copy.deepcopy(PACK_MANIFEST)
        manifest["validation"]["python"] = [{"name": "nothing to run"}]
        self.pack_root = self.pack("novalidation", manifest)
        with self.assertRaises(kibo_project.ProjectError) as raised:
            self.make().validation("python")
        self.assertIn("validation.python must be a list of steps, each with a `run` command", str(raised.exception))

    def test_reserved_names_become_kibo_options_comments_aside(self) -> None:
        self.assertEqual(self.make().reserved("python"), ["--reserve", "field:wrap_value"])
        self.assertEqual(self.make().reserved("cpp"), [])


class ProjectManifests(Scratch):

    def setUp(self) -> None:
        super().setUp()
        self.pack_root = self.pack()

    def manifest(self, content: dict) -> object:
        path = self.write("project/templates/features.json", json.dumps(content))
        for language, features in content.items():
            if isinstance(features, dict):
                for spec in features.values():
                    for stg in spec.get("templates", []) if isinstance(spec, dict) else []:
                        self.write(f"project/templates/{language}/{stg}", "main(m) ::= <<x>>\n")
        return path

    def test_its_templates_sit_beside_it_in_a_folder_per_target(self) -> None:
        path = self.manifest({"python": {"Report": {"templates": ["report.py.stg"], "requires": ["Base"]}}})
        pack = kibo_project.Pack(self.pack_root, [path])
        templates = pack.templates("python", pack.closure("python", ["Report"]))
        self.assertEqual(templates[-1], self.root / "project" / "templates" / "python" / "report.py.stg")

    def test_a_feature_the_pack_declares_is_refused(self) -> None:
        path = self.manifest({"python": {"Base": {"templates": ["mine.py.stg"]}}})
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.Pack(self.pack_root, [path]).closure("python", ["Base"])
        self.assertIn(f"{path} declares 'Base', which the pack already declares", str(raised.exception))

    def test_its_reserved_names_are_reserved_too(self) -> None:
        path = self.manifest({"reserved": {"python": {"type": ["Report"]}}})
        self.assertEqual(kibo_project.Pack(self.pack_root, [path]).reserved("python"),
                         ["--reserve", "field:wrap_value", "--reserve", "type:Report"])

    def test_malformed_reserved_names_name_the_manifest(self) -> None:
        path = self.manifest({"reserved": {"python": {"type": "Report"}}})
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.Pack(self.pack_root, [path]).reserved("python")
        self.assertIn(f"{path}: reserved.python maps a family of names", str(raised.exception))


class Requirements(Scratch):
    """`with_requirements = false`: only the features named, when another target renders the rest."""

    def setUp(self) -> None:
        super().setUp()
        self.pack_root = self.pack()
        self.pack_ = kibo_project.Pack(self.pack_root, [])

    def project_with(self, client: str, other: str = "") -> kibo_project.Project:
        return self.load(MINIMAL.replace("[target.python]", other + "[target.python]") + client)

    def test_the_rest_rendered_by_another_target(self) -> None:
        project = self.project_with('\n[target.cppbase]\nlanguage = "cpp"\nfeatures = ["Pool"]\noutput = "a"\n'
                                    '\n[target.client]\nlanguage = "cpp"\nfeatures = ["PoolRemote"]\n'
                                    'with_requirements = false\noutput = "b"\n')
        self.assertEqual(self.pack_.rendered(project, project.targets["client"]), ["PoolRemote"])

    def test_refused_when_no_target_renders_the_rest(self) -> None:
        project = self.project_with('\n[target.client]\nlanguage = "cpp"\nfeatures = ["PoolRemote"]\n'
                                    'with_requirements = false\noutput = "b"\n')
        with self.assertRaises(kibo_project.ProjectError) as raised:
            self.pack_.rendered(project, project.targets["client"])
        self.assertIn("[target.client] leaves 'Base' to another target, and no cpp target for app renders it",
                      str(raised.exception))

    def test_refused_when_the_other_target_is_for_another_infrastructure(self) -> None:
        project = self.project_with('\n[target.cppbase]\nlanguage = "cpp"\nfeatures = ["Pool"]\noutput = "a"\n'
                                    'infrastructure = "other"\n'
                                    '\n[target.client]\nlanguage = "cpp"\nfeatures = ["PoolRemote"]\n'
                                    'with_requirements = false\noutput = "b"\n')
        with self.assertRaises(kibo_project.ProjectError):
            self.pack_.rendered(project, project.targets["client"])


if __name__ == "__main__":
    unittest.main()
