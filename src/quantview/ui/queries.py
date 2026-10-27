"""Read-only workbench queries. These views are mutable, not archived snapshots."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from quantview.data.repository import Repository


def json_value(value, default):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


class WorkbenchQueries:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.tables = set()
        if repository.path.exists():
            self.tables = set(repository.query(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            )["table_name"])

    def read(self, table: str, sql: str, parameters=None) -> pd.DataFrame:
        if table not in self.tables:
            return pd.DataFrame()
        return self.repository.query(sql, parameters)

    def dates(self) -> list[date]:
        values = set()
        for table, column in (("product_daily", "trade_date"), ("futures_daily", "trade_date"),
                              ("pipeline_run", "target_trade_date")):
            frame = self.read(table, f"SELECT DISTINCT {column} AS day FROM {table}")
            if not frame.empty:
                values.update(pd.to_datetime(frame["day"]).dt.date)
        return sorted(values, reverse=True)

    def runs(self, target=None):
        where = "WHERE target_trade_date=?" if target else ""
        return self.read("pipeline_run", f"SELECT * FROM pipeline_run {where} ORDER BY started_at DESC LIMIT 20",
                         [target] if target else [])

    def products(self, target):
        return self.read("product_daily", "SELECT * FROM product_daily WHERE trade_date=? ORDER BY product", [target])

    def product_history(self, product, target):
        return self.read("product_daily", "SELECT * FROM product_daily WHERE product=? AND trade_date<=? ORDER BY trade_date", [product, target])

    def bars(self, product, target):
        if "instrument" not in self.tables:
            return pd.DataFrame()
        return self.read("futures_daily", """
            SELECT f.*, i.product, i.contract_code, i.delivery_year, i.delivery_month
            FROM futures_daily f JOIN instrument i USING (instrument_id)
            WHERE i.product=? AND f.trade_date<=? AND f.quality_status!='rejected'
            ORDER BY f.trade_date, i.delivery_year, i.delivery_month
        """, [product, target])

    def spreads(self, product, target):
        return self.read("calendar_spread_daily", """
            SELECT * FROM calendar_spread_daily WHERE product=? AND trade_date<=?
            ORDER BY trade_date, near_contract, far_contract, pair_type
        """, [product, target])

    def analysis(self, target, stages, run_status):
        # Without immutable analysis references, only show products completed by
        # this run, and only analyses created during that run's time interval.
        runs = self.runs(target)
        completed = stages.get("ai", {}).get("completed_products", [])
        if runs.empty or run_status not in {"success", "partial"} or not completed:
            return pd.DataFrame()
        run = runs.iloc[0]
        if pd.isna(run["finished_at"]):
            return pd.DataFrame()
        frame = self.read("ai_analysis", """
            SELECT * FROM ai_analysis WHERE trade_date=? AND created_at BETWEEN ? AND ?
            QUALIFY row_number() OVER (PARTITION BY product ORDER BY created_at DESC)=1
            ORDER BY product
        """, [target, run["started_at"], run["finished_at"]])
        return frame.loc[frame["product"].isin(completed)] if not frame.empty else frame


def important_changes(products, completeness, bars_by_product):
    """Stable, factual changes, capped at five; never replace unknowns with zero."""
    events = []
    rows = {row["product"]: row for row in products.to_dict("records")}
    for product in sorted(bars_by_product):
        quality = completeness.get(product, {})
        row = rows.get(product)
        if row is None or quality.get("status", "unknown") != "complete":
            events.append((0, product, "覆盖", f"{product}：数据缺失或覆盖未确认；缺少合约：{'、'.join(quality.get('missing_contracts', [])) or '详见质量证据'}"))
        bars = bars_by_product[product]
        if row is None:
            continue
        target_bars = bars.loc[pd.to_datetime(bars["trade_date"]).dt.date == pd.Timestamp(row["trade_date"]).date()] if not bars.empty else bars
        flagged = target_bars.loc[target_bars["quality_flags"].map(lambda x: bool(json_value(x, [])))] if not target_bars.empty else target_bars
        if not flagged.empty:
            contracts = "、".join(flagged["contract_code"].tolist())
            events.append((0, product, "质量", f"{product}：{contracts} 有替代价或质量警告，请查看原始标记。"))
        if pd.notna(row.get("main_changed")) and row["main_changed"]:
            old, new = row["previous_main_contract"], row["main_contract"]
            prices = {item["contract_code"]: item["settlement"] for item in target_bars.to_dict("records")}
            events.append((1, product, "换月", f"{product}：主力 {old} → {new}；当日字段值 {prices.get(old, '缺失')} → {prices.get(new, '缺失')}，不作跨合约涨跌比较。"))
    return [event[3] for event in sorted(events)[:5]]
