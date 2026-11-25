from datetime import date

import pytest

from quantview.config import Settings
from quantview.domain.rules import parse_contract_code
from quantview.pipeline import DailyPipeline

TARGET = date(2026, 11, 13)


@pytest.fixture
def pipeline(tmp_path):
    settings = Settings(
        root=tmp_path,
        app={"paths": {"database": "test.duckdb"}},
        products={"LH": {
            "name": "生猪", "exchange": "DCE",
            "allowed_delivery_months": [1, 3, 5, 7, 9, 11],
        }},
        spreads={},
    )
    pipeline = DailyPipeline(settings)
    pipeline.initialize()
    pipeline.repository.upsert_instruments([
        parse_contract_code("LH2701"), parse_contract_code("LH2705"),
    ])
    return pipeline


@pytest.mark.parametrize("discovered,expected", [
    (["LH2403", "LH2405"], ["LH2701", "LH2705"]),
    (["LH2707", "LH2403", "LH2702", "C2705", "LH2901", "invalid"],
     ["LH2701", "LH2705", "LH2707"]),
])
def test_stale_or_mixed_discovery_preserves_valid_local_contracts(pipeline, discovered, expected):
    pipeline.adapters["akshare_sina"].discover_contracts = lambda *args: discovered

    instruments = pipeline.discover_instruments(TARGET)

    assert [item.contract_code for item in instruments] == expected
    assert pipeline.universe_sources == {"LH": "local_candidates"}
    assert pipeline.discovery_warnings["LH"]
    records = pipeline.repository.query("SELECT contract_code FROM instrument WHERE is_active")
    assert sorted(records.contract_code) == expected
    assert not pipeline.repository.query("SELECT * FROM instrument WHERE delivery_year=2024").shape[0]


def test_valid_discovery_replaces_active_set_and_resets_warnings(pipeline):
    pipeline.adapters["akshare_sina"].discover_contracts = lambda *args: ["LH2403"]
    pipeline.discover_instruments(TARGET)
    pipeline.adapters["akshare_sina"].discover_contracts = lambda *args: ["LH2701", "LH2707"]

    instruments = pipeline.discover_instruments(TARGET)

    assert [item.contract_code for item in instruments] == ["LH2701", "LH2707"]
    assert pipeline.universe_sources == {"LH": "live_discovery"}
    assert not pipeline.discovery_warnings
    records = pipeline.repository.query("SELECT contract_code FROM instrument WHERE is_active")
    assert sorted(records.contract_code) == ["LH2701", "LH2707"]
    assert len(pipeline.repository.query("SELECT * FROM instrument WHERE contract_code='LH2705'")) == 1


def test_stale_local_candidates_are_filtered_before_generating_current_months(pipeline):
    with pipeline.repository.connect() as connection:
        connection.execute("UPDATE instrument SET is_active=false")
    pipeline.repository.upsert_instruments([parse_contract_code("LH2403")])
    pipeline.adapters["akshare_sina"].discover_contracts = lambda *args: []

    instruments = pipeline.discover_instruments(TARGET)

    assert [item.contract_code for item in instruments] == [
        "LH2611", "LH2701", "LH2703", "LH2705", "LH2707", "LH2709", "LH2711",
    ]
    assert pipeline.universe_sources == {"LH": "local_candidates"}
