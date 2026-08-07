# Adobe Access Manager 1.0

A Streamlit application for validating users against your domain, reading Adobe custom user
groups, previewing idempotent access changes, and optionally creating Federated/Enterprise/Adobe
ID users and assigning user groups through Adobe UMAPI.

## Why this instead of Adobe's Admin Console

This isn't a general Admin Console replacement — it deliberately covers one workflow (custom
user-group provisioning) end to end, rather than everything the console does. Within that
workflow, a few things it does that the console doesn't:

- **Preview is a first-class step, not an afterthought.** Every change goes through a Review
  step showing exactly what will happen — new user or existing, which groups get added, which
  are already assigned — before anything is sent to Adobe. "Run test" sends the real
  `testOnly=true` payload so you see Adobe's own validation, not just local guesses.
- **Bulk, not one-at-a-time.** Paste or upload a list of users once; validation, name derivation,
  duplicate detection, and domain checks happen together, not per-user click-through.
- **Templates.** Save a named bundle of groups once ("CJA Analyst", "AEM Prod Author") and apply
  it in one click instead of re-selecting the same 8 groups from a long list every time.
- **Compare Users and Copy Access.** Side-by-side membership diffing, and "give these people what
  this person already has" — workflows the console has no equivalent for; today that's manual
  cross-referencing across two user pages.
- **Request history as a real object**, not just an audit log line: every preview is saved with
  its users, groups, and summary, and can be reopened, reused, or re-executed later — separate
  from the lower-level audit trail of individual actions.
- **Idempotent, retry-safe execution.** Re-running the same request is explicitly safe (nothing
  gets double-assigned); transient Adobe failures retry automatically, permanent ones don't.
- **Reads hit a local cache, not the API.** Group search/browse and dashboard metrics come from a
  synced SQLite cache; Adobe is only contacted for sync, lookups, preview, and execute — faster,
  and lighter on API usage.

What it doesn't do: manage product profiles or full admin/console features, or support multiple
concurrently-authenticated users (see "Production readiness checklist" — this is a trusted-admin
tool run locally or self-hosted, not a public multi-tenant service).

## Windows quick start

1. Extract the ZIP.
2. Double-click `start-windows.bat`.
3. Open `http://localhost:8501` if the browser does not open automatically.

The first start creates `.venv`, installs packages, and copies `.env.example` to `.env`.

## Linux / macOS quick start

1. `chmod +x start-unix.sh` (first time only).
2. `./start-unix.sh`
3. Open `http://localhost:8501` if the browser does not open automatically.

Same behavior as `start-windows.bat`: creates `.venv`, installs packages, and
copies `.env.example` to `.env` on first run. Requires `python3 -m venv` to be
available (on Debian/Ubuntu: `sudo apt install python3-venv` if it's missing).

## Project structure

```
app.py                        # Entry point: page config, session init, sidebar/nav, page dispatch
adobe_access/
  ui/                          # One module per page — each exposes a single render() function
    shared.py                  #   state defaults, session helpers, group_picker, friendly-error renderer
    dashboard.py, provision.py, templates_page.py, user_groups.py, user_search.py,
    compare_users.py, copy_access.py, request_history.py, audit_history.py,
    diagnostics_page.py, settings_page.py
  client.py                    # Mock + live Adobe UMAPI clients, response normalization
  provisioning.py              # build/preview/execute — the core provisioning logic (no Streamlit)
  retry.py                     # exponential backoff for Execute's transient-failure handling
  errors.py                    # exception → friendly title/reasons classification (no Streamlit)
  database.py                  # SQLite: groups cache, templates, requests, executions, audit, settings
  settings_store.py            # non-secret runtime setting overrides layered on config.py
  diagnostics.py               # Diagnostics-page data assembly (env info, connection check, log tail)
  templates.py, users.py       # template CRUD, user lookup/compare/copy-access logic
  utils.py                     # email/name parsing, CSV/log sanitization, file-permission hardening
  config.py                    # pydantic Settings — the .env-backed source of truth
  logging_setup.py             # rotating file logger, hooked into database.record()
```

Everything under `adobe_access/` is Streamlit-agnostic and unit-testable in isolation (see
`tests/`) except `ui/*.py` (obviously — that's the UI) and `diagnostics.py`, which imports
`streamlit` only to read `st.__version__` for the Diagnostics page, never calling any `st.*`
UI function itself. `adobe_access/ui/*.py` and `app.py` are exercised through Streamlit's
`AppTest` harness instead (`tests/test_app_widget_state.py`, `tests/test_security.py`) since
UI/session-state interaction bugs don't show up in plain unit tests.

`adobe_access/ui/` is deliberately not named `pages/` — Streamlit auto-discovers a literal
top-level `pages/` directory as a native multipage app with its own routing, which would
fight with this app's own sidebar-radio navigation (`shared.render_sidebar()` / the
`pending_navigation` session-state dance that lets any page redirect to another).

## Modes

### Mock mode

```env
MOCK_ADOBE=true
ADOBE_WRITE_ENABLED=false
```

Safe local testing with sample data.

### Live read and Adobe test mode

```env
MOCK_ADOBE=false
ADOBE_WRITE_ENABLED=false
ADOBE_ORG_ID=...
ADOBE_CLIENT_ID=...
ADOBE_CLIENT_SECRET=...
ADOBE_SCOPES=...
```

The app reads real users and user groups. Provision execution uses Adobe's `testOnly=true` action mode and cannot write.

### Live write mode

```env
MOCK_ADOBE=false
ADOBE_WRITE_ENABLED=true
```

Enable only after test-mode payloads succeed and Adobe administrators validate the technical account, claimed domain, identity type, country, and permitted user groups.

Once enabled, the Provision Wizard's Review step shows an **Execute** section below
"Run test": an explicit warning stating exactly how many users will be created and
group assignments added, a confirmation checkbox, and only then an Execute button.
`ADOBE_WRITE_ENABLED` is never exposed as a UI toggle — it can only be set in `.env`
— so Execute is structurally unreachable unless you've deliberately opted in on the
machine running the app. See "Production readiness checklist" below before relying
on this against a real tenant.

## Adobe Developer Console setup

Either live mode needs a Project in [Adobe Developer Console](https://developer.adobe.com/console)
with a **User Management API** service added and an **OAuth Server-to-Server** credential.
This app has only ever used that credential type — Adobe's older Service Account (JWT)
credentials are deprecated, and Developer Console won't offer them for a new integration
anyway.

**Prerequisite:** you need the **System Administrator** role in your Adobe organization to
create this integration. If you don't have it, this is the point to loop in whoever does.

1. Go to [developer.adobe.com/console](https://developer.adobe.com/console) and create a new
   Project (or open an existing one you want to reuse for this).
2. **Add to Project → API**, select **User Management API**, click Next.
3. Choose **OAuth Server-to-Server** as the credential type and give it a name.
4. **Assign Product Profiles** — Adobe requires selecting at least one to save the
   integration. This is Developer-Console-side scoping of what the technical account can
   touch and is separate from the custom user groups this app manages day to day; pick
   whatever your Adobe admin considers appropriate for a UMAPI integration.
5. Save. The credential page now shows your **Client ID**, **Client Secret**, and
   **Organization ID** (format `1234567890ABCDEF0A495E2D@AdobeOrg`) — these map directly
   to `.env`:

   ```env
   ADOBE_ORG_ID=<Organization ID from the credential page>
   ADOBE_CLIENT_ID=<Client ID>
   ADOBE_CLIENT_SECRET=<Client Secret>
   ADOBE_SCOPES=openid,AdobeID,user_management_sdk
   ```

   `ADOBE_SCOPES` above is this app's default (already in `.env.example`) and matches
   Adobe's own User Management API getting-started guide — you shouldn't need to change it.

6. **If API calls come back with a permission/403 error after this**, the Developer
   Console credential existing isn't the whole story — the technical account also needs
   adequate admin rights in **Admin Console** (Users → Administrators) over the groups
   you're targeting. Adobe's own setup docs explicitly don't cover this half either
   ("speak with your system administrator or Adobe sales representative") — if you hit
   this, that's genuinely who to loop in, not something to debug in this app's code.

Once you have real credentials, go through Live read/test mode before Live write mode —
don't skip straight to write mode.

Official references: [OAuth Server-to-Server implementation guide](https://developer.adobe.com/developer-console/docs/guides/authentication/ServerToServerAuthentication/implementation),
[Add User Management API to a project](https://developer.adobe.com/developer-console/docs/guides/services/services-add-api-oauth-s2-s),
[User Management API documentation](https://adobe-apiplatform.github.io/umapi-documentation/en/).

## Important configuration

```env
ALLOWED_EMAIL_DOMAINS=example.com
DEFAULT_COUNTRY=US
DEFAULT_IDENTITY_TYPE=federatedID
```

Supported identity values are `federatedID`, `enterpriseID`, and `adobeID`.

These non-secret values (plus cache TTL and auto-validation) can also be changed
from the in-app **Settings** page without editing `.env` or restarting — see
"Editable settings vs. secrets" below. Adobe credentials and `ADOBE_WRITE_ENABLED`
are never editable from the UI.

`.env.example` only lists variables that map to a field in `adobe_access/config.py`'s
`Settings` class — anything else in a `.env` file is silently ignored
(`extra="ignore"`). Two worth knowing about: `CACHE_TTL_SECONDS` (seconds, not
minutes) and `ADOBE_HTTP_TIMEOUT` (seconds per Adobe HTTP call — raise it if a
corporate proxy is slow).

## Pages

- **Dashboard** — connection/sync/request health at a glance, quick actions,
  recent requests and activity, favorite groups, most-used templates. Never
  calls Adobe itself.
- **Provision access** — the 4-step wizard (Users → Validate → Access → Review). Review
  always offers "Run test" (Adobe `testOnly=true`, never writes). When
  `ADOBE_WRITE_ENABLED=true`, it also offers **Execute** — gated by a confirmation
  dialog, idempotent, retried with backoff on transient failures, and fully logged
  (see "Production readiness checklist").
- **User search** — two tabs. "Search Adobe" does a live, exact-email lookup (always
  current). "Browse synced users" searches a local directory cache instead — including a
  blank search to list everyone synced — populated by its own "Sync users from Adobe"
  action (mirrors how User groups syncs, fetching every page Adobe has, no cap).
  Each cached user's custom-group count is computed against the *current* group cache,
  so it stays accurate even without re-syncing users after a group sync.
- **Templates**, **User groups**, **Compare users**, **Copy access** — as before.
- **Request history** — every preview built in the wizard is saved as a request.
  Search/filter, open the full detail, re-open it in the wizard ("Reuse"), or
  export it. The Dashboard's "Recent requests" widget is a shortcut into this page.
- **Audit history** — the low-level action log (every button click that changes
  state), separate from Request history's higher-level "what was previewed" view.
- **Diagnostics** — for administrators: Adobe connection status (manual check,
  never automatic), cache size, SQLite integrity, environment info, app version,
  and a downloadable JSON diagnostics bundle. A rotating log file is written to
  `logs/access-manager.log` and mirrored into that bundle.
- **Settings** — editable non-secret settings (see below) plus a manual
  "Test Adobe connection" button.

## Editable settings vs. secrets

`.env` still defines every default. The Settings page can override five
non-secret, operational fields at runtime (stored in SQLite, not `.env`):
allowed email domains, default country, default identity type, cache TTL, and
whether Adobe validation in the wizard runs automatically. "Reset to .env
defaults" clears all overrides.

Adobe credentials (`ADOBE_ORG_ID`, `ADOBE_CLIENT_ID`, `ADOBE_CLIENT_SECRET`,
`ADOBE_SCOPES`) and `ADOBE_WRITE_ENABLED` are **not** part of this override
system and can only be changed in `.env` — this is deliberate, so write mode
can never be flipped on from the UI.

## Production readiness checklist

Before flipping `ADOBE_WRITE_ENABLED=true` against a real tenant:

| Item | Status | Where |
|---|---|---|
| Feature flag controls all write operations | ✅ | `ADOBE_WRITE_ENABLED` gates both the UI (Execute section only renders when enabled) and the client (`AdobeUMAPIClient.provision()` raises if a non-test call is attempted while disabled) — two independent checks, never a UI toggle. |
| Execute requires an explicit confirmation dialog | ✅ | Review step shows exact counts ("Create N users, add M group assignments") plus a required checkbox before Execute is clickable. |
| Every request receives a unique Request ID | ✅ | `recent_requests.id` per preview; each Execute additionally gets its own `executions.id`. |
| Each Adobe API operation is logged with timestamp and outcome | ✅ | Every user's outcome is written to `audit_events` (visible on Audit history) and mirrored to `logs/access-manager.log`; each Execute run's start/end/duration is stored in `executions`. |
| Partial failures are reported without stopping unrelated users | ✅ | `execute()` continues past a failed user; the run is marked `Partial` (vs. `Succeeded`/`Failed`) and every row's outcome is shown individually. |
| Retries are limited and exponential for transient failures | ✅ | `retry.call_with_retry()` — timeout/429/5xx/connection errors get up to 3 attempts with capped exponential backoff; invalid email/permission-denied/config errors never retry. |
| Requests can be rerun safely because operations are idempotent | ✅ | Verified in testing: re-running the same request twice creates no duplicate users or assignments — the second run reports everything as "already assigned". |
| Secrets remain only in `.env` and are never exposed in the UI or logs | ✅ | Settings/Diagnostics only ever display `adobe_org_id` and the UMAPI base URL — never `client_id`/`client_secret`/`scopes`; audit/log entries never include the Authorization header or credential values. |

This table reflects what the code does today, not a promise — re-verify against your
own Adobe tenant's behavior (rate limits, error shapes, group-name validation) during
the mock/test-mode soak period before trusting Execute in production.

## Corporate network

The HTTP client honors `HTTPS_PROXY`, `HTTP_PROXY`, and `NO_PROXY`. If Adobe calls fail while browser access works, confirm VPN/proxy/certificate requirements with IT.

## Tests

```
python -m pytest
```

Runs entirely against the mock Adobe client and temp SQLite files — no network access or
real credentials needed, and it's fast (a few seconds). See [CONTRIBUTING.md](CONTRIBUTING.md)
for full setup. CI (`.github/workflows/tests.yml`) runs this on every push/PR across Python
3.11 and 3.12.

## Safety behavior

- Only configured email domains are accepted.
- New-user emails in Provision access must match `firstname.lastname@domain` — exactly two
  letter-only parts separated by one dot; anything else (a single word, digits, underscores,
  hyphens, or extra parts) is marked Invalid and excluded from the batch. This is the org's
  account-naming convention, not general email syntax — it doesn't apply to User search,
  Compare users, or Copy access, which look up existing addresses of any shape.
- Existing users are reused.
- Existing memberships are skipped.
- Catalog displays user groups and excludes known product-profile/admin records.
- Dashboard does not call Adobe — Adobe is only contacted by Sync, Search/Compare/Copy
  lookups, Preview, and the manual "Test Adobe connection" / "Check Adobe connection now"
  buttons on Settings and Diagnostics.
- Live writes require an explicit environment switch (`ADOBE_WRITE_ENABLED`), which is
  never exposed as an editable setting in the UI.
- Audit records are stored locally in `access_manager.db`; a rotating log file at
  `logs/access-manager.log` mirrors the same events for the Diagnostics download.

## Group/membership classification

`is_user_group()` only trusts an explicit "user-group" type from Adobe — a bare
`type: "group"` or a missing type is excluded rather than guessed from the group
name. This fails closed deliberately: under-caching a group is a UI inconvenience
(sync again), but silently caching a product profile or admin group as if it were
a safe custom user group would be a real safety problem. If your tenant's Adobe
groups endpoint uses a type value not in `client._group_type`'s recognized set,
groups sync will exclude them — check Diagnostics/User groups after a sync against
what you expect from Adobe, and widen the recognized set in `client.py` if needed.

`membership_table()` reports how many of a user's real Adobe memberships were
excluded for not being in the local custom-group cache via
`result.attrs["ignored_non_custom_memberships"]`, surfaced as a caption on the
User search page.

## Contributing

Bug reports and PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

Found a vulnerability? See [SECURITY.md](SECURITY.md) for how to report it privately
rather than opening a public issue.

## License

[MIT](LICENSE)
