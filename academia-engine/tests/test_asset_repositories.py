import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from app.repositories import CharacterRepository, StyleRepository


class CharacterRepositoryTests(unittest.TestCase):
    def test_loads_gets_and_caches_characters(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            storage_path = Path(temporary_directory)
            asset_path = storage_path / "mia.json"
            self._write_json(asset_path, self._character_data("Mia"))
            repository = CharacterRepository(storage_path)

            self.assertEqual(repository.get_character("mia").display_name, "Mia")
            self._write_json(asset_path, self._character_data("Ana"))

            self.assertEqual(repository.list_characters()[0].display_name, "Mia")
            self.assertIsNone(repository.get_character("unknown"))

    def test_rejects_invalid_character_json(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            storage_path = Path(temporary_directory)
            self._write_json(storage_path / "invalid.json", {"id": "mia"})

            with self.assertRaises(ValidationError):
                CharacterRepository(storage_path).list_characters()

    @staticmethod
    def _character_data(display_name: str) -> dict[str, object]:
        return {
            "id": "mia",
            "display_name": display_name,
            "age": 7,
            "appearance": "Păr brunet și ochi căprui.",
            "clothing": "Costum spațial mov.",
            "personality": "Curioasă și prietenoasă.",
            "voice": "Calmă și veselă.",
            "default_emotions": ["curious", "happy"],
        }

    @staticmethod
    def _write_json(path: Path, data: dict[str, object]) -> None:
        path.write_text(json.dumps(data), encoding="utf-8")


class StyleRepositoryTests(unittest.TestCase):
    def test_loads_and_gets_styles(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            storage_path = Path(temporary_directory)
            (storage_path / "soft-3d.json").write_text(
                json.dumps(
                    {
                        "id": "soft-3d",
                        "name": "Soft 3D",
                        "visual_style": "Animație 3D blândă.",
                        "palette": ["#AABBCC", "#DDEEFF"],
                        "lighting": "Lumină caldă.",
                        "rendering": "Randare 3D netedă.",
                        "environment_defaults": {"weather": "clear"},
                    }
                ),
                encoding="utf-8",
            )
            repository = StyleRepository(storage_path)

            self.assertEqual(repository.get_style("soft-3d").name, "Soft 3D")
            self.assertEqual(len(repository.list_styles()), 1)
            self.assertIsNone(repository.get_style("unknown"))
