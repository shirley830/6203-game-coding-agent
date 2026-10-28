# Live model tests (OpenRouter, 2026-10-16)

Four rounds, 21 autonomous runs (plus nine runs with the course model GLM-5.3, section 5c) of `python -m agent game_design_prompt.md` against
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

## 5b. Additional run: non-browser specification

After adding process-mode verification (non-browser games), Sonnet ran the new
`evaluation/specs/dev_05_terminal.md` (terminal hangman, standard library only):

| Outcome | Time | Steps | Recoveries | Launch command | Verification |
|---|---|---|---|---|---|
| succeeded | 77 s | 30 | 0 | `python3 main.py` | launch, functional, interaction pass in process mode (typed moves produced 1403 new output characters) |

The agent wrote its own `test_hangman.py`, ran it with `python3 -m unittest`
before requesting verification, and documented the launch command. Playing the
generated game by hand confirmed correct guesses, repeated guesses without a
life lost, and wrong guesses costing a life. Spec-derived scenarios are not
used for non-browser games, so rule-level checks are weaker than for browser
games.

## 5c. Course model: GLM-5.3 (2026-10-18)

The course confirmed GLM-5.3 (`openrouter/z-ai/glm-5.3`) as the evaluation model.

**Probe before any run.** With no reasoning setting, one acceptance call spent all
16 000 output tokens on reasoning (`finish_reason=length`, 81.5 s) and returned **no
text**: every planning call would have failed. Reasoning cannot be disabled on
OpenRouter for this model ("reasoning is mandatory"), but with
`reasoning.max_tokens` set the same call answered in about 5 s with valid JSON.
Commit `dfb0ae0` caps reasoning at 8 000 tokens for GLM models
(`AGENT_REASONING_MAX_TOKENS`) and raises the output limit to 32 000.

| Round | Case | Outcome | Time | Recoveries | Verification cycles | Spec review / notes |
|---|---|---|---|---|---|---|
| 5a | Collection | ✅ succeeded | 39 s | 0 | 12/12 | Correct: spawns 60 px away, win at 10 freezes input, restart resets score and position |
| 5a | Dodge | ✅ succeeded | 453 s | 5 | 2 → 11 → 10 → 11 → 12/12 | Correct; the contract reported `paused` as `playing` (state not reported faithfully) |
| 5a | Memory (mouse only) | ✅ succeeded | 1151 s | 8 | 8 cycles, final 13/13 | Correct and mouse only; 196 of 200 steps; added a 450 ms settle lock to absorb timing |
| 5a | Maze | ❌ exhausted (recoveries) | 265 s | 20 | best 9/11 | Unpassable contract (function field, see below) + 11 empty `{}` replies |
| 5a | Terminal hangman | ✅ succeeded | 17 s | 0 | 3/3 (process mode) | Correct, played by hand |
| 5a | Switches (mouse only) | ✅ succeeded | 115 s | 1 | 6 → 13/13 | Rewrote its DOM board as a canvas 8 times only because `click_cell` needed a canvas |
| 5a | Turn-based | ✅ succeeded | 433 s | 1 | 10 → 13/13 | First cycle caught "walking into the enemy does not lose"; fixed in one repair |
| 5b | Maze (rerun) | ✅ succeeded | 235 s | 6 | 11 → 11 → 11 → 13/13 | Empty replies absorbed by the planner instead of recovery |
| 5b | Switches (rerun) | ✅ succeeded | 202 s | 1 | 10 → 13/13 | |

**GLM-5.3 succeeded in 8 of 9 runs.** Five of the successes (collection, dodge,
memory, terminal, turn-based) were also reviewed against the specification by
hand; the switches runs and the maze rerun rely on their passing spec-derived
scenarios. Its games and tests were close to Claude Sonnet 5.5 in
quality, far better than GPT-4.1-mini and Qwen3-Coder; it needs more repair cycles
on timing-heavy rules (memory: 19 min of the 20-min budget).

Issues found and fixed (commit `3758484` and the following commit):

1. **Function fields in the state contract.** The maze contract required
   `isWall(x, y)`. The harness checked fields on a JSON copy, which drops
   functions, so the contract could never pass. Fields are now checked on the live
   object.
2. **Empty decisions.** GLM-5.3 sometimes replied `{}` (2 output tokens); 11 such
   replies spent the maze run's recovery budget in 4.5 minutes. The planner now
   absorbs up to two consecutive empty or unrecognised replies by refreshing the
   workspace with a note, then normal recovery applies.
3. **DOM boards.** `click_cell` now also clicks DOM boards (data-* cells or a
   container of equal cells), and the generic checks accept a DOM board and words
   such as moves, turns, lives or status as gameplay signals. When every
   spec-derived functional scenario passes and the page has no JavaScript errors,
   the generic functional check is satisfied.

Cost: about **US$1.7** for the probes and all nine GLM-5.3 runs (OpenRouter
account usage); single runs ranged from about $0.01 to $0.41.

## 5d. Final GLM-5.3 rehearsal (2026-10-19)

After the model probe and best-version restore were added (PR #7), Member 5 ran all seven specifications once more
with GLM-5.3 on OpenRouter (Member 5's own key; the evaluation key is issued only before the presentation), a 20-minute budget and no human intervention.

| Spec | Outcome | Time | Steps | Recoveries |
|---|---|---|---|---|
| dev_01_collect | succeeded | 48 s | 20 | 0 |
| dev_02_dodge | exhausted | 1180 s | 166 | 8 |
| dev_03_memory | succeeded | 45 s | 22 | 0 |
| dev_04_maze | succeeded | 334 s | 128 | 5 |
| dev_05_terminal | succeeded | 23 s | 18 | 0 |
| holdout_01_switches | succeeded | 61 s | 41 | 1 |
| holdout_02_turns | succeeded | 106 s | 30 | 0 |

The dodge run failed timing assertions in model-written scenarios (exact score values while the real-time loop keeps
running). At the deadline the controller restored the best assessed version (11 mandatory criteria passing) and the run
was still reported as exhausted. The batch cost about US$0.5. Raw data: `GLM_REPEAT_20261019.json`,
`BEST_VERSION_RESTORE_20261019.json`; checklist: `HANDOFF_20261019.md`. These seven runs used a newer agent than the 32
runs above, so they are reported separately rather than merged into one success rate.

## 5e. Course model Kimi K3 (2026-10-26)

In October 2026 the course switched to Kimi K3 (`openrouter/moonshotai/kimi-k3`). The probe returned valid JSON in about
one second. Kimi K3 bills reasoning as output at US$20 per million tokens, so it gets the default 8000-token reasoning
cap. Three specifications were rehearsed with the presentation limit (`AGENT_MAX_RUNTIME=420`, 400 s of work), using a
team member's key, with the agent changed between rounds:

| Round | Change before the round | Minesweeper | Grid snake | Turn-based keys | Cost |
|---|---|---|---|---|---|
| 1 | Model switch only (`43e3e4a`) | succeeded, 368 s | exhausted, 11/13 | exhausted, 13/15 | $1.70 |
| 2 | Repairs patch changed lines (`4d93734`) | exhausted, 13/14 | succeeded, 331 s | succeeded, 77 s | $0.65 |
| 3 | Current file texts in decisions; step reasoning cap 3000 (`4e8fdf0`) | succeeded, 165 s | succeeded, 299 s | succeeded, 338 s | $0.64 |

Findings from the traces:

- Waiting for the model was 330-370 s of each 400 s run in round 1. Single calls rewrote whole files (10 000-12 000
  output tokens, about 100 s each).
- After a failed check the model re-read files one at a time because it only saw the last file it read; one minesweeper
  repair spent about 200 s on four `read_file` decisions, each preceded by 47-81 s of reasoning.
- Round 3 removed both: no file was read again, each run used 3 file writes and 1-3 patches, and model time fell to
  141-290 s. Grid snake (real-time) had never passed within 7 minutes before round 2.
- Model speed varies between runs (minesweeper: 368 s, exhausted, 165 s), so these are the effects of each change, not
  a success rate. Minesweeper and grid snake are new specifications written for these rehearsals.

## 5f. Kimi K3 retest of the shared acceptance fixes (2026-10-27)

Member 5 retested four specifications of their own on the demo machine (source `a924db5`). The problems were in the
tests, not the games: simple memory cards lost every scenario to the mouse filter (succeeded on generic checks with
0 specific rules), a minesweeper check made the first click win and still expected "playing" (the game capped its
flood reveal to satisfy it), and a whack-a-mole check allowed a missed mole in the last second to go uncounted.

**Morning retest** (`2f4c472`: Member 4 pointer filter and contradictory-pair check, Member 5 structured
`test_script_error`, result levels): detailed memory 9/9, whack-a-mole 10/10, simple memory 8/8 fully accepted;
minesweeper exhausted at 8/9 on the same flood-reveal contradiction.

**Afternoon retest** (`fc42a0e`, last five runs on `6809e80`): disputed checks with one bounded re-derivation, the
tester rule that a setup must not end the game early, and provider-reported cost. 7-minute limit, fresh folders:

| Specification | Run | Result | Specific rules | Time | Recoveries | Disputed check | Cost |
|---|---|---|---|---|---|---|---|
| Detailed memory cards | 1 | fully accepted | 9/9 | 213 s | 2 | REQ-R7 replaced (clicked row 3 of a 3-row grid) | $0.19 |
| Detailed memory cards | 2 | fully accepted | 7/7 | 304 s | 2 | REQ-R6 replaced (same coordinate error) | $0.34 |
| Minesweeper | 1 | exhausted | 7/10 | 400 s | 1 | none; one decision took 204 s | $0.48 |
| Minesweeper | 2 | fully accepted | 9/9 | 280 s | 3 | REQ-R3 replaced (setup flood-revealed the board and won) | $0.36 |
| Detailed whack-a-mole | 1 | fully accepted | 10/10 | 230 s | 1 | none | $0.28 |
| Detailed whack-a-mole | 2 | fully accepted | 10/10 | 262 s | 2 | REQ-R3 replaced (expected no mole after the 300 ms respawn) | $0.21 |
| Simple memory cards | 1 | fully accepted | 9/9 | 87 s | 0 | none | $0.22 |
| Simple memory cards | 2 | fully accepted | 5/5 | 246 s | 3 | REQ-R1 kept (the game did not reveal the clicked card; fixed) | $0.55 |
| Lights Out (unseen) | 1 | fully accepted | 8/8 | 102 s | 0 | none | $0.17 |
| Tic-tac-toe vs computer (unseen) | 1 | fully accepted | 9/9 | 59 s | 0 | none | $0.09 |

- 9 of 10 fully accepted; 218 s on average; $2.89 in total.
- Every dispute was reviewed by hand. The four replacements keep the rule and its expectations and change only an
  impossible coordinate, setup or timing; the one *keep* was correct.
- The generated games use a standard flood reveal and count a missed mole in the last second.
- Four more runs, and the end of the first whack-a-mole run 2, stopped at once on HTTP 402 (the key's spending limit).
  They are excluded and were re-run with a new key; the agent now stops such runs with an "out of credit" reason.

## 5g. Speed check (2026-10-27, `dd39246`)

The minesweeper failure above spent 204 s of 400 s on one decision (23 469 output tokens, far beyond the 3000-token
reasoning cap) whose only result was to request the first verification. Changes: the planner requests the first
verification itself once README.md with the launch command is written and every file the page loads exists, and every
decision prompt shows the remaining work time (with under 150 s left: smallest patch only).

| Specification | Result | Specific rules | Time | Recoveries | Disputed check | Cost |
|---|---|---|---|---|---|---|
| Detailed memory cards | fully accepted | 9/9 | 76 s | 0 | none | $0.08 |
| Minesweeper | fully accepted | 8/8 | 94 s | 0 | none | $0.09 |
| Minesweeper | fully accepted | 7/7 | 360 s | 6 | REQ-R1, REQ-R2 replaced (setups flood-revealed the board and won) | $0.32 |
| Detailed whack-a-mole | fully accepted | 9/9 | 133 s | 1 | none | $0.12 |

- 4 of 4 fully accepted; $0.61 in total. Every first verification started without a model call; apart from planning
  in the slow minesweeper run (111 s), no model call took more than 60 s.
- Both replacements keep the expectations and change only the mine layout (reviewed by hand). The tester still writes
  flood-winning setups despite the prompt rule; the bounded re-derivation is what keeps these runs passing.

## 5h. Real-time check (2026-10-27, `a3fc8fb`)

Real-time games keep moving or counting while a check runs, which made them the weakest case in earlier rounds.
Same agent, Kimi K3, 7-minute limit, fresh folders.

| Specification | Result | Specific rules | Time | Recoveries | Disputed check | Cost |
|---|---|---|---|---|---|---|
| Grid snake | fully accepted | 9/9 | 179 s | 3 | REQ-R3 replaced (snake moved during the setup, saved length out of date) | $0.17 |
| Dodge (`dev_02_dodge`) | fully accepted | 7/7 | 188 s | 5 | REQ-R4 replaced (new obstacles changed which one is last) | $0.17 |
| Collection (`dev_01_collect`) | fully accepted | 9/9 | 82 s | 0 | none | $0.07 |

- 3 of 3 fully accepted; $0.42 in total. Every first verification started without a model call; no model call took
  more than 60 s.
- Reviewed by hand: the snake replacement keeps the expectations (score 1, length + 1) and only saves the length after
  the setup. The dodge replacement still requires obstacles to fall, but checks that some obstacle passes the earlier
  lowest position instead of tracking the last one, which is slightly looser.

## 5i. Checks of the latest changes (2026-10-28)

Three changes, each checked with Kimi K3, 7-minute limit, fresh folders: Member 2's spec-priority prompt rules
(`847b8c2`), same-state checks re-derived together (`50125bc`), and right-click steps (`b6b8bce`).

| Agent (main) | Specification | Result | Specific rules | Time | Recoveries | Disputed checks | Cost |
|---|---|---|---|---|---|---|---|
| 847b8c2: specification outranks tests (Member 2) | Minesweeper | exhausted | 6/8 | 400 s | 11 | REQ-R1, REQ-R3 replaced; REQ-R4, REQ-R6 used the same winning layout but no dispute was left | $0.39 |
|  | Detailed whack-a-mole | fully accepted | 10/10 | 125 s | 1 | none | $0.11 |
|  | Grid snake | fully accepted | 9/9 | 316 s | 6 | REQ-R3 replaced | $0.26 |
|  | Dodge | fully accepted | 7/7 | 99 s | 0 | none | $0.10 |
| 50125bc: same-state checks re-derived together | Minesweeper | exhausted | 8/9 | 400 s | 5 | REQ-R3 replaced, but the flag check could only left-click | $0.33 |
|  | Minesweeper | fully accepted | 8/8 | 95 s | 0 | none | $0.11 |
|  | Detailed whack-a-mole | fully accepted | 9/9 | 90 s | 0 | none | $0.09 |
| b6b8bce: right-click steps | Minesweeper | exhausted | 8/10 | 400 s | 3 | REQ-R1, R4, R6, R7 replaced in one group; the new REQ-R4 placed 11 mines | $0.18 |
|  | Minesweeper | fully accepted | 10/10 | 193 s | 2 | REQ-R1 and REQ-R4 replaced together | $0.14 |
|  | Minesweeper | failed | 5/9 | 400 s | 2 | REQ-R2, R5, R7 replaced; the new REQ-R5 again wins on the first click | $0.27 |

- 6 of 10 fully accepted; $1.97 in total. All four non-minesweeper runs passed; minesweeper passed 2 of 6.
- Member 2's rules caused no failure: in every minesweeper run the agent kept the game faithful and disputed the checks.
- Run 1: four checks shared one mine layout that a correct flood reveal wins at once; after two disputes the planner
  raised five rejected disputes. Fix: failed checks with an identical state after the failing step are re-derived
  together (at most 2 disputes and 4 checks per run); the prompt shows the disputes left and, at zero, forbids more.
- Round 2, minesweeper 1: the flag check used `click_cell` (a left click) and the harness had no right-click, so no
  replacement could express it. Fix: `"button": "right"` on click / click_at / click_cell.
- Still open: the tester writes winning layouts in almost every minesweeper scenario, and the same model writes the
  replacements; 2 of 9 replacements in round 3 were wrong again (11 mines; another first-click win), and four
  re-derivations in a row take 60-90 s.

## 5j. Demo rehearsal (2026-10-28, `0ee43dd`)

The clean demo checkout was updated to the final `main` (run time defaults to 420 s). Self-check: ready (sandbox,
Chromium, port 8000); test suite: 324 passed; `python -m agent.preflight --probe-model`: usable JSON from Kimi K3.
Then two held-out specifications, run as on presentation day: a fresh folder with only `game_design_prompt.md` and
`agent.env`, then `python -m agent game_design_prompt.md`.

| Specification | Result | Specific rules | Time | Recoveries | Disputed check | Cost |
|---|---|---|---|---|---|---|
| `holdout_01_switches` (light switches) | fully accepted | 8/8 | 82 s | 0 | none | $0.17 |
| `holdout_02_turns` (turn-based keys) | fully accepted | 9/9 | 93 s | 0 | none | $0.19 |

- 2 of 2 fully accepted, $0.36 in total; the console announced the 7-minute stop time at the start of each run.
- If the presentation uses a different computer, repeat this rehearsal there.

## 6. Limitations of this evaluation

* One run per cell; model output is nondeterministic, so single failures or
  successes are indicative, not rates.
* Manual specification review was done for the R1 games and for failing
  scenarios, not exhaustively for every later game. Spec-derived scenarios are
  themselves model-written and were judged individually (section 5.3).
* Only three specifications; all are browser games with canvas rendering.
* GLM-5.3 was run once per specification (plus two reruns after fixes); the evaluation key and network on presentation day may differ.

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
