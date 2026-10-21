from __future__ import annotations

import json
from pathlib import Path

from quantview.domain.models import FetchResult


def save_raw_snapshot(result: FetchResult, raw_root: Path) -> Path:
    date_value = result.metadata.get("end") or result.fetched_at.date().isoformat()
    target = raw_root / result.source / str(date_value) / result.raw_batch_id
    target.mkdir(parents=True, exist_ok=True)
    safe_symbol = "".join(ch for ch in result.source_symbol if ch.isalnum() or ch in "-_")
    parquet_path = target / f"daily_{safe_symbol}.parquet"
    result.frame.to_parquet(parquet_path, index=False)
    metadata = {
        **result.metadata,
        "source": result.source,
        "source_symbol": result.source_symbol,
        "fetched_at": result.fetched_at.isoformat(),
        "raw_batch_id": result.raw_batch_id,
        "rows": len(result.frame),
    }
    (target / f"daily_{safe_symbol}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return parquet_path
