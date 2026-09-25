# Serena

## Identity — this file's, then yours

This project's own instructions; the shared CLAUDE.md and shared rules carry everything else.
In this folder you own Serena's source: its agent, its language servers, its tool registry and its
tests.

## Bash Commands Claude Can't Guess

Use these exact commands; they are the only sanctioned ones for their purpose.

- `uv run poe format` — format (black + ruff). The ONLY allowed formatting command.
- `uv run poe type-check` — mypy. The ONLY allowed type-checking command.
- `uv run poe lint` — check style without fixing.
- `uv run poe test` — default markers, which EXCLUDE java and rust.
- `uv run poe test -m "python or go"` — one or more languages by marker.
- `uv run serena-mcp-server` — start the MCP server from the project root.
- `uv run index-project` — index a project so tools respond faster.

## Code Style Rules That Differ From Defaults

- Python 3.11, dependencies managed with `uv`.
- Strict typing under mypy; formatting is black plus ruff, applied only through the commands
  above.

## Testing Instructions and Preferred Test Runners

- IF running tests → `uv run poe test`. Java and rust are excluded by default and need their
  marker naming explicitly.
- Markers available: `python`, `go`, `java`, `rust`, `typescript`, `vue`, `php`, `perl`,
  `powershell`, `csharp`, `elixir`, `terraform`, `clojure`, `swift`, `bash`, `ruby`,
  `ruby_solargraph`, and `snapshot` for symbolic-editing operations.
- Integration coverage lives in `test_serena_agent.py`; test repositories under
  `test/resources/repos/<language>/` supply realistic symbol structures.
- **Always run format, type-check and test before calling any task complete.**

## Repository Etiquette

_Not yet populated — fill in when the information exists._

## Architectural Decisions Specific to This Project

Serena is a dual-layer coding agent toolkit.

- **SerenaAgent** (`src/serena/agent.py`) — central orchestrator over projects, tools and user
  interaction; coordinates language servers, memory persistence and the MCP interface.
- **SolidLanguageServer** (`src/solidlsp/ls.py`) — one wrapper over many LSP implementations,
  giving a language-agnostic symbol interface and owning caching, error recovery and server
  lifecycle.
- **Tool system** (`src/serena/tools/`) — file, symbol, memory, config and workflow tools.
- **Configuration** (`src/serena/config/`) — contexts define tool sets per environment, modes
  define operational patterns, projects hold per-project settings.
- **Configuration precedence**, highest first: command-line arguments to `serena-mcp-server`,
  then `.serena/project.yml`, then the user config, then active modes and contexts.
- **Memory** is markdown under `.serena/memories/`, persisted per project across sessions.

IF adding a LANGUAGE → language server class in `src/solidlsp/language_servers/`, entry in the
`Language` enum in `src/solidlsp/ls_config.py`, factory update in `src/solidlsp/ls.py`, test
repository, test suite, and a pytest marker in `pyproject.toml`.

IF adding a TOOL → inherit `Tool` from `src/serena/tools/tools_base.py`, implement its methods
and parameter validation, register it, and add it to the context and mode configurations.

## Developer Environment Quirks

- Language servers run as SEPARATE PROCESSES speaking LSP, downloaded automatically when a
  language first needs one. A first run for a new language is therefore slow and network-bound.
- Operation is async throughout; language server interactions do not block.

## Common Gotchas or Non-Obvious Behaviors

- IF tests pass suspiciously fast → check the markers. The default set excludes java and rust,
  so a green run says nothing about them.
- A crashed language server is restarted automatically, so a transient failure can disappear
  between runs rather than being fixed.
