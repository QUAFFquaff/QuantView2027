from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from quantview import config
from quantview.data.repository import Repository
from quantview.operations import build_health_snapshot
from quantview.operations.calendar import load_calendar
from quantview.ui.catch_up import render_calendar_controls
from quantview.ui.charts import contract_prices, fixed_spread, main_history, style
from quantview.ui.queries import WorkbenchQueries, important_changes, json_value

LABELS = {
    "healthy": "正常", "due": "待更新", "stale": "数据滞后", "incomplete": "数据不完整",
    "failed": "失败", "empty": "无数据", "calendar_unknown": "日历无法确认",
    "complete": "覆盖完整", "partial": "部分", "missing": "缺失", "unknown": "未确认",
    "success": "成功", "running": "运行中", "skipped": "已跳过",
}
UNITS = {"LH": "元/吨", "JD": "元/500千克", "C": "元/吨"}
CSS = """<style>
.stApp { background: #f5f7fa; }
.block-container { padding-top: 4.5rem; max-width: 1440px; }
h1, h2, h3 { color: #172e42; letter-spacing: -.025em; }
[data-testid="stSidebar"] { background: #edf2f5; }
[data-testid="stMetricValue"] { color: #173d50; }
[data-testid="stVerticalBlockBorderWrapper"] > div { background: #fff; border-radius: 14px; }
[data-testid="stTabs"] button { font-weight: 600; }
@media (max-width: 640px) {
 .block-container { padding: 4.5rem 1rem 1rem; }
 [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
 [data-testid="stColumn"] { min-width: 100% !important; }
 h1 { font-size: 1.65rem !important; }
}
</style>"""


def fmt(value, *, percent=False, signed=False):
    if value is None or pd.isna(value):
        return "—"
    return format(float(value), ("+" if signed else "") + (".2%" if percent else ",.2f"))


def table(frame):
    st.dataframe(frame, width="stretch", hide_index=True)


def quality_evidence(bars):
    if bars.empty:
        st.info("没有可用合约行情。")
        return
    table(bars[["trade_date", "contract_code", "settlement", "source", "source_fetched_at",
                "quality_status", "quality_flags", "raw_batch_id"]].sort_values(
                    ["trade_date", "contract_code"], ascending=[False, True]))
    st.caption("settlement_filled_from_close = 收盘价替代结算价；替代值不能视为真实结算。")


class Dashboard:
    def __init__(self, settings, queries, health, target):
        self.settings, self.queries, self.health, self.target = settings, queries, health, target
        runs = queries.runs(target) if target else pd.DataFrame()
        self.run = runs.iloc[0].to_dict() if not runs.empty else {}
        self.stages = json_value(self.run.get("stages"), {})
        self.completeness = self.stages.get("completeness", {}).get("products", {})
        self.products = queries.products(target) if target else pd.DataFrame()
        self.bars = {p: queries.bars(p, target) if target else pd.DataFrame() for p in settings.products}

    def name(self, product):
        return f"{self.settings.products[product].get('name', product)} · {product}"

    def history(self, product):
        return self.queries.product_history(product, self.target) if self.target else pd.DataFrame()

    def header(self):
        coverage = set(self.products["product"]) if not self.products.empty else set()
        complete = set(self.settings.products) <= coverage and all(
            self.completeness.get(p, {}).get("status") == "complete" for p in self.settings.products)
        cols = st.columns(4)
        cols[0].metric("所选交易日", str(self.target or "暂无"))
        cols[1].metric("品种覆盖", f"{len(coverage & set(self.settings.products))} / {len(self.settings.products)}")
        cols[2].metric("最近完整日", self.health["latest_complete_date"] or "暂无")
        cols[3].metric("任务状态", LABELS.get(self.run.get("status"), "未记录"))
        st.caption(f"本地最新查询 · 非归档快照 · 最近任务结束：{self.run.get('finished_at') or '暂无'}。历史数据可能被补跑修订；暂不提供快照版本切换。")
        if not self.target:
            st.warning("数据库尚未创建或还没有行情 / 运行记录。先导入已核验日历，再明确触发补跑；系统状态仍可查看。")
        elif self.products.empty:
            st.warning("所选交易日没有品种指标。请查看合约行情及系统状态中的失败原因。")
        elif not complete:
            st.warning("所选日数据不完整或覆盖未确认。缺少的指标显示为 —，请查看质量证据。")

    def overview(self):
        st.subheader("市场概览")
        for column, product in zip(st.columns(len(self.settings.products)), self.settings.products, strict=True):
            with column, st.container(border=True):
                st.markdown(f"### {self.name(product)}")
                rows = self.products.loc[self.products["product"] == product] if not self.products.empty else self.products
                if rows.empty:
                    st.metric("结算字段值", "—")
                    st.caption("目标日缺少品种指标")
                    continue
                row = rows.iloc[0]
                st.caption(f"主力 {row['main_contract']} / 次主力 {row['second_contract']} · {UNITS.get(product, '价格单位未配置')}")
                bars = self.bars[product]
                main = bars.loc[(bars["contract_code"] == row["main_contract"]) & (pd.to_datetime(bars["trade_date"]).dt.date == self.target)] if not bars.empty else bars
                bar = main.iloc[0] if not main.empty else {}
                flags = json_value(bar.get("quality_flags"), [])
                label = "替代结算（收盘价）" if "settlement_filled_from_close" in flags else "结算字段值"
                st.metric(label, fmt(row["main_settlement"]), fmt(bar.get("settlement_return"), percent=True, signed=True), delta_color="off")
                st.caption("日变化：当前主力同合约相邻入库日比较")
                st.write(f"链式 5 日 **{fmt(row['return_5d'], percent=True)}** · 20 日 **{fmt(row['return_20d'], percent=True)}**")
                st.caption("窗口不足显示 —；链式变化不是策略收益。")
                st.write(f"总成交变化 {fmt(row['total_volume_change'], signed=True)} 手")
                st.write(f"总持仓变化 {fmt(row['total_oi_change'], signed=True)} 手")
                quality = self.completeness.get(product, {}).get("status", "unknown")
                st.caption(f"覆盖：{LABELS.get(quality, quality)} / 质量：{bar.get('quality_status', '未确认')}")
                if pd.notna(row["main_changed"]) and row["main_changed"]:
                    st.info(f"换月：{row['previous_main_contract']} → {row['main_contract']}")
        st.subheader("重要变化")
        events = important_changes(self.products, self.completeness, self.bars)
        if events:
            for index, event in enumerate(events, 1):
                st.write(f"{index}. {event}")
            st.caption("质量优先，其次换月，最多 5 项；原始证据见下方。")
        else:
            st.info("未发现覆盖或换月事件；尚未配置其他变化阈值。")
        with st.expander("查看变化与质量证据"):
            st.json(self.completeness or {"status": "未记录完整性"})
            for product, bars in self.bars.items():
                st.markdown(f"**{self.name(product)}**")
                current = bars.loc[pd.to_datetime(bars["trade_date"]).dt.date == self.target] if not bars.empty else bars
                quality_evidence(current)
        left, right = st.columns(2)
        with left, st.container(border=True):
            st.markdown("**我的规则条件**")
            st.write("未配置")
            st.caption("纪律版本与机器条件检查待接入。")
        with right, st.container(border=True):
            st.markdown("**待复盘**")
            st.write("暂未接入个人记录")
            st.caption("当前不统计到期数量。")
        st.subheader("主力历史 · 按真实合约分段")
        for product in self.settings.products:
            history = self.history(product)
            if not history.empty:
                st.plotly_chart(main_history(history.tail(120), self.name(product), UNITS.get(product, "")), width="stretch", key=f"overview_{product}")
        st.caption("换月处断线；各品种独立价格轴。点与点之间为入库观测，缺失交易日需核对日历与质量证据。")
        with st.expander("AI 研究详情"):
            ai = self.queries.analysis(self.target, self.stages, self.run.get("status")) if self.target else pd.DataFrame()
            st.caption("仅展示最近一轮任务时间内完成品种的分析；当前尚无不可变快照绑定。")
            if ai.empty:
                st.info("本轮没有可核验的 AI 分析，不使用历史分析填充。")
            for item in ai.to_dict("records"):
                st.markdown(f"#### {self.name(item['product'])}")
                bull, bear = st.columns(2)
                bull.markdown("**多方论证**")
                bull.write(item["bull_case"])
                bear.markdown("**空方论证**")
                bear.write(item["bear_case"])
                st.write(item["judge_view"])
                st.write("推翻条件", json_value(item["invalidation_conditions"], []))
                st.write("风险标记", json_value(item["risk_flags"], []))
                st.caption(f"{item['model']} · {item['created_at']} · AI 自评分 {fmt(item['confidence'])}（非胜率）")

    def research(self):
        product = st.selectbox("品种", list(self.settings.products), format_func=self.name, key="product_detail")
        bars, unit = self.bars[product], UNITS.get(product, "价格单位未配置")
        if bars.empty:
            st.info("所选日期及之前没有该品种的真实合约行情。")
        else:
            contracts = bars.sort_values(["delivery_year", "delivery_month"])["contract_code"].drop_duplicates().tolist()
            contract = st.selectbox("真实合约", contracts, key="research_contract")
            horizon = st.select_slider("展示观测数", options=[30, 60, 120, 240], value=120)
            history = bars.loc[bars["contract_code"] == contract].tail(horizon)
            st.caption(f"{contract} · {unit} · 行情截至 {pd.Timestamp(history.iloc[-1]['trade_date']).date()}，选择交易日 {self.target}")
            st.plotly_chart(contract_prices(history, f"{contract} · 真实合约行情", unit), width="stretch")
            left, right = st.columns(2)
            for column, field, label in ((left, "volume", "成交量"), (right, "open_interest", "持仓量")):
                figure = go.Figure(go.Bar(x=history["trade_date"], y=history[field], name=label, marker_color="#428e96"))
                column.plotly_chart(style(figure, label, "手"), width="stretch")
            st.caption("总持仓不代表主动买卖方向。OHLC 为真实字段，结算价是否替代见质量证据。")
            with st.expander("行情、来源与质量证据"):
                quality_evidence(history)
                st.download_button("下载所选合约 CSV", history.to_csv(index=False).encode("utf-8-sig"), f"{contract}_{self.target}.csv", "text/csv")
        history = self.history(product)
        if not history.empty:
            st.subheader("链式指标与窗口")
            st.caption("按入库的同合约日变化链式计算；预热时不填零。尚不具备归档窗口与缺失日完整性证明。")
            latest = history.iloc[-1]
            st.caption(f"指标截至 {pd.Timestamp(latest['trade_date']).date()}")
            for window in (5, 20):
                status = "窗口不足或含缺值" if pd.isna(latest[f"return_{window}d"]) else "入库观测窗口已满足，交易日连续性未核验"
                st.write(f"{window} 日：{fmt(latest[f'return_{window}d'], percent=True)} · {status}")
            with st.expander("查看品种指标原始值"):
                table(history.tail(60).sort_values("trade_date", ascending=False))

    def curves(self):
        product = st.selectbox("品种", list(self.settings.products), format_func=self.name, key="product_curve")
        bars, unit = self.bars[product], UNITS.get(product, "价格单位未配置")
        if bars.empty:
            st.info("该品种没有期限结构数据。")
            return
        dates = sorted(pd.to_datetime(bars["trade_date"]).dt.date.unique(), reverse=True)
        previous = [d for d in dates if d < self.target]
        comparison = st.selectbox("历史对照交易日", [None, *previous], index=1 if previous else 0, format_func=lambda d: str(d) if d else "不对照", key="curve_comparison")
        figure = go.Figure()
        ticks = bars[["contract_code", "delivery_year", "delivery_month"]].drop_duplicates().sort_values(["delivery_year", "delivery_month"])
        for day in [self.target, comparison]:
            if day is None:
                continue
            part = bars.loc[pd.to_datetime(bars["trade_date"]).dt.date == day].sort_values(["delivery_year", "delivery_month"])
            figure.add_trace(go.Scatter(x=part["delivery_year"] * 12 + part["delivery_month"], y=part["settlement"], mode="lines+markers", name=str(day), text=part["contract_code"], hovertemplate="%{text}<br>%{y}<extra>%{fullData.name}</extra>"))
        figure.update_xaxes(tickvals=ticks["delivery_year"] * 12 + ticks["delivery_month"], ticktext=ticks["contract_code"])
        st.plotly_chart(style(figure, f"{self.name(product)} · 期限结构", unit), width="stretch")
        if self.target not in dates:
            st.warning("所选交易日没有期限结构，不以历史数据冒充当日。")
        with st.expander("两日合约质量与流动性"):
            selected = bars.loc[pd.to_datetime(bars["trade_date"]).dt.date.isin([self.target, comparison])]
            table(selected[["trade_date", "contract_code", "settlement", "volume", "open_interest", "quality_flags", "source"]])
            st.caption("settlement_filled_from_close 表示收盘价替代结算价。")
        spreads = self.queries.spreads(product, self.target)
        if spreads.empty:
            st.info("暂无已计算的固定合约对价差。")
            return
        options = list(spreads[["near_contract", "far_contract", "pair_type"]].drop_duplicates().itertuples(index=False, name=None))
        near, far, kind = st.selectbox("固定合约对 / 样本类型", options, format_func=lambda p: f"{p[0]} − {p[1]} · {p[2]}", key="spread_pair")
        fixed = spreads.loc[(spreads["near_contract"] == near) & (spreads["far_contract"] == far) & (spreads["pair_type"] == kind)].copy()
        all_dates = pd.DataFrame({"trade_date": pd.to_datetime(bars["trade_date"]).drop_duplicates().sort_values()})
        fixed["trade_date"] = pd.to_datetime(fixed["trade_date"])
        plot_data = all_dates.merge(fixed, on="trade_date", how="left")
        st.plotly_chart(fixed_spread(plot_data, f"{near} − {far} · 近月减远月", unit), width="stretch")
        st.caption(f"{kind} 是样本选择类型；只连接同一合约对且该类型有记录的日期。最新观测：{fixed.iloc[-1]['trade_date'].date()}。")
        for window in (20, 60):
            count = int(fixed.tail(window)["settlement_spread"].notna().sum())
            st.write(f"{window} 观测窗口 / sample_count={count} / warmup={'是' if count < window else '否'} / missing_dates=未核验")
        st.caption("统计按入库观测计算；60 日分位不代表跨年同季节可比。替代结算、流动性不足及预热值需单独核对。")
        table(fixed.tail(60).sort_values("trade_date", ascending=False))

    def system(self):
        st.subheader("系统状态")
        cols = st.columns(3)
        cols[0].metric("当前数据健康", LABELS[self.health["overall_status"]])
        cols[1].metric("连续交易日试运行", f"{self.health['trial']['completed_days']} / {self.health['trial']['target_days']}")
        cols[2].metric("邮件", "未接入")
        st.caption(self.health["trial"]["note"])
        if self.run:
            st.write(f"所选日最近任务：{self.run['run_id']} · {LABELS.get(self.run['status'], self.run['status'])}")
            with st.expander("采集、质量与 AI 阶段详情", expanded=self.run["status"] in {"failed", "partial"}):
                st.json(self.stages)
                st.json(json_value(self.run.get("source_errors"), []))
        with st.expander("运行健康与交易日历详情"):
            st.json(self.health)
        st.subheader("Markdown 日报")
        st.caption("按日期保存的最新副本，可能与所选日最近任务不一致；原发布版本尚未归档。")
        path = self.settings.path("reports") / f"{self.target:%Y}" / f"{self.target:%m}" / f"{self.target}.md" if self.target else None
        if path and path.exists():
            report = path.read_text(encoding="utf-8")
            st.download_button("下载 Markdown 日报", report, f"{self.target}.md", "text/markdown")
            st.markdown(report)
        else:
            st.info("该交易日没有 Markdown 日报。")
        st.subheader("最近运行记录")
        runs = self.queries.runs()
        if runs.empty:
            st.info("暂无运行记录。")
        else:
            table(runs)


def render():
    st.set_page_config(page_title="QuantView2027 · 研究看板", page_icon="🌾", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    settings = config.load_settings(Path.cwd())
    repository = Repository(settings.database_path)
    queries = WorkbenchQueries(repository)
    calendar = load_calendar(repository, settings.calendar_exchange)
    health = build_health_snapshot(repository, settings.products, calendar=calendar, ready_time=settings.scheduled_time)
    st.caption("QUANTVIEW / AGRICULTURAL FUTURES")
    st.title("农产品期货研究看板")
    st.caption("生猪 · 鸡蛋 · 玉米 / 日频行情、期限结构与研究证据")
    st.sidebar.title("QuantView2027")
    st.sidebar.metric("运行状态", LABELS[health["overall_status"]])
    if st.sidebar.button("刷新本地数据", key="refresh_local"):
        st.rerun()
    dates = queries.dates()
    target = st.sidebar.selectbox("交易日", dates, key="trade_date") if dates else None
    st.sidebar.caption("只读取本地数据；日历与补跑操作见下方。")
    render_calendar_controls(settings, health)
    dashboard = Dashboard(settings, queries, health, target)
    dashboard.header()
    tabs = st.tabs(["今日总览", "品种研究", "期限结构", "日报与系统状态"])
    for tab, method in zip(tabs, (dashboard.overview, dashboard.research, dashboard.curves, dashboard.system), strict=True):
        with tab:
            method()
