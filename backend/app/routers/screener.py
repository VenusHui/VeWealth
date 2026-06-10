"""Screener API routes — stock screening by strategy signals."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import get_current_active_user
from app.models.user import User
from app.schemas.screener import ScanRequest, ScanResponse, ScanListResponse
from app.services.screener_service import screener_service

router = APIRouter(prefix="/screener", tags=["screener"])


@router.post("/scan", response_model=ScanResponse, status_code=status.HTTP_201_CREATED)
async def start_scan(
    request: ScanRequest,
    current_user: User = Depends(get_current_active_user),
):
    """启动一次全市场策略选股扫描。"""
    try:
        result = screener_service.start_scan(
            strategy_id=request.strategy_id,
            params=request.strategy_params,
            boards=request.boards,
            exclude_st=request.exclude_st,
            user_id=current_user.id,
        )
        return ScanResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.get("/scans/{scan_id}", response_model=ScanResponse)
async def get_scan(
    scan_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """获取扫描状态和结果。扫描中时返回已发现的部分结果。"""
    result = screener_service.get_scan(scan_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="扫描记录不存在"
        )
    return ScanResponse(**result)


@router.get("/scans", response_model=ScanListResponse)
async def list_scans(
    current_user: User = Depends(get_current_active_user),
):
    """获取当前用户的扫描历史列表。"""
    items = screener_service.list_scans(current_user.id)
    return ScanListResponse(data=items, total=len(items))
