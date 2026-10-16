"""Deterministic workspace revision calculation."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat


IGNORED_DIRECTORY_NAMES = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
})
IGNORED_FILE_SUFFIXES = (".pyc", ".pyo")


def _feed(hasher: "hashlib._Hash", kind: str, relative: str, payload: bytes = b"") -> None:
    hasher.update(kind.encode("ascii"))
    hasher.update(b"\0")
    hasher.update(relative.encode("utf-8", errors="surrogateescape"))
    hasher.update(b"\0")
    hasher.update(payload)
    hasher.update(b"\0")


def workspace_revision(game_dir: Path, environment_record: Path | None = None) -> str:
    """Hash relevant game content and the managed dependency record.

    Volatile caches and dependency trees are represented by their manifests and
    the environment record rather than by every generated file.
    """

    root = Path(game_dir).resolve()
    digest = hashlib.sha256(b"pe6203-workspace-revision-v1\0")
    if not root.exists():
        _feed(digest, "missing", ".")
    else:
        for current, directories, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            kept_directories: list[str] = []
            for name in sorted(directories):
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                if name in IGNORED_DIRECTORY_NAMES:
                    continue
                elif path.is_symlink():
                    _feed(digest, "symlink", relative, os.readlink(path).encode("utf-8"))
                else:
                    kept_directories.append(name)
                    _feed(digest, "dir", relative)
            directories[:] = kept_directories
            for name in sorted(files):
                if name.endswith(IGNORED_FILE_SUFFIXES):
                    continue
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    _feed(digest, "symlink", relative, os.readlink(path).encode("utf-8"))
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    _feed(digest, "special", relative)
                    continue
                digest.update(b"file\0" + relative.encode("utf-8") + b"\0")
                digest.update(b"x" if metadata.st_mode & stat.S_IXUSR else b"-")
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                digest.update(b"\0")
    if environment_record is not None and environment_record.is_file():
        _feed(digest, "environment", "managed-environment", environment_record.read_bytes())
    else:
        _feed(digest, "environment", "managed-environment", b"none")
    return digest.hexdigest()
