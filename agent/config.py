"""Immutable, validated runtime policy. No provider credentials live here."""

from dataclasses import dataclass
import math
from pathlib import Path


@dataclass(frozen=True)
class AgentConfig:
    run_root: Path
    max_runtime_seconds: float = 900.0
    max_steps: int = 200
    max_commands: int = 60
    max_retries_per_operation: int = 2
    max_recovery_attempts: int = 8
    model_call_timeout_seconds: float = 60.0
    command_timeout_seconds: float = 60.0
    max_no_progress_cycles: int = 5
    finalization_reserve_seconds: float = 10.0
    infrastructure_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        root = Path(self.run_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("run_root must be an existing directory")
        object.__setattr__(self, "run_root", root)
        for name in ("max_steps", "max_commands", "max_no_progress_cycles"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("max_retries_per_operation", "max_recovery_attempts"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("max_runtime_seconds", "model_call_timeout_seconds",
                     "command_timeout_seconds", "finalization_reserve_seconds",
                     "infrastructure_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite positive number")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if self.finalization_reserve_seconds >= self.max_runtime_seconds:
            raise ValueError("The finalization reserve must be smaller than the total runtime")

    @property
    def game_dir(self) -> Path:
        return self.run_root / "game"

    @property
    def traces_dir(self) -> Path:
        return self.run_root / "traces"
