import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote

import requests

from src.fetcher import ArticleCandidate

logger = logging.getLogger(__name__)

MODEL_NAME = "xiaomi/mimo-v2.6-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_CANDIDATES = 60
RETRY_ATTEMPTS = 3
RETRY_WAITS = (5, 15)
REQUEST_TIMEOUT = 120

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
- N'invente jamais d'URL : recopie strictement celles fournies, sans espace ni retour à la ligne
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


LINK_RE = re.compile(r"\[([^\]]*)\]\s*\(((?:[^()]|\([^()]*\))*)\)")
_URL_SAFE = ":/?#[]@!$&'+,;=%~"
_URL_SIMILARITY_THRESHOLD = 0.7
_TITLE_COVERAGE_THRESHOLD = 0.6


class SummarizerError(Exception):
    pass


def _normalize_url(url: str) -> str:
    return quote(url.strip(), safe=_URL_SAFE)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", " ", stripped.casefold()).strip()


def _match_known_url(url: str, known: set[str]) -> str | None:
    candidate = _normalize_url(url)
    if candidate in known:
        return candidate
    if not url.rstrip().endswith(")"):
        restored = _normalize_url(url + ")")
        if restored in known:
            return restored
    best, best_ratio = None, 0.0
    for option in known:
        ratio = SequenceMatcher(None, candidate, option).ratio()
        if ratio > best_ratio:
            best, best_ratio = option, ratio
    if best is not None and best_ratio >= _URL_SIMILARITY_THRESHOLD:
        return best
    return None


def _match_title_link(title: str, articles: list[ArticleCandidate]) -> str | None:
    title_tokens = set(_fold(title).split())
    if not title_tokens:
        return None
    best_link, best_coverage = None, 0.0
    for art in articles:
        article_tokens = set(_fold(art.title).split())
        if not article_tokens or not art.link:
            continue
        coverage = len(title_tokens & article_tokens) / len(title_tokens)
        if coverage > best_coverage:
            best_link, best_coverage = art.link, coverage
    if best_link and best_coverage >= _TITLE_COVERAGE_THRESHOLD:
        return _normalize_url(best_link)
    return None


def repair_digest_links(digest: list[str], articles: list[ArticleCandidate]) -> list[str]:
    known = {_normalize_url(a.link) for a in articles if a.link}
    repaired: list[str] = []
    for item in digest:
        title_match = re.search(r"\*\*(.+?)\*\*", item)

        def _fix(match: re.Match) -> str:
            text, url = match.group(1), match.group(2)
            fixed = _match_known_url(url, known)
            if fixed is None and title_match:
                fixed = _match_title_link(title_match.group(1), articles)
            if fixed is None:
                logger.warning("Lien LLM non vérifiable supprimé: %s", url)
                return text
            return f"[{text}]({fixed})"

        repaired.append(LINK_RE.sub(_fix, item))
    return repaired


class _RetryableError(Exception):
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
    user_prompt = _build_article_context(articles)
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.post(
                OPENROUTER_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise _RetryableError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            if not resp.ok:
                raise SummarizerError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            text = (data["choices"][0]["message"]["content"] or "").strip()
            if not text:
                raise _RetryableError("Réponse LLM vide")
            return repair_digest_links(_split_articles(text), _prefilter(articles))
        except (requests.RequestException, ValueError, KeyError, IndexError, _RetryableError) as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS - 1:
                wait = RETRY_WAITS[attempt]
                logger.warning("Erreur API (tentative %d/%d): %s — retry dans %ds", attempt + 1, RETRY_ATTEMPTS, exc, wait)
                time.sleep(wait)
            else:
                logger.error("Échec après %d tentatives: %s", RETRY_ATTEMPTS, exc)
    raise SummarizerError(f"Échec du résumé après {RETRY_ATTEMPTS} tentatives: {last_error}")
