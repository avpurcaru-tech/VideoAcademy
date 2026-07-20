from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .contracts import ProductionRecord


class RuntimeCleanupError(RuntimeError):
    """Base error with no file contents or underlying exception details."""


class CleanupRootError(RuntimeCleanupError): pass
class CleanupCandidateSafetyError(RuntimeCleanupError): pass
class CleanupConfirmationError(RuntimeCleanupError): pass


class CleanupCategory(str, Enum):
    ATOMIC_PART_FILE = "atomic-part-file"
    ASSEMBLY_WORKSPACE = "assembly-workspace"
    SMOKE_MEDIA_WORKSPACE = "smoke-media-workspace"


@dataclass(frozen=True)
class CleanupEntry:
    path: Path
    category: CleanupCategory
    reason: str
    age_seconds: float
    byte_size: int


@dataclass(frozen=True)
class CleanupPlan:
    runtime_root: Path
    entries: tuple[CleanupEntry, ...]
    scanned_count: int
    older_than_seconds: float | None

    @property
    def candidate_count(self) -> int: return len(self.entries)

    @property
    def recoverable_bytes(self) -> int: return sum(entry.byte_size for entry in self.entries)


@dataclass(frozen=True)
class CleanupResult:
    scanned_count: int
    candidate_count: int
    deleted_count: int
    failed_count: int
    recoverable_bytes: int
    recovered_bytes: int


class RuntimeCleanupService:
    """Conservative cleanup for explicitly recognized disposable runtime paths."""

    _PART_ROOTS = (("productions",), ("requests",), ("kling", "tasks"), ("music", "tasks"), ("media",))

    def scan(self, runtime_root: Path, older_than_seconds: float | None = None) -> CleanupPlan:
        threshold = self._threshold(older_than_seconds)
        root = self._runtime_root(runtime_root)
        protected = self._protected_paths(root)
        entries: list[CleanupEntry] = []
        scanned = 0

        for components in self._PART_ROOTS:
            approved = root.joinpath(*components)
            if not approved.exists():
                continue
            self._require_safe_directory(approved, root)
            for directory, names, files in os.walk(approved, followlinks=False):
                current = Path(directory)
                for name in tuple(names):
                    child = current / name
                    scanned += 1
                    if child.is_symlink():
                        names.remove(name)
                        if self._looks_disposable_directory(child, root):
                            raise CleanupCandidateSafetyError("Cleanup candidate is outside approved runtime roots.")
                        continue
                    classified = self._classify_directory(child, root)
                    if classified is not None:
                        names.remove(name)
                        entry = self._entry(child, *classified)
                        if self._eligible(entry, threshold, protected): entries.append(entry)
                for name in files:
                    path = current / name
                    scanned += 1
                    if not self._is_part_name(name): continue
                    if path.is_symlink():
                        raise CleanupCandidateSafetyError("Cleanup candidate is outside approved runtime roots.")
                    entry = self._entry(path, CleanupCategory.ATOMIC_PART_FILE,
                                        "stale atomic-writer temporary file")
                    if self._eligible(entry, threshold, protected): entries.append(entry)

        unique = {entry.path: entry for entry in entries}
        return CleanupPlan(root, tuple(unique[path] for path in sorted(unique, key=str)), scanned, threshold)

    def execute(self, plan: CleanupPlan) -> CleanupResult:
        if plan.older_than_seconds is None:
            raise CleanupConfirmationError("Cleanup confirmation requires an age threshold.")
        root = self._runtime_root(plan.runtime_root)
        protected = self._protected_paths(root)
        deleted = failed = recovered = 0
        for entry in plan.entries:
            try:
                self._validate_planned_entry(entry, root, protected, plan.older_than_seconds)
                if entry.path.is_dir(): shutil.rmtree(entry.path)
                else: entry.path.unlink()
                deleted += 1; recovered += entry.byte_size
            except (OSError, RuntimeCleanupError):
                failed += 1
        return CleanupResult(plan.scanned_count, plan.candidate_count, deleted, failed,
                             plan.recoverable_bytes, recovered)

    @staticmethod
    def _threshold(value: float | None) -> float | None:
        if value is None: return None
        try: parsed = float(value)
        except (TypeError, ValueError) as error:
            raise CleanupRootError("Cleanup age threshold is invalid.") from error
        if not math.isfinite(parsed) or parsed < 0:
            raise CleanupRootError("Cleanup age threshold is invalid.")
        return parsed

    @staticmethod
    def _runtime_root(value: Path) -> Path:
        path = Path(value)
        if path.exists() and (path.is_symlink() or not path.is_dir()):
            raise CleanupRootError("Cleanup root is invalid.")
        try: return path.resolve(strict=False)
        except OSError as error: raise CleanupRootError("Cleanup root is invalid.") from error

    @staticmethod
    def _require_safe_directory(path: Path, root: Path) -> None:
        try: resolved = path.resolve(strict=True)
        except OSError as error: raise CleanupRootError("Cleanup root is invalid.") from error
        if resolved != path.absolute() or not resolved.is_relative_to(root):
            raise CleanupRootError("Cleanup root is invalid.")

    @staticmethod
    def _is_part_name(name: str) -> bool:
        return name.endswith(".part") or ".part." in name

    @staticmethod
    def _looks_disposable_directory(path: Path, root: Path) -> bool:
        return path.name.startswith("assembly-") or (path.parent == root / "media" and path.name.startswith("smoke-"))

    def _classify_directory(self, path: Path, root: Path):
        media = root / "media"
        if path.parent == media and path.name.startswith("smoke-"):
            return CleanupCategory.SMOKE_MEDIA_WORKSPACE, "allowlisted smoke-test media workspace"
        if path.name.startswith("assembly-") and path.parent.parent == media:
            return CleanupCategory.ASSEMBLY_WORKSPACE, "abandoned isolated assembly workspace"
        return None

    def _entry(self, path: Path, category: CleanupCategory, reason: str) -> CleanupEntry:
        try:
            resolved = path.resolve(strict=True)
            stat = resolved.stat()
            size = self._size(resolved)
            age = max(0.0, __import__("time").time() - stat.st_mtime)
        except OSError as error:
            raise CleanupCandidateSafetyError("Cleanup candidate could not be inspected safely.") from error
        return CleanupEntry(resolved, category, reason, age, size)

    def _size(self, path: Path) -> int:
        if path.is_file(): return path.stat().st_size
        total = 0
        for directory, names, files in os.walk(path, followlinks=False):
            current = Path(directory)
            for name in names + files:
                child = current / name
                if child.is_symlink():
                    raise CleanupCandidateSafetyError("Cleanup candidate is outside approved runtime roots.")
                if child.is_file(): total += child.stat().st_size
        return total

    @staticmethod
    def _eligible(entry: CleanupEntry, threshold: float | None, protected: set[Path]) -> bool:
        if threshold is not None and entry.age_seconds < threshold: return False
        return not any(path == entry.path or path.is_relative_to(entry.path) for path in protected)

    def _validate_planned_entry(self, entry: CleanupEntry, root: Path, protected: set[Path], threshold: float) -> None:
        path = entry.path
        if not path.exists(): raise CleanupCandidateSafetyError("Cleanup candidate no longer exists.")
        if path.is_symlink() or path.resolve(strict=True) != path or not path.is_relative_to(root):
            raise CleanupCandidateSafetyError("Cleanup candidate is outside approved runtime roots.")
        classified = (CleanupCategory.ATOMIC_PART_FILE if path.is_file() and self._is_part_name(path.name)
                      and any(path.is_relative_to(root.joinpath(*parts)) for parts in self._PART_ROOTS)
                      else (self._classify_directory(path, root) or (None,))[0])
        if classified != entry.category:
            raise CleanupCandidateSafetyError("Cleanup candidate is not an allowlisted disposable path.")
        refreshed = self._entry(path, entry.category, entry.reason)
        if refreshed.age_seconds < threshold:
            raise CleanupCandidateSafetyError("Cleanup candidate is protected by the age threshold.")
        if any(item == path or item.is_relative_to(path) for item in protected):
            raise CleanupCandidateSafetyError("Cleanup candidate is protected by durable production state.")

    @staticmethod
    def _protected_paths(root: Path) -> set[Path]:
        protected: set[Path] = set()
        productions = root / "productions"
        if not productions.is_dir(): return protected
        for manifest in productions.glob("*.json"):
            protected.add(manifest.resolve())
            try: record = ProductionRecord.model_validate_json(manifest.read_text(encoding="utf-8"))
            except Exception as error:
                raise CleanupRootError("Cleanup root contains an unreadable production manifest.") from error
            for scene in record.scenes:
                if scene.local_path: protected.add(Path(scene.local_path).resolve(strict=False))
            if record.final_artifact: protected.add(Path(record.final_artifact.local_path).resolve(strict=False))
            protected.add(Path(record.final_output_path).resolve(strict=False))
        return protected
