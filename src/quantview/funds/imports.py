"""Bounded, non-executable tabular input for point-in-time market values."""
from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import ParseError

import pandas as pd

from quantview.funds.exposure import MAX_BYTES, parse_bundle

CODE = "基金代码"
VALUE = "市值（元）"
CSV_TEMPLATE = f"{CODE},{VALUE}\n518880,10000\n159920,5000\n"


def read_table(raw: bytes, filename: str) -> pd.DataFrame:
    if len(raw) > MAX_BYTES:
        raise ValueError("文件超过 1 MiB，请缩小文件后导入。")
    if filename.lower().endswith(".csv"):
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("CSV 编码无法识别，请另存为 UTF-8 CSV。")
        try:
            rows = list(csv.reader(io.StringIO(text), strict=True))
        except csv.Error as exc:
            raise ValueError("CSV 格式无效，请检查引号和分隔符。") from exc
    elif filename.lower().endswith(".xlsx"):
        try:
            from openpyxl import load_workbook
            from openpyxl.utils.exceptions import InvalidFileException
        except ImportError as exc:
            raise ValueError("Excel 导入需要 openpyxl，请更新项目依赖，或先使用 CSV。") from exc

        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 10 * MAX_BYTES:
                    raise ValueError("Excel 解压后过大，请只保留持仓表。")
            workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
            try:
                sheet = workbook.worksheets[0]
                if sheet.max_row > 501 or sheet.max_column > 30:
                    raise ValueError("表格最多 500 行持仓、30 列；读取第一个工作表。")
                rows = []
                for cells in sheet.iter_rows():
                    if any(cell.data_type == "f" for cell in cells):
                        raise ValueError("Excel 含公式，请先粘贴为值后导入。")
                    rows.append(["" if cell.value is None else str(cell.value) for cell in cells])
            finally:
                workbook.close()
        except (zipfile.BadZipFile, KeyError, OSError, ParseError, InvalidFileException) as exc:
            raise ValueError("无法读取 XLSX，请检查文件或改用 CSV。") from exc
    else:
        raise ValueError("仅支持 CSV 或无公式的 XLSX 文件。")
    rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if len(rows) < 2:
        raise ValueError("表格需要表头和至少一行持仓。")
    header = [str(item).strip() for item in rows[0]]
    if not 2 <= len(header) <= 30 or not all(header) or len(set(header)) != len(header):
        raise ValueError("表头须有 2–30 个非空、不重复的列名。")
    if len(rows) > 501 or any(len(row) != len(header) for row in rows[1:]):
        raise ValueError("表格最多 500 行持仓，且各行列数必须一致。")
    return pd.DataFrame(rows[1:], columns=header)


def positions_from_table(frame: pd.DataFrame, code_column=CODE, value_column=VALUE) -> list[dict]:
    if code_column == value_column or not {code_column, value_column} <= set(frame.columns):
        raise ValueError("请选择不同的基金代码列和市值列。")
    if len(frame) > 500:
        raise ValueError("最多导入 500 行持仓。")
    positions, seen = [], set()
    for number, (_, row) in enumerate(frame.iterrows(), 2):
        code, amount = row[code_column], row[value_column]
        code = "" if pd.isna(code) else str(code).strip()
        text = "" if pd.isna(amount) else str(amount).strip()
        if not code and not text:
            continue
        if not re.fullmatch(r"[15][0-9]{5}", code):
            raise ValueError(f"第 {number} 行：基金代码须为 1 或 5 开头的六位文本。")
        if code in seen:
            raise ValueError(f"第 {number} 行：{code} 重复，请先合并同一基金市值。")
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"第 {number} 行：市值不是有效数字。") from exc
        if not value.is_finite() or not 0 < value <= Decimal("1e15") or not math.isfinite(float(value)):
            raise ValueError(f"第 {number} 行：市值须为有效正数。")
        positions.append({"code": code, "market_value_cny": float(value)})
        seen.add(code)
    if not positions:
        raise ValueError("请至少填写一行有效持仓。")
    return positions


def build_table_bundle(frame, *, as_of: datetime, total: float, source: str, disclosures=None, now=None):
    import json

    value = {
        "schema_version": 1, "as_of": as_of.isoformat(), "source": source,
        "securities_value_cny": total, "positions": positions_from_table(frame),
        "disclosures": disclosures or [],
    }
    return parse_bundle(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), now=now)
