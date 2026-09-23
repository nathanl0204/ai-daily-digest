import pytest
from src.config import load_sources, get_config, ConfigError


def test_load_sources_count():
    sources = load_sources()
    assert len(sources) == 21


def test_load_sources_structure():
    sources = load_sources()
    for src in sources:
        assert "name" in src, f"Clé 'name' manquante dans {src}"
        assert "url" in src, f"Clé 'url' manquante dans {src}"
        assert "category" in src, f"Clé 'category' manquante dans {src}"


def test_load_sources_urls_are_strings():
    sources = load_sources()
    for src in sources:
        assert isinstance(src["url"], str) and src["url"].startswith("http"), (
            f"URL invalide pour {src['name']}: {src['url']}"
        )


def test_get_config_missing_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    with pytest.raises(ConfigError, match="Variables d'environnement manquantes"):
        get_config()


def test_get_config_success(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    cfg = get_config()
    assert cfg["openrouter_api_key"] == "test-key"
    assert cfg["ntfy_topic"] == "test-topic"
    assert len(cfg["sources"]) == 21
