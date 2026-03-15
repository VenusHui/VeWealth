#!/usr/bin/env python3
"""刷新静态A股代码清单。

用法：
  python backend/scripts/refresh_a_share_symbols.py
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import akshare as ak


def main() -> None:
    df = ak.stock_info_a_code_name()
    if df is None or df.empty or "code" not in df.columns:
        raise RuntimeError("未获取到有效的A股代码列表")

    codes = sorted({str(c).zfill(6) for c in df["code"].astype(str).tolist() if str(c).strip()})

    target = Path(__file__).resolve().parents[1] / "data" / "a_share_symbols.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("w", encoding="utf-8") as f:
        f.write("# 静态A股代码清单（兜底用途）\n")
        f.write(f"# 自动生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("# 数据源: akshare.stock_info_a_code_name\n")
        f.write("# 格式：每行一个6位股票代码，支持注释行(#)\n\n")
        for code in codes:
            f.write(f"{code}\n")

    print(f"✅ 已刷新 {target}，股票数量: {len(codes)}")


if __name__ == "__main__":
    main()
