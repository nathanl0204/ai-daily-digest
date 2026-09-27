from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.fetcher import ArticleCandidate
from src.summarizer import (
    _prefilter,
    _build_article_context,
    summarize,
    repair_digest_links,
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

    @patch("src.summarizer.requests.post")
    def test_summarize_repairs_hallucinated_link(self, mock_post):
        mock_post.return_value = _mock_response(
            200,
            "1. **Titre**\nRésumé.\n[Link](https://example.com/0-wrong-slug)",
        )

        result = summarize(_make_articles(5), "fake-key")
        assert "https://example.com/0-wrong-slug" not in result[0]
        assert "[Link](https://example.com/0)" in result[0]


def _article(title: str, link: str) -> ArticleCandidate:
    return ArticleCandidate(
        source_name="Src",
        title=title,
        link=link,
        summary="s",
        published_at=datetime.now(timezone.utc),
    )


class TestRepairDigestLinks:
    def test_keeps_valid_known_link(self):
        arts = [_article("Un titre", "https://example.com/post")]
        out = repair_digest_links(
            ["1. **Un titre** 🚀\nRésumé.\n[Lien](https://example.com/post)"], arts
        )
        assert "[Lien](https://example.com/post)" in out[0]

    def test_encodes_space_in_url(self):
        arts = [_article("Titre", "https://example.com/mon%20article")]
        out = repair_digest_links(
            ["1. **Titre**\nRésumé.\n[Lien](https://example.com/mon article)"], arts
        )
        assert "[Lien](https://example.com/mon%20article)" in out[0]

    def test_encodes_parens_to_markdown_safe_url(self):
        arts = [_article("Titre", "https://en.wikipedia.org/wiki/Foo_(bar)")]
        out = repair_digest_links(
            ["1. **Titre**\nRésumé.\n[Lien](https://en.wikipedia.org/wiki/Foo_(bar))"], arts
        )
        assert "[Lien](https://en.wikipedia.org/wiki/Foo_%28bar%29)" in out[0]

    def test_fixes_space_between_bracket_and_paren(self):
        arts = [_article("Titre", "https://example.com/post")]
        out = repair_digest_links(
            ["1. **Titre**\nRésumé.\n[Lien] (https://example.com/post)"], arts
        )
        assert "[Lien](https://example.com/post)" in out[0]

    def test_replaces_hallucinated_url_by_similarity(self):
        arts = [_article("Titre", "https://openai.com/index/gpt-5")]
        out = repair_digest_links(
            ["1. **Titre**\nRésumé.\n[Lien](https://openai.com/index/gpt-5-release-2025)"],
            arts,
        )
        assert "[Lien](https://openai.com/index/gpt-5)" in out[0]

    def test_title_fallback_when_url_is_garbage(self):
        arts = [_article("OpenAI releases GPT-5 model", "https://openai.com/index/gpt-5")]
        out = repair_digest_links(
            ["1. **OpenAI GPT-5 release** 🚀\nRésumé.\n[Lien](https://made-up.example/foo)"],
            arts,
        )
        assert "[Lien](https://openai.com/index/gpt-5)" in out[0]

    def test_strips_unrecoverable_link(self):
        arts = [_article("Autre sujet totally different", "https://example.com/x")]
        out = repair_digest_links(
            ["1. **Zorglub quantum** 🚀\nRésumé.\n[Lien](https://made-up.example/foo)"],
            arts,
        )
        assert "[Lien](" not in out[0]
        assert "https://made-up.example" not in out[0]
        assert "Lien" in out[0]
