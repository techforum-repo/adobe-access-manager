from __future__ import annotations

import time
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from .config import settings
from .settings_store import default_country, default_identity_type
from .utils import classify_environment, classify_system, is_privileged


def _group_type(item: dict[str, Any]) -> str:
    return str(item.get("type") or item.get("groupType") or item.get("group_type") or "").lower().replace("-", "_").replace(" ", "_")


def is_user_group(item: dict[str, Any]) -> bool:
    """Return True only when Adobe explicitly tags this as a user group.

    A bare/ambiguous "group" type, or no type field at all, is not treated as
    a user group — Adobe's groups endpoint tags real user groups explicitly
    ("user-group" / "USER_GROUP"), so anything less than that is excluded
    rather than guessed from the name. Failing closed here matters: this
    filter decides what gets cached as "safe to assign during provisioning",
    and silently caching a product profile or admin group as a user group
    would be a real safety problem, not just a cosmetic one.
    """
    kind = _group_type(item)
    if kind in {"user_group", "usergroup"}:
        return True
    return False


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _looks_like_user(mapping: dict[str, Any]) -> bool:
    """Return True only when the mapping itself contains scalar identity fields."""
    for key in ("email", "username", "userName", "firstname", "firstName", "lastname", "lastName"):
        value = mapping.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return True
    # Some UMAPI responses use `user` as the username, but other responses use
    # it as a nested object. Only treat scalar values as identity fields.
    value = mapping.get("user")
    return isinstance(value, (str, int, float)) and bool(str(value).strip())


def _unwrap_user_payload(payload: Any) -> dict[str, Any]:
    """Find the actual user record in UMAPI exact-user response envelopes."""
    if not isinstance(payload, dict):
        return {}

    # Inspect nested records first. A common exact-user response is
    # {"user": {"username": ..., "groups": [...]}}. Checking the outer
    # envelope first previously caused the entire nested dictionary to be
    # converted into the displayed email address.
    candidates: list[dict[str, Any]] = []
    for key in ("user", "result", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))

    users = payload.get("users")
    if isinstance(users, list):
        candidates.extend(item for item in users if isinstance(item, dict))
    elif isinstance(users, dict):
        candidates.append(users)

    candidates.append(payload)
    for candidate in candidates:
        if _looks_like_user(candidate):
            return candidate
    return candidates[0] if candidates else {}

def normalize_groups(raw: Any) -> set[str]:
    """Normalize UMAPI group memberships from strings, objects, and nested envelopes."""
    values: set[str] = set()
    if raw is None:
        return values
    if isinstance(raw, str):
        return {raw} if raw.strip() else set()
    if isinstance(raw, dict):
        direct = _first_value(raw, "groupName", "name", "group", "userGroup")
        if isinstance(direct, str) and direct.strip():
            values.add(direct.strip())
        for key in ("groups", "userGroups", "memberships", "items", "results"):
            if key in raw:
                values.update(normalize_groups(raw[key]))
        return values
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            values.update(normalize_groups(item))
    return values


def normalize_user(payload: dict[str, Any]) -> dict[str, Any]:
    item = _unwrap_user_payload(payload)
    email = str(_first_value(item, "email", "username", "user", "userName") or "").strip().lower()

    groups_raw = _first_value(
        item,
        "groups",
        "group",
        "userGroups",
        "memberships",
        "groupMemberships",
    )
    if groups_raw is None and item is not payload:
        groups_raw = _first_value(
            payload,
            "groups",
            "group",
            "userGroups",
            "memberships",
            "groupMemberships",
        )

    first_name = _first_value(item, "firstname", "firstName", "givenName") or ""
    last_name = _first_value(item, "lastname", "lastName", "familyName", "surname") or ""
    identity_type = _first_value(item, "type", "identityType", "idType") or ""
    status = _first_value(item, "status", "accountStatus", "state")
    if status in (None, ""):
        # UMAPI exact-user responses do not always provide an explicit status.
        # A returned user record is treated as active unless explicitly disabled.
        disabled = _first_value(item, "disabled", "isDisabled")
        status = "Disabled" if disabled is True else "Active"

    return {
        "email": email,
        "first_name": str(first_name),
        "last_name": str(last_name),
        "identity_type": str(identity_type),
        "status": str(status),
        "domain": email.rsplit("@", 1)[-1] if "@" in email else "",
        "groups": normalize_groups(groups_raw),
        "raw": payload,
    }


class MockAdobeClient:
    def __init__(self) -> None:
        self.groups = [
            {"name": "AEM-PROD-AUTHORS", "system": "AEM", "environment": "Production", "privileged": False, "member_count": 46},
            {"name": "AEM-DEV-DEVELOPERS", "system": "AEM", "environment": "Development", "privileged": True, "member_count": 18},
            {"name": "AEP-DATA-ENGINEERS", "system": "AEP", "environment": "Production", "privileged": False, "member_count": 31},
            {"name": "CJA-ANALYSTS", "system": "CJA", "environment": "Production", "privileged": False, "member_count": 72},
        ]
        self.users: dict[str, dict[str, Any]] = {
            "existing.user@example.com": {"email": "existing.user@example.com", "first_name": "Existing", "last_name": "User", "identity_type": "federatedID", "status": "active", "groups": {"AEM-PROD-AUTHORS"}}
        }

    async def test_connection(self) -> dict[str, Any]:
        return {"connected": True, "mode": "mock", "group_count": len(self.groups)}

    async def list_groups(self, max_pages: int | None = None) -> list[dict[str, Any]]:
        return list(self.groups)

    async def list_users(self) -> list[dict[str, Any]]:
        return list(self.users.values())

    async def get_user(self, email: str) -> dict[str, Any] | None:
        return self.users.get(email.lower())

    async def provision(self, email: str, first_name: str, last_name: str, groups: list[str], test_only: bool) -> dict[str, Any]:
        existing = self.users.get(email.lower())
        missing = [g for g in groups if not existing or g not in existing["groups"]]
        will_create = not bool(existing)
        if test_only:
            return {"success": True, "test_only": True, "created": will_create, "groups_added": missing, "raw": {}}
        if not existing:
            existing = {"email": email.lower(), "first_name": first_name, "last_name": last_name, "identity_type": "federatedID", "status": "active", "groups": set()}
            self.users[email.lower()] = existing
        existing["groups"].update(missing)
        return {"success": True, "test_only": False, "created": will_create, "groups_added": missing, "raw": {}}


class AdobeUMAPIClient:
    # Pagination loops call _request() once per page; a misbehaving Adobe
    # response that never sets lastPage would otherwise loop forever.
    _PAGE_SAFETY_CAP = 20_000

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at = 0.0

    def _check_config(self) -> None:
        if not settings.adobe_configured:
            raise RuntimeError("Adobe credentials are incomplete. Update .env and restart the app.")

    def _new_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=settings.adobe_http_timeout, trust_env=True)

    async def _token_value(self, http: httpx.AsyncClient) -> str:
        self._check_config()
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        try:
            response = await http.post(settings.adobe_ims_token_url, data={
                "client_id": settings.adobe_client_id,
                "client_secret": settings.adobe_client_secret,
                "grant_type": "client_credentials",
                "scope": settings.adobe_scopes,
            })
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Adobe IMS connection failed: {exc}") from exc
        data = response.json()
        self._token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 86399))
        return self._token

    async def _headers(self, http: httpx.AsyncClient) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._token_value(http)}", "x-api-key": settings.adobe_client_id, "Accept": "application/json", "Content-Type": "application/json"}

    async def _request(self, http: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> Any:
        """Issue one request on the given (caller-owned) client.

        `http` is always passed explicitly rather than opened per call: a
        paginated fetch can be dozens to hundreds of requests, and opening a
        fresh TCP/TLS connection for each one is real, avoidable overhead —
        every public method below owns one `httpx.AsyncClient` for its full
        duration (including internal pagination) and reuses it here.
        """
        try:
            response = await http.request(method, url, headers=await self._headers(http), **kwargs)
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json() if response.content else {}
        except httpx.ConnectError as exc:
            raise RuntimeError(f"Cannot connect to Adobe. Check VPN, proxy, and firewall. Endpoint: {url}") from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"Adobe request timed out. Endpoint: {url}") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise RuntimeError(f"Adobe returned HTTP {exc.response.status_code}: {detail}") from exc
        except httpx.HTTPError as exc:
            # Catch-all for httpx failure modes that aren't a plain connect/timeout/status
            # error (proxy errors, protocol errors, ...) — still normalized to a
            # RuntimeError with the endpoint attached, instead of a raw httpx
            # exception reaching the UI's generic "Something went wrong" fallback.
            raise RuntimeError(f"Adobe request failed: {exc}. Endpoint: {url}") from exc

    async def test_connection(self) -> dict[str, Any]:
        groups = await self.list_groups(max_pages=1)
        return {"connected": True, "mode": "live", "org_id": settings.adobe_org_id, "group_count_first_page": len(groups)}

    async def list_groups(self, max_pages: int | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 0
        async with self._new_http_client() as http:
            while True:
                url = f"{settings.adobe_umapi_base_url}/groups/{quote(settings.adobe_org_id, safe='@')}/{page}"
                data = await self._request(http, "GET", url)
                if not isinstance(data, dict):
                    break
                for item in data.get("groups", []):
                    if not is_user_group(item):
                        continue
                    name = item.get("groupName") or item.get("name")
                    if not name:
                        continue
                    result.append({"name": str(name), "system": classify_system(str(name)), "environment": classify_environment(str(name)), "privileged": is_privileged(str(name)), "member_count": item.get("userCount") or item.get("memberCount")})
                if data.get("lastPage", True) or (max_pages is not None and page + 1 >= max_pages):
                    break
                page += 1
                if page >= self._PAGE_SAFETY_CAP:
                    raise RuntimeError(f"Stopped after {self._PAGE_SAFETY_CAP} pages without Adobe reporting lastPage — the response may be malformed.")
        return result

    async def list_users(self) -> list[dict[str, Any]]:
        """Fetch the entire org's user directory — no page cap, same as list_groups()."""
        result: list[dict[str, Any]] = []
        page = 0
        async with self._new_http_client() as http:
            while True:
                url = f"{settings.adobe_umapi_base_url}/users/{quote(settings.adobe_org_id, safe='@')}/{page}"
                data = await self._request(http, "GET", url)
                if not isinstance(data, dict):
                    break
                result.extend(normalize_user(x) for x in data.get("users", []))
                if data.get("lastPage", True):
                    break
                page += 1
                if page >= self._PAGE_SAFETY_CAP:
                    raise RuntimeError(f"Stopped after {self._PAGE_SAFETY_CAP} pages without Adobe reporting lastPage — the response may be malformed.")
        return result

    async def get_user(self, email: str) -> dict[str, Any] | None:
        url = f"{settings.adobe_umapi_base_url}/organizations/{quote(settings.adobe_org_id, safe='@')}/users/{quote(email, safe='')}"
        async with self._new_http_client() as http:
            data = await self._request(http, "GET", url)
        return normalize_user(data) if isinstance(data, dict) and data else None

    async def provision(self, email: str, first_name: str, last_name: str, groups: list[str], test_only: bool) -> dict[str, Any]:
        if not test_only and not settings.adobe_write_enabled:
            raise RuntimeError("Live writes are disabled. Set ADOBE_WRITE_ENABLED=true only after validating test mode.")
        existing = await self.get_user(email)
        current = existing["groups"] if existing else set()
        missing = [g for g in groups if g not in current]
        steps: list[dict[str, Any]] = []
        if not existing:
            action = {"email": email, "country": default_country().upper(), "firstname": first_name, "lastname": last_name, "option": "ignoreIfAlreadyExists"}
            identity = default_identity_type().lower()
            key = "createFederatedID" if identity == "federatedid" else ("createEnterpriseID" if identity == "enterpriseid" else "addAdobeID")
            steps.append({key: action})
        if missing:
            steps.append({"add": {"group": missing}})
        if not steps:
            return {"success": True, "test_only": test_only, "created": False, "groups_added": [], "raw": {"message": "No changes"}}
        command = [{"user": email, "requestID": str(uuid.uuid4()), "do": steps}]
        url = f"{settings.adobe_umapi_base_url}/action/{quote(settings.adobe_org_id, safe='@')}?testOnly={'true' if test_only else 'false'}"
        async with self._new_http_client() as http:
            raw = await self._request(http, "POST", url, json=command)
        errors = raw.get("errors", []) if isinstance(raw, dict) else []
        return {"success": not bool(errors), "test_only": test_only, "created": not bool(existing), "groups_added": missing, "raw": raw}


client = MockAdobeClient() if settings.mock_adobe else AdobeUMAPIClient()
