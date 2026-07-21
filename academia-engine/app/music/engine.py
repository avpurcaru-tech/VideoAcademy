import time
from collections.abc import Mapping
from datetime import datetime,timezone
from pathlib import Path
from typing import Callable

from app.models import GenerationTaskStatus
from pydantic import BaseModel,ConfigDict,Field,ValidationError

from .contracts import (DurableAudioArtifactSet,GeneratedMusicVariant,MusicGenerationRequest,MusicGenerationTask,
                        MusicGenerationTaskRecord,SUPPORTED_AUDIO_CONTENT_TYPES)
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
class MusicArtifactCardinalityError(MusicEngineError):
    def __init__(self,message: str,available_variants: int|None=None):
        super().__init__(message); self.available_variants=available_variants
class MusicVariantIndexError(MusicEngineError): pass
class MusicArtifactSetConflictError(MusicEngineError): pass
class MusicVariantSelectionRequiredError(MusicEngineError):
    def __init__(self,provider_task_id: str,available_variants: int):
        super().__init__(f"Music variant selection is required. Available variants: {available_variants}")
        self.provider_task_id=provider_task_id; self.available_variants=available_variants
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
        try: task=selected.submit_generation(request)
        except Exception as error:
            task_id=getattr(error,"provider_task_id",None)
            if isinstance(task_id,str) and task_id and task_id.replace("_","").replace("-","").isalnum():
                now=_now(); orphan=MusicGenerationTaskRecord(provider=name,provider_task_id=task_id,
                    normalized_status=GenerationTaskStatus.SUBMITTED,created_at=now,updated_at=now)
                try:
                    if not self._registry.exists(task_id): self._registry.create(orphan)
                except MusicTaskRegistryError as registry_error:
                    raise MusicEngineRegistryError("Provider task ID was returned but could not be stored.") from registry_error
            operation=MusicProviderOperationError("Music generation submission failed.")
            operation.provider_task_id=task_id
            raise operation from error
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

    def reconcile_existing_task(self,provider: str,provider_task_id: str) -> MusicGenerationTaskRecord:
        """Query once and adopt a known provider task without submitting generation."""
        task=self._query(provider,provider_task_id); now=_now()
        record=MusicGenerationTaskRecord(provider=provider,provider_task_id=provider_task_id,
            external_correlation_id=task.external_correlation_id,normalized_status=task.normalized_status,
            created_at=task.created_at or now,updated_at=task.updated_at or now,
            provider_artifact_ids=tuple(artifact.artifact_id for artifact in task.artifacts))
        try:
            if self._registry.exists(provider_task_id): self._registry.update(record)
            else: self._registry.create(record)
            return self._registry.load(provider_task_id)
        except MusicTaskRegistryError as error:
            raise MusicEngineRegistryError("Reconciled music task could not be stored.") from error

    def get_task_by_external_id(self,external_correlation_id: str,provider: str|None=None) -> MusicGenerationTask:
        name=provider or self._default_provider; selected=self._provider(name)
        if not isinstance(selected,MusicExternalIdProvider):
            raise MusicExternalLookupUnsupportedError("Music provider does not support external ID lookup.")
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
        if len(task.artifacts)!=1: raise MusicArtifactCardinalityError(
            "Music task must contain exactly one audio artifact.",len(task.artifacts))
        return self._download_artifact(refreshed,task.artifacts[0],destination)

    def list_variants(self,provider_task_id: str) -> tuple[GeneratedMusicVariant,...]:
        existing=self._load(provider_task_id); task=self._query(existing.provider,provider_task_id)
        self._persist(existing.model_copy(update={"normalized_status":task.normalized_status,
            "external_correlation_id":task.external_correlation_id,"updated_at":task.updated_at or _now()}))
        if task.normalized_status!=GenerationTaskStatus.SUCCEEDED: raise MusicTaskNotSucceededError("Music task has not succeeded.")
        return tuple(GeneratedMusicVariant(variant_index=index,artifact_id=artifact.artifact_id,
                     content_type=artifact.content_type.lower()) for index,artifact in enumerate(task.artifacts,start=1))

    def download_variant(self,provider_task_id: str,variant_index: int,destination: Path) -> MusicGenerationTaskRecord:
        if isinstance(variant_index,bool) or not isinstance(variant_index,int) or variant_index<1:
            raise MusicVariantIndexError("Music variant index must be a positive one-based integer.")
        existing=self._load(provider_task_id); task=self._query(existing.provider,provider_task_id)
        refreshed=self._persist(existing.model_copy(update={"normalized_status":task.normalized_status,
            "external_correlation_id":task.external_correlation_id,"updated_at":task.updated_at or _now()}))
        if task.normalized_status!=GenerationTaskStatus.SUCCEEDED: raise MusicTaskNotSucceededError("Music task has not succeeded.")
        if variant_index>len(task.artifacts): raise MusicVariantIndexError("Music variant index exceeds available variants.")
        return self._download_artifact(refreshed,task.artifacts[variant_index-1],destination)

    def download_all_variants(self,provider_task_id: str,destination_directory: Path) -> MusicGenerationTaskRecord:
        existing=self._load(provider_task_id)
        if existing.artifact_set is not None and existing.artifact_set.complete: return existing
        task=self._query(existing.provider,provider_task_id)
        refreshed=self._persist(existing.model_copy(update={"normalized_status":task.normalized_status,
            "external_correlation_id":task.external_correlation_id,"updated_at":task.updated_at or _now()}))
        if task.normalized_status!=GenerationTaskStatus.SUCCEEDED: raise MusicTaskNotSucceededError("Music task has not succeeded.")
        if not task.artifacts: raise MusicArtifactCardinalityError("Music task contains no audio artifacts.",0)
        for artifact in task.artifacts:
            if artifact.content_type.lower() not in SUPPORTED_AUDIO_CONTENT_TYPES:
                raise UnsupportedAudioContentTypeError("Music artifact content type is unsupported.")
        current=refreshed.artifact_set
        if current is not None and current.expected_artifact_count!=len(task.artifacts):
            raise MusicArtifactSetConflictError("Durable music variant count conflicts with provider output.")
        completed={artifact.variant_index:artifact for artifact in (current.artifacts if current else ())}
        for index,artifact in enumerate(task.artifacts,start=1):
            durable=completed.get(index)
            if durable is not None and durable.artifact_id!=artifact.artifact_id:
                raise MusicArtifactSetConflictError("Durable music variant identity conflicts with provider output.")
        artifact_set=DurableAudioArtifactSet(provider_task_id=provider_task_id,
            artifacts=tuple(completed[index] for index in sorted(completed)),expected_artifact_count=len(task.artifacts),complete=False)
        refreshed=self._persist(refreshed.model_copy(update={"artifact_set":artifact_set}))
        directory=Path(destination_directory)
        for index,artifact in enumerate(task.artifacts,start=1):
            if index in completed: continue
            suffix=SUPPORTED_AUDIO_CONTENT_TYPES[artifact.content_type.lower()]
            destination=directory/f"variant-{index:02d}{suffix}"
            try: durable=self._downloader.download_audio_artifact(artifact,destination)
            except Exception as error: raise MusicEngineDownloadError("Music variant could not be downloaded safely.") from error
            completed[index]=durable.model_copy(update={"variant_index":index})
            partial=DurableAudioArtifactSet(provider_task_id=provider_task_id,
                artifacts=tuple(completed[value] for value in sorted(completed)),expected_artifact_count=len(task.artifacts),complete=False)
            refreshed=self._persist(refreshed.model_copy(update={"artifact_set":partial}))
        complete=DurableAudioArtifactSet(provider_task_id=provider_task_id,
            artifacts=tuple(completed[value] for value in sorted(completed)),expected_artifact_count=len(task.artifacts),complete=True)
        return self._persist(refreshed.model_copy(update={"artifact_set":complete}))

    def _download_artifact(self,refreshed,artifact,destination):
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
        submitted=self.submit(request,provider)
        try: return self.wait_and_download(submitted.provider_task_id,destination,policy)
        except MusicArtifactCardinalityError as error:
            if error.available_variants and error.available_variants>1:
                raise MusicVariantSelectionRequiredError(submitted.provider_task_id,error.available_variants) from None
            raise

    def generate_all_variants(self,request,destination_directory,policy,provider=None):
        submitted=self.submit(request,provider)
        try:
            terminal=self.wait_until_terminal(submitted.provider_task_id,policy)
            if terminal.normalized_status==GenerationTaskStatus.FAILED: raise MusicEngineTaskFailedError("Music task failed.")
            return self.download_all_variants(submitted.provider_task_id,destination_directory)
        except Exception as error:
            if getattr(error,"provider_task_id",None) is None:
                try: error.provider_task_id=submitted.provider_task_id
                except Exception: pass
            raise

    def resume(self,provider_task_id,destination,policy):
        existing=self._load(provider_task_id)
        if existing.normalized_status in {GenerationTaskStatus.SUBMITTED,GenerationTaskStatus.PROCESSING}:
            try: return self.wait_and_download(provider_task_id,destination,policy)
            except MusicArtifactCardinalityError as error:
                if error.available_variants and error.available_variants>1:
                    raise MusicVariantSelectionRequiredError(provider_task_id,error.available_variants) from None
                raise
        if existing.normalized_status==GenerationTaskStatus.SUCCEEDED:
            if existing.artifact is not None: return existing
            variants=self.list_variants(provider_task_id)
            if len(variants)>1: raise MusicVariantSelectionRequiredError(provider_task_id,len(variants))
            return self.download_variant(provider_task_id,1,destination)
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
