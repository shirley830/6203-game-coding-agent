"""Isolated Python and Node dependency installation."""

from __future__ import annotations

from dataclasses import dataclass
from email.parser import Parser
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Sequence

from .errors import RuntimeToolError
from .processes import ProcessResult, ProcessRunner, RunStorage


PYTHON_MANIFESTS = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")


@dataclass(frozen=True)
class InstallationResult:
    manager: str
    steps: tuple[ProcessResult, ...]

    @property
    def final(self) -> ProcessResult:
        return self.steps[-1]

    @property
    def output_refs(self) -> tuple[str, ...]:
        return tuple(ref for step in self.steps for ref in step.output_refs)


class DependencyInstaller:
    def __init__(self, game_dir: Path, storage: RunStorage, runner: ProcessRunner) -> None:
        self.game_dir = Path(game_dir)
        self.storage = storage
        self.runner = runner

    def detect_manager(self) -> str:
        has_node = (self.game_dir / "package.json").is_file()
        has_python = any((self.game_dir / name).is_file() for name in PYTHON_MANIFESTS)
        if not has_python:
            has_python = any(self.game_dir.glob("*.py"))
        if has_node and has_python:
            raise RuntimeToolError(
                "DEPENDENCY_MANAGER_AMBIGUOUS",
                "Both Python and Node project markers exist; split or clarify the dependency action",
            )
        if has_node:
            return "npm"
        return "pip"

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeToolError(
                "DEPENDENCY_TIMEOUT",
                "Dependency action exhausted its timeout before the next subprocess",
                side_effects_unknown=True,
            )
        return remaining

    def _existing_inventory(self) -> list[str]:
        try:
            payload = json.loads(self.storage.environment_record.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []
        installed = payload.get("installed_packages", [])
        return sorted(item for item in installed if isinstance(item, str) and item)

    @staticmethod
    def _python_inventory(environment: Path) -> list[str]:
        candidates = list(environment.glob("lib/python*/site-packages/*.dist-info/METADATA"))
        candidates.extend(environment.glob("Lib/site-packages/*.dist-info/METADATA"))
        inventory: list[str] = []
        for metadata_path in candidates:
            try:
                metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            name, version = metadata.get("Name"), metadata.get("Version")
            if name and version:
                inventory.append(f"{name}=={version}")
        return sorted(set(inventory), key=str.casefold)

    def _record(
        self,
        manager: str,
        packages: Sequence[str],
        status: str,
        *,
        installed_packages: Sequence[str] | None = None,
    ) -> None:
        inventory = (
            sorted(set(installed_packages), key=str.casefold)
            if installed_packages is not None else self._existing_inventory()
        )
        if status == "ok" and installed_packages is None:
            inventory = sorted(
                set(inventory) | {item for item in packages if not item.startswith("-")},
                key=str.casefold,
            )
        payload = {
            "schema": 1,
            "manager": manager,
            "installed_packages": inventory,
            "last_request": list(packages),
            "last_status": status,
        }
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.storage.root, delete=False
            ) as stream:
                temporary_name = stream.name
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.storage.environment_record)
            temporary_name = None
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    async def install(
        self,
        packages: Sequence[str],
        *,
        timeout_seconds: float,
        action_id: str,
    ) -> InstallationResult:
        if not packages or any(not isinstance(item, str) or not item.strip() for item in packages):
            raise RuntimeToolError("DEPENDENCIES_INVALID", "packages must contain non-empty strings")
        manager = self.detect_manager()
        deadline = time.monotonic() + timeout_seconds
        steps: list[ProcessResult] = []
        if manager == "pip":
            environment = self.storage.environment_dir / "python"
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if not python.is_file():
                setup = await self.runner.run(
                    [sys.executable, "-m", "venv", str(environment)],
                    cwd=self.game_dir,
                    timeout_seconds=self._remaining(deadline),
                    action_id=action_id,
                    label="venv",
                    dependency_command=True,
                )
                steps.append(setup)
                if setup.timed_out or setup.exit_code != 0 or not setup.cleanup_confirmed:
                    unknown = setup.timed_out or not setup.cleanup_confirmed
                    self._record(manager, packages, "unknown" if unknown else "failed")
                    return InstallationResult(manager, tuple(steps))
            install = await self.runner.run(
                [
                    str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "--no-input", *packages,
                ],
                cwd=self.game_dir,
                timeout_seconds=self._remaining(deadline),
                action_id=action_id,
                label="pip",
                dependency_command=True,
            )
        else:
            install = await self.runner.run(
                ["npm", "install", "--no-audit", "--no-fund", "--save-exact", *packages],
                cwd=self.game_dir,
                timeout_seconds=self._remaining(deadline),
                action_id=action_id,
                label="npm",
                dependency_command=True,
            )
        steps.append(install)
        status = (
            "unknown" if install.timed_out or not install.cleanup_confirmed
            else "ok" if install.exit_code == 0 else "failed"
        )
        inventory = (
            self._python_inventory(self.storage.environment_dir / "python")
            if manager == "pip" and status == "ok" else None
        )
        self._record(manager, packages, status, installed_packages=inventory)
        return InstallationResult(manager, tuple(steps))
