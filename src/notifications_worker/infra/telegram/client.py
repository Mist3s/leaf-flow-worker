from typing import Any

import httpx

from notifications_worker.infra.settings import settings
from notifications_worker.infra.telegram.errors import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbidden,
    TelegramNotFound,
    TelegramRateLimited,
    TelegramServerError,
    TelegramTransportError,
)


class TelegramClient:
    def __init__(self) -> None:
        self._base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
        self._client = httpx.Client(
            proxy=settings.proxy_url,
            timeout=httpx.Timeout(
                timeout=settings.telegram_http_timeout_seconds,
                connect=settings.telegram_http_connect_timeout_seconds,
            ),
        )

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
        disable_web_page_preview: bool = True,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if thread_id and thread_id > 0:
            payload["message_thread_id"] = thread_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        self._post("sendMessage", payload)

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}/{method}"
        try:
            resp = self._client.post(url, json=payload)
        except httpx.RequestError as exc:
            raise TelegramTransportError(str(exc)) from exc

        data = self._try_json(resp)

        # Telegram almost always returns JSON, even on errors.
        # If not, keep fallback to text.
        if resp.status_code >= 400:
            self._raise_http_error(resp, data)

        # API-level error (HTTP 200 but ok=false)
        if isinstance(data, dict) and data.get("ok") is False:
            raise TelegramAPIError(
                description=str(data.get("description") or "Unknown Telegram error"),
                error_code=data.get("error_code"),
                parameters=data.get("parameters"),
            )

        if not isinstance(data, dict):
            raise TelegramServerError(status_code=resp.status_code, body=resp.text)

        return data

    @staticmethod
    def _try_json(resp: httpx.Response) -> dict[str, Any] | None:
        try:
            obj = resp.json()
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    @staticmethod
    def _raise_http_error(resp: httpx.Response, data: dict[str, Any] | None) -> None:
        desc = None
        params: dict[str, Any] | None = None

        if data:
            desc = data.get("description")
            params = data.get("parameters")

        desc = desc or resp.text or f"HTTP {resp.status_code}"

        if resp.status_code == 429:
            retry_after = None
            if params and isinstance(params, dict):
                retry_after = params.get("retry_after")
            raise TelegramRateLimited(retry_after=retry_after)

        if 500 <= resp.status_code <= 599:
            raise TelegramServerError(status_code=resp.status_code, body=resp.text)

        if resp.status_code == 400:
            raise TelegramBadRequest(description=desc)
        if resp.status_code == 403:
            raise TelegramForbidden(description=desc)
        if resp.status_code == 404:
            raise TelegramNotFound(description=desc)

        # Any other 4xx
        raise TelegramBadRequest(description=desc)


tg = TelegramClient()