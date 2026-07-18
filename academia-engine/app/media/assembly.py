import shutil
from pathlib import Path
from tempfile import mkdtemp

from .concat import FFmpegVideoConcatenator
from .contracts import AssembledVideoArtifact, VideoAssemblyRequest
from .ffmpeg import FFmpegVideoNormalizer
from .loudness import FFmpegLoudnessNormalizer


class VideoAssemblyError(RuntimeError):
    """Base safe error for final media assembly orchestration."""


class AssemblySourceValidationError(VideoAssemblyError):
    """Raised when the ordered source set cannot enter the workflow."""


class AssemblyDestinationExistsError(VideoAssemblyError):
    """Raised before expensive work when final output already exists."""


class AssemblyWorkspaceError(VideoAssemblyError):
    """Raised when an isolated workflow directory cannot be created."""


class AssemblySceneNormalizationError(VideoAssemblyError):
    """Raised with scene-stage context when normalization fails."""


class AssemblyConcatenationError(VideoAssemblyError):
    """Raised with concatenation-stage context."""


class AssemblyLoudnessNormalizationError(VideoAssemblyError):
    """Raised with final loudness-stage context."""


class AssemblyWorkspaceCleanupError(VideoAssemblyError):
    """Raised when an isolated workflow directory cannot be safely removed."""


class VideoAssemblyService:
    """Composes existing media services without owning FFmpeg command logic."""

    def __init__(
        self,
        normalizer: FFmpegVideoNormalizer,
        concatenator: FFmpegVideoConcatenator,
        loudness_normalizer: FFmpegLoudnessNormalizer,
    ) -> None:
        self._normalizer = normalizer
        self._concatenator = concatenator
        self._loudness_normalizer = loudness_normalizer

    def assemble(self, request: VideoAssemblyRequest) -> AssembledVideoArtifact:
        self._validate_request(request)
        workflow_directory = self._create_workflow_directory(request.workspace)
        failure: tuple[VideoAssemblyError, Exception] | None = None
        final_artifact = None
        try:
            normalized_paths: list[Path] = []
            for index, source in enumerate(request.sources, start=1):
                normalized_path = workflow_directory / f"scene_{index:04d}.normalized.mp4"
                try:
                    normalized = self._normalizer.normalize_video(
                        source,
                        normalized_path,
                        request.normalization_profile,
                    )
                except Exception as error:
                    failure = (
                        AssemblySceneNormalizationError(
                            f"Scene normalization failed for source: {source}"
                        ),
                        error,
                    )
                    break
                normalized_paths.append(normalized.local_path)

            if failure is None:
                concatenated_path = workflow_directory / "concatenated.mp4"
                try:
                    concatenated = self._concatenator.concatenate_videos(
                        normalized_paths,
                        concatenated_path,
                    )
                except Exception as error:
                    failure = (
                        AssemblyConcatenationError("Normalized scene concatenation failed."),
                        error,
                    )

            if failure is None:
                try:
                    final_artifact = self._loudness_normalizer.normalize_loudness(
                        concatenated.local_path,
                        request.destination,
                        request.loudness_profile,
                        overwrite=request.overwrite,
                    )
                except Exception as error:
                    failure = (
                        AssemblyLoudnessNormalizationError(
                            "Final audio loudness normalization failed."
                        ),
                        error,
                    )
        finally:
            try:
                self._cleanup_workflow_directory(workflow_directory, request.workspace)
            except Exception as cleanup_error:
                if failure is None:
                    raise AssemblyWorkspaceCleanupError(
                        "The isolated assembly workspace could not be cleaned safely."
                    ) from cleanup_error

        if failure is not None:
            wrapped, cause = failure
            raise wrapped from cause
        return AssembledVideoArtifact(
            local_path=final_artifact.local_path,
            byte_size=final_artifact.byte_size,
            sha256=final_artifact.sha256,
            media_info=final_artifact.media_info,
            source_count=len(request.sources),
        )

    @staticmethod
    def _validate_request(request: VideoAssemblyRequest) -> None:
        if len(request.sources) < 2:
            raise AssemblySourceValidationError(
                "Final assembly requires at least two scene sources."
            )
        for source in request.sources:
            if not source.is_file():
                raise AssemblySourceValidationError(
                    f"Assembly source does not exist: {source}"
                )
        if request.destination.exists() and not request.overwrite:
            raise AssemblyDestinationExistsError(
                "The final assembly destination already exists."
            )

    @staticmethod
    def _create_workflow_directory(workspace_root: Path) -> Path:
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
            root = workspace_root.resolve()
            workflow = Path(mkdtemp(prefix="assembly-", dir=root)).resolve()
        except OSError as error:
            raise AssemblyWorkspaceError(
                "An isolated assembly workspace could not be created."
            ) from error
        if workflow.parent != root:
            raise AssemblyWorkspaceError(
                "The isolated assembly workspace escaped its requested root."
            )
        return workflow

    @staticmethod
    def _cleanup_workflow_directory(workflow: Path, workspace_root: Path) -> None:
        root = workspace_root.resolve()
        target = workflow.resolve()
        if target == root or target.parent != root or not target.name.startswith("assembly-"):
            raise AssemblyWorkspaceCleanupError(
                "Refusing to remove a path outside the isolated workflow boundary."
            )
        shutil.rmtree(target)
