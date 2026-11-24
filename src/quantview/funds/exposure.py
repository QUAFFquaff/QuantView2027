"""Point-in-time, partial look-through from locally imported data."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from quantview.funds.catalog import STARTERS
from quantview.metals.service import _atomic, _lock
from quantview.operations.clock import SHANGHAI, shanghai_now

TEMPLATE = {
    "schema_version": 1,
    "as_of": "2026-01-01T15:00:00+08:00",
    "source": "示例：券商证券市值快照",
    "securities_value_cny": 1000,
    "positions": [{"code": "560860", "market_value_cny": 500}],
    "disclosures": [],
}
MAX_BYTES = 1024 * 1024


def _object(value, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label}字段无效，请按模板填写。")
    return value


def _array(value, limit: int, label: str) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{label}须为列表，最多 {limit} 项。")
    return value


def _number(value, label: str) -> Decimal:
    if type(value) not in {int, float}:
        raise ValueError(f"{label}须为正数。")
    number = Decimal(str(value))
    if not number.is_finite() or not 0 < number <= Decimal("1e15"):
        raise ValueError(f"{label}须为有效正数。")
    return number


def _text(value, label: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label}无效。")
    return value


def _code(value) -> str:
    code = _text(value, "基金代码", 6)
    if not re.fullmatch(r"[15][0-9]{5}", code):
        raise ValueError("基金代码须为六位文本。")
    return code


def _date(value) -> date:
    try:
        text = _text(value, "日期", 10)
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
            raise ValueError
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("日期须使用 YYYY-MM-DD。") from exc


def _json_object(items: list[tuple]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("JSON 有重复字段。")
        result[key] = value
    return result


def parse_bundle(raw: bytes, *, now: datetime | None = None) -> dict:
    if len(raw) > MAX_BYTES:
        raise ValueError("导入文件过大。")
    try:
        value = json.loads(raw, object_pairs_hook=_json_object)
    except (ValueError, RecursionError) as exc:
        raise ValueError("无法读取 JSON 导入文件。") from exc
    _object(value, set(TEMPLATE), "快照")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("不支持的快照版本。")
    instant = datetime.fromisoformat(_text(value["as_of"], "快照时点", 40))
    current = shanghai_now(now)
    if instant.tzinfo is None or instant > current:
        raise ValueError("快照时点必须带时区且不得位于未来。")
    _text(value["source"], "持仓来源")
    total = _number(value["securities_value_cny"], "证券总市值")
    seen: set[str] = set()
    subtotals = Decimal(0)
    for row in _array(value["positions"], 500, "持仓"):
        _object(row, {"code", "market_value_cny"}, "持仓")
        code = _code(row["code"])
        if code in seen:
            raise ValueError("同一基金不能重复计入快照。")
        seen.add(code)
        subtotals += _number(row["market_value_cny"], "基金市值")
    if subtotals > total:
        raise ValueError("导入基金市值超过证券总市值。")

    reports: set[tuple] = set()
    for report in _array(value["disclosures"], 200, "公开披露"):
        _object(report, {"code", "period_end", "published_on", "source_url", "holdings"}, "披露")
        code = _code(report["code"])
        if code not in STARTERS or not STARTERS[code].stock:
            raise ValueError("只有已核验的股票型基金可导入成分权重。")
        end, published = _date(report["period_end"]), _date(report["published_on"])
        if end > published or published > current.date():
            raise ValueError("报告期末和发布日期顺序无效或位于未来。")
        url = urlsplit(_text(report["source_url"], "披露链接", 1000))
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("披露来源须为 HTTPS 链接。")
        key = (code, end, published)
        if key in reports:
            raise ValueError("同基金、同报告期和发布日期有重复披露。")
        reports.add(key)
        ids: set[str] = set()
        covered = Decimal(0)
        for holding in _array(report["holdings"], 1000, "底层持仓"):
            _object(holding, {"security_id", "name", "weight"}, "底层持仓")
            security_id = _text(holding["security_id"], "底层证券标识", 15)
            if not re.fullmatch(r"(?:SSE|SZSE):[0-9]{6}|HKEX:[0-9]{5}", security_id) or security_id in ids:
                raise ValueError("底层证券须为唯一交易所代码（SSE/SZSE 六位，HKEX 五位）。")
            ids.add(security_id)
            _text(holding["name"], "底层证券名称")
            covered += _number(holding["weight"], "基金净值权重")
        if not 0 < covered <= 1:
            raise ValueError("基金净值权重之和须大于 0 且不超过 1；请用小数填写。")
    return value


def exposure_path(root: Path) -> Path:
    return root / "data" / "portfolio" / "fund_exposure.json"


def bundle_revision(bundle: dict | None) -> str:
    canonical = json.dumps(bundle, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def save_bundle(root: Path, raw: bytes, *, expected_revision: str | None = None) -> None:
    bundle = parse_bundle(raw)
    path = exposure_path(root)
    with _lock(path.parent):
        current = load_bundle(root)
        if expected_revision is not None and bundle_revision(current) != expected_revision:
            raise ValueError("快照已被其他操作修改，请重新预览后保存。")
        if current == bundle:
            return
        # Archive the previous published copy first. The current copy is also
        # included by bundle_history, so a failed publish cannot expose a draft.
        for version in (current,):
            if version is not None:
                destination = path.parent / "fund_exposure_versions" / f"{bundle_revision(version)}.json"
                if not destination.exists():
                    _atomic(destination, version)
        _atomic(path, bundle)


def bundle_history(root: Path) -> list[dict]:
    directory = exposure_path(root).parent / "fund_exposure_versions"
    result = {}
    for path in directory.glob("*.json"):
        bundle = parse_bundle(path.read_bytes())
        if bundle_revision(bundle) != path.stem:
            raise ValueError("历史快照校验失败，未展示损坏的版本。")
        result[path.stem] = {"revision": path.stem, "bundle": bundle}
    current = load_bundle(root)
    if current is not None:
        revision = bundle_revision(current)
        result[revision] = {"revision": revision, "bundle": current}
    return sorted(result.values(), key=lambda item: datetime.fromisoformat(item["bundle"]["as_of"]), reverse=True)


def load_bundle(root: Path) -> dict | None:
    path = exposure_path(root)
    try:
        with path.open("rb") as stream:
            return parse_bundle(stream.read(MAX_BYTES + 1))
    except FileNotFoundError:
        return None


def estimate(bundle: dict) -> dict:
    as_of = datetime.fromisoformat(bundle["as_of"]).astimezone(SHANGHAI)
    total = Decimal(str(bundle["securities_value_cny"]))
    selected: dict[str, dict] = {}
    for report in sorted(bundle["disclosures"], key=lambda row: (row["period_end"], row["published_on"])):
        # Date-only disclosure is not enough to prove availability earlier that day.
        if _date(report["published_on"]) < as_of.date():
            selected[report["code"]] = report

    groups: dict[str, Decimal] = {}
    equities: dict[str, dict] = {}
    fund_rows = []
    for row in bundle["positions"]:
        code = row["code"]
        value = Decimal(str(row["market_value_cny"]))
        profile = STARTERS.get(code)
        category = profile.exposure if profile else "产品类型未核验"
        groups[category] = groups.get(category, Decimal(0)) + value
        report = selected.get(code)
        known = Decimal(0)
        if report:
            for holding in report["holdings"]:
                amount = value * Decimal(str(holding["weight"]))
                known += amount
                entry = equities.setdefault(holding["security_id"], {"name": holding["name"], "value": Decimal(0), "funds": {}})
                entry["value"] += amount
                entry["funds"][code] = amount
        fund_rows.append({
            "code": code, "name": profile.name if profile else code, "category": category,
            "value": value, "known": known, "remaining": value - known, "report": report,
            "status": "已估算" if report else "不适用股票穿透" if profile and not profile.stock else "无当时可用披露",
        })
    tracked = sum((row["value"] for row in fund_rows), Decimal(0))
    known = sum((row["known"] for row in fund_rows), Decimal(0))
    return {
        "as_of": as_of, "total": total, "tracked": tracked, "known": known,
        "outside": total - tracked, "groups": groups, "funds": fund_rows, "equities": equities,
    }
