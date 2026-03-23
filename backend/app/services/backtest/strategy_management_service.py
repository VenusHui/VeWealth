"""回测策略管理（列表/详情）服务"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.backtest import BacktestRun
from app.services.backtest.registry import (
    STRATEGY_REGISTRY,
    get_strategy_source_path,
    get_strategy_validation,
)

CORE_METHODS = [
    "param_schema",
    "required_columns",
    "default_policy_profile",
    "generate_candidates",
]


class BacktestStrategyManagementService:
    def _extract_latest_backtest(
        self, db: Session, strategy_id: str
    ) -> dict[str, Any] | None:
        row = (
            db.query(BacktestRun)
            .filter(BacktestRun.strategy_id == strategy_id)
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .first()
        )
        if not row:
            return None

        summary = row.summary or {}
        return {
            "run_id": row.id,
            "run_name": row.name,
            "created_at": row.created_at,
            "annual_return": summary.get("annual_return"),
            "total_return": summary.get("total_return"),
            "sharpe": summary.get("sharpe"),
            "max_drawdown": summary.get("max_drawdown"),
        }

    def _read_source(
        self, source_path: str | None
    ) -> tuple[str | None, int, str | None]:
        if not source_path:
            return None, 0, None

        path = Path(source_path)
        if not path.exists() or not path.is_file():
            return None, 0, None

        text = path.read_text(encoding="utf-8")
        return text, len(text.splitlines()), path.as_posix()

    def _extract_core_snippet(self, full_source: str, strategy_class_name: str) -> str:
        try:
            tree = ast.parse(full_source)
            class_node = next(
                (
                    node
                    for node in tree.body
                    if isinstance(node, ast.ClassDef)
                    and node.name == strategy_class_name
                ),
                None,
            )
            if not class_node:
                return full_source

            lines = full_source.splitlines()
            snippets: list[str] = []

            # class 头部
            class_start = class_node.lineno - 1
            class_header_end = class_start
            while class_header_end < len(lines) and not lines[
                class_header_end
            ].strip().endswith(":"):
                class_header_end += 1
            snippets.extend(lines[class_start : min(class_header_end + 1, len(lines))])
            snippets.append("")

            for node in class_node.body:
                if isinstance(node, ast.FunctionDef) and node.name in CORE_METHODS:
                    start = node.lineno - 1
                    end = getattr(node, "end_lineno", node.lineno) - 1
                    snippets.extend(lines[start : end + 1])
                    snippets.append("")

            return "\n".join(snippets).strip() or full_source
        except Exception:
            return full_source

    def list_strategies(self, db: Session) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for strategy_id, strategy_cls in STRATEGY_REGISTRY.items():
            validation = get_strategy_validation(strategy_id)
            source_path = get_strategy_source_path(strategy_id)
            has_code = bool(source_path and Path(source_path).exists())
            last_modified_at = None
            if has_code:
                mtime = Path(source_path).stat().st_mtime
                last_modified_at = datetime.fromtimestamp(mtime)

            items.append(
                {
                    "strategy_id": strategy_id,
                    "name": strategy_cls.name,
                    "description": strategy_cls.description,
                    "usable": bool(validation.get("usable", False)),
                    "policy_profile": validation.get("policy_profile"),
                    "last_modified_at": last_modified_at,
                    "latest_backtest": self._extract_latest_backtest(db, strategy_id),
                    "has_code": has_code,
                }
            )

        items.sort(
            key=lambda x: (
                x.get("last_modified_at") or datetime.fromtimestamp(0),
                x.get("strategy_id") or "",
            ),
            reverse=True,
        )
        return items

    def get_strategy_detail(self, db: Session, strategy_id: str) -> dict[str, Any]:
        strategy_cls = STRATEGY_REGISTRY.get(strategy_id)
        if not strategy_cls:
            raise ValueError(f"未知策略: {strategy_id}")

        validation = get_strategy_validation(strategy_id)
        source_path = get_strategy_source_path(strategy_id)
        full_source, line_count, normalized_path = self._read_source(source_path)
        has_code = bool(full_source)

        last_modified_at = None
        if source_path and Path(source_path).exists():
            last_modified_at = datetime.fromtimestamp(Path(source_path).stat().st_mtime)

        core_snippet = (
            self._extract_core_snippet(full_source, strategy_cls.__name__)
            if full_source
            else None
        )

        return {
            "strategy_info": {
                "strategy_id": strategy_id,
                "name": strategy_cls.name,
                "description": strategy_cls.description,
                "usable": bool(validation.get("usable", False)),
                "policy_profile": validation.get("policy_profile"),
                "last_modified_at": last_modified_at,
                "latest_backtest": self._extract_latest_backtest(db, strategy_id),
                "has_code": has_code,
            },
            "latest_backtest": self._extract_latest_backtest(db, strategy_id),
            "code": {
                "language": "python",
                "source_path": normalized_path,
                "core_snippet": core_snippet,
                "full_source": full_source,
                "line_count": line_count,
            },
        }


backtest_strategy_management_service = BacktestStrategyManagementService()
