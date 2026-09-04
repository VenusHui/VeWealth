"""Point-in-time 股票池解析服务。

职责：
- ``capture_snapshot``：把当前 ``security_universe`` 维表（静态清单兜底）落成某个
  日期的股票池快照，让 ST / 板块 / 上市退市信息随时间被记录。
- ``get_universe_as_of``：按 ``as_of`` 日期解析股票池，返回按日期生效的成员列表，
  并携带点状态语义（是否用了快照、ST 过滤是否真正生效、是否点状态），避免调用方
  在"看起来成功"的降级路径上误判。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.security_universe import SecurityUniverse
from app.models.universe_snapshot import UniverseSnapshot

_ALLOWED_BOARDS = {"main", "gem", "star", "bse"}
_DEFAULT_BOARDS = ["main"]


@dataclass
class UniversePool:
    """按 as_of 解析出的股票池结果。

    source: snapshot（用了带日期的快照）/ current_universe（降级到当前维表）/
            empty（维表与静态清单均为空）。
    snapshot_date: 实际采用快照的日期；None 表示无快照、降级到当前维表。
    st_filter_effective: exclude_st 是否真的反映在返回名单上。
    st_point_in_time: ST 过滤是否来自带日期的历史快照（False 则表示用的是当前状态）。
    """

    symbols: list[str]
    as_of: date
    source: str
    snapshot_date: date | None
    st_filter_effective: bool
    st_point_in_time: bool
    warning: str | None = None


def _coerce_date(value: str | date | datetime | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _normalize_boards(boards: Iterable[str] | None) -> list[str]:
    allowed = [str(b).strip().lower() for b in (boards or list(_DEFAULT_BOARDS))]
    return [b for b in allowed if b in _ALLOWED_BOARDS] or list(_DEFAULT_BOARDS)


def _detect_board(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("300", "301")):
        return "gem"
    if code.startswith("688"):
        return "star"
    if code.startswith(("4", "8")):
        return "bse"
    return "main"


def _detect_market(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def _load_static_codes() -> list[str]:
    """从静态清单读取 A 股代码（仅当维表为空时兜底），失败返回 []。"""
    try:
        backend_root = Path(__file__).resolve().parents[2]  # backend/
        static_file = backend_root / "data" / "a_share_symbols.txt"
        if not static_file.exists():
            return []
        codes: list[str] = []
        with static_file.open("r", encoding="utf-8") as f:
            for line in f:
                code = line.strip()
                if not code or code.startswith("#"):
                    continue
                if code.isdigit() and len(code) <= 6:
                    codes.append(code.zfill(6))
        return list(dict.fromkeys(codes))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Snapshot capture
# ---------------------------------------------------------------------------


def capture_snapshot(
    db: Session, as_of: str | date | None = None, source: str = "universe_refresh"
) -> int:
    """把当前维表（静态清单兜底）落成某日的股票池快照，返回写入/更新的行数。"""
    snapshot_date = _coerce_date(as_of)
    universe_rows = db.query(SecurityUniverse).all()

    existing = {
        (snapshot_date, str(r.stock_code).zfill(6)): r
        for r in db.query(UniverseSnapshot)
        .filter(UniverseSnapshot.snapshot_date == snapshot_date)
        .all()
    }

    seen: set[str] = set()
    count = 0
    for row in universe_rows:
        code = str(row.stock_code).zfill(6)
        seen.add(code)
        payload = {
            "stock_name": row.stock_name,
            "market": row.market,
            "board": row.board or _detect_board(code),
            "is_st": bool(row.is_st),
            "is_active": bool(row.is_active),
            "list_date": row.list_date,
            "delist_date": row.delist_date,
        }
        if _upsert_snapshot(db, existing, snapshot_date, code, payload, source):
            count += 1

    # 维表为空/缺漏时用静态清单兜底（ST 无法可靠识别，按 False 记录）
    if not universe_rows:
        for code in _load_static_codes():
            if code in seen:
                continue
            payload = {
                "stock_name": None,
                "market": _detect_market(code),
                "board": _detect_board(code),
                "is_st": False,
                "is_active": True,
                "list_date": None,
                "delist_date": None,
            }
            if _upsert_snapshot(db, existing, snapshot_date, code, payload, "static"):
                count += 1

    db.commit()
    return count


def _upsert_snapshot(
    db: Session,
    existing: dict[tuple[date, str], UniverseSnapshot],
    snapshot_date: date,
    code: str,
    payload: dict,
    source: str,
) -> bool:
    existing_row = existing.get((snapshot_date, code))
    if existing_row is not None:
        for key, value in payload.items():
            setattr(existing_row, key, value)
        existing_row.source = source
        return True
    db.add(
        UniverseSnapshot(
            snapshot_date=snapshot_date, stock_code=code, source=source, **payload
        )
    )
    return True


# ---------------------------------------------------------------------------
# Point-in-time resolution
# ---------------------------------------------------------------------------


def get_universe_as_of(
    db: Session,
    as_of: str | date,
    boards: Iterable[str] | None = None,
    exclude_st: bool = True,
    limit: int | None = None,
) -> UniversePool:
    """解析 as_of 时刻的股票池。

    优先使用 ``snapshot_date <= as_of`` 的最近快照（点状态）；无快照时降级到当前
    ``security_universe`` 维表并加以说明；维表也为空时返回空并标注 source=empty。
    """
    as_of = _coerce_date(as_of)
    norm_boards = _normalize_boards(boards)

    latest_snapshot_date = (
        db.query(func.max(UniverseSnapshot.snapshot_date))
        .filter(UniverseSnapshot.snapshot_date <= as_of)
        .scalar()
    )

    if latest_snapshot_date is not None:
        symbols = _query_snapshot(
            db, latest_snapshot_date, as_of, norm_boards, exclude_st, limit
        )
        return UniversePool(
            symbols=symbols,
            as_of=as_of,
            source="snapshot",
            snapshot_date=latest_snapshot_date,
            st_filter_effective=exclude_st,
            st_point_in_time=True,
            warning=None,
        )

    symbols, st_effective = _query_current_universe(
        db, as_of, norm_boards, exclude_st, limit
    )
    if symbols:
        warning = (
            None
            if not exclude_st
            else (
                f"数据库尚无 {as_of.isoformat()} 或更早的股票池快照，ST 过滤使用当前 "
                "security_universe 状态（非回测时点状态，可能有幸存者偏差）"
            )
        )
        return UniversePool(
            symbols=symbols,
            as_of=as_of,
            source="current_universe",
            snapshot_date=None,
            st_filter_effective=st_effective,
            st_point_in_time=False,
            warning=warning,
        )

    return UniversePool(
        symbols=[],
        as_of=as_of,
        source="empty",
        snapshot_date=None,
        st_filter_effective=False,
        st_point_in_time=False,
        warning="security_universe 为空，无法解析股票池",
    )


def _query_snapshot(
    db: Session,
    snapshot_date: date,
    as_of: date,
    boards: list[str],
    exclude_st: bool,
    limit: int | None,
) -> list[str]:
    query = db.query(UniverseSnapshot.stock_code).filter(
        UniverseSnapshot.snapshot_date == snapshot_date
    )
    if boards:
        query = query.filter(UniverseSnapshot.board.in_(boards))
    if exclude_st:
        query = query.filter(UniverseSnapshot.is_st.is_(False))
    query = query.filter(
        or_(UniverseSnapshot.list_date.is_(None), UniverseSnapshot.list_date <= as_of)
    )
    query = query.filter(
        or_(
            UniverseSnapshot.delist_date.is_(None),
            UniverseSnapshot.delist_date > as_of,
        )
    )
    query = query.order_by(UniverseSnapshot.stock_code.asc())
    if limit:
        query = query.limit(limit)
    return [str(row[0]).zfill(6) for row in query.all() if row[0]]


def _query_current_universe(
    db: Session,
    as_of: date,
    boards: list[str],
    exclude_st: bool,
    limit: int | None,
) -> tuple[list[str], bool]:
    """从当前维表解析股票池（无历史快照时的降级路径）。"""
    query = db.query(SecurityUniverse.stock_code).filter(
        SecurityUniverse.is_active.is_(True)
    )
    if boards:
        query = query.filter(SecurityUniverse.board.in_(boards))
    if exclude_st:
        query = query.filter(SecurityUniverse.is_st.is_(False))
    query = query.filter(
        or_(SecurityUniverse.list_date.is_(None), SecurityUniverse.list_date <= as_of)
    )
    query = query.filter(
        or_(
            SecurityUniverse.delist_date.is_(None),
            SecurityUniverse.delist_date > as_of,
        )
    )
    query = query.order_by(SecurityUniverse.stock_code.asc())
    if limit:
        query = query.limit(limit)
    rows = [str(row[0]).zfill(6) for row in query.all() if row[0]]
    # exclude_st 已经在 is_st=False 上生效；是否点状态由调用方经 warning 说明
    return rows, exclude_st
