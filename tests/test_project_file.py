"""The project file: every option the README documents is read as it says, and every refusal
names the file, the key, and what to write instead."""
from __future__ import annotations

import unittest

from support import MINIMAL, Scratch, kibo_project


class Options(Scratch):

    def test_a_minimal_project(self) -> None:
        project = self.load(MINIMAL)
        self.assertEqual(project.infrastructure, "app")
        self.assertEqual(project.templates_line, 2)
        self.assertIsNone(project.kibo_line)
        self.assertEqual(project.definitions, [self.root / "project" / "definitions"])
        target = project.targets["python"]
        self.assertEqual((target.language, target.features, target.output),
                         ("python", ["Base"], self.root / "project" / "generated"))
        self.assertEqual((target.infrastructure, target.clean, target.with_requirements, target.validate),
                         ("app", False, True, True))
        self.assertIsNone(project.select)

    def test_paths_are_relative_to_the_project_file(self) -> None:
        project = self.load(MINIMAL.replace('"definitions"', '"../model/defs"')
                            .replace('output = "generated"', 'output = "../out/py"'))
        self.assertEqual(project.definitions, [self.root / "model" / "defs"])
        self.assertEqual(project.targets["python"].output, self.root / "out" / "py")

    def test_definitions_as_a_list_keep_their_order(self) -> None:
        project = self.load(MINIMAL.replace('definitions = "definitions"', 'definitions = ["b", "a.dsm"]'))
        self.assertEqual(project.definitions, [self.root / "project" / "b", self.root / "project" / "a.dsm"])

    def test_a_target_named_for_what_it_produces_states_its_language(self) -> None:
        project = self.load(MINIMAL + '\n[target.client]\nlanguage = "cpp"\nfeatures = ["PoolRemote"]\n'
                            'with_requirements = false\noutput = "client"\ninfrastructure = "other"\n')
        client = project.targets["client"]
        self.assertEqual((client.language, client.with_requirements, client.infrastructure),
                         ("cpp", False, "other"))

    def test_the_optional_switches(self) -> None:
        project = self.load(MINIMAL + 'clean = true\nvalidate = false\n')
        self.assertTrue(project.targets["python"].clean)
        self.assertFalse(project.targets["python"].validate)

    def test_the_generator_lines_and_manifests(self) -> None:
        project = self.load(MINIMAL.replace('templates = "2"',
                                            'templates = "2"\nkibo = "2"\nmanifests = ["../templates/features.json"]'))
        self.assertEqual(project.kibo_line, 2)
        self.assertEqual(project.manifests, [self.root / "templates" / "features.json"])

    def test_names(self) -> None:
        project = self.load(MINIMAL + '''
[names]
atoms = ["IPv4", "YCoCg"]

[names.rename]
"f_E" = "f_enum"

[names.types]
"tuple<N::A_and_N_B, N::C>" = "Tuple2_of_N_A_and_N_B_and_N_C_2"

[names.cpp.rename]
class = "klass"

[names.python.rename]
wrap_value = "wrapped"
''')
        self.assertEqual(project.naming, ["--atom", "IPv4", "--atom", "YCoCg", "--rename", "f_E=f_enum",
                                          "--type-name", "tuple<N::A_and_N_B, N::C>=Tuple2_of_N_A_and_N_B_and_N_C_2"])
        self.assertEqual(project.spelling("cpp"), ["--spell", "class=klass"])
        self.assertEqual(project.spelling("python"), ["--spell", "wrap_value=wrapped"])
        self.assertEqual(project.spelling("typescript"), [])

    def test_select_as_a_list(self) -> None:
        project = self.load(MINIMAL + '\n[select]\nattachments = ["A::K.x", "A::K.y", "A::K.x"]\n')
        self.assertEqual(project.select, ["A::K.x", "A::K.y"])

    def test_select_as_a_file_with_comments(self) -> None:
        self.write("project/attachments.txt", "# what the tool reads\nA::K.x   # the first\n\n  A::K.y\n")
        project = self.load(MINIMAL + '\n[select]\nattachments = "attachments.txt"\n')
        self.assertEqual(project.select, ["A::K.x", "A::K.y"])


class Refusals(Scratch):

    def test_no_project_file(self) -> None:
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.load_project(self.root / "absent.toml")
        self.assertIn("no project file", str(raised.exception))

    def test_a_missing_section(self) -> None:
        self.refuses(MINIMAL.replace("[generator]", "[generators]"), "[generator] is missing")

    def test_a_missing_key(self) -> None:
        self.refuses(MINIMAL.replace('infrastructure = "app"\n', ""), "[project].infrastructure is missing")
        self.refuses(MINIMAL.replace('output = "generated"\n', ""), "[target.python].output is missing")

    def test_no_target(self) -> None:
        self.refuses(MINIMAL.split("[target.python]")[0], "no [target.*] section")

    def test_a_target_without_a_language(self) -> None:
        self.refuses(MINIMAL.replace("[target.python]", "[target.client]"),
                     "[target.client] needs a language (cpp, python, typescript)")

    def test_names_must_be_tables_of_strings(self) -> None:
        self.refuses(MINIMAL + '\n[names]\natoms = "IPv4"\n', "[names].atoms must be a list of words")
        self.refuses(MINIMAL + '\n[names.rename]\nx = 1\n', "[names.rename] maps a DSM name")
        self.refuses(MINIMAL + '\n[names.types]\n"tuple<int8>" = ""\n', "[names.types] maps a DSM type")
        self.refuses(MINIMAL + '\n[names.python.rename]\nx = ""\n', "[names.python.rename] maps a DSM name")

    def test_a_language_table_holds_only_rename(self) -> None:
        self.refuses(MINIMAL + '\n[names.python.containers]\nx = "y"\n',
                     "[names.python] holds only a `rename` table")

    def test_select_refusals(self) -> None:
        self.refuses(MINIMAL + '\n[select]\nattachments = []\n', "[select] selects nothing",
                     "remove [select]")
        self.refuses(MINIMAL + '\n[select]\nattachments = ["A::K.x"]\npools = ["P"]\n',
                     "[select] names attachments only, not pools")
        self.refuses(MINIMAL + '\n[select]\nattachments = "absent.txt"\n', "which does not exist")
        self.refuses(MINIMAL + '\n[select]\nattachments = 3\n', "[select] attachments is a list of names")


class Mistakes(Scratch):
    """What a project file gets wrong in a way a reader makes: each is refused, naming the key."""

    def test_features_written_as_a_string(self) -> None:
        self.refuses(MINIMAL.replace('features = ["Base"]', 'features = "Base"'),
                     "[target.python].features")

    def test_a_line_that_is_not_a_number(self) -> None:
        self.refuses(MINIMAL.replace('templates = "2"', 'templates = "2.0"'), "[generator].templates")

    def test_a_misspelt_key_of_a_target(self) -> None:
        self.refuses(MINIMAL + 'clen = true\n', "clen")

    def test_a_misspelt_key_of_names(self) -> None:
        self.refuses(MINIMAL + '\n[names]\nrenames = {}\n', "renames")


if __name__ == "__main__":
    unittest.main()
