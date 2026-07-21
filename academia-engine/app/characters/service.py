from .registry import CharacterRegistry


class CharacterService:
    def __init__(self, registry=None): self._registry=registry or CharacterRegistry()
    def require_many(self, character_ids): return self._registry.require_many(character_ids)
