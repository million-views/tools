# tsr development

Read [AGENTS.md](../AGENTS.md) and the [behavior specification](spec.md) before
changing code. The [verification report](v1.md) records implementation status,
coverage and remaining acceptance work. Use the [acceptance guide](acceptance.md)
for owner-run real-agent checks.

## Environment and tests

Use Python 3.14 and uv. The runtime uses only the standard library.

From the `tsr/` directory, run the test suite:

```sh
uv run python -m unittest discover -s tests -v
```

The suite uses `unittest` and does not require Ghostty or live agents.

## Isolated live capture

To exercise capture/list without replacing the normal tsr snapshot, choose a
separate state root:

```sh
TSR_CHECK_STATE=$(mktemp -d)
XDG_STATE_HOME="$TSR_CHECK_STATE" uv run tsr save
XDG_STATE_HOME="$TSR_CHECK_STATE" uv run tsr list
```

These commands read real Ghostty and agent metadata. They save private paths and
prompts in the selected state root, but do not create terminals or type into
them. Use the approval rules in `AGENTS.md` before any desktop probe.

The project is one production module at `src/tsr/__init__.py`. Keep runtime
dependencies in the standard library and the package layout in `pyproject.toml`.
