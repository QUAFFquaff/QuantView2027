from __future__ import annotations

import os
from dataclasses import dataclass
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
    def ai_model(self) -> str:
        model_env = self.app["ai"].get("model_env", "QUANTVIEW_AI_MODEL")
        return os.getenv(model_env) or self.app["ai"].get("default_model", "gpt-5-mini")


def load_settings(root: Path | None = None) -> Settings:
    project_root = root.resolve() if root else find_project_root()
    load_dotenv(project_root / ".env")
    app = _read_yaml(project_root / "config" / "app.yaml")
    product_config = _read_yaml(project_root / "config" / "products.yaml")
    spreads = _read_yaml(project_root / "config" / "spreads.yaml")
    settings = Settings(
        root=project_root,
        app=app,
        products=product_config.get("products", {}),
        spreads=spreads,
    )
    for name in ("database", "raw", "reports", "logs"):
        path = settings.path(name)
        (path.parent if name == "database" else path).mkdir(parents=True, exist_ok=True)
    return settings

