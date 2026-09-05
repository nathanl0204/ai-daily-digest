import logging
import time

import requests

logger = logging.getLogger(__name__)

NTFY_BASE_URL = "https://ntfy.sh"
MAX_PAYLOAD_BYTES = 4096

DEFAULT_HEADERS = {
    "Title": "AI Daily Digest",
    "Priority": "default",
    "Tags": "robot,newspaper",
    "Markdown": "yes",
}


class NotifierError(Exception):
    pass


def send_notification(markdown_body: str, topic: str, title: str | None = None) -> None:
    url = f"{NTFY_BASE_URL}/{topic}"
    body = markdown_body.encode("utf-8")
    if len(body) > MAX_PAYLOAD_BYTES:
        logger.warning("Payload %d octets > limite %d — troncature intelligente", len(body), MAX_PAYLOAD_BYTES)
        body = body[:MAX_PAYLOAD_BYTES]
        last_newline = body.rfind(b"\n")
        if last_newline > 0:
            body = body[:last_newline]

    headers = {**DEFAULT_HEADERS}
    if title:
        headers["Title"] = title

    try:
        resp = requests.post(url, data=body, headers=headers, timeout=10)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise NotifierError(f"Erreur HTTP ntfy: {exc}") from exc
    except requests.RequestException as exc:
        raise NotifierError(f"Erreur réseau ntfy: {exc}") from exc

    logger.info("Notification envoyée sur ntfy/%s (%d octets)", topic, len(body))


def send_digest(articles: list[str], topic: str) -> None:
    total = len(articles)
    for i, article in enumerate(articles, 1):
        title = f"AI Digest [{i}/{total}]"
        send_notification(article, topic, title=title)
        if i < total:
            time.sleep(1)
