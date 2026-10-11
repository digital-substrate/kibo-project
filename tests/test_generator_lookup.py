"""Where the generator comes from, as the README's table says: the environment first, then the
DevKit's generator lines beside tools/, then sibling checkouts; the newest jar the pack accepts."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from support import Scratch, kibo_project


class Jar(Scratch):

    def setUp(self) -> None:
        super().setUp()
        self.tools = self.root / "devkit" / "tools"
        self.tools.mkdir(parents=True)
        self._here = mock.patch.object(kibo_project, "HERE", self.tools)
        self._here.start()

    def tearDown(self) -> None:
        self._here.stop()
        super().tearDown()

    def test_the_environment_wins(self) -> None:
        jar = self.jar("elsewhere/kibo-2.0.3.jar")
        self.jar("devkit/kibo-2/tools/kibo-2.0.9.jar")
        os.environ["KIBO_JAR"] = str(jar)
        self.assertEqual(kibo_project.find_kibo((2, 0, 0), None), jar)

    def test_an_explicit_jar_below_the_floor_is_refused(self) -> None:
        os.environ["KIBO_JAR"] = str(self.jar("elsewhere/kibo-1.2.13.jar"))
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.find_kibo((2, 0, 0), None)
        self.assertIn("no kibo jar >=2.0.0", str(raised.exception))

    def test_the_newest_of_the_devkit_lines_and_the_sibling_checkout(self) -> None:
        self.jar("devkit/tools/kibo-2.0.0.jar")
        self.jar("devkit/kibo-2/tools/kibo-2.0.4.jar")
        newest = self.jar("devkit/kibo/target/kibo-2.1.0.jar")
        self.jar("devkit/kibo/target/original-kibo-2.2.0.jar")
        self.assertEqual(kibo_project.find_kibo((2, 0, 0), None), newest)

    def test_a_pinned_line_excludes_the_others(self) -> None:
        two = self.jar("devkit/kibo-2/tools/kibo-2.0.4.jar")
        self.jar("devkit/kibo-3/tools/kibo-3.0.0.jar")
        self.assertEqual(kibo_project.find_kibo((2, 0, 0), 2), two)

    def test_nothing_found_says_where_it_looked(self) -> None:
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.find_kibo((2, 0, 0), 2)
        message = str(raised.exception)
        self.assertIn("no kibo jar >=2.0.0, line 2", message)
        self.assertIn(f"{self.tools}/kibo-*.jar", message)

    def test_an_explicit_jar_not_named_kibo_version_says_so(self) -> None:
        os.environ["KIBO_JAR"] = str(self.jar("elsewhere/kibo.jar"))
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.find_kibo((2, 0, 0), None)
        self.assertIn("kibo-X.Y.Z.jar", str(raised.exception))


class Templates(Scratch):

    def setUp(self) -> None:
        super().setUp()
        self.tools = self.root / "devkit" / "tools"
        self.tools.mkdir(parents=True)
        self._here = mock.patch.object(kibo_project, "HERE", self.tools)
        self._here.start()

    def tearDown(self) -> None:
        self._here.stop()
        super().tearDown()

    def test_the_environment_wins(self) -> None:
        pack = self.pack("elsewhere/pack")
        self.pack("devkit/kibo-2/templates")
        os.environ["KIBO_TEMPLATES"] = str(pack)
        self.assertEqual(kibo_project.find_templates(2), pack)

    def test_the_devkit_line_before_the_sibling_checkout(self) -> None:
        devkit = self.pack("devkit/kibo-2/templates")
        self.pack("devkit/kibo-template-viper")
        self.assertEqual(kibo_project.find_templates(2), devkit)

    def test_the_sibling_checkout(self) -> None:
        sibling = self.pack("devkit/kibo-template-viper")
        self.assertEqual(kibo_project.find_templates(2), sibling)

    def test_a_pack_of_another_line_is_refused(self) -> None:
        self.pack("devkit/kibo-template-viper", stamp="// kibo-template-viper 3.0.0\n")
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.find_templates(2)
        self.assertIn("template pack 3.0.0, line 2 required", str(raised.exception))

    def test_templates_that_disagree_on_their_version(self) -> None:
        pack = self.pack("devkit/kibo-template-viper")
        (pack / "python" / "pool.py.stg").write_text("// kibo-template-viper 2.0.1\n")
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.find_templates(2)
        self.assertIn("its templates disagree on their version: 2.0.0, 2.0.1", str(raised.exception))

    def test_nothing_found_says_where_it_looked(self) -> None:
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.find_templates(2)
        self.assertIn("no template pack found (tried", str(raised.exception))
        self.assertIn(str(self.root / "devkit" / "kibo-2" / "templates"), str(raised.exception))

    def test_a_pack_without_a_version_stamp_says_so(self) -> None:
        pack = self.pack("elsewhere/pack", stamp="")
        os.environ["KIBO_TEMPLATES"] = str(pack)
        with self.assertRaises(kibo_project.ProjectError) as raised:
            kibo_project.find_templates(2)
        self.assertIn(str(pack), str(raised.exception))
        self.assertNotIn("no template pack found", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
