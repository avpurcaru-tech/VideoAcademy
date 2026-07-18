import subprocess
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class ProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exit_code: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        timeout_seconds: float | None = None,
    ) -> ProcessResult:
        """Execute one tool using an argument vector, never a shell command string."""


class SubprocessProcessRunner:
    """Production argv-only external process adapter."""

    def run(
        self,
        args: Sequence[str],
        timeout_seconds: float | None = None,
    ) -> ProcessResult:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
        return ProcessResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
