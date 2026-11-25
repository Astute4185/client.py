from __future__ import annotations

from http import HTTPStatus
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, Mock, patch

from aiohttp import ClientResponse, ClientSession, hdrs
import pytest

from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.exceptions import ApiError
from deebot_client.models import Credentials

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from deebot_client.models import ApiDeviceInfo


_PASSWORD_HASH = "password-hash"  # noqa: S105
_DEVICE: ApiDeviceInfo = {
    "class": "eyfj07",
    "company": "eco-ng",
    "did": "device-id",
    "name": "Test device",
    "resource": "resource-id",
    "service": {"mqs": "api-ngiot.dc-na.ww.ecouser.net"},
}


@pytest.fixture
async def ngiot_authenticator() -> AsyncGenerator[tuple[Authenticator, Mock]]:
    session = Mock(spec_set=ClientSession)
    config = create_rest_config(session, device_id="client-id", alpha_2_country="AU")
    authenticator = Authenticator(config, "user@example.com", _PASSWORD_HASH)
    yield authenticator, session
    await authenticator.teardown()


def _response(payload: dict[str, Any]) -> MagicMock:
    response = Mock(spec_set=ClientResponse)
    response.status = HTTPStatus.OK
    response.json.return_value = payload
    context = MagicMock()
    context.__aenter__.return_value = response
    return context


async def test_ngiot_client_is_lazy(
    ngiot_authenticator: tuple[Authenticator, Mock],
) -> None:
    authenticator, session = ngiot_authenticator
    with patch.object(authenticator, "_login") as login:
        authenticator.get_ngiot_client(_DEVICE)
        login.assert_not_awaited()
    session.post.assert_not_called()


async def test_legacy_authentication_unchanged(
    ngiot_authenticator: tuple[Authenticator, Mock],
) -> None:
    authenticator, session = ngiot_authenticator
    credentials = Credentials("portal-token", "user-id", int(time.time()) + 3600)
    with (
        patch.object(authenticator, "_login", return_value=credentials) as login,
        patch("deebot_client.authentication.NgiotClient") as ngiot_client,
    ):
        assert await authenticator.authenticate() == credentials
        assert await authenticator.authenticate() == credentials
        login.assert_awaited_once()
        ngiot_client.assert_not_called()
    session.post.assert_not_called()


@pytest.mark.parametrize(
    "host",
    [
        "api-ngiot.dc-na.ww.ecouser.net",
        "https://api-ngiot.dc-na.ww.ecouser.net/",
        "API-NGIOT.DC-NA.WW.ECOUSER.NET",
    ],
)
async def test_reuse_regional_client(
    ngiot_authenticator: tuple[Authenticator, Mock], host: str
) -> None:
    authenticator, _ = ngiot_authenticator
    other: ApiDeviceInfo = {**_DEVICE, "did": "other-device", "service": {"mqs": host}}
    assert authenticator.get_ngiot_client(_DEVICE) is authenticator.get_ngiot_client(
        other
    )


@pytest.mark.parametrize(
    "service",
    [
        None,
        {},
        {"mqs": None},
        {"mqs": 123},
        {"mqs": ""},
        {"mqs": "example.invalid"},
        {"mqs": "api-ngiot.dc-na.ww.ecouser.net.example.invalid"},
        {"mqs": "http://api-ngiot.dc-na.ww.ecouser.net"},
        {"mqs": "https://api-ngiot.dc-na.ww.ecouser.net@evil.invalid"},
        {"mqs": "api-ngiot.dc-na.ww.ecouser.net:443"},
        {"mqs": "api-ngiot.dc-na.ww.ecouser.net/path"},
        {"mqs": "api-ngiot.dc-na.ww.ecouser.net?foo=bar"},
        {"mqs": "api-ngiot.dc-\u0130.ww.ecouser.net"},
    ],
)
async def test_invalid_service_endpoint(
    ngiot_authenticator: tuple[Authenticator, Mock], service: Any
) -> None:
    authenticator, session = ngiot_authenticator
    device: ApiDeviceInfo = {**_DEVICE, "service": service}
    with pytest.raises(ApiError, match="NGIOT service endpoint"):
        authenticator.get_ngiot_client(device)
    session.post.assert_not_called()


async def test_missing_service_endpoint(
    ngiot_authenticator: tuple[Authenticator, Mock],
) -> None:
    authenticator, _ = ngiot_authenticator
    device = _DEVICE.copy()
    del device["service"]
    with pytest.raises(ApiError, match="NGIOT service endpoint"):
        authenticator.get_ngiot_client(device)


async def test_regional_endpoints(
    ngiot_authenticator: tuple[Authenticator, Mock],
) -> None:
    authenticator, session = ngiot_authenticator
    other: ApiDeviceInfo = {
        **_DEVICE,
        "service": {"mqs": "api-ngiot.dc-eu.ww.ecouser.net"},
    }
    na = authenticator.get_ngiot_client(_DEVICE)
    eu = authenticator.get_ngiot_client(other)
    assert na is not eu
    ack = {"body": {"code": 0}}
    session.post.side_effect = [
        _response({"code": 0, "data": {"data": {"token": "na-sst"}}}),
        _response(ack),
        _response({"code": 0, "data": {"data": {"token": "eu-sst"}}}),
        _response(ack),
        _response(ack),
    ]
    credentials = Credentials("portal-token", "user-id", int(time.time()) + 3600)
    data = {"fields": ["battery"]}
    with patch.object(authenticator, "_login", return_value=credentials) as login:
        assert await na.request(_DEVICE, apn=10001, body_data=data) == ack
        assert await eu.request(other, apn=10001, body_data=data) == ack
        assert await na.request(_DEVICE, apn=10001, body_data=data) == ack
        login.assert_awaited_once()
    requests = session.post.call_args_list
    assert [request.args[0] for request in requests] == [
        "https://api-base.dc-na.ww.ecouser.net/api/new-perm/token/sst/issue",
        "https://api-ngiot.dc-na.ww.ecouser.net/api/iot/endpoint/control",
        "https://api-base.dc-eu.ww.ecouser.net/api/new-perm/token/sst/issue",
        "https://api-ngiot.dc-eu.ww.ecouser.net/api/iot/endpoint/control",
        "https://api-ngiot.dc-na.ww.ecouser.net/api/iot/endpoint/control",
    ]
    assert [request.kwargs["headers"][hdrs.AUTHORIZATION] for request in requests] == [
        "Bearer portal-token",
        "Bearer na-sst",
        "Bearer portal-token",
        "Bearer eu-sst",
        "Bearer na-sst",
    ]


async def test_teardown_discards_clients(
    ngiot_authenticator: tuple[Authenticator, Mock],
) -> None:
    authenticator, session = ngiot_authenticator
    client = authenticator.get_ngiot_client(_DEVICE)
    await authenticator.teardown()
    assert authenticator.get_ngiot_client(_DEVICE) is not client
    session.post.assert_not_called()
