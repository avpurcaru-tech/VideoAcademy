import time
from collections.abc import Mapping
from datetime import datetime,timezone
from pathlib import Path
from typing import Callable

from app.models import GenerationTaskStatus
from pydantic import BaseModel,ConfigDict,Field,ValidationError

from .contracts import MusicGenerationRequest,MusicGenerationTask,MusicGenerationTaskRecord,SUPPORTED_AUDIO_CONTENT_TYPES
from .downloader import AudioArtifactDownloader
from .provider import MusicExternalIdProvider,MusicProvider
from .registry import MusicTaskRegistry,MusicTaskRegistryError,MusicTaskRegistryNotFoundError


class MusicPollingPolicy(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    interval_seconds: float=Field(gt=0)
    timeout_seconds: float=Field(gt=0)
    max_attempts: int|None=Field(default=None,gt=0)


class MusicEngineError(RuntimeError): pass
class UnknownMusicProviderError(MusicEngineError): pass
class MusicTaskNotFoundError(MusicEngineError): pass
class MusicProviderTaskIdMismatchError(MusicEngineError): pass
class MusicExternalIdMismatchError(MusicEngineError): pass
class MusicExternalLookupUnsupportedError(MusicEngineError): pass
class MusicTaskNotSucceededError(MusicEngineError): pass
class MusicArtifactCardinalityError(MusicEngineError): pass
class UnsupportedAudioContentTypeError(MusicEngineError): pass
class MusicProviderOperationError(MusicEngineError): pass
class MusicEngineTimeoutError(MusicEngineError): pass
class MusicEngineAttemptsExceededError(MusicEngineError): pass
class MusicEngineDownloadError(MusicEngineError): pass
class MusicEngineRegistryError(MusicEngineError): pass
class MusicEngineTaskFailedError(MusicEngineError): pass
class MusicEngineContractError(MusicEngineError): pass


class MusicEngine:
    def __init__(self,providers: Mapping[str,MusicProvider],registry: MusicTaskRegistry,downloader: AudioArtifactDownloader,
                 *,default_provider: str|None=None,monotonic_clock: Callable[[],float]=time.monotonic,
                 sleeper: Callable[[float],None]=time.sleep) -> None:
        self._providers=dict(providers); self._registry=registry; self._downloader=downloader
        self._default_provider=default_provider; self._clock=monotonic_clock; self._sleep=sleeper

    def submit(self,request: MusicGenerationRequest,provider: str|None=None) -> MusicGenerationTaskRecord:
        name=provider or self._default_provider; selected=self._provider(name)
        if not isinstance(selected,MusicExternalIdProvider):
            raise MusicExternalLookupUnsupportedError("Music provider does not support external ID lookup.")
        try: task=selected.submit_generation(request)
        except Exception as error: raise MusicProviderOperationError("Music generation submission failed.") from error
        task=_validated_task(task)
        if task.provider!=name: raise MusicEngineContractError("Music provider identity is inconsistent.")
        now=_now(); record=MusicGenerationTaskRecord(provider=name,provider_task_id=task.provider_task_id,
            external_correlation_id=task.external_correlation_id,normalized_status=task.normalized_status,
            created_at=task.created_at or now,updated_at=task.updated_at or now)
        try: self._registry.create(record); return self._registry.load(record.provider_task_id)
        except MusicTaskRegistryError as error: raise MusicEngineRegistryError("Submitted music task could not be stored.") from error

    def refresh(self,provider_task_id: str) -> MusicGenerationTaskRecord:
        existing=self._load(provider_task_id); task=self._query(existing.provider,provider_task_id)
        return self._persist(existing.model_copy(update={"normalized_status":task.normalized_status,
            "external_correlation_id":task.external_correlation_id,"updated_at":task.updated_at or _now()}))

    def get_task_by_external_id(self,external_correlation_id: str,provider: str|None=None) -> MusicGenerationTask:
        name=provider or self._default_provider; selected=self._provider(name)
        try: task=selected.get_task_by_external_id(external_correlation_id)
        except Exception as error: raise MusicProviderOperationError("Music provider external task query failed.") from error
        task=_validated_task(task)
        if task.external_correlation_id!=external_correlation_id: raise MusicExternalIdMismatchError("Music provider returned a different external ID.")
        if task.provider!=name: raise MusicEngineContractError("Music provider identity is inconsistent.")
        return task

    def download(self,provider_task_id: str,destination: Path) -> MusicGenerationTaskRecord:
        existing=self._load(provider_task_id); task=self._query(existing.provider,provider_task_id)
        refreshed=self._persist(existing.model_copy(update={"normalized_status":task.normalized_status,
            "external_correlation_id":task.external_correlation_id,"updated_at":task.updated_at or _now()}))
        if task.normalized_status!=GenerationTaskStatus.SUCCEEDED: raise MusicTaskNotSucceededError("Music task has not succeeded.")
        if len(task.artifacts)!=1: raise MusicArtifactCardinalityError("Music task must contain exactly one audio artifact.")
        artifact=task.artifacts[0]
        if artifact.content_type.lower() not in SUPPORTED_AUDIO_CONTENT_TYPES:
            raise UnsupportedAudioContentTypeError("Music artifact content type is unsupported.")
        try: durable=self._downloader.download_audio_artifact(artifact,Path(destination))
        except Exception as error: raise MusicEngineDownloadError("Music artifact could not be downloaded safely.") from error
        return self._persist(refreshed.model_copy(update={"artifact":durable}))

    def wait_until_terminal(self,provider_task_id: str,policy: MusicPollingPolicy) -> MusicGenerationTaskRecord:
        self._load(provider_task_id); deadline=self._clock()+policy.timeout_seconds; attempts=0
        while True:
            record=self.refresh(provider_task_id); attempts+=1
            if record.normalized_status in {GenerationTaskStatus.SUCCEEDED,GenerationTaskStatus.FAILED}: return record
            if policy.max_attempts is not None and attempts>=policy.max_attempts:
                raise MusicEngineAttemptsExceededError("Music polling reached its attempt limit.")
            remaining=deadline-self._clock()
            if remaining<=0: raise MusicEngineTimeoutError("Music polling timed out.")
            self._sleep(min(policy.interval_seconds,remaining))
            if self._clock()>=deadline: raise MusicEngineTimeoutError("Music polling timed out.")

    def wait_and_download(self,provider_task_id: str,destination: Path,policy: MusicPollingPolicy) -> MusicGenerationTaskRecord:
        terminal=self.wait_until_terminal(provider_task_id,policy)
        if terminal.normalized_status==GenerationTaskStatus.FAILED: raise MusicEngineTaskFailedError("Music task failed.")
        return self.download(provider_task_id,destination)

    def generate(self,request,destination,policy,provider=None):
        submitted=self.submit(request,provider); return self.wait_and_download(submitted.provider_task_id,destination,policy)

    def resume(self,provider_task_id,destination,policy):
        existing=self._load(provider_task_id)
        if existing.normalized_status in {GenerationTaskStatus.SUBMITTED,GenerationTaskStatus.PROCESSING}:
            return self.wait_and_download(provider_task_id,destination,policy)
        if existing.normalized_status==GenerationTaskStatus.SUCCEEDED:
            return existing if existing.artifact is not None else self.download(provider_task_id,destination)
        if existing.normalized_status==GenerationTaskStatus.FAILED: raise MusicEngineTaskFailedError("Music task failed.")
        raise MusicEngineContractError("Music task has an unsupported status.")

    def _provider(self,name):
        if not name or name not in self._providers: raise UnknownMusicProviderError("Music provider is not configured.")
        return self._providers[name]
    def _load(self,task_id):
        try: return self._registry.load(task_id)
        except MusicTaskRegistryNotFoundError as error: raise MusicTaskNotFoundError("Music task is missing.") from error
        except MusicTaskRegistryError as error: raise MusicEngineRegistryError("Music registry could not be read.") from error
    def _query(self,name,task_id):
        try: task=self._provider(name).get_task_by_id(task_id)
        except MusicEngineError: raise
        except Exception as error: raise MusicProviderOperationError("Music provider task query failed.") from error
        task=_validated_task(task)
        if task.provider_task_id!=task_id: raise MusicProviderTaskIdMismatchError("Music provider returned a different task ID.")
        if task.provider!=name: raise MusicEngineContractError("Music provider identity is inconsistent.")
        return task
    def _persist(self,record):
        try: self._registry.update(record); return self._registry.load(record.provider_task_id)
        except MusicTaskRegistryError as error: raise MusicEngineRegistryError("Music registry could not be updated.") from error


def _now(): return datetime.now(timezone.utc)


def _validated_task(value) -> MusicGenerationTask:
    try:
        payload=value.model_dump(mode="python") if isinstance(value,BaseModel) else value
        return MusicGenerationTask.model_validate(payload)
    except (ValidationError,TypeError,AttributeError) as error:
        raise MusicEngineContractError("Music provider returned an invalid task contract.") from error
