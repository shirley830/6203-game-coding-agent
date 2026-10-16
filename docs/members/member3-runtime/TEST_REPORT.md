# Member 3 Runtime Test Report

Verification date: 2026-10-13, Asia/Singapore.

Runtime used for verification: Python 3.12.14. The project contract remains Python 3.11 or newer.

## Automated result

Command:

```bash
python -m unittest discover -v
```

Result after Member 3 implementation: all 58 tests passed. This consists of the original 43 controller/configuration tests and 15 new real-runtime tests.

## Member 3 coverage

- Real `Controller` dispatch through `LocalRuntime` with real file creation and subprocess verification.
- Empty-workspace creation and protection of existing artifacts.
- UTF-8 specification reading.
- Atomic nested file creation, readback, inspection, and deterministic revisions.
- All-or-nothing unified-diff application and patch-conflict preservation.
- Independent traversal and symbolic-link protection inside the runtime.
- Successful subprocess execution with real stdout/stderr files.
- Distinct non-zero-exit and timeout results.
- Timeout process-group cleanup confirmation.
- Shell rejection and prevention of dependency-install bypass through `run_command`.
- A real offline wheel installation into the isolated Python environment, followed by import through the managed environment.
- Member 6 output-directory injection into `traces/<run-id>/raw`.
- Runtime single-run enforcement.
- Cache-independent workspace revisions and executable-bit tracking.
- macOS sandbox profile scope construction.
- Wheel packaging includes the `agent.tools` subpackage.

## Platform limitation requiring rehearsal

The development host runs Codex inside an outer macOS sandbox. macOS rejects activation of a nested `sandbox-exec` with `sandbox_apply: Operation not permitted`. Consequently, command behavior tests use `strict_os_sandbox=False` while still running inside Codex's outer sandbox.

Production defaults remain fail-closed with strict sandboxing enabled. Before the assessed run, execute the real-runtime tests from a normal terminal on the presentation machine and confirm:

1. macOS `sandbox-exec` or Linux `bwrap` starts successfully;
2. commands can write inside `game/` and the run-support directory;
3. commands cannot write to `agent/`, the specification, `traces/` without the Member 6 output hook, or arbitrary external paths;
4. game launch, local port binding, screenshots, and browser checks required by Member 4 work inside the selected sandbox;
5. process groups leave no children after timeout or cancellation.

Node.js and npm were not available in the development environment. npm manager selection and invocation are implemented, but an offline npm installation and `npm run` flow must be added to the rehearsal matrix if the team decides to generate Node-based games.

Windows command execution is intentionally unsupported until a strict Windows sandbox adapter is designed and tested.
