from typing import Protocol


class LLMClient(Protocol):
    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
    ) -> str:
        """Generate one model response without hidden retries."""
        ...
