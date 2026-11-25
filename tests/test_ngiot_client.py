from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus
import string
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock, call, patch

from aiohttp import ClientConnectionError, ClientSession, hdrs, web
from aiohttp.test_utils import TestServer
import orjson
import pytest

from deebot_client.exceptions import ApiError, ApiTimeoutError, AuthenticationError
from deebot_client.models import Credentials
from deebot_client.ngiot_client import NgiotClient
from deebot_client.sst_authentication import SstAuthenticator

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from deebot_client.models import ApiDeviceInfo

_PATH = "/api/iot/endpoint/control"
_DEVICE: ApiDeviceInfo = {
    "class": "eyfj07",
    "did": "device-id",
    "resource": "resource-id",
    "company": "eco-ng",
    "name": "Test device",
}
_NOW = 1_800_000_000
_ACK = {"body": {"code": 0, "msg": "ok"}}


@dataclass
class _NgiotApi:
    """Fake endpoint with a configurable response sequence."""

    responses: list[tuple[int, bytes]] = field(
        default_factory=lambda: [(HTTPStatus.OK, orjson.dumps(_ACK))]
    )
    requests: list[tuple[dict[str, str], dict[str, str], dict[str, Any]]] = field(
        default_factory=list
    )

    async def handle(self, request: web.Request) -> web.Response:
        """Record a request and return the configured response."""
        self.requests.append(
            (
                dict(request.query),
                dict(request.headers),
                orjson.loads(await request.read()),
            )
        )
        status, body = self.responses.pop(0)
        return web.Response(
            body=body,
            status=status,
            content_type="application/octet-stream",
            headers={hdrs.LOCATION: _PATH}
            if status == HTTPStatus.TEMPORARY_REDIRECT
            else None,
        )


@pytest.fixture
async def ngiot_api() -> AsyncGenerator[tuple[NgiotClient, _NgiotApi, Mock]]:
    api = _NgiotApi()
    app = web.Application()
    app.router.add_post(_PATH, api.handle)
    authenticator = Mock(spec_set=SstAuthenticator)
    authenticator.get_token.return_value = "sst-token"
    async with TestServer(app) as server, ClientSession() as session:
        yield (
            NgiotClient(session, authenticator, base_url=str(server.make_url("/"))),
            api,
            authenticator,
        )


async def test_request(ngiot_api: tuple[NgiotClient, _NgiotApi, Mock]) -> None:
    client, api, authenticator = ngiot_api
    response = {"body": {"code": 0, "data": {"battery": 100}}}
    api.responses = [(HTTPStatus.OK, orjson.dumps(response))]
    data = {"fields": ["battery"], "type": "get"}
    with patch("deebot_client.ngiot_client.time") as clock:
        clock.time.return_value = _NOW
        assert await client.request(_DEVICE, apn=10001, body_data=data) == response
    assert data == {"fields": ["battery"], "type": "get"}
    assert len(api.requests) == 1
    params, headers, payload = api.requests[0]
    request_id = params["si"]
    assert len(request_id) == 32
    assert all(char in string.hexdigits for char in request_id)
    assert params == {
        "si": request_id,
        "ct": "q",
        "eid": "device-id",
        "et": "eyfj07",
        "er": "resource-id",
        "apn": "10001",
        "fmt": "j",
    }
    assert headers[hdrs.AUTHORIZATION] == "Bearer sst-token"
    assert headers[hdrs.CONTENT_TYPE] == "application/octet-stream"
    assert headers[hdrs.USER_AGENT] == "okhttp/4.9.1"
    assert headers["x-eco-request-id"] == request_id
    body_request_id = payload["header"]["reqid"]
    assert len(body_request_id) == 6
    assert all(char in string.ascii_letters + string.digits for char in body_request_id)
    assert payload == {
        "body": {
            "data": {
                **data,
                "reqId": str(_NOW * 1000),
                "timestamp": _NOW,
            }
        },
        "header": {
            "channel": "Android",
            "m": "request",
            "pri": 2,
            "reqid": body_request_id,
            "ts": str(_NOW * 1000),
            "tzc": "UTC",
            "tzm": 0,
            "ver": "0.0.22",
        },
    }
    authenticator.get_token.assert_awaited_once_with(_DEVICE)
    authenticator.invalidate.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [
        {"pauseSwitch": True, "reqId": "caller-id", "timestamp": 123},
        [{"pauseSwitch": True}],
    ],
)
async def test_ack(
    ngiot_api: tuple[NgiotClient, _NgiotApi, Mock], data: dict[str, Any] | list[Any]
) -> None:
    client, api, _ = ngiot_api
    assert await client.request(_DEVICE, apn="40009", body_data=data) == _ACK
    assert len(api.requests) == 1
    assert api.requests[0][2]["body"]["data"] == data


async def test_string_success_code(
    ngiot_api: tuple[NgiotClient, _NgiotApi, Mock],
) -> None:
    client, api, _ = ngiot_api
    response = {"body": {"code": "0000"}}
    api.responses = [(HTTPStatus.OK, orjson.dumps(response))]
    assert await client.request(_DEVICE, apn=10001, body_data={}) == response


@pytest.mark.parametrize("status", [HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN])
async def test_authentication_retry(
    ngiot_api: tuple[NgiotClient, _NgiotApi, Mock], status: int
) -> None:
    client, api, authenticator = ngiot_api
    api.responses.insert(0, (status, b""))
    authenticator.get_token.side_effect = ["old-token", "new-token"]
    assert await client.request(_DEVICE, apn=10001, body_data={}) == _ACK
    assert len(api.requests) == 2
    assert api.requests[0][1][hdrs.AUTHORIZATION] == "Bearer old-token"
    assert api.requests[1][1][hdrs.AUTHORIZATION] == "Bearer new-token"
    assert api.requests[0][0]["si"] != api.requests[1][0]["si"]
    assert authenticator.get_token.await_args_list == [call(_DEVICE), call(_DEVICE)]
    authenticator.invalidate.assert_awaited_once_with(_DEVICE)


async def test_repeated_authentication_failure(
    ngiot_api: tuple[NgiotClient, _NgiotApi, Mock],
) -> None:
    client, api, authenticator = ngiot_api
    api.responses = [(HTTPStatus.UNAUTHORIZED, b"")] * 2
    with pytest.raises(AuthenticationError, match="not authorized"):
        await client.request(_DEVICE, apn=10001, body_data={})
    assert len(api.requests) == 2
    assert authenticator.invalidate.await_args_list == [call(_DEVICE), call(_DEVICE)]


async def test_token_failure(
    ngiot_api: tuple[NgiotClient, _NgiotApi, Mock],
) -> None:
    client, api, authenticator = ngiot_api
    authenticator.get_token.side_effect = AuthenticationError("SST issue failed")
    with pytest.raises(AuthenticationError, match="SST issue failed"):
        await client.request(_DEVICE, apn=10001, body_data={})
    assert not api.requests
    authenticator.get_token.assert_awaited_once_with(_DEVICE)
    authenticator.invalidate.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"null",
        b"[]",
        b"{}",
        b'{"body": null}',
        b'{"body": {}}',
        b'{"body": {"code": false}}',
        b'{"body": {"code": 1, "msg": "cmd busy"}}',
        b'{"body": {"code": 500}}',
    ],
)
async def test_invalid_response(
    ngiot_api: tuple[NgiotClient, _NgiotApi, Mock], body: bytes
) -> None:
    client, api, authenticator = ngiot_api
    api.responses = [(HTTPStatus.OK, body)]
    with pytest.raises(ApiError, match="NGIOT"):
        await client.request(_DEVICE, apn=40009, body_data={"pauseSwitch": True})
    assert len(api.requests) == 1
    authenticator.invalidate.assert_not_awaited()


@pytest.mark.parametrize(
    "status",
    [HTTPStatus.TEMPORARY_REDIRECT, HTTPStatus.NOT_FOUND, HTTPStatus.BAD_GATEWAY],
)
async def test_http_errors(
    ngiot_api: tuple[NgiotClient, _NgiotApi, Mock], status: int
) -> None:
    client, api, authenticator = ngiot_api
    api.responses = [(status, b"")]
    with pytest.raises(ApiError, match="NGIOT"):
        await client.request(_DEVICE, apn=40009, body_data={"pauseSwitch": True})
    assert len(api.requests) == 1
    authenticator.invalidate.assert_not_awaited()


@pytest.mark.parametrize(
    ("cause", "error"),
    [(TimeoutError, ApiTimeoutError), (ClientConnectionError, ApiError)],
)
async def test_transport_errors(cause: type[Exception], error: type[Exception]) -> None:
    session = Mock(spec_set=ClientSession)
    session.post.side_effect = cause
    authenticator = Mock(spec_set=SstAuthenticator)
    authenticator.get_token.return_value = "sst-token"
    client = NgiotClient(session, authenticator, base_url="https://example.invalid")
    with pytest.raises(error):
        await client.request(_DEVICE, apn=40009, body_data={"pauseSwitch": True})
    session.post.assert_called_once()
    authenticator.invalidate.assert_not_awaited()


async def test_sst_integration() -> None:
    api = _NgiotApi()
    api.responses.insert(0, (HTTPStatus.UNAUTHORIZED, b""))
    issued_tokens: list[str] = []

    async def issue_sst(request: web.Request) -> web.Response:
        assert request.headers[hdrs.AUTHORIZATION] == "Bearer portal-token"
        token = f"sst-token-{len(issued_tokens)}"
        issued_tokens.append(token)
        return web.Response(
            body=orjson.dumps({"code": 0, "data": {"data": {"token": token}}}),
            content_type="application/json",
        )

    app = web.Application()
    app.router.add_post("/api/new-perm/token/sst/issue", issue_sst)
    app.router.add_post(_PATH, api.handle)
    portal = Mock()
    portal.authenticate = AsyncMock(return_value=Credentials("portal-token", "user-id"))
    async with TestServer(app) as server, ClientSession() as session:
        base_url = str(server.make_url("/"))
        authenticator = SstAuthenticator(session, portal, base_url=base_url)
        client = NgiotClient(session, authenticator, base_url=base_url)
        assert await client.request(_DEVICE, apn=10001, body_data={}) == _ACK
        assert await authenticator.get_token(_DEVICE) == "sst-token-1"
    assert issued_tokens == ["sst-token-0", "sst-token-1"]
    assert [headers[hdrs.AUTHORIZATION] for _, headers, _ in api.requests] == [
        "Bearer sst-token-0",
        "Bearer sst-token-1",
    ]
