from __future__ import annotations

from datetime import date, datetime, timedelta

import streamlit as st

from quantview.config import Settings
from quantview.data.repository import Repository
from quantview.operations.calendar import (
    CalendarUnavailable,
    backfill_targets,
    load_calendar,
    resolve_target,
)
from quantview.pipeline import DailyPipeline
from quantview.ui.calendar_view import render_calendar_backup, render_calendar_month
from quantview.ui.data_status import date_status


def _run(settings: Settings, targets: list[date], trigger: str, *, data_only=False) -> None:
    st.session_state["quantview_running"] = True
    results = []
    try:
        with st.sidebar.status("正在更新日行情并生成日报……", expanded=True) as box:
            pipeline = DailyPipeline(settings)
            for target in targets:
                try:
                    options = {"trigger_type": trigger}
                    if data_only:
                        options["allow_ai"] = False
                    result = pipeline.run(target, **options)
                except Exception as exc:
                    result = {"status": "failed", "rows_accepted": 0,
                              "source_errors": [{"source": "pipeline", "error": str(exc)}]}
                results.append(result)
                box.write(f"{target}：{result['status']}，接受 {result['rows_accepted']} 行")
                if result["source_errors"]:
                    box.write(result["source_errors"])
            failed = any(result["status"] == "failed" for result in results)
            partial = any(result["status"] == "partial" for result in results)
            label = "更新失败" if failed else "更新结束，数据或日历仍待核验" if partial else "更新完成"
            box.update(label=label, state="error" if failed else "complete")
        st.session_state["quantview_last_update"] = results
        if results and results[-1]["status"] in {"success", "partial"}:
            st.session_state["quantview_select_date"] = targets[-1]
    finally:
        st.session_state["quantview_running"] = False
    st.rerun()


def render_update_controls(settings: Settings, health: dict) -> None:
    instant = datetime.fromisoformat(health["calendar"]["as_of"])
    status = date_status(health["latest_market_date"], now=instant)
    history = health.get("history", {})
    latest_ready = health.get("expected_trade_date")
    latest_available = latest_ready is not None
    aligned = health["overall_status"] == "healthy" and status["date"] == latest_ready
    with st.container(border=True):
        if aligned:
            st.success(f"最新已就绪行情已更新至 {latest_ready} · 品种覆盖 {health['coverage']['count']} / {health['coverage']['expected_count']}")
        elif status["date"] is None:
            st.info("暂无本地期货行情。可按已核验日历选日采集，或明确指定日期手动采集。")
        else:
            label = f"本地行情截至 {status['date']} · 距今 {status['age']} 个自然日"
            if status["age"] > 1:
                st.warning(label + "。请核对休市情况并更新；不以自然日数量推算交易日缺口。")
            else:
                st.write(label)
        if not latest_available:
            st.caption("自动选日暂停：交易日历缺失或覆盖不足。仍可通过下方入口明确指定日期；不会猜测最近交易日。")
        else:
            st.caption(f"按已核验日历，当前应展示 {latest_ready} 的日终行情。")
            if health["calendar"].get("is_trading_day") and instant.time().replace(tzinfo=None) < settings.scheduled_time:
                st.caption(f"今天 {instant.date()} 的日终采集时间为上海时间 {settings.scheduled_time:%H:%M}；此前展示最近已就绪交易日，不代表更新失败。")
            if history.get("summary_missing_count"):
                st.caption(f"历史三品种摘要有 {history['summary_missing_count']} 个交易日存在缺口，可在侧栏“历史补数与运行核验”查看。")
        label = f"重新采集最新日（{latest_ready}）" if aligned else "采集最新已就绪行情"
        if st.button(label, key="futures_update_latest", type="primary",
                     disabled=not latest_available or st.session_state.get("quantview_running", False)):
            # The latest button must never silently select an older audit gap.
            target = date.fromisoformat(health["expected_trade_date"])
            _run(settings, [target], "catch_up", data_only=True)
        with st.expander("明确指定日期采集（日历未就绪时也可使用）"):
            latest_ready = instant.date() if instant.time().replace(tzinfo=None) >= settings.scheduled_time else instant.date() - timedelta(days=1)
            selected = st.date_input("手动采集日期", value=latest_ready, max_value=instant.date(), key="manual_market_date")
            st.caption("选择实际已结束的交易日。日历未核验时任务标为部分，本入口仅采集行情、计算指标和日报，关闭 AI。")
            confirmed = st.checkbox("我已核对所选日期，接受日历未知时的质量提示", key="manual_market_confirm")
            if st.button("采集所选日期", key="manual_market_submit", disabled=not confirmed):
                calendar = load_calendar(Repository(settings.database_path), settings.calendar_exchange)
                decision = resolve_target(calendar, selected, now=instant, ready_time=settings.scheduled_time)
                if not decision["allowed"]:
                    st.error(decision["reason"])
                else:
                    _run(settings, [selected], "manual", data_only=True)
        results = st.session_state.get("quantview_last_update")
        if results:
            latest = results[-1]
            if latest["status"] == "failed":
                st.error("最近采集未成功，已有行情保留。请查看下方错误原因后重试。")
            elif latest["status"] == "partial":
                st.warning("最近采集已结束，但合约覆盖或日历未完整核验，请查看结果说明。")
            with st.expander("最近采集结果"):
                st.json(results)
        render_calendar_month(settings, health)


def render_calendar_controls(settings: Settings, health: dict) -> None:
    plan = health["catch_up"]
    state = health["calendar"]
    st.sidebar.caption(f"上海日终采集时间：{settings.scheduled_time:%H:%M}")
    st.sidebar.caption(f"应更新交易日：{health['expected_trade_date'] or '无法确认'}")
    st.sidebar.caption(f"最近完整日：{health['latest_complete_date'] or '暂无'}")
    if state["status"] != "verified":
        message = "尚未导入交易日历，请在下方同步并备份。" if not state.get("coverage_start") else "交易日历未覆盖当前选日范围，请同步相应年度。"
        st.sidebar.warning(message)
    elif state.get("coverage_end"):
        days_left = (date.fromisoformat(state["coverage_end"]) - datetime.fromisoformat(state["as_of"]).date()).days
        if 0 <= days_left <= 45:
            st.sidebar.caption(f"日历覆盖至 {state['coverage_end']}，剩余 {days_left} 个自然日；下一年度公告发布后同步即可。")
    with st.sidebar.expander("交易日历与本地备份", expanded=state["status"] != "verified"):
        render_calendar_backup(settings, state)
    history = health.get("history", {})
    st.sidebar.caption(f"历史品种摘要缺口：{history['summary_missing_count']} 日" if history.get("summary_missing_count") is not None else "历史摘要缺口：日历覆盖不足，无法确认")
    busy = st.session_state.get("quantview_running", False)
    with st.sidebar.expander("历史补数与运行核验"):
        st.caption("行情摘要缺口与日终任务记录分开检查；回溯行情可能已有摘要，但没有对应日期的完整日报或核验记录。历史核验不影响最新行情的更新入口。")
        if plan["reason"] and state["status"] == "verified":
            st.warning("历史核验计划不可用：" + plan["reason"])
        if history.get("summary_missing_count") is not None:
            st.write(f"历史品种摘要缺口：{history['summary_missing_count']} 日；已有摘要但日终任务未核验：{history['run_pending_count']} 日。")
            st.caption("摘要缺口只检查所配置品种的摘要行，不据此认定历史全合约已核验。")
        if history.get("summary_missing_details"):
            st.dataframe(history["summary_missing_details"], hide_index=True, width="stretch")
        if history.get("run_pending_dates"):
            st.write("已有摘要、待日终任务核验的日期：")
            st.write(history["run_pending_dates"])
        target = plan.get("latest_missing_date")
        if st.button(
            f"重跑待核验日（{target}）" if target else "暂无待核验日", type="secondary", key="catch_up_latest",
            disabled=busy or not plan["available"] or not target,
        ):
            _run(settings, [date.fromisoformat(target)], "catch_up")
    with st.sidebar.expander("指定区间历史补数"):
        st.caption("仅补已核验交易日；历史补数默认不调用 AI。")
        if plan["available"]:
            end = date.fromisoformat(health["expected_trade_date"])
            selected = st.date_input("补数区间", value=(end, end), max_value=end, key="backfill_range")
            if st.button("运行所选区间补数", disabled=busy or len(selected) != 2, key="backfill_submit"):
                try:
                    calendar = load_calendar(Repository(settings.database_path), settings.calendar_exchange)
                    targets = backfill_targets(calendar, selected[0], selected[1], ready_time=settings.scheduled_time)
                    if targets:
                        _run(settings, targets, "backfill")
                    else:
                        st.info("所选区间没有交易日。")
                except CalendarUnavailable as exc:
                    st.error(str(exc))
        else:
            st.info("日历未就绪，暂不能生成补数计划。")
    if "quantview_last_update" in st.session_state:
        with st.sidebar.expander("最近一次补跑结果"):
            st.json(st.session_state["quantview_last_update"])
