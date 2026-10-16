# Member 4 Verification Decisions

Local production-verifier choices for Member 4 (`ZHOU SIHAN`) so Member 1 can integrate against explicit behavior.

## Delivered interface

`agent.verification.GameVerifier` implements the asynchronous `VerifierPort` contract without modifying `agent/state.py`, `agent/controller.py`, budgets, or phases.

```python
from agent.verification import GameVerifier

verifier = GameVerifier()
# Dependencies(..., verifier=verifier, ...)
```

## Chosen behavior

### Acceptance criteria

- Criteria are derived from `view.spec_text` only, never from planner claims.
- Suite version is `member4-<sha12>` over normalized specification text.
- Mandatory categories always include `launch`, `functional`, and `interaction`.
- A `technical` criterion is added when the specification mentions canvas/HTML/local assets.

### Verification actions

Allowed kinds only:

- `inspect_workspace`
- `read_file`
- `run_command`

Writes and dependency installation are never proposed.

### Bounded command policy

Member 3 requires every command to finish. The Playwright harness therefore:

1. Starts `view.launch_command` (or a local `http.server` fallback),
2. Opens the page and checks launch / functional / interaction / technical signals,
3. Terminates the launched process group,
4. Prints `HARNESS_JSON=...` and exits.

Opening a page without an observable interaction state change fails `C-INTERACTION`.

### Evidence

- Every `CriterionResult.evidence_refs` is non-empty.
- Harness refs include screenshots under `.verification-evidence/` inside `game/` when the command cwd is the game workspace.
- Static file reads contribute supporting refs but cannot alone prove interaction.

### Controller success gate

`GameVerifier` may emit PASS rows with evidence; Member 1 still rejects stale digests/revisions, empty evidence, missing mandatory coverage, or cycles with zero successful verification tool dispatches.

## Non-goals

- No LLM calls inside the verifier.
- No mutation of controller state.
- No long-lived game servers left running after a check.
