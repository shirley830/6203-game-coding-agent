"""Classify structured failures without executing tools or mutating state."""

from enum import Enum

from agent.state import FailureRecord


class ErrorClass(str, Enum):
    PERMANENT = "permanent"
    CLEANUP_FAILED = "cleanup_failed"
    UNCERTAIN = "uncertain"
    PROPOSAL = "proposal"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    VERIFIER_DEFECT = "verifier_defect"
    TEST_CONFLICT = "test_conflict"
    TRANSIENT_MODULE = "transient_module"
    MALFORMED_PROPOSAL = "malformed_proposal"
    MODULE = "module"
    UNKNOWN = "unknown"


PERMANENT_CODES = frozenset({
    "CONTEXT_INVALID", "RUNTIME_REUSE_NOT_ALLOWED", "RUNTIME_STORAGE_UNSAFE",
    "SANDBOX_UNAVAILABLE", "SPEC_NOT_FOUND", "SPEC_TOO_LARGE",
    "WORKSPACE_INVALID", "WORKSPACE_NOT_EMPTY", "TIMEOUT_INVALID",
    "input", "PERMISSION_DENIED", "AUTHENTICATION_FAILED", "CREDENTIAL_INVALID",
})
UNCERTAIN_CODES = frozenset({"COMMAND_TIMEOUT", "DEPENDENCY_TIMEOUT", "runtime"})
PROPOSAL_CODES = frozenset({
    "ACTION_INVALID", "COMMAND_INVALID", "CONTENT_INVALID", "DEPENDENCIES_INVALID",
    "DEPENDENCY_BYPASS", "DEPENDENCY_MANAGER_AMBIGUOUS", "FILE_TOO_LARGE",
    "PATCH_CONFLICT", "PATCH_INVALID", "PATH_INVALID", "PATH_OUTSIDE_WORKSPACE",
    "SHELL_NOT_ALLOWED", "SYMLINK_ESCAPE", "SYMLINK_NOT_ALLOWED",
})
EXECUTION_CODES = frozenset({
    "COMMAND_NOT_FOUND", "DEPENDENCY_INSTALL_FAILED", "INVALID_ENCODING",
    "NONZERO_EXIT", "PATH_NOT_FOUND",
})


# Exception type names (litellm/openai/anthropic SDKs and asyncio) that are
# worth another proposal-only attempt.
TRANSIENT_MODULE_ERRORS = frozenset({
    "TimeoutError", "ConnectionError", "RateLimitError", "Timeout",
    "APIConnectionError", "APITimeoutError", "ServiceUnavailableError",
    "InternalServerError", "OverloadedError",
})
MALFORMED_OUTPUT_ERRORS = frozenset({
    "ValidationError", "ValueError", "JSONDecodeError", "TypeError", "KeyError",
})


def classify_failure(failure: FailureRecord) -> ErrorClass:
    """Use category codes first; exception prefixes only for module failures.

    Exception text is produced by the controller as TypeName: message. Do not
    scan arbitrary tool output for words that could override policy.
    """
    code = failure.category
    if code == "PROCESS_CLEANUP_FAILED":
        return ErrorClass.CLEANUP_FAILED
    if code in PERMANENT_CODES:
        return ErrorClass.PERMANENT
    # Member 1 may promote a confirmed verifier UNKNOWN/script marker to this
    # category. It is a test defect, never an instruction to repair the game.
    # A tool action or another module cannot claim this verifier-only signal.
    # Even uncertain side effects require stopping here, not another game edit.
    if (code == "test_script_error" and failure.phase.value == "verifying"
            and failure.operation_key == "verifier.next_check"
            and failure.action is None):
        return ErrorClass.VERIFIER_DEFECT
    # Member 1: a failed spec-derived check is disputed (planner dispute_check, or
    # the same failure after a game change). Only the controller emits this,
    # without a tool action, from a decision or a verification report.
    if (code == "test_conflict" and failure.action is None and not failure.side_effects_unknown
            and failure.operation_key in {"planner.next_decision", "verifier.next_check"}
            and failure.phase.value in {"deciding", "verifying"}):
        return ErrorClass.TEST_CONFLICT
    if failure.side_effects_unknown or code in UNCERTAIN_CODES:
        return ErrorClass.UNCERTAIN
    # Explicit provenance only: game output may contain the same exception names.
    if (failure.phase.value in {"initializing", "verifying"}
            and failure.operation_key.startswith("verifier.")
            and code in {"VERIFIER_SCRIPT_ERROR", "ACCEPTANCE_PARSE_ERROR",
                         "VERIFIER_CONTRACT_ERROR"}):
        return ErrorClass.VERIFIER_DEFECT
    if code == "verification":
        return ErrorClass.VERIFICATION
    if code in PROPOSAL_CODES:
        return ErrorClass.PROPOSAL
    if code in EXECUTION_CODES:
        return ErrorClass.EXECUTION
    if code == "contract":
        # A rejected proposal can be regenerated. Runtime contract violations
        # or malformed verifier results need integration fixes instead.
        return (ErrorClass.PROPOSAL if failure.operation_key.startswith("planner.")
                or failure.operation_key in {"tool.schema", "tool.policy"}
                else ErrorClass.PERMANENT)
    if code == "module":
        name = failure.message.partition(":")[0].strip()
        if name in {"PermissionError", "AuthenticationError", "UnauthorizedError"}:
            return ErrorClass.PERMANENT
        if (failure.operation_key.startswith("verifier.")
                and name in {"NameError", "SyntaxError", "UnboundLocalError"}):
            return ErrorClass.VERIFIER_DEFECT
        if name in TRANSIENT_MODULE_ERRORS:
            return ErrorClass.TRANSIENT_MODULE
        if failure.operation_key.startswith("planner.") and name in MALFORMED_OUTPUT_ERRORS:
            # The model produced unparsable/invalid JSON: regenerate, never abort.
            return ErrorClass.MALFORMED_PROPOSAL
        return ErrorClass.MODULE
    return ErrorClass.UNKNOWN
