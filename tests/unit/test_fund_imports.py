from __future__ import annotations

import io
import json
from datetime import datetime

import pandas as pd
import pytest
from openpyxl import Workbook

from quantview.funds.exposure import bundle_history, bundle_revision, load_bundle, save_bundle
from quantview.funds.imports import (
    CODE,
    VALUE,
    build_table_bundle,
    positions_from_table,
    read_table,
)
from quantview.metals.models import MetalsSettings
from quantview.metals.service import MetalsService
from quantview.operations.clock import SHANGHAI

NOW = datetime(2026, 11, 24, 12, tzinfo=SHANGHAI)


def snapshot(amount=100):
    return build_table_bundle(pd.DataFrame([{CODE: "518880", VALUE: str(amount)}]),
                              as_of=NOW, total=1000, source="synthetic fixture", now=NOW)


def raw(bundle):
    return json.dumps(bundle).encode()


def test_csv_encoding_mapping_preserves_codes_and_reports_row_errors():
    frame = read_table("证券代码,市值\n518880,100.25\n159920,20\n".encode("gb18030"), "fixture.csv")
    assert positions_from_table(frame, "证券代码", "市值") == [
        {"code": "518880", "market_value_cny": 100.25}, {"code": "159920", "market_value_cny": 20.0}]
    frame.loc[1, "市值"] = "=1+2"
    with pytest.raises(ValueError, match="第 3 行"):
        positions_from_table(frame, "证券代码", "市值")


@pytest.mark.parametrize("row", [
    {CODE: "518880", VALUE: "NaN"}, {CODE: "518880", VALUE: "0"},
    {CODE: "518880.0", VALUE: "100"}, {CODE: "", VALUE: "100"},
])
def test_bad_table_values_cannot_be_saved(row):
    with pytest.raises(ValueError):
        positions_from_table(pd.DataFrame([row]))


def test_duplicate_and_over_total_are_rejected():
    with pytest.raises(ValueError, match="重复"):
        positions_from_table(pd.DataFrame([{CODE: "518880", VALUE: "1"}] * 2))
    with pytest.raises(ValueError, match="超过"):
        snapshot(1001)
    with pytest.raises(ValueError, match="表头"):
        read_table(b"code,code\n518880,1", "fixture.csv")
    with pytest.raises(ValueError, match="1 MiB"):
        read_table(b"x" * (1024 * 1024 + 1), "fixture.csv")


def test_xlsx_reads_values_but_refuses_formulas():
    with pytest.raises(ValueError, match="无法读取"):
        read_table(b"broken workbook", "fixture.xlsx")
    book = Workbook()
    sheet = book.active
    sheet.append([CODE, VALUE])
    sheet.append([518880, 100.25])
    buffer = io.BytesIO()
    book.save(buffer)
    assert positions_from_table(read_table(buffer.getvalue(), "fixture.xlsx"))[0]["code"] == "518880"
    sheet["B2"] = "=100+1"
    buffer = io.BytesIO()
    book.save(buffer)
    with pytest.raises(ValueError, match="公式"):
        read_table(buffer.getvalue(), "fixture.xlsx")


def test_versions_conflict_detection_and_failed_publish_leave_no_draft(tmp_path, monkeypatch):
    monkeypatch.setattr("quantview.funds.exposure.shanghai_now", lambda now=None: now or NOW)
    first, second = snapshot(), snapshot(200)
    initial = bundle_revision(None)
    save_bundle(tmp_path, raw(first), expected_revision=initial)
    with pytest.raises(ValueError, match="其他操作"):
        save_bundle(tmp_path, raw(second), expected_revision=initial)
    save_bundle(tmp_path, raw(second), expected_revision=bundle_revision(first))
    save_bundle(tmp_path, raw(second))
    assert len(bundle_history(tmp_path)) == 2
    original_atomic = __import__("quantview.funds.exposure", fromlist=["_atomic"])._atomic

    def interrupted(path, value):
        if path.name == "fund_exposure.json":
            raise OSError("simulated publication failure")
        original_atomic(path, value)

    monkeypatch.setattr("quantview.funds.exposure._atomic", interrupted)
    with pytest.raises(OSError):
        save_bundle(tmp_path, raw(snapshot(300)))
    assert load_bundle(tmp_path) == second
    assert len(bundle_history(tmp_path)) == 2


def test_watchlist_conflicts_do_not_overwrite_new_entries(tmp_path):
    service = MetalsService(MetalsSettings(tmp_path))
    original = [{"code": "518880", "kind": "ETF", "metal": "gold"}]
    service.save_watchlist(original)
    service.append_watchlist([{"code": "159920", "kind": "ETF", "metal": "unknown"}])
    with pytest.raises(ValueError, match="其他操作"):
        service.save_watchlist([], expected=original)
    assert len(service.watchlist()) == 2
