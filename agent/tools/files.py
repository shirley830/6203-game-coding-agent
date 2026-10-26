"""Safe, atomic file operations scoped to ``game/``."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any

from .errors import RuntimeToolError
from .revision import IGNORED_DIRECTORY_NAMES, workspace_revision


_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
)


class WorkspaceFiles:
    """Filesystem operations that independently enforce the controller scope."""

    def __init__(self, game_dir: Path, environment_record: Path, *, max_read_bytes: int) -> None:
        self.game_dir = Path(game_dir)
        self.environment_record = Path(environment_record)
        self.max_read_bytes = max_read_bytes

    def revision(self) -> str:
        return workspace_revision(self.game_dir, self.environment_record)

    def prepare(self) -> None:
        if self.game_dir.is_symlink():
            raise RuntimeToolError("SYMLINK_ESCAPE", "game/ must not be a symbolic link")
        if self.game_dir.exists():
            if not self.game_dir.is_dir():
                raise RuntimeToolError("WORKSPACE_INVALID", "game/ exists but is not a directory")
            if any(self.game_dir.iterdir()):
                raise RuntimeToolError(
                    "WORKSPACE_NOT_EMPTY",
                    "Refusing to overwrite an existing non-empty game/ directory",
                )
        else:
            self.game_dir.mkdir(parents=False)

    @staticmethod
    def _validate_relative(value: Any, *, allow_root: bool = False) -> PurePosixPath:
        if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
            raise RuntimeToolError("PATH_INVALID", "Path must be a non-empty POSIX relative path")
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or ":" in value:
            raise RuntimeToolError("PATH_OUTSIDE_WORKSPACE", "Path is outside the game workspace")
        if not allow_root and not relative.parts:
            raise RuntimeToolError("PATH_INVALID", "A file path cannot name the workspace root")
        return relative

    def resolve(self, value: Any, *, allow_root: bool = False) -> Path:
        relative = self._validate_relative(value, allow_root=allow_root)
        if not self.game_dir.is_dir() or self.game_dir.is_symlink():
            raise RuntimeToolError("WORKSPACE_INVALID", "game/ is not a safe directory")
        root = self.game_dir.resolve()
        candidate = self.game_dir.joinpath(*relative.parts)
        current = self.game_dir
        if current.is_symlink():
            raise RuntimeToolError("SYMLINK_ESCAPE", "game/ must not be a symbolic link")
        for part in relative.parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeToolError(
                    "SYMLINK_NOT_ALLOWED",
                    f"Symbolic links are not accepted in tool paths: {value}",
                )
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise RuntimeToolError("SYMLINK_ESCAPE", "Resolved path escapes the game workspace")
        return candidate

    def _ensure_parent(self, path: Path) -> None:
        root = self.game_dir.resolve()
        relative_parent = path.parent.relative_to(self.game_dir)
        current = self.game_dir
        for part in relative_parent.parts:
            current = current / part
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise RuntimeToolError(
                        "PATH_INVALID", f"Parent component is not a safe directory: {part}"
                    )
            else:
                current.mkdir()
            if not current.resolve().is_relative_to(root):
                raise RuntimeToolError("SYMLINK_ESCAPE", "Parent directory escaped game/")

    def read_text(self, relative: Any) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise RuntimeToolError("PATH_NOT_FOUND", f"File does not exist: {relative}")
        size = path.stat().st_size
        if size > self.max_read_bytes:
            raise RuntimeToolError(
                "FILE_TOO_LARGE",
                f"File is {size} bytes; read limit is {self.max_read_bytes} bytes",
                data={"path": str(relative), "size": size},
            )
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeToolError(
                "INVALID_ENCODING", f"File is not valid UTF-8: {relative}"
            ) from exc

    def write_text(self, relative: Any, content: Any) -> int:
        if not isinstance(content, str):
            raise RuntimeToolError("CONTENT_INVALID", "File content must be a string")
        path = self.resolve(relative)
        if path.exists() and not path.is_file():
            raise RuntimeToolError("PATH_INVALID", f"Target is not a regular file: {relative}")
        self._ensure_parent(path)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() and path.is_file() else 0o644
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_name = stream.name
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, mode)
            os.replace(temporary_name, path)
            temporary_name = None
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
        return len(content.encode("utf-8"))

    def patch_text(self, relative: Any, patch: Any) -> int:
        if not isinstance(patch, str):
            raise RuntimeToolError("PATCH_INVALID", "Patch must be a unified diff string")
        original = self.read_text(relative)
        updated = apply_unified_diff(original, patch)
        return self.write_text(relative, updated)

    def inspect(self, *, max_entries: int) -> dict[str, Any]:
        if not self.game_dir.is_dir() or self.game_dir.is_symlink():
            raise RuntimeToolError("WORKSPACE_INVALID", "game/ is not a safe directory")
        entries: list[dict[str, Any]] = []
        truncated = False
        entry_count = 0
        root = self.game_dir.resolve()
        for current, directories, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(
                name for name in directories if name not in IGNORED_DIRECTORY_NAMES
            )
            for name in [*directories, *sorted(files)]:
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    target = path.resolve(strict=False)
                    if not target.is_relative_to(root):
                        raise RuntimeToolError(
                            "SYMLINK_ESCAPE", f"Workspace contains an escaping symlink: {relative}"
                        )
                    item = {"path": relative, "type": "symlink", "target": os.readlink(path)}
                elif stat.S_ISDIR(metadata.st_mode):
                    item = {"path": relative, "type": "directory"}
                elif stat.S_ISREG(metadata.st_mode):
                    item = {"path": relative, "type": "file", "size": metadata.st_size}
                else:
                    item = {"path": relative, "type": "special"}
                entry_count += 1
                if len(entries) < max_entries:
                    entries.append(item)
                else:
                    truncated = True
        return {"entries": entries, "truncated": truncated, "entry_count": entry_count}


def apply_unified_diff(original: str, patch: str) -> str:
    """Apply one all-or-nothing unified diff to text.

    The strict reader is tried first. Models often get hunk line numbers or
    counts slightly wrong, so a rejected hunk is then located by its content:
    its context and removed lines must appear exactly once (searching forward
    from the previous hunk). Nothing is written unless every hunk applies.
    """
    try:
        return _apply_unified_diff_strict(original, patch)
    except RuntimeToolError as strict_error:
        if "one file" in strict_error.message or "+++ header" in strict_error.message:
            raise
        return _apply_unified_diff_by_content(original, patch)


def _apply_unified_diff_by_content(original: str, patch: str) -> str:
    lines = [line for line in patch.splitlines() if line != "\\ No newline at end of file"]
    while lines and lines[0].startswith(("--- ", "+++ ")):
        lines.pop(0)
    hunks: list[tuple[int | None, list[str], list[str]]] = []
    for line in lines:
        if line.startswith("@@"):
            match = _HUNK_HEADER.match(line)
            hunks.append((int(match.group(1)) - 1 if match else None, [], []))
            continue
        if not hunks:
            raise RuntimeToolError("PATCH_INVALID", "Patch contains no hunks")
        marker, value = (line[0], line[1:]) if line else (" ", "")
        if marker not in " +-":
            raise RuntimeToolError("PATCH_INVALID", f"Invalid patch line: {line}")
        if marker in " -":
            hunks[-1][1].append(value)
        if marker in " +":
            hunks[-1][2].append(value)
    if not hunks:
        raise RuntimeToolError("PATCH_INVALID", "Patch contains no hunks")
    source = original.splitlines()
    cursor = 0
    for hint, old, new in hunks:
        if not old:
            raise RuntimeToolError(
                "PATCH_CONFLICT", "A hunk without context cannot be placed; include unchanged lines")
        wanted = [line.rstrip() for line in old]
        starts = [i for i in range(cursor, len(source) - len(old) + 1)
                  if [line.rstrip() for line in source[i:i + len(old)]] == wanted]
        if not starts:
            raise RuntimeToolError(
                "PATCH_CONFLICT", "Patch context does not match the file; read_file and send a "
                "new patch, or rewrite the file with write_file")
        if len(starts) > 1 and (hint is None or hint not in starts):
            raise RuntimeToolError(
                "PATCH_CONFLICT", "Patch context matches several places; include more unchanged lines")
        start = hint if len(starts) > 1 else starts[0]
        source[start:start + len(old)] = new
        cursor = start + len(new)
    result = "\n".join(source)
    if original.endswith("\n"):
        result += "\n"
    return result


def _apply_unified_diff_strict(original: str, patch: str) -> str:
    """Strict reader: hunk headers must match the file exactly."""

    patch_lines = patch.splitlines()
    index = 0
    if index < len(patch_lines) and patch_lines[index].startswith("--- "):
        index += 1
        if index >= len(patch_lines) or not patch_lines[index].startswith("+++ "):
            raise RuntimeToolError("PATCH_INVALID", "Unified diff is missing the +++ header")
        index += 1
    source = original.splitlines()
    output: list[str] = []
    source_index = 0
    saw_hunk = False
    while index < len(patch_lines):
        line = patch_lines[index]
        if line.startswith(("--- ", "+++ ")):
            raise RuntimeToolError("PATCH_INVALID", "Only one file may be patched per action")
        match = _HUNK_HEADER.match(line)
        if not match:
            raise RuntimeToolError("PATCH_INVALID", f"Invalid hunk header: {line}")
        saw_hunk = True
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_count = int(match.group(4) or "1")
        target_index = max(old_start - 1, 0)
        if target_index < source_index or target_index > len(source):
            raise RuntimeToolError("PATCH_CONFLICT", "Patch hunk is out of range")
        output.extend(source[source_index:target_index])
        source_index = target_index
        index += 1
        consumed = produced = 0
        while index < len(patch_lines) and not patch_lines[index].startswith("@@ "):
            patch_line = patch_lines[index]
            if patch_line == "\\ No newline at end of file":
                index += 1
                continue
            if not patch_line or patch_line[0] not in " +-":
                raise RuntimeToolError("PATCH_INVALID", f"Invalid patch line: {patch_line}")
            marker, value = patch_line[0], patch_line[1:]
            if marker in " -":
                if source_index >= len(source) or source[source_index] != value:
                    raise RuntimeToolError("PATCH_CONFLICT", "Patch context does not match the file")
                source_index += 1
                consumed += 1
            if marker in " +":
                output.append(value)
                produced += 1
            index += 1
        if consumed != old_count or produced != new_count:
            raise RuntimeToolError("PATCH_INVALID", "Patch hunk counts do not match its header")
    if not saw_hunk:
        raise RuntimeToolError("PATCH_INVALID", "Patch contains no hunks")
    output.extend(source[source_index:])
    result = "\n".join(output)
    if original.endswith("\n"):
        result += "\n"
    return result
