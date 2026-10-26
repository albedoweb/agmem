# Codex plugin

The Codex plugin bundles two skills: project-memory retrieval and setup. It
uses the local agmem CLI and requires no MCP server, account, or API key.
It does not automatically inject context on every prompt or start a watcher.

From a local clone, install with Codex CLI:

```sh
codex plugin marketplace add /absolute/path/to/agmem
codex plugin add agmem@agmem-local
codex plugin list --marketplace agmem-local --json
```

Start a new Codex session after installation so it discovers the plugin skills.
Ask it to use agmem for the task or to set up agmem in a repository. The setup
skill installs the CLI if needed and initializes the target repository.

For CLI development, install the local checkout:

```sh
uv tool install --python 3.12 --editable /absolute/path/to/agmem
```

To remove the plugin:

```sh
codex plugin remove agmem@agmem-local
```

The package is in `codex-plugin/`; its marketplace catalog is
`.agents/plugins/marketplace.json`. The existing `plugin/` package remains the
Claude Code integration. Follow the installed CLI's `--help` if commands change.

For parallel workers, use one-shot `agmem context` calls. For isolated benchmark
conditions, use separate clones: linked worktrees share memory and `--session`
shares retrieval history within a repository.

Packaging follows the [OpenAI plugin documentation](https://developers.openai.com/plugins/build/plugins).
