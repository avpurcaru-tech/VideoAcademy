import os
import hashlib
from pathlib import Path

from pydantic import ValidationError

from .contracts import CanonicalCharacterProfile


class CharacterRegistryError(RuntimeError): pass
class CharacterNotFoundError(CharacterRegistryError): pass
class CorruptedCharacterRecordError(CharacterRegistryError): pass
class ConflictingCharacterProfileError(CharacterRegistryError): pass


class CharacterRegistry:
    def __init__(self, root: Path | None = None): self._root = Path(root or Path.cwd()/".runtime"/"characters")
    def path_for(self, character_id: str) -> Path:
        try: safe = CanonicalCharacterProfile.model_validate({"character_id":character_id,"name":"x",
            "canonical_description":"x","personality_traits":["x"],"behavior_rules":["x"],"negative_rules":["x"]}).character_id
        except ValidationError as error: raise CharacterRegistryError("Character ID is invalid.") from error
        return self._root/f"{safe}.json"
    def register(self, profile: CanonicalCharacterProfile) -> Path:
        profile=CanonicalCharacterProfile.model_validate(profile); destination=self.path_for(profile.character_id)
        if profile.visual_reference is not None:
            reference=profile.visual_reference
            if not reference.local_path.is_file():
                raise CharacterRegistryError("Canonical character visual reference is missing.")
            if hashlib.sha256(reference.local_path.read_bytes()).hexdigest()!=reference.sha256:
                raise CharacterRegistryError("Canonical character visual reference failed integrity validation.")
        if destination.exists():
            existing=self.get(profile.character_id)
            if existing==profile: return destination
            raise ConflictingCharacterProfileError("A different canonical profile already uses this character ID.")
        destination.parent.mkdir(parents=True,exist_ok=True); temporary=destination.with_suffix(".json.part")
        try:
            with temporary.open("x",encoding="utf-8",newline="") as stream:
                stream.write(profile.model_dump_json(indent=2)); stream.flush(); os.fsync(stream.fileno())
            if destination.exists(): raise ConflictingCharacterProfileError("Character registration conflicted with another writer.")
            os.replace(temporary,destination); return destination
        except CharacterRegistryError: raise
        except OSError as error: raise CharacterRegistryError("Canonical character could not be registered atomically.") from error
        finally: temporary.unlink(missing_ok=True)
    def get(self, character_id: str) -> CanonicalCharacterProfile:
        destination=self.path_for(character_id)
        if not destination.is_file(): raise CharacterNotFoundError("Required canonical character is not registered.")
        try: return CanonicalCharacterProfile.model_validate_json(destination.read_text(encoding="utf-8"))
        except (OSError,ValidationError) as error: raise CorruptedCharacterRecordError("Canonical character record is corrupted.") from error
    def require_many(self, character_ids) -> tuple[CanonicalCharacterProfile,...]:
        ids=tuple(character_ids)
        if len(ids)!=len(set(ids)): raise CharacterRegistryError("Required character IDs must be unique.")
        return tuple(self.get(value) for value in ids)
    def list_profiles(self) -> tuple[CanonicalCharacterProfile,...]:
        """Return registered profiles without creating or changing registry data."""
        if not self._root.is_dir(): return ()
        profiles=[]
        for path in sorted(self._root.glob("*.json")):
            try: profiles.append(self.get(path.stem))
            except CharacterRegistryError: continue
        return tuple(profiles)
