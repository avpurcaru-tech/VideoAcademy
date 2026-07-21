import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from .contracts import AudioProbeResult,MediaProbeResult
from .process_runner import ProcessRunner


_DIAGNOSTIC_LIMIT = 500


class MediaProbeError(RuntimeError):
    """Base sanitized media inspection error."""


class FFprobeExecutionError(MediaProbeError):
    """Raised when FFprobe cannot successfully inspect a file."""


class FFprobeResponseError(MediaProbeError):
    """Raised when FFprobe output does not satisfy the normalized contract."""


class FFprobeAdapter:
    def __init__(
        self,
        runner: ProcessRunner,
        *,
        executable: str = "ffprobe",
        timeout_seconds: float | None = 30,
    ) -> None:
        self._runner = runner
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def probe_video(self, path: Path) -> MediaProbeResult:
        local_path,payload=self._probe_payload(path)
        return self._parse_payload(local_path,payload)

    def probe_audio(self,path: Path) -> AudioProbeResult:
        local_path,payload=self._probe_payload(path)
        if not isinstance(payload,dict): raise FFprobeResponseError("ffprobe JSON root must be an object.")
        streams=payload.get("streams"); media_format=payload.get("format")
        if not isinstance(streams,list) or not isinstance(media_format,dict):
            raise FFprobeResponseError("ffprobe response is missing streams or format data.")
        audios=[stream for stream in streams if isinstance(stream,dict) and stream.get("codec_type")=="audio"]
        if not audios: raise FFprobeResponseError("ffprobe response contains no audio stream.")
        try:
            return AudioProbeResult(local_path=local_path,duration_seconds=float(media_format["duration"]),
                audio_codec=_required_text(audios[0].get("codec_name"),"audio codec"),
                container_format=_required_text(media_format.get("format_name"),"container format"))
        except (KeyError,TypeError,ValueError) as error:
            raise FFprobeResponseError("ffprobe response contains incomplete or invalid audio fields.") from error

    def _probe_payload(self,path: Path) -> tuple[Path,Any]:
        local_path = Path(path)
        args = [
            self._executable,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(local_path),
        ]
        try:
            result = self._runner.run(args, timeout_seconds=self._timeout_seconds)
        except Exception as error:
            raise FFprobeExecutionError("ffprobe could not be executed.") from error
        if result.exit_code != 0:
            summary = _bounded_stderr(result.stderr)
            detail = f"; stderr: {summary}" if summary else ""
            raise FFprobeExecutionError(
                f"ffprobe failed with exit code {result.exit_code}{detail}."
            )
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise FFprobeResponseError("ffprobe returned malformed JSON.") from error
        return local_path,payload

    @staticmethod
    def _parse_payload(path: Path, payload: Any) -> MediaProbeResult:
        if not isinstance(payload, dict):
            raise FFprobeResponseError("ffprobe JSON root must be an object.")
        streams = payload.get("streams")
        media_format = payload.get("format")
        if not isinstance(streams, list) or not isinstance(media_format, dict):
            raise FFprobeResponseError("ffprobe response is missing streams or format data.")
        videos = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"]
        if not videos:
            raise FFprobeResponseError("ffprobe response contains no video stream.")
        video = videos[0]
        audios = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"]
        audio = audios[0] if audios else None
        try:
            duration = float(media_format["duration"])
            width = int(video["width"])
            height = int(video["height"])
            video_codec = _required_text(video.get("codec_name"), "video codec")
            container = _required_text(media_format.get("format_name"), "container format")
            rate = _frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
            audio_codec = _required_text(audio.get("codec_name"), "audio codec") if audio else None
            return MediaProbeResult(
                local_path=path,
                duration_seconds=duration,
                width=width,
                height=height,
                frame_rate=rate,
                video_codec=video_codec,
                audio_codec=audio_codec,
                has_audio=audio is not None,
                container_format=container,
            )
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            raise FFprobeResponseError("ffprobe response contains incomplete or invalid media fields.") from error


def _frame_rate(value: Any) -> float:
    if not isinstance(value, str) or not value:
        raise ValueError("missing frame rate")
    rate = float(Fraction(value))
    if rate <= 0:
        raise ValueError("invalid frame rate")
    return rate


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing {field}")
    return value


def _bounded_stderr(stderr: str) -> str:
    return " ".join(stderr.split())[:_DIAGNOSTIC_LIMIT]
