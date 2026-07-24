"""Provider-neutral character continuity registry (Sprint 17.4)."""
import json,os,re,unicodedata
from enum import Enum
from pathlib import Path

from pydantic import BaseModel,ConfigDict,Field

from app.scene_planning import semantic_sha256

CHARACTER_REGISTRY_SCHEMA_VERSION="1.0"
CHARACTER_REGISTRY_VERSION="17.4.0"

class CharacterRegistryError(RuntimeError): pass
class CharacterRegistryPersistenceError(CharacterRegistryError): pass
class CharacterRole(str,Enum): MAIN="main"; SUPPORTING="supporting"; BACKGROUND="background"

class CharacterAppearance(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    hair_color:str="unspecified"; hair_style:str="unspecified"; eye_color:str="unspecified"
    skin_tone:str="unspecified"; body_type:str="unspecified"; height_category:str="unspecified"
    distinctive_features:tuple[str,...]=()
class CharacterWardrobe(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    top:str="unspecified"; bottom:str="unspecified"; shoes:str="unspecified"; accessories:tuple[str,...]=()
class CharacterIdentity(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    character_id:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$"); canonical_name:str; aliases:tuple[str,...]=()
    role:CharacterRole=CharacterRole.SUPPORTING; appearance:CharacterAppearance=CharacterAppearance()
    wardrobe:CharacterWardrobe=CharacterWardrobe(); fixed_attributes:tuple[str,...]=(); variable_attributes:tuple[str,...]=()
class CharacterRegistryWarning(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    code:str; alias:str; character_ids:tuple[str,...]=()
class CharacterRegistryDependencyMetadata(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    source_characters_sha256:str=Field(pattern=r"^[a-f0-9]{64}$"); registry_version:str
class CharacterRegistry(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    project_id:str; schema_version:str=CHARACTER_REGISTRY_SCHEMA_VERSION; characters:tuple[CharacterIdentity,...]
    dependency_metadata:CharacterRegistryDependencyMetadata; semantic_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    def resolve_alias(self,alias):
        key=_normal(alias); matches=tuple(x.character_id for x in self.characters
            if key in {_normal(x.canonical_name),_normal(x.character_id),*(_normal(a) for a in x.aliases)})
        if len(matches)==1: return matches[0],None
        code="ambiguous_character_alias" if matches else "unknown_character_alias"
        return None,CharacterRegistryWarning(code=code,alias=alias,character_ids=matches)
    def require(self,character_id): return next((x for x in self.characters if x.character_id==character_id),None)
    def dependency_sha256(self,character_ids):
        identities=tuple(x for x in self.characters if x.character_id in set(character_ids))
        return semantic_sha256([x.model_dump(mode="json") for x in identities])

def _normal(value): return unicodedata.normalize("NFKC",str(value)).strip().casefold()
def stable_character_id(canonical_name):
    folded=unicodedata.normalize("NFKD",canonical_name).encode("ascii","ignore").decode().casefold()
    slug=re.sub(r"[^a-z0-9]+","-",folded).strip("-") or "character"
    return slug[:80]

class CharacterRegistryBuilder:
    def __init__(self,*,registry_version=CHARACTER_REGISTRY_VERSION): self.registry_version=registry_version
    def dependencies(self,characters):
        values=tuple(CharacterIdentity.model_validate(x) for x in characters)
        return CharacterRegistryDependencyMetadata(source_characters_sha256=semantic_sha256(
            [x.model_dump(mode="json") for x in values]),registry_version=self.registry_version)
    def build(self,project_id,characters):
        values=tuple(sorted((CharacterIdentity.model_validate(x) for x in characters),key=lambda x:x.character_id))
        dependencies=self.dependencies(values); core={"project_id":project_id,"schema_version":CHARACTER_REGISTRY_SCHEMA_VERSION,
            "characters":[x.model_dump(mode="json") for x in values],"dependency_metadata":dependencies.model_dump(mode="json")}
        return CharacterRegistry(**core,semantic_sha256=semantic_sha256(core))
    def from_names(self,project_id,names):
        unique={_normal(x):x for x in names if str(x).strip()}
        return self.build(project_id,tuple(CharacterIdentity(character_id=stable_character_id(name),canonical_name=name) for name in unique.values()))

def write_character_registry(path,registry):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(path.suffix+".part")
    try:
        payload=CharacterRegistry.model_validate(registry).model_dump(mode="json")
        part.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        with part.open("r+b") as stream: os.fsync(stream.fileno())
        os.replace(part,path)
    except OSError as error: raise CharacterRegistryPersistenceError("Character registry could not be persisted.") from error
    finally: part.unlink(missing_ok=True)
def read_character_registry(path):
    try: return CharacterRegistry.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except Exception as error: raise CharacterRegistryPersistenceError("Character registry is invalid.") from error

class CharacterContinuityRepository:
    def __init__(self,path): self.path=Path(path)
    def resolve_or_build(self,*,project_id,characters,builder):
        expected=builder.dependencies(characters); existing=read_character_registry(self.path) if self.path.is_file() else None
        if existing is not None and existing.project_id==project_id and existing.dependency_metadata==expected: return existing,True
        value=builder.build(project_id,characters); write_character_registry(self.path,value); return value,False
