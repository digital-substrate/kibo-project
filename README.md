# kibo-project

A project states its code generation once, in a project file; `kibo-project` reads it and drives
the two components it does not replace: `dsviper`, which assembles the DSM into definitions, and
the [kibo](https://github.com/digital-substrate/kibo) jar, which renders a template pack.

`kibo_project.py`:

1. assembles the definitions with `dsviper`, into `<infrastructure>.dsm.json` beside the
   project file (an intermediate: ignore it in version control) — or only the attachments the
   project selects, and what they depend on (`[select]`);
2. finds the template pack, and the newest kibo jar the pack accepts;
3. resolves the features into the templates they need, dependencies included;
4. runs kibo, into the directories the pack declares — once per directory, every template in
   the same run, with kibo 2.0.1 or later; once per template with an older kibo;
5. writes the embedded definitions in the encoding the pack declares, and copies the pack's
   runtime beside the generated sources.
6. validates what it wrote, as the pack declares: for the script targets, where a wrong name
   would otherwise go unnoticed — Python imported, every structure built, `mypy --strict`;
   TypeScript through `tsc`. A validation that cannot run, its tool missing, is an error.

Every file it writes says where it comes from: kibo's banner names the `.dsm.json`
relative to the project, the same on every machine, and the embedded definitions open with
the same line, naming this tool.

The tool knows nothing of a particular pack: what a feature renders, where it lands and how the
definitions are embedded are read from the pack's `features.json`. A project no longer carries a
generation script.

Status: in development, for the kibo 2 line.

## Usage

```bash
python3 kibo_project.py generate [kibo.toml] [--target NAME ...] [--definitions PATH] [--into DIR] [--no-validate]
python3 kibo_project.py plan     [kibo.toml] [--definitions PATH]
python3 kibo_project.py check    [kibo.toml] [--target NAME ...] [--definitions PATH]
```

`plan` shows the jar and the pack it found and, per target, the features it renders and
every template with where it lands, without writing anything. `--definitions` renders
another model than the project's, for one run: a test programme checked against a real
project's model, for instance. `--into` renders into another directory, each output keeping
its place relative to the project, and leaves the project untouched: two renderings, before
and after a change, can then be compared. `--no-validate` skips the pack's validation of every
target, for a rendering compared as text rather than run.

`check` says whether the outputs in place are what the project generates now, and writes nothing
in the project: every target is rendered into a scratch directory and compared file by file. It
exits 0 when they are current; otherwise 1, listing per target each file that differs, is missing,
or is extra — extra only in a directory the generator owns (`clean`), where it is a file of a type
the definitions no longer declare. Files that running the code leaves (`__pycache__`, `.mypy_cache`)
are not compared. The pack's validation does not run: the comparison is of text.

## The project file

```toml
[project]
definitions = "definitions"          # a .dsm file, a directory of them, or a list of either
infrastructure = "crossing"          # the name passed to kibo as -n

[generator]
templates = "2"                      # the template pack's line
manifests = ["../templates/features.json"]   # optional: the project's own features

[target.cpp]
features = ["TestApp"]
output = "cpp/generated"

[target.python]
features = ["Base", "Pool", "Wheel"]
output = "python/generated"
clean = true                         # optional: empty the sources directory first
validate = false                     # optional: skip the pack's validation of this target
```

Paths are relative to the project file. A target may set its own `infrastructure`.

A target named after a language needs nothing more. A project that renders one language to
several places names each target for what it produces, and states its language:

```toml
[target.infrastructure]
language = "cpp"
features = ["Base", "Attachments", "Pool"]
output = "src/rei"

[target.client]                      # the pool's client side, for another binary
language = "cpp"
features = ["PoolRemote"]
with_requirements = false            # Base is the infrastructure's
output = "client/generated"

[target.scripts]
language = "python"
features = ["Base", "Pool"]
output = "RaptorEditor/RaptorEditor/Scripts"
```

- `[generator] kibo = "2"` optionally pins the kibo line; by default the pack's floor decides.
- `with_requirements = false` renders only the templates of the features named, not of those
  they require. It is refused unless another target of the same language and infrastructure
  renders the rest. The embedded definitions and the runtime go with the feature that reads
  them, as the pack declares, so such a target gets neither.
- `clean` removes the files of a type the definitions no longer declare. It is refused when the
  sources directory holds the project itself.
- A project's own manifest follows the pack's format; its templates sit beside it, in
  `<manifest dir>/<target>/`. A feature name the pack already declares is refused.
  Its `reserved` names — the modules or names its own templates take — are reserved as the
  pack's are: a DSM name meeting one stops the generation.

How a static name is spelled where kibo projects it to snake_case — a Python field, method,
parameter or module, the packages' directories — follows one rule (`vec3Curves` →
`vec3_curves`, `docUInt8` → `doc_uint8`, `render2DAttributes` → `render_2d_attributes`). Two
names that land on one spelling stop the generation. A project says what only its author knows:

```toml
[names]
atoms = ["IPv4", "YCoCg"]            # never split: IPv4Address -> ipv4_address

[names.rename]
"f_E" = "f_enum"                     # a whole name, spelled as written here
```

A DSM name is valid in every language or in none, and the model is never changed for one
target. When a target cannot take a name — a field `class` in C++, which its compiler refuses —
or a name meets one the pack's own code takes — a field `wrap_value` beside the method every
generated class has, which kibo refuses, saying so — the project spells it otherwise for that
target:

```toml
[names.cpp.rename]
class = "klass"                      # every C++ identifier for the DSM name `class`

[names.python.rename]
wrap_value = "wrapped"
```

The DSM name stays the one sent to the runtime: a C++ client and a Python service still meet on
the wire. Nothing is renamed without such a line.

### A part of the model

A project that reads a few attachments of a large model names them, and every target is
generated from those attachments and the types they depend on, nothing else:

```toml
[select]
attachments = [
    "Patchwork3D::Surface.geometry",     # Namespace::KeyType.identifier
    "Patchwork3D::Product.name",
]
# or: attachments = "attachments.txt"  -- one name per line, `#` starts a comment
```

- A name is `Namespace::KeyType.identifier`: an identifier alone is not unique, the same one may
  be declared on several concepts.
- dsviper writes the selection as DSM source, which is parsed again: the model every target reads
  is held to the same parse as any other. Runtime ids are the whole model's, so a program built on
  the selection opens a database written with the whole model.
- The embedded definitions are the selection's; the intermediate is
  `<infrastructure>.subset.dsm.json`, so every generated file's banner says it renders a subset,
  and the embedded definitions say how much of the model (`46 of its 385 attachments`).
- `plan` prints the selection's counts (attachments, concepts, structures, enumerations) beside
  the whole model's.
- Refused, before anything is written: a name the model does not declare (the refusal lists the
  declarations nearest to it), an empty selection (remove `[select]` to generate the whole model),
  and any key but `attachments` — function pools, and types read outside any attachment, are not
  selectable.
- A project that reads an attachment it did not select fails to build: the accessor does not
  exist in C++, the import fails in Python and TypeScript.

## Where the generator comes from

| | |
|---|---|
| kibo jar | `KIBO_JAR`; else `kibo-*.jar` beside this script, or in `../kibo-*/tools/`; else `../kibo/target/kibo-*.jar` |
| template pack | `KIBO_TEMPLATES`; else `../kibo-<line>/templates`; else `../templates`; else `../kibo-template-viper` |

In the DevKit, this script is `tools/kibo_project.py`, and each generator line sits beside
`tools/` in its own folder: `kibo-2/tools/kibo-2.*.jar` and `kibo-2/templates/` for
`templates = "2"`.

The pack is checked against the project's declared line through the version stamped in its
templates. The jar is the newest at or above the floor the pack declares (`generator.kibo` in
`features.json`); an explicit `KIBO_JAR` below that floor is refused.

## Requirements

Python 3.11 or later with `dsviper` installed, and Java 17 for kibo. On Python 3.10, install
`tomli`.

## License

MIT — see [LICENSE](LICENSE).
