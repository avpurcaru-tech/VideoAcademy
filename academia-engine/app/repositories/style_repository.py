from pathlib import Path

from app.models.assets import Style


class StyleRepository:
    def __init__(self, storage_path: Path = Path("storage/styles")) -> None:
        self._storage_path = storage_path
        self._cache: dict[str, Style] = {}
        self._is_loaded = False

    def get_style(self, style_id: str) -> Style | None:
        self._load_assets()
        return self._cache.get(style_id)

    def list_styles(self) -> list[Style]:
        self._load_assets()
        return list(self._cache.values())

    def _load_assets(self) -> None:
        if self._is_loaded:
            return

        for asset_file in sorted(self._storage_path.glob("*.json")):
            style = Style.model_validate_json(asset_file.read_text(encoding="utf-8"))
            if style.id in self._cache:
                raise ValueError(f"Duplicate style ID: {style.id}")
            self._cache[style.id] = style

        self._is_loaded = True
