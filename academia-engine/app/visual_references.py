import hashlib
import json
import os
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field


class SceneVisualReference(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    reference_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    character_ids: tuple[str,...] = Field(min_length=1)
    local_path: Path
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str = Field(pattern=r"^image/(png|jpeg|webp)$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class PublishedVisualReference(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    https_url: str
    remote_asset_id: str | None=None


class CanonicalReferenceUrlUnavailableError(RuntimeError): pass
class VisualReferenceIntegrityError(RuntimeError): pass
class VisualReferencePublisherUnavailableError(RuntimeError): pass


class VisualReferencePublisher(Protocol):
    def publish(self,local_path:Path,sha256:str,content_type:str)->PublishedVisualReference: ...


class VisualReferencePublicationRegistry:
    """Durable SHA keyed publication mappings. It never uploads by itself."""
    def __init__(self,path:Path|None=None):
        self._path=path or Path.cwd()/".runtime"/"visual-reference-publications.json"

    def resolve(self,reference:SceneVisualReference)->str:
        self._verify(reference)
        values=self._load(); item=values.get(reference.sha256)
        if item is None: raise CanonicalReferenceUrlUnavailableError("Canonical reference URL is unavailable.")
        publication=PublishedVisualReference.model_validate(item)
        self._validate_https(publication.https_url)
        return publication.https_url

    def publish_once(self,reference:SceneVisualReference,publisher:VisualReferencePublisher)->PublishedVisualReference:
        self._verify(reference); values=self._load()
        if reference.sha256 in values:
            result=PublishedVisualReference.model_validate(values[reference.sha256]); self._validate_https(result.https_url); return result
        result=PublishedVisualReference.model_validate(publisher.publish(reference.local_path,reference.sha256,reference.content_type))
        if result.sha256!=reference.sha256: raise VisualReferenceIntegrityError("Published visual reference hash differs.")
        self._validate_https(result.https_url); values[reference.sha256]=result.model_dump(mode="json")
        self._save(values)
        return result

    def register_existing(self,reference:SceneVisualReference,https_url:str)->PublishedVisualReference:
        """Register an already-public durable asset without performing an upload."""
        self._verify(reference); self._validate_https(https_url); values=self._load()
        if reference.sha256 in values:
            result=PublishedVisualReference.model_validate(values[reference.sha256])
            self._validate_https(result.https_url)
            return result
        result=PublishedVisualReference(sha256=reference.sha256,https_url=https_url,
            remote_asset_id=reference.reference_id)
        values[reference.sha256]=result.model_dump(mode="json")
        self._save(values)
        return result

    def _save(self,values):
        self._path.parent.mkdir(parents=True,exist_ok=True); part=self._path.with_suffix(self._path.suffix+".part")
        try:
            part.write_text(json.dumps(values,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
            with part.open("r+b") as stream: os.fsync(stream.fileno())
            os.replace(part,self._path)
        finally: part.unlink(missing_ok=True)

    def _load(self):
        if not self._path.is_file(): return {}
        try: value=json.loads(self._path.read_text(encoding="utf-8"))
        except OSError as error: raise VisualReferencePublisherUnavailableError("Visual reference publication registry is unavailable.") from error
        except json.JSONDecodeError as error: raise VisualReferencePublisherUnavailableError("Visual reference publication registry is invalid.") from error
        if not isinstance(value,dict): raise CanonicalReferenceUrlUnavailableError("Visual reference publication registry is invalid.")
        return value

    @staticmethod
    def _verify(reference):
        if not reference.local_path.is_file(): raise VisualReferenceIntegrityError("Canonical scene reference is missing.")
        if hashlib.sha256(reference.local_path.read_bytes()).hexdigest()!=reference.sha256:
            raise VisualReferenceIntegrityError("Canonical scene reference failed integrity validation.")

    @staticmethod
    def _validate_https(url):
        parsed=urlparse(url)
        if parsed.scheme!="https" or not parsed.netloc: raise CanonicalReferenceUrlUnavailableError("Canonical reference requires a reusable HTTPS URL.")


LUCA_MAX_SCENE_REFERENCE=SceneVisualReference(reference_id="luca-max-canonical-v1",character_ids=("luca","max"),
    local_path=Path("assets/characters/luca-max-canonical-first-frame.png"),
    sha256="1bbcff69015d1f136a74148ecc76b2c46ae1328016bca567ded5c268b7dd79fb",content_type="image/png",
    width=1680,height=941)
LUCA_SCENE_REFERENCE=SceneVisualReference(reference_id="luca-canonical-v1",character_ids=("luca",),
    local_path=Path("assets/characters/luca-canonical.png"),
    sha256="91028d59fc504ecc0c43e5d5a9034e4cd949fa46b8e4ccab4106f13769bbe1f8",content_type="image/png",width=1536,height=1024)
MAX_SCENE_REFERENCE=SceneVisualReference(reference_id="max-canonical-v1",character_ids=("max",),
    local_path=Path("assets/characters/max-canonical.png"),
    sha256="de02c9a85e46489ca64a6ab782a1f321eb9822aab5e48505030c60b09a958587",content_type="image/png",width=1536,height=1024)
