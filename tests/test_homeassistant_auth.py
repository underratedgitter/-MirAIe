"""Regression tests for Home Assistant authentication.

Pins the real Authorization bytes (``Bearer test-token``) and ensures empty or
masked tokens fail before any request is sent.
"""

import asyncio

import pytest

import providers.homeassistant as homeassistant
from providers.homeassistant import HomeAssistantProvider, clean_token

URL = "http://ha.local:8123"


def provider_with(token):
    return HomeAssistantProvider({"url": URL, "token": token})


def test_auth_header_is_exactly_bearer_token():
    assert provider_with("test-token").auth_header() == {
        "Authorization": "Bearer test-token"
    }


def test_auth_header_strips_surrounding_whitespace():
    assert provider_with("  test-token\n").auth_header() == {
        "Authorization": "Bearer test-token"
    }


@pytest.mark.parametrize("token", ["", "   ", "\n\t", "***", "******", "********"])
def test_auth_header_rejects_empty_or_masked_token(token):
    with pytest.raises(RuntimeError, match="re-enter"):
        provider_with(token).auth_header()


def test_masked_token_is_named_explicitly():
    with pytest.raises(RuntimeError, match="masked"):
        clean_token("******")


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = [] if payload is None else payload

    async def json(self):
        return self.payload

    async def text(self):
        return ""


class FakeRequest:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def get(self, url, headers=None, **kwargs):
        self.requests.append(("GET", url, headers))
        return FakeRequest(self.response)

    def post(self, url, headers=None, **kwargs):
        self.requests.append(("POST", url, headers))
        return FakeRequest(self.response)

    async def close(self):
        self.closed = True


def test_fetch_states_uses_exact_auth_header():
    provider = provider_with("test-token")
    provider.session = FakeSession(FakeResponse(payload=[]))

    assert asyncio.run(provider.fetch_states()) == []
    assert provider.session.requests == [
        ("GET", f"{URL}/api/states", {"Authorization": "Bearer test-token"})
    ]


def test_fetch_entity_uses_exact_auth_header():
    provider = provider_with("test-token")
    provider.session = FakeSession(FakeResponse(payload={"state": "off"}))

    assert asyncio.run(provider.fetch_entity("climate.bedroom")) == {"state": "off"}
    assert provider.session.requests == [
        (
            "GET",
            f"{URL}/api/states/climate.bedroom",
            {"Authorization": "Bearer test-token"},
        )
    ]


def test_call_service_uses_exact_auth_header():
    provider = provider_with("test-token")
    provider.session = FakeSession(FakeResponse())
    provider._schedule_refresh = lambda: None

    asyncio.run(provider.call_service("turn_on", {"entity_id": "climate.bedroom"}))
    assert provider.session.requests == [
        (
            "POST",
            f"{URL}/api/services/climate/turn_on",
            {"Authorization": "Bearer test-token"},
        )
    ]


def test_connect_uses_exact_auth_header(monkeypatch):
    provider = provider_with("test-token")
    session = FakeSession(FakeResponse(payload=[]))
    monkeypatch.setattr(homeassistant.aiohttp, "ClientSession", lambda **_: session)
    monkeypatch.setattr(homeassistant.aiohttp, "TCPConnector", lambda **_: object())

    async def no_poll():
        return

    monkeypatch.setattr(provider, "_poll_loop", no_poll)

    asyncio.run(provider.connect())
    assert session.requests == [
        ("GET", f"{URL}/api/", {"Authorization": "Bearer test-token"}),
        ("GET", f"{URL}/api/states", {"Authorization": "Bearer test-token"}),
    ]
    asyncio.run(provider.close())
    assert session.closed is True


def test_401_message_tells_user_to_reenter_token():
    provider = provider_with("test-token")
    provider.session = FakeSession(FakeResponse(status=401))

    with pytest.raises(RuntimeError, match="create a new long-lived token and re-enter"):
        asyncio.run(provider.fetch_states())


def test_connect_rejects_masked_token_before_network(monkeypatch):
    provider = provider_with("******")

    def fail_if_called(**_):
        raise AssertionError("network session should not be created")

    monkeypatch.setattr(homeassistant.aiohttp, "ClientSession", fail_if_called)

    with pytest.raises(RuntimeError, match="masked"):
        asyncio.run(provider.connect())
