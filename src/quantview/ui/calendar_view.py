from __future__ import annotations

from calendar import monthcalendar
from datetime import date, datetime

import streamlit as st

from quantview.data.repository import Repository
from quantview.operations.calendar import load_calendar
from quantview.operations.calendar_sync import read_backup, sync_calendar


def render_calendar_backup(settings, state):
    instant = datetime.fromisoformat(state["as_of"])
    if state.get("coverage_start"):
        st.caption(f"已核验覆盖：{state['coverage_start']} — {state['coverage_end']}")
    st.caption("年度日历本地保存，页面浏览不联网。通常每年补充一次；交易所临时调整时可手动重新校验。")
    year = int(st.number_input("日历年份", min_value=2000, max_value=2100, value=instant.year, step=1, key="calendar_sync_year"))
    refresh = st.checkbox("重新联网校验（已有备份时可不选）", key="calendar_force_refresh")
    if st.button("同步并备份所选年度", key="calendar_sync_submit", type="primary"):
        st.session_state.pop("calendar_sync_message", None)
        try:
            with st.spinner("正在读取备份或同步公开日历…"):
                result = sync_calendar(settings, year=year, refresh=refresh)
            label = {"synced": "公开日历已同步并备份", "restored": "已从本地备份恢复日历", "cached": "本地日历与备份一致，无需联网"}[result["status"]]
            st.session_state["calendar_sync_message"] = {"year": year, "text": f"{label}：{year} 年，{result['trading_days']} 个交易日。"}
            st.rerun()
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    message = st.session_state.get("calendar_sync_message")
    if message and message["year"] == year:
        st.success(message["text"])
    try:
        backup = read_backup(settings, year)
    except (ValueError, OSError) as exc:
        st.error(str(exc))
        backup = None
    if backup:
        content, metadata = backup
        st.caption(f"本地备份：{metadata['coverage_start']} — {metadata['coverage_end']} · {metadata['trading_days']} 个交易日")
        st.caption(f"获取时间：{metadata['fetched_at'][:19].replace('T', ' ')}（上海）")
        st.caption(f"Markets Open · CC BY 4.0 · 版本 {metadata['data_version']}")
        st.download_button("下载逐日日历 CSV", content, f"DCE_{year}_calendar.csv", "text/csv", key="calendar_download_csv")
        with st.expander("备份与公告依据"):
            st.code(metadata["backup_directory"], language=None)
            st.markdown(f"[公开数据来源]({metadata['source_url']}) · [许可](https://creativecommons.org/licenses/by/4.0/)")
            for url in sorted({row["notice_url"] for row in metadata["holidays"]}):
                st.markdown(f"[大商所年度公告]({url})")
            st.json(metadata)
    else:
        st.caption(f"尚无 {year} 年公开日历备份。点击同步后保存；来源未载入的年份会明确提示，不生成推测日历。")
    with st.expander("当前数据库日历详情"):
        st.json(state)


def render_calendar_month(settings, health):
    instant = datetime.fromisoformat(health["calendar"]["as_of"])
    with st.expander("查看交易日历 · 开市与休市"):
        left, right = st.columns(2)
        year = int(left.number_input("查看年份", min_value=2000, max_value=2100, value=instant.year, step=1, key="calendar_view_year"))
        month = right.selectbox("查看月份", list(range(1, 13)), index=instant.month - 1, key="calendar_view_month", format_func=lambda n: f"{n} 月")
        calendar = load_calendar(Repository(settings.database_path), settings.calendar_exchange)
        rows = []
        for week in monthcalendar(year, month):
            cells = []
            for number in week:
                if not number:
                    cells.append('<td class="blank"></td>')
                    continue
                day = date(year, month, number)
                flag = calendar.days.get(day)
                kind, label = ("open", "开市") if flag is True else ("closed", "休市") if flag is False else ("unknown", "未覆盖")
                today = " today" if day == instant.date() else ""
                cells.append(f'<td class="{kind}{today}"><strong>{number}</strong><span>{label}</span></td>')
            rows.append("<tr>" + "".join(cells) + "</tr>")
        st.markdown('''<style>
        .qv-calendar { width:100%; border-collapse:separate; border-spacing:4px; table-layout:fixed; }
        .qv-calendar caption { text-align:left; color:#e5edf5; padding:8px 4px; font-weight:600; }
        .qv-calendar th { font-weight:400; color:#94a6bd; text-align:center; border:0; padding:6px 0; }
        .qv-calendar td { text-align:center; border:1px solid #283547; border-radius:6px; padding:8px 2px; }
        .qv-calendar td strong { display:block; font-size:1rem; font-variant-numeric:tabular-nums; }
        .qv-calendar td span { display:block; font-size:.7rem; margin-top:4px; }
        .qv-calendar .open { background:#14322e; color:#73d4bb; }
        .qv-calendar .closed { background:#19222e; color:#9caec6; }
        .qv-calendar .unknown { background:#28251b; color:#d6af63; }
        .qv-calendar .today { border:2px solid #d6af63; }
        .qv-calendar .blank { border:0; }
        </style>''' + f'<table class="qv-calendar"><caption>大商所 · {year} 年 {month} 月 · 上海时区</caption><thead><tr>'
                    + "".join(f"<th>{day}</th>" for day in ("一", "二", "三", "四", "五", "六", "日"))
                    + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>", unsafe_allow_html=True)
        st.caption("绿色为已核验开市日，灰色为休市，金色边框为今天；未知日期标为“未覆盖”。这里只判断交易日，不提供夜盘时段或结算日历。")
        if not calendar.covers(date(year, 1, 1), date(year, 12, 31)):
            st.info("所选年度未完整覆盖，可在左侧“交易日历与本地备份”同步该年度。")
