from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.fetcher import ArticleCandidate
from src.summarizer import (
    _prefilter,
    _build_article_context,
    summarize,
    SummarizerError,
    MAX_CANDIDATES,
    CATEGORY_PRIORITY,
)


def _make_articles(n: int, source_name: str = "TestSource") -> list[ArticleCandidate]:
    now = datetime.now(timezone.utc)
    return [
        ArticleCandidate(
            source_name=source_name,
            title=f"Article {i}",
            link=f"https://example.com/{i}",
            summary=f"Résumé de l'article {i}",
            published_at=now,
        )
        for i in range(n)
    ]


def _make_sources(names: list[str]) -> list[dict]:
    return [{"name": n, "url": "http://x", "category": "lab"} for n in names]


class TestPrefilter:
    def test_below_threshold_unchanged(self):
        articles = _make_articles(10)
        assert len(_prefilter(articles)) == 10

    def test_above_threshold_truncated(self):
        articles = _make_articles(MAX_CANDIDATES + 20)
        result = _prefilter(articles)
        assert len(result) == MAX_CANDIDATES

    def test_priority_sorts_lab_first(self):
        from src import summarizer

        summarizer.set_sources([
            {"name": "PressSource", "url": "http://x", "category": "press"},
            {"name": "LabSource", "url": "http://x", "category": "lab"},
        ])
        press_art = _make_articles(30, "PressSource")
        lab_art = _make_articles(40, "LabSource")
        all_art = press_art + lab_art
        result = _prefilter(all_art)
        lab_count = sum(1 for a in result if a.source_name == "LabSource")
        assert lab_count == 40


class TestBuildArticleContext:
    def test_includes_source_and_link(self):
        articles = _make_articles(2)
        ctx = _build_article_context(articles)
        assert "TestSource" in ctx
        assert "https://example.com/0" in ctx
        assert "Article 0" in ctx


def _mock_response(status_code: int = 200, content: str = "1. **Titre**\nRésumé.\n[Link](http://x)"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.text = content if status_code >= 400 else ""
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


class TestSummarize:
    @patch("src.summarizer.requests.post")
    def test_returns_list_on_success(self, mock_post):
        mock_post.return_value = _mock_response(
            200, "**1. Titre**\nRésumé.\n🔗 http://x"
        )

        result = summarize(_make_articles(5), "fake-key")
        assert isinstance(result, list)
        assert len(result) == 1
        assert "Titre" in result[0]
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["model"] == "xiaomi/mimo-v2.6-flash"
        assert kwargs["headers"]["Authorization"] == "Bearer fake-key"
        roles = [m["role"] for m in kwargs["json"]["messages"]]
        assert roles == ["system", "user"]

    @patch("src.summarizer.requests.post")
    def test_returns_multiple_articles(self, mock_post):
        mock_post.return_value = _mock_response(
            200,
            "1. **Titre A**\nRésumé A.\n🔗 http://a\n\n"
            "2. **Titre B**\nRésumé B.\n🔗 http://b\n\n"
            "3. **Titre C**\nRésumé C.\n🔗 http://c\n\n"
            "4. **Titre D**\nRésumé D.\n🔗 http://d\n\n"
            "5. **Titre E**\nRésumé E.\n🔗 http://e",
        )

        result = summarize(_make_articles(10), "fake-key")
        assert isinstance(result, list)
        assert len(result) == 5

    @patch("src.summarizer.requests.post")
    def test_raises_on_empty_response(self, mock_post):
        mock_post.return_value = _mock_response(200, "")

        with patch("src.summarizer.time.sleep"):
            with pytest.raises(SummarizerError, match="Réponse LLM vide"):
                summarize(_make_articles(2), "fake-key")
        from src.summarizer import RETRY_ATTEMPTS

        assert mock_post.call_count == RETRY_ATTEMPTS

    @patch("src.summarizer.requests.post")
    def test_rate_limit_retries_with_short_wait(self, mock_post):
        from src.summarizer import RETRY_WAITS

        mock_post.side_effect = [
            _mock_response(429, "rate limited"),
            _mock_response(200, "1. **Titre**\nRésumé.\n[Link](http://x)"),
        ]

        with patch("src.summarizer.time.sleep") as mock_sleep:
            result = summarize(_make_articles(2), "fake-key")
        mock_sleep.assert_called_once_with(RETRY_WAITS[0])
        assert RETRY_WAITS[0] <= 5
        assert isinstance(result, list)

    @patch("src.summarizer.requests.post")
    def test_fatal_401_fails_fast_without_retry(self, mock_post):
        mock_post.return_value = _mock_response(401, "invalid api key")

        with patch("src.summarizer.time.sleep") as mock_sleep:
            with pytest.raises(SummarizerError, match="401"):
                summarize(_make_articles(2), "fake-key")
        mock_sleep.assert_not_called()
        assert mock_post.call_count == 1

    @patch("src.summarizer.requests.post")
    def test_retries_on_transient_503(self, mock_post):
        mock_post.side_effect = [
            _mock_response(503, "service unavailable"),
            _mock_response(200, "1. **Titre**\nRésumé.\n[Link](http://x)"),
        ]

        with patch("src.summarizer.time.sleep"):
            result = summarize(_make_articles(2), "fake-key")
        assert isinstance(result, list)
        assert len(result) == 1

    @patch("src.summarizer.requests.post")
    def test_raises_after_all_retries(self, mock_post):
        from src.summarizer import RETRY_ATTEMPTS

        mock_post.return_value = _mock_response(503, "Persistent error")

        with patch("src.summarizer.time.sleep"):
            with pytest.raises(SummarizerError, match="Échec du résumé"):
                summarize(_make_articles(2), "fake-key")
        assert mock_post.call_count == RETRY_ATTEMPTS
