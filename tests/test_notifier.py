from unittest.mock import patch, MagicMock

import pytest
import requests

from src.notifier import send_notification, NotifierError, MAX_PAYLOAD_BYTES, DEFAULT_HEADERS


class TestSendNotification:
    @patch("src.notifier.requests.post")
    def test_sends_post_with_correct_url_and_headers(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        send_notification("**1. Titre**\nRésumé.", "my-topic")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == "https://ntfy.sh/my-topic"
        assert call_kwargs[1]["headers"] == DEFAULT_HEADERS

    @patch("src.notifier.requests.post")
    def test_sends_utf8_body(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        body = "Résumé avec accents: é, è, ê, ù"
        send_notification(body, "t")

        sent_body = mock_post.call_args[1]["data"]
        assert sent_body == body.encode("utf-8")

    @patch("src.notifier.requests.post")
    def test_raises_on_http_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        mock_post.return_value = mock_resp

        with pytest.raises(NotifierError, match="Erreur HTTP"):
            send_notification("test", "topic")

    @patch("src.notifier.requests.post")
    def test_raises_on_network_error(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        with pytest.raises(NotifierError, match="Erreur réseau"):
            send_notification("test", "topic")

    @patch("src.notifier.requests.post")
    def test_truncates_payload_over_limit(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        body = "x" * (MAX_PAYLOAD_BYTES + 100)
        send_notification(body, "t")

        sent_body = mock_post.call_args[1]["data"]
        assert len(sent_body) == MAX_PAYLOAD_BYTES

    @patch("src.notifier.requests.post")
    def test_success_with_valid_payload(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        send_notification("**OK**", "topic")
        mock_post.assert_called_once()
