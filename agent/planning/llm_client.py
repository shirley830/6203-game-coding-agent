import time
from typing import Any, Protocol

import litellm
from litellm import acompletion

# LiteLLM prints a feedback banner to stdout on every error, which would corrupt
# the CLI's JSON RunSummary on stdout. Errors still reach the controller.
litellm.suppress_debug_info = True

from agent.tracing.usage import UsageMeter  # noqa: E402


class LLMClient(Protocol):
    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
    ) -> str:
        """Generate one response without hidden retries."""
        ...


class LiteLLMClient:
    def __init__(
        self,
        *,
        model: str,
        api_base: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = True,
        role: str = "model",
        usage: "UsageMeter | None" = None,
        reasoning_max_tokens: int | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("Model name must not be empty")

        self.model = model
        self.api_base = api_base
        self.max_tokens = max_tokens
        self.temperature = temperature
        # Every caller expects one JSON object; JSON mode makes providers that
        # support it emit syntactically valid JSON. Unsupported -> dropped.
        self.json_mode = json_mode
        # Accounting only (tokens, latency, cost); never prompt text or keys.
        self.role = role
        self.usage = usage
        # Cap for reasoning models (OpenRouter `reasoning.max_tokens`). Without
        # it GLM-5.3 spent the whole output budget reasoning and returned no text.
        self.reasoning_max_tokens = reasoning_max_tokens

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
    ) -> str:
        request = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            "timeout": timeout_seconds,
            "num_retries": 0,
            "max_retries": 0,
        }

        if self.api_base is not None:
            request["api_base"] = self.api_base
        if self.max_tokens is not None:
            # Whole game files are returned inside write_file content.
            request["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.json_mode:
            request["response_format"] = {"type": "json_object"}
            request["drop_params"] = True
        extra_body: dict[str, Any] = {}
        if self.reasoning_max_tokens:
            extra_body["reasoning"] = {"max_tokens": self.reasoning_max_tokens}
        if self.model.startswith("openrouter/"):
            # OpenRouter reports the billed cost itself; LiteLLM has no price for new
            # models such as Kimi K3 (the 2026-10-27 runs logged cost_usd null).
            extra_body["usage"] = {"include": True}
        if extra_body:
            request["extra_body"] = extra_body

        started = time.monotonic()
        try:
            response = await acompletion(**request)
        except BaseException as exc:
            self._record(started, ok=False, error=type(exc).__name__)
            raise
        self._record(started, ok=True, response=response)
        content = response.choices[0].message.content

        if not isinstance(content, str) or not content.strip():
            finish = getattr(response.choices[0], "finish_reason", None)
            if finish == "length":
                raise ValueError("Model returned no text: the output limit was used up "
                                 "(reasoning models: set AGENT_REASONING_MAX_TOKENS)")
            raise ValueError("Model returned empty text content")

        return content

    def _record(self, started: float, *, ok: bool, response: Any = None,
                error: str | None = None) -> None:
        if self.usage is None:
            return
        prompt_tokens = completion_tokens = None
        cost = None
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
        if response is not None:
            try:
                cost = float(litellm.completion_cost(completion_response=response))
            except Exception:  # noqa: BLE001 - pricing unknown for some models
                cost = None
            if cost == 0.0 and (prompt_tokens or completion_tokens):
                cost = None  # LiteLLM returns 0 for models it has no price for
            if cost is None:
                cost = _provider_cost(usage)
        self.usage.record(role=self.role, model=self.model, ok=ok,
                          latency_seconds=round(time.monotonic() - started, 3),
                          prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                          cost_usd=cost, error=error)


def _provider_cost(usage: Any) -> float | None:
    """Cost the provider reported in the usage block (OpenRouter: usage.cost, in USD)."""
    if usage is None:
        return None
    value = usage.get("cost") if isinstance(usage, dict) else getattr(usage, "cost", None)
    if value is None:
        extra = getattr(usage, "model_extra", None) or {}
        value = extra.get("cost") if isinstance(extra, dict) else None
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return None
    return cost if cost >= 0 else None

