"""
数据采集服务
"""

from datetime import date
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.models.stock_data import StockMinuteData
from app.models.watchlist import WatchList
from app.core.logger import get_module_logger
from app.utils.stock_data_fetcher import stock_data_fetcher

# 获取logger
logger = get_module_logger("data_collector")


class DataCollector:
    """数据采集器"""

    def __init__(self, db: Session):
        self.db = db

    def collect_minute_data(self, stock_code: str, trade_date: date) -> int:
        """
        采集单个股票的分时数据

        Args:
            stock_code: 股票代码
            trade_date: 交易日期

        Returns:
            采集的数据条数
        """
        try:
            # 构造交易时间范围（09:00:00 到 16:00:00）
            start_datetime_str = trade_date.strftime("%Y-%m-%d 09:00:00")
            end_datetime_str = trade_date.strftime("%Y-%m-%d 16:00:00")

            # 使用统一的数据获取工具获取分时数据（不复权）
            df = stock_data_fetcher.fetch_minute_data(
                stock_code=stock_code,
                start_datetime=start_datetime_str,
                end_datetime=end_datetime_str,
                period="1",
                adjust="",
            )

            if df is None or df.empty:
                return 0

            # 使用统一的数据清洗方法
            df = stock_data_fetcher.clean_minute_data_for_storage(
                df=df,
                stock_code=stock_code,
                trade_date=trade_date,
            )

            # 批量插入数据库
            count = 0
            for _, row in df.iterrows():
                # 检查是否已存在
                existing = (
                    self.db.query(StockMinuteData)
                    .filter(
                        and_(
                            StockMinuteData.stock_code == stock_code,
                            StockMinuteData.trade_time == row["trade_time"],
                        )
                    )
                    .first()
                )

                if not existing:
                    data_point = StockMinuteData(
                        stock_code=stock_code,
                        trade_date=trade_date,
                        trade_time=row["trade_time"],
                        open_price=float(row["open_price"]),
                        high_price=float(row["high_price"]),
                        low_price=float(row["low_price"]),
                        close_price=float(row["close_price"]),
                        volume=float(row["volume"]),
                    )
                    self.db.add(data_point)
                    count += 1

            self.db.commit()
            logger.info(f"成功采集股票 {stock_code} 数据，共 {count} 条")
            return count

        except Exception as e:
            logger.error(f"采集股票 {stock_code} 数据失败: {str(e)}", exc_info=True)
            self.db.rollback()
            return 0

    def collect_all_watchlist_stocks(self, trade_date: Optional[date] = None) -> dict:
        """
        采集所有监控列表中的股票数据

        Args:
            trade_date: 交易日期，默认为今天

        Returns:
            采集结果统计
        """
        if trade_date is None:
            trade_date = date.today()

        # 获取所有不重复的股票代码
        stock_codes = self.db.query(WatchList.stock_code).distinct().all()
        stock_codes = [code[0] for code in stock_codes]

        results = {
            "total_stocks": len(stock_codes),
            "success_count": 0,
            "fail_count": 0,
            "total_records": 0,
            "details": [],
        }

        for stock_code in stock_codes:
            count = self.collect_minute_data(stock_code, trade_date)
            if count > 0:
                results["success_count"] += 1
                results["total_records"] += count
                results["details"].append(
                    {"stock_code": stock_code, "records": count, "status": "success"}
                )
            else:
                results["fail_count"] += 1
                results["details"].append(
                    {"stock_code": stock_code, "records": 0, "status": "failed"}
                )

        return results
