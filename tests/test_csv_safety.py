from __future__ import annotations

import pandas as pd

from adobe_access.utils import safe_csv, sanitize_csv_cell


def test_sanitize_csv_cell_neutralizes_formula_trigger_characters():
    assert sanitize_csv_cell("=cmd|'/c calc'!A0") == "'=cmd|'/c calc'!A0"
    assert sanitize_csv_cell("+1+1") == "'+1+1"
    assert sanitize_csv_cell("-2+3") == "'-2+3"
    assert sanitize_csv_cell("@SUM(A1:A9)") == "'@SUM(A1:A9)"


def test_sanitize_csv_cell_leaves_ordinary_text_and_non_strings_alone():
    assert sanitize_csv_cell("CJA Analyst") == "CJA Analyst"
    assert sanitize_csv_cell("") == ""
    assert sanitize_csv_cell(None) is None
    assert sanitize_csv_cell(42) == 42
    assert sanitize_csv_cell(True) is True


def test_safe_csv_sanitizes_every_text_column_but_not_numeric_columns():
    df = pd.DataFrame([
        {"name": "=HYPERLINK(\"http://evil\")", "count": 3, "email": "a@example.com"},
        {"name": "Normal Template", "count": 5, "email": "=cmd"},
    ])
    output = safe_csv(df)
    assert "'=HYPERLINK" in output
    assert "'=cmd" in output
    assert "Normal Template" in output
    assert ",3," in output or ",3\n" in output or ",3\r\n" in output  # numeric untouched


def test_safe_csv_matches_plain_to_csv_when_nothing_dangerous():
    df = pd.DataFrame([{"a": "hello", "b": 1}])
    assert safe_csv(df) == df.to_csv(index=False)
