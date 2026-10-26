from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def find_project_root(start: Path | None = None) -> Path:
    """Find the nearest directory containing config/app.yaml."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "config" / "app.yaml").exists():
            return candidate
    raise FileNotFoundError("Could not find project root containing config/app.yaml")


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


@dataclass(frozen=True)
class Settings:
    root: Path
    app: dict[str, Any]
    products: dict[str, dict[str, Any]]
    spreads: dict[str, Any]

    def path(self, key: str) -> Path:
        return (self.root / self.app["paths"][key]).resolve()

    @property
    def database_path(self) -> Path:
        return self.path("database")

    @property
    def calendar_exchange(self) -> str:
        return self.app.get("calendar", {}).get("exchange", "DCE")

    @property
    def scheduled_time(self) -> time:
        raw = self.app.get("pipeline", {}).get("scheduled_time", "18:10")
        if not isinstance(raw, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw):
            raise ValueError("pipeline.scheduled_time must use HH:MM format")
        value = time.fromisoformat(raw)
        if value.tzinfo is not None or value.second or value.microsecond:
            raise ValueError("pipeline.scheduled_time must be a local HH:MM time")
        return value

    @property
    def ai_model(self) -> str:
        model_env = self.app["ai"].get("model_env", "QUANTVIEW_AI_MODEL")
        return os.getenv(model_env) or self.app["ai"].get("default_model", "gpt-5-mini")


def load_settings(root: Path | None = None) -> Settings:
    project_root = root.resolve() if root else find_project_root()
    load_dotenv(project_root / ".env")
    app = _read_yaml(project_root / "config" / "app.yaml")
    if app.get("runtime", {}).get("timezone", "Asia/Shanghai") != "Asia/Shanghai":
        raise ValueError("runtime.timezone must be Asia/Shanghai")
    if app.get("calendar", {}).get("exchange", "DCE") != "DCE":
        raise ValueError("calendar.exchange must be DCE")
    if set(app.get("runtime", {})) - {"timezone"} or set(app.get("calendar", {})) - {"exchange"}:
        raise ValueError("Unknown runtime or calendar configuration field")
    product_config = _read_yaml(project_root / "config" / "products.yaml")
    spreads = _read_yaml(project_root / "config" / "spreads.yaml")
    settings = Settings(
        root=project_root,
        app=app,
        products=product_config.get("products", {}),
        spreads=spreads,
    )
    _ = settings.scheduled_time
    for name in ("database", "raw", "reports", "logs"):
        path = settings.path(name)
        (path.parent if name == "database" else path).mkdir(parents=True, exist_ok=True)
    return settings

