"""Adapter for the third-party gateway at docs.sunoapi.org (not Suno, Inc.)."""
from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import re
from typing import Any,Protocol

import requests
from pydantic import AliasChoices,BaseModel,ConfigDict,Field,ValidationError,field_validator

from app.models import GenerationTaskStatus
from app.music.contracts import GeneratedAudioArtifact,MusicGenerationRequest,MusicGenerationTask


class SunoApiOrgError(RuntimeError):
    def __init__(self,message: str,*,phase: str|None=None,http_status: int|None=None,
                 provider_code: int|str|None=None,provider_message: str|None=None,
                 provider_task_id: str|None=None,provider_request_id: str|None=None,
                 retry_after: str|None=None,response_shape: tuple[str,...]=()):
        super().__init__(message); self.phase=phase; self.http_status=http_status
        self.provider_code=provider_code; self.provider_message=provider_message
        self.provider_task_id=provider_task_id; self.provider_request_id=provider_request_id
        self.retry_after=retry_after
        self.response_shape=response_shape
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
        shape=_submit_response_shape if method=="POST" and path=="/api/v1/generate" else _account_response_shape
        if parsed is _INVALID_JSON: raise SunoApiOrgContractError(
            "sunoapi.org returned malformed JSON.",phase="response_parsing",
            response_shape=shape(_INVALID_JSON))
        if not isinstance(parsed,dict): raise SunoApiOrgContractError(
            "sunoapi.org returned an invalid response shape.",phase="response_parsing",
            response_shape=shape(parsed))
        return parsed

    def download(self,url):
        try: response=requests.get(url,timeout=self._timeout)
        except requests.RequestException: raise SunoApiOrgNetworkError("Audio retrieval failed at the network boundary.",phase="artifact_download") from None
        if response.status_code>=400: raise SunoApiOrgApiError("Audio retrieval failed.",phase="artifact_download",http_status=response.status_code)
        return response.content


_INVALID_JSON=object()


def _requests_json(response):
    try: value=response.json()
    except (ValueError,TypeError): return _INVALID_JSON
    return value


def _safe_json_type(value: Any) -> str:
    if value is _INVALID_JSON: return "invalid-json"
    if value is None: return "null"
    if isinstance(value,bool): return "boolean"
    if isinstance(value,int): return "integer"
    if isinstance(value,float): return "number"
    if isinstance(value,dict): return "object"
    if isinstance(value,list): return "list"
    if isinstance(value,str): return "string"
    return "unknown"


def _account_response_shape(payload: Any) -> tuple[str,...]:
    lines=[f"Response root type: {_safe_json_type(payload)}"]
    for field in ("code","msg","data"):
        present=isinstance(payload,dict) and field in payload
        lines.append(f"Field present: {field} {'yes' if present else 'no'}")
        lines.append(f"Field type: {field} {_safe_json_type(payload[field]) if present else 'absent'}")
    return tuple(lines)


_SAFE_FIELD_NAME=re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _submit_response_shape(payload: Any) -> tuple[str,...]:
    lines=[f"Response root type: {_safe_json_type(payload)}"]
    for path in ("code","msg","data","data.taskId","data.task_id","taskId","task_id"):
        current: Any=payload; present=True
        for part in path.split("."):
            if not isinstance(current,dict) or part not in current:
                present=False; break
            current=current[part]
        lines.append(f"Field present: {path} {'yes' if present else 'no'}")
        lines.append(f"Field type: {path} {_safe_json_type(current) if present else 'absent'}")
    data=payload.get("data") if isinstance(payload,dict) else None
    if isinstance(data,dict):
        for name in sorted(data):
            if isinstance(name,str) and _SAFE_FIELD_NAME.fullmatch(name):
                lines.append(f"Data field: {name} {_safe_json_type(data[name])}")
    return tuple(lines)


def _valid_task_id(value: Any) -> str|None:
    if not isinstance(value,str) or not value or not value.replace("_","").replace("-","").isalnum(): return None
    return value


def _extract_submit_task_id(payload: Any) -> str|None:
    if not isinstance(payload,dict): return None
    data=payload.get("data")
    candidates=[]
    if isinstance(data,dict): candidates.extend((data.get("taskId"),data.get("task_id")))
    candidates.extend((payload.get("taskId"),payload.get("task_id")))
    return next((task_id for value in candidates if (task_id:=_valid_task_id(value)) is not None),None)


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
    taskId: str=Field(pattern=r"^[A-Za-z0-9_-]+$",validation_alias=AliasChoices("taskId","task_id"))


class _SubmitResponse(BaseModel):
    model_config=ConfigDict(extra="ignore",strict=True)
    code: int
    msg: str
    data: _SubmitData


class SunoApiOrgAccountStatus(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    authentication_valid: bool
    credits_remaining: int|None=Field(default=None,ge=0)
    http_status: int|None=None
    provider_code: int|str|None=None


class _CreditResponse(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    code: int
    msg: str
    data: int=Field(ge=0)

    @field_validator("data",mode="before")
    @classmethod
    def normalize_integral_credit_balance(cls,value: Any) -> int:
        if isinstance(value,bool): raise ValueError("credit balance must be numeric")
        if isinstance(value,int): return value
        if isinstance(value,float) and math.isfinite(value) and value>=0 and value.is_integer():
            return int(value)
        raise ValueError("credit balance must be a non-negative integral number")


class _Song(BaseModel):
    model_config=ConfigDict(extra="ignore")
    id: str=Field(default="",max_length=200)
    audioUrl: str=Field(default="",max_length=4000)
    duration: float|int|None=Field(default=None,gt=0)

    @field_validator("duration")
    @classmethod
    def duration_is_not_boolean(cls,value):
        if isinstance(value,bool): raise ValueError("duration must be numeric")
        return value


class _Response(BaseModel):
    model_config=ConfigDict(extra="ignore")
    sunoData: tuple[_Song,...]=()


class _QueryData(BaseModel):
    model_config=ConfigDict(extra="ignore")
    taskId: str=Field(pattern=r"^[A-Za-z0-9_-]+$")
    status: str
    response: _Response|None=None


class _ObservedSong(BaseModel):
    model_config=ConfigDict(extra="ignore",strict=True,allow_inf_nan=False)
    id: str=Field(min_length=1,max_length=200)
    audio_url: str=Field(default="",max_length=4000)
    duration: float|int|None=Field(default=None,gt=0)

    @field_validator("duration")
    @classmethod
    def duration_is_not_boolean(cls,value):
        if isinstance(value,bool): raise ValueError("duration must be numeric")
        return value


class _ObservedQueryData(BaseModel):
    model_config=ConfigDict(extra="ignore",strict=True)
    task_id: str=Field(pattern=r"^[A-Za-z0-9_-]+$")
    callbackType: str
    data: tuple[_ObservedSong,...]=()

    @field_validator("data",mode="before")
    @classmethod
    def json_array_to_tuple(cls,value):
        return tuple(value) if isinstance(value,list) else value


def _envelope(payload,contract):
    if payload.get("code")!=200:
        data=payload.get("data") if isinstance(payload.get("data"),dict) else {}
        task_id=data.get("taskId") if isinstance(data.get("taskId"),str) else None
        raise SunoApiOrgApiError("sunoapi.org returned an application error.",phase="provider_application",
            provider_code=payload.get("code"),provider_message=_safe_message(payload.get("msg")),provider_task_id=task_id)
    try: return contract.model_validate(payload.get("data"))
    except ValidationError: raise SunoApiOrgContractError(
        "sunoapi.org returned an invalid response contract.",phase="response_parsing") from None


class SunoCallbackParser:
    """Normalize submit, query, and callback envelopes without retaining their raw bodies."""
    _CALLBACK_STATUS={"text":GenerationTaskStatus.PROCESSING,"first":GenerationTaskStatus.PROCESSING,
                      "complete":GenerationTaskStatus.SUCCEEDED}

    def parse(self,payload: Any) -> MusicGenerationTask:
        task_id=_extract_submit_task_id(payload); shape=_submit_response_shape(payload)
        if not isinstance(payload,dict):
            self._contract_error(task_id,shape)
        code=payload.get("code")
        if not isinstance(code,int) or isinstance(code,bool): self._contract_error(task_id,shape)
        if code!=200:
            raise SunoApiOrgApiError("sunoapi.org returned an application error.",phase="provider_application",
                provider_code=code,provider_message=_safe_message(payload.get("msg")),provider_task_id=task_id,
                response_shape=shape)
        data=payload.get("data")
        if isinstance(data,dict) and "callbackType" in data:
            return self._callback(data,task_id,shape)
        try: submitted=_SubmitResponse.model_validate(payload)
        except ValidationError: self._contract_error(task_id,shape)
        return MusicGenerationTask(provider="sunoapi_org",provider_task_id=submitted.data.taskId,
            external_correlation_id=None,normalized_status=GenerationTaskStatus.SUBMITTED)

    def _callback(self,data: dict[str,Any],task_id: str|None,shape: tuple[str,...]) -> MusicGenerationTask:
        try: dto=_ObservedQueryData.model_validate(data)
        except ValidationError: self._contract_error(task_id,shape)
        status=self._CALLBACK_STATUS.get(dto.callbackType)
        if status is None:
            raise SunoApiOrgContractError("sunoapi.org returned an unknown callback type.",phase="response_parsing",
                                          provider_task_id=dto.task_id,response_shape=shape)
        artifacts=()
        if status==GenerationTaskStatus.SUCCEEDED:
            if not dto.data or any(not song.audio_url for song in dto.data):
                raise SunoApiOrgContractError("sunoapi.org completion contains unavailable audio artifacts.",
                    phase="response_parsing",provider_task_id=dto.task_id,response_shape=shape)
            artifacts=tuple(GeneratedAudioArtifact(artifact_id=song.id,download_url=song.audio_url,
                content_type="audio/mpeg",duration_seconds=float(song.duration) if song.duration is not None else None)
                for song in dto.data)
        return MusicGenerationTask(provider="sunoapi_org",provider_task_id=dto.task_id,
            external_correlation_id=None,normalized_status=status,artifacts=artifacts)

    @staticmethod
    def _contract_error(task_id,shape):
        raise SunoApiOrgContractError("sunoapi.org returned an invalid generation response.",phase="response_parsing",
                                      provider_task_id=task_id,response_shape=shape)


def _query_envelope(payload: dict[str,Any]) -> MusicGenerationTask:
    """Compatibility wrapper around the shared production parser."""
    data=payload.get("data") if isinstance(payload,dict) else None
    if isinstance(data,dict) and "callbackType" in data: return SunoCallbackParser().parse(payload)
    dto=_envelope(payload,_QueryData)
    status=_STATUS.get(dto.status)
    if status is None: raise SunoApiOrgContractError("sunoapi.org returned an unknown task status.")
    artifacts=()
    if status==GenerationTaskStatus.SUCCEEDED:
        songs=dto.response.sunoData if dto.response else ()
        if len(songs)!=2 or any(not song.id or not song.audioUrl for song in songs):
            raise SunoApiOrgContractError("sunoapi.org success must contain exactly two available songs.")
        artifacts=tuple(GeneratedAudioArtifact(artifact_id=song.id,download_url=song.audioUrl,content_type="audio/mpeg",
            duration_seconds=float(song.duration) if song.duration is not None else None) for song in songs)
    return MusicGenerationTask(provider="sunoapi_org",provider_task_id=dto.taskId,
        external_correlation_id=None,normalized_status=status,artifacts=artifacts)


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
        self._callback_parser=SunoCallbackParser()

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
        return self._callback_parser.parse(self._transport.request_json("POST","/api/v1/generate",
                                           map_request(request,model=self._model,callback_url=self._callback)))

    def parse_callback(self,payload: Any) -> MusicGenerationTask:
        return self._callback_parser.parse(payload)

    def get_task_by_id(self,provider_task_id):
        if not provider_task_id or not provider_task_id.replace("_","").replace("-","").isalnum():
            raise SunoApiOrgContractError("sunoapi.org task ID is invalid.")
        query=urllib.parse.urlencode({"taskId":provider_task_id})
        payload=self._transport.request_json("GET",f"/api/v1/generate/record-info?{query}")
        data=payload.get("data") if isinstance(payload,dict) else None
        task=(self._callback_parser.parse(payload) if isinstance(data,dict) and "callbackType" in data
              else _query_envelope(payload))
        if task.provider_task_id!=provider_task_id: raise SunoApiOrgContractError("sunoapi.org returned a different task ID.")
        return task

    def download_audio_bytes(self,artifact): return self._transport.download(artifact.download_url)

    def get_timestamped_lyrics(self,provider_task_id,audio_id,*,instrumental=False):
        from .suno_timestamped_lyrics import SunoTimestampedLyricsAdapter
        return SunoTimestampedLyricsAdapter(self._transport).retrieve(provider_task_id,audio_id,instrumental=instrumental)


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
        code=payload.get("code") if isinstance(payload,dict) else None
        if not isinstance(code,int) or isinstance(code,bool):
            raise SunoApiOrgContractError(
                "sunoapi.org returned an invalid credit response.",phase="response_parsing",
                response_shape=_account_response_shape(payload))
        if code!=200: raise SunoApiOrgApiError("sunoapi.org returned an account error.",phase="provider_application",
            provider_code=code,
            provider_message=_safe_message(payload.get("msg")) if isinstance(payload,dict) else None)
        try: response=_CreditResponse.model_validate(payload)
        except ValidationError: raise SunoApiOrgContractError(
            "sunoapi.org returned an invalid credit response.",phase="response_parsing",
            response_shape=_account_response_shape(payload)) from None
        return SunoApiOrgAccountStatus(authentication_valid=True,credits_remaining=response.data,
                                       http_status=200,provider_code=response.code)


def _valid_https_url(value: str) -> bool:
    try: parsed=urllib.parse.urlparse(value)
    except (TypeError,ValueError): return False
    return parsed.scheme=="https" and bool(parsed.netloc) and not parsed.username and not parsed.password
