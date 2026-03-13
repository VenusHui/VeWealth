"""回测API路由"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.schemas.backtest import (
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestRunListResponse,
    BacktestRunDetailResponse,
    BacktestStrategiesResponse,
)
from app.services.backtest import backtest_service

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.get("/strategies", response_model=BacktestStrategiesResponse)
async def get_strategies(
    current_user: User = Depends(get_current_active_user),
):
    return BacktestStrategiesResponse(data=backtest_service.list_strategies())


@router.post("/run", response_model=BacktestRunResponse)
async def run_backtest(
    request: BacktestRunRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    try:
        result = backtest_service.run_backtest(
            request=request, current_user=current_user, db=db
        )
        return BacktestRunResponse(data=result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.get("/runs", response_model=BacktestRunListResponse)
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    runs, total = backtest_service.list_runs(
        current_user=current_user, db=db, limit=limit, offset=offset
    )
    return BacktestRunListResponse(data=runs, total=total)


@router.get("/runs/{run_id}", response_model=BacktestRunDetailResponse)
async def get_run(
    run_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    run = backtest_service.get_run(run_id=run_id, current_user=current_user, db=db)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="回测记录不存在"
        )
    return BacktestRunDetailResponse(data=run)
