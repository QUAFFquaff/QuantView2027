from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from quantview.config import load_settings
from quantview.data.repository import Repository
from quantview.operations import build_health_snapshot
from quantview.pipeline import DailyPipeline

st.set_page_config(page_title="QuantView2027", page_icon="📈", layout="wide")
settings = load_settings(Path.cwd())
repository = Repository(settings.database_path)

st.title("QuantView2027 农产品期货投研工作台")
st.caption("本地只读研究界面｜LH 生猪・JD 鸡蛋・C 玉米｜不具备下单能力")

if not settings.database_path.exists():
    st.warning("数据库尚未创建。请先运行 `quantview init-db` 和 `quantview run-daily`。")
    st.stop()

health = build_health_snapshot(repository, settings.products, as_of=date.today())
health_labels = {
    "healthy": "正常",
    "due": "待今日更新",
    "stale": "数据滞后",
    "incomplete": "数据不完整",
    "failed": "最近任务失败",
    "empty": "无数据",
}
st.sidebar.metric("运行状态", health_labels[health["overall_status"]])
st.sidebar.caption("新鲜度按工作日估算，暂未扣除交易所节假日。")

dates = repository.query("SELECT DISTINCT trade_date FROM product_daily ORDER BY trade_date DESC")
if dates.empty:
    st.warning("还没有可展示的分析数据。请先运行每日任务。")
    st.stop()

available_dates = pd.to_datetime(dates["trade_date"]).dt.date.tolist()
selected_date = st.sidebar.selectbox("交易日", available_dates, index=0)
selected_runs = repository.query(
    "SELECT stages, status FROM pipeline_run WHERE target_trade_date=? ORDER BY started_at DESC LIMIT 1",
    [selected_date],
)
selected_stages = json.loads(selected_runs.iloc[0]["stages"]) if not selected_runs.empty else {}
selected_completeness = selected_stages.get("completeness", {}).get("products", {})
latest_date = available_dates[0]
today = date.today()
st.sidebar.caption(f"数据库最新交易日：{latest_date}")
if today.weekday() < 5 and latest_date < today:
    st.sidebar.warning(f"可能缺少 {today} 的日终数据。")
if st.sidebar.button("检查并补跑今天", type="primary"):
    with st.sidebar.status("正在更新日行情并生成日报……", expanded=True) as status_box:
        result = DailyPipeline(settings).run(today, trigger_type="catch_up")
        status_box.write(f"任务状态：{result['status']}")
        status_box.write(f"接受数据：{result['rows_accepted']} 行")
        if result["source_errors"]:
            status_box.write(result["source_errors"])
        status_box.update(
            label="更新完成" if result["status"] != "failed" else "更新失败",
            state="complete" if result["status"] != "failed" else "error",
        )
    st.rerun()
overview_tab, product_tab, curve_tab, report_tab = st.tabs(
    ["今日总览", "品种研究", "期限结构", "日报与系统状态"]
)

with overview_tab:
    incomplete_products = [
        key for key, value in selected_completeness.items() if value["status"] != "complete"
    ]
    if incomplete_products:
        st.warning("合约数据不完整：" + "、".join(incomplete_products) + "。请先查看日报中的缺口说明。")
    overview = repository.query(
        "SELECT * FROM product_daily WHERE trade_date=? ORDER BY product", [selected_date]
    )
    ai = repository.query(
        """
        SELECT product, bias, confidence, judge_view FROM (
          SELECT *, row_number() OVER (PARTITION BY product ORDER BY created_at DESC) rn
          FROM ai_analysis WHERE trade_date=?
        ) WHERE rn=1 ORDER BY product
        """,
        [selected_date],
    )
    if "completeness" in selected_stages or (
        not selected_runs.empty and selected_runs.iloc[0]["status"] in {"failed", "running"}
    ):
        ai = ai.loc[ai["product"].isin(selected_stages.get("ai", {}).get("completed_products", []))]
    if not ai.empty:
        overview = overview.merge(ai, on="product", how="left")
    display_columns = [
        "product", "main_contract", "second_contract", "main_changed", "main_settlement",
        "return_5d", "return_20d", "total_volume_change", "total_oi_change",
        "price_oi_regime",
    ]
    if "bias" in overview:
        display_columns += ["bias", "confidence"]
    st.dataframe(overview[display_columns], width="stretch", hide_index=True)
    bars = repository.query(
        """
        SELECT f.trade_date, i.product, f.settlement, f.instrument_id
        FROM futures_daily f JOIN instrument i USING (instrument_id)
        JOIN product_daily p ON p.trade_date=f.trade_date AND p.main_contract=i.contract_code
        WHERE f.trade_date BETWEEN ? - INTERVAL 120 DAY AND ?
        ORDER BY f.trade_date
        """,
        [selected_date, selected_date],
    )
    if not bars.empty:
        figure = px.line(bars, x="trade_date", y="settlement", color="product", title="主力真实合约结算价（含换月）")
        st.plotly_chart(figure, width="stretch")

with product_tab:
    product = st.selectbox("品种", list(settings.products), key="product_detail")
    history = repository.query(
        """
        SELECT * FROM product_daily
        WHERE product=? AND trade_date <= ?
        ORDER BY trade_date
        """,
        [product, selected_date],
    )
    if not history.empty:
        left, right = st.columns(2)
        with left:
            st.plotly_chart(
                px.line(history.tail(180), x="trade_date", y="main_settlement", title=f"{product} 主力结算价"),
                width="stretch",
            )
        with right:
            volume_oi = history.tail(180).melt(
                id_vars="trade_date", value_vars=["total_volume", "total_open_interest"],
                var_name="metric", value_name="value",
            )
            st.plotly_chart(
                px.line(volume_oi, x="trade_date", y="value", color="metric", title="成交量与持仓量"),
                width="stretch",
            )
        st.dataframe(
            history.tail(30).sort_values("trade_date", ascending=False),
            width="stretch",
            hide_index=True,
        )

with curve_tab:
    product = st.selectbox("品种", list(settings.products), key="product_curve")
    curve = repository.query(
        """
        SELECT i.contract_code, i.delivery_year, i.delivery_month, f.settlement,
               f.volume, f.open_interest
        FROM futures_daily f JOIN instrument i USING (instrument_id)
        WHERE f.trade_date=? AND i.product=?
        ORDER BY i.delivery_year, i.delivery_month
        """,
        [selected_date, product],
    )
    if not curve.empty:
        st.plotly_chart(
            px.line(curve, x="contract_code", y="settlement", markers=True, title=f"{product} 期限结构"),
            width="stretch",
        )
    spread_history = repository.query(
        """
        SELECT * FROM calendar_spread_daily
        WHERE product=? AND pair_type='main_second' AND trade_date <= ?
        ORDER BY trade_date
        """,
        [product, selected_date],
    )
    if not spread_history.empty:
        spread_history["pair"] = spread_history["near_contract"] + "-" + spread_history["far_contract"]
        st.plotly_chart(
            px.line(spread_history.tail(240), x="trade_date", y="settlement_spread", color="pair", title="主力—次主力结算价差"),
            width="stretch",
        )
        st.dataframe(
            spread_history.tail(30).sort_values("trade_date", ascending=False),
            width="stretch",
            hide_index=True,
        )

with report_tab:
    if selected_completeness:
        st.subheader("所选交易日合约完整性")
        st.dataframe(
            [{"品种": key, **value} for key, value in selected_completeness.items()],
            width="stretch", hide_index=True,
        )
    st.subheader("运行健康度")
    health_col, coverage_col, trial_col = st.columns(3)
    health_col.metric("数据状态", health_labels[health["overall_status"]])
    coverage_col.metric(
        "品种覆盖",
        f"{health['coverage']['count']}/{health['coverage']['expected_count']}",
    )
    trial_col.metric(
        "5 日试运行",
        f"{health['trial']['completed_days']}/{health['trial']['target_days']}",
    )
    if health["coverage"]["missing"]:
        st.error("缺少品种：" + "、".join(health["coverage"]["missing"]))
    if health["business_days_behind"] is not None and health["business_days_behind"] > 1:
        st.warning(
            f"最新行情日为 {health['latest_market_date']}，"
            f"按工作日估算滞后 {health['business_days_behind']} 日。"
        )
    latest_run = health["latest_run"]
    if latest_run and latest_run["source_errors"]:
        with st.expander(f"最近任务异常（{len(latest_run['source_errors'])}）"):
            st.json(latest_run["source_errors"])

    st.subheader("Markdown 日报")
    report_path = settings.path("reports") / f"{selected_date:%Y}" / f"{selected_date:%m}" / f"{selected_date}.md"
    if report_path.exists():
        st.markdown(report_path.read_text(encoding="utf-8"))
    else:
        st.info("该交易日没有 Markdown 日报。")
    st.subheader("最近运行记录")
    runs = repository.query("SELECT * FROM pipeline_run ORDER BY started_at DESC LIMIT 20")
    st.dataframe(runs, width="stretch", hide_index=True)
