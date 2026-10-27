"""Three result levels for a finished run (Member 1, integration).

The controller outcome says whether the success gate passed. That alone hides
how much was proven: a run whose spec-derived requirements could not be
parsed passes on the generic checks only. The levels keep these apart:

- launch:   the game loaded in the browser (C-LAUNCH);
- rules:    the spec-derived requirements (REQ-*) that were checked and passed;
- delivery: game/ with README.md and a recorded launch command.

Checks re-derived after a dispute (test_conflict) are listed under
``revised_checks``; a run that passed only after re-deriving a check says so.

``verdict`` is "fully_accepted" only when the run succeeded and all three
levels are complete; "acceptance_incomplete" when it succeeded without any
specific rules (未完整验收); otherwise "not_accepted". The levels never
change the outcome or the exit code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .state import AcceptanceSuite, CheckStatus, Phase, VerificationReport

VERDICT_LABELS = {
    "fully_accepted": "完整成功 fully accepted",
    "acceptance_incomplete": "未完整验收 acceptance incomplete (generic checks only)",
    "not_accepted": "未通过 not accepted",
}


def _status(report: VerificationReport | None, criterion_id: str) -> str:
    if report is None:
        return "not_verified"
    for result in report.results:
        if result.criterion_id == criterion_id:
            return CheckStatus(result.status).value
    return "not_checked"


def result_levels(outcome: Phase, suite: AcceptanceSuite | None, report: VerificationReport | None,
                  game_dir: Path, launch_command: tuple[str, ...],
                  revisions: list[dict[str, Any]] | tuple = ()) -> dict[str, Any]:
    criteria = suite.criteria if suite is not None else ()
    rule_ids = [c.criterion_id for c in criteria if c.criterion_id.startswith("REQ-")]
    incomplete = any(c.criterion_id == "C-ACCEPTANCE-INCOMPLETE" for c in criteria)
    statuses = {r.criterion_id: CheckStatus(r.status).value for r in (report.results if report else ())}
    passed = sum(statuses.get(cid) == "pass" for cid in rule_ids)
    failed = [cid for cid in rule_ids if statuses.get(cid) == "fail"]

    launch = _status(report, "C-LAUNCH")
    if not rule_ids:
        rules_state = "incomplete" if incomplete or suite is not None else "not_checked"
    elif report is None:
        rules_state = "not_verified"
    else:
        rules_state = "passed" if passed == len(rule_ids) else "partial"

    missing = []
    if not game_dir.is_dir():
        missing.append("game/")
    elif not (game_dir / "README.md").is_file():
        missing.append("README.md")
    if not launch_command:
        missing.append("launch command")

    if outcome == Phase.SUCCEEDED and launch == "pass" and rules_state == "passed" and not missing:
        verdict = "fully_accepted"
    elif outcome == Phase.SUCCEEDED and rules_state == "incomplete":
        verdict = "acceptance_incomplete"
    else:
        verdict = "not_accepted"
    return {
        "verdict": verdict,
        "launch": launch,
        "rules": {"state": rules_state, "passed": passed, "total": len(rule_ids), "failed": failed},
        "delivery": {"complete": not missing, "missing": missing},
        # Disputed checks that were re-examined: "replace" (re-derived), "keep" or "error".
        "revised_checks": [dict(r) for r in revisions],
    }


def render_levels(levels: dict[str, Any]) -> list[str]:
    """Plain lines for the terminal and the run report."""
    rules = levels["rules"]
    rule_text = {
        "passed": f"{rules['passed']}/{rules['total']} passed",
        "partial": f"{rules['passed']}/{rules['total']} passed; failed: {', '.join(rules['failed']) or '-'}",
        "incomplete": "0 specific rules (未完整验收): only the generic checks ran",
        "not_verified": f"{rules['total']} rules, no final verification",
        "not_checked": "no acceptance suite",
    }[rules["state"]]
    delivery = levels["delivery"]
    revised = levels.get("revised_checks") or []
    replaced = [r["criterion_id"] for r in revised if r.get("verdict") == "replace"]
    kept = [r["criterion_id"] for r in revised if r.get("verdict") != "replace"]
    if replaced or kept:
        rule_text += ("; re-derived (重做): " + ", ".join(replaced) if replaced else "") + (
            "; disputed, kept: " + ", ".join(kept) if kept else "")
    return [
        f"Result: {VERDICT_LABELS[levels['verdict']]}",
        f"  1. Launch (启动):          {levels['launch']}",
        f"  2. Specific rules (具体规则): {rule_text}",
        "  3. Delivery (交付):        "
        + ("complete" if delivery["complete"] else "missing " + ", ".join(delivery["missing"])),
    ]
