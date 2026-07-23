from pydantic import BaseModel,ConfigDict,Field,ValidationError,field_validator

from app.lyrics_alignment import ProviderAlignedWord,TimestampedLyricsParseFailed,TimestampedLyricsRequestFailed
from .sunoapi_org_music_provider import SunoApiOrgError,SunoApiOrgTransport


class _TimestampedWord(BaseModel):
    model_config=ConfigDict(extra="ignore",strict=True,allow_inf_nan=False)
    word:str=Field(min_length=1); success:bool; startS:float; endS:float; palign:int|float|None=None
    @field_validator("startS","endS")
    @classmethod
    def non_negative(cls,value):
        if value<0: raise ValueError("timestamp is negative")
        return value

class _TimestampedData(BaseModel):
    model_config=ConfigDict(extra="ignore",strict=True)
    alignedWords:tuple[_TimestampedWord,...]=()
    hootCer:float|None=None; isStreamed:bool|None=None
    @field_validator("alignedWords",mode="before")
    @classmethod
    def tuple_words(cls,value): return tuple(value) if isinstance(value,list) else value

class TimestampedLyricsResult(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    words:tuple[ProviderAlignedWord,...]; confidence:float|None=None; instrumental:bool=False


class SunoTimestampedLyricsAdapter:
    endpoint="/api/v1/generate/get-timestamped-lyrics"
    def __init__(self,transport:SunoApiOrgTransport): self._transport=transport
    def retrieve(self,task_id,audio_id,*,instrumental=False):
        if not task_id or not audio_id: raise TimestampedLyricsRequestFailed("Suno taskId and audioId are required.")
        try: payload=self._transport.request_json("POST",self.endpoint,{"taskId":task_id,"audioId":audio_id})
        except SunoApiOrgError as error: raise TimestampedLyricsRequestFailed("Timestamped lyrics request failed.") from error
        if not isinstance(payload,dict) or payload.get("code")!=200:
            raise TimestampedLyricsRequestFailed("Timestamped lyrics provider returned an application error.")
        try: data=_TimestampedData.model_validate(payload.get("data"))
        except ValidationError as error: raise TimestampedLyricsParseFailed("Timestamped lyrics response is malformed.") from error
        accepted=tuple(value for value in data.alignedWords if value.success)
        if not accepted:
            if instrumental: return TimestampedLyricsResult(words=(),confidence=None,instrumental=True)
            raise TimestampedLyricsParseFailed("Timestamped lyrics contain no successful aligned words.")
        words=[]
        for value in accepted:
            if value.endS<value.startS: raise TimestampedLyricsParseFailed("Timestamped word ends before it starts.")
            words.append(ProviderAlignedWord(text=value.word,start_seconds=value.startS,end_seconds=value.endS))
        confidence=None if data.hootCer is None else max(0,min(1,1-data.hootCer))
        return TimestampedLyricsResult(words=tuple(words),confidence=confidence)
