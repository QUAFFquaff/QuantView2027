from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from quantview.funds.catalog import STARTERS
from quantview.metals.analytics import compare_prices, price_metrics, quote_frame
from quantview.ui.charts import style
from quantview.ui.data_status import date_status

PERIODS = {"20 个观测": 20, "60 个观测": 60, "120 个观测": 120, "全部缓存": None}


def name_of(asset, snapshot=None):
    profile = STARTERS.get(asset.symbol) if asset.kind == "ETF" else None
    return profile.name if profile else snapshot["verification"]["name"] if snapshot else asset.name


def percent(value):
    return f"{value:+.2%}" if value is not None else "—"


def load_market(service, assets):
    snapshots, attempts = {}, {}
    for asset in assets:
        try:
            snapshots[asset.asset_id] = service.latest(asset)
            attempts[asset.asset_id] = service.attempt(asset)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            snapshots[asset.asset_id] = None
            attempts[asset.asset_id] = {"status": "failed", "error": str(exc)}
    return snapshots, attempts


def render_watch_table(assets, snapshots, attempts, settings, *, prefix):
    st.subheader("行情总览")
    search, group = st.columns([3, 1])
    query = search.text_input("搜索代码、名称或暴露类型", key=f"{prefix}_search", placeholder="输入代码或关键词…")
    category = group.selectbox("类别筛选", ["全部", *sorted({a.kind for a in assets})], key=f"{prefix}_category")
    rows, identities = [], []
    for asset in assets:
        snapshot = snapshots[asset.asset_id]
        attempt = attempts.get(asset.asset_id)
        profile = STARTERS.get(asset.symbol) if asset.kind == "ETF" else None
        exposure = profile.exposure if profile else "现货金属" if asset.kind == "SGE" else "产品类型未核验"
        name = name_of(asset, snapshot)
        if category != "全部" and asset.kind != category:
            continue
        if query.strip().casefold() not in f"{asset.symbol} {name} {exposure}".casefold():
            continue
        frame = quote_frame(snapshot) if snapshot else None
        metrics = price_metrics(frame) if snapshot else {}
        day = snapshot["quotes"][-1]["trade_date"] if snapshot else None
        state = date_status(day, stale_after=settings.stale_after_calendar_days)["state"]
        if attempt and attempt["status"] == "failed":
            state = f"更新失败 · {state}"
        rows.append({"代码": asset.symbol, "名称": name, "类型": asset.kind,
                     "收盘价": metrics.get("latest"), "1期 %": _change(metrics, 1),
                     "5期 %": _change(metrics, 5), "20期 %": _change(metrics, 20),
                     "60期走势": frame["close"].tail(60).tolist() if snapshot else [],
                     "行情日期": day, "状态": state, "产品暴露": exposure})
        identities.append(asset.asset_id)
    if not rows:
        st.info("没有匹配的标的。调整筛选条件，或先添加基金自选。")
        return
    table_key = f"{prefix}_watch_table"

    def pick():
        selected = st.session_state[table_key]["selection"]["rows"]
        if selected and selected[0] < len(identities):
            st.session_state[f"{prefix}_asset"] = identities[selected[0]]

    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(400, 38 + len(rows) * 36),
                 on_select=pick, selection_mode="single-row", key=table_key, column_config={
                     "收盘价": st.column_config.NumberColumn(format="%.3f", help="单位见单标的详情；不同资产的报价单位可能不同"),
                     **{f"{n}期 %": st.column_config.NumberColumn(format="%+.2f", help=f"相隔 {n} 个有效观测的未复权价格变化（%），非账户收益") for n in (1, 5, 20)},
                     "60期走势": st.column_config.LineChartColumn(y_min=None, y_max=None, width="small"),
                 })
    st.caption("点击行联动下方详情；点击列头排序。1 / 5 / 20 期按有效观测计算，日线未请求复权；不同市场的最新日期可能不同。")


def _change(metrics, count):
    value = metrics.get(f"change_{count}")
    return value * 100 if value is not None else None


def render_asset_detail(assets, snapshots, attempts, *, prefix):
    ready = {a.asset_id: a for a in assets if snapshots.get(a.asset_id)}
    choices = {a.asset_id: a for a in assets}
    st.subheader("标的详情")
    if not ready:
        st.info("更新后可查看价格图、均线、成交量、回撤和原始行情。缺少的数据保留为空。")
        return
    asset_key = f"{prefix}_asset"
    if st.session_state.get(asset_key) not in choices:
        st.session_state[asset_key] = next(iter(ready))
    left, middle, right = st.columns([2.4, 1.6, 1])
    identity = left.selectbox("观察标的", list(choices), key=asset_key,
                              format_func=lambda key: f"{name_of(choices[key], snapshots[key])} · {choices[key].symbol}")
    period = middle.selectbox("图表范围", list(PERIODS), index=1, key=f"{prefix}_period")
    chart_type = right.selectbox("图表类型", ["收盘线", "K 线"], key=f"{prefix}_chart_type")
    asset, snapshot = choices[identity], snapshots[identity]
    if snapshot is None:
        st.info(f"{name_of(asset)} · {asset.symbol} 尚无已核验缓存，请点击更新采集。")
        return
    frame = quote_frame(snapshot)
    metrics = price_metrics(frame)
    last = frame.iloc[-1]
    st.markdown(f"**{name_of(asset, snapshot)}**　`{asset.market} · {asset.symbol}`　{asset.price_unit}　·　截至 {last['trade_date']:%Y-%m-%d}")
    columns = st.columns(5)
    columns[0].metric("日线收盘参考价", f"{metrics['latest']:,.3f}", percent(metrics["change_1"]), delta_color="inverse")
    columns[1].metric("5 期价格变化", percent(metrics["change_5"]))
    columns[2].metric("20 期价格变化", percent(metrics["change_20"]))
    columns[3].metric("60 观测最大回撤", percent(metrics["drawdown_60"]))
    columns[4].metric("20 期日变化波动", f"{metrics['volatility_20']:.2%}" if metrics["volatility_20"] is not None else "—")
    overlays, volume_column = st.columns([3, 1])
    averages = overlays.multiselect("均线叠加", [5, 20, 60], default=[20, 60], key=f"{prefix}_averages", format_func=lambda n: f"MA{n}")
    show_volume = volume_column.checkbox("显示成交量", value=False, key=f"{prefix}_volume", disabled=frame["volume"].isna().all())
    count = PERIODS[period]
    shown = frame.tail(count) if count else frame
    volume_available = show_volume and shown["volume"].notna().any()
    figure = make_subplots(rows=2 if volume_available else 1, cols=1, shared_xaxes=True,
                           vertical_spacing=0.04, row_heights=[0.8, 0.2] if volume_available else [1])
    ohlc = shown[["open", "high", "low"]].notna().all(axis=1)
    if chart_type == "K 线" and ohlc.all():
        figure.add_trace(go.Candlestick(x=shown["trade_date"], open=shown["open"], high=shown["high"], low=shown["low"], close=shown["close"], name="日线 OHLC", increasing_line_color="#ef7487", decreasing_line_color="#48c7ad"), row=1, col=1)
    else:
        if chart_type == "K 线":
            st.caption(f"该区间仅 {int(ohlc.sum())}/{len(shown)} 个观测有完整 OHLC，使用真实收盘线。")
        figure.add_trace(go.Scatter(x=shown["trade_date"], y=shown["close"], name="收盘价", line={"color": "#d6af63", "width": 2}), row=1, col=1)
    for average in averages:
        figure.add_trace(go.Scatter(x=shown["trade_date"], y=shown[f"ma{average}"], name=f"MA{average}", line={"width": 1}, connectgaps=False), row=1, col=1)
    if volume_available:
        figure.add_trace(go.Bar(x=shown["trade_date"], y=shown["volume"], name="成交量 · 来源原单位", marker_color="#516c86"), row=2, col=1)
        figure.update_yaxes(title_text="来源原单位", row=2, col=1)
    style(figure, "", asset.price_unit)
    figure.update_layout(height=440 if volume_available else 380, margin={"l": 12, "r": 12, "t": 12, "b": 30}, xaxis_rangeslider_visible=False, uirevision=f"{identity}-{period}-{chart_type}")
    figure.update_yaxes(title_text=asset.price_unit, row=1, col=1)
    st.plotly_chart(figure, width="stretch", key=f"{prefix}_price_chart")
    attempt = attempts.get(identity)
    if attempt and attempt["status"] == "failed":
        st.warning(f"最近更新失败，当前展示旧版本：{attempt['error']}")
    with st.expander("区间统计、口径与来源"):
        stats = st.columns(4)
        stats[0].metric("60 观测最高收盘", f"{metrics['high_60']:,.3f}" if metrics["high_60"] is not None else "—")
        stats[1].metric("60 观测最低收盘", f"{metrics['low_60']:,.3f}" if metrics["low_60"] is not None else "—")
        stats[2].metric("较 MA20 偏离", percent(metrics["ma20_gap"]))
        stats[3].metric("缓存有效观测", str(metrics["count"]))
        st.caption("均线按观测数计算；最大回撤为最近 60 个收盘观测相对区间内历史高点的最差跌幅。波动为最近 20 个相邻观测价格变化的样本标准差，未年化，非账户风险。窗口不足不计算。")
        st.caption(f"采集：{snapshot['fetched_at']} · AKShare / {snapshot['endpoint']} · 上海时间 18:10 前不展示当日来源数据。成交量沿用来源原单位，不在跨市场间直接比较。")
        st.markdown(f"[行情来源]({asset.source_url})")
        profile = STARTERS.get(asset.symbol) if asset.kind == "ETF" else None
        if profile:
            st.markdown(f"产品暴露：{profile.exposure} · [核验资料]({profile.source})")
        st.caption("未接入净值 / IOPV / 折溢价、复权、份额和个人成本；本页价格变化不代表总回报或持仓收益。")
    with st.expander("行情明细与版本证据"):
        export = frame.drop(columns=[f"ma{n}" for n in (5, 20, 60)]).copy()
        export["trade_date"] = export["trade_date"].dt.strftime("%Y-%m-%d")
        st.dataframe(export.sort_values("trade_date", ascending=False), hide_index=True, width="stretch")
        st.download_button("下载该版本行情 CSV", export.to_csv(index=False), f"{asset.asset_id}_{snapshot['version']}.csv", "text/csv", key=f"{prefix}_download_quotes")
        st.json({key: value for key, value in snapshot.items() if key not in {"raw_payload", "quotes"}})


def render_comparison(assets, snapshots, *, prefix):
    ready = {a.asset_id: a for a in assets if snapshots.get(a.asset_id)}
    st.subheader("同基期价格对比")
    if len(ready) < 2:
        st.info("至少需要两只标的的缓存行情。添加自选并更新后，即可比较走势和观测区间相关性。")
        return
    selected = st.multiselect("比较标的", list(ready), default=list(ready)[:3], key=f"{prefix}_compare_assets",
                               format_func=lambda key: f"{name_of(ready[key], snapshots[key])} · {ready[key].symbol}")
    period = st.selectbox("比较范围 · 共同观测", list(PERIODS), index=1, key=f"{prefix}_compare_period")
    if len(selected) < 2:
        st.info("请选择至少两只标的。")
        return
    currencies = {ready[key].currency for key in selected}
    if len(currencies) > 1:
        st.warning("币种不同，当前未接入汇率，无法直接比较。")
        return
    frames = {f"{name_of(ready[key], snapshots[key])} {ready[key].symbol}": quote_frame(snapshots[key]) for key in selected}
    indexed, correlation = compare_prices(frames, PERIODS[period])
    if indexed.empty:
        st.warning("共同日期不足两个，不能拼接或补齐价格。")
        return
    figure = go.Figure()
    for name in indexed:
        figure.add_trace(go.Scatter(x=indexed.index, y=indexed[name], name=name, mode="lines", connectgaps=False))
    st.plotly_chart(style(figure, "共同起点 = 100", "价格指数"), width="stretch", key=f"{prefix}_compare_chart")
    st.caption(f"共同区间 {indexed.index[0]:%Y-%m-%d} — {indexed.index[-1]:%Y-%m-%d} · {len(indexed)} 个共同日期。仅取真实日期交集、不补价；首个共同收盘 = 100，未复权、不含分红，非组合收益。")
    st.dataframe(pd.DataFrame({"标的": indexed.columns, "区间价格变化（%）": (indexed.iloc[-1].to_numpy() / 100 - 1) * 100}),
                 hide_index=True, width="stretch", column_config={"区间价格变化（%）": st.column_config.NumberColumn(format="%+.2f")})
    if correlation.empty:
        st.caption("相关矩阵至少需要 21 个共同价格观测（20 个变化样本）。")
    else:
        figure = go.Figure(go.Heatmap(z=correlation.to_numpy(), x=correlation.columns, y=correlation.index,
                                     zmin=-1, zmax=1, colorscale=[[0, "#48c7ad"], [0.5, "#19222e"], [1, "#ef7487"]],
                                     texttemplate="%{z:.2f}", hovertemplate="%{x}<br>%{y}<br>相关系数 %{z:.3f}<extra></extra>"))
        figure.update_layout(template="plotly_dark", height=270, margin={"l": 12, "r": 12, "t": 20, "b": 20}, paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(figure, width="stretch", key=f"{prefix}_correlation")
        st.caption("Pearson 相关系数基于相邻共同观测的价格变化；间隔可能跨多个来源交易日，常数序列无定义时留空。历史相关性不代表未来表现。")
