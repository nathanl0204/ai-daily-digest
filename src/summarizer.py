import logging
import re
import time

import google.generativeai as genai

from src.fetcher import ArticleCandidate

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3.6-flash"
MAX_CANDIDATES = 60
RETRY_ATTEMPTS = 4
RETRY_BACKOFF = 2
RETRY_WAIT = 65
TRANSIENT_MARKERS = ("429", "500", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "quota")

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
Tu es un curateur expert. Tu dois sélectionner exactement 5 actualités majeures et les résumer.

FORMAT EXACT à respecter (copie ce format) :

1. **Titre court** 🚀
Une phrase de contexte. Une phrase d'impact technique.
[Lien](https://lien-vers-source.com)

2. **Titre court** 🔬
Une phrase de contexte. Une phrase d'impact technique.
[Lien](https://lien-vers-source.com)

3. **Titre court** 📰
Une phrase de contexte. Une phrase d'impact technique.
[Lien](https://lien-vers-source.com)

4. **Titre court** 💡
Une phrase de contexte. Une phrase d'impact technique.
[Lien](https://lien-vers-source.com)

5. **Titre court** 🎯
Une phrase de contexte. Une phrase d'impact technique.
[Lien](https://lien-vers-source.com)

CONTRAINTES :
- Langue : Français
- Chaque titre : max 6 mots
- Chaque résumé : exactement 2 phrases courtes (max 30 mots chacune)
- Chaque lien : URL complète et exacte fournie dans les données
- Utilise la syntaxe Markdown [Lien](URL) pour les liens (OBLIGATOIRE)
- Pas d'introduction, pas de conclusion, pas de commentaire
- Total max : 2500 caractères"""


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


def _split_articles(text: str) -> list[str]:
    markers = list(re.finditer(r"(?:^|\n)\s*\d+\.\s", text))
    if not markers:
        return [text]

    articles = []
    for i, m in enumerate(markers):
        start = m.start()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        article = text[start:end].strip()
        if article:
            articles.append(article)
    return articles[:5]


def summarize(articles: list[ArticleCandidate], api_key: str) -> list[str]:
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
            return _split_articles(text)
        except Exception as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS - 1:
                msg = str(exc)
                transient = any(marker in msg for marker in TRANSIENT_MARKERS)
                wait = RETRY_WAIT if transient else RETRY_BACKOFF ** (attempt + 1)
                logger.warning("Erreur API (tentative %d/%d): %s — retry dans %ds", attempt + 1, RETRY_ATTEMPTS, exc, wait)
                time.sleep(wait)
            else:
                logger.error("Échec après %d tentatives: %s", RETRY_ATTEMPTS, exc)
    raise SummarizerError(f"Échec du résumé après {RETRY_ATTEMPTS} tentatives: {last_error}")
