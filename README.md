# Adobe Access Manager 1.0

A Streamlit application for validating `@bsci.com` users, reading Adobe user groups, previewing idempotent access changes, and optionally creating Federated/Enterprise/Adobe ID users and assigning user groups through Adobe UMAPI.

## Windows quick start

1. Extract the ZIP.
2. Double-click `start-windows.bat`.
3. Open `http://localhost:8501` if the browser does not open automatically.

The first start creates `.venv`, installs packages, and copies `.env.example` to `.env`.

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

## Important configuration

```env
ALLOWED_EMAIL_DOMAINS=bsci.com
DEFAULT_COUNTRY=US
DEFAULT_IDENTITY_TYPE=federatedID
```

Supported identity values are `federatedID`, `enterpriseID`, and `adobeID`.

## Corporate network

The HTTP client honors `HTTPS_PROXY`, `HTTP_PROXY`, and `NO_PROXY`. If Adobe calls fail while browser access works, confirm VPN/proxy/certificate requirements with IT.

## Tests

```cmd
.venv\Scripts\activate
python -m pytest
```

## Safety behavior

- Only configured email domains are accepted.
- Existing users are reused.
- Existing memberships are skipped.
- Catalog displays user groups and excludes known product-profile/admin records.
- Dashboard does not call Adobe.
- Live writes require an explicit environment switch.
- Audit records are stored locally in `access_manager.db`.
