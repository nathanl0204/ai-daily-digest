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


class TestSummarize:
    @patch("src.summarizer.genai.configure")
    @patch("src.summarizer.genai.GenerativeModel")
    def test_returns_list_on_success(self, mock_model_cls, mock_configure):
        mock_response = MagicMock()
        mock_response.text = "**1. Titre**\nRésumé.\n🔗 http://x"
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_instance

        result = summarize(_make_articles(5), "fake-key")
        assert isinstance(result, list)
        assert len(result) == 1
        assert "Titre" in result[0]
        mock_configure.assert_called_once_with(api_key="fake-key")
        mock_instance.generate_content.assert_called_once()
        _, kwargs = mock_instance.generate_content.call_args
        assert kwargs["request_options"] == {"retry": None}

    @patch("src.summarizer.genai.configure")
    @patch("src.summarizer.genai.GenerativeModel")
    def test_returns_multiple_articles(self, mock_model_cls, mock_configure):
        mock_response = MagicMock()
        mock_response.text = (
            "1. **Titre A**\nRésumé A.\n🔗 http://a\n\n"
            "2. **Titre B**\nRésumé B.\n🔗 http://b\n\n"
            "3. **Titre C**\nRésumé C.\n🔗 http://c\n\n"
            "4. **Titre D**\nRésumé D.\n🔗 http://d\n\n"
            "5. **Titre E**\nRésumé E.\n🔗 http://e"
        )
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_instance

        result = summarize(_make_articles(10), "fake-key")
        assert isinstance(result, list)
        assert len(result) == 5

    @patch("src.summarizer.genai.configure")
    @patch("src.summarizer.genai.GenerativeModel")
    def test_raises_on_empty_response(self, mock_model_cls, mock_configure):
        mock_response = MagicMock()
        mock_response.text = ""
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_instance

        with patch("src.summarizer.time.sleep"):
            with pytest.raises(SummarizerError, match="Réponse LLM vide"):
                summarize(_make_articles(2), "fake-key")

    @patch("src.summarizer.genai.configure")
    @patch("src.summarizer.genai.GenerativeModel")
    def test_rate_limit_waits_full_minute(self, mock_model_cls, mock_configure):
        from src.summarizer import RETRY_WAIT

        mock_instance = MagicMock()
        mock_instance.generate_content.side_effect = [
            Exception("429 You exceeded your current quota, please retry in 55s"),
            MagicMock(text="1. **Titre**\nRésumé.\n[Link](http://x)"),
        ]
        mock_model_cls.return_value = mock_instance

        with patch("src.summarizer.time.sleep") as mock_sleep:
            result = summarize(_make_articles(2), "fake-key")
        mock_sleep.assert_called_once_with(RETRY_WAIT)
        assert isinstance(result, list)

    @patch("src.summarizer.genai.configure")
    @patch("src.summarizer.genai.GenerativeModel")
    def test_daily_quota_fails_fast_without_retry(self, mock_model_cls, mock_configure):
        daily_error = Exception(
            "429 Quota exceeded ... quota_id: \"GenerateRequestsPerDayPerProjectPerModel-FreeTier\""
        )
        mock_instance = MagicMock()
        mock_instance.generate_content.side_effect = daily_error
        mock_model_cls.return_value = mock_instance

        with patch("src.summarizer.time.sleep") as mock_sleep:
            with pytest.raises(SummarizerError, match="tentatives"):
                summarize(_make_articles(2), "fake-key")
        mock_sleep.assert_not_called()
        assert mock_instance.generate_content.call_count == 1

    @patch("src.summarizer.genai.configure")
    @patch("src.summarizer.genai.GenerativeModel")
    def test_retries_on_exception(self, mock_model_cls, mock_configure):
        mock_instance = MagicMock()
        mock_instance.generate_content.side_effect = [Exception("API error"), MagicMock(text="OK")]
        mock_model_cls.return_value = mock_instance

        with patch("src.summarizer.time.sleep"):
            result = summarize(_make_articles(2), "fake-key")
        assert isinstance(result, list)
        assert len(result) == 1

    @patch("src.summarizer.genai.configure")
    @patch("src.summarizer.genai.GenerativeModel")
    def test_raises_after_all_retries(self, mock_model_cls, mock_configure):
        mock_instance = MagicMock()
        mock_instance.generate_content.side_effect = Exception("Persistent error")
        mock_model_cls.return_value = mock_instance

        with patch("src.summarizer.time.sleep"):
            with pytest.raises(SummarizerError, match="Échec du résumé"):
                summarize(_make_articles(2), "fake-key")
