from collections.abc import Sequence

from .process_runner import ProcessRunner


class MediaToolAvailabilityError(RuntimeError):
    """Safe error raised when a required local media executable is unavailable."""


class MediaToolAvailabilityChecker:
    """Verify executables through the existing argv-only process abstraction."""

    def __init__(
        self,
        runner: ProcessRunner,
        *,
        timeout_seconds: float | None = 10,
    ) -> None:
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def require_available(self, tools: Sequence[str] = ("ffmpeg", "ffprobe")) -> None:
        for tool in tools:
            try:
                result = self._runner.run(
                    (tool, "-version"),
                    timeout_seconds=self._timeout_seconds,
                )
            except Exception as error:
                raise MediaToolAvailabilityError(
                    f"Required media tool is unavailable: {tool}"
                ) from error
            if result.exit_code != 0:
                raise MediaToolAvailabilityError(
                    f"Required media tool is not executable: {tool}"
                )
