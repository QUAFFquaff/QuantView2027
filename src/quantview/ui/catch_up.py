from __future__ import annotations

from datetime import date

import streamlit as st

from quantview.config import Settings
from quantview.data.repository import Repository
from quantview.operations.calendar import CalendarUnavailable, backfill_targets, load_calendar
from quantview.pipeline import DailyPipeline


def _run(settings: Settings, targets: list[date], trigger: str) -> None:
    st.session_state["quantview_running"] = True
    results = []
    try:
        with st.sidebar.status("正在更新日行情并生成日报……", expanded=True) as box:
            pipeline = DailyPipeline(settings)
            for target in targets:
                result = pipeline.run(target, trigger_type=trigger)
                results.append(result)
                box.write(f"{target}：{result['status']}，接受 {result['rows_accepted']} 行")
                if result["source_errors"]:
                    box.write(result["source_errors"])
            failed = any(result["status"] == "failed" for result in results)
            box.update(label="更新失败" if failed else "更新完成", state="error" if failed else "complete")
        st.session_state["quantview_last_update"] = results
    finally:
        st.session_state["quantview_running"] = False
    st.rerun()


def render_calendar_controls(settings: Settings, health: dict) -> None:
    plan = health["catch_up"]
    state = health["calendar"]
    st.sidebar.caption(f"上海日终采集时间：{settings.scheduled_time:%H:%M}")
    st.sidebar.caption(f"应更新交易日：{health['expected_trade_date'] or '无法确认'}")
    st.sidebar.caption(f"最近完整日：{health['latest_complete_date'] or '暂无'}")
    if state["status"] != "verified":
        st.sidebar.warning(state["reason"])
        st.sidebar.caption("请先用 quantview import-calendar 导入已核验日历。")
    with st.sidebar.expander("交易日历来源与覆盖"):
        st.json(state)
    st.sidebar.caption(f"缺失交易日：{plan['count']} 个（仅检查已核验覆盖范围）")
    if plan["reason"]:
        st.sidebar.warning(plan["reason"])
    if plan["missing_dates"]:
        with st.sidebar.expander("查看缺失日期"):
            st.write(plan["missing_dates"])
    busy = st.session_state.get("quantview_running", False)
    if st.sidebar.button(
        "补跑最近缺失交易日", type="primary", key="catch_up_latest",
        disabled=busy or not plan["available"] or not plan["latest_missing_date"],
    ):
        _run(settings, [date.fromisoformat(plan["latest_missing_date"])], "catch_up")
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
