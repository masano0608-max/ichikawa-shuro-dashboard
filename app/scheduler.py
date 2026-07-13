"""
APScheduler による自動更新スケジューラー
厚労省データは半年ごとに更新されるので、毎月チェックして新データがあれば取得する
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import get_houmon_stats, log_houmon_update, upsert_houmon_offices
from app.fetcher_houmon import fetch_houmon_data

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Tokyo")


async def run_houmon_update(force: bool = False):
    """訪問看護データ更新を実行する"""
    logger.info("訪問看護データ更新開始...")
    try:
        df, fetched_at = fetch_houmon_data()

        if df.empty:
            log_houmon_update(0, 0, "no_data", "データが取得できませんでした")
            logger.warning("訪問看護データが空でした")
            return {"status": "no_data", "message": "データが取得できませんでした"}

        total = len(df)
        psych = len(df[df["category"] == "精神科特化"]) if "category" in df.columns else 0

        upsert_houmon_offices(df, fetched_at)
        log_houmon_update(total, psych, "success")

        logger.info(f"訪問看護更新完了: 合計={total} 精神科特化={psych}")
        return {
            "status": "success",
            "total_count": total,
            "psych_count": psych,
        }

    except Exception as e:
        logger.error(f"訪問看護更新エラー: {e}", exc_info=True)
        log_houmon_update(0, 0, "error", str(e))
        return {"status": "error", "message": str(e)}


def start_scheduler():
    """スケジューラーを起動する（毎月1日 午前4時にチェック）"""
    scheduler.add_job(
        run_houmon_update,
        trigger=CronTrigger(day=1, hour=4, minute=0, timezone="Asia/Tokyo"),
        id="houmon_update",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("スケジューラー起動: 毎月1日 04:00 訪問看護 自動更新")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
