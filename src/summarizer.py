import logging
import re
import time

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
Tu es un curateur expert. Tu dois sélectionner exactement 5 actualités majeures et les résumer.

FORMAT EXACT à respecter (copie ce format) :

1. **Titre court** 🚀
Une phrase de contexte. Une phrase d'impact technique.
🔗 https://lien-vers-source.com

2. **Titre court** 🔬
Une phrase de contexte. Une phrase d'impact technique.
🔗 https://lien-vers-source.com

3. **Titre court** 📰
Une phrase de contexte. Une phrase d'impact technique.
🔗 https://lien-vers-source.com

4. **Titre court** 💡
Une phrase de contexte. Une phrase d'impact technique.
🔗 https://lien-vers-source.com

5. **Titre court** 🎯
Une phrase de contexte. Une phrase d'impact technique.
🔗 https://lien-vers-source.com

CONTRAINTES :
- Langue : Français
- Chaque titre : max 6 mots
- Chaque résumé : exactement 2 phrases courtes (max 30 mots chacune)
- Chaque lien : URL complète et exacte fournie dans les données
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
    parts = re.split(r"\n(?=\d+\.\s)", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _ensure_link(article: str, fallback_link: str = "") -> str:
    if re.search(r"https?://\S+", article):
        return article
    if fallback_link:
        return article.rstrip() + "\n🔗 " + fallback_link
    return article


def _compress_article(article: str, budget: int) -> str:
    if len(article.encode("utf-8")) <= budget:
        return article

    link_match = re.search(r"🔗\s*https?://\S+", article)
    link_line = link_match.group(0) if link_match else ""
    link_len = len(link_line.encode("utf-8")) if link_line else 0

    no_link = article[:link_match.start()].rstrip() if link_match else article
    title_match = re.match(r"(\d+\.\s+\*\*[^*]+\*\*\s*\S*\s*\n?)", no_link)
    title_line = title_match.group(0) if title_match else ""
    title_len = len(title_line.encode("utf-8"))

    separator = "\n"
    summary_budget = budget - title_len - link_len - len(separator.encode("utf-8")) - 4

    if summary_budget < 30:
        return (title_line.rstrip() + "\n" + link_line).strip()

    summary_text = no_link[title_match.end():] if title_match else no_link
    summary_bytes = summary_text.encode("utf-8")
    if len(summary_bytes) <= summary_budget:
        summary_out = summary_text
    else:
        truncated = summary_bytes[:summary_budget].decode("utf-8", errors="ignore")
        last_period = truncated.rfind(".")
        last_space = truncated.rfind(" ")
        cut = max(last_period, last_space)
        if cut > 30:
            truncated = truncated[:cut + 1]
        summary_out = truncated.rstrip()

    parts = [title_line.rstrip(), summary_out.strip()]
    if link_line:
        parts.append(link_line)
    return "\n".join(parts)


def _fit_to_budget(text: str, budget: int = 4000) -> str:
    articles = _split_articles(text)

    if len(articles) >= 5:
        articles = articles[:5]
    else:
        return text[:budget]

    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return "\n\n".join(articles)

    overhead = len("\n\n".join("").encode("utf-8"))
    total_article_bytes = sum(len(a.encode("utf-8")) for a in articles)
    slack = budget - overhead

    if total_article_bytes <= slack:
        return "\n\n".join(articles)

    budgets = []
    for a in articles:
        ratio = len(a.encode("utf-8")) / total_article_bytes
        budgets.append(max(int(slack * ratio), 200))

    compressed = [_compress_article(a, b) for a, b in zip(articles, budgets)]
    result = "\n\n".join(compressed)

    if len(result.encode("utf-8")) > budget:
        result = result.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
        last_nl = result.rfind("\n\n")
        if last_nl > 0:
            result = result[:last_nl]

    return result


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
            return _fit_to_budget(text)
        except Exception as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS - 1:
                wait = RETRY_BACKOFF ** (attempt + 1)
                logger.warning("Erreur API (tentative %d/%d): %s — retry dans %ds", attempt + 1, RETRY_ATTEMPTS, exc, wait)
                time.sleep(wait)
            else:
                logger.error("Échec après %d tentatives: %s", RETRY_ATTEMPTS, exc)
    raise SummarizerError(f"Échec du résumé après {RETRY_ATTEMPTS} tentatives: {last_error}")
