"""Email delivery port. Implemented: Resend (https://resend.com/docs/api-reference/emails)."""

from typing import Protocol

import httpx

from app.core.config import Settings

RESEND_URL = "https://api.resend.com/emails"


class EmailError(Exception):
    pass


class EmailSender(Protocol):
    name: str

    def send(self, to: str, subject: str, text: str, html: str) -> str:
        """Send one message; returns the provider's message id or raises EmailError."""
        ...


class ResendSender:
    name = "resend"

    def __init__(self, api_key: str, sender: str, http: httpx.Client) -> None:
        self._api_key = api_key
        self._sender = sender
        self._http = http

    def send(self, to: str, subject: str, text: str, html: str) -> str:
        try:
            response = self._http.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "from": self._sender,
                    "to": [to],
                    "subject": subject,
                    "text": text,
                    "html": html,
                },
            )
        except httpx.HTTPError as exc:
            raise EmailError(f"Resend request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            try:
                detail = str(response.json().get("message") or response.text)
            except ValueError:
                detail = response.text
            raise EmailError(f"Resend returned HTTP {response.status_code}: {detail[:200]}")
        return str(response.json().get("id") or "")


def get_email_sender(settings: Settings) -> EmailSender | None:
    if settings.resend_api_key is None:
        return None
    return ResendSender(
        settings.resend_api_key.get_secret_value(),
        settings.alerts_email_from,
        httpx.Client(timeout=15.0),
    )
