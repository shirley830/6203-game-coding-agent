# Member 3 Runtime Decisions

This document records the local production-runtime choices made for Member 3 so the other team members can integrate against explicit behavior rather than assumptions.

## Delivered interface

`agent.tools.LocalRuntime` implements the existing asynchronous `RuntimePort` contract without modifying `agent/state.py`, `agent/controller.py`, controller phases, or controller-owned budgets.

```python
from agent.tools import LocalRuntime

runtime = LocalRuntime()
```

The team dependency factory should pass that object as `Dependencies.runtime`. A `LocalRuntime` instance is single-run and must not be reused with a different `run_id`.

## Module layout

```text
agent/tools/
├── __init__.py
├── commands.py       # command policy and structured presentation
├── dependencies.py   # isolated Python and Node installation
├── errors.py         # known runtime failures
├── files.py          # scoped file operations and unified-diff patches
├── processes.py      # subprocess, sandbox, output, and cleanup handling
├── revision.py       # deterministic workspace revision
└── runtime.py        # RuntimePort adapter and tool dispatch
```

## Chosen behavior

### Filesystem

- All tool file paths are POSIX-style paths relative to `game/`.
- Absolute paths, `..`, backslashes, NUL characters, and colon-containing paths are rejected.
- File tools reject every symbolic link in a requested path. This is intentionally stricter than only rejecting escaping symlinks and avoids time-of-check/time-of-use ambiguity.
- Writes are atomic: content is written and flushed to a sibling temporary file, then replaced into place.
- `prepare_workspace` creates an empty `game/` and refuses an existing non-empty directory. It never deletes artifacts.
- Text input is UTF-8. Normal file reads are capped at 256 KiB; the specification is capped at 1 MiB.
- `patch_file` accepts one all-or-nothing unified diff. Partial application and multiple-file patches are rejected.

### Commands and processes

- Commands use argument vectors and `asyncio.create_subprocess_exec`; no implicit shell is used.
- Explicit shell executables such as `sh`, `bash`, `zsh`, PowerShell, and `cmd.exe` are rejected.
- Package installation through `run_command` is rejected; it must use `install_dependencies` so controller command accounting and environment revision remain accurate.
- Every process starts in a new process group. Timeout and cancellation terminate the group with TERM, then KILL if needed.
- Each action is bounded and must end. No hidden background server survives a successful tool result. Member 4 should request a bounded verification harness that starts, tests, and stops a game/server within one command.
- stdout and stderr go directly to files, preventing pipe deadlocks and unbounded in-memory output. Tool observations contain a bounded 8 KiB preview.

### OS sandbox

- Strict OS sandboxing is enabled by default.
- macOS uses `/usr/bin/sandbox-exec` with the filesystem read-only except for `game/` and the run-support directory.
- Linux uses `bubblewrap` when `bwrap` is installed, with `/` read-only and only `game/` plus run-support storage writable.
- Unsupported or unavailable sandboxes fail closed with `SANDBOX_UNAVAILABLE`.
- Windows has no strict adapter in this version and therefore fails closed for production command execution.
- `RuntimeSettings(strict_os_sandbox=False)` exists only for unit tests or execution inside an already enforced outer sandbox. It must not be selected for the assessed run.

The Codex desktop environment used to develop this version already applies an outer sandbox and rejects a nested `sandbox-exec`. Therefore functional subprocess tests explicitly disable the inner adapter. The strict profile construction is tested, but a rehearsal from a normal terminal on the final presentation machine is still required.

### Dependency environments

- If `package.json` exists, `install_dependencies` uses npm.
- Otherwise it uses pip. Python is the safe default for an empty or Python-only workspace.
- If Node and Python project markers both exist, the action fails with `DEPENDENCY_MANAGER_AMBIGUOUS` instead of guessing.
- Python packages are installed into `.agent-runtime/<run-id>/environment/python`, not global Python and not the submitted `game/` tree.
- npm installs into `game/node_modules`; npm cache files stay under the run-support directory.
- Installers are non-interactive and receive the controller timeout. No automatic retries are hidden inside the runtime.
- A normalized environment record is included in the workspace revision.

Member 2 should write the appropriate manifest before requesting installation. For mixed Python and Node games, separate-manager semantics require a future contract change owned by Member 1.

### Workspace revision

The revision is a SHA-256 hash over:

- relative paths and content of relevant files under `game/`;
- executable bits on regular files;
- symbolic-link targets, if a command created links;
- the normalized managed dependency record.

Volatile directories and bytecode are excluded: `.git`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `node_modules`, `*.pyc`, and `*.pyo`. Node dependency state is represented by manifests/lockfiles rather than hashing `node_modules`.

Read-only actions retain a revision. Writes, commands, and installation actions recompute it. A timed-out command returns `UNKNOWN` with `side_effects_unknown=True`; recovery must request a fresh workspace inspection.

### Raw output and Member 6

Until Member 6 connects a trace-owned store, complete command output is saved under:

```text
.agent-runtime/<run-id>/outputs/
```

The absolute local paths are returned in `ToolResult.output_refs`. Member 6 can own the final location without changing the runtime contract:

```python
runtime = LocalRuntime(
    output_directory_factory=lambda context: context.trace_dir / "raw"
)
```

An injected output directory is accepted only inside `traces/<run-id>/`. Member 6 must create/open the trace run before the controller dispatches runtime actions, which already matches the controller lifecycle.

### Environment and credentials

Child processes receive a minimal environment allowlist plus isolated `HOME`, temporary, pip-cache, npm-cache, and Python-cache paths. Credentials are not inherited automatically. If the final game toolchain needs a credential, the integration factory must inject only that named value through `RuntimeSettings.injected_environment` and Member 6 must redact it from stored output.

## Error codes for Member 5

The recovery module should treat these codes as stable local-runtime categories:

```text
ACTION_INVALID
COMMAND_INVALID
COMMAND_NOT_FOUND
COMMAND_TIMEOUT
CONTEXT_INVALID
CONTENT_INVALID
DEPENDENCIES_INVALID
DEPENDENCY_BYPASS
DEPENDENCY_INSTALL_FAILED
DEPENDENCY_MANAGER_AMBIGUOUS
DEPENDENCY_TIMEOUT
FILE_TOO_LARGE
INVALID_ENCODING
NONZERO_EXIT
PATCH_CONFLICT
PATCH_INVALID
PATH_INVALID
PATH_NOT_FOUND
PATH_OUTSIDE_WORKSPACE
PROCESS_CLEANUP_FAILED
RUNTIME_REUSE_NOT_ALLOWED
RUNTIME_STORAGE_UNSAFE
SANDBOX_UNAVAILABLE
SHELL_NOT_ALLOWED
SPEC_NOT_FOUND
SPEC_TOO_LARGE
SYMLINK_ESCAPE
SYMLINK_NOT_ALLOWED
TIMEOUT_INVALID
WORKSPACE_INVALID
WORKSPACE_NOT_EMPTY
```

`COMMAND_TIMEOUT`, `DEPENDENCY_TIMEOUT`, and `PROCESS_CLEANUP_FAILED` have unknown side effects and must not be directly retried. A plain `NONZERO_EXIT` has known post-process state and can be inspected before Member 5 chooses recovery.

## Packaging and integration

`pyproject.toml` now uses package discovery for `agent*` so `agent.tools` is included in wheels. No third-party Python runtime dependency was added.

Member 1 still owns `agent.integrations:create_dependencies`. Member 3 does not provide placeholder implementations for the other ports and does not make the formal launch command silently use mocks.

## Local verification commands

Use Python 3.11 or newer:

```bash
python -m unittest discover -v
python -m unittest tests.test_runtime -v
```

See `MEMBER3_TEST_REPORT.md` for verified scope and remaining system-level checks.
