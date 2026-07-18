from pathlib import Path

from app.models.assets import Character


class CharacterRepository:
    def __init__(self, storage_path: Path = Path("storage/characters")) -> None:
        self._storage_path = storage_path
        self._cache: dict[str, Character] = {}
        self._is_loaded = False

    def get_character(self, character_id: str) -> Character | None:
        self._load_assets()
        return self._cache.get(character_id)

    def list_characters(self) -> list[Character]:
        self._load_assets()
        return list(self._cache.values())

    def _load_assets(self) -> None:
        if self._is_loaded:
            return

        for asset_file in sorted(self._storage_path.glob("*.json")):
            character = Character.model_validate_json(asset_file.read_text(encoding="utf-8"))
            if character.id in self._cache:
                raise ValueError(f"Duplicate character ID: {character.id}")
            self._cache[character.id] = character

        self._is_loaded = True
