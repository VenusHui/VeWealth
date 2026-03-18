#!/usr/bin/env python3
"""刷新A股代码清单并同步 security_universe 维表。

用法：
  python backend/scripts/refresh_a_share_symbols.py
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import akshare as ak

# 允许从仓库根目录直接执行: python backend/scripts/refresh_a_share_symbols.py
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.models.security_universe import SecurityUniverse


def detect_market(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def detect_board(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("300", "301")):
        return "gem"
    if code.startswith("688"):
        return "star"
    if code.startswith(("4", "8")):
        return "bse"
    return "main"


def is_st_name(name: str) -> bool:
    value = (name or "").upper()
    return "ST" in value


def main() -> None:
    df = ak.stock_info_a_code_name()
    if df is None or df.empty or "code" not in df.columns:
        raise RuntimeError("未获取到有效的A股代码列表")

    name_col = "name" if "name" in df.columns else ("名称" if "名称" in df.columns else None)
    records = []
    for _, row in df.iterrows():
        code = str(row["code"]).strip()
        if not code:
            continue
        code = code.zfill(6)
        name = str(row[name_col]).strip() if name_col else ""
        records.append((code, name))

    # 1) 继续维护静态 txt（兜底/排障）
    codes = sorted({code for code, _ in records})
    target = Path(__file__).resolve().parents[1] / "data" / "a_share_symbols.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("w", encoding="utf-8") as f:
        f.write("# 静态A股代码清单（兜底用途）\n")
        f.write(f"# 自动生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("# 数据源: akshare.stock_info_a_code_name\n")
        f.write("# 格式：每行一个6位股票代码，支持注释行(#)\n\n")
        for code in codes:
            f.write(f"{code}\n")

    # 2) 同步 security_universe 维表
    db = SessionLocal()
    try:
        existing = {
            item.stock_code: item
            for item in db.query(SecurityUniverse).all()
        }

        seen_codes = set()
        for code, name in records:
            seen_codes.add(code)
            row = existing.get(code)
            payload = {
                "stock_name": name or None,
                "market": detect_market(code),
                "board": detect_board(code),
                "is_st": is_st_name(name),
                "is_active": True,
            }
            if row:
                for key, val in payload.items():
                    setattr(row, key, val)
            else:
                db.add(SecurityUniverse(stock_code=code, **payload))

        # 不在最新列表中的标记为 inactive
        for code, row in existing.items():
            if code not in seen_codes:
                row.is_active = False

        db.commit()
    finally:
        db.close()

    print(f"✅ 已刷新 {target}，股票数量: {len(codes)}，并同步 security_universe")


if __name__ == "__main__":
    main()
