from __future__ import annotations

"""Coverage for AdobeUMAPIClient's real HTTP path (previously untested — the
rest of the suite only ever exercises MockAdobeClient). Uses httpx.MockTransport
so no real network call happens; the app's own `httpx.AsyncClient(...)` calls
are redirected to it by monkeypatching `httpx.AsyncClient` for the test.
"""

import httpx
import pytest

from adobe_access import client as client_module
from adobe_access import database
from adobe_access.client import AdobeUMAPIClient
from adobe_access.config import settings
from adobe_access.errors import AdobeRateLimitError
from adobe_access.provisioning import run

ORG_ID = "org@AdobeOrg"


@pytest.fixture()
def configured(tmp_path, monkeypatch):
    # provision() reads default_country/default_identity_type from the DB
    # (via settings_store) — needs a real, initialized DB, not just a mocked
    # HTTP transport.
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "adobe_umapi_client.db")
    database.initialize()
    monkeypatch.setattr(settings, "adobe_org_id", ORG_ID)
    monkeypatch.setattr(settings, "adobe_client_id", "client-id")
    monkeypatch.setattr(settings, "adobe_client_secret", "client-secret")
    monkeypatch.setattr(settings, "adobe_scopes", "openid,AdobeID,user_management_sdk")
    monkeypatch.setattr(settings, "adobe_ims_token_url", "https://ims.example.com/token")
    monkeypatch.setattr(settings, "adobe_umapi_base_url", "https://umapi.example.com/v2/usermanagement")
    monkeypatch.setattr(settings, "adobe_write_enabled", True)


def _install_transport(monkeypatch, handler):
    """Redirect every httpx.AsyncClient constructed by client.py to a MockTransport,
    and return a call counter so tests can assert on connection reuse."""
    real_async_client = httpx.AsyncClient
    calls = {"clients_constructed": 0}

    def factory(**kwargs):
        calls["clients_constructed"] += 1
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return calls


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 3600})


def test_list_groups_paginates_and_reuses_one_http_client(configured, monkeypatch):
    """The whole point of the refactor: N pages should open exactly ONE
    httpx.AsyncClient, not N (previously a fresh client per page)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        if request.url.path.endswith("/0"):
            return httpx.Response(200, json={
                "groups": [{"groupName": "AEM-PROD-AUTHORS", "type": "user-group"}],
                "lastPage": False,
            })
        if request.url.path.endswith("/1"):
            return httpx.Response(200, json={
                "groups": [{"groupName": "AEP-DATA-ENGINEERS", "type": "user-group"}],
                "lastPage": True,
            })
        raise AssertionError(f"unexpected request: {request.url}")

    calls = _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    groups = run(client.list_groups())

    assert {g["name"] for g in groups} == {"AEM-PROD-AUTHORS", "AEP-DATA-ENGINEERS"}
    # One AsyncClient for the whole paginated fetch (list_groups), not one per page.
    assert calls["clients_constructed"] == 1


def test_list_groups_filters_non_user_groups(configured, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        return httpx.Response(200, json={
            "groups": [
                {"groupName": "AEM-PROD-AUTHORS", "type": "user-group"},
                {"groupName": "SOME-PRODUCT-PROFILE", "type": "product-profile"},
                {"groupName": "AMBIGUOUS", "type": "group"},
            ],
            "lastPage": True,
        })

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    groups = run(client.list_groups())
    assert {g["name"] for g in groups} == {"AEM-PROD-AUTHORS"}


def test_list_groups_stops_at_the_page_safety_cap_instead_of_looping_forever(configured, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        return httpx.Response(200, json={"groups": [], "lastPage": False})  # never stops on its own

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    client._PAGE_SAFETY_CAP = 3  # keep the test fast
    with pytest.raises(RuntimeError, match="Stopped after 3 pages"):
        run(client.list_groups())


def test_get_user_returns_none_on_404(configured, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        return httpx.Response(404)

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    assert run(client.get_user("nobody@example.com")) is None


def test_get_user_normalizes_a_found_user(configured, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        return httpx.Response(200, json={
            "email": "jane.doe@example.com", "firstname": "Jane", "lastname": "Doe",
            "type": "federatedID", "status": "active", "groups": ["AEM-PROD-AUTHORS"],
        })

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    user = run(client.get_user("jane.doe@example.com"))
    assert user["email"] == "jane.doe@example.com"
    assert user["groups"] == {"AEM-PROD-AUTHORS"}


def test_provision_creates_a_new_user_and_adds_groups(configured, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        if request.method == "GET":
            return httpx.Response(404)  # no existing user
        assert request.method == "POST"
        return httpx.Response(200, json={"errors": []})

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    result = run(client.provision("new.user@example.com", "New", "User", ["AEM-PROD-AUTHORS"], test_only=False))
    assert result["success"] is True
    assert result["created"] is True
    assert result["groups_added"] == ["AEM-PROD-AUTHORS"]


def test_provision_sends_a_remove_step_for_a_group_the_user_currently_holds(configured, monkeypatch):
    sent_commands = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        if request.method == "GET":
            return httpx.Response(200, json={
                "email": "jane.doe@example.com", "type": "federatedID", "status": "active",
                "groups": ["AEM-PROD-AUTHORS", "AEP-DATA-ENGINEERS"],
            })
        assert request.method == "POST"
        sent_commands.append(request.content)
        return httpx.Response(200, json={"errors": []})

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    result = run(client.provision(
        "jane.doe@example.com", "Jane", "Doe", [], test_only=False,
        groups_to_remove=["AEM-PROD-AUTHORS", "GROUP-NEVER-HELD"],
    ))
    assert result["success"] is True
    # Only the group actually held is reported removed — a group the user was
    # never in is silently dropped, not sent to Adobe or reported as removed.
    assert result["groups_removed"] == ["AEM-PROD-AUTHORS"]
    import json as _json
    sent_body = _json.loads(sent_commands[0])
    assert sent_body[0]["do"] == [{"remove": {"group": ["AEM-PROD-AUTHORS"]}}]


def test_a_generic_httpx_failure_is_normalized_to_a_runtime_error(configured, monkeypatch):
    """Regression: previously only ConnectError/TimeoutException/HTTPStatusError
    were caught — a ProtocolError (or similar) reached the UI as a raw httpx
    exception instead of a clean, endpoint-attached RuntimeError."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        raise httpx.ProtocolError("peer closed connection")

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    with pytest.raises(RuntimeError, match="Adobe request failed"):
        run(client.list_groups())


def test_connect_error_still_gets_its_specific_friendly_message(configured, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        raise httpx.ConnectError("connection refused")

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    with pytest.raises(RuntimeError, match="Cannot connect to Adobe"):
        run(client.list_groups())


def test_429_raises_adobe_rate_limit_error_with_parsed_retry_after(configured, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        return httpx.Response(429, text="slow down", headers={"Retry-After": "7"})

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    with pytest.raises(AdobeRateLimitError) as exc_info:
        run(client.list_groups())
    assert exc_info.value.retry_after == 7.0


def test_429_without_a_retry_after_header_leaves_it_none(configured, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return _token_response()
        return httpx.Response(429, text="slow down")

    _install_transport(monkeypatch, handler)
    client = AdobeUMAPIClient()
    with pytest.raises(AdobeRateLimitError) as exc_info:
        run(client.list_groups())
    assert exc_info.value.retry_after is None


def test_request_pacer_enforces_the_configured_minimum_interval(monkeypatch):
    """Bulk provisioning fires many requests back to back; the pacer's job is
    to space them out so that never becomes a 429 burst. Verified against a
    fake clock so the test doesn't actually sleep."""
    pacer = client_module._RequestPacer()
    monkeypatch.setattr(client_module, "adobe_requests_per_second", lambda: 5.0)  # -> 0.2s interval
    fake_now = {"t": 100.0}
    sleeps: list[float] = []
    monkeypatch.setattr(client_module.time, "monotonic", lambda: fake_now["t"])

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_now["t"] += seconds

    monkeypatch.setattr(client_module.time, "sleep", fake_sleep)

    pacer.wait()  # first call: nothing to wait on yet
    assert sleeps == []
    pacer.wait()  # immediately after: must wait out the rest of the interval
    assert sleeps == [pytest.approx(0.2)]

    fake_now["t"] += 0.2  # enough time has now passed on its own
    pacer.wait()
    assert sleeps == [pytest.approx(0.2)]  # unchanged — no extra sleep needed


def test_request_pacer_disabled_when_rate_limit_is_zero(monkeypatch):
    pacer = client_module._RequestPacer()
    monkeypatch.setattr(client_module, "adobe_requests_per_second", lambda: 0)
    monkeypatch.setattr(
        client_module.time, "sleep",
        lambda seconds: pytest.fail("throttling should be disabled when the rate limit is 0"),
    )
    pacer.wait()
    pacer.wait()
