from __future__ import annotations

import json
from datetime import date

import pandas as pd

from quantview.domain.models import Instrument


def assess_completeness(
    target: date,
    instruments: list[Instrument],
    target_bars: pd.DataFrame,
    products: dict,
    *,
    min_contracts: int = 2,
    universe_sources: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Check the target-day cross section against the run's contract universe.

    This checks known contracts, not the accuracy of upstream contract discovery.
    Valid persisted target-day bars may be reused on a rerun.
    """
    if min_contracts < 1:
        raise ValueError("min_contracts must be positive")
    rows = target_bars.copy()
    if not rows.empty:
        rows = rows.loc[
            (pd.to_datetime(rows["trade_date"]).dt.date == target)
            & (rows["quality_status"] != "rejected")
        ]
    summaries = {}
    for product in products:
        universe_source = (universe_sources or {}).get(product, "provided")
        coverage_warnings = (
            ["contract_universe_unverified"] if universe_source == "local_candidates" else []
        )
        expected = {
            item.instrument_id: item.contract_code
            for item in instruments
            if item.product == product
            and (item.listed_date is None or item.listed_date <= target)
            and (item.last_trade_date is None or item.last_trade_date >= target)
        }
        available = rows.loc[rows["product"] == product] if not rows.empty else rows
        present_ids = set(available["instrument_id"]) if not available.empty else set()
        missing = sorted(code for key, code in expected.items() if key not in present_ids)
        warnings = []
        for row in available.to_dict("records"):
            flags = row["quality_flags"]
            if isinstance(flags, str):
                flags = json.loads(flags)
            if flags:
                warnings.append({"contract": row["contract_code"], "flags": list(flags)})
        status = "complete"
        if not present_ids:
            status = "missing"
        elif (
            not expected or missing or len(present_ids & expected.keys()) < min_contracts
            or coverage_warnings
        ):
            status = "partial"
        summaries[product] = {
            "status": status,
            "expected_contracts": sorted(expected.values()),
            "available_contracts": sorted(available["contract_code"].unique()) if not available.empty else [],
            "missing_contracts": missing,
            "minimum_contracts": min_contracts,
            "universe_source": universe_source,
            "coverage_warnings": coverage_warnings,
            "sources": sorted(available["source"].unique()) if not available.empty else [],
            "quality_warnings": warnings,
        }
    return summaries
