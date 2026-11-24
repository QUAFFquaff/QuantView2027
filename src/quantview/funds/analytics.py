from __future__ import annotations

from decimal import Decimal


def snapshot_difference(selected: dict, baseline: dict) -> dict:
    """Recorded value differences only: no inference of trades or investment P&L."""
    left = {row["code"]: Decimal(str(row["market_value_cny"])) for row in baseline["positions"]}
    right = {row["code"]: Decimal(str(row["market_value_cny"])) for row in selected["positions"]}
    return {"total_change": Decimal(str(selected["securities_value_cny"])) - Decimal(str(baseline["securities_value_cny"])),
            "rows": [{"code": code, "baseline": left.get(code, Decimal(0)), "selected": right.get(code, Decimal(0)),
                      "change": right.get(code, Decimal(0)) - left.get(code, Decimal(0)),
                      "state": "新增列示" if code not in left else "不再列示" if code not in right else "继续列示"}
                     for code in sorted(set(left) | set(right))]}
