"""HTTP transport for NGIOT endpoint-control requests."""

from __future__ import annotations

from http import HTTPStatus
import secrets
import string
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from aiohttp import ClientError, ClientResponseError, ClientTimeout, hdrs
import orjson

from .exceptions import ApiError, ApiTimeoutError, AuthenticationError

if TYPE_CHECKING:
    from aiohttp import ClientSession

    from .models import ApiDeviceInfo
    from .sst_authentication import SstAuthenticator

_TIMEOUT = ClientTimeout(60)
_PATH = "/api/iot/endpoint/control"


class NgiotClient:
    """Send JSON requests using device-scoped SST credentials."""

    def __init__(
        self, session: ClientSession, authenticator: SstAuthenticator, *, base_url: str
    ) -> None:
        self._session = session
        self._authenticator = authenticator
        self._url = urljoin(base_url, _PATH)

    async def request(
        self,
        device: ApiDeviceInfo,
        *,
        apn: str | int,
        body_data: dict[str, Any] | list[Any],
    ) -> dict[str, Any]:
        """Return the response envelope, retrying one HTTP authorization failure."""
        for _ in range(2):
            token = await self._authenticator.get_token(device)
            try:
                return await self._request(device, apn, body_data, token)
            except AuthenticationError:
                await self._authenticator.invalidate(device)
        raise AuthenticationError("NGIOT endpoint-control request was not authorized")

    async def _request(
        self,
        device: ApiDeviceInfo,
        apn: str | int,
        body_data: dict[str, Any] | list[Any],
        token: str,
    ) -> dict[str, Any]:
        request_id = secrets.token_hex(16)
        params = {
            "si": request_id,
            "ct": "q",
            "eid": device["did"],
            "et": device["class"],
            "er": device["resource"],
            "apn": str(apn),
            "fmt": "j",
        }
        headers = {
            hdrs.AUTHORIZATION: f"Bearer {token}",
            "x-eco-request-id": request_id,
            hdrs.CONTENT_TYPE: "application/octet-stream",
            hdrs.USER_AGENT: "okhttp/4.9.1",
        }
        payload = _create_payload(body_data)
        try:
            async with self._session.post(
                self._url,
                params=params,
                data=orjson.dumps(payload),
                headers=headers,
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                if 300 <= response.status < 400:
                    raise ApiError("Unexpected NGIOT redirect")
                # The endpoint can return JSON with a non-JSON content type.
                response_data = await response.json(
                    content_type=None, loads=orjson.loads
                )
        except TimeoutError as ex:
            raise ApiTimeoutError(path=_PATH, timeout=_TIMEOUT) from ex
        except ClientResponseError as ex:
            if ex.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                raise AuthenticationError("NGIOT request was not authorized") from ex
            raise ApiError("NGIOT request failed") from ex
        except ClientError as ex:
            raise ApiError("NGIOT request failed") from ex
        except orjson.JSONDecodeError as ex:
            raise ApiError("Invalid NGIOT response") from ex
        return _validate_response(response_data)


def _create_payload(body_data: dict[str, Any] | list[Any]) -> dict[str, Any]:
    now = time.time()
    if isinstance(body_data, dict):
        body_data = body_data.copy()
        body_data.setdefault("reqId", str(int(now * 1000)))
        body_data.setdefault("timestamp", int(now))
    return {
        "body": {"data": body_data},
        "header": {
            "channel": "Android",
            "m": "request",
            "pri": 2,
            "reqid": "".join(
                secrets.choice(string.ascii_letters + string.digits) for _ in range(6)
            ),
            "ts": str(int(now * 1000)),
            "tzc": "UTC",
            "tzm": 0,
            "ver": "0.0.22",
        },
    }


def _validate_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ApiError("Invalid NGIOT response")
    body = response.get("body")
    if not isinstance(body, dict):
        raise ApiError("Invalid NGIOT response body")
    code = body.get("code")
    if isinstance(code, bool) or code not in (0, "0000"):
        raise ApiError("NGIOT request was rejected")
    return response
