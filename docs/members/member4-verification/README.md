# Member 4 — Verification (`ZHOU SIHAN`)

Implements **VerifierPort** for PE6203 game coding agent acceptance testing.

- **Owner:** Member 4 / ZHOU SIHAN  
- **Entry class:** `GameVerifier`  
- **集成请用：** `from agent.verification import GameVerifier`（根目录 `agent/verification/` 是给成员1注入用的集成副本；本目录保留作交付说明，请勿删除）  
- **Does not modify** Member 1 controller/state, Member 3 runtime, or other members' files.

## What this module does

| Method | Behavior |
|---|---|
| `prepare_acceptance(view, limits) -> AcceptanceSuite` | Derives frozen criteria from `view.spec_text`. Always includes mandatory **launch**, **functional**, and **interaction** criteria (plus **technical** when the spec implies canvas/local assets). |
| `next_check(view, limits) -> ToolAction \| VerificationReport` | Proposes only `inspect_workspace` / `read_file` / `run_command`, then returns a `VerificationReport` with non-empty `evidence_refs`. |

Hard rules followed:

1. Verification tools are read-only or a single bounded command (no writes / installs).
2. The Playwright harness **starts → checks → shuts down** inside one `run_command`.
3. **Page open ≠ playability.** Interaction requires an observable state change.
4. The controller computes success; this module never mutates `AgentState`.

## Directory layout

```text
member4_ZHOU SIHAN/
├── README.md
├── MEMBER4_DECISIONS.md
├── requirements.txt
├── agent/
│   └── verification/
│       ├── __init__.py      # exports GameVerifier
│       ├── verifier.py      # GameVerifier (VerifierPort)
│       ├── criteria.py      # spec -> AcceptanceSuite
│       └── evidence.py      # tool observations -> CriterionResult
├── harness/
│   └── playwright_game_check.py   # invoked via run_command
└── tests/
    └── test_verifier.py
```

## How Member 1 should wire this

### 1. Place the package

根目录已提供集成副本（本 PR）：

```text
agent/verification/          # 注入用副本
harness/playwright_game_check.py
```

`member4_ZHOU SIHAN/` 保留作交付说明，不删除。也可显式传入：

```python
GameVerifier(harness_path="...")
```


### 2. Dependency injection

In the team factory (`create_dependencies`):

```python
from agent.verification import GameVerifier

def create_dependencies(config):
    return Dependencies(
        planner=...,
        runtime=...,
        verifier=GameVerifier(),          # <-- Member 4
        recovery=...,
        tracer=...,
    )
```

Optional:

```python
verifier=GameVerifier(harness_path="/absolute/path/to/playwright_game_check.py")
```

### 3. Shared types

`GameVerifier` imports contracts from Member 1:

```python
from agent.state import (
    AcceptanceSuite, AgentView, CallLimits, Criterion, CriterionResult,
    CheckStatus, ToolAction, ToolKind, VerificationReport, ...
)
```

Do not fork these types. If the frozen contract changes, coordinate with Member 1.

## Check pipeline (per verification cycle)

1. `inspect_workspace` — inventory game artifacts  
2. `read_file` — README / `index.html` / `main.js` (up to 3 files)  
3. `run_command` — Playwright harness with `view.launch_command`  
4. `VerificationReport` — one `CriterionResult` per frozen criterion  

Cycle state resets when `workspace_revision`, suite version, or `launch_command` changes.

## Playwright harness

```bash
# From a game/ directory after installing Playwright browsers:
pip install -r "member4_ZHOU SIHAN/requirements.txt"
python -m playwright install chromium

python "member4_ZHOU SIHAN/harness/playwright_game_check.py" \
  --timeout-seconds 40 \
  --launch python -m http.server 8765
```

The harness prints:

```text
HARNESS_JSON={"ok": ..., "checks": {"launch": ..., "functional": ..., "interaction": ..., "technical": ...}}
```

`GameVerifier` parses that line from `ToolResult.observation`.

## Run Member 4 unit tests

From the **repository root** (`6203-game-coding-agent/`):

```bash
python -m unittest "member4_ZHOU SIHAN.tests.test_verifier" -v
```

Or:

```bash
python -m unittest discover -s "member4_ZHOU SIHAN/tests" -v
```

These tests mock tool results and **do not** require a real browser. Live harness rehearsal needs Playwright + Chromium as above.

## Coordination notes

| Partner | Need |
|---|---|
| Member 2 | Provide a documented `launch_command` via `RequestVerification` and generate launch docs under `game/`. |
| Member 3 | Execute verification `run_command` with process-group cleanup; observe that argv may include an absolute path to the harness script. |
| Member 1 | Inject `Dependencies.verifier = GameVerifier(...)` and keep `agent.state` / controller unchanged. |
| Member 5 | If a criterion detail contains `test_script_error` (status stays `unknown`), do **not** REPAIR the game. The assertion script used a name that was not injected (`s` and `saved.*` only), for example a bare `before`. That result is not a game FAIL and not a game PASS. |
| Member 6 | Optional: store harness screenshots under trace output refs if the runtime forwards `output_refs`. |

## Status

- [x] VerifierPort implementation (`GameVerifier`)
- [x] Mandatory launch / functional / interaction coverage
- [x] Bounded Playwright harness script
- [x] Local unit tests without browser
- [x] Root integration copy at `agent/verification/` + `harness/`
- [ ] Full-system rehearsal after Members 2–3–6 are connected
