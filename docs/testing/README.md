# Testing overview

Current status (2026-10-26): **298 tests (plus 58 subtests), all passing** on macOS / Python 3.11
(`python -m pytest -q`, about 80 s). Browser tests are skipped automatically
when Playwright Chromium is not installed.

## Test suite by area

| File | Tests | Owner area | What it covers |
|---|---|---|---|
| `test_controller.py` | 36 | Member 1 | State machine, budgets, stale-evidence rejection, safe retries, cancellation, delivery failure (injected fakes) |
| `test_config_cli.py` | 7 | Member 1 | Configuration validation and the `python -m agent` entry point |
| `test_planner.py` | 9 | Member 2 | Plan coverage of mandatory criteria, decisions, launch-command validation |
| `test_runtime.py` | 16 | Member 3 | Real file/command/dependency tools, path and shell restrictions |
| `test_runtime_process_cleanup.py` | 7 | Member 3 | Process-group cleanup on success, timeout and cancellation, strict macOS sandbox |
| `test_verifier_member4.py` | 8 | Member 4 | Generic acceptance suite and the verification cycle |
| `test_spec_acceptance.py` | 18 | Member 4 | Spec-derived contract and scenarios: parsing and dropping invalid requirements, real Playwright runs of passing/buggy/mouse-only fixture games |
| `test_acceptance_reliability.py` | 6 | Member 4 | Broken test scripts dropped before freezing, incomplete acceptance reported without blocking |
| `test_recovery.py` | 27 | Member 5 | Error classification and recovery decisions (no blind retry after timeouts or unknown side effects) |
| `test_recovery_verifier.py` | 7 | Member 5 | Recovery decisions for verifier-originated failures |
| `test_evaluation.py` | 12 | Member 5 | Offline run-record evaluation |
| `test_recovery_progress.py` | 19 | Member 5 | Same failure on an unchanged workspace: replan, then stop |
| `test_recovery_signals.py` | 10 | Member 5 | Verifier-fault signals (test-script errors, incomplete acceptance) abort instead of repairing |
| `test_tracing.py` | 27 | Member 6 | Durable events, checkpoints, screenshot archive and audit |
| `test_trace_usage.py` | 2 | Member 6 | Model-call accounting and the run report |
| `test_integration_tasks.py` | 17 | Integration | Production factory, cross-member checklist, sandbox write limits, offline end-to-end rehearsal |
| `test_p0_hardening.py` | 12 | Integration | Retry backoff, startup self-check, `agent.env`, credential isolation |
| `test_no_game_templates.py` | 3 | Integration | The agent ships no game code and never reads test fixtures |
| `test_reasoning_config.py` | 5 | Integration | Reasoning caps for GLM and Kimi models (lower cap for step decisions), request parameters |
| `test_non_browser.py` | 7 | Integration | Process-mode verification of terminal, crashing and pygame-style games; run time from `agent.env` |
| `test_handoff_completion.py` | 19 | Member 5 | Model probe in the self-check, source snapshots and best-version restore, rehearsal archiving |

## End-to-end evidence

* **Offline rehearsal** (`tests/rehearsal_factory.py`, run by
  `test_integration_tasks.py`): real runtime, sandbox, Playwright verifier,
  recovery and tracing with a scripted model; a broken game fails verification,
  is repaired, and succeeds.
* **Live model runs**: 48 runs with five real models, including the course models Kimi K3 and (earlier) GLM-5.3, see
  [LIVE_MODEL_TESTS.md](LIVE_MODEL_TESTS.md).

## Fixtures

Hand-written pages used by the tests live in `tests/fixtures/games/` and are
described in `tests/fixtures/README.md`. They are test inputs, not templates.

## Historical records

Earlier per-member records are kept for the report and are not current counts:

* [MEMBER1_TEST_REPORT.md](MEMBER1_TEST_REPORT.md): controller, 43 tests (2026-10-11)
* [MEMBER3_INTEGRATION_TEST_OUTPUT.txt](MEMBER3_INTEGRATION_TEST_OUTPUT.txt): runtime integration, 65 tests
* `docs/members/*/TEST_REPORT.md`: each member's module tests before integration
