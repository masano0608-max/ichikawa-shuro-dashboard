"""
SQLite データベース管理モジュール（訪問看護）
"""

import json
import sqlite3
from typing import Optional
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "ichikawa.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS houmon_offices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            office_no TEXT,
            office_name TEXT,
            office_name_kana TEXT,
            corp_name TEXT,
            pref_name TEXT,
            city_name TEXT,
            address TEXT,
            phone TEXT,
            fax TEXT,
            url TEXT,
            category TEXT,
            capacity TEXT,
            lat TEXT,
            lng TEXT,
            available_days TEXT,
            available_days_note TEXT,
            kaigo_certified INTEGER,
            shogai_certified INTEGER,
            note TEXT,
            raw_json TEXT,
            fetched_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            memo TEXT DEFAULT '',
            is_favorite INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS houmon_update_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_count INTEGER,
            psych_count INTEGER,
            status TEXT,
            message TEXT,
            run_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_houmon_category ON houmon_offices(category);

        CREATE TABLE IF NOT EXISTS kv_store (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        """)


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── 訪問看護 ─────────────────────────────────────────


def upsert_houmon_offices(df, fetched_at: str):
    """訪問看護DataFrameをDBに保存（全件置き換え・メモ/お気に入りは保持）"""

    with get_conn() as conn:
        # メモ・お気に入りを退避
        saved = conn.execute(
            "SELECT office_no, memo, is_favorite FROM houmon_offices WHERE (memo != '' AND memo IS NOT NULL) OR is_favorite = 1"
        ).fetchall()
        user_data = {r["office_no"]: (r["memo"], r["is_favorite"]) for r in saved}

        conn.execute("DELETE FROM houmon_offices")

        rows_to_insert = []
        for _, row in df.iterrows():
            raw = row.to_dict()
            rows_to_insert.append((
                row.get("office_no", ""),
                row.get("office_name", ""),
                row.get("office_name_kana", ""),
                row.get("corp_name", ""),
                row.get("pref_name", ""),
                row.get("city_name", ""),
                row.get("address", ""),
                row.get("phone", ""),
                row.get("fax", ""),
                row.get("url", ""),
                row.get("category", "一般"),
                row.get("capacity", ""),
                row.get("lat", ""),
                row.get("lng", ""),
                row.get("available_days", ""),
                row.get("available_days_note", ""),
                int(row.get("kaigo_certified", 0) or 0),
                int(row.get("shogai_certified", 0) or 0),
                row.get("note", ""),
                json.dumps({k: v for k, v in raw.items() if not str(k).startswith("Unnamed")}, ensure_ascii=False),
                fetched_at,
            ))

        conn.executemany("""
            INSERT INTO houmon_offices
                (office_no, office_name, office_name_kana, corp_name,
                 pref_name, city_name, address, phone, fax, url,
                 category, capacity, lat, lng,
                 available_days, available_days_note,
                 kaigo_certified, shogai_certified, note,
                 raw_json, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows_to_insert)

        # メモ・お気に入りを復元
        for office_no, (memo, is_fav) in user_data.items():
            conn.execute(
                "UPDATE houmon_offices SET memo=?, is_favorite=? WHERE office_no=?",
                (memo, is_fav, office_no)
            )

    return len(rows_to_insert)


def log_houmon_update(total_count: int, psych_count: int, status: str, message: str = ""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO houmon_update_log (total_count, psych_count, status, message)
            VALUES (?,?,?,?)
        """, (total_count, psych_count, status, message))


def get_houmon_offices(category: Optional[str] = None, favorite_only: bool = False):
    with get_conn() as conn:
        conditions = []
        params = []
        if category:
            conditions.append("category=?")
            params.append(category)
        if favorite_only:
            conditions.append("is_favorite=1")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM houmon_offices {where} ORDER BY is_favorite DESC, office_name",
            params
        ).fetchall()
    return [dict(r) for r in rows]


def update_houmon_memo(office_id: int, memo: str):
    with get_conn() as conn:
        conn.execute("UPDATE houmon_offices SET memo=? WHERE id=?", (memo, office_id))


def update_houmon_favorite(office_id: int, is_favorite: bool):
    with get_conn() as conn:
        conn.execute("UPDATE houmon_offices SET is_favorite=? WHERE id=?", (1 if is_favorite else 0, office_id))


def get_houmon_stats():
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM houmon_offices").fetchone()[0]
        psych = conn.execute("SELECT COUNT(*) FROM houmon_offices WHERE category='精神科特化'").fetchone()[0]
        general = conn.execute("SELECT COUNT(*) FROM houmon_offices WHERE category='一般'").fetchone()[0]
        shogai = conn.execute("SELECT COUNT(*) FROM houmon_offices WHERE shogai_certified=1").fetchone()[0]

        # カテゴリ別集計
        cat_rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM houmon_offices GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        categories = [{"category": r["category"], "count": r["cnt"]} for r in cat_rows]

        last_update = conn.execute(
            "SELECT run_at FROM houmon_update_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_run = last_update["run_at"] if last_update else None

    return {
        "total": total,
        "psych_count": psych,
        "general_count": general,
        "shogai_count": shogai,
        "categories": categories,
        "last_run": last_run,
    }


def get_houmon_update_log(limit: int = 10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM houmon_update_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── KV Store（ガントチャート等） ─────────────────────

def kv_get(key: str):
    with get_conn() as conn:
        row = conn.execute("SELECT value, updated_at FROM kv_store WHERE key=?", (key,)).fetchone()
    if row:
        return {"value": row["value"], "updated_at": row["updated_at"]}
    return None


def kv_set(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO kv_store (key, value, updated_at) VALUES (?, ?, datetime('now','localtime')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value)
        )
