from collections import deque
from pathlib import PurePosixPath

from agent.state import (
    AgentView,
    CallLimits,
    Plan,
    RequestVerification,
    RevisePlan,
    ToolAction,
    ToolKind,
    ToolResult,
    ToolStatus,
)

from .llm_client import LLMClient
from .parser import parse_decision, parse_plan
from .prompts import build_decision_prompt, build_plan_prompt


HISTORY_LIMIT = 25
# Current file texts shown in every decision prompt, so the model never spends a
# slow decision on read_file (Kimi K3 rehearsals lost ~200 s re-reading files).
FILE_TEXT_LIMIT = 40000
# After the first verification, this many consecutive edits without asking for
# verification triggers one: live runs showed weaker models rewriting the same
# file dozens of times without fresh feedback until the budget ran out.
MAX_EDITS_BEFORE_VERIFICATION = 8
# Consecutive empty decision objects absorbed before normal recovery takes over.
MAX_EMPTY_REPLIES = 2


def _is_empty_decision(model_response: str) -> bool:
    import json
    from .parser import extract_json_object, normalize_decision
    try:
        payload = json.loads(extract_json_object(model_response))
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and "decision" not in (normalize_decision(payload) or {})


_EDIT_KINDS = {ToolKind.WRITE_FILE, ToolKind.PATCH_FILE, ToolKind.INSTALL_DEPENDENCIES}
_SOURCE_SUFFIXES = (".html", ".htm", ".js", ".mjs", ".py", ".css")


class LLMPlanner:
    """Planning and decision port.

    The controller only shows the latest tool result, so the planner keeps its
    own working memory (files in game/, recent actions, guard notes) and feeds
    it back into every decision prompt. Memory never changes controller state.
    """

    def __init__(self, client: LLMClient, *, environment_notes: list[str] | None = None,
                 decision_client: LLMClient | None = None) -> None:
        self.client = client
        # Step-by-step decisions may use a separately configured client (for
        # example a lower reasoning cap); planning keeps the main client.
        self.decision_client = decision_client or client
        # Facts about this machine from the startup self-check (e.g. no node).
        self.environment_notes = list(environment_notes or [])
        self._sequence = 0
        self._pending: dict[str, ToolAction] = {}
        self._files: dict[str, int] = {}
        self._texts: dict[str, str] = {}
        self._history: deque[str] = deque(maxlen=HISTORY_LIMIT)
        self._notes: deque[str] = deque(maxlen=5)
        self._seen_result: str | None = None
        self._edits_since_verification = 0
        self._packages: list[str] = []
        self._empty_replies = 0
        self._seen_failure: object | None = None

    async def create_plan(
        self,
        view: AgentView,
        limits: CallLimits,
    ) -> Plan:
        if view.suite is None:
            raise ValueError(
                "Acceptance suite must exist before planning"
            )

        model_response = await self.client.generate(
            system_prompt=(
                "You are the planning component of an autonomous "
                "game coding agent. Follow the supplied contract and "
                "return JSON only. Do not execute tools or hide retries."
            ),
            user_prompt=build_plan_prompt(view),
            timeout_seconds=limits.timeout_seconds,
        )

        return parse_plan(
            model_response=model_response,
            suite=view.suite,
        )

    async def next_decision(
        self,
        view: AgentView,
        limits: CallLimits,
    ) -> ToolAction | RequestVerification | RevisePlan:
        if view.plan is None:
            return RevisePlan(
                reason="No development plan exists"
            )

        self._observe(view)
        if (view.launch_command
                and self._edits_since_verification >= MAX_EDITS_BEFORE_VERIFICATION):
            self._history.append(
                f"planner: {self._edits_since_verification} edits since the last verification; "
                "requesting verification for fresh feedback")
            return self._guard_verification(RequestVerification(view.launch_command))
        model_response = await self.decision_client.generate(
            system_prompt=(
                "You are the decision component of an autonomous "
                "game coding agent. You write complete, working game code. "
                "Select exactly one next action and return JSON only. "
                "Do not execute tools, change controller state, or hide retries."
            ),
            user_prompt=build_decision_prompt(
                view, files=self._files, texts=self._texts, history=list(self._history),
                notes=list(self._notes),
                environment=self.environment_notes,
            ),
            timeout_seconds=limits.timeout_seconds,
        )

        if _is_empty_decision(model_response) and self._empty_replies < MAX_EMPTY_REPLIES:
            # GLM-5.3 occasionally answers "{}". Rather than spend a recovery
            # attempt, refresh the workspace view and ask again next step.
            self._empty_replies += 1
            self._notes.append("Your previous reply was an empty or unrecognised JSON object. "
                               "Reply with one complete decision object as specified.")
            return self._register(ToolAction("guard", ToolKind.INSPECT_WORKSPACE, {},
                                             "Previous model reply was empty; refresh the file list"))
        decision = parse_decision(
            model_response=model_response,
        )
        self._empty_replies = 0
        if isinstance(decision, ToolAction):
            return self._register(decision)
        if isinstance(decision, RequestVerification):
            return self._guard_verification(decision)
        return decision

    # ----------------------------------------------------------- memory

    def _observe(self, view: AgentView) -> None:
        result = view.last_result
        if result is not None and result.action_id != self._seen_result:
            self._seen_result = result.action_id
            self._ingest(result)
        failure = view.last_failure
        if failure is not None and failure is not self._seen_failure:
            self._seen_failure = failure
            self._history.append(
                f"FAILURE [{failure.category}] {failure.operation_key}: "
                f"{' '.join(failure.message.split())[:300]}"
            )

    def _ingest(self, result: ToolResult) -> None:
        action = self._pending.pop(result.action_id, None)
        first_line = (result.observation or "").strip().splitlines()[:1]
        summary = first_line[0][:200] if first_line else ""
        if action is None:
            # Controller- or verifier-issued action (bootstrap, verification checks).
            self._history.append(f"{result.action_id} -> {result.status.value}: {summary}")
            if result.action_id.startswith("verify.") and "HARNESS_JSON=" in result.observation:
                self._history.append("verification harness ran; its JSON is in LAST TOOL RESULT")
            entries = result.data.get("entries")
            if isinstance(entries, (list, tuple)):
                self._refresh_files(entries)
            return

        if result.status == ToolStatus.OK and action.kind == ToolKind.INSTALL_DEPENDENCIES:
            for package in action.arguments.get("packages", ()):
                if package not in self._packages:
                    self._packages.append(package)
        if result.status == ToolStatus.OK and action.kind in _EDIT_KINDS:
            self._edits_since_verification += 1
        label = self._describe(action)
        self._history.append(f"{action.action_id} {label} -> {result.status.value}: {summary}")
        if result.status != ToolStatus.OK:
            return
        path = action.arguments.get("path")
        if action.kind == ToolKind.WRITE_FILE and isinstance(path, str):
            content = action.arguments.get("content", "")
            self._files[path] = len(content.encode("utf-8"))
            if len(content) <= FILE_TEXT_LIMIT:
                self._texts[path] = content
        elif action.kind == ToolKind.PATCH_FILE and isinstance(path, str):
            self._files[path] = int(result.data.get("bytes", self._files.get(path, 0)) or 0)
            self._texts[path] = self._patched_text(path, action.arguments.get("patch"))
            if self._texts[path] is None:
                self._texts.pop(path, None)
        elif action.kind == ToolKind.READ_FILE and isinstance(path, str):
            text = result.data.get("text")
            if isinstance(text, str):
                self._files[path] = len(text.encode("utf-8"))
                if len(text) <= FILE_TEXT_LIMIT:
                    self._texts[path] = text
        elif action.kind == ToolKind.INSPECT_WORKSPACE:
            entries = result.data.get("entries")
            if isinstance(entries, (list, tuple)):
                self._refresh_files(entries)

    def _patched_text(self, path: str, patch: object) -> str | None:
        """Apply the accepted patch to the remembered text (same pure function as the runtime)."""
        from agent.tools.files import apply_unified_diff
        old = self._texts.get(path)
        if old is None or not isinstance(patch, str):
            return None
        try:
            new = apply_unified_diff(old, patch)
        except Exception:  # noqa: BLE001 - unknown result: the model reads it instead
            return None
        return new if len(new) <= FILE_TEXT_LIMIT else None

    def _refresh_files(self, entries: object) -> None:
        files: dict[str, int] = {}
        for entry in entries:  # type: ignore[union-attr]
            try:
                if entry.get("type") == "file":
                    files[str(entry["path"])] = int(entry.get("size", 0))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
        self._files = files
        self._texts = {k: v for k, v in self._texts.items() if k in files}

    @staticmethod
    def _describe(action: ToolAction) -> str:
        args = action.arguments
        if "path" in args:
            return f"{action.kind.value} {args['path']}"
        if "argv" in args:
            return f"{action.kind.value} {' '.join(args['argv'])}"
        if "packages" in args:
            return f"{action.kind.value} {' '.join(args['packages'])}"
        return action.kind.value

    def _next_id(self, kind: ToolKind) -> str:
        self._sequence += 1
        return f"dev.{self._sequence:03d}.{kind.value}"

    def _register(self, action: ToolAction) -> ToolAction:
        # The planner, not the model, owns action IDs: duplicates are rejected
        # by the controller and models reuse example IDs.
        arguments = dict(action.arguments)
        path = arguments.get("path")
        if isinstance(path, str):
            normalized = path.strip().removeprefix("./").removeprefix("game/")
            arguments["path"] = normalized
        registered = ToolAction(self._next_id(action.kind), action.kind, arguments, action.rationale)
        self._pending[registered.action_id] = registered
        return registered

    # ----------------------------------------------------------- guards

    def _guard_verification(
        self, decision: RequestVerification,
    ) -> ToolAction | RequestVerification:
        launch = decision.launch_command
        missing = [
            token for token in launch
            if token.lower().endswith(_SOURCE_SUFFIXES)
            and not PurePosixPath(token).is_absolute()
            and token not in self._files
        ]
        if "http.server" in launch and "index.html" not in self._files:
            missing.append("index.html")
        if missing:
            self._notes.append(
                "Verification was refused: the launch command needs "
                f"{', '.join(sorted(set(missing)))}, which is not in game/. "
                "Create it (or fix the launch command) first."
            )
            return self._register(ToolAction("guard", ToolKind.INSPECT_WORKSPACE, {},
                                             "Refresh the file list before verification"))

        command = " ".join(launch)
        readme = self._texts.get("README.md")
        if readme is None or command not in readme:
            # Launch documentation inside game/ is required; write it
            # deterministically so it always matches the verified command.
            self._notes.append("README.md was (re)written with the exact launch command.")
            return self._register(ToolAction(
                "guard", ToolKind.WRITE_FILE,
                {"path": "README.md", "content": self._readme(launch, readme, self._packages,
                                                             "requirements.txt" in self._files)},
                "Launch documentation must contain the exact launch command",
            ))
        self._edits_since_verification = 0
        return decision

    @staticmethod
    def _readme(launch: tuple[str, ...], previous: str | None,
                packages: list[str] | None = None, has_requirements: bool = False) -> str:
        command = " ".join(launch)
        http = "http.server" in launch
        port = next((t for t in launch if t.isdigit()), "8000")
        lines = ["# Generated game", "", "Generated autonomously by the PE6203 coding agent.", "",
                 "## Requirements", ""]
        if http:
            lines += ["- Python 3.11+ (only the standard library is used to serve the game)",
                      "- A modern desktop browser (Chrome, Edge, Firefox or Safari)", "",
                      "No dependency installation is required.", ""]
        else:
            lines += ["- Python 3.11+", ""]
            if has_requirements:
                lines += ["Install the dependencies once:", "", "```bash",
                          "python3 -m pip install -r requirements.txt", "```", ""]
            elif packages:
                lines += ["Install the dependencies once:", "", "```bash",
                          "python3 -m pip install " + " ".join(packages), "```", ""]
            else:
                lines += ["No dependency installation is required (standard library only).", ""]
        lines += ["## Start the game", "", "From this `game/` directory run:", "",
                  "```bash", command, "```", ""]
        if http:
            lines += [f"Then open http://127.0.0.1:{port}/ in a browser.", ""]
        if previous and command not in previous:
            lines += ["## Notes from development", "", previous.strip(), ""]
        return "\n".join(lines) + "\n"
