"""Official Mureka v1 asynchronous song-generation adapter."""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime,timezone
from typing import Any,Protocol

from pydantic import BaseModel,ConfigDict,Field,ValidationError

from app.models import GenerationTaskStatus
from app.music.contracts import GeneratedAudioArtifact,MusicGenerationRequest,MusicGenerationTask


class MurekaMusicError(RuntimeError): pass
class MurekaMusicConfigurationError(MurekaMusicError): pass
class MurekaMusicAuthenticationError(MurekaMusicError): pass
class MurekaMusicRateLimitError(MurekaMusicError): pass
class MurekaMusicTimeoutError(MurekaMusicError): pass
class MurekaMusicNetworkError(MurekaMusicError): pass
class MurekaMusicApiError(MurekaMusicError): pass
class MurekaMusicContractError(MurekaMusicError): pass


class MurekaTransport(Protocol):
    def request_json(self,method: str,path: str,payload: dict[str,Any]|None=None) -> dict[str,Any]: ...
    def download(self,url: str) -> bytes: ...


class UrllibMurekaTransport:
    def __init__(self,api_key: str,*,base_url: str="https://api.mureka.ai",timeout_seconds: float=30) -> None:
        if not api_key.strip(): raise MurekaMusicConfigurationError("Mureka API key is not configured.")
        if timeout_seconds<=0: raise MurekaMusicConfigurationError("Mureka HTTP timeout is invalid.")
        self._key=api_key; self._base=base_url.rstrip("/"); self._timeout=timeout_seconds

    def request_json(self,method,path,payload=None):
        body=None if payload is None else json.dumps(payload,ensure_ascii=False).encode("utf-8")
        request=urllib.request.Request(self._base+path,data=body,method=method,
            headers={"Authorization":f"Bearer {self._key}","Content-Type":"application/json","Accept":"application/json"})
        raw=self._open(request)
        try: value=json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError,json.JSONDecodeError): raise MurekaMusicContractError("Mureka returned malformed JSON.") from None
        if not isinstance(value,dict): raise MurekaMusicContractError("Mureka returned an invalid response shape.")
        return value

    def download(self,url):
        # Artifact URLs are transient provider URLs; never forward API authorization.
        return self._open(urllib.request.Request(url,method="GET",headers={"Accept":"audio/wav"}))

    def _open(self,request):
        try:
            with urllib.request.urlopen(request,timeout=self._timeout) as response: return response.read()
        except urllib.error.HTTPError as error:
            if error.code in (401,403): raise MurekaMusicAuthenticationError("Mureka authentication failed.") from None
            if error.code==429: raise MurekaMusicRateLimitError("Mureka rate limit was reached.") from None
            raise MurekaMusicApiError(f"Mureka request failed with HTTP status {error.code}.") from None
        except (TimeoutError,socket.timeout): raise MurekaMusicTimeoutError("Mureka request timed out.") from None
        except (urllib.error.URLError,OSError): raise MurekaMusicNetworkError("Mureka request failed at the network boundary.") from None


class _Choice(BaseModel):
    model_config=ConfigDict(extra="ignore")
    id: str=Field(min_length=1,max_length=200)
    wav_url: str|None=None


class _Task(BaseModel):
    model_config=ConfigDict(extra="ignore")
    id: str=Field(pattern=r"^[A-Za-z0-9_-]+$")
    status: str
    created_at: int|float|None=None
    finished_at: int|float|None=None
    choices: tuple[_Choice,...]=()


_STATUS={"preparing":GenerationTaskStatus.SUBMITTED,"queued":GenerationTaskStatus.SUBMITTED,
         "running":GenerationTaskStatus.PROCESSING,"streaming":GenerationTaskStatus.PROCESSING,
         "succeeded":GenerationTaskStatus.SUCCEEDED,"failed":GenerationTaskStatus.FAILED,
         "timeouted":GenerationTaskStatus.FAILED,"cancelled":GenerationTaskStatus.FAILED}


def flatten_lyrics(request: MusicGenerationRequest) -> str:
    blocks=[]
    for section in request.lyrics.sections:
        blocks.append(f"[{section.kind.value.title()}]\n"+"\n".join(line.text for line in section.lines))
    value="\n\n".join(blocks)
    if len(value)>5000: raise MurekaMusicContractError("Lyrics exceed the Mureka contract limit.")
    return value


def map_request(request: MusicGenerationRequest,model: str) -> dict[str,Any]:
    plan=request.music_plan
    prompt=(f"Style: {plan.musical_style}. Mood: {plan.mood}. Instrumentation: {', '.join(plan.instrumentation)}. "
            f"Vocal style: {plan.vocal_style}. Tempo: {plan.tempo_bpm:g} BPM. Target duration: {plan.target_duration_seconds:g} seconds.")
    if len(prompt)>1024: raise MurekaMusicContractError("Music style prompt exceeds the Mureka contract limit.")
    return {"lyrics":flatten_lyrics(request),"model":model,"n":1,"prompt":prompt,"stream":False}


class MurekaMusicProvider:
    provider_name="mureka"
    def __init__(self,transport: MurekaTransport,*,model: str="auto") -> None:
        if model not in {"auto","mureka-7.6","mureka-o2","mureka-8","mureka-9"}:
            raise MurekaMusicConfigurationError("Mureka model is unsupported.")
        self._transport=transport; self._model=model

    @classmethod
    def from_environment(cls):
        key=os.getenv("MUREKA_API_KEY","")
        try: timeout=float(os.getenv("MUREKA_TIMEOUT_SECONDS","30"))
        except ValueError: raise MurekaMusicConfigurationError("Mureka HTTP timeout is invalid.") from None
        return cls(UrllibMurekaTransport(key,timeout_seconds=timeout),model=os.getenv("MUREKA_MUSIC_MODEL","auto"))

    def submit_generation(self,request):
        return self._task(self._transport.request_json("POST","/v1/song/generate",map_request(request,self._model)))

    def get_task_by_id(self,provider_task_id):
        if not provider_task_id or not provider_task_id.replace("_","").replace("-","").isalnum():
            raise MurekaMusicContractError("Mureka task ID is invalid.")
        return self._task(self._transport.request_json("GET",f"/v1/song/query/{provider_task_id}"))

    def download_audio_bytes(self,artifact): return self._transport.download(artifact.download_url)

    def _task(self,payload):
        try: dto=_Task.model_validate(payload)
        except ValidationError: raise MurekaMusicContractError("Mureka returned an invalid task response.") from None
        status=_STATUS.get(dto.status)
        if status is None: raise MurekaMusicContractError("Mureka returned an unknown task status.")
        artifacts=()
        if status==GenerationTaskStatus.SUCCEEDED:
            if len(dto.choices)!=1: raise MurekaMusicContractError("Mureka succeeded response must contain exactly one song.")
            choice=dto.choices[0]
            if not choice.wav_url: raise MurekaMusicContractError("Mureka succeeded response has no documented WAV artifact.")
            artifacts=(GeneratedAudioArtifact(artifact_id=choice.id,download_url=choice.wav_url,content_type="audio/wav"),)
        created=datetime.fromtimestamp(dto.created_at,tz=timezone.utc) if dto.created_at is not None else None
        updated=datetime.fromtimestamp(dto.finished_at,tz=timezone.utc) if dto.finished_at is not None else created
        return MusicGenerationTask(provider=self.provider_name,provider_task_id=dto.id,external_correlation_id=None,
                                   normalized_status=status,artifacts=artifacts,created_at=created,updated_at=updated)
