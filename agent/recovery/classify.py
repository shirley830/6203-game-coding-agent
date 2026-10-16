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
    if failure.side_effects_unknown or code in UNCERTAIN_CODES:
        return ErrorClass.UNCERTAIN
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
        if name in TRANSIENT_MODULE_ERRORS:
            return ErrorClass.TRANSIENT_MODULE
        if failure.operation_key.startswith("planner.") and name in MALFORMED_OUTPUT_ERRORS:
            # The model produced unparsable/invalid JSON: regenerate, never abort.
            return ErrorClass.MALFORMED_PROPOSAL
        return ErrorClass.MODULE
    return ErrorClass.UNKNOWN
