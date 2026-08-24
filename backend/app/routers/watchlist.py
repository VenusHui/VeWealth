"""
监控列表路由
"""

import time
from datetime import date, timedelta
from typing import Optional, Dict, Tuple
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import and_
import numpy as np
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.watchlist import WatchList
from app.models.stock_data import StockMinuteData
from app.schemas.watchlist import (
    AddWatchListRequest,
    UpdateWatchListRequest,
    WatchListResponse,
    WatchListItemResponse,
    DeleteResponse,
)
from app.providers.astock_data import tencent_quote
from app.utils.data_processor import DataProcessor

router = APIRouter(prefix="/watchlist", tags=["监控列表"])

# In-memory cache for GMM signal computation: {stock_code: (timestamp, signal_dict)}
_signal_cache: Dict[str, Tuple[float, dict]] = {}
_SIGNAL_CACHE_TTL = 60


def _compute_gmm_signal(
    stock_code: str, current_price: float, db: Session
) -> Optional[dict]:
    """Compute GMM signal for a stock, with 60s cache."""
    now = time.time()
    if stock_code in _signal_cache:
        ts, cached = _signal_cache[stock_code]
        if now - ts < _SIGNAL_CACHE_TTL:
            return cached

    threshold = 0.7
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=3)
        rows = (
            db.query(StockMinuteData)
            .filter(
                and_(
                    StockMinuteData.stock_code == stock_code,
                    StockMinuteData.trade_date >= start_date,
                    StockMinuteData.trade_date <= end_date,
                )
            )
            .order_by(StockMinuteData.trade_time)
            .all()
        )
        if not rows:
            _signal_cache[stock_code] = (now, None)
            return None

        chart_data = [
            {
                "datetime": str(r.trade_time),
                "price": r.close_price,
                "volume": r.volume,
                "open": r.open_price,
                "high": r.high_price,
                "low": r.low_price,
            }
            for r in rows
        ]

        fit = DataProcessor.fit_gaussian_mixture(chart_data)
        if not fit or "fit_curve" not in fit:
            _signal_cache[stock_code] = (now, None)
            return None

        prices = [p["price"] for p in fit["fit_curve"]]
        densities = [p["fitVolume"] for p in fit["fit_curve"]]
        max_den = max(densities)
        if max_den <= 0:
            _signal_cache[stock_code] = (now, None)
            return None

        cur_den = float(np.interp(current_price, prices, densities))
        percentile = cur_den / max_den

        upper = threshold
        lower = 1.0 - threshold
        if percentile >= upper:
            signal = "sell"
        elif percentile <= lower:
            signal = "buy"
        else:
            signal = "neutral"

        components = fit.get("components", [])
        nearest_peak = None
        if components:
            nearest = min(components, key=lambda c: abs(c["mean"] - current_price))
            nearest_peak = nearest["mean"]

        result = {
            "signal": signal,
            "density": round(percentile, 4),
            "peak_price": nearest_peak,
        }
        _signal_cache[stock_code] = (now, result)
        return result

    except Exception:
        _signal_cache[stock_code] = (now, None)
        return None


@router.get("", response_model=WatchListResponse)
async def get_watchlist(
    current_user: User = Depends(get_current_active_user), db: Session = Depends(get_db)
):
    """
    获取当前用户的监控列表
    """
    watchlist = (
        db.query(WatchList)
        .filter(WatchList.user_id == current_user.id)
        .order_by(WatchList.created_at.desc())
        .all()
    )

    # Batch-fetch real-time quotes and attach to each item
    if watchlist:
        codes = [item.stock_code for item in watchlist]
        try:
            quotes = tencent_quote(codes)
        except Exception:
            quotes = {}

        for item in watchlist:
            q = quotes.get(item.stock_code, {})
            if q:
                item.current_price = q.get("price")
                item.change_pct = q.get("change_pct")
                item.change_amt = q.get("change_amt")

            # Compute GMM signal for alert-enabled stocks with valid price
            if item.alert_enabled and item.current_price is not None:
                sig = _compute_gmm_signal(item.stock_code, item.current_price, db)
                if sig:
                    item.gmm_signal = sig["signal"]
                    item.gmm_density = sig["density"]
                    item.gmm_peak_price = sig["peak_price"]

    return WatchListResponse(success=True, data=watchlist, total=len(watchlist))


@router.post("", response_model=WatchListItemResponse)
async def add_to_watchlist(
    request: AddWatchListRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    添加股票到监控列表
    """
    # 检查是否已存在
    existing = (
        db.query(WatchList)
        .filter(
            WatchList.user_id == current_user.id,
            WatchList.stock_code == request.stock_code,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="该股票已在监控列表中"
        )

    # 创建监控项
    watchlist_item = WatchList(
        user_id=current_user.id,
        stock_code=request.stock_code,
        stock_name=request.stock_name,
        alert_enabled=request.alert_enabled,
        alert_threshold=request.alert_threshold,
    )

    db.add(watchlist_item)
    db.commit()
    db.refresh(watchlist_item)

    return WatchListItemResponse(success=True, data=watchlist_item)


@router.put("/{item_id}", response_model=WatchListItemResponse)
async def update_watchlist_item(
    item_id: int,
    request: UpdateWatchListRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    更新监控列表项
    """
    # 查询监控项
    watchlist_item = (
        db.query(WatchList)
        .filter(WatchList.id == item_id, WatchList.user_id == current_user.id)
        .first()
    )

    if not watchlist_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="监控项不存在"
        )

    # 更新字段
    if request.stock_name is not None:
        watchlist_item.stock_name = request.stock_name
    if request.alert_enabled is not None:
        watchlist_item.alert_enabled = request.alert_enabled
    if request.alert_threshold is not None:
        watchlist_item.alert_threshold = request.alert_threshold

    db.commit()
    db.refresh(watchlist_item)

    return WatchListItemResponse(success=True, data=watchlist_item)


@router.delete("/{item_id}", response_model=DeleteResponse)
async def delete_watchlist_item(
    item_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    删除监控列表项
    """
    # 查询监控项
    watchlist_item = (
        db.query(WatchList)
        .filter(WatchList.id == item_id, WatchList.user_id == current_user.id)
        .first()
    )

    if not watchlist_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="监控项不存在"
        )

    db.delete(watchlist_item)
    db.commit()

    return DeleteResponse(success=True, message="删除成功")
