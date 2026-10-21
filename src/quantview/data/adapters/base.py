from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from quantview.domain.models import FetchResult


class DataSourceError(RuntimeError):
    pass


class DailyDataAdapter(ABC):
    name: str

    @abstractmethod
    def fetch_daily(self, symbol: str, start: date, end: date) -> FetchResult:
        raise NotImplementedError

    def discover_contracts(self, product: str, product_name: str) -> list[str]:
        return []

