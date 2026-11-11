# Tools

This repository is a home for small, independently maintained tools. Each
tool owns its implementation, documentation, tests and development workflow.

## Projects

### [tsr](tsr/README.md)

Save and restore Ghostty windows, folders and agent sessions on macOS.

## Repository conventions

- Work from the tool's directory and follow its `AGENTS.md` and project docs.
- Give each tool its own `CHANGELOG.md`; record only notable user-facing
  features and breaking changes, without implementation rationale or process
  notes.
- Keep each tool's runtime, dependencies, tests and language-specific ignore
  rules with that tool. Track lockfiles when the tool uses them.
- Use a `tmp/` directory inside a tool for scratch files. Any file, directory
  or symlink named `tmp` is ignored at every depth; do not keep source or
  durable documentation there.
- The root `.gitignore` handles macOS metadata. Project ignore files handle
  generated files for their respective languages and build tools.
