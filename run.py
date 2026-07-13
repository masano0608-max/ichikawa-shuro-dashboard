#!/usr/bin/env python3
"""
精神科訪問看護ステーション 開業準備ダッシュボード 起動スクリプト
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
