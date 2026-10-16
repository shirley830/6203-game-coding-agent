# Live model tests (OpenRouter, 2026-10-16)

Four rounds, 21 autonomous runs of `python -m agent game_design_prompt.md` against
real language models. Each round changed the agent based on what the previous
round's traces showed. This document is the evidence base for the report's
"how the agent verifies its work", "failure recovery" and "limitations" sections.

## 1. Setup

| Item | Value |
|---|---|
| Models (via OpenRouter, LiteLLM `openrouter/<id>`) | `anthropic/claude-sonnet-5.5` (strong), `openai/gpt-4.1-mini` (mid-tier), `qwen/qwen3-coder` (open model) |
| Specifications | `evaluation/specs/dev_01_collect.md` (keyboard collection game), `dev_03_memory.md` (mouse-only memory cards), `holdout_02_turns.md` (turn-based grid game with keys, enemy and exit) |
| Command | `python -m agent game_design_prompt.md --max-runtime 1200` from an empty run directory containing only the spec and `agent.env` |
| Budgets | 1200 s total, 200 steps, 80 commands, 20 recovery attempts, model timeout 240 s, command timeout 90 s |
| Machine | macOS (Apple silicon), Python 3.11, strict `sandbox-exec` command sandbox, Playwright headless Chromium |
| Runs per cell | 1 (results are single samples, see section 6) |

## 2. What changed between rounds

| Round | Code (commit) | Verification approach |
|---|---|---|
| **R1** baseline | `f1499a3` | Generic criteria only: launch, canvas/score signals, "input changes the page", canvas present |
| **R2** | `9570e79` | Spec-derived acceptance: before planning, the verifier asks the model for a state contract (`window.__gameState`, `window.__gameTest` setup hooks) and 3-10 executable scenarios (real keys/clicks + assertions); harness runs each scenario on a fresh page load; harness clicks canvas points and board elements; planner prompt forbids adding behaviour outside the spec to satisfy a check |
| **R3** | `97615eb` | JSON mode for all model calls; requirements validated one by one (keep valid ones); hook shorthand and nested descriptions accepted; canvas-click scenarios dropped for specs without mouse input; planner repairs unambiguous format slips; acceptance outcome recorded in the trace (`C-ACCEPTANCE-INFO`) |
| **R4** | `82802d6` | Planner requests verification after 8 consecutive edits; acceptance prompt rules against unpassable scenarios (targets relative to the current position, create pickups/collisions with hooks, change state before testing a reset, win by moving into the goal, mentally run each scenario) |

R4 re-ran only the three cases that failed in R3.

## 3. Results matrix

`✅` succeeded (controller verdict), `❌` exhausted a budget. "Spec review" is a manual
reading of the generated game against the specification.

| Case | R1 | R2 | R3 | R4 |
|---|---|---|---|---|
| Collection · Sonnet | ✅ 4/4 · spec review: correct | ✅ 13/13 (8 req.) | ✅ 14/14 (8 req.) | — |
| Collection · GPT-4.1-mini | ✅ 4/4 · spec review: misleading win hint | ✅ 4/4 · **acceptance fell back to generic** | ❌ time · unpassable tests + edit loop | **✅ 12/12** (6 req., 4 verification cycles) |
| Collection · Qwen3-Coder | ✅ 4/4 · spec review: **spawns not away from player, no stop after win** | ❌ recoveries · keyboard game tested with canvas clicks | ❌ steps · chance-based tests + edit loop | ❌ steps · contract missing in early cycles, best 11/12 |
| Memory · Sonnet | ✅ after 3 cycles · **added keyboard controls to pass the probe** | ✅ 12/12 (7 req.), no keyboard added | **✅ 14/14 first cycle** (8 req.) | — |
| Turns · Sonnet | ✅ 4/4 · spec review: correct | ✅ 15/15 (10 req.) | ✅ 15/15 (9 req.) | — |
| Turns · GPT-4.1-mini | ✅ 4/4 · spec review: **walking into the enemy does not lose** | ✅ 4/4 · **acceptance fell back to generic** | ❌ time · real defect + 1-2 bad tests | ❌ time · same real defect (13-14/15) |

## 4. Per-run metrics

| Round | Case | Outcome | Time | Steps | Recoveries | Spec req. | Verification cycles | File writes |
|---|---|---|---|---|---|---|---|---|
| R1 | Collection · Sonnet | succeeded | 66 s | 32 | 0 | 0 | 4/4 | 4 |
| R1 | Collection · GPT-4.1-mini | succeeded | 49 s | 22 | 0 | 0 | 4/4 | 4 |
| R1 | Collection · Qwen | succeeded | 217 s | 26 | 1 | 0 | 4/4 | 3 |
| R1 | Memory · Sonnet | succeeded | 208 s | 72 | 5 | 0 | 3/4, 3/4, 4/4 | 6 |
| R1 | Turns · Sonnet | succeeded | 98 s | 32 | 0 | 0 | 4/4 | 4 |
| R1 | Turns · GPT-4.1-mini | succeeded | 39 s | 22 | 0 | 0 | 4/4 | 3 |
| R2 | Collection · Sonnet | succeeded | 234 s | 57 | 3 | 8 | 12/13, 13/13 | 6 |
| R2 | Collection · GPT-4.1-mini | succeeded | 57 s | 24 | 1 | 0 (fallback) | 4/4 | 4 |
| R2 | Collection · Qwen | exhausted (recoveries) | 419 s | 196 | 20 | 6 | 7 cycles, best 9/11 | 26 |
| R2 | Memory · Sonnet | succeeded | 194 s | 51 | 2 | 7 | 11/12, 12/12 | 5 |
| R2 | Turns · Sonnet | succeeded | 168 s | 34 | 1 | 10 | 15/15 | 4 |
| R2 | Turns · GPT-4.1-mini | succeeded | 111 s | 22 | 1 | 0 (fallback) | 4/4 | 3 |
| R3 | Collection · Sonnet | succeeded | 215 s | 49 | 3 | 8 | 13/14, 14/14 | 4 |
| R3 | Collection · GPT-4.1-mini | exhausted (time) | 1180 s | 177 | 3 | 5 | 8/11, 8/11 | 74 |
| R3 | Collection · Qwen | exhausted (steps) | 311 s | 200 | 2 | 6 | 8/12 | 34 |
| R3 | Memory · Sonnet | succeeded | 125 s | 32 | 0 | 8 | 14/14 | 4 |
| R3 | Turns · Sonnet | succeeded | 199 s | 49 | 1 | 9 | 14/15, 15/15 | 5 |
| R3 | Turns · GPT-4.1-mini | exhausted (time) | 1180 s | 119 | 4 | 10 | 13/16, 13/16 | 44 |
| R4 | Collection · GPT-4.1-mini | succeeded | 602 s | 105 | 4 | 6 | 10/12, 11/12, 10/12, 12/12 | 28 |
| R4 | Collection · Qwen | exhausted (steps) | 354 s | 200 | 9 | 6 | 9 cycles, best 11/12 | 27 |
| R4 | Turns · GPT-4.1-mini | exhausted (time) | 1180 s | 149 | 8 | 9 | 13/15, 8/15, 13/15, 13/15, 14/15 | 41 |

## 5. Findings

### 5.1 Generic verification produced false passes (R1)

All six R1 runs "succeeded", yet manual review found specification violations in
three of them: Qwen's collectibles could spawn on the player and the game kept
running after 10 points; GPT-4.1-mini's turn-based game let the player walk into
the enemy without losing; GPT-4.1-mini's collection game showed a misleading win
hint. The generic checks (page loads, canvas exists, input changes pixels) cannot
see these. In R3/R4 the spec-derived scenario for "enemy contact loses" failed on
GPT-4.1-mini's game in every verification cycle, i.e. the defect is now detected.

### 5.2 A weak verifier pushes the agent to bend the game (R1 memory)

The mouse-only memory game failed the generic interaction probe twice because the
probe only pressed keys and clicked Restart. The agent then added arrow/Enter/digit
keyboard controls and made a click anywhere flip the nearest card, neither of
which the specification asks for. After R2 (mouse-aware harness, click scenarios,
anti-gaming prompt rule) the same specification passed 12/12 and 14/14 with no
keyboard controls added.

### 5.3 Model-written tests can be unpassable, and a bad test makes the agent loop

Spec-derived scenarios are written by the same model before any code exists. The
strong model wrote valid scenarios in every run. The weaker models wrote some that
no correct game could pass, for example:

* clicking the canvas to collect items in an arrow-key game (Qwen, R2);
* placing a collectible at (50, 50) and then moving the player right and down,
  away from it (GPT-4.1-mini, R3);
* expecting a pickup or win after holding an arrow key with no target placed
  (Qwen and GPT-4.1-mini, R3);
* expecting the position to change on restart when the player never moved
  (GPT-4.1-mini, R3).

Because the anti-gaming rule (correctly) stops the planner from adding behaviour
outside the specification, an unpassable test can only end in an exhausted budget.
R3/R4 mitigations: drop canvas-click scenarios for keyboard specifications, and
prompt rules against these patterns. They reduced but did not remove the problem
for the weaker models.

### 5.4 Output-format robustness matters as much as reasoning

* GPT-4.1-mini's acceptance documents were valid in 2 of 6 direct calls (same
  prompt) with per-requirement parsing, 4 of 6 after accepting hook-call
  shorthand, and 6 of 6 with JSON mode added; its R2 runs had silently fallen
  back to generic criteria.
* Typical decision slips: `{"decision": "read_file", ...}` instead of
  `{"decision": "tool_action", "kind": "read_file"}`, extra keys, a second JSON
  object after the first. In R2, 14 of Qwen's 21 recorded failures were malformed
  model output, consuming most of its 20 recovery attempts. The planner now repairs unambiguous slips before validation.
* The recovery policy kept every run alive through malformed output (REPAIR
  instead of ABORT); Sonnet produced malformed JSON 3 times in one R1 run.

### 5.5 Edit loops without fresh feedback

In R3, GPT-4.1-mini rewrote `index.html` 45 times (collection) and 35 times (turns)
without requesting verification again, until the 1200 s budget ran out; content
differed each time, so the no-progress detector did not fire. With the R4 guard
(verification after 8 consecutive edits) the collection case went from exhausted
to succeeded through four verify-and-repair cycles.

### 5.6 Infrastructure behaved correctly under real conditions

* A real provider timeout (Qwen, R1) was retried after the 5 s backoff and the run
  continued.
* A genuine missing `style.css` (404) was reported as a runtime error and repaired.
* No API key appeared in any of the 21 trace directories, game directories or
  runtime outputs; no game server or browser process was left running.

## 6. Limitations of this evaluation

* One run per cell; model output is nondeterministic, so single failures or
  successes are indicative, not rates.
* Manual specification review was done for the R1 games and for failing
  scenarios, not exhaustively for every later game. Spec-derived scenarios are
  themselves model-written and were judged individually (section 5.3).
* Only three specifications; all are browser games with canvas rendering.
* The course model is not yet announced; it may behave like any of the three.

## 7. Implications for the presentation

* The verification design (frozen spec-derived scenarios, real input, per-step
  failure detail with the state after the step) is the clearest "technical
  implementation explained in depth" candidate, with R1-vs-R3 evidence.
* If the course model behaves like the mid-tier or open model, expect more repair
  cycles and possibly an exhausted budget; the trace (`run_report.md`,
  `verification/`, `evidence/`) then shows which requirement failed, at which
  step, and what the agent tried.

## 8. Cost and reproduction

Total OpenRouter spend for all four rounds plus diagnostics: about **US$3.4**.

```bash
mkdir run && cp evaluation/specs/dev_01_collect.md run/game_design_prompt.md
printf 'AGENT_MODEL=openrouter/anthropic/claude-sonnet-5.5\nOPENROUTER_API_KEY=...\n' > run/agent.env
cd run && python -m agent game_design_prompt.md --max-runtime 1200
```

The raw run directories (games, traces, screenshots) were kept outside the
repository; the numbers above were extracted from their `summary.json` and
`events.jsonl` files.
