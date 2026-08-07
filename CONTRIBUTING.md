# Contributing

Thanks for considering a contribution. This is a small, focused tool — please keep changes
scoped and in the same spirit as the existing code.

## Getting started

```
git clone <your fork>
cd adobe-access-manager
python3 -m venv .venv && source .venv/bin/activate   # or start-unix.sh / start-windows.bat
pip install -r requirements-dev.txt   # runtime deps + pytest/pyflakes; use requirements.txt alone for a deploy
cp .env.example .env      # defaults to mock mode — no Adobe credentials needed
python -m pytest
streamlit run app.py
```

Mock mode (`MOCK_ADOBE=true`, the default) is enough for almost all development — the mock
Adobe client is a small in-memory stand-in in `adobe_access/client.py`. You won't need real
Adobe credentials unless you're specifically working on the live UMAPI client.

## Before opening a PR

- `python -m pytest` — the full suite should pass. It's fast (a few seconds) and runs entirely
  against mock data / temp SQLite files, no network access needed.
- If your change touches a `adobe_access/ui/*.py` page, prefer adding a Streamlit `AppTest`
  case (see `tests/test_app_widget_state.py`) over a manual click-through description — widget
  state bugs (stale selections, collapsed forms) are exactly the class of bug that only shows up
  under `AppTest`, not in a plain unit test of business logic.
- Business logic (`provisioning.py`, `client.py`, `users.py`, `templates.py`, `errors.py`,
  `retry.py`) is intentionally Streamlit-free — keep it that way so it stays independently
  testable. UI-only concerns belong in `adobe_access/ui/*.py`.
- If you're changing anything under `adobe_access/database.py`'s schema, add a migration path
  the way `initialize()` already does for older installs (see the `access_templates` →
  `templates`/`template_groups` migration) — existing users' local `access_manager.db` shouldn't
  break on upgrade.
- Keep secrets out of it: Adobe credentials and `ADOBE_WRITE_ENABLED` are `.env`-only by design
  (see README's "Editable settings vs. secrets") — don't add a UI path that exposes or edits
  them.

## Reporting bugs

Include: which mode you were in (mock / live read / live write), the page, and steps to
reproduce. If it's a UI state bug (something shows the wrong data after a sequence of clicks),
that's usually the most useful class of bug report for this app — please be specific about the
click order.
