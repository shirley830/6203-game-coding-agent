"""Controller-only, bounded source snapshots; never offered to the planner.

Snapshots live in trace-owned storage, outside the generated command's writable
runtime directory. Symlinks/special files are rejected. Dependencies are not
rolled back; the controller invalidates evidence if the resulting revision differs.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time
from uuid import uuid4

from .errors import RuntimeToolError
from .revision import IGNORED_DIRECTORY_NAMES, IGNORED_FILE_SUFFIXES


class GameVersions:
    def __init__(self, game_dir: Path, trace_dir: Path, *, max_bytes=64 * 1024 * 1024,
                 max_files=10000):
        self.game = Path(game_dir)
        self.trace = Path(trace_dir)
        self.max_bytes = max_bytes
        self.max_files = max_files

    def _root(self):
        # Reject symlinks in every existing ancestor of the trace path.
        for path in (self.trace, *self.trace.parents):
            if path.is_symlink():
                raise RuntimeToolError("VERSION_UNSAFE", "Trace storage contains a symlink")
        root = self.trace / "game_versions"
        if root.is_symlink():
            raise RuntimeToolError("VERSION_UNSAFE", "Version store must not be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root

    @staticmethod
    def _check(deadline):
        if time.monotonic() >= deadline:
            raise RuntimeToolError("VERSION_TIMEOUT", "Version operation exceeded its deadline")

    def _inventory(self, root: Path, deadline):
        if root.is_symlink() or not root.is_dir():
            raise RuntimeToolError("VERSION_UNSAFE", "Game/snapshot root is not a real directory")
        records = []
        total = 0
        for current, dirs, files in os.walk(root, followlinks=False):
            self._check(deadline)
            for name in dirs:
                if (Path(current) / name).is_symlink():
                    raise RuntimeToolError("VERSION_UNSAFE", "Source symlink rejected")
            dirs[:] = sorted(name for name in dirs if name not in IGNORED_DIRECTORY_NAMES)
            for name in dirs:
                path = Path(current) / name
                records.append({"path": path.relative_to(root).as_posix(), "type": "directory",
                                "mode": stat.S_IMODE(path.stat().st_mode)})
                if len(records) > self.max_files:
                    raise RuntimeToolError("VERSION_TOO_LARGE", "Snapshot entry limit exceeded")
            for name in sorted(files):
                self._check(deadline)
                if name.endswith(IGNORED_FILE_SUFFIXES):
                    continue
                path = Path(current) / name
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise RuntimeToolError("VERSION_UNSAFE", "Source symlink/special file rejected")
                total += info.st_size
                if total > self.max_bytes or len(records) >= self.max_files:
                    raise RuntimeToolError("VERSION_TOO_LARGE", "Snapshot limits exceeded")
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        self._check(deadline)
                        digest.update(chunk)
                records.append({"path": path.relative_to(root).as_posix(), "type": "file",
                                "sha256": digest.hexdigest(), "mode": stat.S_IMODE(info.st_mode)})
        return records

    def save(self, *, timeout_seconds: float):
        deadline = time.monotonic() + timeout_seconds
        records = self._inventory(self.game, deadline)
        root = self._root()
        version = uuid4().hex
        pending = root / (".pending-" + version)
        pending.mkdir(mode=0o700)
        (pending / "source").mkdir()
        try:
            for record in records:
                self._check(deadline)
                target = pending / "source" / record["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if record["type"] == "directory":
                    target.mkdir(exist_ok=True)
                    target.chmod(record["mode"])
                else:
                    shutil.copy2(self.game / record["path"], target)
            if self._inventory(pending / "source", deadline) != records:
                raise RuntimeToolError("VERSION_UNSAFE", "Source changed while snapshotting")
            (pending / "manifest.json").write_text(json.dumps(records), encoding="utf-8")
            pending.rename(root / version)
        except BaseException:
            shutil.rmtree(pending, ignore_errors=True)
            raise
        return version

    def restore(self, version: str, *, timeout_seconds: float):
        deadline = time.monotonic() + timeout_seconds
        if len(version) != 32 or any(c not in "0123456789abcdef" for c in version):
            raise RuntimeToolError("VERSION_UNSAFE", "Invalid version ID")
        root = self._root() / version
        if root.is_symlink() or (root / "manifest.json").is_symlink():
            raise RuntimeToolError("VERSION_UNSAFE", "Snapshot metadata symlink rejected")
        records = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if self._inventory(root / "source", deadline) != records:
            raise RuntimeToolError("VERSION_UNSAFE", "Snapshot hashes/modes do not match manifest")
        current = self._inventory(self.game, deadline)
        expected = {r["path"] for r in records}
        expected_types = {r["path"]: r["type"] for r in records}
        # Preserve the losing source as a new snapshot before replacing anything.
        previous = self.save(timeout_seconds=max(0, deadline - time.monotonic()))
        # Stage the entire replacement before touching live game/. Dependencies
        # remain current, not rolled back. Abort if copying exceeds the bound.
        stage = self._root() / (".restore-" + uuid4().hex)
        copied = 0
        def copy_bounded(source, target):
            nonlocal copied
            self._check(deadline)
            copied += Path(source).stat().st_size
            if copied > self.max_bytes:
                raise RuntimeToolError("VERSION_TOO_LARGE", "Restore copy limit exceeded")
            return shutil.copy2(source, target)
        try:
            shutil.copytree(self.game, stage, symlinks=True, copy_function=copy_bounded)
            for record in current:
                self._check(deadline)
                if record["type"] == "file" and expected_types.get(record["path"]) != "file":
                    (stage / record["path"]).unlink()
            source_dirs = {Path(record["path"]) for record in current if record["type"] == "directory"}
            for relative in sorted(source_dirs, key=lambda p: len(p.parts), reverse=True):
                if expected_types.get(relative.as_posix()) == "directory":
                    continue
                try:
                    (stage / relative).rmdir()
                except OSError:
                    pass
            for record in records:
                self._check(deadline)
                target = stage / record["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if record["type"] == "directory":
                    target.mkdir(exist_ok=True)
                    target.chmod(record["mode"])
                else:
                    copy_bounded(root / "source" / record["path"], target)
            if self._inventory(stage, deadline) != records:
                raise RuntimeToolError("VERSION_UNSAFE", "Restored source differs from saved snapshot")
            self._check(deadline)
            retained = self._root() / ("retired-" + uuid4().hex)
            self.game.rename(retained)
            try:
                stage.rename(self.game)
            except BaseException:
                retained.rename(self.game)
                raise
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return previous
