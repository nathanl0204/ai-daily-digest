import asyncio
import logging
import sys

from src.config import get_config, ConfigError
from src.fetcher import fetch_all
from src.summarizer import summarize, set_sources, SummarizerError
from src.notifier import send_digest, NotifierError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    try:
        config = get_config()
    except ConfigError as exc:
        logger.critical("Configuration invalide: %s", exc)
        sys.exit(1)

    sources = config["sources"]
    api_key = config["openrouter_api_key"]
    topic = config["ntfy_topic"]

    set_sources(sources)
    logger.info("Démarrage du pipeline — %d sources configurées", len(sources))

    articles = asyncio.run(fetch_all(sources))
    logger.info("Articles collectés après filtrage: %d", len(articles))

    if not articles:
        logger.warning("Aucun article récent trouvé — pipeline terminé sans envoi")
        return

    try:
        digest = summarize(articles, api_key)
    except SummarizerError as exc:
        logger.critical("Échec de la synthèse LLM: %s", exc)
        sys.exit(1)

    try:
        send_digest(digest, topic)
    except NotifierError as exc:
        logger.critical("Échec de l'envoi ntfy: %s", exc)
        sys.exit(1)

    logger.info("Pipeline terminé avec succès")


if __name__ == "__main__":
    main()
