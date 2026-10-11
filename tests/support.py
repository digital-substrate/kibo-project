"""What the tests share: a scratch tree, a small pack, a project file, and kibo's calls recorded.

The unit tests never start Java or dsviper: the pack is a few stamped templates, the jar an empty
file with a jar's name, and every call to kibo is recorded instead of run. The integration tests
(test_integration.py) use the real jar, the real pack and dsviper.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kibo_project  # noqa: E402

STAMP = "// kibo-template-viper 2.0.0\n"

PACK_MANIFEST: dict[str, Any] = {
    "generator": {"kibo": ">=2.0.0"},
    "layout": {
        "cpp": {"sources": "",
                "resources": {"with": "Base", "path": "{infrastructure}_resources.hpp",
                              "encoding": "cpp-bytes", "symbol": "{infrastructure}_resources_definitions"}},
        "python": {"sources": "{infrastructure}", "root": ["pyproject.toml.stg"],
                   "runtime": {"with": "Base", "from": "python/runtime", "to": "_codegen",
                               "exclude": ["__pycache__", "*.md"]},
                   "resources": {"with": "Base", "path": "resources.py", "encoding": "python-base64-zlib",
                                 "symbol": "B64_DEFINITIONS"}},
    },
    "cpp": {"Base": {"templates": ["model.hpp.stg"]},
            "Pool": {"templates": ["pool.hpp.stg"], "requires": ["Base"]},
            "PoolRemote": {"templates": ["remote.hpp.stg"], "requires": ["Pool"]}},
    "python": {"Base": {"templates": ["__init__.py.stg", "data.py.stg"]},
               "Pool": {"templates": ["pool.py.stg"], "requires": ["Base"]},
               "Wheel": {"templates": ["pyproject.toml.stg"], "requires": ["Base"]}},
    "reserved": {"python": {"field": ["wrap_value"], "_comment": ["ignored"]}},
    "validation": {"python": [{"name": "check", "with": "Base", "tools": ["python"],
                               "run": ["{python}", "-c", "pass"]}]},
}


class Scratch(unittest.TestCase):
    """A test with its own scratch tree, its own environment, and kibo's calls recorded."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="kibo-project-test-")
        self.root = Path(self._tmp.name).resolve()
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for name in ("KIBO_JAR", "KIBO_TEMPLATES"):
            os.environ.pop(name, None)
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    # Files

    def write(self, relative: str, text: str = "") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    def pack(self, relative: str = "pack", manifest: dict[str, Any] | None = None, stamp: str = STAMP) -> Path:
        """A pack: its manifest, a stamped file for every template it names, and a runtime."""
        manifest = manifest or PACK_MANIFEST
        root = self.root / relative
        self.write(f"{relative}/features.json", json.dumps(manifest))
        for language in ("cpp", "python", "typescript"):
            for feature in manifest.get(language, {}).values():
                for stg in feature.get("templates", []):
                    self.write(f"{relative}/{language}/{stg}", stamp + "main(m) ::= <<x>>\n")
        self.write(f"{relative}/python/runtime/proxy.py", "# runtime\n")
        self.write(f"{relative}/python/runtime/README.md", "excluded\n")
        return root

    def jar(self, relative: str = "jars/kibo-2.0.0.jar") -> Path:
        return self.write(relative, "")

    def project(self, text: str, relative: str = "project/kibo.toml") -> Path:
        path = self.write(relative, text)
        self.write(f"{Path(relative).parent}/definitions/model.dsm", "")
        return path

    def use(self, pack: Path, jar: Path) -> None:
        os.environ["KIBO_TEMPLATES"] = str(pack)
        os.environ["KIBO_JAR"] = str(jar)

    # Running

    def recorder(self, returncode: int = 0, stderr: str = "") -> Any:
        """subprocess.run as kibo-project calls it, recording kibo's command lines."""
        def run(command: list[str], **_: Any) -> SimpleNamespace:
            self.calls.append([str(part) for part in command])
            return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)
        return run

    def main(self, *argv: str, assemble: bool = True, returncode: int = 0, stderr: str = "") -> tuple[int, str, str]:
        """kibo_project.main with kibo recorded, answering with this status and error output, and,
        unless asked, dsviper replaced by a fixed model."""
        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch.object(kibo_project.subprocess, "run", self.recorder(returncode, stderr))]
        if assemble:
            patches.append(mock.patch.object(kibo_project, "assemble", self.fake_assemble))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = kibo_project.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def fake_assemble(project: kibo_project.Project) -> tuple[Path, bytes, None]:
        project.workdir.mkdir(parents=True, exist_ok=True)
        dsm = project.workdir / f"{project.infrastructure}.dsm.json"
        dsm.write_text("{}")
        return dsm, b"definitions", None

    def kibo_runs(self) -> list[list[str]]:
        return [call for call in self.calls if call[:2] == ["java", "-jar"]]

    @staticmethod
    def templates_of(call: list[str]) -> list[str]:
        return [Path(call[i + 1]).name for i, part in enumerate(call) if part == "-t"]

    @staticmethod
    def option(call: list[str], name: str) -> str:
        return call[call.index(name) + 1]

    def load(self, text: str) -> kibo_project.Project:
        return kibo_project.load_project(self.project(text))

    def refuses(self, text: str, *fragments: str) -> str:
        """The project file is refused, and the message says each fragment."""
        with self.assertRaises(kibo_project.ProjectError) as raised:
            self.load(text)
        message = str(raised.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message


MINIMAL = """\
[project]
definitions = "definitions"
infrastructure = "app"

[generator]
templates = "2"

[target.python]
features = ["Base"]
output = "generated"
"""
