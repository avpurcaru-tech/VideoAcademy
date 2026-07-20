"""Application-boundary environment bootstrap."""
from pathlib import Path

from dotenv import load_dotenv


def application_root() -> Path:
    """Resolve the shared VideoAcademy root from this module, independent of cwd."""
    return Path(__file__).resolve().parents[3]


def load_application_environment(env_path: Path | None=None) -> bool:
    """Load project configuration without overriding the process environment."""
    path=Path(env_path) if env_path is not None else application_root()/".env"
    return bool(load_dotenv(dotenv_path=path,override=False))
