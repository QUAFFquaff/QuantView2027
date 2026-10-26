from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from quantview.data.repository import Repository
from quantview.operations.clock import shanghai_now


def _clean(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def _records(frame: pd.DataFrame) -> list[dict]:
    return [{key: _clean(value) for key, value in row.items()} for row in frame.to_dict("records")]


def _parse_json_columns(records: list[dict], columns: tuple[str, ...]) -> list[dict]:
    for record in records:
        for column in columns:
            value = record.get(column)
            if isinstance(value, str):
                try:
                    record[column] = json.loads(value)
                except json.JSONDecodeError:
                    pass
    return records


def render_daily_report(
    repository: Repository,
    root: Path,
    trade_date: date,
    run_meta: dict,
) -> Path:
    products = repository.query(
        "SELECT * FROM product_daily WHERE trade_date=? ORDER BY product", [trade_date]
    )
    spreads = repository.query(
        """
        SELECT * FROM calendar_spread_daily
        WHERE trade_date=? AND pair_type='main_second'
        ORDER BY product, near_contract, far_contract
        """,
        [trade_date],
    )
    ai = repository.query(
        """
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (PARTITION BY product ORDER BY created_at DESC) rn
            FROM ai_analysis WHERE trade_date=?
        ) WHERE rn=1 ORDER BY product
        """,
        [trade_date],
    )
    product_records = _records(products)
    spread_records = _records(spreads)
    ai_records = _parse_json_columns(
        _records(ai), ("bull_case", "bear_case", "key_evidence", "risk_flags", "invalidation_conditions")
    )
    if "ai_products" in run_meta:
        ai_records = [row for row in ai_records if row["product"] in run_meta["ai_products"]]
    ai_by_product = {row["product"]: row for row in ai_records}
    spreads_by_product: dict[str, list[dict]] = {}
    for row in spread_records:
        spreads_by_product.setdefault(row["product"], []).append(row)

    environment = Environment(
        loader=FileSystemLoader(root / "src" / "quantview" / "reports" / "templates"),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["pct"] = lambda value: "—" if value is None else f"{value:.2%}"
    environment.filters["num"] = lambda value: "—" if value is None else f"{value:,.2f}"
    environment.filters["integer"] = lambda value: "—" if value is None else f"{value:,.0f}"
    template = environment.get_template("daily_report.md.j2")
    content = template.render(
        trade_date=trade_date,
        generated_at=shanghai_now(),
        products=product_records,
        spreads_by_product=spreads_by_product,
        ai_by_product=ai_by_product,
        run=run_meta,
    )
    report_root = root / "reports" / "daily" / f"{trade_date:%Y}" / f"{trade_date:%m}"
    report_root.mkdir(parents=True, exist_ok=True)
    output = report_root / f"{trade_date.isoformat()}.md"
    temporary = output.with_suffix(".md.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(output)
    return output

