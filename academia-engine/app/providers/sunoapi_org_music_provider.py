"""Adapter for the third-party gateway at docs.sunoapi.org (not Suno, Inc.)."""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any,Protocol

from pydantic import BaseModel,ConfigDict,Field,ValidationError

from app.models import GenerationTaskStatus
from app.music.contracts import GeneratedAudioArtifact,MusicGenerationRequest,MusicGenerationTask


class SunoApiOrgError(RuntimeError): pass
class SunoApiOrgConfigurationError(SunoApiOrgError): pass
class SunoApiOrgAuthenticationError(SunoApiOrgError): pass
class SunoApiOrgRateLimitError(SunoApiOrgError): pass
class SunoApiOrgTimeoutError(SunoApiOrgError): pass
class SunoApiOrgNetworkError(SunoApiOrgError): pass
class SunoApiOrgApiError(SunoApiOrgError): pass
class SunoApiOrgContractError(SunoApiOrgError): pass


MODELS={"V4","V4_5","V4_5PLUS","V4_5ALL","V5","V5_5"}
_STATUS={
    "PENDING":GenerationTaskStatus.SUBMITTED,
    "GENERATING":GenerationTaskStatus.PROCESSING,
    "TEXT_SUCCESS":GenerationTaskStatus.PROCESSING,
    "FIRST_SUCCESS":GenerationTaskStatus.PROCESSING,
    "SUCCESS":GenerationTaskStatus.SUCCEEDED,
    "FAILED":GenerationTaskStatus.FAILED,
    "CREATE_TASK_FAILED":GenerationTaskStatus.FAILED,
    "GENERATE_AUDIO_FAILED":GenerationTaskStatus.FAILED,
    "CALLBACK_EXCEPTION":GenerationTaskStatus.FAILED,
    "SENSITIVE_WORD_ERROR":GenerationTaskStatus.FAILED,
}


class SunoApiOrgTransport(Protocol):
    def request_json(self,method: str,path: str,payload: dict[str,Any]|None=None) -> dict[str,Any]: ...
    def download(self,url: str) -> bytes: ...


class UrllibSunoApiOrgTransport:
    def __init__(self,api_key: str,*,base_url: str="https://api.sunoapi.org",timeout_seconds: float=30) -> None:
        if not api_key.strip(): raise SunoApiOrgConfigurationError("sunoapi.org API key is not configured.")
        if not base_url.startswith("https://"): raise SunoApiOrgConfigurationError("sunoapi.org base URL must use HTTPS.")
        if timeout_seconds<=0: raise SunoApiOrgConfigurationError("sunoapi.org HTTP timeout is invalid.")
        self._key=api_key; self._base=base_url.rstrip("/"); self._timeout=timeout_seconds

    def request_json(self,method,path,payload=None):
        body=None if payload is None else json.dumps(payload,ensure_ascii=False).encode("utf-8")
        request=urllib.request.Request(self._base+path,data=body,method=method,headers={
            "Authorization":f"Bearer {self._key}","Content-Type":"application/json","Accept":"application/json"})
        raw=self._open(request)
        try: result=json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError,json.JSONDecodeError): raise SunoApiOrgContractError("sunoapi.org returned malformed JSON.") from None
        if not isinstance(result,dict): raise SunoApiOrgContractError("sunoapi.org returned an invalid response shape.")
        return result

    def download(self,url):
        # Never send gateway credentials to the transient artifact host.
        return self._open(urllib.request.Request(url,method="GET",headers={"Accept":"audio/mpeg"}))

    def _open(self,request):
        try:
            with urllib.request.urlopen(request,timeout=self._timeout) as response: return response.read()
        except urllib.error.HTTPError as error:
            if error.code in (401,403): raise SunoApiOrgAuthenticationError("sunoapi.org authentication failed.") from None
            if error.code in (405,429,430): raise SunoApiOrgRateLimitError(f"sunoapi.org request was rejected with status {error.code}.") from None
            raise SunoApiOrgApiError(f"sunoapi.org request failed with HTTP status {error.code}.") from None
        except (TimeoutError,socket.timeout): raise SunoApiOrgTimeoutError("sunoapi.org request timed out.") from None
        except (urllib.error.URLError,OSError): raise SunoApiOrgNetworkError("sunoapi.org request failed at the network boundary.") from None


class _SubmitData(BaseModel):
    model_config=ConfigDict(extra="ignore")
    taskId: str=Field(pattern=r"^[A-Za-z0-9_-]+$")


class _Song(BaseModel):
    model_config=ConfigDict(extra="ignore")
    id: str=Field(min_length=1,max_length=200)
    audioUrl: str=Field(min_length=1,max_length=4000)


class _Response(BaseModel):
    model_config=ConfigDict(extra="ignore")
    sunoData: tuple[_Song,...]=()


class _QueryData(BaseModel):
    model_config=ConfigDict(extra="ignore")
    taskId: str=Field(pattern=r"^[A-Za-z0-9_-]+$")
    status: str
    response: _Response|None=None


def _envelope(payload,contract):
    if payload.get("code")!=200: raise SunoApiOrgApiError(f"sunoapi.org returned provider code {payload.get('code')}.")
    try: return contract.model_validate(payload.get("data"))
    except ValidationError: raise SunoApiOrgContractError("sunoapi.org returned an invalid response contract.") from None


def flatten_lyrics(request: MusicGenerationRequest) -> str:
    return "\n\n".join(f"[{section.kind.value.title()}]\n"+"\n".join(line.text for line in section.lines)
                         for section in request.lyrics.sections)


def map_request(request: MusicGenerationRequest,*,model: str,callback_url: str) -> dict[str,Any]:
    if model not in MODELS: raise SunoApiOrgConfigurationError("sunoapi.org model is unsupported.")
    if not callback_url.startswith("https://"): raise SunoApiOrgConfigurationError("sunoapi.org callback URL must use HTTPS.")
    lyrics=flatten_lyrics(request); prompt_limit=3000 if model=="V4" else 5000
    title_limit=80 if model in {"V4","V4_5ALL"} else 100; style_limit=200 if model=="V4" else 1000
    plan=request.music_plan
    style=(f"{plan.musical_style}; mood: {plan.mood}; instruments: {', '.join(plan.instrumentation)}; "
           f"vocals: {plan.vocal_style}; tempo: {plan.tempo_bpm:g} BPM")
    if len(lyrics)>prompt_limit: raise SunoApiOrgContractError("Lyrics exceed the selected gateway model limit.")
    if len(request.title)>title_limit: raise SunoApiOrgContractError("Title exceeds the selected gateway model limit.")
    if len(style)>style_limit: raise SunoApiOrgContractError("Style exceeds the selected gateway model limit.")
    return {"customMode":True,"instrumental":False,"model":model,"callBackUrl":callback_url,
            "prompt":lyrics,"style":style,"title":request.title}


class SunoApiOrgMusicProvider:
    provider_name="sunoapi_org"
    def __init__(self,transport: SunoApiOrgTransport,*,model: str="V4_5",callback_url: str) -> None:
        if model not in MODELS: raise SunoApiOrgConfigurationError("sunoapi.org model is unsupported.")
        if not callback_url.startswith("https://"): raise SunoApiOrgConfigurationError("sunoapi.org callback URL must use HTTPS.")
        self._transport=transport; self._model=model; self._callback=callback_url

    @classmethod
    def from_environment(cls):
        try: timeout=float(os.getenv("SUNOAPI_ORG_TIMEOUT_SECONDS","30"))
        except ValueError: raise SunoApiOrgConfigurationError("sunoapi.org HTTP timeout is invalid.") from None
        transport=UrllibSunoApiOrgTransport(os.getenv("SUNOAPI_ORG_API_KEY",""),
            base_url=os.getenv("SUNOAPI_ORG_BASE_URL","https://api.sunoapi.org"),timeout_seconds=timeout)
        return cls(transport,model=os.getenv("SUNOAPI_ORG_MODEL","V4_5"),
                   callback_url=os.getenv("SUNOAPI_ORG_CALLBACK_URL",""))

    def submit_generation(self,request):
        dto=_envelope(self._transport.request_json("POST","/api/v1/generate",
                      map_request(request,model=self._model,callback_url=self._callback)),_SubmitData)
        return MusicGenerationTask(provider=self.provider_name,provider_task_id=dto.taskId,
                                   external_correlation_id=None,normalized_status=GenerationTaskStatus.SUBMITTED)

    def get_task_by_id(self,provider_task_id):
        if not provider_task_id or not provider_task_id.replace("_","").replace("-","").isalnum():
            raise SunoApiOrgContractError("sunoapi.org task ID is invalid.")
        query=urllib.parse.urlencode({"taskId":provider_task_id})
        dto=_envelope(self._transport.request_json("GET",f"/api/v1/generate/record-info?{query}"),_QueryData)
        if dto.taskId!=provider_task_id: raise SunoApiOrgContractError("sunoapi.org returned a different task ID.")
        status=_STATUS.get(dto.status)
        if status is None: raise SunoApiOrgContractError("sunoapi.org returned an unknown task status.")
        artifacts=()
        if status==GenerationTaskStatus.SUCCEEDED:
            songs=dto.response.sunoData if dto.response else ()
            if len(songs)!=2: raise SunoApiOrgContractError("sunoapi.org success must contain exactly two songs.")
            artifacts=tuple(GeneratedAudioArtifact(artifact_id=song.id,download_url=song.audioUrl,content_type="audio/mpeg") for song in songs)
        return MusicGenerationTask(provider=self.provider_name,provider_task_id=dto.taskId,
                                   external_correlation_id=None,normalized_status=status,artifacts=artifacts)

    def download_audio_bytes(self,artifact): return self._transport.download(artifact.download_url)
