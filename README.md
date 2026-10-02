# kibo-project

A project states its code generation once, in a project file; `kibo-project` reads it and drives
the two components it does not replace: `dsviper`, which assembles the DSM into definitions, and
the [kibo](https://github.com/digital-substrate/kibo) jar, which renders a template pack.

`kibo_project.py`:

1. assembles the definitions with `dsviper`, into `<infrastructure>.dsm.json` beside the
   project file (an intermediate: ignore it in version control);
2. finds the template pack, and the newest kibo jar the pack accepts;
3. resolves the features into the templates they need, dependencies included;
4. runs kibo once per template, into the directories the pack declares;
5. writes the embedded definitions in the encoding the pack declares, and copies the pack's
   runtime beside the generated sources.

Every file it writes says where it comes from: kibo's banner names the `.dsm.json`
relative to the project, the same on every machine, and the embedded definitions open with
the same line, naming this tool.

The tool knows nothing of a particular pack: what a feature renders, where it lands and how the
definitions are embedded are read from the pack's `features.json`. A project no longer carries a
generation script.

Status: in development, for the kibo 2 line.

## Usage

```bash
python3 kibo_project.py generate [kibo.toml] [--target NAME ...] [--definitions PATH]
python3 kibo_project.py plan     [kibo.toml] [--definitions PATH]
```

`plan` shows the jar and the pack it found and, per target, the features it renders and
every template with where it lands, without writing anything. `--definitions` renders
another model than the project's, for one run: a test programme checked against a real
project's model, for instance.

## The project file

```toml
[project]
definitions = "definitions"          # a .dsm file or a directory of them
infrastructure = "crossing"          # the name passed to kibo as -n

[generator]
templates = "2"                      # the template pack's line
manifests = ["../templates/features.json"]   # optional: the project's own features

[target.cpp]
features = ["TestApp", "AttachmentPool"]
output = "cpp/generated"

[target.python]
features = ["Base", "Pool", "Wheel"]
output = "python/generated"
clean = true                         # optional: empty the sources directory first
```

Paths are relative to the project file. A target may set its own `infrastructure`.

A target named after a language needs nothing more. A project that renders one language to
several places names each target for what it produces, and states its language:

```toml
[target.infrastructure]
language = "cpp"
features = ["Base", "Attachments", "AttachmentPool", "Pool"]
output = "src/rei"

[target.editor]                      # two files the application target compiles
language = "cpp"
features = ["PythonDefinitions"]
with_requirements = false            # Base and Attachments are the infrastructure's
output = "RaptorEditor/RaptorEditor"

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

## Where the generator comes from

| | |
|---|---|
| kibo jar | `KIBO_JAR`; else `kibo-*.jar` beside this script; else `../kibo/target/kibo-*.jar` |
| template pack | `KIBO_TEMPLATES`; else `../templates`; else `../kibo-template-viper` |

The pack is checked against the project's declared line through the version stamped in its
templates. The jar is the newest at or above the floor the pack declares (`generator.kibo` in
`features.json`); an explicit `KIBO_JAR` below that floor is refused.

## Requirements

Python 3.11 or later with `dsviper` installed, and Java 17 for kibo. On Python 3.10, install
`tomli`.

## License

MIT — see [LICENSE](LICENSE).
