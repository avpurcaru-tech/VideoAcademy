"""Adapter for the third-party gateway at docs.sunoapi.org (not Suno, Inc.)."""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any,Protocol

import requests
from pydantic import BaseModel,ConfigDict,Field,ValidationError

from app.models import GenerationTaskStatus
from app.music.contracts import GeneratedAudioArtifact,MusicGenerationRequest,MusicGenerationTask


class SunoApiOrgError(RuntimeError):
    def __init__(self,message: str,*,phase: str|None=None,http_status: int|None=None,
                 provider_code: int|str|None=None,provider_message: str|None=None,
                 provider_task_id: str|None=None,provider_request_id: str|None=None,
                 retry_after: str|None=None):
        super().__init__(message); self.phase=phase; self.http_status=http_status
        self.provider_code=provider_code; self.provider_message=provider_message
        self.provider_task_id=provider_task_id; self.provider_request_id=provider_request_id
        self.retry_after=retry_after
class SunoApiOrgConfigurationError(SunoApiOrgError): pass
class SunoApiOrgAuthenticationError(SunoApiOrgError): pass
class SunoApiOrgRateLimitError(SunoApiOrgError): pass
class SunoApiOrgTimeoutError(SunoApiOrgError): pass
class SunoApiOrgNetworkError(SunoApiOrgError): pass
class SunoApiOrgAmbiguousTransportError(SunoApiOrgNetworkError): pass
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
        except (UnicodeDecodeError,json.JSONDecodeError): raise SunoApiOrgContractError(
            "sunoapi.org returned malformed JSON.",phase="response_parsing") from None
        if not isinstance(result,dict): raise SunoApiOrgContractError(
            "sunoapi.org returned an invalid response shape.",phase="response_parsing")
        return result

    def download(self,url):
        # Never send gateway credentials to the transient artifact host.
        return self._open(urllib.request.Request(url,method="GET",headers={"Accept":"audio/mpeg"}))

    def _open(self,request):
        try:
            with urllib.request.urlopen(request,timeout=self._timeout) as response: return response.read()
        except urllib.error.HTTPError as error:
            details=_safe_error_details(error); kwargs=dict(phase="http_failure",http_status=error.code,
                retry_after=_safe_header(error.headers,"Retry-After"),**details)
            if error.code in (401,403): raise SunoApiOrgAuthenticationError("sunoapi.org authentication failed.",**kwargs) from None
            if error.code in (405,429,430): raise SunoApiOrgRateLimitError("sunoapi.org request was rate or credit limited.",**kwargs) from None
            raise SunoApiOrgApiError("sunoapi.org HTTP request failed.",**kwargs) from None
        except (TimeoutError,socket.timeout): raise SunoApiOrgTimeoutError(
            "sunoapi.org submit outcome is ambiguous.",phase="ambiguous_transport") from None
        except urllib.error.URLError as error:
            reason=getattr(error,"reason",None)
            if isinstance(reason,(socket.gaierror,ConnectionRefusedError)):
                raise SunoApiOrgNetworkError("sunoapi.org connection failed before a response.",phase="network_before_response") from None
            raise SunoApiOrgAmbiguousTransportError("sunoapi.org transport outcome is ambiguous.",phase="ambiguous_transport") from None
        except OSError: raise SunoApiOrgAmbiguousTransportError(
            "sunoapi.org transport outcome is ambiguous.",phase="ambiguous_transport") from None


class RequestsSunoApiOrgTransport:
    """Production transport matching the gateway's documented requests examples."""
    def __init__(self,api_key: str,*,base_url: str="https://api.sunoapi.org",timeout_seconds: float=30) -> None:
        if not api_key.strip(): raise SunoApiOrgConfigurationError("sunoapi.org API key is not configured.")
        if not base_url.startswith("https://"): raise SunoApiOrgConfigurationError("sunoapi.org base URL must use HTTPS.")
        if timeout_seconds<=0: raise SunoApiOrgConfigurationError("sunoapi.org HTTP timeout is invalid.")
        self._key=api_key; self._base=base_url.rstrip("/"); self._timeout=timeout_seconds

    def request_json(self,method,path,payload=None):
        headers={"Authorization":f"Bearer {self._key}","Content-Type":"application/json"}
        try:
            if method=="POST": response=requests.post(self._base+path,json=payload,headers=headers,timeout=self._timeout)
            elif method=="GET": response=requests.get(self._base+path,headers=headers,timeout=self._timeout)
            else: raise SunoApiOrgConfigurationError("sunoapi.org HTTP method is unsupported.")
        except requests.ConnectTimeout: raise SunoApiOrgNetworkError(
            "sunoapi.org connection failed before a response.",phase="network_before_response") from None
        except requests.Timeout: raise SunoApiOrgTimeoutError(
            "sunoapi.org submit outcome is ambiguous.",phase="ambiguous_transport") from None
        except requests.ConnectionError: raise SunoApiOrgAmbiguousTransportError(
            "sunoapi.org transport outcome is ambiguous.",phase="ambiguous_transport") from None
        except requests.RequestException: raise SunoApiOrgAmbiguousTransportError(
            "sunoapi.org transport outcome is ambiguous.",phase="ambiguous_transport") from None
        parsed=_requests_json(response)
        if response.status_code>=400:
            details=_safe_payload_details(parsed); kwargs=dict(phase="http_failure",http_status=response.status_code,
                retry_after=str(response.headers.get("Retry-After"))[:100] if response.headers.get("Retry-After") else None,**details)
            if response.status_code in (401,403): raise SunoApiOrgAuthenticationError("sunoapi.org authentication failed.",**kwargs)
            if response.status_code in (405,429,430): raise SunoApiOrgRateLimitError("sunoapi.org request was rate or credit limited.",**kwargs)
            raise SunoApiOrgApiError("sunoapi.org HTTP request failed.",**kwargs)
        if parsed is None: raise SunoApiOrgContractError("sunoapi.org returned malformed JSON.",phase="response_parsing")
        return parsed

    def download(self,url):
        try: response=requests.get(url,timeout=self._timeout)
        except requests.RequestException: raise SunoApiOrgNetworkError("Audio retrieval failed at the network boundary.",phase="artifact_download") from None
        if response.status_code>=400: raise SunoApiOrgApiError("Audio retrieval failed.",phase="artifact_download",http_status=response.status_code)
        return response.content


def _requests_json(response):
    try: value=response.json()
    except (ValueError,TypeError): return None
    return value if isinstance(value,dict) else None


def _safe_payload_details(payload):
    if not isinstance(payload,dict): payload={}
    data=payload.get("data") if isinstance(payload.get("data"),dict) else {}
    task_id=data.get("taskId") if isinstance(data.get("taskId"),str) else None
    if task_id and not task_id.replace("_","").replace("-","").isalnum(): task_id=None
    request_id=payload.get("requestId") or payload.get("traceId")
    if not isinstance(request_id,str) or not request_id.replace("_","").replace("-","").isalnum(): request_id=None
    code=payload.get("code") if isinstance(payload.get("code"),(int,str)) else None
    return {"provider_code":code,"provider_message":_safe_message(payload.get("msg")),
            "provider_task_id":task_id,"provider_request_id":request_id}


def _safe_header(headers,name):
    value=headers.get(name) if headers is not None else None
    return str(value)[:100] if value is not None else None


def _safe_message(value):
    if not isinstance(value,str): return None
    text=" ".join(value.split())[:200]; lowered=text.casefold()
    if any(marker in lowered for marker in ("http://","https://","authorization","bearer","api_key","prompt","lyrics","callback")):
        return None
    return text or None


def _safe_error_details(error):
    try: raw=error.read(65536)
    except Exception: raw=b""
    try: payload=json.loads(raw.decode("utf-8"))
    except Exception: payload={}
    if not isinstance(payload,dict): payload={}
    data=payload.get("data") if isinstance(payload.get("data"),dict) else {}
    task_id=data.get("taskId") if isinstance(data.get("taskId"),str) else None
    if task_id and not task_id.replace("_","").replace("-","").isalnum(): task_id=None
    request_id=payload.get("requestId") or payload.get("traceId")
    if not isinstance(request_id,str) or not request_id.replace("_","").replace("-","").isalnum(): request_id=None
    code=payload.get("code") if isinstance(payload.get("code"),(int,str)) else None
    return {"provider_code":code,"provider_message":_safe_message(payload.get("msg")),
            "provider_task_id":task_id,"provider_request_id":request_id}


class _SubmitData(BaseModel):
    model_config=ConfigDict(extra="ignore")
    taskId: str=Field(pattern=r"^[A-Za-z0-9_-]+$")


class SunoApiOrgAccountStatus(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    authentication_valid: bool
    credits_remaining: int|None=Field(default=None,ge=0)
    http_status: int|None=None
    provider_code: int|str|None=None


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
    if payload.get("code")!=200:
        data=payload.get("data") if isinstance(payload.get("data"),dict) else {}
        task_id=data.get("taskId") if isinstance(data.get("taskId"),str) else None
        raise SunoApiOrgApiError("sunoapi.org returned an application error.",phase="provider_application",
            provider_code=payload.get("code"),provider_message=_safe_message(payload.get("msg")),provider_task_id=task_id)
    try: return contract.model_validate(payload.get("data"))
    except ValidationError: raise SunoApiOrgContractError(
        "sunoapi.org returned an invalid response contract.",phase="response_parsing") from None


def flatten_lyrics(request: MusicGenerationRequest) -> str:
    return "\n\n".join(f"[{section.kind.value.title()}]\n"+"\n".join(line.text for line in section.lines)
                         for section in request.lyrics.sections)


def map_request(request: MusicGenerationRequest,*,model: str,callback_url: str) -> dict[str,Any]:
    if model not in MODELS: raise SunoApiOrgConfigurationError("sunoapi.org model is unsupported.")
    if not _valid_https_url(callback_url): raise SunoApiOrgConfigurationError("sunoapi.org callback URL must be a valid HTTPS URL.")
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
        if not _valid_https_url(callback_url): raise SunoApiOrgConfigurationError("sunoapi.org callback URL must be a valid HTTPS URL.")
        self._transport=transport; self._model=model; self._callback=callback_url

    @classmethod
    def from_environment(cls,*,require_explicit_model: bool=False):
        try: timeout=float(os.getenv("SUNOAPI_ORG_TIMEOUT_SECONDS","30"))
        except ValueError: raise SunoApiOrgConfigurationError("sunoapi.org HTTP timeout is invalid.") from None
        transport=RequestsSunoApiOrgTransport(os.getenv("SUNOAPI_ORG_API_KEY",""),
            base_url=os.getenv("SUNOAPI_ORG_BASE_URL","https://api.sunoapi.org"),timeout_seconds=timeout)
        model=os.getenv("SUNOAPI_ORG_MODEL")
        if require_explicit_model and not model: raise SunoApiOrgConfigurationError("sunoapi.org model is not configured.")
        return cls(transport,model=model or "V4_5",
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


class SunoApiOrgAccountClient:
    """Read-only account client; it has no generation method."""
    def __init__(self,transport: SunoApiOrgTransport) -> None: self._transport=transport

    @classmethod
    def from_environment(cls):
        try: timeout=float(os.getenv("SUNOAPI_ORG_TIMEOUT_SECONDS","30"))
        except ValueError: raise SunoApiOrgConfigurationError("sunoapi.org HTTP timeout is invalid.") from None
        return cls(RequestsSunoApiOrgTransport(os.getenv("SUNOAPI_ORG_API_KEY",""),
            base_url=os.getenv("SUNOAPI_ORG_BASE_URL","https://api.sunoapi.org"),timeout_seconds=timeout))

    def get_account_status(self) -> SunoApiOrgAccountStatus:
        payload=self._transport.request_json("GET","/api/v1/generate/credit")
        code=payload.get("code")
        if code!=200: raise SunoApiOrgApiError("sunoapi.org returned an account error.",phase="provider_application",
            provider_code=code,provider_message=_safe_message(payload.get("msg")))
        credits=payload.get("data")
        if isinstance(credits,bool) or not isinstance(credits,int) or credits<0:
            raise SunoApiOrgContractError("sunoapi.org returned an invalid credit response.",phase="response_parsing")
        return SunoApiOrgAccountStatus(authentication_valid=True,credits_remaining=credits,http_status=200,provider_code=code)


def _valid_https_url(value: str) -> bool:
    try: parsed=urllib.parse.urlparse(value)
    except (TypeError,ValueError): return False
    return parsed.scheme=="https" and bool(parsed.netloc) and not parsed.username and not parsed.password
