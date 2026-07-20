import hashlib
import os
from pathlib import Path
from typing import Callable,Protocol

from .contracts import DurableAudioArtifact, GeneratedAudioArtifact, SUPPORTED_AUDIO_CONTENT_TYPES


class AudioArtifactDownloadError(RuntimeError): pass
class AudioDestinationConflictError(AudioArtifactDownloadError): pass
class AudioContentTypeError(AudioArtifactDownloadError): pass
class AudioDownloadEmptyError(AudioArtifactDownloadError): pass


class AudioArtifactDownloader(Protocol):
    def download_audio_artifact(self,artifact: GeneratedAudioArtifact,destination: Path) -> DurableAudioArtifact: ...


class AtomicAudioArtifactDownloader:
    """Atomically publishes bytes supplied by an injected provider transport."""
    def __init__(self,reader: Callable[[GeneratedAudioArtifact],bytes]) -> None: self._reader=reader

    def download_audio_artifact(self,artifact: GeneratedAudioArtifact,destination: Path) -> DurableAudioArtifact:
        content_type=artifact.content_type.lower(); expected=SUPPORTED_AUDIO_CONTENT_TYPES.get(content_type)
        if expected is None: raise AudioContentTypeError("Audio content type is unsupported.")
        target=Path(destination)
        if target.suffix.lower()!=expected: raise AudioContentTypeError("Audio destination extension does not match its content type.")
        if target.exists(): raise AudioDestinationConflictError("Audio destination already exists.")
        part=target.with_suffix(target.suffix+".part"); target.parent.mkdir(parents=True,exist_ok=True)
        try:
            try: content=self._reader(artifact)
            except Exception as error: raise AudioArtifactDownloadError("Audio artifact retrieval failed.") from error
            if not isinstance(content,(bytes,bytearray)) or not content: raise AudioDownloadEmptyError("Audio artifact is empty.")
            digest=hashlib.sha256()
            with part.open("xb") as stream:
                stream.write(content); digest.update(content); stream.flush(); os.fsync(stream.fileno())
            if target.exists(): raise AudioDestinationConflictError("Audio destination already exists.")
            os.replace(part,target)
            return DurableAudioArtifact(artifact_id=artifact.artifact_id,local_path=target,byte_size=len(content),
                                        sha256=digest.hexdigest(),content_type=content_type)
        except AudioArtifactDownloadError: raise
        except OSError as error: raise AudioArtifactDownloadError("Audio artifact could not be published safely.") from error
        finally: part.unlink(missing_ok=True)

