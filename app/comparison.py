"""
隣接市との就労継続支援事業所比較モジュール
WAM NET オープンデータから市川市周辺の市区町村を集計
"""

import io
import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import chardet
import pandas as pd
import requests

WAMNET_BASE = "https://www.wam.go.jp/content/files/pcpub/top/sfkopendata"
FILE_CODES = {"A型": 45, "B型": 46}
COMPARISON_CITIES = ["市川市", "松戸市", "船橋市", "浦安市", "鎌ケ谷市", "八千代市", "江戸川区"]
CACHE_FILE = Path(__file__).parent.parent / "data" / "city_comparison.json"

logger = logging.getLogger(__name__)


def _quarter_candidates() -> list:
    now = datetime.now()
    quarters = [3, 6, 9, 12]
    year, month = now.year, now.month
    candidates = []
    for _ in range(6):
        for q in reversed(quarters):
            if month >= q:
                candidates.append(f"{year}{q:02d}")
                break
        month -= 3
        if month <= 0:
            month += 12
            year -= 1
    return list(dict.fromkeys(candidates))


def _download_and_parse(ym: str, code: int) -> Optional[pd.DataFrame]:
    url = f"{WAMNET_BASE}/{ym}/sfkopendata_{ym}_{code}.zip"
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200:
            return None
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = [n for n in zf.namelist() if n.endswith(".csv")][0]
            raw = zf.read(csv_name)
        enc = chardet.detect(raw).get("encoding") or "cp932"
        df = pd.read_csv(io.StringIO(raw.decode(enc, errors="replace")), dtype=str)
        return df
    except Exception as e:
        logger.warning(f"Error: {url} - {e}")
        return None


def fetch_city_comparison() -> List[Dict]:
    """全比較都市のA型/B型統計をWAM NETから取得"""
    all_data: Dict[str, Dict] = {}

    for service_type, code in FILE_CODES.items():
        df = None
        for ym in _quarter_candidates():
            df = _download_and_parse(ym, code)
            if df is not None:
                logger.info(f"{service_type} {ym}: {len(df)}件取得")
                break
        if df is None:
            continue

        addr_col = next(
            (c for c in df.columns if "事業所住所" in c and "市区町村" in c), None
        )
        if not addr_col:
            addr_col = next((c for c in df.columns if "市区町村" in c), None)
        if not addr_col:
            continue

        cap_col = next((c for c in df.columns if "定員" in c), None)

        for city in COMPARISON_CITIES:
            sub = df[df[addr_col].str.contains(city, na=False)]
            count = len(sub)
            if cap_col and count > 0:
                total_cap = pd.to_numeric(sub[cap_col], errors="coerce").sum()
                avg_cap = round(total_cap / count, 1)
            else:
                total_cap, avg_cap = 0, 0.0

            if city not in all_data:
                all_data[city] = {}
            all_data[city][service_type] = {
                "count": count,
                "total_capacity": int(total_cap),
                "avg_capacity": avg_cap,
            }

    result = []
    for city in COMPARISON_CITIES:
        a = all_data.get(city, {}).get("A型", {"count": 0, "total_capacity": 0, "avg_capacity": 0.0})
        b = all_data.get(city, {}).get("B型", {"count": 0, "total_capacity": 0, "avg_capacity": 0.0})
        result.append({
            "city": city,
            "a_count": a["count"],
            "a_total_capacity": a["total_capacity"],
            "a_avg_capacity": a["avg_capacity"],
            "b_count": b["count"],
            "b_total_capacity": b["total_capacity"],
            "b_avg_capacity": b["avg_capacity"],
        })

    # キャッシュ保存
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"updated_at": datetime.now().isoformat(), "data": result},
            f,
            ensure_ascii=False,
        )

    return result


def get_city_comparison() -> List[Dict]:
    """キャッシュから都市比較データを返す（なければ空リスト）"""
    if CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            cached = json.load(f)
        return cached.get("data", [])
    return []
