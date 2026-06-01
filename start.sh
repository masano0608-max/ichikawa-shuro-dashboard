#!/bin/bash
# 市川市 就労継続支援 市場調査ダッシュボード 起動スクリプト

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SITE_PACKAGES="/Users/masanotanaka/Library/Python/3.9/lib/python/site-packages"

echo "=========================================="
echo "  市川市 就労継続支援 市場調査ダッシュボード"
echo "=========================================="
echo ""

# サーバー起動
echo "サーバー起動中... http://localhost:8000"
PYTHONPATH="$SITE_PACKAGES" python3 run.py
