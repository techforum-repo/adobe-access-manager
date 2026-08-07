from __future__ import annotations

"""Shared, autouse test fixtures.

`adobe_access.settings_store` caches DB-backed settings at module level (see
its docstring) — safe for the real app (one process, one DB), but multiple
test files each swap `database.DB_PATH` to their own temp DB via a local
`temp_db` fixture. Without resetting the cache between tests, a value read
in one test's temp DB could leak into the next test's, whichever `temp_db`
fixture is in play. Same class of pitfall as the MockAdobeClient singleton
noted elsewhere in this suite — reset explicitly rather than relying on
per-file fixtures to remember to do it.
"""

import pytest

from adobe_access import settings_store


@pytest.fixture(autouse=True)
def _reset_settings_store_cache():
    settings_store._invalidate_cache()
    yield
    settings_store._invalidate_cache()
