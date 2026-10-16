from typing import Protocol

import litellm
from litellm import acompletion

# LiteLLM prints a feedback banner to stdout on every error, which would corrupt
# the CLI's JSON RunSummary on stdout. Errors still reach the controller.
litellm.suppress_debug_info = True


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

        response = await acompletion(**request)
        content = response.choices[0].message.content

        if not isinstance(content, str) or not content.strip():
            raise ValueError("Model returned empty text content")

        return content
