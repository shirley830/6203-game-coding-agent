"""Required entry point: python -m agent game_design_prompt.md."""

import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
from collections.abc import Sequence

from .config import AgentConfig
from .controller import Controller, Dependencies
from .state import Phase, to_jsonable


EXIT_CODES = {Phase.SUCCEEDED: 0, Phase.FAILED: 1, Phase.EXHAUSTED: 3, Phase.CANCELLED: 130}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autonomous coding-agent harness (Member 1)")
    parser.add_argument("spec", type=Path, help="Game specification Markdown file")
    parser.add_argument("--run-root", type=Path, default=Path.cwd(), help="Parent directory of game/ and traces/")
    parser.add_argument("--factory", default=os.environ.get(
        "AGENT_MODULE_FACTORY", "agent.integrations:create_dependencies"),
        help="Team dependency factory in module:function format")
    parser.add_argument("--max-runtime", type=float, default=1800.0)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--max-commands", type=int, default=80)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--max-recovery-attempts", type=int, default=20)
    parser.add_argument("--model-timeout", type=float, default=240.0)
    parser.add_argument("--command-timeout", type=float, default=90.0)
    parser.add_argument("--max-no-progress", type=int, default=5)
    parser.add_argument("--finalization-reserve", type=float, default=20.0)
    parser.add_argument("--infrastructure-timeout", type=float, default=5.0)
    return parser


def load_dependencies(factory_path: str, config: AgentConfig) -> Dependencies:
    if factory_path.count(":") != 1:
        raise ValueError("Factory format must be module:function")
    module_name, function_name = factory_path.split(":")
    if not module_name or not function_name:
        raise ValueError("Factory module or function name is empty")
    module = importlib.import_module(module_name)
    factory = getattr(module, function_name)
    if not callable(factory):
        raise TypeError("Dependency factory is not callable")
    dependencies = factory(config)
    if not isinstance(dependencies, Dependencies):
        raise TypeError("Dependency factory must return Dependencies")
    methods = {
        "planner": ("create_plan", "next_decision"),
        "runtime": ("execute",),
        "verifier": ("prepare_acceptance", "next_check"),
        "recovery": ("recommend_recovery",),
        "tracer": ("open_run", "append_event", "save_checkpoint", "finalize"),
    }
    for name, required in methods.items():
        port = getattr(dependencies, name)
        for method in required:
            if not callable(getattr(port, method, None)):
                raise TypeError(f"{name} is missing method {method}")
    return dependencies


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = AgentConfig(
            run_root=args.run_root, max_runtime_seconds=args.max_runtime,
            max_steps=args.max_steps, max_commands=args.max_commands,
            max_retries_per_operation=args.max_retries,
            max_recovery_attempts=args.max_recovery_attempts,
            model_call_timeout_seconds=args.model_timeout,
            command_timeout_seconds=args.command_timeout,
            max_no_progress_cycles=args.max_no_progress,
            finalization_reserve_seconds=args.finalization_reserve,
            infrastructure_timeout_seconds=args.infrastructure_timeout,
        )
        dependencies = load_dependencies(args.factory, config)
    except Exception as exc:
        print(f"Agent startup failed: {type(exc).__name__}: {exc}\n"
              "Connect the team modules according to docs/INTERFACES.md before the live run. No mocks are enabled; development has not started.",
              file=sys.stderr)
        return 2
    try:
        summary = asyncio.run(Controller(config, dependencies).run(args.spec))
    except KeyboardInterrupt:
        print("Run interrupted; existing game/ and traces/ will not be deleted.", file=sys.stderr)
        return 130
    print(json.dumps(to_jsonable(summary), ensure_ascii=False, indent=2))
    return EXIT_CODES[summary.outcome]


if __name__ == "__main__":
    raise SystemExit(main())
