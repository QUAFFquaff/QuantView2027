from pathlib import Path

import pandas as pd

from quantview.config import Settings
from quantview.data.manual_import import import_market_file
from quantview.data.repository import Repository


def test_manual_csv_import_is_normalized_and_persisted(tmp_path: Path):
    source = tmp_path / "manual.csv"
    pd.DataFrame(
        [
            {
                "contract_code": "C2701",
                "date": "2026-10-19",
                "open": 2200,
                "high": 2220,
                "low": 2190,
                "close": 2210,
                "settle": 2208,
                "volume": 100,
                "hold": 1000,
            },
            {
                "contract_code": "C2701",
                "date": "2026-10-20",
                "open": 2210,
                "high": 2230,
                "low": 2200,
                "close": 2220,
                "settle": 2218,
                "volume": 120,
                "hold": 1010,
            },
        ]
    ).to_csv(source, index=False)
    settings = Settings(
        root=tmp_path,
        app={
            "paths": {
                "database": "data/test.duckdb",
                "raw": "raw",
                "reports": "reports",
                "logs": "logs",
            }
        },
        products={
            "C": {
                "exchange": "DCE",
                "multiplier": 10,
                "price_tick": 1,
            }
        },
        spreads={},
    )
    result = import_market_file(settings, source)
    stored = Repository(settings.database_path).query(
        "SELECT trade_date, close, settlement, volume, open_interest FROM futures_daily ORDER BY trade_date"
    )
    assert result["accepted"] == 2
    assert result["rejected"] == 0
    assert stored["close"].tolist() == [2210.0, 2220.0]
    assert stored["open_interest"].tolist() == [1000, 1010]
