"""Atomic local project creation for the web UI (Sprint 18.2)."""
import json,os
from pathlib import Path

from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from app.characters import CharacterRegistry,CharacterRegistryError

from .workflow import WorkflowStateMachine,write_workflow_state

ALLOWED_LANGUAGES=("ro","en","fr","de","es")
ALLOWED_TARGET_AGES=("2-5","6-8","9-12")
ALLOWED_ASPECT_RATIOS=("16:9","9:16","1:1")

class ProjectCreationError(RuntimeError): pass
class ProjectCreationConflict(ProjectCreationError): pass
class EpisodeCreationInput(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    title:str=Field(min_length=1,max_length=200); description:str=Field(min_length=1,max_length=2000)
    language:str; target_age:str; aspect_ratio:str; main_character_name:str|None=Field(default=None,max_length=100)
    main_character_description:str|None=Field(default=None,max_length=1000); selected_character_ids:tuple[str,...]=(); primary_character_id:str|None=None; episode_theme:str|None=Field(default=None,max_length=500)
    educational_goal:str|None=Field(default=None,max_length=1000); notes:str|None=Field(default=None,max_length=4000)
    @field_validator("title","description","main_character_name","main_character_description","primary_character_id","episode_theme","educational_goal","notes",mode="before")
    @classmethod
    def trim(cls,value): return value.strip() if isinstance(value,str) else value
    @field_validator("title")
    @classmethod
    def safe_title(cls,value):
        if ".." in value or "/" in value or "\\" in value or "\0" in value: raise ValueError("Title contains unsafe path characters.")
        return value
    @field_validator("language")
    @classmethod
    def language_allowed(cls,value):
        if value not in ALLOWED_LANGUAGES: raise ValueError("Unsupported language.")
        return value
    @field_validator("target_age")
    @classmethod
    def age_allowed(cls,value):
        if value not in ALLOWED_TARGET_AGES: raise ValueError("Unsupported target age.")
        return value
    @field_validator("aspect_ratio")
    @classmethod
    def ratio_allowed(cls,value):
        if value not in ALLOWED_ASPECT_RATIOS: raise ValueError("Unsupported aspect ratio.")
        return value
    @model_validator(mode="after")
    def character_coherent(self):
        if self.selected_character_ids:
            if len(self.selected_character_ids)!=len(set(self.selected_character_ids)): raise ValueError("Selected character IDs must be unique.")
            primary=self.primary_character_id or (self.selected_character_ids[0] if len(self.selected_character_ids)==1 else None)
            if primary is None: raise ValueError("A primary character is required when multiple characters are selected.")
            if primary not in self.selected_character_ids: raise ValueError("Primary character must be selected.")
            object.__setattr__(self,"primary_character_id",primary)
        elif not self.main_character_name or not self.main_character_description: raise ValueError("At least one registered character must be selected.")
        return self
class EpisodeManifest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    title:str; description:str; language:str; target_age:str; aspect_ratio:str
    episode_theme:str|None=None; educational_goal:str|None=None; notes:str|None=None
class MainCharacterManifest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    name:str; description:str
class CharacterSelectionManifest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    primary_character_id:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]*$"); selected_character_ids:tuple[str,...]=Field(min_length=1)
    @model_validator(mode="after")
    def coherent(self):
        if len(self.selected_character_ids)!=len(set(self.selected_character_ids)): raise ValueError("Character IDs must be unique.")
        if self.primary_character_id not in self.selected_character_ids: raise ValueError("Primary character must be selected.")
        return self
class WebProjectManifest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    project_id:str=Field(pattern=r"^[0-9]{3,}$"); episode:EpisodeManifest; main_character:MainCharacterManifest|None=None; characters:CharacterSelectionManifest|None=None
    @model_validator(mode="after")
    def character_format(self):
        if (self.main_character is None)==(self.characters is None): raise ValueError("Manifest must contain either legacy main_character or character IDs.")
        return self
    def resolve_primary_character(self,registry):
        return registry.get(self.characters.primary_character_id) if self.characters else self.main_character
    @property
    def selected_character_ids(self): return self.characters.selected_character_ids if self.characters else ()

class AtomicProjectCreationService:
    def __init__(self,projects_root,character_registry=None): self.root=Path(projects_root); self.character_registry=character_registry or CharacterRegistry(self.root.parent/"characters")
    def create(self,payload):
        data=EpisodeCreationInput.model_validate(payload)
        if data.selected_character_ids: self._validate_characters(data.selected_character_ids)
        self.root.mkdir(parents=True,exist_ok=True)
        lock=self.root/".creation.lock"; descriptor=None
        try:
            try: descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            except FileExistsError as error: raise ProjectCreationConflict("Another project creation is in progress.") from error
            project_id=self._next_id(); directory=self.root/project_id
            try: directory.mkdir()
            except FileExistsError as error: raise ProjectCreationConflict("Allocated project ID already exists.") from error
            episode=EpisodeManifest(**data.model_dump(exclude={"main_character_name","main_character_description","selected_character_ids","primary_character_id"}))
            if data.selected_character_ids:
                manifest=WebProjectManifest(project_id=project_id,episode=episode,characters=CharacterSelectionManifest(primary_character_id=data.primary_character_id,selected_character_ids=data.selected_character_ids))
            else: manifest=WebProjectManifest(project_id=project_id,episode=episode,main_character=MainCharacterManifest(name=data.main_character_name,description=data.main_character_description))
            for name in ("lyrics","music","visual","assets","output","workflow"): (directory/name).mkdir()
            self._write_json(directory/"project.json",manifest.model_dump(mode="json"))
            workflow,_transition=WorkflowStateMachine().approve(WorkflowStateMachine().initial(project_id),"episode")
            write_workflow_state(directory/"workflow"/"state.json",workflow)
            return manifest
        finally:
            if descriptor is not None: os.close(descriptor)
            lock.unlink(missing_ok=True)
    def _validate_characters(self,ids):
        try: profiles=self.character_registry.require_many(ids)
        except CharacterRegistryError as error: raise ValueError("Selected character does not exist in Character Registry.") from error
        for profile in profiles:
            reference=profile.visual_reference
            if reference is None or not reference.local_path.is_file(): raise ValueError(f"Character {profile.character_id} has no required reference image.")
            import hashlib
            if hashlib.sha256(reference.local_path.read_bytes()).hexdigest()!=reference.sha256: raise ValueError(f"Character {profile.character_id} reference image is invalid.")
    def _next_id(self):
        used={int(x.name) for x in self.root.iterdir() if x.is_dir() and x.name.isdigit()}
        value=8
        while value in used: value+=1
        return f"{value:03d}"
    @staticmethod
    def _write_json(path,payload):
        part=path.with_suffix(path.suffix+".part")
        try:
            part.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
            with part.open("r+b") as stream: os.fsync(stream.fileno())
            os.replace(part,path)
        finally: part.unlink(missing_ok=True)
