"""
股票数据服务
负责调用AKShare API获取数据并进行处理
"""

import pandas as pd
from typing import List, Dict, Any, Tuple
from datetime import datetime
import logging
from pathlib import Path
from sqlalchemy.orm import Session

from app.schemas.stock import StockSearchResult
from app.utils.data_processor import DataProcessor
from app.providers import get_data_provider
from app.providers.astock_data import tencent_quote, eastmoney_stock_info
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.stock_data import StockMinuteData
from app.models.security_universe import SecurityUniverse

logger = logging.getLogger(__name__)


class StockService:
    """股票数据服务类"""

    def __init__(self):
        self.data_processor = DataProcessor()
        self.provider = get_data_provider()

    def get_all_stock_symbols(
        self,
        limit: int | None = None,
        boards: List[str] | None = None,
        exclude_st: bool = True,
    ) -> List[str]:
        """获取股票代码列表，优先使用 security_universe 维表，失败时回退静态清单。"""
        allowed_boards = {"main", "gem", "star", "bse"}
        normalized_boards = [b for b in (boards or ["main"]) if b in allowed_boards]

        try:
            db: Session = SessionLocal()
            try:
                query = db.query(SecurityUniverse.stock_code).filter(
                    SecurityUniverse.is_active.is_(True)
                )
                if normalized_boards:
                    query = query.filter(SecurityUniverse.board.in_(normalized_boards))
                if exclude_st:
                    query = query.filter(SecurityUniverse.is_st.is_(False))

                rows = query.order_by(SecurityUniverse.stock_code.asc()).all()
                symbols = [str(row[0]).zfill(6) for row in rows if row[0]]
                if symbols:
                    logger.info(
                        "从 security_universe 获取股票池成功，数量: %s, boards=%s, exclude_st=%s",
                        len(symbols),
                        normalized_boards,
                        exclude_st,
                    )
                    if limit and limit > 0:
                        return symbols[:limit]
                    return symbols
            finally:
                db.close()

            static_codes = self._load_static_symbols()
            if not static_codes:
                logger.error(
                    "security_universe 与静态A股清单均为空，无法执行全市场扫描"
                )
                return []

            # 静态池回退时，只做板块过滤（ST 信息无法可靠识别）
            filtered = [
                code
                for code in static_codes
                if self._detect_board(code) in set(normalized_boards or ["main"])
            ]
            logger.warning(
                "security_universe 为空，已回退静态清单。boards=%s, exclude_st=%s(回退模式可能不生效), count=%s",
                normalized_boards,
                exclude_st,
                len(filtered),
            )
            if limit and limit > 0:
                return filtered[:limit]
            return filtered
        except Exception as e:
            logger.error(f"获取全市场股票列表失败: {str(e)}")
            return []

    @staticmethod
    def _detect_board(code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith(("300", "301")):
            return "gem"
        if code.startswith("688"):
            return "star"
        if code.startswith(
            (
                "430",
                "831",
                "832",
                "833",
                "834",
                "835",
                "836",
                "837",
                "838",
                "839",
                "870",
                "871",
                "872",
                "873",
                "874",
                "875",
                "876",
                "877",
                "878",
                "879",
                "880",
                "881",
                "882",
                "883",
                "884",
                "885",
                "886",
                "887",
                "888",
                "889",
            )
        ):
            return "bse"
        return "main"

    def _load_static_symbols(self) -> List[str]:
        """从静态文件加载A股代码清单"""
        try:
            backend_root = Path(__file__).resolve().parents[2]  # backend/
            static_file = backend_root / "data" / "a_share_symbols.txt"
            if not static_file.exists():
                return []

            codes: List[str] = []
            with static_file.open("r", encoding="utf-8") as f:
                for line in f:
                    code = line.strip()
                    if not code or code.startswith("#"):
                        continue
                    if code.isdigit() and len(code) <= 6:
                        codes.append(code.zfill(6))
            return list(dict.fromkeys(codes))
        except Exception as e:
            logger.error(f"读取静态A股清单失败: {str(e)}")
            return []

    def search_stocks(self, keyword: str) -> List[StockSearchResult]:
        """搜索股票（Eastmoney 主源，security_universe + Tencent 备源）。"""
        try:
            df = self.provider.fetch_realtime_data()
            if df is not None and not df.empty:
                mask = df["code"].str.contains(keyword, case=False, na=False) | df[
                    "name"
                ].str.contains(keyword, case=False, na=False)
                filtered_df = df[mask].head(settings.MAX_SEARCH_RESULTS)
                results = []
                for _, row in filtered_df.iterrows():
                    results.append(
                        StockSearchResult(
                            code=str(row["code"]),
                            name=str(row["name"]),
                            current_price=(
                                float(row["price"]) if pd.notna(row["price"]) else 0.0
                            ),
                        )
                    )
                if results:
                    return results
        except Exception:
            pass  # Fall through to DB fallback

        # Fallback: search security_universe table + get prices from Tencent
        try:
            db: Session = SessionLocal()
            try:
                query = db.query(
                    SecurityUniverse.stock_code, SecurityUniverse.stock_name
                ).filter(
                    SecurityUniverse.is_active.is_(True),
                    (
                        SecurityUniverse.stock_code.contains(keyword)
                        | SecurityUniverse.stock_name.contains(keyword)
                    ),
                )
                rows = query.limit(settings.MAX_SEARCH_RESULTS).all()
                if not rows:
                    return []

                codes = [str(r[0]).zfill(6) for r in rows]
                names = {str(r[0]).zfill(6): str(r[1]) for r in rows}

                # Batch query Tencent for real-time prices
                quotes = tencent_quote(codes)

                results = []
                for code in codes:
                    q = quotes.get(code, {})
                    results.append(
                        StockSearchResult(
                            code=code,
                            name=names.get(code, code),
                            current_price=q.get("price", 0.0),
                        )
                    )
                return results
            finally:
                db.close()
        except Exception as e:
            raise Exception(f"搜索股票失败: {str(e)}")

    def get_daily_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        count: int = 500,
        start_offset: int = 0,
    ) -> Tuple[pd.DataFrame, str, str]:
        """
        获取日线数据（用于回测）

        Args:
            symbol: 股票代码
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            count: 返回的最大K线数量（分页用）
            start_offset: 跳过前N根K线（分页用）

        Returns:
            (日线数据DataFrame, 实际开始日期, 实际结束日期)
        """
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            api_df = self.provider.fetch_daily_data(
                stock_code=symbol,
                start_date=start_dt.strftime("%Y%m%d"),
                end_date=end_dt.strftime("%Y%m%d"),
                adjust="qfq",
                count=count,
                start_offset=start_offset,
            )

            if api_df is None or api_df.empty:
                return (
                    pd.DataFrame(
                        columns=["datetime", "open", "high", "low", "close", "volume"]
                    ),
                    start_date,
                    end_date,
                )

            result_df = api_df
            if result_df.empty or "datetime" not in result_df.columns:
                return (
                    pd.DataFrame(
                        columns=["datetime", "open", "high", "low", "close", "volume"]
                    ),
                    start_date,
                    end_date,
                )

            actual_start = result_df["datetime"].min()
            actual_end = result_df["datetime"].max()
            return result_df, actual_start, actual_end

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"获取日线数据失败: {str(e)}")
            return (
                pd.DataFrame(
                    columns=["datetime", "open", "high", "low", "close", "volume"]
                ),
                start_date,
                end_date,
            )

    def get_minute_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> Tuple[pd.DataFrame, str, str]:
        """
        获取分钟数据，优先从数据库获取历史数据，然后从API获取最近数据

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            (分钟数据DataFrame, 实际开始日期, 实际结束日期)

        Raises:
            Exception: 获取数据失败时抛出异常
        """
        try:
            # 获取日期范围
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            # 设置交易时间范围（09:00:00 到 16:00:00）
            start_datetime_str = start_dt.strftime("%Y-%m-%d 09:00:00")
            end_datetime_str = end_dt.strftime("%Y-%m-%d 16:00:00")

            # 1. 从数据库查询历史数据
            db = SessionLocal()
            try:
                db_records = (
                    db.query(StockMinuteData)
                    .filter(
                        StockMinuteData.stock_code == symbol,
                        StockMinuteData.trade_date >= start_dt.date(),
                        StockMinuteData.trade_date <= end_dt.date(),
                    )
                    .order_by(StockMinuteData.trade_time)
                    .all()
                )

                # 转换数据库记录为DataFrame
                db_data = []
                if db_records:
                    for record in db_records:
                        db_data.append(
                            {
                                "datetime": record.trade_time.strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                                "open": record.open_price,
                                "high": record.high_price,
                                "low": record.low_price,
                                "close": record.close_price,
                                "volume": record.volume,
                            }
                        )
                    logger.info(
                        f"从数据库获取到 {len(db_data)} 条历史数据，股票: {symbol}"
                    )

                db_df = pd.DataFrame(db_data) if db_data else pd.DataFrame()

            finally:
                db.close()

            # 2. 使用统一的数据源接口获取分时数据（前复权）
            api_df = self.provider.fetch_minute_data(
                stock_code=symbol,
                start_datetime=start_datetime_str,
                end_datetime=end_datetime_str,
                period="1",
                adjust="qfq",
            )

            if api_df is not None and not api_df.empty:
                logger.info(f"从API获取到 {len(api_df)} 条数据，股票: {symbol}")
            else:
                api_df = pd.DataFrame()

            # 3. 合并数据库数据和API数据
            if not db_df.empty and not api_df.empty:
                # 合并两个DataFrame
                combined_df = pd.concat([db_df, api_df], ignore_index=True)
                # 去重（基于datetime列）
                combined_df = combined_df.drop_duplicates(
                    subset=["datetime"], keep="last"
                )
                # 按时间排序
                combined_df = combined_df.sort_values("datetime").reset_index(drop=True)
                logger.info(f"合并后共 {len(combined_df)} 条数据，股票: {symbol}")
                result_df = combined_df
            elif not db_df.empty:
                result_df = db_df
            elif not api_df.empty:
                result_df = api_df
            else:
                result_df = pd.DataFrame(
                    columns=["datetime", "open", "high", "low", "close", "volume"]
                )

            # 4. 计算实际的时间范围
            if result_df.empty or "datetime" not in result_df.columns:
                return result_df, start_datetime_str, end_datetime_str

            actual_start = result_df["datetime"].min()
            actual_end = result_df["datetime"].max()

            return result_df, actual_start, actual_end

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"获取分钟数据失败: {str(e)}")
            return (
                pd.DataFrame(
                    columns=["datetime", "open", "high", "low", "close", "volume"]
                ),
                start_date,
                end_date,
            )

    def get_cyq_data(self, symbol: str, adjust: str = "") -> Dict[str, Any]:
        """
        获取股票筹码分布数据

        Args:
            symbol: 股票代码
            adjust: 复权类型，""表示不复权，"qfq"表示前复权，"hfq"表示后复权

        Returns:
            包含筹码分布数据的字典

        Raises:
            Exception: 数据获取失败
        """
        try:
            df = self.provider.fetch_cyq_data(stock_code=symbol, adjust=adjust)

            if df is None or df.empty:
                raise Exception(f"未找到股票 {symbol} 的筹码分布数据")

            cyq_info = self.provider.normalize_cyq_data(df)

            return {
                "success": True,
                "symbol": symbol,
                "adjust": adjust,
                "cyq_info": cyq_info,
            }

        except Exception as e:
            logger.error(f"获取筹码分布数据失败: {str(e)}")
            raise Exception(f"获取筹码分布数据失败: {str(e)}")

    # ------------------------------------------------------------------
    # Period label mapping (shared across K-line / stock-data endpoints)
    # ------------------------------------------------------------------

    _PERIOD_LABEL_MAP: Dict[str, str] = {
        "1": "1min",
        "5": "5min",
        "15": "15min",
        "30": "30min",
        "60": "60min",
        "101": "daily",
    }

    @staticmethod
    def _df_to_kline_list(df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Convert a DataFrame to a list of K-line dicts with correct field names.

        Produces dicts with keys: datetime, open, close, high, low, volume, amount.
        """
        klines: List[Dict[str, Any]] = []
        has_amount = "amount" in df.columns
        for _, row in df.iterrows():
            point: Dict[str, Any] = {
                "datetime": str(row.get("datetime", "")),
                "open": float(row.get("open", 0)),
                "close": float(row.get("close", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "volume": float(row.get("volume", 0)),
            }
            if has_amount:
                amount = row.get("amount")
                point["amount"] = float(amount) if pd.notna(amount) else None
            klines.append(point)
        return klines

    # ------------------------------------------------------------------
    # K-line data (multi-period)
    # ------------------------------------------------------------------

    def get_kline_data(
        self,
        symbol: str,
        period: str = "5",
        start_date: str = "",
        end_date: str = "",
        adjust: str = "qfq",
        offset: int = 0,
        count: int = 500,
    ) -> Dict[str, Any]:
        """获取任意周期的K线数据。offset/count 用于分页动态加载。"""
        period_label = self._PERIOD_LABEL_MAP.get(period, f"{period}min")

        try:
            if period == "101":
                df, actual_start, actual_end = self.get_daily_data(
                    symbol=symbol,
                    start_date=start_date or "2000-01-01",
                    end_date=end_date or "2099-12-31",
                    count=count,
                    start_offset=offset,
                )
            else:
                start_dt = start_date or "2000-01-01"
                end_dt = end_date or "2099-12-31"
                start_datetime = f"{start_dt} 09:00:00"
                end_datetime = f"{end_dt} 16:00:00"

                df = self.provider.fetch_minute_data(
                    stock_code=symbol,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    period=period,
                    adjust=adjust,
                    count=count,
                    start_offset=offset,
                )

                if df is None or df.empty:
                    df = pd.DataFrame(
                        columns=["datetime", "open", "high", "low", "close", "volume"]
                    )
                    actual_start = start_datetime
                    actual_end = end_datetime
                else:
                    df["datetime"] = df["datetime"].astype(str)
                    actual_start = str(df["datetime"].min())
                    actual_end = str(df["datetime"].max())

            if not df.empty:
                df["datetime"] = df["datetime"].astype(str)

            klines = self._df_to_kline_list(df)

            return {
                "success": True,
                "symbol": symbol,
                "period": period_label,
                "adjust": adjust,
                "start_date": start_date,
                "end_date": end_date,
                "actual_start_date": str(actual_start),
                "actual_end_date": str(actual_end),
                "count": len(klines),
                "klines": klines,
            }
        except Exception as e:
            logger.error(f"获取K线数据失败: {str(e)}")
            raise Exception(f"获取K线数据失败: {str(e)}")

    def get_volume_profile(
        self,
        symbol: str,
        period: str = "5",
        start_date: str = "",
        end_date: str = "",
        adjust: str = "qfq",
        bins: int = 100,
    ) -> Dict[str, Any]:
        """获取Volume Profile（成交量分布）。先获取K线数据，再计算Volume Profile。"""
        kline_result = self.get_kline_data(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )

        klines_list = kline_result.get("klines", [])
        if not klines_list:
            return {
                "success": True,
                "symbol": symbol,
                "period": kline_result["period"],
                "total_volume": 0.0,
                "price_min": 0.0,
                "price_max": 0.0,
                "bin_size": 0.0,
                "profile": [],
                "poc": {"price": 0.0, "volume": 0.0},
                "value_area": {"vah": 0.0, "val": 0.0, "volume_pct": 0.0},
                "hvn_levels": [],
                "lvn_levels": [],
                "vwap": 0.0,
            }

        df = pd.DataFrame(klines_list)
        result = DataProcessor.compute_volume_profile(df, bins=bins)
        result["success"] = True
        result["symbol"] = symbol
        result["period"] = kline_result["period"]

        # GMM fit on Volume Profile distribution
        result["fit_result"] = DataProcessor.fit_gaussian_mixture(result["profile"])
        return result

    def get_batch_quotes(self, codes: list[str]) -> Dict[str, Any]:
        """批量获取腾讯实时行情。"""
        if not codes:
            return {"success": True, "quotes": {}}
        raw = tencent_quote(codes)
        return {"success": True, "quotes": raw}

    def get_stock_info(self, symbol: str) -> Dict[str, Any]:
        """获取个股基本信息 + 腾讯行情。"""
        stock_info = eastmoney_stock_info(symbol)
        tencent_data = tencent_quote([symbol])
        quote = tencent_data.get(symbol, {}) if tencent_data else {}
        return {
            "success": True,
            "symbol": symbol,
            "stock_info": stock_info if stock_info else None,
            "tencent_quote": quote if quote else None,
        }

    def get_depth_data(
        self,
        symbol: str,
        period: str = "5",
        start_date: str = "",
        end_date: str = "",
        adjust: str = "qfq",
    ) -> Dict[str, Any]:
        """获取深度数据综合响应 — 一次返回所有所需数据。"""
        kline_result = self.get_kline_data(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )

        klines_list = kline_result.get("klines", [])

        if klines_list:
            df = pd.DataFrame(klines_list)
            vol_profile = DataProcessor.compute_volume_profile(df)
            vol_profile["symbol"] = symbol
            vol_profile["period"] = kline_result["period"]
            vol_profile["success"] = True
            vol_profile["fit_result"] = DataProcessor.fit_gaussian_mixture(
                vol_profile["profile"]
            )
        else:
            vol_profile = {
                "success": True,
                "symbol": symbol,
                "period": kline_result["period"],
                "total_volume": 0.0,
                "price_min": 0.0,
                "price_max": 0.0,
                "bin_size": 0.0,
                "profile": [],
                "poc": {"price": 0.0, "volume": 0.0},
                "value_area": {"vah": 0.0, "val": 0.0, "volume_pct": 0.0},
                "hvn_levels": [],
                "lvn_levels": [],
                "vwap": 0.0,
                "fit_result": None,
            }

        cyq_info = None
        try:
            cyq_result = self.get_cyq_data(symbol=symbol, adjust=adjust)
            cyq_info = cyq_result.get("cyq_info")
        except Exception:
            logger.warning(f"获取 {symbol} 筹码分布失败，继续返回其他数据")

        stock_info = None
        tencent_data = None
        try:
            info_result = self.get_stock_info(symbol)
            stock_info = info_result.get("stock_info")
            tencent_data = info_result.get("tencent_quote")
        except Exception:
            logger.warning(f"获取 {symbol} 个股信息失败，继续返回其他数据")

        return {
            "success": True,
            "symbol": symbol,
            "period": kline_result["period"],
            "adjust": adjust,
            "start_date": start_date,
            "end_date": end_date,
            "klines": klines_list,
            "volume_profile": vol_profile,
            "cyq_info": cyq_info,
            "stock_info": stock_info,
            "tencent_quote": tencent_data,
        }


# 创建全局服务实例
stock_service = StockService()
