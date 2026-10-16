"""Parse controller tool observations into per-criterion evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.state import CheckStatus, Criterion, CriterionResult, ToolKind, ToolResult, ToolStatus


_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


@dataclass
class EvidenceLedger:
    """Accumulates read-only / command evidence within one verification cycle."""

    refs_by_criterion: dict[str, list[str]] = field(default_factory=dict)
    details: dict[str, list[str]] = field(default_factory=dict)
    harness: dict[str, Any] = field(default_factory=dict)
    tool_ops: list[str] = field(default_factory=list)
    files_seen: list[str] = field(default_factory=list)
    inspect_ok: bool = False
    command_ok: bool = False

    def note_tool(self, action_id: str, kind: ToolKind) -> None:
        self.tool_ops.append(f"tool:{kind.value}:{action_id}")

    def add_ref(self, criterion_id: str, ref: str, detail: str = "") -> None:
        if not ref or not ref.strip():
            return
        bucket = self.refs_by_criterion.setdefault(criterion_id, [])
        if ref not in bucket:
            bucket.append(ref)
        if detail:
            self.details.setdefault(criterion_id, []).append(detail)

    def ingest_result(self, result: ToolResult, kind: ToolKind, action_id: str) -> None:
        self.note_tool(action_id, kind)
        base_ref = f"tool:{kind.value}:{action_id}"
        if result.status != ToolStatus.OK:
            self.add_ref("_runtime", base_ref, result.observation[:500])
            return

        for ref in result.output_refs:
            if isinstance(ref, str) and ref.strip():
                self.add_ref("_runtime", ref)

        if kind == ToolKind.INSPECT_WORKSPACE:
            self.inspect_ok = True
            files = _extract_paths(result)
            self.files_seen.extend(files)
            for path in files[:20]:
                self.add_ref("C-LAUNCH", f"workspace:{path}", "workspace listing")
                self.add_ref("C-TECHNICAL", f"workspace:{path}", "workspace listing")
            self.add_ref("C-LAUNCH", base_ref, result.observation[:300])
            self.add_ref("C-FUNCTIONAL", base_ref, result.observation[:300])
            self.add_ref("C-INTERACTION", base_ref, result.observation[:300])
            self.add_ref("C-TECHNICAL", base_ref, result.observation[:300])
            return

        if kind == ToolKind.READ_FILE:
            path = ""
            if isinstance(result.data, Mapping):
                # Runtime may echo path; otherwise use observation snippet.
                maybe = result.data.get("path")
                if isinstance(maybe, str):
                    path = maybe
            text = ""
            if isinstance(result.data, Mapping) and isinstance(result.data.get("text"), str):
                text = result.data["text"]
            elif result.observation:
                text = result.observation
            lower = text.lower()
            file_ref = f"file:{path or action_id}"
            self.add_ref("C-LAUNCH", file_ref, "source/docs read")
            if any(token in lower for token in ("canvas", "requestanimationframe", "<canvas", "webgl")):
                self.add_ref("C-TECHNICAL", file_ref, "canvas/render surface mentioned")
            if any(token in lower for token in ("keydown", "keyup", "pointer", "click", "addEventListener", "wasd", "arrow")):
                self.add_ref("C-INTERACTION", file_ref, "input handlers present in source")
            if any(token in lower for token in ("score", "collision", "collect", "restart", "game over", "win")):
                self.add_ref("C-FUNCTIONAL", file_ref, "gameplay symbols present in source")
            self.add_ref("C-FUNCTIONAL", base_ref, result.observation[:200])
            return

        if kind == ToolKind.RUN_COMMAND:
            self.command_ok = result.status == ToolStatus.OK
            payload = _parse_harness_json(result.observation, result.data)
            self.harness = payload or {}
            self.add_ref("C-LAUNCH", base_ref, result.observation[:400])
            self.add_ref("C-FUNCTIONAL", base_ref, result.observation[:400])
            self.add_ref("C-INTERACTION", base_ref, result.observation[:400])
            self.add_ref("C-TECHNICAL", base_ref, result.observation[:400])
            if payload:
                _apply_harness_payload(self, payload)


def _extract_paths(result: ToolResult) -> list[str]:
    paths: list[str] = []
    data = result.data if isinstance(result.data, Mapping) else {}
    for key in ("files", "paths", "entries", "listing"):
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    paths.append(item)
                elif isinstance(item, Mapping):
                    if item.get("type") not in (None, "file"):
                        continue  # directories/symlinks cannot be read as files
                    name = item.get("path") or item.get("name")
                    if isinstance(name, str):
                        paths.append(name)
    if not paths and result.observation:
        for match in re.findall(r"[\w./-]+\.(?:html|js|css|md|json|py|ts|tsx)", result.observation):
            paths.append(match)
    return paths


def _parse_harness_json(observation: str, data: Any) -> dict[str, Any] | None:
    if isinstance(data, Mapping):
        nested = data.get("harness") or data.get("verification") or data.get("json")
        if isinstance(nested, dict):
            return dict(nested)
        if "launch" in data and isinstance(data.get("launch"), dict):
            return dict(data)
    text = observation or ""
    # Prefer a line starting with HARNESS_JSON=
    for line in text.splitlines():
        if line.startswith("HARNESS_JSON="):
            raw = line[len("HARNESS_JSON="):].strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    match = _JSON_BLOCK.search(text)
    if match:
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and ("launch" in parsed or "checks" in parsed or "ok" in parsed):
            return parsed
    return None


def _apply_harness_payload(ledger: EvidenceLedger, payload: dict[str, Any]) -> None:
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        checks = {}
    launch = checks.get("launch") if isinstance(checks.get("launch"), dict) else payload.get("launch")
    functional = checks.get("functional") if isinstance(checks.get("functional"), dict) else payload.get("functional")
    interaction = checks.get("interaction") if isinstance(checks.get("interaction"), dict) else payload.get("interaction")
    technical = checks.get("technical") if isinstance(checks.get("technical"), dict) else payload.get("technical")

    def absorb(criterion_id: str, block: Any, default_ref: str) -> None:
        if not isinstance(block, dict):
            return
        status = str(block.get("status", "")).lower()
        refs = block.get("evidence_refs") or block.get("refs") or []
        if isinstance(refs, (list, tuple)):
            for ref in refs:
                if isinstance(ref, str) and ref.strip():
                    ledger.add_ref(criterion_id, ref, str(block.get("detail", ""))[:300])
        detail = str(block.get("detail", "") or block.get("message", ""))[:300]
        ledger.add_ref(criterion_id, default_ref, detail)
        if status in {"pass", "ok", "true"}:
            ledger.add_ref(criterion_id, f"harness:{criterion_id}:pass", detail)

    absorb("C-LAUNCH", launch, "harness:launch")
    absorb("C-FUNCTIONAL", functional, "harness:functional")
    absorb("C-INTERACTION", interaction, "harness:interaction")
    absorb("C-TECHNICAL", technical, "harness:technical")


def evaluate_criteria(criteria: tuple[Criterion, ...], ledger: EvidenceLedger) -> tuple[CriterionResult, ...]:
    """Map accumulated evidence to CriterionResult rows (controller computes overall pass)."""
    harness = ledger.harness
    checks = harness.get("checks") if isinstance(harness.get("checks"), dict) else harness

    requirement_blocks = harness.get("requirements") if isinstance(harness.get("requirements"), Mapping) else {}
    # Real input proven by a spec scenario is stronger evidence than the generic
    # probe clicks/keys, which can miss small targets.
    spec_input_proven = [
        c.criterion_id for c in criteria
        if c.criterion_id.startswith("REQ-") and c.category == "interaction"
        and isinstance(requirement_blocks.get(c.criterion_id), Mapping)
        and str(requirement_blocks[c.criterion_id].get("status", "")).lower() == "pass"
    ]

    results: list[CriterionResult] = []
    for criterion in criteria:
        if criterion.criterion_id == "C-ACCEPTANCE-INFO":
            # Informational note about how the suite was built; never a test.
            results.append(CriterionResult(
                criterion.criterion_id, CheckStatus.SKIP,
                tuple(ledger.tool_ops[-1:]) or ("note:acceptance",), "informational, not a test"))
            continue
        refs = list(ledger.refs_by_criterion.get(criterion.criterion_id, []))
        # Always attach at least one cycle-level tool ref when tools ran.
        if not refs and ledger.tool_ops:
            refs.append(ledger.tool_ops[-1])

        status = CheckStatus.UNKNOWN
        detail_parts = list(ledger.details.get(criterion.criterion_id, []))[-3:]

        block = None
        requirements = harness.get("requirements") if isinstance(harness.get("requirements"), Mapping) else {}
        spec_criterion = criterion.criterion_id.startswith("REQ-") or criterion.criterion_id == "C-CONTRACT"
        if spec_criterion:
            # Spec-derived criteria are judged only by their own scenario (or the
            # contract check); generic page signals never satisfy them.
            if criterion.criterion_id == "C-CONTRACT":
                block = checks.get("contract") if isinstance(checks, Mapping) else None
            else:
                block = requirements.get(criterion.criterion_id)
            if not isinstance(block, Mapping):
                block = {"status": "fail", "detail": "scenario was not executed by the harness"}
            refs.extend(r for r in block.get("evidence_refs", []) if isinstance(r, str) and r not in refs)
        elif isinstance(checks, dict):
            key = {
                "launch": "launch",
                "functional": "functional",
                "interaction": "interaction",
                "technical": "technical",
            }.get(criterion.category)
            if key and isinstance(checks.get(key), dict):
                block = checks[key]
            elif criterion.criterion_id in checks and isinstance(checks[criterion.criterion_id], dict):
                block = checks[criterion.criterion_id]

        if isinstance(block, Mapping):
            raw = str(block.get("status", "")).lower()
            if raw in {"pass", "ok", "true", "passed"}:
                status = CheckStatus.PASS
            elif raw in {"fail", "false", "failed", "error"}:
                status = CheckStatus.FAIL
            elif raw in {"skip", "skipped"}:
                status = CheckStatus.SKIP
            detail_parts.append(str(block.get("detail", block.get("message", "")))[:500])
        else:
            # Heuristic fallback when harness JSON is missing but static evidence exists.
            status = _heuristic_status(criterion, ledger, refs)

        # Mandatory controller rule: PASS requires non-empty evidence_refs.
        if status == CheckStatus.PASS and not refs:
            status = CheckStatus.FAIL
            detail_parts.append("PASS claimed without evidence_refs")

        if (criterion.category == "interaction" and not spec_criterion
                and status != CheckStatus.PASS and spec_input_proven):
            status = CheckStatus.PASS
            refs.extend(f"scenario:{cid}" for cid in spec_input_proven)
            detail_parts.append("player input proven by spec scenario(s) " + ", ".join(spec_input_proven))
        elif status == CheckStatus.PASS and criterion.category == "interaction" and not spec_criterion:
            if not _interaction_proven(ledger, refs):
                status = CheckStatus.FAIL
                detail_parts.append(
                    "Interaction not proven: page open alone is insufficient; "
                    "need harness state-change evidence"
                )

        results.append(
            CriterionResult(
                criterion_id=criterion.criterion_id,
                status=status,
                evidence_refs=tuple(refs) if refs else (f"missing:{criterion.criterion_id}",),
                detail="; ".join(dict.fromkeys(p for p in detail_parts if p))[:800],
            )
        )
    return tuple(results)


def _interaction_proven(ledger: EvidenceLedger, refs: list[str]) -> bool:
    if any("interaction" in r and "pass" in r for r in refs):
        return True
    interaction = None
    checks = ledger.harness.get("checks") if isinstance(ledger.harness.get("checks"), dict) else ledger.harness
    if isinstance(checks, dict) and isinstance(checks.get("interaction"), dict):
        interaction = checks["interaction"]
    if isinstance(interaction, dict):
        if interaction.get("state_changed") is True:
            return True
        if str(interaction.get("status", "")).lower() in {"pass", "ok", "true", "passed"}:
            return True
    return False


def _heuristic_status(criterion: Criterion, ledger: EvidenceLedger, refs: list[str]) -> CheckStatus:
    if not ledger.command_ok:
        if criterion.category == "launch" and ledger.inspect_ok and refs:
            return CheckStatus.FAIL
        return CheckStatus.FAIL if criterion.mandatory else CheckStatus.SKIP

    # Command ran but harness JSON missing: fail mandatory rather than invent PASS.
    if criterion.mandatory:
        return CheckStatus.FAIL
    return CheckStatus.SKIP
