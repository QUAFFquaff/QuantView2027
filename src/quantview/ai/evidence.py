from __future__ import annotations

import hashlib
import json
from datetime import date

import pandas as pd


def _number(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def build_evidence_pack(
    trade_date: date,
    product: str,
    product_row: pd.Series,
    spreads: pd.DataFrame,
    quality_summary: dict,
) -> dict:
    evidence = [
        {
            "evidence_id": f"{product}-trend",
            "category": "trend",
            "main_contract": product_row.get("main_contract"),
            "main_settlement": _number(product_row.get("main_settlement")),
            "return_5d": _number(product_row.get("return_5d")),
            "return_20d": _number(product_row.get("return_20d")),
            "atr_20": _number(product_row.get("atr_20")),
            "main_changed": bool(product_row.get("main_changed", False)),
        },
        {
            "evidence_id": f"{product}-volume-oi",
            "category": "volume_open_interest",
            "total_volume": _number(product_row.get("total_volume")),
            "total_volume_change": _number(product_row.get("total_volume_change")),
            "total_open_interest": _number(product_row.get("total_open_interest")),
            "total_oi_change": _number(product_row.get("total_oi_change")),
            "main_volume_share": _number(product_row.get("main_volume_share")),
            "main_oi_share": _number(product_row.get("main_oi_share")),
            "price_oi_regime": product_row.get("price_oi_regime"),
            "interpretation_limit": "Total open interest does not identify which side initiated trades.",
        },
    ]
    product_spreads = spreads.loc[spreads["product"] == product].copy() if not spreads.empty else spreads
    for _index, row in product_spreads.sort_values(["pair_type", "near_contract", "far_contract"]).iterrows():
        evidence.append(
            {
                "evidence_id": f"{product}-spread-{row['near_contract']}-{row['far_contract']}-{row['pair_type']}",
                "category": "calendar_spread",
                "near_contract": row["near_contract"],
                "far_contract": row["far_contract"],
                "pair_type": row["pair_type"],
                "settlement_spread": _number(row["settlement_spread"]),
                "spread_change": _number(row["spread_change"]),
                "zscore_20": _number(row["zscore_20"]),
                "zscore_60": _number(row["zscore_60"]),
                "percentile_60": _number(row["percentile_60"]),
                "liquidity_status": row["liquidity_status"],
            }
        )
    pack = {
        "trade_date": trade_date.isoformat(),
        "product": product,
        "quality": quality_summary,
        "evidence": evidence,
    }
    canonical = json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    pack["input_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return pack

