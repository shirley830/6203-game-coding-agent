"""Model-call accounting shared by the LLM clients and the trace recorder (Member 6).

Each model call appends one record (role, model, tokens, latency, estimated
cost, error type). The recorder persists new records to
``traces/<run_id>/model_usage.jsonl`` and summarises them in run_report.md.
No prompt or response text and no credentials are stored here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import threading
from typing import Any


@dataclass(frozen=True)
class ModelCall:
    seq: int
    role: str
    model: str
    ok: bool
    latency_seconds: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class UsageMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: list[ModelCall] = []

    def record(self, **fields: Any) -> ModelCall:
        with self._lock:
            call = ModelCall(seq=len(self._calls) + 1, **fields)
            self._calls.append(call)
            return call

    def since(self, count: int) -> list[ModelCall]:
        with self._lock:
            return list(self._calls[count:])

    @property
    def calls(self) -> list[ModelCall]:
        with self._lock:
            return list(self._calls)


def summarise(calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Totals per role from persisted records (plain dicts)."""
    totals: dict[str, dict[str, Any]] = {}
    for call in calls:
        row = totals.setdefault(call.get("role", "?"), {
            "calls": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "cost_usd": 0.0, "cost_known": True, "seconds": 0.0})
        row["calls"] += 1
        row["failed"] += 0 if call.get("ok") else 1
        row["prompt_tokens"] += call.get("prompt_tokens") or 0
        row["completion_tokens"] += call.get("completion_tokens") or 0
        row["seconds"] += call.get("latency_seconds") or 0.0
        if call.get("cost_usd") is None:
            row["cost_known"] = row["cost_known"] and not call.get("ok")
        else:
            row["cost_usd"] += call["cost_usd"]
    return totals
