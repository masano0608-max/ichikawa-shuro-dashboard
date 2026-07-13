"""
訪問看護ステーションの分類モジュール
事業所名・法人名・備考からキーワードマッチで精神科特化等を判定する
"""

import unicodedata


def _normalize(text):
    """全角→半角正規化"""
    return unicodedata.normalize("NFKC", text)


CATEGORIES = [
    ("精神科特化", [
        "精神", "メンタル", "こころ", "心療", "マインド",
        "ぴあ", "リカバリー", "ピア", "メンタルヘルス",
        "こころの", "心の", "心と",
    ]),
    ("小児・重症児", ["小児", "こども", "キッズ", "重心", "児童", "子ども"]),
    ("リハビリ特化", ["リハビリ", "リハ"]),
    ("24時間対応", ["24時間", "ナイト"]),
]


def _to_str(val):
    """NaN/None/floatを安全に文字列化"""
    if val is None:
        return ""
    if isinstance(val, float):
        import math
        return "" if math.isnan(val) else str(val)
    return str(val)


def classify_houmon(office_name, corp_name="", note=""):
    """訪問看護ステーションのカテゴリを判定する"""
    text = _normalize(_to_str(office_name) + " " + _to_str(corp_name) + " " + _to_str(note))
    text_lower = text.lower()
    for category, keywords in CATEGORIES:
        for kw in keywords:
            if _normalize(kw).lower() in text_lower:
                return category
    return "一般"
