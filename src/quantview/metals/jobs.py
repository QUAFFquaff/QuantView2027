"""Single-process background updates shared by all browser sessions.

Workers never call Streamlit. Published quotes/attempts remain durable through
the existing atomic service; the progress registry intentionally lives in memory.
"""
from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path

from quantview.metals.service import MetalsService
from quantview.operations.clock import shanghai_now

_guard = threading.RLock()
_jobs: dict[str, dict] = {}
_threads: dict[str, threading.Thread] = {}


def _key(root: Path) -> str:
    return str(root.resolve()).casefold()


def job_status(root: Path) -> dict | None:
    with _guard:
        return copy.deepcopy(_jobs.get(_key(root)))


def start_update(service: MetalsService, *, funds_only=False, asset_ids=None) -> str:
    key = _key(service.settings.root)
    allowed = service.watchlist() if funds_only else service.assets()
    requested = set(asset_ids) if asset_ids is not None else {a.asset_id for a in allowed}
    assets = [a for a in allowed if a.asset_id in requested]
    if not assets or requested - {a.asset_id for a in allowed}:
        raise ValueError("更新对象已变化或为空，请重新读取自选后重试。")
    with _guard:
        if _jobs.get(key, {}).get("status") == "running":
            raise ValueError("已有行情更新在后台运行，请查看进度。")
        identity = uuid.uuid4().hex
        _jobs[key] = {"id": identity, "status": "running", "started_at": shanghai_now().isoformat(),
                      "scope": "基金日线" if funds_only else "公开行情", "total": len(assets),
                      "current": None, "results": [], "error": None}

        def progress(asset):
            with _guard:
                _jobs[key]["current"] = asset.symbol

        def completed(attempt):
            with _guard:
                _jobs[key]["results"].append(copy.deepcopy(attempt))

        def run():
            try:
                result = service.sync(funds_only=funds_only, asset_ids=requested, on_progress=progress, on_result=completed)
                with _guard:
                    _jobs[key].update(result, current=None, finished_at=shanghai_now().isoformat())
            except Exception as exc:
                with _guard:
                    _jobs[key].update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:800]}")
            finally:
                with _guard:
                    _jobs[key].update(current=None, finished_at=shanghai_now().isoformat())

        thread = threading.Thread(target=run, name=f"quantview-market-{identity[:8]}", daemon=True)
        _threads[key] = thread
        try:
            thread.start()
        except RuntimeError as exc:
            _threads.pop(key, None)
            _jobs[key].update(status="failed", error=str(exc), finished_at=shanghai_now().isoformat())
            raise ValueError("无法启动后台任务，请稍后重试。") from exc
        return identity


def wait_update(root: Path, timeout=5) -> None:
    """For diagnostics and tests. Browser rendering never waits for a worker."""
    with _guard:
        thread = _threads.get(_key(root))
    if thread:
        thread.join(timeout)
