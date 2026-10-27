<div align="center">

<img src="assets/readme-banner.svg" width="100%" alt="PE6203 Game Coding Agent: from an unseen specification to a verified game. A terminal replays a real run: 8 spec scenarios frozen, a failed check, an automatic repair, then 14 of 14 criteria passing.">

<p><b>Give it one Markdown spec. Get back a playable game, the tests it passed,<br>and a trace of every decision along the way.</b></p>

<p>
<img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-0f2a33?style=flat-square&logo=python&logoColor=6ff0cf&labelColor=0f2a33">
<img alt="298 tests passing" src="https://img.shields.io/badge/tests-298_passing-1f9d7a?style=flat-square&labelColor=0f2a33">
<img alt="6 of 6 modules integrated" src="https://img.shields.io/badge/modules-6%2F6_integrated-1f9d7a?style=flat-square&labelColor=0f2a33">
<img alt="Verified in headless Chromium" src="https://img.shields.io/badge/verified_in-Chromium_(Playwright)-2b7bb9?style=flat-square&labelColor=0f2a33">
<img alt="Any model via LiteLLM" src="https://img.shields.io/badge/models-any_via_LiteLLM-2b7bb9?style=flat-square&labelColor=0f2a33">
</p>

<p>
<a href="#quick-start"><b>Quick start</b></a> &nbsp;·&nbsp;
<a href="#games"><b>Games</b></a> &nbsp;·&nbsp;
<a href="#how-it-works"><b>How it works</b></a> &nbsp;·&nbsp;
<a href="#verification"><b>Verification</b></a> &nbsp;·&nbsp;
<a href="docs/testing/LIVE_MODEL_TESTS.md"><b>Live model tests</b></a> &nbsp;·&nbsp;
<a href="docs/INTERFACES.md"><b>Team contracts</b></a>
</p>

</div>

<img src="assets/glance.svg" width="100%" alt="At a glance. In: one unseen spec. Process: freeze tests, plan, write code, play it in a browser, repair. Out: a playable game and a complete trace.">

<table>
<tr><td><b>Status</b></td><td>All six modules integrated · 298 tests passing · 48 live runs on 5 models · course model Kimi K3 (<a href="docs/testing/LIVE_MODEL_TESTS.md">results</a>)</td></tr>
<tr><td><b>Success means</b></td><td>Every mandatory criterion passes on the current code, with evidence; nothing else counts</td></tr>
<tr><td><b>Never</b></td><td>Mocks in the production path, edits to <code>agent/</code> or the spec during a run, deleting earlier <code>game/</code> or <code>traces/</code></td></tr>
<tr><td><b>Submission</b></td><td><a href="agent/">agent/</a> (with <a href="agent/README.md">README</a>) · <code>game/</code> and <code>traces/</code> from the assessed run at the presentation · <a href="report.pdf">report.pdf</a></td></tr>
<tr><td><b>Report</b></td><td><a href="report.pdf">report.pdf</a> (section 7.4, the live presentation run, is completed after the presentation) · source <code>docs/report/report.html</code></td></tr>
</table>

<br>

<a name="quick-start"></a>
<img src="assets/sections/01-quick-start.svg" width="100%" alt="01 · Quick start">

<table>
<tr>
<td width="50%" valign="top">

**1 · Install once**

Python 3.11+ on macOS (`sandbox-exec`) or Linux with `bwrap`.

```bash
git clone https://github.com/shirley830/6203-game-coding-agent.git
cd 6203-game-coding-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
python -m playwright install chromium-headless-shell
```

</td>
<td width="50%" valign="top">

**2 · Configure the model**

`agent.env` next to the spec ([template](agent.env.example)):

```text
AGENT_MODEL=openrouter/moonshotai/kimi-k3
OPENROUTER_API_KEY=…
AGENT_MAX_RUNTIME=420
```

The course model is Kimi K3 through OpenRouter. Any other LiteLLM model name works too; its key uses the provider's own variable.

</td>
</tr>
<tr>
<td width="50%" valign="top">

**3 · Check the machine, then run**

```bash
python -m agent.preflight
python -m agent.preflight --probe-model   # one billed call: key, model and reasoning settings work
python -m agent game_design_prompt.md
```

The preflight prints `READY` or what is missing. The agent repeats it at start-up and stops **before** creating `game/` if success would be impossible. If a run ends without success, the agent restores the best version it verified (the outcome is still reported as not successful).

</td>
<td width="50%" valign="top">

**4 · Play and inspect**

```bash
cd game
python3 -m http.server 8000
```

The exact command is always in `game/README.md`; `traces/<run_id>/run_report.md` tells the story of the run.

</td>
</tr>
</table>

<a name="games"></a>
<img src="assets/sections/02-games.svg" width="100%" alt="02 · Games it built, live">

<table>
<tr>
<td width="50%"><img src="assets/gallery/collect.gif" width="100%" alt="Collection game generated live: Claude Sonnet 5.5, 8 spec scenarios, 14 of 14 criteria, 215 seconds"></td>
<td width="50%"><img src="assets/gallery/memory.gif" width="100%" alt="Memory cards game generated live: mouse only, 8 spec scenarios, 14 of 14 on the first try, 125 seconds"></td>
</tr>
<tr>
<td width="50%"><img src="assets/gallery/turns.gif" width="100%" alt="Turn-based key collection game generated live: 9 spec scenarios, 15 of 15, 199 seconds"></td>
<td width="50%"><img src="assets/gallery/hangman.svg" width="100%" alt="Terminal hangman generated live in Python: process mode, 4 of 4, 77 seconds"></td>
</tr>
</table>

<p align="center"><sub>The real games the agent generated from <code>evaluation/specs/</code>, recorded while playing themselves, and a terminal transcript; every number comes from that run's trace.</sub></p>

<a name="how-it-works"></a>
<img src="assets/sections/03-how-it-works.svg" width="100%" alt="03 · How it works">

<img src="assets/architecture.svg" width="100%" alt="Architecture: a finite-state controller decides; planner, verifier and recovery propose and check; the sandboxed runtime and tracing act and record, producing game/ and traces/.">

<table>
<tr>
<td width="33%" valign="top">

**Explicit control**

A finite-state machine with legal transitions only; typed, immutable views go to every module; one gateway for every file write, command and install.

</td>
<td width="33%" valign="top">

**Bounded by design**

Time, step, command, retry, recovery and no-progress limits; transient model errors wait (5 s, 15 s, 45 s, 60 s) before retrying; nothing with unknown side effects is ever retried blindly.

</td>
<td width="33%" valign="top">

**Evidence before success**

Success needs passing, evidenced checks for the **current** code and spec, a launch command, and a durable trace. The model can ask for verification; it cannot declare success.

</td>
</tr>
</table>

<a name="verification"></a>
<img src="assets/sections/04-verification.svg" width="100%" alt="04 · How a game is verified">

<img src="assets/verification.svg" width="100%" alt="Verification: the spec becomes frozen scenarios before any code exists, the harness plays the game in Chromium, a failed check is repaired, and success needs every mandatory criterion to pass.">

| A scenario from a real run | Result |
|---|---|
| `placePlayerAt(2,2); placeEnemyAt(3,2); key ArrowRight; expect s.status === 'lost'` | ❌ turn-based game: caught "walking into the enemy does not lose", which generic checks had passed |
| `click_cell [0,0]; click_cell [1,0]; click_cell [2,0]; expect s.cards[2].faceUp === false` | ✅ mouse-only memory game: flips are blocked during the mismatch delay, verified by real clicks |

<details>
<summary><b>The success gate, condition by condition</b></summary>

| Required condition | Controller check |
|---|---|
| Correct task | The report's specification digest matches the input |
| Current artifact | The report's workspace revision matches the current workspace |
| Frozen criteria | The acceptance-suite version matches; every criterion has a result |
| Mandatory acceptance | Every mandatory result is PASS with non-empty evidence |
| Observed verification | At least one verification tool operation succeeded in this cycle |
| Launch information | A non-empty launch argument vector was supplied |
| Preserved delivery | `game/` exists; the tracer confirms preservation; the terminal event is durable |

</details>

<details>
<summary><b>Non-browser games (terminal, pygame)</b></summary>

Games are built for the browser unless the specification explicitly asks otherwise. Python games (`python3 main.py`) are verified in **process mode**: the program must start and keep running without a traceback; terminal games are played by typing moves on standard input; graphical games run headless (SDL dummy driver) and their input handling is checked statically, labelled as such in the trace. Spec-derived scenarios apply to browser games only.

</details>

<a name="inside-a-run"></a>
<img src="assets/sections/05-inside-a-run.svg" width="100%" alt="05 · Inside a run">

<img src="assets/state-machine.svg" width="100%" alt="State machine: initializing, planning, deciding, executing, verifying; a failed check goes to recovering, which repairs or replans; passing verification finalizes the run as succeeded.">

<details>
<summary><b>One tool action through the system</b></summary>

```mermaid
%%{init: {"theme": "base", "themeVariables": {"actorBkg": "#e6f7f2", "actorBorder": "#1f9d7a", "actorTextColor": "#0f2a33", "actorLineColor": "#9fc3cb", "signalColor": "#0f2a33", "signalTextColor": "#0f2a33", "noteBkgColor": "#fff4e5", "noteBorderColor": "#d99a2b"}}}%%
sequenceDiagram
    participant Port as Planner or verifier
    participant C as Controller
    participant T as Tracing
    participant R as Runtime
    Port->>C: Typed ToolAction
    C->>C: Validate scope, reserve budgets
    C->>T: Persist action_start
    C->>R: Execute in sandbox with limits
    R-->>C: ToolResult + workspace revision
    C->>C: Update state, invalidate stale evidence
    C->>T: Persist result + checkpoint (copy logs, screenshots)
    C-->>Port: Fresh immutable view on the next call
```

A rejected proposal costs a step but never reaches the runtime. A timeout or uncertain side effect invalidates earlier verification and is never retried blindly. [`TRANSITIONS`](agent/controller.py) is the authoritative state table.

</details>

<a name="run-output"></a>
<img src="assets/sections/06-run-output.svg" width="100%" alt="06 · What a run leaves behind">

<img src="assets/run-output.svg" width="100%" alt="Run output: the run directory with game/ and traces/<run_id>/, and the trace files run_report.md, events.jsonl, evidence/, verification/, model_usage.jsonl and summary.json.">

> [!NOTE]
> `python -m agent.delivery traces/<run_id>` audits a finished trace: contiguous events, a terminal event, and every referenced screenshot present with a matching hash.

<a name="budgets"></a>
<img src="assets/sections/07-budgets.svg" width="100%" alt="07 · Budgets and outcomes">

<img src="assets/budgets.svg" width="100%" alt="Budgets: 1800 s runtime, 200 steps, 80 commands, 20 recoveries, 5 retries with backoff. Outcomes and exit codes: succeeded 0, failed 1, startup error 2, exhausted 3, cancelled 130.">

<details>
<summary><b>All limits and their options</b></summary>

| Limit | Default | Option |
|---|---:|---|
| Total runtime, including finalization | 1800 s | `AGENT_MAX_RUNTIME` or `--max-runtime` |
| Work steps | 200 | `--max-steps` |
| Commands and dependency installs | 80 | `--max-commands` |
| Additional retries per operation | 5 | `--max-retries` |
| Wait before the first retry (×3 each time, max 60 s) | 5 s | `--retry-backoff` |
| Global recovery attempts | 20 | `--max-recovery-attempts` |
| Model call timeout | 240 s | `--model-timeout` |
| Command timeout | 90 s | `--command-timeout` |
| Repeated no-progress cycles | 5 | `--max-no-progress` |
| Finalization reserve inside the runtime | 20 s | `--finalization-reserve` |
| Trace infrastructure timeout | 5 s | `--infrastructure-timeout` |

Every module call and tool attempt consumes a step, including rejected proposals and retries; failures never refund counters. After 8 consecutive edits without a verification, the planner requests one itself.

</details>

<a name="team"></a>
<img src="assets/sections/08-team.svg" width="100%" alt="08 · Team">

<img src="assets/team.svg" width="100%" alt="Team: six members and the module each owns.">

<details>
<summary><b>Modules, code and member notes</b></summary>

| Member | Module | Code | Notes |
|:---:|---|---|---|
| **1** | Controller, CLI, configuration, integration | `agent/controller.py` · `agent/integrations.py` | [Team contracts](docs/INTERFACES.md) |
| **2** | Planning, prompts, model client | `agent/planning/` | |
| **3** | Tools, sandbox, processes, dependencies | `agent/tools/` | [notes](docs/members/member3-runtime/) |
| **4** | Acceptance, spec scenarios, browser harness | `agent/verification/` | [notes](docs/members/member4-verification/) |
| **5** | Recovery policy, offline evaluation | `agent/recovery/` · `evaluation/` | [notes](docs/members/member5-recovery/) |
| **6** | Tracing, run report, delivery audit | `agent/tracing/` · `agent/delivery.py` | |

</details>

<details>
<summary><b>Repository map</b></summary>

```text
6203-game-coding-agent/
├── agent/                      SUBMITTED: the agent (no game code); see agent/README.md
│   ├── __main__.py             CLI: python -m agent game_design_prompt.md
│   ├── controller.py           state machine, tool gateway, success gate
│   ├── state.py · config.py    shared contracts, validated limits
│   ├── integrations.py         production wiring of the six modules
│   ├── preflight.py            start-up self-check (python -m agent.preflight)
│   ├── envfile.py              agent.env loading
│   ├── planning/               planner, prompts, parser, LiteLLM client
│   ├── tools/                  sandboxed runtime: files, commands, processes, deps
│   ├── verification/           verifier, spec scenarios, Playwright harness
│   ├── recovery/               error classification and recovery policy
│   ├── tracing/                recorder, run report, model usage
│   └── delivery.py             trace audit
├── game/                       SUBMITTED: the game built in the assessed run (created by the agent)
├── traces/                     SUBMITTED: the full trace of that run (created by the agent)
├── report.pdf                  SUBMITTED: the project report
├── evaluation/                 evaluation specs and run-record report
├── tests/                      298 tests; fixtures/ holds test-only pages
├── docs/                       contracts, integration review, member notes, testing
│   ├── report/                 report source; rebuild: python docs/report/build_report.py
│   └── handoff/member5/        Member 5 hand-off notes
├── assets/                     README artwork; regenerate: python3 assets/src/build_readme_art.py
├── agent.env.example
└── pyproject.toml
```

</details>

<a name="testing"></a>
<img src="assets/sections/09-testing.svg" width="100%" alt="09 · Testing">

<img src="assets/testing.svg" width="100%" alt="Testing: 298 tests, 48 live model runs with 5 models; succeeded: Kimi K3 (the course model) 6 of 9, GLM-5.3 14 of 16, Claude Sonnet 5.5 11 of 11, GPT-4.1-mini 5 of 8, Qwen3-Coder 1 of 4.">

```bash
python -m pytest -q
```

Tests by area: [docs/testing/README.md](docs/testing/README.md) · Live model results and findings: [docs/testing/LIVE_MODEL_TESTS.md](docs/testing/LIVE_MODEL_TESTS.md)

<br>

<img src="assets/footer.svg" width="100%" alt="Built live, verified in a browser, traced end to end. PE6203 Generative and Agentic AI group project.">
