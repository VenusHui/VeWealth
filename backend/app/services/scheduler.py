"""
定时任务调度器
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import date
from app.core.config import settings
from app.core.database import SessionLocal


def collect_daily_data(trade_date: date = None):
    """
    每日数据采集任务
    
    Args:
        trade_date: 交易日期，默认为今天
    """
    from app.services.data_collector import DataCollector

    if trade_date is None:
        trade_date = date.today()
    
    print(f"[定时任务] 开始采集数据: {trade_date}")

    db = SessionLocal()
    try:
        collector = DataCollector(db)
        results = collector.collect_all_watchlist_stocks(trade_date=trade_date)
        print(f"[定时任务] 数据采集完成: {results}")
        return results
    except Exception as e:
        print(f"[定时任务] 数据采集失败: {str(e)}")
        return None
    finally:
        db.close()


def check_price_alerts():
    """
    价格预警检查任务
    """
    from app.services.alert_service import AlertService

    print(f"[定时任务] 开始检查价格预警: {date.today()}")

    db = SessionLocal()
    try:
        alert_service = AlertService(db)
        results = alert_service.check_all_alerts()
        print(f"[定时任务] 预警检查完成: {results}")
    except Exception as e:
        print(f"[定时任务] 预警检查失败: {str(e)}")
    finally:
        db.close()


class AppScheduler:
    """应用调度器"""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self._setup_jobs()

    def _setup_jobs(self):
        """设置定时任务"""
        if not settings.SCHEDULER_ENABLED:
            print("[调度器] 定时任务已禁用")
            return

        # 每日数据采集任务
        self.scheduler.add_job(
            collect_daily_data,
            trigger=CronTrigger.from_crontab(settings.DATA_COLLECT_CRON),
            id="daily_data_collection",
            name="每日数据采集",
            replace_existing=True,
        )
        print(f"[调度器] 已添加任务: 每日数据采集 ({settings.DATA_COLLECT_CRON})")

        # 价格预警检查任务
        self.scheduler.add_job(
            check_price_alerts,
            trigger=CronTrigger.from_crontab(settings.ALERT_CHECK_CRON),
            id="price_alert_check",
            name="价格预警检查",
            replace_existing=True,
        )
        print(f"[调度器] 已添加任务: 价格预警检查 ({settings.ALERT_CHECK_CRON})")

    def start(self):
        """启动调度器"""
        if not settings.SCHEDULER_ENABLED:
            return

        if not self.scheduler.running:
            self.scheduler.start()
            print("[调度器] 定时任务调度器已启动")

    def shutdown(self):
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            print("[调度器] 定时任务调度器已关闭")


# 全局调度器实例
app_scheduler = AppScheduler()
