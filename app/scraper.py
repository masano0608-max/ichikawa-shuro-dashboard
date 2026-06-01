"""
WAM NET 個別事業所ページから業種・作業内容を取得するスクレイパー
（CSVに業種情報がないため補完用）
"""

import logging
import re
import time
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.wam.go.jp/sfkohyoout/COP010100E0000.do"
DETAIL_URL = "https://www.wam.go.jp/sfkohyoout/COP010200E0000.do"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "ja,en;q=0.9",
}

# 就労継続支援に多い業種キーワード
WORK_TYPE_KEYWORDS = [
    "清掃", "クリーニング", "洗車",
    "農業", "農作業", "農産物",
    "製造", "組立", "梱包", "パッキング",
    "軽作業", "内職",
    "食品", "弁当", "カフェ", "飲食", "焼き菓子", "パン",
    "印刷", "データ入力", "PC", "パソコン",
    "縫製", "手工芸", "クラフト",
    "販売", "ネット販売", "EC",
    "リサイクル", "古紙",
    "事務", "書類整理",
    "福祉", "介護補助",
    "アート", "絵画", "デザイン",
    "園芸", "植栽",
    "木工", "工芸",
]


def _extract_work_type(text: str) -> str:
    """テキストから業種キーワードを抽出する"""
    found = []
    for kw in WORK_TYPE_KEYWORDS:
        if kw in text:
            found.append(kw)
    return "、".join(found[:3]) if found else ""


def fetch_work_types_for_offices(offices: list) -> Dict[str, str]:
    """
    事業所番号 → 業種 のマッピングを返す。
    WAM NET検索ページから事業所の詳細情報を取得する。
    件数が多いのでサンプリングして取得する。
    """
    result = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    for office in offices[:50]:  # 負荷軽減のため最大50件
        office_no = office.get("office_no", "")
        if not office_no:
            continue

        try:
            # WAM NETの検索エンドポイント
            resp = session.get(
                DETAIL_URL,
                params={"action": "init", "jigyosyoNo": office_no},
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            text = resp.text
            # 作業内容・サービス内容を探す
            patterns = [
                r"主な作業.*?<td[^>]*>(.*?)</td>",
                r"サービス内容.*?<td[^>]*>(.*?)</td>",
                r"就労支援.*?<td[^>]*>(.*?)</td>",
                r"作業内容.*?<td[^>]*>(.*?)</td>",
            ]
            for pat in patterns:
                m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
                if m:
                    raw = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                    if raw and len(raw) < 200:
                        result[office_no] = raw[:100]
                        break
            else:
                # キーワードマッチで推定
                wt = _extract_work_type(text)
                if wt:
                    result[office_no] = wt

        except Exception as e:
            logger.debug(f"scrape error {office_no}: {e}")

        time.sleep(0.5)  # レートリミット

    return result
