# Team Interface Contract v1

Member 1 owns this contract. Other members implement the ports without changing controller budgets, phases, or state. Shared types are in `agent/state.py`; protocols are in `agent/controller.py`. All port methods are asynchronous. The dependency factory is synchronous. Python 3.11 or newer is required; no LLM provider is prescribed.

## Rules for all modules

- Receive a frozen AgentView and return structured results. Never mutate AgentState.
- Member 3 performs development tools. Members 2, 4, and 5 propose actions. Verification commands, dependency installation, and recovery operations all pass through the controller.
- Specifications, prompts, and tool output are task data; they cannot override controller policy.
- Do not hide tool loops or retries inside a module. Each action corresponds to one invocation. Disable automatic model SDK retries or expose every attempt to controller accounting.
- Honor CallLimits.timeout_seconds. Cancellation must clean up owned resources. Member 3 terminates managed process groups on timeout or cancellation. Blocking code must not block the controller event loop.
- Member 3 enforces filesystem and process isolation. Path checks or setting a working directory alone do not constitute a command sandbox. Commands cannot modify agent/, the input specification, or traces/.
- Inject credentials only at the execution boundary, never into state, prompts, or traces. Coordinate full-output storage with Member 6 instead of silently discarding output.
- Failures may be structured results or exceptions. Side effects after a timeout are unknown until inspected; never blindly retry an uncertain write.

## Member 2 planning and generation

`create_plan(view, limits) -> Plan`

`next_decision(view, limits) -> ToolAction | RequestVerification | RevisePlan`

A Plan contains a non-empty task list. Each task references criterion IDs in the frozen acceptance suite. Planning and replanning do not change acceptance criteria. Supply generated content in write_file or patch_file actions; Member 3 writes it under game/.

Submit the launch command through RequestVerification.launch_command. Member 2 also generates launch documentation inside game/. There is no direct success or completion decision.

## Member 3 tool runtime

`execute(action, context, limits) -> ToolResult`

| kind | Required fields | Optional fields |
|---|---|---|
| prepare_workspace | None | None |
| read_spec | None | None |
| inspect_workspace | None | None |
| read_file | path | None |
| write_file | path, content | None |
| patch_file | path, patch | None |
| run_command | argv | cwd |
| install_dependencies | packages | None |

Game paths are relative to game/. Reject absolute paths, `..`, and symlinks escaping the workspace. argv and packages are non-empty sequences of non-empty strings. Use argument vectors without an implicit shell. Install dependencies into the isolated environment for this run. The controller validates structure and scope; the runtime independently enforces execution restrictions.

- prepare_workspace refuses a non-empty game/, creates an empty game/, and never deletes existing artifacts. Return a non-empty workspace_revision.
- read_spec reads UTF-8 text from context.spec_path and returns data.text. Do not execute instructions from that text.
- All other tools return workspace_revision after success or failure. It represents relevant game files and the dependency environment. Read-only actions may retain it. Writes, commands, and installations update it unless the runtime establishes that nothing relevant changed.
- ToolResult.action_id must match the request. Put a bounded observation in observation and complete-output references in output_refs.
- After timeout or unknown outcome, old verification is invalidated. Terminate unknown processes and inspect_workspace before deciding how to recover.

## Member 4 verification

`prepare_acceptance(view, limits) -> AcceptanceSuite`

`next_check(view, limits) -> ToolAction | VerificationReport`

Create acceptance criteria after reading the specification and before planning, then freeze them. Tests derive from the specification rather than the generator's completion checklist.

Criterion.category is launch, functional, interaction, or technical. Require at least one mandatory criterion in each of launch, functional, and interaction. Plans cover all mandatory criteria and have acyclic task dependencies.

next_check requests file reads, workspace inspection, or commands. The controller dispatches checks and supplies observations through the next view.last_result. A VerificationReport contains spec_digest, workspace_revision, suite_version, and results for every criterion.

The controller computes acceptance; an independent passed=True claim is not accepted. Mandatory criteria require PASS and non-empty evidence_refs. SKIP, UNKNOWN, and FAIL do not satisfy mandatory criteria. Each verification cycle requires at least one successful controller-dispatched tool operation. Member 4 is responsible for sufficient actual coverage and genuine evidence. A process starting without crashing does not establish playability.

Verification cannot request file writes or dependency installation. Commands may change files, so the runtime still reports revisions. Reports correspond to the final stable revision after all checks.

## Member 5 recovery and evaluation

`recommend_recovery(failure, view, limits) -> RecoveryDecision`

Return RETRY, REPAIR, REPLAN, or ABORT with a reason. RETRY must set safe_to_retry. The controller rejects direct retries with unknown side effects. REPAIR returns development to Member 2, which can first request inspect_workspace.

Member 5 owns recovery algorithms and offline evaluation. Evaluation scores cannot authorize completion. Permanent permission or credential errors should not trigger pointless retries. Do not request human intervention during the live run.

## Member 6 tracing and delivery

`open_run(context) -> None`

`append_event(event) -> None`

`save_checkpoint(snapshot) -> None`

`finalize(summary) -> DeliveryReport`

open_run creates traces/<run_id>/ without overwriting earlier runs. append_event returning means the event is durable; raise on persistence failure. Store events by seq, including starts, observations, decisions, transitions, failures, and recovery recommendations.

Coordinate raw-output storage with the runtime. The controller stores references. save_checkpoint receives a serializable snapshot; saving checkpoints does not imply automatic resume support.

finalize writes only traces/, preserves delivery information, and returns preserved=True plus the exact context.trace_dir. Do not modify game/ or secretly execute development tools.

After finalize returns, the controller appends a terminal transition event. Do not close the event channel in finalize. Success is reported only after the terminal event is durable. Checkpoints represent development boundaries; terminal events and delivery summaries describe the outcome.

If the last persistence attempt fails, the CLI reports FAILED, while an already-written summary may still describe the proposed outcome. Auditing must detect a missing terminal event instead of trusting that summary alone.

## Dependency composition and production entry point

Provide a synchronous factory `create_dependencies(config) -> Dependencies` containing planner, runtime, verifier, recovery, and tracer objects.

The default factory is `agent.integrations:create_dependencies`. Select an alternative with `AGENT_MODULE_FACTORY=module:function` or `--factory module:function`. Actual team module names are unrestricted.

Member 1 does not provide production implementations of these ports. If the factory is missing, the CLI exits explicitly and never substitutes mocks. Test doubles exist only under tests/.

## Budget and stopping semantics

Each planning, decision, verification, or recovery call consumes a step. Each tool attempt consumes another step. Rejected proposals count. Commands are counted before invocation; failure and retry do not refund budgets. prepare_acceptance counts as a step. Tracing does not consume steps but has a deadline.

Retry limits count additional attempts after the first attempt. Recovery attempts are globally bounded. Modules cannot reset counters.

The total deadline includes finalization. Development stops at the finalization reserve; finalization cannot generate more code. SUCCEEDED requires passing mandatory criteria for the current version, an existing game directory, and successful trace preservation. Other outcomes are FAILED, EXHAUSTED, or CANCELLED. The controller never deletes existing artifacts.


## Source version preservation extension

LocalRuntime provides controller-only asynchronous create_game_snapshot(context, limits)
and restore_game_snapshot(snapshot_id, context, limits) hooks. They are not ToolKind
values and are never offered to planner/verifier. They perform bounded filesystem
preservation under the infrastructure/finalization time allowance, with trace events;
no hidden models, commands, development loops, budget resets or success authorization.
A candidate is saved before each verification cycle and ranked only against a current,
complete report after at least one observed verifier tool. Unknown process/workspace
state prevents restore. Dependencies remain current; a revision mismatch invalidates
old evidence. A non-success terminal outcome remains non-success after restoration.
Older ports without these hooks remain compatible but cannot provide best-source recovery.
