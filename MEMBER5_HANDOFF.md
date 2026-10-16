# Member 5 Recovery and Evaluation

Implemented against main commit b2aa330 and Team Interface Contract v1.
Integration branch: member5-recovery-evaluation.
Based on main 1658dc3; shared contracts are unchanged from b2aa330.

## Delivered files

- agent/recovery/: stateless asynchronous RecoveryPort implementation and classification.
- evaluation/report.py: read-only offline metrics from summary.json and events.jsonl.
- evaluation/specs/: development and reserved test specifications; no game code.
- tests/test_recovery.py: classification/policy and controller integration tests.
- tests/test_evaluation.py: incomplete/corrupt record handling and metric correctness.

No shared controller, state, budget, tool, trace, interface, or packaging files were modified.

## Integration

```python
from agent.recovery import RuleBasedRecovery

# Member 1 supplies the other real ports in its synchronous dependency factory.
recovery = RuleBasedRecovery()
# Dependencies(planner=..., runtime=..., verifier=..., recovery=recovery, tracer=...)
```

No constructor arguments or third-party dependencies are required. Ensure packaging
includes agent.recovery and evaluation. The current main pyproject.toml explicitly
packages only agent. Member 1 should coordinate package discovery with Members 3/6;
for example, replace the explicit packages list with discovery for agent* and
evaluation*. Running from a checkout does not prove an installed wheel includes them.

## Policy

| Failure | Decision |
|---|---|
| Process cleanup failed | ABORT; no legal cleanup/restart tool exists in v1 |
| Sandbox unavailable, unsafe runtime storage, invalid input/environment | ABORT; never relax isolation |
| Authentication or permission failure | ABORT |
| Command/dependency timeout, runtime exception, uncertain side effects | REPAIR with inspect_workspace first; never direct RETRY |
| Invalid planner plan | REPLAN against frozen criteria |
| Invalid development tool proposal | REPAIR with corrected proposal |
| Known execution failure such as NONZERO_EXIT | REPAIR; inspect output_refs and correct the cause |
| Failed/stale/incomplete acceptance | REPAIR and fresh verification |
| Recognized transient proposal-only module exception | RETRY with safe_to_retry=True |
| Invalid verifier contract/proposal or unclassified failure | ABORT |

Initialization only permits safe retry or abort. Temporary acceptance preparation
exceptions may retry; uncertain initialization tools abort. Exception prefixes
TimeoutError, ConnectionError and RateLimitError are recognized for module calls.
Unrecognized SDK exception types fail conservatively until Member 2 provides a
structured mapping. Arbitrary tool output cannot change the policy.

Controller owns retry counts, global recovery attempts, command/step/deadline
budgets and finalization reserve. Recovery contains no loops, waits, tools or writes.
CallLimits.timeout_seconds is this invocation's allowance, NOT total remaining
development time. v1 does not expose per-operation retries or remaining total time
in AgentView; this module does not invent those values or reset any counters.

## Required team followups

1. After uncertain execution, Member 2 must propose inspect_workspace before any
   further write/command/install. Recommendation reasons are traced but are NOT
   exposed to the planner in AgentView; planner must react to last_failure and
   workspace_revision=None. Member 1 should enforce inspection in the controller
   or provide an explicit typed handoff. v1 does not currently enforce this order.
2. PROCESS_CLEANUP_FAILED aborts conservatively. Member 3 must fix its reported
   process-cleanup test failures and rehearse strict sandboxing on the final machine.
3. No rollback/checkpoints.py was added. RecoveryKind has no ROLLBACK and runtime
   has no restore tool. Workspace hashes and trace state checkpoints are not game
   snapshots. A future rollback extension needs a Member 1-owned contract change.
4. Main still stores Member 3 under DAI MINFEI_Member3/. Member 6 is on a separate
   remote branch. These implementations were inspected, not merged into this work.
5. Main includes an agent/member2-planner reference, but the root production
   dependency factory and Member 4 implementation are not present. This work does
   not validate that planner reference. Tests here are not live game generation.

## Tests

```bash
python -m unittest tests.test_recovery tests.test_evaluation -v
python -m unittest discover -v
```

Controller integration uses the existing Scenario test double. It exercises the
real controller and this recovery port, but does not execute generated games, call
an LLM or test OS process cleanup. The uncertainty integration fixture explicitly
proposes inspection; it does not prove the controller forces that order.

## Offline evaluation

```bash
python -m evaluation.report traces/RUN_A traces/RUN_B > experiment_metrics.json
```

The analyzer is read-only and returns exit code 1 for incomplete records (not simply
for failed games). It counts ALL supplied runs, including corrupt/missing records.
Recorded terminal success is a controller outcome, not independent proof of game
playability, evidence-file validity, or individual repair effectiveness. Member 6's
delivery audit remains the delivery-specific tool. Do not compare fake test runs
with production runs in one cohort.

For comparable experiments keep model, controller budgets, machine, Agent version
and test conditions fixed. Start each run in a separate clean run root, preserve all
outputs and record seed/repetition, spec ID, cohort, commit, model/API and human
interventions separately. Record manual gameplay checks using Member 4's criteria.
The report currently counts elapsed time, steps, commands, recovery recommendations
and failure categories; token cost, first playable time and verified recovery rate
need additional agreed trace fields and are intentionally not fabricated.

Use development specs for tuning. Keep reserved specs out of tuning; once read or
used to adjust prompts, replace them with new unseen tests before claiming a blind
evaluation. A recommended first experiment compares abort-on-failure with this
policy using otherwise identical modules. Never prewrite games for these specs.

Latest Member 5 verifier integration findings: [MEMBER5_INTEGRATION_UPDATE.md](MEMBER5_INTEGRATION_UPDATE.md).
