"""
WAM NET オープンデータ取得・パーサーモジュール
就労継続支援A型(ファイル45)・B型(ファイル46)のZIPをダウンロードして市川市データを抽出する
"""

import io
import logging
import zipfile
from datetime import datetime
from typing import List, Optional, Tuple

import chardet
import pandas as pd
import requests

logger = logging.getLogger(__name__)

WAMNET_BASE = "https://www.wam.go.jp/content/files/pcpub/top/sfkopendata"

# ファイル番号: A型=45, B型=46
FILE_CODES = {
    "A型": 45,
    "B型": 46,
}

TARGET_CITY = "市川市"
TARGET_PREF = "千葉県"

# WAM NET 個別ページから業種を補完するためのURL
WAMNET_DETAIL_BASE = "https://www.wam.go.jp/sfkohyoout/COP010100E0000.do"


def _latest_quarter() -> str:
    """直近の公開済み四半期を返す (例: '202603')"""
    now = datetime.now()
    quarters = [3, 6, 9, 12]
    year = now.year
    month = now.month
    for q_month in reversed(quarters):
        if month >= q_month:
            return f"{year}{q_month:02d}"
    return f"{year - 1}12"


def _quarter_candidates(yyyymm: Optional[str] = None) -> List[str]:
    """試行する年月リストを返す（最新から過去6期分）"""
    if yyyymm:
        return [yyyymm]
    base = _latest_quarter()
    year = int(base[:4])
    month = int(base[4:])
    candidates = []
    for _ in range(6):
        candidates.append(f"{year}{month:02d}")
        month -= 3
        if month <= 0:
            month += 12
            year -= 1
    return candidates


def _download_zip(year_month: str, file_code: int) -> Optional[bytes]:
    url = f"{WAMNET_BASE}/{year_month}/sfkopendata_{year_month}_{file_code}.zip"
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            logger.info(f"Downloaded: {url}")
            return resp.content
        logger.debug(f"Not found ({resp.status_code}): {url}")
    except Exception as e:
        logger.warning(f"Error downloading {url}: {e}")
    return None


def _parse_zip(data: bytes, service_type: str) -> pd.DataFrame:
    """ZIPを解凍しCSVを読み込んでDataFrameを返す"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            return pd.DataFrame()
        raw = zf.read(csv_names[0])

    detected = chardet.detect(raw)
    enc = detected.get("encoding") or "utf-8-sig"
    try:
        text = raw.decode(enc)
    except Exception:
        text = raw.decode("cp932", errors="replace")

    df = pd.read_csv(io.StringIO(text), dtype=str)
    df["service_type"] = service_type
    return df


def _filter_ichikawa(df: pd.DataFrame) -> pd.DataFrame:
    """市川市の事業所だけに絞る"""
    addr_col = "事業所住所（市区町村）"
    if addr_col in df.columns:
        mask = df[addr_col].str.contains(TARGET_CITY, na=False)
        return df[mask].copy()

    # フォールバック: 法人住所で検索
    corp_col = "法人住所（市区町村）"
    if corp_col in df.columns:
        mask = df[corp_col].str.contains(TARGET_CITY, na=False)
        return df[mask].copy()

    return df.copy()


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """WAM NET の実カラム名を内部標準名にリネームする"""
    rename = {
        "都道府県コード又は市区町村コード": "area_code",
        "指定機関名": "designated_by",
        "法人の名称": "corp_name",
        "法人番号": "corp_no",
        "法人住所（市区町村）": "corp_city",
        "法人住所（番地以降）": "corp_addr2",
        "法人電話番号": "corp_phone",
        "法人URL": "corp_url",
        "サービス種別": "service_label",
        "事業所の名称": "office_name",
        "事業所番号": "office_no",
        "事業所住所（市区町村）": "city_name",
        "事業所住所（番地以降）": "address2",
        "事業所電話番号": "phone",
        "事業所FAX番号": "fax",
        "事業所URL": "url",
        "事業所緯度": "lat",
        "事業所経度": "lng",
        "定員": "capacity",
        "定休日": "closed_days",
        "利用可能な時間帯（平日）": "hours_weekday",
    }
    existing = {k: v for k, v in rename.items() if k in df.columns}
    df = df.rename(columns=existing)

    # 住所を結合
    if "city_name" in df.columns and "address2" in df.columns:
        df["address"] = df["city_name"].fillna("") + df["address2"].fillna("")
    elif "city_name" in df.columns:
        df["address"] = df["city_name"].fillna("")

    # work_type は空欄（CSVにない）
    if "work_type" not in df.columns:
        df["work_type"] = ""

    # pref_name を city_name から抽出
    if "city_name" in df.columns:
        df["pref_name"] = df["city_name"].str.extract(r"^(..?[都道府県])")[0].fillna("")

    return df


def fetch_ichikawa_data(year_month: Optional[str] = None) -> Tuple[pd.DataFrame, str]:
    """
    市川市の就労継続支援A型・B型データを取得して結合したDataFrameを返す。
    戻り値: (DataFrame, 取得した年月yyyymm)
    """
    all_frames = []
    used_ym = None

    for service_type, code in FILE_CODES.items():
        for ym in _quarter_candidates(year_month):
            raw = _download_zip(ym, code)
            if raw is None:
                continue

            df = _parse_zip(raw, service_type)
            if df.empty:
                continue

            filtered = _filter_ichikawa(df)
            filtered = _normalize(filtered)

            used_ym = ym
            logger.info(f"{service_type} {ym}: {len(filtered)}件 (市川市)")
            all_frames.append(filtered)
            break

    if not all_frames:
        return pd.DataFrame(), used_ym or ""

    result = pd.concat(all_frames, ignore_index=True)
    return result, used_ym or ""
