"""Structured, known failures returned by the production runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class RuntimeToolError(Exception):
    """A failure whose side effects and user-facing error code are known."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        data: Mapping[str, Any] | None = None,
        side_effects_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = dict(data or {})
        self.side_effects_unknown = side_effects_unknown
