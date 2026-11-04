from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest

from quantview.funds.exposure import estimate, load_bundle, parse_bundle, save_bundle
from quantview.operations.clock import SHANGHAI

NOW = datetime(2026, 11, 4, 15, tzinfo=SHANGHAI)


def report(code, weight, published="2026-10-27", period="2026-09-30"):
    return {
        "code": code, "period_end": period, "published_on": published,
        "source_url": "https://example.org/report.pdf",
        "holdings": [{"security_id": "SSE:600000", "name": "示例证券", "weight": weight}],
    }


def bundle():
    return {
        "schema_version": 1, "as_of": "2026-11-04T12:00:00+08:00", "source": "测试",
        "securities_value_cny": 1000,
        "positions": [{"code": "560860", "market_value_cny": 400},
                      {"code": "512400", "market_value_cny": 200},
                      {"code": "159980", "market_value_cny": 100}],
        "disclosures": [
            report("560860", .1, "2026-08-01", "2026-06-30"),
            report("560860", .2), report("512400", .05),
            report("560860", .5, "2026-11-04"),  # Not proven public at 12:00.
        ],
    }


def test_partial_lookthrough_reconciles_and_excludes_same_day_unproven_disclosure():
    view = estimate(parse_bundle(json.dumps(bundle()).encode(), now=NOW))
    equity = view["equities"]["SSE:600000"]
    assert equity["funds"] == {"560860": Decimal(80), "512400": Decimal(10)}
    assert equity["value"] == view["known"] == Decimal(90)
    assert view["outside"] == Decimal(300)
    assert sum(view["groups"].values()) == view["tracked"] == Decimal(700)
    assert view["funds"][2]["status"] == "不适用股票穿透"
    assert sum(row["remaining"] for row in view["funds"]) + view["known"] + view["outside"] == view["total"]


@pytest.mark.parametrize("change, match", [
    (lambda b: b["positions"].append(b["positions"][0]), "重复"),
    (lambda b: b.update(securities_value_cny=500), "超过"),
    (lambda b: b["disclosures"].append(report("159980", .2)), "股票型"),
    (lambda b: b["disclosures"].append(report("512400", .2)), "重复披露"),
    (lambda b: b["disclosures"][0]["holdings"][0].update(weight=20), "权重之和"),
    (lambda b: b.update(as_of="2026-11-04T12:00:00"), "时区"),
])
def test_invalid_input_cannot_create_fake_exposure(change, match):
    value = bundle()
    change(value)
    with pytest.raises(ValueError, match=match):
        parse_bundle(json.dumps(value).encode(), now=NOW)


def test_failed_import_leaves_local_snapshot_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr("quantview.funds.exposure.shanghai_now", lambda now=None: NOW)
    assert load_bundle(tmp_path) is None
    assert not (tmp_path / "data").exists()
    save_bundle(tmp_path, json.dumps(bundle()).encode())
    with pytest.raises(ValueError):
        save_bundle(tmp_path, b'{"as_of": "bad"}')
    assert load_bundle(tmp_path) == bundle()
