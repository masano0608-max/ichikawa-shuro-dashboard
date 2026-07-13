"""
SQLite データベース管理モジュール
"""

import json
import sqlite3
from typing import Optional
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.classify import classify

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
        """)
        # 既存DBへのカラム追加（マイグレーション）
        for col, definition in [
            ("memo", "TEXT DEFAULT ''"),
            ("is_favorite", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE offices ADD COLUMN {col} {definition}")
            except Exception:
                pass  # 既に存在する場合はスキップ

        # 既存レコードの業種を再分類（work_typeが空・その他のもの）
        rows = conn.execute(
            "SELECT id, office_name FROM offices WHERE work_type IS NULL OR work_type = '' OR work_type = 'その他・不明'"
        ).fetchall()
        if rows:
            for r in rows:
                cat = classify(r["office_name"] or "")
                conn.execute("UPDATE offices SET work_type=? WHERE id=?", (cat, r["id"]))


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
    """DataFrameをDBに保存（全件置き換え・メモ/お気に入りは保持）"""

    with get_conn() as conn:
        # メモ・お気に入りを退避（office_no + service_type をキーに）
        saved = conn.execute(
            "SELECT office_no, service_type, memo, is_favorite FROM offices WHERE (memo != '' AND memo IS NOT NULL) OR is_favorite = 1"
        ).fetchall()
        user_data = {(r["office_no"], r["service_type"]): (r["memo"], r["is_favorite"]) for r in saved}

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
                classify(row.get("office_name", "")),
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

        # メモ・お気に入りを復元
        for (office_no, service_type), (memo, is_fav) in user_data.items():
            conn.execute(
                "UPDATE offices SET memo=?, is_favorite=? WHERE office_no=? AND service_type=?",
                (memo, is_fav, office_no, service_type)
            )

    return len(rows_to_insert)


def log_update(fetched_ym: str, a_count: int, b_count: int, status: str, message: str = ""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO update_log (fetched_ym, a_count, b_count, status, message)
            VALUES (?,?,?,?,?)
        """, (fetched_ym, a_count, b_count, status, message))


def get_offices(service_type: Optional[str] = None, favorite_only: bool = False):
    with get_conn() as conn:
        conditions = []
        params = []
        if service_type:
            conditions.append("service_type=?")
            params.append(service_type)
        if favorite_only:
            conditions.append("is_favorite=1")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        # お気に入り→名前順
        rows = conn.execute(
            f"SELECT * FROM offices {where} ORDER BY is_favorite DESC, office_name",
            params
        ).fetchall()
    return [dict(r) for r in rows]


def update_memo(office_id: int, memo: str):
    with get_conn() as conn:
        conn.execute("UPDATE offices SET memo=? WHERE id=?", (memo, office_id))


def update_favorite(office_id: int, is_favorite: bool):
    with get_conn() as conn:
        conn.execute("UPDATE offices SET is_favorite=? WHERE id=?", (1 if is_favorite else 0, office_id))


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
