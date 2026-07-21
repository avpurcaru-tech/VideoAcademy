import os
from typing import Mapping
from urllib.parse import urlparse

from pydantic import BaseModel,ConfigDict,SecretStr

from .kling_generation import KlingGenerationConfigurationError,KlingGenerationSettings


class KlingProviderConfigurationError(ValueError):
    def __init__(self,diagnostics):
        self.diagnostics=tuple(diagnostics)
        super().__init__("Kling provider configuration is invalid.")


class KlingProviderConfiguration(BaseModel):
    """Authoritative side-effect-free Kling environment configuration."""
    model_config=ConfigDict(extra="forbid",frozen=True)
    api_key: SecretStr
    base_url: str
    generation: KlingGenerationSettings
    schema_version: int=1

    @classmethod
    def from_environment(cls,environment: Mapping[str,str]|None=None):
        source=os.environ if environment is None else environment; diagnostics=[]
        key=source.get("KLING_API_KEY","")
        if not isinstance(key,str) or not key.strip(): diagnostics.append(("KLING_API_KEY","missing"))
        base=source.get("KLING_BASE_URL","https://api-singapore.klingai.com")
        parsed=urlparse(base) if isinstance(base,str) else None
        if parsed is None or parsed.scheme!="https" or not parsed.netloc:
            diagnostics.append(("KLING_BASE_URL","invalid URL"))
        try: generation=KlingGenerationSettings.from_environment(source)
        except KlingGenerationConfigurationError as error:
            message=str(error); field="KLING_GENERATION_SETTINGS"
            for candidate in ("KLING_RESOLUTION","KLING_DURATION","KLING_AUDIO","KLING_MULTI_SHOT"):
                if candidate in message: field=candidate; break
            if field=="KLING_GENERATION_SETTINGS" and getattr(error,"__cause__",None) is not None:
                locations={item.get("loc",(None,))[0] for item in error.__cause__.errors()}
                mapping={"resolution":"KLING_RESOLUTION","duration":"KLING_DURATION","audio":"KLING_AUDIO","multi_shot":"KLING_MULTI_SHOT"}
                field=mapping.get(next(iter(locations),None),field)
            diagnostics.append((field,"unsupported value")); generation=None
        if diagnostics: raise KlingProviderConfigurationError(diagnostics)
        return cls(api_key=SecretStr(key.strip()),base_url=base.rstrip("/"),generation=generation)

    def validate_configuration(self):
        return self
