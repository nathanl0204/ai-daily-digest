import logging
import time
from dataclasses import asdict

import google.generativeai as genai

from src.fetcher import ArticleCandidate

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3.6-flash"
MAX_CANDIDATES = 60
RETRY_ATTEMPTS = 2
RETRY_BACKOFF = 2

CATEGORY_PRIORITY = {
    "lab": 0,
    "research": 0,
    "cloud": 1,
    "newsletter": 2,
    "blog": 2,
    "press": 3,
    "fr_press": 3,
    "fr_community": 4,
}

SYSTEM_PROMPT = """\
Tu es un curateur expert et ingénieur IA senior.
Ta mission : analyser les articles des dernières 24h, fusionner les doublons, sélectionner STRICTEMENT les 5 actualités les plus majeures/structurantes, et les résumer pour un pair ingénieur.

Règles strictes :
1. Langue de réponse : Français.
2. Exactement 5 items (numérotés de 1 à 5).
3. Structure par item :
   - Titre concis avec émoji évocateur
   - Synthèse de 2 phrases (contexte + impact technique concret)
   - Lien exact vers la source (priorise la source primaire / officielle)
4. Pas de bavardage d'introduction ni de conclusion.
5. Utilise du Markdown propre compatible ntfy.sh (pas de HTML)."""


def _prefilter(articles: list[ArticleCandidate]) -> list[ArticleCandidate]:
    if len(articles) <= MAX_CANDIDATES:
        return articles
    sorted_articles = sorted(
        articles,
        key=lambda a: CATEGORY_PRIORITY.get(
            next(
                (s.get("category", "press") for s in _sources_ref if s["name"] == a.source_name),
                "press",
            ),
            5,
        ),
    )
    return sorted_articles[:MAX_CANDIDATES]


_sources_ref: list[dict] = []


def set_sources(sources: list[dict]) -> None:
    global _sources_ref
    _sources_ref = sources


def _build_user_prompt(articles: list[ArticleCandidate]) -> str:
    lines: list[str] = []
    for i, art in enumerate(articles, 1):
        lines.append(f"{i}. [{art.source_name}] {art.title}")
        lines.append(f"   Résumé : {art.summary}")
        lines.append(f"   Lien : {art.link}")
        lines.append("")
    return "\n".join(lines)


def _build_article_context(articles: list[ArticleCandidate]) -> str:
    filtered = _prefilter(articles)
    return _build_user_prompt(filtered)


class SummarizerError(Exception):
    pass


def summarize(articles: list[ArticleCandidate], api_key: str) -> str:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SYSTEM_PROMPT,
    )
    user_prompt = _build_article_context(articles)

    last_error: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = model.generate_content(user_prompt)
            text = response.text.strip()
            if not text:
                raise SummarizerError("Réponse LLM vide")
            return text
        except Exception as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS - 1:
                wait = RETRY_BACKOFF ** (attempt + 1)
                logger.warning("Erreur API (tentative %d/%d): %s — retry dans %ds", attempt + 1, RETRY_ATTEMPTS, exc, wait)
                time.sleep(wait)
            else:
                logger.error("Échec après %d tentatives: %s", RETRY_ATTEMPTS, exc)
    raise SummarizerError(f"Échec du résumé après {RETRY_ATTEMPTS} tentatives: {last_error}")
