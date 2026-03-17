"""回测API路由"""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
    BacktestJobCreateResponse,
    BacktestJobListResponse,
    BacktestJobDetailResponse,
    BacktestRunOverviewResponse,
    BacktestRunTradesResponse,
    BacktestRunRoundsResponse,
    BacktestRunSnapshotsResponse,
    BacktestRunStrategyConfigResponse,
)
from app.services.backtest import backtest_service, backtest_job_manager

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _get_run_or_404(run_id: int, current_user: User, db: Session):
    run = backtest_service.get_run(run_id=run_id, current_user=current_user, db=db)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="回测记录不存在"
        )
    return run


def _get_job_or_404(job_id: str, current_user: User):
    job = backtest_job_manager.get_job(job_id, current_user.id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return job


def _build_csv_response(filename: str, fieldnames: list[str], rows: list[dict]):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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
    run = _get_run_or_404(run_id=run_id, current_user=current_user, db=db)
    return BacktestRunDetailResponse(data=run)


@router.get("/runs/{run_id}/overview", response_model=BacktestRunOverviewResponse)
async def get_run_overview(
    run_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    overview = backtest_service.get_run_overview(
        run_id=run_id, current_user=current_user, db=db
    )
    if not overview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="回测记录不存在"
        )
    return BacktestRunOverviewResponse(data=overview)


@router.get("/runs/{run_id}/trades", response_model=BacktestRunTradesResponse)
async def get_run_trades(
    run_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    _get_run_or_404(run_id=run_id, current_user=current_user, db=db)
    trades = backtest_service.get_run_trades(
        run_id=run_id, current_user=current_user, db=db
    )
    return BacktestRunTradesResponse(data=trades, total=len(trades))


@router.get("/runs/{run_id}/rounds", response_model=BacktestRunRoundsResponse)
async def get_run_rounds(
    run_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    _get_run_or_404(run_id=run_id, current_user=current_user, db=db)
    rounds = backtest_service.get_run_rounds(
        run_id=run_id, current_user=current_user, db=db
    )
    return BacktestRunRoundsResponse(data=rounds, total=len(rounds))


@router.get("/runs/{run_id}/snapshots", response_model=BacktestRunSnapshotsResponse)
async def get_run_snapshots(
    run_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    _get_run_or_404(run_id=run_id, current_user=current_user, db=db)
    snapshots = backtest_service.get_run_snapshots(
        run_id=run_id, current_user=current_user, db=db
    )
    return BacktestRunSnapshotsResponse(data=snapshots)


@router.get("/runs/{run_id}/trades/export")
async def export_run_trades_csv(
    run_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    _get_run_or_404(run_id=run_id, current_user=current_user, db=db)

    trades = backtest_service.get_run_trades(
        run_id=run_id, current_user=current_user, db=db
    )
    rows = [
        {
            "datetime": t.get("datetime"),
            "symbol": t.get("symbol"),
            "side": t.get("side"),
            "price": t.get("price"),
            "qty": t.get("qty"),
            "amount": t.get("amount"),
            "fee": t.get("fee"),
            "pnl": t.get("pnl"),
            "reason": t.get("reason"),
        }
        for t in trades
    ]

    filename = f"backtest_run_{run_id}_trades.csv"
    fieldnames = [
        "datetime",
        "symbol",
        "side",
        "price",
        "qty",
        "amount",
        "fee",
        "pnl",
        "reason",
    ]
    return _build_csv_response(filename=filename, fieldnames=fieldnames, rows=rows)


@router.get("/runs/{run_id}/rounds/export")
async def export_run_rounds_csv(
    run_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    _get_run_or_404(run_id=run_id, current_user=current_user, db=db)

    rounds = backtest_service.get_run_rounds(
        run_id=run_id, current_user=current_user, db=db
    )
    rows = [
        {
            "symbol": r.get("symbol"),
            "open_time": r.get("open_time"),
            "open_price": r.get("open_price"),
            "close_time": r.get("close_time"),
            "close_price": r.get("close_price"),
            "qty": r.get("qty"),
            "holding_days": r.get("holding_days"),
            "pnl_ratio": r.get("pnl_ratio"),
            "pnl_amount": r.get("pnl_amount"),
            "exit_reason": r.get("exit_reason"),
        }
        for r in rounds
    ]

    filename = f"backtest_run_{run_id}_rounds.csv"
    fieldnames = [
        "symbol",
        "open_time",
        "open_price",
        "close_time",
        "close_price",
        "qty",
        "holding_days",
        "pnl_ratio",
        "pnl_amount",
        "exit_reason",
    ]
    return _build_csv_response(filename=filename, fieldnames=fieldnames, rows=rows)


@router.get(
    "/runs/{run_id}/strategy-config", response_model=BacktestRunStrategyConfigResponse
)
async def get_run_strategy_config(
    run_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    config = backtest_service.get_run_strategy_config(
        run_id=run_id, current_user=current_user, db=db
    )
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="回测记录不存在"
        )
    return BacktestRunStrategyConfigResponse(data=config)


@router.post("/jobs", response_model=BacktestJobCreateResponse)
async def create_backtest_job(
    request: BacktestRunRequest,
    current_user: User = Depends(get_current_active_user),
):
    job = backtest_job_manager.create_job(request, current_user)
    return BacktestJobCreateResponse(data=job)


@router.get("/jobs", response_model=BacktestJobListResponse)
async def list_backtest_jobs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_active_user),
):
    jobs = backtest_job_manager.list_jobs(current_user.id, limit=limit, offset=offset)
    return BacktestJobListResponse(data=jobs)


@router.get("/jobs/{job_id}", response_model=BacktestJobDetailResponse)
async def get_backtest_job(
    job_id: str,
    current_user: User = Depends(get_current_active_user),
):
    job = _get_job_or_404(job_id=job_id, current_user=current_user)
    return BacktestJobDetailResponse(data=job)


@router.post("/jobs/{job_id}/cancel", response_model=BacktestJobDetailResponse)
async def cancel_backtest_job(
    job_id: str,
    current_user: User = Depends(get_current_active_user),
):
    _get_job_or_404(job_id=job_id, current_user=current_user)
    job = backtest_job_manager.cancel_job(job_id, current_user.id)
    return BacktestJobDetailResponse(data=job)


@router.post("/jobs/{job_id}/retry", response_model=BacktestJobDetailResponse)
async def retry_backtest_job(
    job_id: str,
    current_user: User = Depends(get_current_active_user),
):
    _get_job_or_404(job_id=job_id, current_user=current_user)
    job = backtest_job_manager.retry_job(job_id, current_user.id)
    return BacktestJobDetailResponse(data=job)
