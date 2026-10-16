# Member 3 Process Cleanup Fix Report

Date: 2026-10-13, Asia/Singapore.
Status: verified on the integration branch; main integration awaits pull request review.

## Changed behavior

Normal exit, nonzero exit, timeout, and cancellation now all run bounded cleanup of the POSIX process group created for the command. Cleanup targets the group even if its initial leader has already exited. TERM is followed by a bounded wait and KILL escalation when necessary. Parent exit alone no longer confirms cleanup. Each signal stage waits at most approximately 0.75 seconds plus polling/event-loop scheduling. An exit-callback race is handled with a bounded leader wait.

Repeated task cancellation is deferred until the retained cleanup task finishes. Cancellation is then re-raised, preserving the controller's cancellation semantics. Output streams remain open until cleanup finishes.

If cleanup cannot be confirmed, RUN_COMMAND and INSTALL_DEPENDENCIES return UNKNOWN, PROCESS_CLEANUP_FAILED, side_effects_unknown=True, and no trusted workspace revision. An uncertain virtual-environment setup does not proceed to pip; uncertain installation is not recorded as successful. Observations explicitly describe cleanup uncertainty.

## Files changed

- agent/tools/processes.py: bounded group cleanup on all completion paths.
- agent/tools/runtime.py: gate command/dependency results on confirmed cleanup.
- agent/tools/dependencies.py: stop after uncertain setup and record unknown installation state.
- agent/tools/commands.py: include cleanup uncertainty in observations.
- tests/test_runtime_process_cleanup.py: seven tests for lifecycle and result behavior.
- README.md and review records: updated local status.

Member 1's __main__.py, controller.py, state.py, and config.py are unchanged. Public tool/action/result schemas and strict sandbox defaults are unchanged. The original DAI MINFEI_Member3 staging directory is historical source; the fixed implementation is in root agent/tools. The staging directory is retained for provenance; root agent/tools is the integrated implementation.

## Verification

Full discovery: 65 tests passed, no failures, errors, or skips on the review Mac. The original 58 tests remain passing. Seven cleanup tests cover normal exit, nonzero exit, TERM-resistant child timeout, repeated cancellation, strict macOS sandbox execution, command/setup cleanup-failure gating, and dependency installation state after cleanup failure.

The strict macOS test uses RuntimeSettings(strict_os_sandbox=True) and checks both normal exit and timeout. Functional tests also use real processes in their unsandboxed fixture settings. Uncertain cleanup results and installation behavior are tested with injected process results; the npm result-gating test is mocked and is not an actual npm installation test.

Commands:

```bash
python -m unittest tests.test_runtime_process_cleanup -v
python -m unittest discover -v
```

See INTEGRATION_TEST_OUTPUT.txt for complete final test output. A wheel built from the fixed project includes all eight agent.tools modules.

## Remaining limits

This confirms cleanup of the original POSIX group, not containment of a process that detaches into another session. Stronger process-tree/container lifecycle enforcement is still required for that threat. Group existence checks are conservative; permission failures or a group remaining visible at the deadline yield unconfirmed cleanup. Linux behavior, including zombie reaping, must be rehearsed separately; this run validated macOS. No strict Windows process-tree adapter exists, and non-POSIX cleanup cannot claim confirmed tree cleanup.

Bounded cleanup adds a small grace interval after command execution. asyncio timeouts wait for cooperative cancellation cleanup, so timeout reporting is not an instantaneous hard stop. Other production ports and the full live game-generation pipeline still need integration.

## Integration

This branch integrates the runtime and tests into the root package, enables agent.tools package discovery, and ignores .agent-runtime/. The original Member 3 staging folder is retained. The team factory can import LocalRuntime from agent.tools and assign it to Dependencies.runtime. Other production ports and the factory are still pending; this change does not silently use mocks at the required entry point.
