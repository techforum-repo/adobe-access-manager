from __future__ import annotations

import pandas as pd

from adobe_access.provisioning import preview_summary


def test_preview_summary_counts_assignments() -> None:
    df = pd.DataFrame([
        {
            "exists": True,
            "groups_to_add": "A; B",
            "already_assigned": "C",
            "lookup": "OK",
        },
        {
            "exists": False,
            "groups_to_add": "A",
            "already_assigned": "None",
            "lookup": "OK",
        },
    ])
    summary = preview_summary(df)
    assert summary == {
        "users": 2,
        "existing": 1,
        "new": 1,
        "assignments": 3,
        "already": 1,
        "failures": 0,
    }


def test_preview_summary_handles_failure() -> None:
    df = pd.DataFrame([
        {
            "exists": False,
            "groups_to_add": "Not evaluated",
            "already_assigned": "Not evaluated",
            "lookup": "network failure",
        }
    ])
    summary = preview_summary(df)
    assert summary["failures"] == 1
    assert summary["new"] == 0
