from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "access_manager.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize() -> None:
    with _connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          action TEXT NOT NULL,
          email TEXT,
          groups_json TEXT,
          status TEXT NOT NULL,
          details TEXT
        )""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(managed_groups)").fetchall()}
        expected = {
            "adobe_group_name", "display_name", "system", "description",
            "privileged", "member_count", "synced_at",
        }
        if columns and columns != {"id", *expected}:
            conn.execute("DROP TABLE managed_groups")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS managed_groups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          adobe_group_name TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL,
          system TEXT NOT NULL DEFAULT 'Other',
          description TEXT NOT NULL DEFAULT '',
          privileged INTEGER NOT NULL DEFAULT 0,
          member_count INTEGER,
          synced_at TEXT NOT NULL
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS templates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          description TEXT NOT NULL DEFAULT '',
          system TEXT NOT NULL DEFAULT 'Other',
          created_by TEXT NOT NULL DEFAULT '',
          updated_by TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS template_groups (
          template_id INTEGER NOT NULL,
          adobe_group_name TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(template_id, adobe_group_name),
          FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE CASCADE
        )""")

        # One-time migration from the prototype JSON template table.
        legacy_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='access_templates'"
        ).fetchone()
        migrated = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        if legacy_exists and not migrated:
            legacy_rows = conn.execute(
                "SELECT name,description,groups_json,created_at,updated_at FROM access_templates"
            ).fetchall()
            for row in legacy_rows:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO templates(name,description,system,created_by,updated_by,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (row[0], row[1] or '', 'Other', 'migration', 'migration', row[3], row[4]),
                )
                template_id = cursor.lastrowid
                if template_id:
                    for index, group in enumerate(json.loads(row[2] or '[]')):
                        conn.execute(
                            "INSERT OR IGNORE INTO template_groups(template_id,adobe_group_name,sort_order) VALUES(?,?,?)",
                            (template_id, str(group), index),
                        )
        conn.commit()

def record(actor: str, action: str, email: str, groups: list[str], status: str, details: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_events(created_at,actor,action,email,groups_json,status,details) VALUES(?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), actor, action, email, json.dumps(groups), status, details),
        )
        conn.commit()


def read(limit: int = 500) -> pd.DataFrame:
    with _connect() as conn:
        return pd.read_sql_query(
            "SELECT created_at,actor,action,email,groups_json,status,details FROM audit_events ORDER BY id DESC LIMIT ?",
            conn,
            params=(limit,),
        )


def audit_summary() -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN substr(created_at,1,10)=? THEN 1 ELSE 0 END) today,
                      SUM(CASE WHEN lower(status) LIKE '%fail%' THEN 1 ELSE 0 END) failed
               FROM audit_events""",
            (today,),
        ).fetchone()
    return {"total": int(row["total"] or 0), "today": int(row["today"] or 0), "failed": int(row["failed"] or 0)}


def replace_managed_groups(groups: list[dict[str, Any]]) -> dict[str, int | str]:
    synced_at = datetime.now(timezone.utc).isoformat()
    rows: list[tuple[Any, ...]] = []
    seen: set[str] = set()
    for group in groups:
        name = str(group.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append((
            name,
            str(group.get("display_name") or name).strip() or name,
            str(group.get("system") or "Other").strip() or "Other",
            str(group.get("description") or "").strip(),
            1 if bool(group.get("privileged")) else 0,
            group.get("member_count"),
            synced_at,
        ))
    with _connect() as conn:
        conn.execute("DELETE FROM managed_groups")
        conn.executemany(
            """INSERT INTO managed_groups(adobe_group_name,display_name,system,description,privileged,member_count,synced_at)
               VALUES(?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
    return {"groups": len(rows), "synced_at": synced_at}


def read_managed_groups() -> pd.DataFrame:
    with _connect() as conn:
        df = pd.read_sql_query(
            """SELECT adobe_group_name,display_name,system,description,privileged,member_count,synced_at
               FROM managed_groups ORDER BY system,display_name,adobe_group_name""",
            conn,
        )
    if "privileged" in df.columns:
        df["privileged"] = df["privileged"].astype(bool)
    return df


def catalog_status() -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) group_count, MAX(synced_at) synced_at FROM managed_groups").fetchone()
    return {"group_count": int(row["group_count"] or 0), "synced_at": row["synced_at"]}



def _template_groups(conn: sqlite3.Connection, template_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT adobe_group_name FROM template_groups WHERE template_id=? ORDER BY sort_order, adobe_group_name",
        (template_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def list_template_records() -> pd.DataFrame:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id,name,description,system,created_by,updated_by,created_at,updated_at
               FROM templates ORDER BY name COLLATE NOCASE"""
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["groups"] = _template_groups(conn, int(row["id"]))
            item["group_count"] = len(item["groups"])
            result.append(item)
    return pd.DataFrame(result)


def get_template_record(template_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """SELECT id,name,description,system,created_by,updated_by,created_at,updated_at
               FROM templates WHERE id=?""",
            (template_id,),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["groups"] = _template_groups(conn, template_id)
        item["group_count"] = len(item["groups"])
        return item


def create_template_record(
    name: str, description: str, system: str, groups: list[str], actor: str
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as conn:
            cursor = conn.execute(
                """INSERT INTO templates(name,description,system,created_by,updated_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (name, description, system, actor, actor, now, now),
            )
            template_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO template_groups(template_id,adobe_group_name,sort_order) VALUES(?,?,?)",
                [(template_id, group, index) for index, group in enumerate(groups)],
            )
            conn.commit()
            return template_id
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"A template named '{name}' already exists.") from exc


def update_template_record(
    template_id: int, name: str, description: str, system: str, groups: list[str], actor: str
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as conn:
            updated = conn.execute(
                """UPDATE templates SET name=?,description=?,system=?,updated_by=?,updated_at=?
                   WHERE id=?""",
                (name, description, system, actor, now, template_id),
            )
            if updated.rowcount == 0:
                raise ValueError("Template was not found.")
            conn.execute("DELETE FROM template_groups WHERE template_id=?", (template_id,))
            conn.executemany(
                "INSERT INTO template_groups(template_id,adobe_group_name,sort_order) VALUES(?,?,?)",
                [(template_id, group, index) for index, group in enumerate(groups)],
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"A template named '{name}' already exists.") from exc


def duplicate_template_record(template_id: int, new_name: str, actor: str) -> int:
    source = get_template_record(template_id)
    if not source:
        raise ValueError("Template was not found.")
    return create_template_record(
        name=new_name,
        description=source["description"],
        system=source["system"],
        groups=source["groups"],
        actor=actor,
    )


def delete_template_record(template_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM template_groups WHERE template_id=?", (template_id,))
        deleted = conn.execute("DELETE FROM templates WHERE id=?", (template_id,))
        if deleted.rowcount == 0:
            raise ValueError("Template was not found.")
        conn.commit()


def _ensure_workflow_tables() -> None:
    with _connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS favorite_groups (
          actor TEXT NOT NULL,
          adobe_group_name TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(actor, adobe_group_name)
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS recent_requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          template_id INTEGER,
          template_name TEXT NOT NULL DEFAULT '',
          users_json TEXT NOT NULL,
          groups_json TEXT NOT NULL,
          status TEXT NOT NULL,
          summary_json TEXT NOT NULL DEFAULT '{}'
        )""")
        conn.commit()


def list_favorite_groups(actor: str) -> list[str]:
    _ensure_workflow_tables()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT adobe_group_name FROM favorite_groups WHERE actor=? ORDER BY adobe_group_name COLLATE NOCASE",
            (actor,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def replace_favorite_groups(actor: str, groups: list[str]) -> None:
    _ensure_workflow_tables()
    now = datetime.now(timezone.utc).isoformat()
    clean = list(dict.fromkeys(str(group).strip() for group in groups if str(group).strip()))
    with _connect() as conn:
        conn.execute("DELETE FROM favorite_groups WHERE actor=?", (actor,))
        conn.executemany(
            "INSERT INTO favorite_groups(actor,adobe_group_name,created_at) VALUES(?,?,?)",
            [(actor, group, now) for group in clean],
        )
        conn.commit()


def save_recent_request(
    actor: str,
    users: list[dict[str, Any]],
    groups: list[str],
    status: str,
    summary: dict[str, Any],
    template_id: int | None = None,
    template_name: str = "",
) -> int:
    _ensure_workflow_tables()
    with _connect() as conn:
        cursor = conn.execute(
            """INSERT INTO recent_requests(
                   created_at,actor,template_id,template_name,users_json,groups_json,status,summary_json
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(), actor, template_id, template_name,
                json.dumps(users), json.dumps(groups), status, json.dumps(summary),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_recent_requests(limit: int = 20) -> pd.DataFrame:
    _ensure_workflow_tables()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id,created_at,actor,template_id,template_name,users_json,groups_json,status,summary_json
               FROM recent_requests ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        users = json.loads(item.pop("users_json") or "[]")
        groups = json.loads(item.pop("groups_json") or "[]")
        summary = json.loads(item.pop("summary_json") or "{}")
        item["users"] = users
        item["groups"] = groups
        item["user_count"] = len(users)
        item["group_count"] = len(groups)
        item.update({f"summary_{key}": value for key, value in summary.items()})
        result.append(item)
    return pd.DataFrame(result)


def get_recent_request(request_id: int) -> dict[str, Any] | None:
    _ensure_workflow_tables()
    with _connect() as conn:
        row = conn.execute(
            """SELECT id,created_at,actor,template_id,template_name,users_json,groups_json,status,summary_json
               FROM recent_requests WHERE id=?""",
            (request_id,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["users"] = json.loads(item.pop("users_json") or "[]")
    item["groups"] = json.loads(item.pop("groups_json") or "[]")
    item["summary"] = json.loads(item.pop("summary_json") or "{}")
    return item


def workflow_summary(actor: str | None = None) -> dict[str, Any]:
    _ensure_workflow_tables()
    today = datetime.now(timezone.utc).date().isoformat()
    with _connect() as conn:
        request_row = conn.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN substr(created_at,1,10)=? THEN 1 ELSE 0 END) today
               FROM recent_requests""",
            (today,),
        ).fetchone()
        favorite_count = 0
        if actor:
            favorite_count = int(conn.execute(
                "SELECT COUNT(*) FROM favorite_groups WHERE actor=?", (actor,)
            ).fetchone()[0] or 0)
        template_count = int(conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0] or 0)
    return {
        "requests_total": int(request_row["total"] or 0),
        "requests_today": int(request_row["today"] or 0),
        "favorite_count": favorite_count,
        "template_count": template_count,
    }
