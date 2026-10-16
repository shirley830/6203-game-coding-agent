<div align="center">

![PE6203 Game Coding Agent: from a specification to a verified game](assets/readme-banner.svg)

# PE6203 Game Coding Agent

**An autonomous coding agent designed to build playable games from unseen Markdown specifications.**

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Controller implemented](https://img.shields.io/badge/Controller-implemented-137C66?style=flat-square)
![137 local tests passed](https://img.shields.io/badge/Local_tests-137_passed-137C66?style=flat-square)
![Team modules integrated](https://img.shields.io/badge/Team_modules-integrated-137C66?style=flat-square)

[Quick start](#quick-start) &nbsp; / &nbsp; [Architecture](#architecture) &nbsp; / &nbsp; [Team contracts](INTERFACES.md) &nbsp; / &nbsp; [Test report](TEST_REPORT.md)

</div>

> [!IMPORTANT]
> **Current milestone: all six modules integrated.** `python -m agent game_design_prompt.md` wires the real planner (LiteLLM), sandboxed runtime, Playwright verifier, recovery policy and tracer through `agent/integrations.py`. An offline end-to-end rehearsal (scripted model, everything else real) passes: it builds a game, fails verification on interaction, repairs, and succeeds. A rehearsal with the course-provided model is still required before the presentation.

## Project at a glance

| Input | Development process | Preserved output |
|---|---|---|
| An unseen `game_design_prompt.md` | Plan, propose, execute, observe, verify, and recover | Generated `game/` plus execution records in `traces/` |

The agent must start with one documented command and develop autonomously without manual fixes. The controller owns the workflow, validates proposed actions, enforces budgets, and permits success only when verification and delivery conditions hold.

```bash
python -m agent game_design_prompt.md
```

This is the production command. If `AGENT_MODEL` is not configured it stops before development starts and never substitutes mocks.

## Why the harness matters

| Capability | Implemented behavior |
|---|---|
| Explicit control flow | A finite-state machine defines legal transitions and terminal outcomes |
| Typed handoffs | Shared dataclasses and asynchronous protocols define module inputs and results |
| One tool gateway | File actions, commands, installs, and verification tools pass through the controller |
| Bounded execution | Time, step, command, retry, recovery, and no-progress limits stop runaway loops |
| Evidence before success | Reports must match the current specification, workspace, and acceptance suite |
| Provider independence | LLM and runtime modules are injected through a dependency factory |
| Artifact preservation | Existing non-empty game directories are protected; failures retain partial work |

## Quick start

### 1. Install (once, before the presentation)

Python **3.11+** on macOS (uses `sandbox-exec`) or Linux with `bwrap` installed.

```bash
git clone https://github.com/shirley830/6203-game-coding-agent.git
cd 6203-game-coding-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
python -m playwright install chromium-headless-shell
```

### 2. Configure the model

```bash
export AGENT_MODEL=anthropic/claude-sonnet-5-5   # any LiteLLM model name
export ANTHROPIC_API_KEY=...                       # the provider's own key variable
# optional: export AGENT_API_BASE=https://...      # course-provided endpoint
```

Credentials are read only by the LiteLLM client; commands run with a filtered environment and keys never enter state, prompts or traces.

### 3. Run

From an empty directory containing only the specification:

```bash
python -m agent game_design_prompt.md
```

The agent creates `game/` (with `README.md` and the launch command), `traces/<run_id>/` and `.agent-runtime/`. Start the generated game with the command in `game/README.md`, normally `cd game && python3 -m http.server 8000 --bind 127.0.0.1`, then open http://127.0.0.1:8000/.

### 4. Checks and offline rehearsal

```bash
python -m pytest -q                                   # 137 tests incl. end-to-end
PYTHONPATH=<repo> python -m agent game_design_prompt.md \
    --factory tests.rehearsal_factory:create_dependencies   # no API key needed
python -m agent.delivery traces/<run_id>              # audit a finished trace
```

The rehearsal factory lives under `tests/` and uses a scripted model client; it is never the default.

## Architecture

The team builds six modules, while the runtime uses **one sequential controller**. Modules return proposals or observations; they do not mutate controller state.

```mermaid
flowchart TB
    CLI["CLI entry point<br/>Member 1"] --> C["Finite-state controller<br/>Transitions / budgets / success gate"]
    CFG["Validated configuration"] --> C
    STATE["Shared state and immutable views"] --- C
    C -. "planning calls" .-> P["Member 2<br/>PlannerPort"]
    C -->|"authorized tools"| RT["Member 3<br/>RuntimePort"]
    C -. "acceptance checks" .-> V["Member 4<br/>VerifierPort"]
    C -. "recovery advice" .-> R["Member 5<br/>RecoveryPort"]
    C -. "durable events" .-> T["Member 6<br/>TracePort"]
    RT --> G["game/<br/>Generated source and launch instructions"]
    T --> LOG["traces/<br/>Actions, observations, failures, checkpoints"]

    classDef ready fill:#e7f6f1,stroke:#137c66,color:#123e35;
    classDef pending fill:#fff8eb,stroke:#b7791f,color:#604419,stroke-dasharray:5 4;
    classDef artifact fill:#eef3fa,stroke:#5278a6,color:#233e60;
    class CLI,C,CFG,STATE ready;
    class P,V,R,T,RT ready;
    class G,LOG artifact;
```

All ports are wired by `agent/integrations.py:create_dependencies`.

The controller decides **whether** an action can run. Member 3 determines **how** it runs and must enforce OS/process isolation. A working-directory setting alone is not a sandbox.

## One action through the system

The same gateway serves generation, verification, and repair tools:

```mermaid
sequenceDiagram
    participant Port as Planner or verifier
    participant C as Controller
    participant T as TracePort
    participant R as RuntimePort
    Port->>C: Typed ToolAction
    C->>C: Validate scope and reserve budgets
    C->>T: Persist action_start
    T-->>C: Durable acknowledgement
    C->>R: Execute with context and limits
    R-->>C: ToolResult and workspace revision
    C->>C: Update state and invalidate stale evidence
    C->>T: Persist observation and checkpoint
    C-->>Port: Updated immutable view on next call
```

A rejected proposal consumes a step but does not reach the runtime. A timeout or uncertain side effect invalidates old verification and cannot be blindly retried.

## Development workflow

```mermaid
flowchart TD
    I["INITIALIZING<br/>Workspace / specification / frozen criteria"] --> P["PLANNING"]
    P --> D["DECIDING"]
    D -->|"propose tool"| E["EXECUTING"]
    E -->|"development observation"| D
    D -->|"request checks"| V["VERIFYING"]
    V -->|"next check"| E
    E -->|"verification observation"| V
    P -->|"error"| R["RECOVERING"]
    D -->|"error"| R
    E -->|"error or uncertain outcome"| R
    V -->|"failed or stale evidence"| R
    R -->|"repair"| D
    R -->|"replan"| P
    V -->|"mandatory checks pass"| F["FINALIZING"]
    R -->|"abort"| F
    STOP["Budget exhaustion or cancellation<br/>from any active phase"] --> F
    F --> S["SUCCEEDED"]
    F --> FAIL["FAILED"]
    F --> X["EXHAUSTED"]
    F --> CANCEL["CANCELLED"]

    classDef work fill:#eef3fa,stroke:#5278a6,color:#233e60;
    classDef gate fill:#e7f6f1,stroke:#137c66,color:#123e35;
    classDef recovery fill:#fff8eb,stroke:#b7791f,color:#604419;
    class I,P,D,E work;
    class V,F,S gate;
    class R,STOP,FAIL,X,CANCEL recovery;
```

This overview omits initialization-failure edges and safe retries back to the originating phase. [`TRANSITIONS`](agent/controller.py) is the authoritative transition table. Tool execution returns to the phase stored in `execution_return_phase`.

## The success gate

The model can request verification; it cannot declare success.

| Required condition | Controller check |
|---|---|
| Correct task | The report's specification digest matches the input |
| Current artifact | The report's workspace revision matches current state |
| Frozen criteria | The acceptance-suite version matches; every criterion has a result |
| Mandatory acceptance | Every mandatory result is PASS with non-empty evidence |
| Observed verification | At least one verification tool operation succeeded in this cycle |
| Launch information | A non-empty launch argument vector was supplied |
| Preserved delivery | game/ exists; TracePort confirms preservation in the correct directory; the terminal event is persisted |

Member 4 must establish actual startup, functionality, and interaction coverage. A process that starts without crashing is not enough to prove the game is playable.

## Budgets and outcomes

Defaults are tunable rehearsal settings, not assignment requirements.

| Limit | Default | CLI option |
|---|---:|---|
| Total runtime, including finalization | 900 s | `--max-runtime` |
| Work steps | 200 | `--max-steps` |
| Commands and dependency installations | 60 | `--max-commands` |
| Additional retries per operation | 2 | `--max-retries` |
| Global recovery attempts | 8 | `--max-recovery-attempts` |
| Model call timeout | 60 s | `--model-timeout` |
| Command timeout | 60 s | `--command-timeout` |
| Repeated no-progress cycles | 5 | `--max-no-progress` |
| Finalization reserve inside total runtime | 10 s | `--finalization-reserve` |
| Trace infrastructure timeout | 5 s | `--infrastructure-timeout` |

Each module call and tool attempt consumes a step. Rejected proposals and retries count; failure never refunds counters. Tracing has a timeout but does not consume steps. Alternating actions may evade the repetition heuristic, but global limits still apply.

| Outcome | Exit code | Meaning |
|---|---:|---|
| SUCCEEDED | 0 | Current acceptance and delivery conditions hold |
| FAILED | 1 | Unrecoverable failure, invalid evidence, or preservation failure |
| Startup/configuration error | 2 | Development did not start |
| EXHAUSTED | 3 | A budget or no-progress limit stopped development |
| CANCELLED | 130 | External cancellation or user interruption |

Runs return a JSON `RunSummary` with the reason, counters, artifact locations, launch command, and verification report. Non-success outcomes preserve existing work.

## Repository map

```text
6203-game-coding-agent/
├── agent/
│   ├── __init__.py
│   ├── __main__.py          # CLI and dependency-factory loading
│   ├── controller.py        # Protocols, state machine, and tool gateway
│   ├── state.py             # Shared data contracts and immutable views
│   ├── config.py            # Validated limits and paths
│   ├── integrations.py      # Production dependency factory (Member 1)
│   ├── planning/            # Member 2 LLM planner, prompts, parser
│   ├── tools/               # Member 3 sandboxed runtime
│   ├── verification/        # Member 4 GameVerifier + playwright_game_check.py
│   ├── recovery/            # Member 5 recovery policy
│   ├── tracing/             # Member 6 recorder + run_report.md renderer
│   └── delivery.py          # Member 6 trace audit
├── evaluation/              # Member 5 offline evaluation specs and report
├── assets/
│   └── readme-banner.svg    # Documentation artwork
├── tests/
│   ├── __init__.py
│   ├── test_controller.py   # Behavioral tests with injected fakes
│   ├── test_config_cli.py   # Configuration and entry-point tests
│   ├── test_runtime.py      # Member 3 real runtime tests
│   ├── test_runtime_process_cleanup.py # Process cleanup and uncertainty regressions
│   ├── test_planner.py / test_recovery.py / test_tracing.py / test_evaluation.py
│   ├── test_verifier_member4.py
│   ├── test_integration_tasks.py   # Cross-member checklist + end-to-end rehearsal
│   └── rehearsal_factory.py        # Scripted-model factory (tests only)
├── member4_ZHOU SIHAN/      # Member 4 delivery notes + source of truth copy
├── INTERFACES.md            # Team contract v1
├── TEST_REPORT.md           # Verified behavior and test limitations
├── README.md
├── pyproject.toml
└── .gitignore
```

Runtime outputs are separate from submitted Agent code:

```text
run-root/
├── agent/                  # Submitted Agent
├── game_design_prompt.md   # Unchanged input
├── game/                   # Generated source and launch documentation
├── .agent-runtime/         # Isolated dependency environments and default raw output
└── traces/
    └── <run-id>/           # Durable record for this run
```

`run_root` defaults to the launch directory. With `--run-root`, relative specification paths are relative to that directory. Absolute specification paths are read-only input; tool file paths are relative to game/. An existing non-empty game/ is never overwritten.

> [!NOTE]
> game/ and traces/ are ignored during development, but they must be preserved and submitted after the assessed run. Preserve referenced raw output and the managed environment under .agent-runtime/ too, or arrange trace-owned raw output and a documented reproducible dependency setup. Git ignore rules are not permission to delete them. No game source or template is prewritten in this repository.

## Evidence flow

Verification screenshots and logs are written by the harness into a per-command `AGENT_ARTIFACT_DIR` inside `.agent-runtime/` (the sandbox allows writes there, not to `traces/`), returned as `output_refs`, and copied by the tracer into `traces/<run_id>/outputs/` with an index. `game/` never receives evidence, so the workspace revision verified is the revision delivered.

## Team implementation map

| Member | Ownership | Interface | Current repository status |
|---|---|---|---|
| **1** | Entry point, controller, state, configuration, and integration | Dependencies / Controller | Framework implemented |
| **2** | LLM, planning, prompts, and content generation | PlannerPort | Integrated (`agent/planning`) |
| **3** | Files, commands, dependencies, and runtime isolation | RuntimePort | Integrated (`agent/tools`) |
| **4** | Game verification and acceptance testing | VerifierPort | Integrated (`agent/verification`) |
| **5** | Error recovery and evaluation | RecoveryPort | Integrated (`agent/recovery`, `evaluation/`) |
| **6** | Tracing, logging, and delivery | TracePort | Integrated (`agent/tracing`, `agent/delivery.py`) |

Read [INTERFACES.md](INTERFACES.md) before implementation. Use the shared types in [state.py](agent/state.py) and asynchronous signatures in [controller.py](agent/controller.py). Coordinate shared-contract changes with Member 1.

When a module is ready, provide its import path, construction requirements, dependencies, and tests. Member 1 will compose the real objects and run full-system rehearsals. For Members 3 and 6, agree on raw-output storage and references; for Members 3 and 4, agree on workspace revisions and bounded game checks.

## Testing scope

The [43-test verification record](TEST_REPORT.md) covers:

- Current-revision acceptance, rejection of stale or missing evidence, and repair followed by fresh verification.
- Safe retry accounting and rejection of retries with unknown side effects.
- Time, step, command, recovery, and no-progress stopping conditions.
- Path traversal, symlink escape, verifier writes, and undeclared-argument rejection.
- Existing-artifact protection, cancellation, trace failure, and delivery failure.
- The required module entry point with an explicitly selected test factory.

Mocks exist only under tests/. They do not install dependencies, execute game code, or replace missing production adapters. Real LLM calls, OS isolation, game interaction, and durable production logging remain integration checks.

## Roadmap and practical limits

- [x] Define typed team contracts and immutable module views.
- [x] Implement the Member 1 finite-state controller and configurable budgets.
- [x] Enforce current-revision verification before success.
- [x] Validate the framework with 43 mock-based tests.
- [x] Integrate Member 3 files, tests, and packaging locally for review.
- [x] Fix Member 3 process-group cleanup and pass all 65 integration tests.
- [x] Implement and connect remaining production ports.
- [x] Offline end-to-end rehearsal (scripted model, real runtime/verifier/recovery/tracing).
- [ ] Rehearse with the course-provided model against varied unseen specifications.
- [ ] Verify real game launch, interactions, failure recovery, and submission artifacts.

<details>
<summary><strong>Runtime and persistence limitations</strong></summary>

Checkpoints are saved, but automatic resume is not implemented. Controller instances are single-use. Token and monetary budgets await the common model/API selection. Runtime execution is sequential.

Timeouts require cooperative asynchronous cancellation. asyncio cannot forcibly stop arbitrary blocking or cancellation-resistant code. Member 3 must use managed process/isolation boundaries; Member 2 must use cancellable network calls. A single Ctrl+C requests best-effort finalization; repeated forced interruption, kill -9, power loss, or disk failure can prevent the final record from being written.

TracePort.preserved=True must reflect actual persistence. The controller checks directory existence and path agreement, but storage durability belongs to Member 6. If the terminal event cannot be saved, the CLI returns FAILED while an earlier summary may contain the proposed outcome. Auditing must check terminal events too.

</details>

---

**PE6203 group project** - reusable Agent infrastructure, generated game artifacts, and a traceable autonomous development process.
