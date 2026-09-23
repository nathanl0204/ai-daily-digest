import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_BASE_DIR = Path(__file__).resolve().parent.parent
_SOURCES_PATH = _BASE_DIR / "sources.json"

REQUIRED_ENV_VARS = ("OPENROUTER_API_KEY", "NTFY_TOPIC")


class ConfigError(Exception):
    pass


def _validate_env() -> None:
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        raise ConfigError(
            f"Variables d'environnement manquantes : {', '.join(missing)}. "
            "Consulte .env.example pour la liste requise."
        )


def load_sources() -> list[dict]:
    with open(_SOURCES_PATH, encoding="utf-8") as f:
        sources = json.load(f)
    if not isinstance(sources, list) or len(sources) == 0:
        raise ConfigError("sources.json doit être un tableau non vide.")
    return sources


def get_config() -> dict:
    _validate_env()
    return {
        "sources": load_sources(),
        "openrouter_api_key": os.environ["OPENROUTER_API_KEY"],
        "ntfy_topic": os.environ["NTFY_TOPIC"],
    }
