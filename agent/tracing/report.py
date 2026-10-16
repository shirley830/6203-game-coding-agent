"""Render traces/<run_id>/run_report.md from persisted events (Member 6).

Read-only: works from the already-durable events.jsonl so the report can never
claim something the event log does not contain.
"""

from __future__ import annotations

from typing import Any


def _short(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cell(value: Any, limit: int = 160) -> str:
    return _short(value, limit).replace("|", "\\|")


def _describe_action(action: dict[str, Any]) -> str:
    args = action.get("arguments") or {}
    kind = action.get("kind", "?")
    if "path" in args:
        return f"{kind} `{args['path']}`"
    if "argv" in args:
        return f"{kind} `{' '.join(args['argv'])}`"
    if "packages" in args:
        return f"{kind} {', '.join(args['packages'])}"
    return str(kind)


def render_run_report(events: list[dict[str, Any]], summary: dict[str, Any],
                      usage: list[dict[str, Any]] | None = None) -> str:
    lines = [
        f"# Run report `{summary.get('run_id')}`",
        "",
        f"- **Outcome:** {summary.get('outcome')} — {summary.get('reason')}",
        f"- **Launch command:** `{' '.join(summary.get('launch_command') or []) or '(none)'}`",
        f"- **Budget used:** {summary.get('steps_used')} steps, {summary.get('commands_used')} commands, "
        f"{summary.get('recovery_attempts')} recovery attempts, {summary.get('elapsed_seconds', 0):.1f}s",
        "",
    ]

    criteria: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    reports: list[tuple[int, dict[str, Any]]] = []
    for event in events:
        payload = event.get("payload") or {}
        if event.get("kind") != "module_result":
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            continue
        if payload.get("operation") == "verifier.prepare_acceptance":
            criteria = result.get("criteria") or []
        elif payload.get("operation") == "planner.create_plan":
            plans.append(result)
        elif payload.get("operation") == "verifier.next_check" and "results" in result:
            reports.append((event.get("seq", 0), result))

    if criteria:
        lines += ["## Acceptance criteria (frozen before planning)", "",
                  "| ID | Category | Mandatory | Description |", "|---|---|---|---|"]
        lines += [f"| {c.get('criterion_id')} | {c.get('category')} | {c.get('mandatory')} | "
                  f"{_cell(c.get('description'))} |" for c in criteria]
        lines.append("")

    for number, plan in enumerate(plans, 1):
        lines += [f"## Plan v{number} `{plan.get('plan_id')}`", "",
                  "| Task | Criteria | Depends on | Description |", "|---|---|---|---|"]
        lines += [f"| {t.get('task_id')} | {', '.join(t.get('requirement_ids') or [])} | "
                  f"{', '.join(t.get('depends_on') or []) or '-'} | {_cell(t.get('description'))} |"
                  for t in plan.get("tasks") or []]
        lines.append("")

    if reports:
        lines += ["## Verification cycles", "",
                  "| Event | Criterion | Status | Detail |", "|---|---|---|---|"]
        for seq, report in reports:
            for row in report.get("results") or []:
                lines.append(f"| {seq} | {row.get('criterion_id')} | {row.get('status')} | "
                             f"{_cell(row.get('detail'), 200)} |")
        lines.append("")

    if usage:
        from agent.tracing.usage import summarise

        lines += ["## Model usage", "",
                  "| Role | Calls | Failed | Prompt tokens | Completion tokens | Model time | Est. cost |",
                  "|---|---|---|---|---|---|---|"]
        for role, row in summarise(usage).items():
            cost = f"${row['cost_usd']:.4f}" if row["cost_known"] else "unknown"
            lines.append(f"| {role} | {row['calls']} | {row['failed']} | {row['prompt_tokens']} | "
                         f"{row['completion_tokens']} | {row['seconds']:.0f}s | {cost} |")
        lines.append("")

    lines += ["## Timeline", "", "| Seq | Phase | Event | Detail |", "|---|---|---|---|"]
    for event in events:
        payload = event.get("payload") or {}
        kind = event.get("kind")
        detail = ""
        if kind == "transition":
            detail = f"{payload.get('from')} → {payload.get('to')}"
        elif kind == "action_start":
            action = payload.get("action") or {}
            detail = _describe_action(action)
            if payload.get("retry"):
                detail += " (retry)"
            if action.get("rationale"):
                detail += f" — *why:* {_short(action['rationale'], 150)}"
        elif kind == "action_result":
            result = payload.get("result") or {}
            detail = f"{result.get('status')}: {_short(result.get('observation'), 140)}"
        elif kind in {"action_unknown", "action_cancelled"}:
            detail = _short(payload)
        elif kind == "failure":
            detail = f"[{payload.get('category')}] {_short(payload.get('message'), 200)}"
        elif kind == "recovery":
            detail = f"**{payload.get('kind')}** — {_short(payload.get('reason'), 200)}"
        elif kind == "module_result":
            result = payload.get("result")
            label = payload.get("operation")
            if isinstance(result, dict) and "launch_command" in result:
                detail = f"{label}: request verification `{' '.join(result['launch_command'])}`"
            elif isinstance(result, dict) and "reason" in result and "kind" not in result:
                detail = f"{label}: revise plan — {_short(result['reason'])}"
            else:
                detail = str(label)
        elif kind in {"module_start", "run_start"}:
            continue  # Paired with the result rows; keeps the table readable.
        else:
            detail = _short(payload)
        lines.append(f"| {event.get('seq')} | {event.get('phase')} | {kind} | {_cell(detail, 240)} |")
    lines.append("")
    lines.append("Raw data: `events.jsonl` (all events), `checkpoints.jsonl`, `outputs/index.jsonl`, "
                 "`evidence/`, `verification/`, `model_usage.jsonl`, `summary.json`.")
    return "\n".join(lines) + "\n"
