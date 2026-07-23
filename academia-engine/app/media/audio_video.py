from __future__ import annotations

import hashlib
import math
import os
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any,Iterable
from urllib.parse import urlparse

from pydantic import BaseModel,ConfigDict,Field

from .contracts import AudioProbeResult,MediaProbeResult
from .ffprobe import MediaProbeError
from .process_runner import ProcessRunner


COMPOSITION_DURATION_TOLERANCE_SECONDS=0.25
DURATION_TOLERANCE_SECONDS=COMPOSITION_DURATION_TOLERANCE_SECONDS
FRAME_RATE_TOLERANCE=0.01


class AudioVideoDurationPolicy(str,Enum):
    TRIM_VIDEO_TO_AUDIO="trim_video_to_audio"
    EXTEND_VIDEO_TO_AUDIO="extend_video_to_audio"


class AudioVideoCompositionRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    video_source: Path
    audio_source: Path
    destination: Path
    workspace: Path
    duration_policy: AudioVideoDurationPolicy
    overwrite: bool=False


class AudioVideoComposedArtifact(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    local_path: Path
    byte_size: int=Field(gt=0)
    sha256: str=Field(pattern=r"^[a-f0-9]{64}$")
    media_info: MediaProbeResult
    video_source_path: Path
    audio_source_path: Path
    duration_policy: AudioVideoDurationPolicy


class AudioVideoCompositionError(RuntimeError): pass
class VideoCompositionSourceMissingError(AudioVideoCompositionError): pass
class VideoCompositionSourceInvalidError(AudioVideoCompositionError): pass
class AudioCompositionSourceMissingError(AudioVideoCompositionError): pass
class AudioCompositionSourceInvalidError(AudioVideoCompositionError): pass
class CompositionPathError(AudioVideoCompositionError): pass
class CompositionInvalidDurationPolicyError(AudioVideoCompositionError): pass
class CompositionDestinationConflictError(AudioVideoCompositionError): pass
class CompositionDurationIncompatibleError(AudioVideoCompositionError): pass
class CompositionFFmpegError(AudioVideoCompositionError): pass
class CompositionOutputMissingError(AudioVideoCompositionError): pass
class CompositionOutputEmptyError(AudioVideoCompositionError): pass
class CompositionProbeError(AudioVideoCompositionError): pass
class CompositionVideoStreamMissingError(AudioVideoCompositionError): pass
class CompositionAudioStreamMissingError(AudioVideoCompositionError): pass
class CompositionDurationMismatchError(AudioVideoCompositionError): pass
class CompositionResolutionMismatchError(AudioVideoCompositionError): pass
class CompositionFrameRateMismatchError(AudioVideoCompositionError): pass
class CompositionCodecMismatchError(AudioVideoCompositionError): pass
class CompositionPublicationError(AudioVideoCompositionError): pass


class AudioVariantCompositionPartialError(AudioVideoCompositionError):
    def __init__(self,completed_artifacts: tuple[AudioVideoComposedArtifact,...],failed_variant_index: int):
        super().__init__("Audio variant composition stopped after a partial batch failure.")
        self.completed_artifacts=completed_artifacts; self.completed_count=len(completed_artifacts)
        self.failed_variant_index=failed_variant_index


class FFmpegAudioVideoComposer:
    def __init__(self,runner: ProcessRunner,probe: Any,*,executable: str="ffmpeg",
                 timeout_seconds: float|None=None) -> None:
        self._runner=runner; self._probe=probe; self._executable=executable; self._timeout=timeout_seconds

    def compose(self,request: AudioVideoCompositionRequest) -> AudioVideoComposedArtifact:
        video=Path(request.video_source); audio=Path(request.audio_source)
        destination=Path(request.destination); workspace=Path(request.workspace)
        self._preflight(video,audio,destination,workspace,request.overwrite)
        video_info=self._probe_video(video); audio_info=self._probe_audio(audio)
        if request.duration_policy==AudioVideoDurationPolicy.TRIM_VIDEO_TO_AUDIO and \
                video_info.duration_seconds+DURATION_TOLERANCE_SECONDS<audio_info.duration_seconds:
            raise CompositionDurationIncompatibleError("Video is shorter than audio under the trim policy.")
        loop=(request.duration_policy==AudioVideoDurationPolicy.EXTEND_VIDEO_TO_AUDIO and
              video_info.duration_seconds+DURATION_TOLERANCE_SECONDS<audio_info.duration_seconds)
        destination.parent.mkdir(parents=True,exist_ok=True); workspace.mkdir(parents=True,exist_ok=True)
        temporary: Path|None=None
        try:
            with NamedTemporaryFile(mode="wb",dir=destination.parent,prefix=f".{destination.stem}.",
                                   suffix=".part.mp4",delete=False) as stream: temporary=Path(stream.name)
            result=self._runner.run(self._arguments(video,audio,temporary,video_info,audio_info,loop),
                                    timeout_seconds=self._timeout)
            if result.exit_code!=0:
                error=CompositionFFmpegError("Audio/video composition failed."); error.exit_code=result.exit_code
                error.safe_category=_safe_ffmpeg_category(result.stderr); raise error
            if not temporary.is_file(): raise CompositionOutputMissingError("Composition output is missing.")
            size=temporary.stat().st_size
            if size<=0: raise CompositionOutputEmptyError("Composition output is empty.")
            with temporary.open("r+b") as output: os.fsync(output.fileno())
            digest=_sha256(temporary)
            try: output_info=self._probe.probe_video(temporary)
            except Exception as error: raise CompositionProbeError("Composition output could not be inspected.") from error
            self._validate_output(output_info,video_info,audio_info)
            if destination.exists() and not request.overwrite:
                raise CompositionDestinationConflictError("Composition destination already exists.")
            try: os.replace(temporary,destination)
            except OSError as error: raise CompositionPublicationError("Composition output could not be published atomically.") from error
            temporary=None
            return AudioVideoComposedArtifact(local_path=destination,byte_size=size,sha256=digest,
                media_info=output_info.model_copy(update={"local_path":destination}),video_source_path=video,
                audio_source_path=audio,duration_policy=request.duration_policy)
        except AudioVideoCompositionError: raise
        except Exception as error:
            failure=CompositionFFmpegError("Audio/video composition could not be executed.")
            failure.exit_code=getattr(error,"exit_code",None)
            failure.safe_category="ffmpeg_not_installed" if isinstance(error,FileNotFoundError) else "unknown_ffmpeg_failure"
            raise failure from error
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)

    def _preflight(self,video,audio,destination,workspace,overwrite):
        if not video.exists(): raise VideoCompositionSourceMissingError("Video source does not exist.")
        if not video.is_file(): raise VideoCompositionSourceInvalidError("Video source is not a regular file.")
        if not audio.exists(): raise AudioCompositionSourceMissingError("Audio source does not exist.")
        if not audio.is_file(): raise AudioCompositionSourceInvalidError("Audio source is not a regular file.")
        for value in (destination,workspace):
            parsed=urlparse(str(value))
            if parsed.scheme and len(parsed.scheme)>1: raise CompositionPathError("Composition path must be local.")
        if destination.resolve()==workspace.resolve(): raise CompositionPathError("Destination and workspace must differ.")
        if destination.exists() and destination.is_dir(): raise CompositionPathError("Composition destination must be a file path.")
        if workspace.exists() and not workspace.is_dir(): raise CompositionPathError("Composition workspace must be a directory.")
        if destination.exists() and not overwrite: raise CompositionDestinationConflictError("Composition destination already exists.")

    def _probe_video(self,path):
        try: info=self._probe.probe_video(path)
        except MediaProbeError as error: raise CompositionProbeError("Video source could not be inspected.") from error
        if not isinstance(info,MediaProbeResult): raise CompositionVideoStreamMissingError("Video source has no valid video stream.")
        if not math.isfinite(info.duration_seconds) or info.duration_seconds<=0:
            raise VideoCompositionSourceInvalidError("Video duration is invalid.")
        return info

    def _probe_audio(self,path):
        try: info=self._probe.probe_audio(path)
        except MediaProbeError as error: raise CompositionProbeError("Audio source could not be inspected.") from error
        if not isinstance(info,AudioProbeResult): raise CompositionAudioStreamMissingError("Audio source has no valid audio stream.")
        if info.audio_codec.lower() not in {"mp3","aac"}: raise AudioCompositionSourceInvalidError("Audio codec is unsupported.")
        return info

    def _arguments(self,video,audio,destination,video_info,audio_info,loop):
        args=[self._executable,"-hide_banner","-loglevel","error","-y"]
        if loop: args.extend(["-stream_loop","-1"])
        args.extend(["-i",str(video),"-i",str(audio),"-map","0:v:0","-map","1:a:0","-t",
            _number(audio_info.duration_seconds),"-vf",f"scale={video_info.width}:{video_info.height}","-r",
            _number(video_info.frame_rate),"-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",
            "-movflags","+faststart",str(destination)])
        return args

    @staticmethod
    def _validate_output(output,video,audio):
        if not output.video_codec: raise CompositionVideoStreamMissingError("Composition output has no video stream.")
        if not output.has_audio or not output.audio_codec: raise CompositionAudioStreamMissingError("Composition output has no audio stream.")
        if abs(output.duration_seconds-audio.duration_seconds)>DURATION_TOLERANCE_SECONDS:
            raise CompositionDurationMismatchError("Composition duration does not match audio duration.")
        if (output.width,output.height)!=(video.width,video.height):
            raise CompositionResolutionMismatchError("Composition resolution does not match video source.")
        if abs(output.frame_rate-video.frame_rate)>FRAME_RATE_TOLERANCE:
            raise CompositionFrameRateMismatchError("Composition frame rate does not match video source.")
        if output.video_codec.lower()!="h264" or output.audio_codec.lower()!="aac":
            raise CompositionCodecMismatchError("Composition codecs do not match H.264/AAC output policy.")


class AudioVariantVideoComposer:
    def __init__(self,composer: FFmpegAudioVideoComposer) -> None: self._composer=composer

    def compose_variants(self,video_source: Path,audio_artifacts: Iterable[Any],destination_directory: Path,
                         workspace: Path,duration_policy: AudioVideoDurationPolicy,overwrite: bool=False
                         ,start_index: int=1
                         ) -> tuple[AudioVideoComposedArtifact,...]:
        completed=[]
        if isinstance(start_index,bool) or not isinstance(start_index,int) or start_index<1:
            raise ValueError("Variant start index must be a positive integer.")
        for index,item in enumerate(audio_artifacts,start=start_index):
            audio=Path(getattr(item,"local_path",item)); destination=Path(destination_directory)/f"final-variant-{index:02d}.mp4"
            try: completed.append(self._composer.compose(AudioVideoCompositionRequest(video_source=video_source,
                audio_source=audio,destination=destination,workspace=workspace,duration_policy=duration_policy,
                overwrite=overwrite)))
            except Exception as error: raise AudioVariantCompositionPartialError(tuple(completed),index) from error
        return tuple(completed)

def _safe_ffmpeg_category(stderr):
    value=(stderr or "").casefold()
    if "no such file" in value or "not found" in value: return "input_not_found"
    if "unknown decoder" in value or "unsupported codec" in value: return "unsupported_codec"
    if "filter" in value: return "invalid_filter_graph"
    if "permission denied" in value or "could not write" in value: return "output_write_failed"
    return "unknown_ffmpeg_failure"


def _sha256(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk:=stream.read(1024*1024): digest.update(chunk)
    return digest.hexdigest()


def _number(value):
    value=float(value)
    return str(int(value)) if value.is_integer() else str(value)
