import base64
import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Literal

import httpx

from app.texts import TEMP_OVERLOADED_TEXT, TEMP_UNAVAILABLE_TEXT, UNREADABLE_TEXT

logger = logging.getLogger(__name__)

WHATFONTIS_ENDPOINT = "https://www.whatfontis.com/api2/"

RequestStatus = Literal["success", "no_result", "service_error"]
ResultType = Literal[
    "font_found",
    "no_font_match",
    "unreadable_text",
    "no_text_detected",
    "invalid_image",
    "timeout",
    "rate_limited",
    "provider_error",
    "internal_api_error",
    "invalid_response",
]


@dataclass(frozen=True)
class WhatFontIsResult:
    title: str | None
    result_json: Any
    status: RequestStatus
    success: bool
    counted_as_usage: bool
    result_type: ResultType
    key_index: int
    api_request_made: bool
    http_status: int | None = None
    rate_limited: bool = False
    user_message: str | None = None


class WhatFontIsClient:
    def __init__(self, api_keys: str | list[str], key_index: int = 1) -> None:
        if isinstance(api_keys, str):
            keys = [api_keys]
        else:
            keys = api_keys
        self.api_keys = [key.strip() for key in keys if key.strip()]
        if not self.api_keys:
            raise ValueError("At least one WhatFontIs API key is required")
        self.key_index = key_index
        self.api_key = self.api_keys[key_index - 1]

    async def recognize(self, image_bytes: bytes) -> WhatFontIsResult:
        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "API_KEY": self.api_key,
            "IMAGEBASE64": "1",
            "urlimagebase64": image_base64,
            "NOTTEXTBOXSDETECTION": "0",
            "FREEFONTS": "0",
            "limit": "1",
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    WHATFONTIS_ENDPOINT,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.TimeoutException:
            logger.warning("WhatFontIs API timeout")
            return WhatFontIsResult(
                title=None,
                result_json={"error": "timeout"},
                status="service_error",
                success=False,
                counted_as_usage=False,
                result_type="timeout",
                key_index=self.key_index,
                api_request_made=True,
                user_message=TEMP_UNAVAILABLE_TEXT,
            )
        except httpx.HTTPError as exc:
            logger.exception("WhatFontIs API request failed: %s", exc.__class__.__name__)
            return WhatFontIsResult(
                title=None,
                result_json={"error": exc.__class__.__name__},
                status="service_error",
                success=False,
                counted_as_usage=False,
                result_type="provider_error",
                key_index=self.key_index,
                api_request_made=True,
                user_message=TEMP_UNAVAILABLE_TEXT,
            )

        text = response.text
        lower_text = text.lower()

        if response.status_code == 429:
            logger.warning("WhatFontIs API returned 429")
            return WhatFontIsResult(
                title=None,
                result_json={"status_code": 429, "error": "rate_limited"},
                status="service_error",
                success=False,
                counted_as_usage=False,
                result_type="rate_limited",
                key_index=self.key_index,
                api_request_made=True,
                http_status=response.status_code,
                rate_limited=True,
                user_message=TEMP_OVERLOADED_TEXT,
            )

        if response.status_code == 409 or "no api key" in lower_text:
            logger.error("WhatFontIs API key problem, status=%s", response.status_code)
            return WhatFontIsResult(
                title=None,
                result_json={"status_code": response.status_code, "error": "api_key"},
                status="service_error",
                success=False,
                counted_as_usage=False,
                result_type="provider_error",
                key_index=self.key_index,
                api_request_made=True,
                http_status=response.status_code,
                user_message=TEMP_UNAVAILABLE_TEXT,
            )

        if response.status_code >= 500:
            logger.error("WhatFontIs API service error, status=%s", response.status_code)
            return WhatFontIsResult(
                title=None,
                result_json={"status_code": response.status_code, "error": "server_error"},
                status="service_error",
                success=False,
                counted_as_usage=False,
                result_type="internal_api_error",
                key_index=self.key_index,
                api_request_made=True,
                http_status=response.status_code,
                user_message=TEMP_UNAVAILABLE_TEXT,
            )

        if response.status_code == HTTPStatus.OK:
            return self._parse_success_response(response)

        if (
            response.status_code == 422
            or self._is_no_characters_response(lower_text)
            or self._is_invalid_image_response(lower_text)
        ):
            result_json = self._safe_json(response)
            if self._is_invalid_image_response(lower_text):
                result_type: ResultType = "invalid_image"
            elif self._is_no_characters_response(lower_text):
                result_type = "no_text_detected"
            else:
                result_type = "unreadable_text"
            return WhatFontIsResult(
                title=None,
                result_json=result_json,
                status="no_result",
                success=False,
                counted_as_usage=False,
                result_type=result_type,
                key_index=self.key_index,
                api_request_made=True,
                http_status=response.status_code,
                user_message=UNREADABLE_TEXT,
            )

        logger.error(
            "WhatFontIs API unexpected response, status=%s, body=%s",
            response.status_code,
            text[:512],
        )
        return WhatFontIsResult(
            title=None,
            result_json={
                "status_code": response.status_code,
                "error": "unexpected_response",
            },
            status="service_error",
            success=False,
            counted_as_usage=False,
            result_type="invalid_response",
            key_index=self.key_index,
            api_request_made=True,
            http_status=response.status_code,
            user_message=TEMP_UNAVAILABLE_TEXT,
        )

    def _parse_success_response(self, response: httpx.Response) -> WhatFontIsResult:
        try:
            result_json = response.json()
        except ValueError:
            logger.error("WhatFontIs API returned non-JSON OK response")
            return WhatFontIsResult(
                title=None,
                result_json={"status_code": int(HTTPStatus.OK), "error": "invalid_json"},
                status="service_error",
                success=False,
                counted_as_usage=False,
                result_type="invalid_response",
                key_index=self.key_index,
                api_request_made=True,
                http_status=int(HTTPStatus.OK),
                user_message=TEMP_UNAVAILABLE_TEXT,
            )

        title = None
        if isinstance(result_json, list) and result_json:
            first = result_json[0]
            if isinstance(first, dict):
                raw_title = first.get("title")
                if isinstance(raw_title, str) and raw_title.strip():
                    title = raw_title.strip()

        if title is None and self._json_has_no_text_marker(result_json):
            return WhatFontIsResult(
                title=None,
                result_json=result_json,
                status="no_result",
                success=False,
                counted_as_usage=False,
                result_type="no_text_detected",
                key_index=self.key_index,
                api_request_made=True,
                http_status=int(HTTPStatus.OK),
                user_message=UNREADABLE_TEXT,
            )

        if title is None and self._json_has_invalid_image_marker(result_json):
            return WhatFontIsResult(
                title=None,
                result_json=result_json,
                status="no_result",
                success=False,
                counted_as_usage=False,
                result_type="invalid_image",
                key_index=self.key_index,
                api_request_made=True,
                http_status=int(HTTPStatus.OK),
                user_message=UNREADABLE_TEXT,
            )

        normalized_title = (title or "").strip().lower()
        useful_title = bool(normalized_title and normalized_title != "не определён")

        return WhatFontIsResult(
            title=title if useful_title else None,
            result_json=result_json,
            status="success" if useful_title else "no_result",
            success=useful_title,
            counted_as_usage=useful_title,
            result_type="font_found" if useful_title else "no_font_match",
            key_index=self.key_index,
            api_request_made=True,
            http_status=int(HTTPStatus.OK),
        )

    def _safe_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {
                "status_code": response.status_code,
                "error": response.text[:500],
            }

    def _is_no_characters_response(self, lower_text: str) -> bool:
        markers = (
            "no characters",
            "no character",
            "no text",
            "no textbox",
            "no text box",
            "text box",
            "textbox",
        )
        return any(marker in lower_text for marker in markers)

    def _json_has_no_text_marker(self, value: Any) -> bool:
        return self._is_no_characters_response(str(value).lower())

    def _is_invalid_image_response(self, lower_text: str) -> bool:
        markers = (
            "invalid image",
            "image invalid",
            "bad image",
            "not an image",
            "unsupported image",
        )
        return any(marker in lower_text for marker in markers)

    def _json_has_invalid_image_marker(self, value: Any) -> bool:
        return self._is_invalid_image_response(str(value).lower())
