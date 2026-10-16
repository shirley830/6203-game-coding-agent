"""Model-call accounting in traces and the readable run report."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agent.planning import LiteLLMClient
from agent.state import ExecutionContext, Phase, RunSummary, TraceEvent
from agent.tracing.recorder import FileTraceRecorder
from agent.tracing.usage import UsageMeter


def fake_response(text="{}", prompt=120, completion=30):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion))


class ClientAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_failure_are_recorded_without_text(self):
        meter = UsageMeter()
        client = LiteLLMClient(model="openai/x", role="planner", usage=meter)
        with patch("agent.planning.llm_client.acompletion",
                   new=AsyncMock(return_value=fake_response('{"secret": "prompt text"}'))):
            await client.generate(system_prompt="s", user_prompt="u", timeout_seconds=5)
        with patch("agent.planning.llm_client.acompletion",
                   new=AsyncMock(side_effect=TimeoutError("slow"))):
            with self.assertRaises(TimeoutError):
                await client.generate(system_prompt="s", user_prompt="u", timeout_seconds=5)
        first, second = meter.calls
        self.assertEqual((first.role, first.ok, first.prompt_tokens, first.completion_tokens),
                         ("planner", True, 120, 30))
        self.assertEqual((second.ok, second.error), (False, "TimeoutError"))
        self.assertNotIn("secret", json.dumps([c.to_json() for c in meter.calls]))
        self.assertIsNone(first.cost_usd)  # no price known: unknown, never a fake $0


class RecorderUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_usage_persisted_and_summarised_with_rationales(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = ExecutionContext("run", root, root / "s.md", root / "game", root / "traces" / "run")
            meter = UsageMeter()
            tracer = FileTraceRecorder(usage=meter)
            await tracer.open_run(context)
            meter.record(role="acceptance", model="m", ok=True, latency_seconds=2.0,
                         prompt_tokens=1000, completion_tokens=400, cost_usd=0.01)
            meter.record(role="planner", model="m", ok=False, latency_seconds=1.0, error="Timeout")
            await tracer.append_event(TraceEvent("run", 1, "t", "action_start", Phase.EXECUTING, {
                "action": {"kind": "write_file", "arguments": {"path": "game.js"},
                           "rationale": "Implement scoring for REQ-R2"}}))
            meter.record(role="planner", model="m", ok=True, latency_seconds=3.0,
                         prompt_tokens=2000, completion_tokens=800, cost_usd=0.02)
            await tracer.finalize(RunSummary("run", Phase.FAILED, "x", context.game_dir,
                                             context.trace_dir, (), None, 1, 0, 0, 1.0))
            lines = (context.trace_dir / "model_usage.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 3)
            report = (context.trace_dir / "run_report.md").read_text()
            self.assertIn("## Model usage", report)
            self.assertIn("| planner | 2 | 1 | 2000 | 800 |", report)
            self.assertIn("| acceptance | 1 | 0 | 1000 | 400 | 2s | $0.0100 |", report)
            self.assertIn("*why:* Implement scoring for REQ-R2", report)


if __name__ == "__main__":
    unittest.main()
