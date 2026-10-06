"""
数据采集服务（在线分时表）

采集**自选股**分钟数据写入 PG ``stock_minute_data``（在线查询路径：分时图 / 告警 /
选股信号）。本表只是近期窗口、不是归档：全市场跨年分钟归档路径已按「纯网络取数」
方向退役（VEW-71），本模块是按需取数的唯一写入方。

写入契约：按 ``(stock_code, period, trade_time)`` 批量 upsert（PG 走
``ON CONFLICT DO UPDATE``），替代原先「逐行 query 查重再 add」的 N+1 写法。
"""

from datetime import date, datetime
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models.stock_data import DEFAULT_MINUTE_PERIOD, StockMinuteData
from app.models.watchlist import WatchList
from app.core.logger import get_module_logger
from app.providers import get_data_provider

# 获取logger
logger = get_module_logger("data_collector")

# 冲突时用新值覆盖的列（幂等重采 = 覆盖而不是追加）
_UPSERT_UPDATE_COLUMNS = (
    "trade_date",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume",
)


class DataCollector:
    """数据采集器"""

    def __init__(self, db: Session):
        self.db = db
        self.provider = get_data_provider()

    def collect_minute_data(
        self,
        stock_code: str,
        trade_date: date,
        period: str = DEFAULT_MINUTE_PERIOD,
    ) -> int:
        """
        采集单个股票的分时数据（幂等：同 key 重采覆盖）

        Args:
            stock_code: 股票代码
            trade_date: 交易日期
            period: K线周期（分钟），默认 1 分钟

        Returns:
            写入/更新的数据条数
        """
        try:
            # 构造交易时间范围（09:00:00 到 16:00:00）
            start_datetime_str = trade_date.strftime("%Y-%m-%d 09:00:00")
            end_datetime_str = trade_date.strftime("%Y-%m-%d 16:00:00")

            # 使用统一的数据源接口获取分时数据（不复权）
            df = self.provider.fetch_minute_data(
                stock_code=stock_code,
                start_datetime=start_datetime_str,
                end_datetime=end_datetime_str,
                period=str(period),
                adjust="",
            )

            if df is None or df.empty:
                return 0

            # 映射到数据库列名
            df = df.rename(
                columns={
                    "datetime": "trade_time",
                    "open": "open_price",
                    "close": "close_price",
                    "high": "high_price",
                    "low": "low_price",
                    "volume": "volume",
                }
            )
            df["trade_time"] = pd.to_datetime(df["trade_time"])
            # 备源（东财 trends2）会返回跨日窗口，只保留当日 bar，避免把别的交易日
            # 的数据标成今天（VEW-64）。
            df = df[df["trade_time"].dt.date == trade_date]
            if df.empty:
                return 0

            df["trade_date"] = trade_date
            df["stock_code"] = stock_code
            df["period"] = str(period)

            count = self._bulk_upsert(df, stock_code, trade_date, str(period))
            logger.info(
                f"成功采集股票 {stock_code} period={period} 数据，共 {count} 条"
            )
            return count

        except Exception as e:
            logger.error(f"采集股票 {stock_code} 数据失败: {str(e)}", exc_info=True)
            self.db.rollback()
            return 0

    def _bulk_upsert(
        self, df: pd.DataFrame, stock_code: str, trade_date: date, period: str
    ) -> int:
        """按唯一键 ``(stock_code, period, trade_time)`` 批量 upsert。

        一次往返写入整段分时，替掉原先逐行 ``query().first()`` 的 N+1 查重。
        PostgreSQL 走 ``ON CONFLICT DO UPDATE``；其它方言（测试用的 sqlite）退化为
        「一次查出已存在的 trade_time，只插新增」，语义一致但只保证幂等、不覆盖。
        """
        now = datetime.utcnow()
        rows = []
        for row in df.itertuples(index=False):
            rows.append(
                {
                    "stock_code": stock_code,
                    "period": period,
                    "trade_date": trade_date,
                    "trade_time": pd.Timestamp(row.trade_time).to_pydatetime(),
                    "open_price": float(row.open_price),
                    "high_price": float(row.high_price),
                    "low_price": float(row.low_price),
                    "close_price": float(row.close_price),
                    "volume": float(row.volume),
                    # core insert 不走 ORM 的 Python 侧默认值，显式带上 created_at
                    "created_at": now,
                }
            )
        if not rows:
            return 0

        bind = self.db.get_bind()
        dialect = bind.dialect.name if bind is not None else ""

        if dialect == "postgresql":
            stmt = pg_insert(StockMinuteData).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_code", "period", "trade_time"],
                set_={
                    col: getattr(stmt.excluded, col) for col in _UPSERT_UPDATE_COLUMNS
                },
            )
            self.db.execute(stmt)
            self.db.commit()
            return len(rows)

        existing = {
            value
            for (value,) in self.db.query(StockMinuteData.trade_time)
            .filter(
                StockMinuteData.stock_code == stock_code,
                StockMinuteData.period == period,
                StockMinuteData.trade_date == trade_date,
            )
            .all()
        }
        fresh = [r for r in rows if r["trade_time"] not in existing]
        if fresh:
            self.db.bulk_insert_mappings(StockMinuteData, fresh)
        self.db.commit()
        return len(fresh)

    def collect_all_watchlist_stocks(
        self,
        trade_date: Optional[date] = None,
        period: str = DEFAULT_MINUTE_PERIOD,
    ) -> dict:
        """
        采集所有监控列表中的股票数据

        Args:
            trade_date: 交易日期，默认为今天
            period: K线周期（分钟），默认 1 分钟

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
            count = self.collect_minute_data(stock_code, trade_date, period=period)
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
