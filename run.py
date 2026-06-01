#!/usr/bin/env python3
"""
市川市 就労継続支援 市場調査ダッシュボード 起動スクリプト
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
