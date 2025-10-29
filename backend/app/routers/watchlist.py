"""
监控列表路由
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.watchlist import WatchList
from app.schemas.watchlist import (
    AddWatchListRequest, UpdateWatchListRequest,
    WatchListResponse, WatchListItemResponse, DeleteResponse
)

router = APIRouter(prefix="/watchlist", tags=["监控列表"])


@router.get("", response_model=WatchListResponse)
async def get_watchlist(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    获取当前用户的监控列表
    """
    watchlist = db.query(WatchList).filter(
        WatchList.user_id == current_user.id
    ).order_by(WatchList.created_at.desc()).all()
    
    return WatchListResponse(
        success=True,
        data=watchlist,
        total=len(watchlist)
    )


@router.post("", response_model=WatchListItemResponse)
async def add_to_watchlist(
    request: AddWatchListRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    添加股票到监控列表
    """
    # 检查是否已存在
    existing = db.query(WatchList).filter(
        WatchList.user_id == current_user.id,
        WatchList.stock_code == request.stock_code
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该股票已在监控列表中"
        )
    
    # 创建监控项
    watchlist_item = WatchList(
        user_id=current_user.id,
        stock_code=request.stock_code,
        stock_name=request.stock_name,
        alert_enabled=request.alert_enabled,
        alert_threshold=request.alert_threshold
    )
    
    db.add(watchlist_item)
    db.commit()
    db.refresh(watchlist_item)
    
    return WatchListItemResponse(
        success=True,
        data=watchlist_item
    )


@router.put("/{item_id}", response_model=WatchListItemResponse)
async def update_watchlist_item(
    item_id: int,
    request: UpdateWatchListRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    更新监控列表项
    """
    # 查询监控项
    watchlist_item = db.query(WatchList).filter(
        WatchList.id == item_id,
        WatchList.user_id == current_user.id
    ).first()
    
    if not watchlist_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="监控项不存在"
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
    
    return WatchListItemResponse(
        success=True,
        data=watchlist_item
    )


@router.delete("/{item_id}", response_model=DeleteResponse)
async def delete_watchlist_item(
    item_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    删除监控列表项
    """
    # 查询监控项
    watchlist_item = db.query(WatchList).filter(
        WatchList.id == item_id,
        WatchList.user_id == current_user.id
    ).first()
    
    if not watchlist_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="监控项不存在"
        )
    
    db.delete(watchlist_item)
    db.commit()
    
    return DeleteResponse(
        success=True,
        message="删除成功"
    )

