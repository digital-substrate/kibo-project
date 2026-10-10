#!/usr/bin/env python3
"""Generate a project's code from its project file.

A project states its generation once, in a TOML file beside its DSM: the definitions, the
infrastructure name, the features per target, and where each target's output goes. This tool
reads it and drives the two components it does not replace: dsviper, which assembles the DSM
into definitions, and the kibo jar, which renders the template pack. Where the pack's output
lands, and how the definitions are embedded, the pack declares in its features.json.

    kibo_project.py generate [kibo.toml] [--target NAME ...] [--definitions PATH] [--into DIR]
    kibo_project.py plan     [kibo.toml] [--definitions PATH]
    kibo_project.py check    [kibo.toml] [--target NAME ...] [--definitions PATH]

A project that reads part of a large model names that part in [select]: the attachments it
reads, and every target is generated from those attachments and what they depend on.
"""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:                                  # Python 3.10
    import tomli as tomllib                                  # type: ignore[no-redef]

VERSION = "0.4.0"
TARGETS = ("cpp", "python", "typescript")
HERE = Path(__file__).resolve().parent
Table = dict[str, Any]


class ProjectError(Exception):
    """A project that cannot be generated, with what to change."""


# MARK: - Project file

@dataclass
class Target:
    name: str
    language: str
    features: list[str]
    output: Path
    infrastructure: str
    clean: bool = False
    with_requirements: bool = True
    validate: bool = True


@dataclass
class Project:
    path: Path
    definitions: list[Path]
    infrastructure: str
    templates_line: int
    kibo_line: int | None = None
    manifests: list[Path] = field(default_factory=list)
    targets: dict[str, Target] = field(default_factory=dict)
    into: Path | None = None
    # How a static name is spelled where kibo projects it to snake_case: the words never split,
    # and the names spelled as the author wants (`[names]` in the project file).
    atoms: list[str] = field(default_factory=list)
    renames: dict[str, str] = field(default_factory=dict)
    # How a target spells a DSM name it cannot take (`[names.<language>.rename]`): every identifier
    # of that target follows it, the runtime still knows the DSM name.
    spellings: dict[str, dict[str, str]] = field(default_factory=dict)
    # The attachments the project reads (`[select]`), as `Namespace::KeyType.identifier`: every
    # target is generated from them and what they depend on. None generates the whole model.
    select: list[str] | None = None

    def spelling(self, language: str) -> list[str]:
        """How the target of a language spells names, as kibo's arguments."""
        return [option for name, spelled in self.spellings.get(language, {}).items()
                for option in ("--spell", f"{name}={spelled}")]

    @property
    def naming(self) -> list[str]:
        """The naming of the project, as kibo's arguments."""
        arguments: list[str] = []
        for atom in self.atoms:
            arguments += ["--atom", atom]
        for name, spelled in self.renames.items():
            arguments += ["--rename", f"{name}={spelled}"]
        return arguments

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def workdir(self) -> Path:
        """Where the outputs and the .dsm.json go: the project, or the directory it is rendered into."""
        return self.into or self.root


def relocate(project: Project, into: Path) -> None:
    """Render into another directory, the outputs keeping their place relative to the project:
    two renderings can then be compared without touching the working tree."""
    into = into.resolve()
    for target in project.targets.values():
        if not target.output.is_relative_to(project.root):
            raise ProjectError(f"[target.{target.name}] writes outside the project ({target.output}), "
                               f"so it cannot be rendered into {into}")
        target.output = into / target.output.relative_to(project.root)
    project.into = into


def load_project(path: Path) -> Project:
    path = path.resolve()
    if not path.is_file():
        raise ProjectError(f"{path}: no project file")
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    root = path.parent

    def section(name: str) -> Table:
        value = data.get(name)
        if not isinstance(value, dict):
            raise ProjectError(f"{path}: [{name}] is missing")
        return value

    def required(table: Table, key: str, where: str) -> Any:
        if key not in table:
            raise ProjectError(f"{path}: {where}.{key} is missing")
        return table[key]

    project_table, generator = section("project"), section("generator")
    infrastructure = str(required(project_table, "infrastructure", "[project]"))
    project = Project(
        path=path,
        definitions=[(root / str(d)).resolve() for d in _paths(required(project_table, "definitions", "[project]"))],
        infrastructure=infrastructure,
        templates_line=int(str(required(generator, "templates", "[generator]"))),
        kibo_line=int(str(generator["kibo"])) if "kibo" in generator else None,
        manifests=[(root / str(m)).resolve() for m in generator.get("manifests", [])],
    )
    for name, table in data.get("target", {}).items():
        # A target is named for what it produces; one named after a language needs no more.
        language = str(table.get("language", name))
        if language not in TARGETS:
            raise ProjectError(f"{path}: [target.{name}] needs a language ({', '.join(TARGETS)})")
        project.targets[name] = Target(
            name=name,
            language=language,
            features=list(required(table, "features", f"[target.{name}]")),
            output=(root / str(required(table, "output", f"[target.{name}]"))).resolve(),
            infrastructure=str(table.get("infrastructure", infrastructure)),
            clean=bool(table.get("clean", False)),
            with_requirements=bool(table.get("with_requirements", True)),
            validate=bool(table.get("validate", True)),
        )
    if not project.targets:
        raise ProjectError(f"{path}: no [target.*] section")
    names = data.get("names", {})
    if not isinstance(names, dict):
        raise ProjectError(f"{path}: [names] must be a table")
    atoms, renames = names.get("atoms", []), names.get("rename", {})
    if not isinstance(atoms, list) or not all(isinstance(a, str) and a for a in atoms):
        raise ProjectError(f"{path}: [names].atoms must be a list of words")
    if not isinstance(renames, dict) or not all(isinstance(v, str) and v for v in renames.values()):
        raise ProjectError(f"{path}: [names.rename] maps a DSM name to the snake_case it takes")
    project.atoms, project.renames = list(atoms), dict(renames)
    for language in TARGETS:
        table = names.get(language, {})
        if not isinstance(table, dict) or set(table) - {"rename"}:
            raise ProjectError(f"{path}: [names.{language}] holds only a `rename` table")
        spelled = table.get("rename", {})
        if not isinstance(spelled, dict) or not all(isinstance(v, str) and v for v in spelled.values()):
            raise ProjectError(f"{path}: [names.{language}.rename] maps a DSM name to how {language} spells it")
        if spelled:
            project.spellings[language] = dict(spelled)
    project.select = load_select(path, data.get("select"))
    return project


def load_select(path: Path, table: object) -> list[str] | None:
    """`[select] attachments`: a list of names, or a file of them (one per line, `#` comments),
    relative to the project file."""
    if table is None:
        return None
    if not isinstance(table, dict):
        raise ProjectError(f"{path}: [select] must be a table")
    others = sorted(set(table) - {"attachments"})
    if others:
        raise ProjectError(f"{path}: [select] names attachments only, not {', '.join(others)}: a selection "
                           "carries the attachments a project reads and the types they depend on; function "
                           "pools and types read outside any attachment are not selectable")
    listed = table.get("attachments")
    if isinstance(listed, str):
        source = (path.parent / listed).resolve()
        if not source.is_file():
            raise ProjectError(f"{path}: [select] attachments names {source}, which does not exist")
        names = [line.split("#", 1)[0].strip() for line in source.read_text(encoding="utf-8").splitlines()]
        names = [name for name in names if name]
    elif isinstance(listed, list) and all(isinstance(name, str) and name for name in listed):
        names = list(listed)
    else:
        raise ProjectError(f"{path}: [select] attachments is a list of names "
                           "(\"Namespace::KeyType.identifier\"), or the file that lists them")
    if not names:
        raise ProjectError(f"{path}: [select] selects nothing; to generate the whole model, remove [select]")
    return list(dict.fromkeys(names))


# MARK: - The generator: kibo and the template pack

Version = tuple[int, int, int]


def _version(text: str) -> Version | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _dotted(version: Version) -> str:
    return ".".join(map(str, version))


def find_kibo(floor: Version, line: int | None) -> Path:
    """The newest jar at or above the pack's floor, and of the project's line if it pins one:
    KIBO_JAR, else those beside this tool, those of the DevKit's generator lines
    (../kibo-<line>/tools/), and those of a sibling kibo checkout."""
    if os.environ.get("KIBO_JAR"):
        candidates = [Path(os.environ["KIBO_JAR"])]
    else:
        candidates = [*HERE.glob("kibo-*.jar"), *HERE.parent.glob("kibo-*/tools/kibo-*.jar"),
                      *(HERE.parent / "kibo" / "target").glob("kibo-*.jar")]
    eligible: list[tuple[Version, Path]] = []
    for jar in candidates:
        version = _version(jar.name) if re.fullmatch(r"kibo-\d+\.\d+\.\d+\.jar", jar.name) else None
        if version and version >= floor and (line is None or version[0] == line) and jar.is_file():
            eligible.append((version, jar.resolve()))
    if not eligible:
        wanted = f">={_dotted(floor)}" + (f", line {line}" if line is not None else "")
        tried = (os.environ.get("KIBO_JAR")
                 or f"{HERE}/kibo-*.jar, {HERE.parent}/kibo-*/tools/kibo-*.jar, {HERE.parent}/kibo/target/kibo-*.jar")
        raise ProjectError(f"no kibo jar {wanted} (tried {tried})")
    return max(eligible)[1]


def find_templates(line: int) -> Path:
    """The pack of the required line: KIBO_TEMPLATES, else the DevKit's pack of that line
    (../kibo-<line>/templates/), else templates/ beside this tool's folder, else a sibling
    kibo-template-viper checkout."""
    if os.environ.get("KIBO_TEMPLATES"):
        candidates = [Path(os.environ["KIBO_TEMPLATES"])]
    else:
        candidates = [HERE.parent / f"kibo-{line}" / "templates", HERE.parent / "templates",
                      HERE.parent / "kibo-template-viper"]
    for pack in candidates:
        if not (pack / "features.json").is_file():
            continue
        stamps = {s for stg in pack.glob("*/*.stg")
                  for s in re.findall(r"kibo-template-viper (\d+\.\d+\.\d+)", stg.read_text())}
        if len(stamps) > 1:
            raise ProjectError(f"{pack}: its templates disagree on their version: {', '.join(sorted(stamps))}")
        version = _version(next(iter(stamps))) if stamps else None
        if version and version[0] == line:
            return pack.resolve()
        if version:
            raise ProjectError(f"{pack}: template pack {'.'.join(map(str, version))}, line {line} required")
    raise ProjectError(f"no template pack found (tried {', '.join(map(str, candidates))})")


# MARK: - Features

class Pack:
    """The pack's manifest, with the project's own manifests added."""

    def __init__(self, root: Path, extra: list[Path]):
        self.root = root
        self.manifest = json.loads((root / "features.json").read_text())
        self.extra = [(m, json.loads(m.read_text())) for m in extra]

    @property
    def kibo_floor(self) -> Version:
        declared = str(self.manifest.get("generator", {}).get("kibo", ""))
        floor = _version(declared) if declared.startswith(">=") else None
        if floor is None:
            raise ProjectError(f"{self.root}/features.json: generator.kibo does not declare the kibo floor "
                               f"(\">=X.Y.Z\"), found {declared!r}")
        return floor

    def layout(self, target: str) -> Table:
        layout: Table = self.manifest.get("layout", {}).get(target, {})
        return layout

    def validation(self, target: str) -> list[Table]:
        """How the pack's output for a target is checked once generated: commands, the tools they
        need, and the feature that must be rendered for them to apply."""
        steps = self.manifest.get("validation", {}).get(target, [])
        if not isinstance(steps, list) or not all(isinstance(s, dict) and isinstance(s.get("run"), list)
                                                 for s in steps):
            raise ProjectError(f"{self.root / 'features.json'}: validation.{target} must be a list of "
                               "steps, each with a `run` command")
        return steps

    def reserved(self, target: str) -> list[str]:
        """The names the pack's own code takes in a target, per family of names, as kibo's
        arguments: a DSM name meeting one stops the generation, saying how to spell it otherwise.
        A project's own manifest reserves the names its own templates take the same way."""
        options: list[str] = []
        for path, manifest in [(self.root / "features.json", self.manifest), *self.extra]:
            families = manifest.get("reserved", {}).get(target, {})
            if not isinstance(families, dict) or not all(
                    isinstance(names, list) and all(isinstance(n, str) for n in names)
                    for kind, names in families.items() if not kind.startswith("_")):
                raise ProjectError(f"{path}: reserved.{target} maps a family of names "
                                   "(field, namespace...) to the names the templates' code takes")
            options += [option for kind, names in families.items() if not kind.startswith("_")
                        for name in names for option in ("--reserve", f"{kind}:{name}")]
        return options

    def _features(self, target: str) -> dict[str, tuple[Table, Path]]:
        features = {name: (spec, self.root / target)
                    for name, spec in self.manifest.get(target, {}).items()}
        for path, manifest in self.extra:
            for name, spec in manifest.get(target, {}).items():
                if name in features:
                    raise ProjectError(f"{path} declares {name!r}, which the pack already declares")
                features[name] = (spec, path.parent / target)
        return features

    def closure(self, language: str, wanted: list[str]) -> list[str]:
        """The features `wanted` needs, dependencies first, each once."""
        features = self._features(language)
        ordered: list[str] = []

        def visit(name: str, path: tuple[str, ...]) -> None:
            if name in path:
                raise ProjectError(f"a cycle in the features: {' -> '.join(path + (name,))}")
            if name in ordered:
                return
            if name not in features:
                raise ProjectError(f"no feature {name!r} for {language} (known: {', '.join(sorted(features))})")
            for need in features[name][0].get("requires", []):
                visit(need, path + (name,))
            ordered.append(name)

        for name in wanted:
            visit(name, ())
        return ordered

    def templates(self, language: str, rendered: list[str]) -> list[Path]:
        """The .stg files of the features rendered, in order, each once."""
        features = self._features(language)
        out: list[Path] = []
        for name in rendered:
            spec, folder = features[name]
            for stg in spec["templates"]:
                template = folder / stg
                if not template.is_file():
                    raise ProjectError(f"{name} names {stg}, absent from {folder}")
                if template not in out:
                    out.append(template)
        return out

    def rendered(self, project: Project, target: Target) -> list[str]:
        """The features a target renders: the closure of what it asks for, or, with
        `with_requirements = false`, only what it asks for -- provided another target of the
        project renders the rest for the same infrastructure, so nothing is left out unsaid."""
        closure = self.closure(target.language, target.features)
        if target.with_requirements:
            return closure
        own = [name for name in closure if name in target.features]
        for need in (name for name in closure if name not in target.features):
            if not any(other is not target and other.language == target.language
                       and other.infrastructure == target.infrastructure and other.with_requirements
                       and need in self.closure(other.language, other.features)
                       for other in project.targets.values()):
                raise ProjectError(f"[target.{target.name}] leaves {need!r} to another target, and no "
                                   f"{target.language} target for {target.infrastructure} renders it")
        return own


# MARK: - Embedded definitions

# Each encoding opens with the line kibo puts first in what it renders: which DSM, by which
# generator. The definitions are generated as much as the code that decodes them.

def _cpp_bytes(encoded: bytes, layout: Table, infrastructure: str, provenance: str) -> str:
    guard = Path(layout["path"].format(infrastructure=infrastructure)).stem
    symbol = layout["symbol"].format(infrastructure=infrastructure)
    lines = [", ".join(f"0x{b:02x}" for b in encoded[i:i + 12]) for i in range(0, len(encoded), 12)]
    return (f"// {provenance}\n\n#ifndef {guard}_hpp\n#define {guard}_hpp\n\n#include <cstddef>\n\n"
            f"inline constexpr unsigned char {symbol}[] = {{\n " + ",\n ".join(lines) + "\n};\n\n#endif\n")


def _python_base64_zlib(encoded: bytes, layout: Table, infrastructure: str, provenance: str) -> str:
    return f"# {provenance}\n\n{layout['symbol']} = {base64.b64encode(zlib.compress(encoded))!r}\n"


def _typescript_base64(encoded: bytes, layout: Table, infrastructure: str, provenance: str) -> str:
    return f'// {provenance}\n\nexport const {layout["symbol"]} = "{base64.b64encode(encoded).decode("ascii")}";\n'


ENCODINGS = {
    "cpp-bytes": _cpp_bytes,
    "python-base64-zlib": _python_base64_zlib,
    "typescript-base64": _typescript_base64,
}


# MARK: - Generation

def _paths(value: object) -> list[object]:
    """`definitions` is one file or folder, or a list of them assembled in the order written."""
    return list(value) if isinstance(value, list) else [value]


def parse(project: Project) -> tuple[Any, Any]:
    """The project's DSM and definitions, as dsviper assembles them."""
    from dsviper import DSMBuilder
    for path in project.definitions:
        if not path.exists():
            raise ProjectError(f"{path}: no definitions")
    if len(project.definitions) == 1:
        builder = DSMBuilder.assemble(str(project.definitions[0]))
    else:
        # Several folders or files, in the order the project writes them; a folder's .dsm files
        # sorted by path, as DSMBuilder.assemble reads one.
        builder = DSMBuilder()
        for path in project.definitions:
            for file in (sorted(path.glob("*.dsm")) if path.is_dir() else [path]):
                builder.append(str(file), file.read_text(encoding="utf-8"))
    report, dsm, definitions = builder.parse()
    if report.has_error():
        raise ProjectError("the definitions do not parse:\n" + "\n".join(f"  {e!r}" for e in report.errors()))
    if dsm is None or definitions is None:
        raise ProjectError("the definitions parsed to nothing")
    return dsm, definitions


@dataclass
class Selection:
    """What [select] keeps of a model: its DSM and definitions, and both models' counts."""
    dsm: Any
    definitions: Any
    kept: dict[str, int]
    whole: dict[str, int]


def counts(dsm: Any) -> dict[str, int]:
    return {"attachments": len(dsm.attachments()), "concepts": len(dsm.concepts()),
            "structures": len(dsm.structures()), "enumerations": len(dsm.enumerations())}


def select(project: Project, dsm: Any) -> Selection:
    """The attachments the project names, and what they depend on: dsviper writes them as DSM
    source, which is parsed again, so the model every target reads is held to the same parse as
    any other. A name the model does not declare stops everything, before anything is written."""
    from dsviper import DSMBuilder
    assert project.select is not None
    declared = {attachment.identifier(): attachment for attachment in dsm.attachments()}
    unknown = [name for name in project.select if name not in declared]
    if unknown:
        lines = []
        for name in unknown:
            identifier = name.rsplit(".", 1)[-1]
            near = [d for d in declared if d.rsplit(".", 1)[-1] == identifier]
            near += [d for d in difflib.get_close_matches(name, declared, n=3, cutoff=0.6) if d not in near]
            lines.append(f"  {name}" + (f" -- declared nearby: {', '.join(near[:4])}" if near else ""))
        raise ProjectError(f"{project.path}: [select] names {len(unknown)} attachment(s) the model does not "
                           f"declare (a name is Namespace::KeyType.identifier):\n" + "\n".join(lines))
    source = dsm.to_dsm(attachments=[declared[name] for name in project.select])
    builder = DSMBuilder()
    builder.append(f"{project.infrastructure}.subset.dsm", source)
    report, kept, definitions = builder.parse()
    if report.has_error() or kept is None or definitions is None:
        raise ProjectError("the selection, as dsviper writes it, does not parse:\n"
                           + "\n".join(f"  {e!r}" for e in report.errors()))
    return Selection(kept, definitions, counts(kept), counts(dsm))


def assemble(project: Project) -> tuple[Path, bytes, Selection | None]:
    """The DSM as the .dsm.json kibo reads, written beside the project file, and the encoded
    definitions to embed. The file is an intermediate a project does not commit; it is kept so
    that what the banners name exists, and so kibo can be rerun by hand on it. A selection is
    written as `<infrastructure>.subset.dsm.json`, so every banner says it renders a subset."""
    dsm, definitions = parse(project)
    selection = select(project, dsm) if project.select is not None else None
    if selection:
        dsm, definitions = selection.dsm, selection.definitions
    project.workdir.mkdir(parents=True, exist_ok=True)
    path = project.workdir / f"{project.infrastructure}{'.subset' if selection else ''}.dsm.json"
    path.write_text(dsm.json_encode())
    return path, bytes(definitions.encode().encoded()), selection


def subset_line(selection: Selection) -> str:
    return (f"a subset of the model: {selection.kept['attachments']} of its "
            f"{selection.whole['attachments']} attachments, with the types they depend on")


# The first kibo that renders several templates in one run (`-t` repeated). Before it, each
# template is a run of its own -- a JVM started for each, which is most of a generation's time.
KIBO_TEMPLATE_LIST: Version = (2, 0, 0)


def render(jar: Path, target: Target, dsm: Path, templates: list[Path], output: Path,
           naming: list[str]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    version = _version(jar.name)
    runs = [templates] if version and version >= KIBO_TEMPLATE_LIST else [[t] for t in templates]
    for run in filter(None, runs):
        # Run beside the .dsm.json, so that the banner kibo writes names it relative to the
        # project, the same on every machine.
        listed = [option for template in run for option in ("-t", str(template))]
        result = subprocess.run(["java", "-jar", str(jar), "-c", target.language, "-n", target.infrastructure,
                                 "-d", dsm.name, *listed, "-o", str(output), *naming],
                                cwd=dsm.parent, capture_output=True, text=True)
        if result.returncode != 0:
            what = run[0].name if len(run) == 1 else f"{len(run)} templates for {output.name}"
            raise ProjectError(f"kibo failed on {what}:\n{result.stderr or result.stdout}")
        if result.stderr.strip():
            # kibo renders and says what it could not resolve -- a property a template reads
            # that the model does not have. A run that succeeds still owes it to the author.
            for line in result.stderr.strip().splitlines():
                print(f"   {line}")


def generate_target(project: Project, pack: Pack, jar: Path, dsm: Path, encoded: bytes, target: Target,
                    selection: Selection | None = None) -> None:
    layout = pack.layout(target.language)
    fill = {"infrastructure": target.infrastructure}
    sources = target.output / layout.get("sources", "").format(**fill)
    at_root = set(layout.get("root", []))
    rendered = pack.rendered(project, target)
    templates = pack.templates(target.language, rendered)

    def carried(part: Table) -> bool:
        # What the pack writes beside the templates goes with the feature that reads it.
        return "with" not in part or part["with"] in rendered

    if target.clean:
        # The sources directory belongs to the generator: emptying it is what removes the files
        # of a type the definitions no longer declare. Never the project's own directory.
        if project.root.is_relative_to(sources):
            raise ProjectError(f"[target.{target.name}] clean would empty {sources}, which holds the project")
        shutil.rmtree(sources, ignore_errors=True)
    naming = project.naming + project.spelling(target.language) + pack.reserved(target.language)
    render(jar, target, dsm, [t for t in templates if t.name not in at_root], sources, naming)
    render(jar, target, dsm, [t for t in templates if t.name in at_root], target.output, naming)

    resources = layout.get("resources")
    if resources and carried(resources):
        encoding = ENCODINGS.get(resources["encoding"])
        if encoding is None:
            raise ProjectError(f"the pack asks for the encoding {resources['encoding']!r}, "
                               f"which this tool does not know ({', '.join(ENCODINGS)})")
        subset = f", {subset_line(selection)}" if selection else ""
        provenance = f"Generated from {dsm.name} by kibo-project {VERSION}{subset}. Do not edit by hand."
        (sources / resources["path"].format(**fill)).write_text(
            encoding(encoded, resources, target.infrastructure, provenance))

    runtime = layout.get("runtime")
    if runtime and carried(runtime):
        destination = sources / runtime["to"]
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(pack.root / runtime["from"], destination,
                        ignore=shutil.ignore_patterns(*runtime.get("exclude", [])))


# MARK: - Validation

def find_tool(tool: str, start: Path) -> str | None:
    """Where a tool the pack's validation needs is: the running Python and its modules, or a
    node tool from the nearest node_modules above the output, else the PATH."""
    if tool == "python":
        return sys.executable
    if tool == "mypy":
        found = subprocess.run([sys.executable, "-m", "mypy", "--version"], capture_output=True)
        return sys.executable if found.returncode == 0 else None
    for directory in (start, *start.parents):
        candidate = directory / "node_modules" / ".bin" / tool
        if candidate.exists():
            return str(candidate)
    return shutil.which(tool)


def validate(project: Project, pack: Pack, target: Target) -> None:
    """Run what the pack declares to check a target's output. The script targets are silent: a
    field can mask a method and Python imports the class without a word; a type checker says so.
    A validation that cannot run is an error, unless the project switches it off."""
    steps = pack.validation(target.language)
    if not steps:
        return
    if not target.validate:
        print(f"   validation switched off ([target.{target.name}] validate = false)")
        return
    rendered = pack.rendered(project, target)
    with tempfile.TemporaryDirectory(prefix="kibo-validate-") as tmp:
        for step in steps:
            if "with" in step and step["with"] not in rendered:
                continue
            name = str(step.get("name", " ".join(map(str, step["run"]))))
            tools: dict[str, str] = {}
            for tool in step.get("tools", []):
                found = find_tool(str(tool), target.output)
                if found is None:
                    where = (f"in the Python running kibo-project ({sys.executable})" if tool in ("python", "mypy")
                             else f"in a node_modules above {target.output}, nor on the PATH")
                    raise ProjectError(
                        f"[target.{target.name}] the generated code cannot be validated ({name}): {tool} "
                        f"is not found {where}. Install it, or switch the validation off with "
                        f"`validate = false` in [target.{target.name}]")
                tools[str(tool)] = found
            fill = {"python": sys.executable, "output": str(target.output), "pack": str(pack.root),
                    "infrastructure": target.infrastructure, "tmp": tmp, **tools}
            command = [str(part).format(**fill) for part in step["run"]]
            env = {**os.environ, **{str(k): str(v).format(**fill) for k, v in step.get("env", {}).items()}}
            result = subprocess.run(command, cwd=target.output, env=env, capture_output=True, text=True)
            if result.returncode != 0:
                lines = (result.stdout + result.stderr).strip().splitlines()
                shown = "\n".join(f"  {line}" for line in lines[:30])
                more = f"\n  ... {len(lines) - 30} more lines" if len(lines) > 30 else ""
                raise ProjectError(f"[target.{target.name}] the generated code does not validate ({name}):\n"
                                   f"{shown}{more}\n"
                                   f"If it names a DSM name {target.language} cannot take, spell that name otherwise, "
                                   f"the model unchanged: [names.{target.language}.rename] <DSM name> = \"...\" in the "
                                   f"project file. Otherwise the templates wrote code that does not validate: report "
                                   f"it to their pack, {pack.root}, with the lines above.")
            print(f"   validated: {name}")


def generate(project: Project, only: list[str], validating: bool = True) -> None:
    pack = Pack(find_templates(project.templates_line), project.manifests)
    jar = find_kibo(pack.kibo_floor, project.kibo_line)
    missing = [t for t in only if t not in project.targets]
    if missing:
        raise ProjectError(f"{project.path}: no [target.{missing[0]}] (targets: {', '.join(project.targets)})")
    targets = [project.targets[t] for t in (only or project.targets)]
    for target in targets:
        pack.rendered(project, target)
    print(f"kibo: {jar.name}   templates: {pack.root}")
    dsm, encoded, selection = assemble(project)
    if selection:
        print(f"select: {subset_line(selection)}")
    for target in targets:
        print(f"** {target.name} -> {os.path.relpath(target.output, project.workdir)}")
        generate_target(project, pack, jar, dsm, encoded, target, selection)
        if validating:
            validate(project, pack, target)


# Written beside the generated files by running them, never by the generator.
NOT_GENERATED = ("__pycache__", ".mypy_cache", ".DS_Store")


def _files(root: Path) -> set[Path]:
    if not root.is_dir():
        return set()
    return {path.relative_to(root) for path in root.rglob("*")
            if path.is_file() and not any(part in NOT_GENERATED or part.endswith(".pyc") for part in path.parts)}


def check(project: Project, only: list[str]) -> bool:
    """Whether the outputs in place are what the project generates now: every target rendered
    into a scratch directory, compared file by file. Nothing is written in the project. A file
    the outputs hold and the generation does not is reported where the generator owns the
    directory (`clean`); elsewhere it may be the project's own."""
    targets = only or list(project.targets)
    in_place = {name: project.targets[name].output for name in targets if name in project.targets}
    with tempfile.TemporaryDirectory(prefix="kibo-check-") as tmp:
        scratch = Path(tmp)
        relocate(project, scratch)
        pack = Pack(find_templates(project.templates_line), project.manifests)
        generate(project, only, validating=False)
        stale = 0
        for name, output in in_place.items():
            target = project.targets[name]
            fresh = _files(target.output)
            current = _files(output)
            report: list[str] = []
            for path in sorted(fresh):
                if path not in current:
                    report.append(f"   missing  {path}")
                elif (target.output / path).read_bytes() != (output / path).read_bytes():
                    report.append(f"   differs  {path}")
            if target.clean:
                sources = pack.layout(target.language).get("sources", "").format(infrastructure=target.infrastructure)
                owned = Path(sources)
                for path in sorted(current - fresh):
                    if sources and path.is_relative_to(owned):
                        report.append(f"   extra    {path}")
            where = os.path.relpath(output, project.root)
            if report:
                stale += 1
                print(f"[{name}] {where}: not current")
                print("\n".join(report))
            else:
                print(f"[{name}] {where}: current")
    return stale == 0


def plan(project: Project) -> None:
    pack = Pack(find_templates(project.templates_line), project.manifests)
    jar = find_kibo(pack.kibo_floor, project.kibo_line)
    rendered = {name: pack.rendered(project, target) for name, target in project.targets.items()}
    print(f"project:        {project.path}")
    print(f"definitions:    {', '.join(str(d) for d in project.definitions)}")
    print(f"kibo:           {jar}")
    print(f"templates:      {pack.root}")
    if project.select is not None:
        selection = select(project, parse(project)[0])
        print(f"select:         {len(project.select)} attachments named")
        for kind in selection.whole:
            print(f"    {kind:<14} {selection.kept[kind]:>6} of {selection.whole[kind]}")
    for target in project.targets.values():
        layout = pack.layout(target.language)
        print(f"[{target.name}] {target.language} -n {target.infrastructure} -> {target.output}")
        print(f"    features: {', '.join(rendered[target.name])}")
        for template in pack.templates(target.language, rendered[target.name]):
            where = "root" if template.name in set(layout.get("root", [])) else "sources"
            base = pack.root if template.is_relative_to(pack.root) else project.root
            shown = template.relative_to(base) if template.is_relative_to(base) else template
            print(f"    {str(shown):<40} {where}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"kibo-project {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_ in (("generate", "render every target, or those named"), ("plan", "show what generate would do"),
                        ("check", "say whether the outputs in place are current; write nothing")):
        command = commands.add_parser(name, help=help_)
        command.add_argument("project", nargs="?", default="kibo.toml", type=Path)
        if name in ("generate", "check"):
            command.add_argument("--target", action="append", default=[], help="a target's name; repeatable")
        if name == "generate":
            command.add_argument("--into", type=Path,
                                 help="render into this directory instead, leaving the project untouched")
            command.add_argument("--no-validate", action="store_true",
                                 help="skip the pack's validation of every target, saying so")
        command.add_argument("--definitions", type=Path,
                             help="render another model than the project's, a file or a folder of definitions")
    arguments = parser.parse_args(argv)
    try:
        project = load_project(arguments.project)
        if getattr(arguments, "into", None):
            relocate(project, arguments.into)
        if arguments.definitions:
            project.definitions = [arguments.definitions.resolve()]
        if arguments.command == "generate":
            if arguments.no_validate:
                for target in project.targets.values():
                    target.validate = False
            generate(project, arguments.target)
        elif arguments.command == "check":
            return 0 if check(project, arguments.target) else 1
        else:
            plan(project)
    except ProjectError as error:
        print(f"kibo-project: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
