from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

from quantview.config import Settings
from quantview.data.normalization import normalize_daily
from quantview.data.raw_store import save_raw_snapshot
from quantview.data.repository import Repository
from quantview.data.validation import validate_bars
from quantview.domain.models import FetchResult
from quantview.domain.rules import parse_contract_code

CONTRACT_COLUMNS = ("contract_code", "symbol", "合约", "合约代码")


def import_market_file(settings: Settings, path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path)
    elif suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(path)
    else:
        raise ValueError("Manual import supports .csv, .xlsx, and .xls")
    contract_column = next((name for name in CONTRACT_COLUMNS if name in frame.columns), None)
    if contract_column is None:
        raise ValueError(f"Manual file must contain one of: {', '.join(CONTRACT_COLUMNS)}")
    repository = Repository(settings.database_path)
    repository.initialize()
    accepted_count = rejected_count = 0
    imported_contracts: list[str] = []
    for raw_symbol, group in frame.groupby(contract_column):
        symbol = str(raw_symbol).strip().upper()
        product = "".join(character for character in symbol if character.isalpha())
        if product not in settings.products:
            continue
        config = settings.products[product]
        instrument = parse_contract_code(
            symbol,
            exchange=config["exchange"],
            multiplier=config.get("multiplier"),
            price_tick=config.get("price_tick"),
        )
        repository.upsert_instruments([instrument])
        result = FetchResult(
            source="manual_file",
            source_symbol=symbol,
            fetched_at=datetime.now().astimezone(),
            raw_batch_id=uuid.uuid4().hex,
            frame=group.drop(columns=[contract_column]).copy(),
            metadata={"endpoint": "manual_import", "file": str(path.resolve())},
        )
        save_raw_snapshot(result, settings.path("raw"))
        accepted, rejected = validate_bars(normalize_daily(result, instrument))
        repository.upsert_bars(accepted)
        accepted_count += len(accepted)
        rejected_count += len(rejected)
        imported_contracts.append(symbol)
    repository.update_daily_changes()
    return {
        "file": str(path.resolve()),
        "contracts": imported_contracts,
        "accepted": accepted_count,
        "rejected": rejected_count,
    }
