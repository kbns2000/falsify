"""The pre-registered claim: what exactly will be tested, frozen before any result is seen."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .dsl import compile_expression, DSLError


class ExitRule(BaseModel):
    bars: int = Field(5, ge=1, le=500, description="Hold for this many bars, then exit at that bar's close")
    stop_pct: Optional[float] = Field(None, gt=0, le=95, description="Stop loss in percent (optional)")
    target_pct: Optional[float] = Field(None, gt=0, le=1000, description="Take profit in percent (optional)")
    exit_condition: Optional[str] = Field(None, description="Optional DSL condition that closes the position at the next open")

    @field_validator("exit_condition")
    @classmethod
    def _check_exit(cls, v):
        if v:
            compile_expression(v)
        return v or None


class Criterion(BaseModel):
    """Pre-registered definition of 'the belief is true'."""
    metric: Literal["mean_net_return", "win_rate", "total_net_return"] = "mean_net_return"
    beats: Literal["random_entries", "zero", "buy_and_hold"] = "random_entries"
    alpha: float = Field(0.05, gt=0, lt=0.5, description="One-sided significance level against the random-entry baseline")
    min_trades: int = Field(20, ge=5, le=10000)


class ClaimSpec(BaseModel):
    title: str = Field(..., min_length=3, max_length=120)
    belief_text: str = Field(..., min_length=3, max_length=2000)
    asset: str = Field(..., description="Dataset asset id, e.g. BTC/USDT")
    dataset_id: str = "builtin:binance_daily"
    timeframe: Literal["1d", "4h", "1h", "15m", "1m"] = "1d"
    direction: Literal["long", "short"] = "long"
    entry: str = Field(..., description="DSL condition evaluated at the close of each bar")
    exit: ExitRule = Field(default_factory=ExitRule)
    position: Literal["one_at_a_time", "every_signal"] = "one_at_a_time"
    fee_bps_per_side: float = Field(10.0, ge=0, le=200, description="Fee + slippage per side in basis points (10 = 0.10%)")
    start: Optional[str] = None
    end: Optional[str] = None
    criterion: Criterion = Field(default_factory=Criterion)
    plain_language: str = Field("", max_length=2000, description="The formal rule restated in plain words")
    language: str = Field("en", description="Language of the belief text, BCP-47 (en, ko, ...)")
    notes: str = Field("", max_length=2000)

    @field_validator("entry")
    @classmethod
    def _check_entry(cls, v):
        compile_expression(v)  # raises DSLError
        return v.strip()


def canonical_json(spec: ClaimSpec) -> str:
    return json.dumps(spec.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def seal(spec: ClaimSpec, prev_hash: str = "") -> dict:
    """Freeze the spec: hash of canonical JSON chained to the previous ledger entry."""
    body = canonical_json(spec)
    sealed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    h = hashlib.sha256((prev_hash + "\n" + sealed_at + "\n" + body).encode("utf-8")).hexdigest()
    return {"spec_hash": h, "prev_hash": prev_hash, "sealed_at": sealed_at, "canonical": body}


__all__ = ["ClaimSpec", "ExitRule", "Criterion", "seal", "canonical_json", "DSLError"]
