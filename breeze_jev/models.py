from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    open_interest: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d


@dataclass(frozen=True)
class Contract:
    stock_code: str
    expiry: str
    strike: float
    right: str
    lot_size: int

    @property
    def key(self) -> str:
        return f"{self.stock_code}:{self.expiry}:{self.right}:{self.strike:g}"


@dataclass
class Decision:
    action: str
    probabilities: dict[str, float]
    confidence: float
    latency_ms: float
    engine: str
    model: str
    cached: bool = False
    reason: str = ""


@dataclass
class Position:
    contract: Contract
    quantity: int
    entry_ts: datetime
    entry_price: float
    entry_decision_id: int | None = None
    bars_held: int = 0
    peak_price: float = 0.0


@dataclass
class PendingOrder:
    action: str
    contract: Contract
    quantity: int
    decision_id: int | None
    reason: str
    created_ts: datetime
