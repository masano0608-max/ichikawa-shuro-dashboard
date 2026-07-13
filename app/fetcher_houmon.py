"""
厚生労働省 介護サービス情報公表 オープンデータから
訪問看護ステーション（サービスコード130）を取得するモジュール
"""

import io
import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

from app.classify_houmon import classify_houmon

logger = logging.getLogger(__name__)

HOUMON_CSV_URL = "https://www.mhlw.go.jp/content/12300000/jigyosho_130.csv"
TARGET_CITY = "千葉市"
TARGET_PREF = "千葉県"

COMPARISON_CITIES = ["千葉市", "市原市", "船橋市", "習志野市", "市川市", "四街道市", "佐倉市"]

# 政令指定都市（区レベルでデータが登録されるため contains でマッチ）
DESIGNATED_CITIES = ["千葉市", "横浜市", "川崎市", "さいたま市", "相模原市"]


def _download_csv():
    # type: () -> Optional[pd.DataFrame]
    """全国CSV（UTF-8）をダウンロードしてDataFrameで返す"""
    try:
        resp = requests.get(HOUMON_CSV_URL, timeout=120)
        resp.raise_for_status()
        # UTF-8 with BOM の可能性
        text = resp.content.decode("utf-8-sig")
        df = pd.read_csv(io.StringIO(text), dtype=str)
        logger.info(f"訪問看護CSV取得: {len(df)}件（全国）")
        return df
    except Exception as e:
        logger.error(f"訪問看護CSVダウンロードエラー: {e}")
        return None


def _filter_by_city(df, city):
    # type: (pd.DataFrame, str) -> pd.DataFrame
    """市区町村名でフィルタ（政令指定都市は前方一致）"""
    col = "市区町村名"
    if col not in df.columns:
        # フォールバック: 住所列から検索
        addr_col = "住所"
        if addr_col in df.columns:
            mask = df[addr_col].str.contains(city, na=False)
            return df[mask].copy()
        return pd.DataFrame()
    # 政令指定都市の場合は前方一致（例: "千葉市" → "千葉市中央区" 等にマッチ）
    if city in DESIGNATED_CITIES:
        mask = df[col].str.contains(city, na=False)
    else:
        mask = df[col].str.strip() == city
    return df[mask].copy()


def _normalize_columns(df):
    # type: (pd.DataFrame) -> pd.DataFrame
    """CSV列名を内部名にリネーム"""
    rename = {
        "事業所番号": "office_no",
        "事業所名": "office_name",
        "事業所名カナ": "office_name_kana",
        "法人の名称": "corp_name",
        "法人番号": "corp_no",
        "都道府県名": "pref_name",
        "市区町村名": "city_name",
        "住所": "address",
        "方書（ビル名等）": "address2",
        "電話番号": "phone",
        "FAX番号": "fax",
        "URL": "url",
        "緯度": "lat",
        "経度": "lng",
        "定員": "capacity",
        "利用可能曜日": "available_days",
        "利用可能曜日特記事項": "available_days_note",
        "介護保険の通常の指定基準を満たしている": "kaigo_certified",
        "障害福祉の通常の指定基準を満たしている": "shogai_certified",
        "備考": "note",
    }
    existing = {k: v for k, v in rename.items() if k in df.columns}
    df = df.rename(columns=existing)

    # 住所結合
    if "address" in df.columns and "address2" in df.columns:
        df["address"] = df["address"].fillna("") + df["address2"].fillna("")

    # カテゴリ分類
    df["category"] = df.apply(
        lambda row: classify_houmon(
            row.get("office_name", ""),
            row.get("corp_name", ""),
            row.get("note", ""),
        ),
        axis=1,
    )

    # 介護保険/障害福祉基準を整数フラグに
    for col in ["kaigo_certified", "shogai_certified"]:
        if col in df.columns:
            df[col] = df[col].apply(lambda v: 1 if str(v).strip() in ("1", "○", "有", "あり") else 0)
        else:
            df[col] = 0

    return df


def fetch_houmon_data():
    # type: () -> Tuple[pd.DataFrame, str]
    """千葉市の訪問看護ステーションデータを取得して返す"""
    df = _download_csv()
    if df is None or df.empty:
        return pd.DataFrame(), ""

    filtered = _filter_by_city(df, TARGET_CITY)
    if filtered.empty:
        logger.warning(f"訪問看護: {TARGET_CITY}のデータが見つかりません")
        return pd.DataFrame(), ""

    result = _normalize_columns(filtered)
    logger.info(f"訪問看護 {TARGET_CITY}: {len(result)}件")
    return result, "latest"


def fetch_houmon_comparison():
    # type: () -> List[Dict]
    """近隣市の訪問看護ステーション数を集計"""
    df = _download_csv()
    if df is None or df.empty:
        return []

    results = []
    for city in COMPARISON_CITIES:
        sub = _filter_by_city(df, city)
        sub = _normalize_columns(sub) if not sub.empty else sub
        total = len(sub)
        psych = len(sub[sub["category"] == "精神科特化"]) if not sub.empty and "category" in sub.columns else 0
        shogai = int(sub["shogai_certified"].sum()) if not sub.empty and "shogai_certified" in sub.columns else 0
        results.append({
            "city": city,
            "total": total,
            "psych_count": psych,
            "shogai_count": shogai,
        })

    return results
