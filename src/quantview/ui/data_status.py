from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from quantview.operations.clock import shanghai_now


def date_status(latest: str | date | None, *, now: datetime | None = None, stale_after=7):
    today = shanghai_now(now).date()
    if latest is None:
        return {"date": None, "age": None, "state": "无数据"}
    day = date.fromisoformat(str(latest)[:10])
    age = (today - day).days
    return {"date": day.isoformat(), "age": age,
            "state": "日期异常" if age < 0 else "行情陈旧" if age > stale_after else "已有缓存"}


def render_market_status(rows, *, stale_after=7, compact=False):
    """A shared summary for independent daily public-data caches."""
    states = [date_status(row.get("行情日期"), stale_after=stale_after) for row in rows]
    valid = [state["date"] for state in states if state["date"] is not None]
    stale = sum(state["state"] in {"行情陈旧", "日期异常"} for state in states)
    checks = stale + len(rows) - len(valid)
    if compact:
        st.caption(f"本地缓存 {len(valid)}/{len(rows)} · 最新来源 {max(valid) if valid else '暂无'} · 需检查 {checks} · 日线参考 / 非实时")
    else:
        columns = st.columns(3)
        columns[0].metric("有行情的标的", f"{len(valid)} / {len(rows)}")
        columns[1].metric("最新来源日期", max(valid) if valid else "暂无")
        columns[2].metric("需检查的标的", str(checks))
    if not valid:
        st.info("尚无本地日线。先设置观察清单，再点击“更新”采集；刷新页面只重新读取本地文件。")
    elif stale:
        oldest = min(state["date"] for state in states if state["date"])
        age = max(state["age"] for state in states if state["age"] is not None)
        st.warning(f"部分行情需更新：最早来源日期 {oldest}，距今 {age} 个自然日。请明确点击更新；失败时保留旧行情。")
    if not compact:
        st.caption("日期按标的分别记录；各市场交易日历尚未核验，缓存存在不代表交易日完整。")


def render_update_result(result):
    if not result:
        return
    rows = result.get("results", [])
    success = sum(row.get("status") == "success" for row in rows)
    if rows:
        st.caption(f"上次明确采集：{success} / {len(rows)} 个标的成功；未成功的标的继续显示原日期。")
        failures = [row for row in rows if row.get("status") == "failed"]
        if failures:
            with st.expander("查看上次更新失败原因", expanded=True):
                for row in failures:
                    st.write(f"{row.get('asset_id', '标的')}：{row.get('error', '未知错误')}")
