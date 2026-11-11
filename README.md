# Tools

Independent tools, each with its own source, documentation, tests and packaging.
Run development commands from the tool's directory.

| Tool | Purpose | Documentation |
| --- | --- | --- |
| [tsr](tsr/) | Save and restore Ghostty windows, folders and agent sessions on macOS | [Usage](tsr/README.md), [spec](tsr/docs/spec.md), [verification and remaining acceptance](tsr/docs/v1.md) |

To work on tsr, use Python 3.14+ and uv:

```sh
cd tsr
uv run tsr --help
uv run python -m unittest discover -s tests -v
```

Read a tool's `AGENTS.md` before changing it. Do not run desktop probes without
the owner's permission; they can create windows or send terminal input.

Use a `tmp/` folder within each tool for scratch notes, drafts and local outputs.
Directories and symlinks named `tmp` are ignored at any depth. Keep source,
documentation and anonymized test fixtures outside scratch folders.

The root `.gitignore` covers macOS metadata and scratch folders. Each tool owns
language-specific ignores; tsr's covers Python environments, caches and build
artifacts. Keep reproducibility files such as `uv.lock` tracked.

Publication, repository access and release tags are owner-managed. This initial
checkout includes implementation and local verification; see each tool's report
for acceptance checks that still require external environments.
