"""
价格预警服务
"""

from datetime import datetime, timedelta, date
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_
import akshare as ak
import numpy as np
from app.models.user import User
from app.models.watchlist import WatchList
from app.models.stock_data import StockMinuteData
from app.utils.data_processor import DataProcessor
from app.services.wechat_service import wechat_service
from app.core.logger import get_module_logger

# 获取logger
logger = get_module_logger("alert_service")


class AlertService:
    """价格预警服务"""

    def __init__(self, db: Session):
        self.db = db
        self.data_processor = DataProcessor()

    def check_stock_alert(
        self, user: User, watchlist_item: WatchList, current_price: float
    ) -> bool:
        """
        检查单个股票是否需要预警

        Args:
            user: 用户对象
            watchlist_item: 监控项
            current_price: 当前价格

        Returns:
            是否触发预警
        """
        # 获取预警阈值
        threshold = watchlist_item.alert_threshold or user.alert_threshold

        # 获取历史数据（最近5个交易日）
        end_date = date.today()
        start_date = end_date - timedelta(days=7)

        historical_data = (
            self.db.query(StockMinuteData)
            .filter(
                and_(
                    StockMinuteData.stock_code == watchlist_item.stock_code,
                    StockMinuteData.trade_date >= start_date,
                    StockMinuteData.trade_date <= end_date,
                )
            )
            .order_by(StockMinuteData.trade_time)
            .all()
        )

        if not historical_data:
            return False

        # 转换为字典列表
        chart_data = [
            {
                "datetime": str(item.trade_time),
                "price": item.close_price,
                "volume": item.volume,
                "open": item.open_price,
                "high": item.high_price,
                "low": item.low_price,
            }
            for item in historical_data
        ]

        # 进行GMM拟合
        fit_result = self.data_processor.fit_gaussian_mixture(chart_data)

        if not fit_result:
            return False

        # 计算当前价格在分布中的位置
        price_density = self._calculate_price_density(current_price, fit_result)

        # 判断是否超过阈值
        if price_density >= threshold:
            return True

        return False

    def _calculate_price_density(self, price: float, fit_result: dict) -> float:
        """
        计算价格在正态分布中的密度百分位

        Args:
            price: 价格
            fit_result: GMM拟合结果

        Returns:
            密度百分位（0-1）
        """
        if not fit_result or "fit_curve" not in fit_result:
            return 0.0

        fit_curve = fit_result["fit_curve"]
        if not fit_curve:
            return 0.0

        # 找到当前价格对应的拟合密度
        prices = [point["price"] for point in fit_curve]
        densities = [point["fitVolume"] for point in fit_curve]

        # 使用线性插值
        current_density = np.interp(price, prices, densities)

        # 计算百分位
        max_density = max(densities)
        if max_density > 0:
            percentile = current_density / max_density
            return percentile

        return 0.0

    def check_all_alerts(self) -> Dict[str, Any]:
        """
        检查所有用户的预警

        Returns:
            检查结果统计
        """
        results = {
            "total_checks": 0,
            "alerts_triggered": 0,
            "alerts_sent": 0,
            "details": [],
        }

        # 获取所有启用预警的监控项
        watchlist_items = (
            self.db.query(WatchList).filter(WatchList.alert_enabled == True).all()
        )

        for item in watchlist_items:
            results["total_checks"] += 1

            # 获取用户
            user = self.db.query(User).filter(User.id == item.user_id).first()
            if not user or not user.is_active:
                continue

            # 检查是否最近已经预警过（1小时内不重复预警）
            if item.last_alerted_at:
                time_since_alert = datetime.utcnow() - item.last_alerted_at
                if time_since_alert < timedelta(hours=1):
                    continue

            try:
                # 获取当前价格
                current_price = self._get_current_price(item.stock_code)
                if current_price is None:
                    continue

                # 检查是否触发预警
                should_alert = self.check_stock_alert(user, item, current_price)

                if should_alert:
                    results["alerts_triggered"] += 1

                    # 发送微信通知
                    if user.wechat_openid:
                        threshold = item.alert_threshold or user.alert_threshold
                        alert_reason = f"当前价格 ¥{current_price:.2f} 超过正态分布 {threshold*100:.0f}% 阈值"

                        sent = wechat_service.send_price_alert(
                            openid=user.wechat_openid,
                            stock_code=item.stock_code,
                            stock_name=item.stock_name or item.stock_code,
                            current_price=current_price,
                            alert_reason=alert_reason,
                        )

                        if sent:
                            results["alerts_sent"] += 1
                            # 更新最后预警时间
                            item.last_alerted_at = datetime.utcnow()
                            self.db.commit()

                        results["details"].append(
                            {
                                "user_id": user.id,
                                "stock_code": item.stock_code,
                                "current_price": current_price,
                                "alert_sent": sent,
                            }
                        )

            except Exception as e:
                logger.error(f"检查预警失败 {item.stock_code}: {str(e)}", exc_info=True)
                continue

        return results

    def _get_current_price(self, stock_code: str) -> float:
        """
        获取股票当前价格

        Args:
            stock_code: 股票代码

        Returns:
            当前价格，获取失败返回None
        """
        try:
            # 先尝试从数据库获取最新数据
            latest_data = (
                self.db.query(StockMinuteData)
                .filter(StockMinuteData.stock_code == stock_code)
                .order_by(StockMinuteData.trade_time.desc())
                .first()
            )

            if latest_data:
                # 如果是今天的数据，直接使用
                if latest_data.trade_date == date.today():
                    return latest_data.close_price

            # 否则从akshare实时获取
            df = ak.stock_zh_a_spot_em()
            stock_data = df[df["代码"] == stock_code]

            if not stock_data.empty:
                return float(stock_data.iloc[0]["最新价"])

            return None

        except Exception as e:
            logger.error(f"获取价格失败 {stock_code}: {str(e)}", exc_info=True)
            return None
