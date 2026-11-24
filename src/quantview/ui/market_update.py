from __future__ import annotations

import streamlit as st

from quantview.metals.jobs import job_status, start_update
from quantview.ui.data_status import render_update_result


def busy(root):
    return (job_status(root) or {}).get("status") == "running"


def update_button(service, label, key, *, funds_only=False, disabled=False):
    if st.button(label, key=key, type="primary", disabled=disabled or busy(service.settings.root)):
        try:
            start_update(service, funds_only=funds_only)
            st.rerun()
        except (ValueError, OSError) as exc:
            st.error(str(exc))


def _render_status(service):
    job = job_status(service.settings.root)
    if not job:
        return
    done = len(job["results"])
    if job["status"] == "running":
        st.progress(min(done / job["total"], 1.0), text=f"后台更新 · {done}/{job['total']} 完成 · 正在采集 {job['current'] or '准备中'}")
        st.caption("可以切换页面并浏览已发布缓存。每个来源有超时限制；关闭浏览器不影响任务，停止看板进程会中断尚未完成的采集。")
        return
    refresh_key = "market_job_refreshed"
    if st.session_state.get(refresh_key) != job["id"]:
        st.session_state[refresh_key] = job["id"]
        st.rerun()
    if job.get("error"):
        st.error(f"后台更新未完成：{job['error']}")
    else:
        st.caption(f"{job['scope']} · {job['finished_at'][:19].replace('T', ' ')} · 后台任务已结束")
    render_update_result(job)
    failed = [row["asset_id"] for row in job["results"] if row["status"] == "failed"]
    if failed and st.button("仅重试失败标的", key="market_retry_failed"):
        try:
            start_update(service, asset_ids=failed)
            st.rerun()
        except (ValueError, OSError) as exc:
            st.error(str(exc))


@st.fragment(run_every=1)
def _poll_status(service):
    _render_status(service)


def render_job_status(service):
    if busy(service.settings.root):
        _poll_status(service)
    else:
        _render_status(service)
