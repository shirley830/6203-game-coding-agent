# agent/ — the coding agent

An autonomous coding agent that reads one previously unseen game specification, builds the game into `game/`, verifies
it by playing it, and keeps a complete execution record in `traces/`. It runs with one command and needs no human help
after it starts. The package contains no game code: every game is generated during the run.

## Run it

```bash
pip install -e ".[test]"                            # once, from the repository root
python -m playwright install chromium-headless-shell
cp agent.env.example agent.env                      # then fill in the key; never commit agent.env
python -m agent.preflight                           # machine check: Chromium, sandbox, port, Python
python -m agent.preflight --probe-model             # one billed call: key, model and reasoning settings work
python -m agent game_design_prompt.md
```

`game/` and `traces/` are created next to the specification (or under `--run-root`). The exit code is 0 when the run
succeeded. Start the finished game with the command written in `game/README.md`.

At the end the agent prints three result levels, also stored as `levels` in `summary.json` and at the top of
`run_report.md`:

```
Result: 未完整验收 acceptance incomplete (generic checks only)
  1. Launch (启动):          pass
  2. Specific rules (具体规则): 0 specific rules (未完整验收): only the generic checks ran
  3. Delivery (交付):        complete
```

`fully_accepted` (完整成功) needs a succeeded run, a passing launch check, every spec-derived rule (REQ-*) passed and
a complete delivery. A run that succeeded on the generic checks alone is `acceptance_incomplete`, never full
success. The levels do not change the outcome or the exit code, and they cannot detect a wrong test that passes.

When the verifier marks a mandatory criterion UNKNOWN with `test_script_error:` (a broken test script, not a game
defect), the controller reports the failure with category `test_script_error`. Recovery then stops the run as failed
and keeps the game and evidence; it never edits the game to satisfy a broken test. Other failed checks stay
repairable.

A check can also contradict the specification while looking valid (2026-10-27: a minesweeper check expected the
game to keep running after a click whose flood reveal wins at once). Such a check is disputed (`test_conflict`)
when the planner returns `dispute_check` with the specification rule as evidence, or when the same check fails in
the same way before and after the game was changed. The acceptance model then re-examines that one check: `keep`
sends the planner back to fixing the game; `replace` swaps in a check of the same rule (same id and category, at
least one expectation), and the unchanged game is verified again. Each check is re-examined at most once, two per
run; the old and new versions are in `events.jsonl` (`acceptance_revised`) and the result levels list them under
`revised_checks`. Rules are never dropped or weakened to reach a pass.

## Configuration

| Setting | Where | Default |
|---|---|---|
| Model | `AGENT_MODEL` in `agent.env` | course model `openrouter/moonshotai/kimi-k3` |
| API key | `OPENROUTER_API_KEY` in `agent.env` | none (required) |
| Total run time | `AGENT_MAX_RUNTIME` or `--max-runtime` (seconds) | 1800 |
| Reasoning cap | `AGENT_REASONING_MAX_TOKENS` | 8000 for GLM and Kimi models |
| Other budgets | `--max-steps`, `--max-commands`, `--max-retries`, `--max-recovery-attempts`, `--command-timeout` | 200, 80, 5, 20, 90 s |

`python -m agent --help` lists every option.

## How it works

One finite-state controller owns all state and decides; five modules propose and check. The model never declares
success: the controller grants it only when every mandatory acceptance criterion passes on the current code, with
evidence.

| Path | Role |
|---|---|
| `__main__.py`, `config.py`, `envfile.py` | Command line, validated limits, `agent.env` loading |
| `controller.py`, `state.py` | State machine, budgets, the single tool gateway, success gate, best-version restore |
| `integrations.py` | Production wiring of all modules |
| `preflight.py` | Start-up self-check and optional model probe |
| `planning/` | Planner: LiteLLM client, prompts, plan and decision parsing |
| `tools/` | Sandboxed runtime: files, commands, processes, dependencies, source snapshots |
| `verification/` | Spec-derived acceptance scenarios and the Playwright game harness |
| `recovery/` | Failure classification: retry, repair, replan or abort |
| `tracing/`, `delivery.py` | Durable events, evidence, `run_report.md`, trace audit |

## What a run leaves behind

- `game/` — the generated game and its `README.md` with the exact start command.
- `traces/<run_id>/` — `events.jsonl`, checkpoints, every verification report, screenshots with a SHA-256 index,
  model usage per call, source snapshots, and `run_report.md`, a readable timeline of the whole run.

Audit a finished trace with `python -m agent.delivery traces/<run_id>`. The design, evaluation and limitations are
described in [`report.pdf`](../report.pdf).
