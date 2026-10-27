"""Reasoning-model configuration (GLM-5.3 spent its whole output budget reasoning;
Kimi K3 bills reasoning as output, so it is capped too)."""

import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agent.integrations import decision_reasoning_budget, reasoning_budget
from agent.planning import LiteLLMClient


class ReasoningBudgetTests(unittest.TestCase):
    def test_glm_capped_by_default_others_untouched(self):
        with patch.dict(os.environ, {"AGENT_REASONING_MAX_TOKENS": ""}):
            self.assertEqual(reasoning_budget("openrouter/z-ai/glm-5.3"), 8000)
            self.assertEqual(reasoning_budget("openrouter/moonshotai/kimi-k3"), 8000)
            self.assertIsNone(reasoning_budget("openrouter/anthropic/claude-sonnet-5.5"))

    def test_step_decisions_use_a_lower_cap(self):
        with patch.dict(os.environ, {"AGENT_REASONING_MAX_TOKENS": "",
                                     "AGENT_DECISION_REASONING_MAX_TOKENS": ""}):
            self.assertEqual(decision_reasoning_budget("openrouter/moonshotai/kimi-k3"), 3000)
            self.assertIsNone(decision_reasoning_budget("openrouter/anthropic/claude-sonnet-5.5"))
        with patch.dict(os.environ, {"AGENT_DECISION_REASONING_MAX_TOKENS": "1500"}):
            self.assertEqual(decision_reasoning_budget("openrouter/moonshotai/kimi-k3"), 1500)
        with patch.dict(os.environ, {"AGENT_DECISION_REASONING_MAX_TOKENS": "0"}):
            self.assertIsNone(decision_reasoning_budget("openrouter/moonshotai/kimi-k3"))

    def test_environment_overrides_and_disables(self):
        with patch.dict(os.environ, {"AGENT_REASONING_MAX_TOKENS": "4000"}):
            self.assertEqual(reasoning_budget("openai/gpt-4.1-mini"), 4000)
        with patch.dict(os.environ, {"AGENT_REASONING_MAX_TOKENS": "0"}):
            self.assertIsNone(reasoning_budget("openrouter/z-ai/glm-5.3"))


def response(content, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                    finish_reason=finish)], usage=None)


class ClientRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_cap_is_sent_and_truncation_is_explained(self):
        client = LiteLLMClient(model="openrouter/z-ai/glm-5.3", reasoning_max_tokens=8000)
        call = AsyncMock(return_value=response("{}"))
        with patch("agent.planning.llm_client.acompletion", new=call):
            await client.generate(system_prompt="s", user_prompt="u", timeout_seconds=5)
        # OpenRouter also reports the billed cost in the usage block.
        self.assertEqual(call.call_args.kwargs["extra_body"],
                         {"reasoning": {"max_tokens": 8000}, "usage": {"include": True}})
        with patch("agent.planning.llm_client.acompletion",
                   new=AsyncMock(return_value=response("", "length"))):
            with self.assertRaisesRegex(ValueError, "AGENT_REASONING_MAX_TOKENS"):
                await client.generate(system_prompt="s", user_prompt="u", timeout_seconds=5)

    async def test_no_cap_sends_no_reasoning_parameter(self):
        call = AsyncMock(return_value=response("{}"))
        with patch("agent.planning.llm_client.acompletion", new=call):
            await LiteLLMClient(model="openai/x").generate(system_prompt="s", user_prompt="u",
                                                           timeout_seconds=5)
        self.assertNotIn("extra_body", call.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
