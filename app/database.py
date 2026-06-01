"""
SQLite データベース管理モジュール
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
        CREATE TABLE IF NOT EXISTS offices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_type TEXT NOT NULL,
            office_no TEXT,
            office_name TEXT,
            corp_name TEXT,
            pref_name TEXT,
            city_name TEXT,
            address TEXT,
            phone TEXT,
            url TEXT,
            work_type TEXT,
            capacity TEXT,
            lat TEXT,
            lng TEXT,
            hours_weekday TEXT,
            closed_days TEXT,
            updated_at TEXT,
            raw_json TEXT,
            fetched_ym TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS update_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_ym TEXT,
            a_count INTEGER,
            b_count INTEGER,
            status TEXT,
            message TEXT,
            run_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_service_type ON offices(service_type);
        CREATE INDEX IF NOT EXISTS idx_city ON offices(city_name);
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


def upsert_offices(df, fetched_ym: str):
    """DataFrameをDBに保存（全件置き換え）"""
    import pandas as pd

    with get_conn() as conn:
        # 全件削除してから挿入（二重挿入防止）
        conn.execute("DELETE FROM offices")

        rows_to_insert = []
        for _, row in df.iterrows():
            raw = row.to_dict()
            rows_to_insert.append((
                row.get("service_type", ""),
                row.get("office_no", ""),
                row.get("office_name", ""),
                row.get("corp_name", ""),
                row.get("pref_name", ""),
                row.get("city_name", ""),
                row.get("address", ""),
                row.get("phone", ""),
                row.get("url", ""),
                row.get("work_type", ""),
                row.get("capacity", ""),
                row.get("lat", ""),
                row.get("lng", ""),
                row.get("hours_weekday", ""),
                row.get("closed_days", ""),
                row.get("updated_at", ""),
                json.dumps({k: v for k, v in raw.items() if not str(k).startswith("Unnamed")}, ensure_ascii=False),
                fetched_ym,
            ))

        conn.executemany("""
            INSERT INTO offices
                (service_type, office_no, office_name, corp_name,
                 pref_name, city_name, address, phone, url,
                 work_type, capacity, lat, lng, hours_weekday, closed_days,
                 updated_at, raw_json, fetched_ym)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows_to_insert)

    return len(rows_to_insert)


def log_update(fetched_ym: str, a_count: int, b_count: int, status: str, message: str = ""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO update_log (fetched_ym, a_count, b_count, status, message)
            VALUES (?,?,?,?,?)
        """, (fetched_ym, a_count, b_count, status, message))


def get_offices(service_type: Optional[str] = None):
    with get_conn() as conn:
        if service_type:
            rows = conn.execute(
                "SELECT * FROM offices WHERE service_type=? ORDER BY office_name", (service_type,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM offices ORDER BY service_type, office_name"
            ).fetchall()
    return [dict(r) for r in rows]


def get_stats():
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM offices").fetchone()[0]
        a_count = conn.execute("SELECT COUNT(*) FROM offices WHERE service_type='A型'").fetchone()[0]
        b_count = conn.execute("SELECT COUNT(*) FROM offices WHERE service_type='B型'").fetchone()[0]

        def cap_stats(stype):
            rows = conn.execute(
                "SELECT CAST(capacity AS INTEGER) FROM offices WHERE service_type=? AND capacity NOT IN ('', 'None', 'nan') AND capacity IS NOT NULL",
                (stype,)
            ).fetchall()
            vals = [r[0] for r in rows if r[0] is not None]
            return {
                "total_capacity": sum(vals),
                "avg_capacity": round(sum(vals) / len(vals), 1) if vals else 0,
                "no_capacity": (a_count if stype == "A型" else b_count) - len(vals),
            }

        a_cap = cap_stats("A型")
        b_cap = cap_stats("B型")

        last_update = conn.execute(
            "SELECT fetched_ym, run_at FROM update_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        fetched_ym = last_update["fetched_ym"] if last_update else None
        last_run = last_update["run_at"] if last_update else None

    return {
        "total": total,
        "a_count": a_count,
        "b_count": b_count,
        "a_total_capacity": a_cap["total_capacity"],
        "b_total_capacity": b_cap["total_capacity"],
        "a_avg_capacity": a_cap["avg_capacity"],
        "b_avg_capacity": b_cap["avg_capacity"],
        "total_capacity": a_cap["total_capacity"] + b_cap["total_capacity"],
        "fetched_ym": fetched_ym,
        "last_run": last_run,
    }


def get_work_type_stats(service_type: Optional[str] = None):
    """業種別の集計"""
    offices = get_offices(service_type)
    counts = {}
    for o in offices:
        wt = o.get("work_type") or "不明"
        # 複数業種が改行やスラッシュで区切られている場合は最初のものを使う
        wt = wt.split("\n")[0].split("、")[0].split("/")[0].strip()
        if len(wt) > 30:
            wt = wt[:30] + "…"
        if not wt or wt == "nan":
            wt = "情報なし"
        counts[wt] = counts.get(wt, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1])


def get_update_log(limit: int = 10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM update_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
