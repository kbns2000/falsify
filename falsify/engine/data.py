"""Dataset loading and normalisation.

Built-in data: a small CC0 daily OHLCV pack (see data/DATASETS.md).
Uploads: any CSV with a timestamp column and at least a close column.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

REQUIRED = ["ts", "open", "high", "low", "close", "volume"]

# candidate column names, lower-cased
_TS_NAMES = ["timestamp", "ts", "time", "date", "datetime", "open_time", "candle_date_time_utc", "candle_date_time_kst", "opentime"]
_ALIASES = {
    "open": ["open", "opening_price", "o", "open_price"],
    "high": ["high", "high_price", "h", "max"],
    "low": ["low", "low_price", "l", "min"],
    "close": ["close", "trade_price", "c", "close_price", "price", "adj close", "adj_close"],
    "volume": ["volume", "candle_acc_trade_volume", "vol", "v", "quantity", "base_volume"],
}


@dataclass
class DatasetInfo:
    id: str
    label: str
    timeframe: str
    source: str
    license: str
    assets: list[str] = field(default_factory=list)
    rows: int = 0
    start: str = ""
    end: str = ""


class DataError(ValueError):
    pass


def _normalise(df: pd.DataFrame, asset: str | None = None) -> pd.DataFrame:
    cols = {c: str(c).strip().lower() for c in df.columns}
    df = df.rename(columns=cols)
    ts_col = next((c for c in _TS_NAMES if c in df.columns), None)
    if ts_col is None:
        raise DataError("No timestamp column found (looked for: %s)" % ", ".join(_TS_NAMES))
    out = pd.DataFrame()
    ts = df[ts_col]
    if pd.api.types.is_numeric_dtype(ts):
        # epoch seconds or milliseconds
        unit = "ms" if ts.astype("int64").abs().max() > 10**11 else "s"
        out["ts"] = pd.to_datetime(ts, unit=unit, utc=True)
    else:
        out["ts"] = pd.to_datetime(ts, utc=True, errors="coerce")
    for std, names in _ALIASES.items():
        col = next((n for n in names if n in df.columns), None)
        if col is not None:
            out[std] = pd.to_numeric(df[col], errors="coerce")
    if "close" not in out:
        raise DataError("No close/price column found")
    for c in ("open", "high", "low"):
        if c not in out:
            out[c] = out["close"]
    if "volume" not in out:
        out["volume"] = 0.0
    if asset is not None:
        out["asset"] = asset
    elif "asset" in df.columns:
        out["asset"] = df["asset"].astype(str)
    elif "symbol" in df.columns:
        out["asset"] = df["symbol"].astype(str)
    elif "market" in df.columns:
        out["asset"] = df["market"].astype(str)
    else:
        out["asset"] = "UPLOAD"
    out = out.dropna(subset=["ts", "close"]).sort_values(["asset", "ts"]).reset_index(drop=True)
    out = out.drop_duplicates(subset=["asset", "ts"], keep="last").reset_index(drop=True)
    if len(out) < 50:
        raise DataError("Need at least 50 rows of data, got %d" % len(out))
    return out[["asset"] + REQUIRED]


@lru_cache(maxsize=1)
def _builtin() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "top10_daily.csv")
    raw = pd.read_csv(path)
    return _normalise(raw)


def builtin_info() -> DatasetInfo:
    df = _builtin()
    return DatasetInfo(
        id="builtin:top10_daily",
        label="Top-10 crypto, daily (Binance spot, CC0)",
        timeframe="1d",
        source="HF gokulalex/top10-crypto-daily-ohlcv-2024-2026",
        license="CC0-1.0",
        assets=sorted(df["asset"].unique().tolist()),
        rows=len(df),
        start=str(df["ts"].min().date()),
        end=str(df["ts"].max().date()),
    )


def upload_info(dataset_id: str) -> DatasetInfo:
    df = _frame_for(dataset_id)
    return DatasetInfo(
        id=dataset_id,
        label="Uploaded CSV",
        timeframe=_guess_timeframe(df),
        source="user upload",
        license="user",
        assets=sorted(df["asset"].unique().tolist()),
        rows=len(df),
        start=str(df["ts"].min().date()),
        end=str(df["ts"].max().date()),
    )


def list_assets(dataset_id: str = "builtin:top10_daily") -> list[str]:
    return sorted(_frame_for(dataset_id)["asset"].unique().tolist())


def _frame_for(dataset_id: str) -> pd.DataFrame:
    if dataset_id.startswith("builtin"):
        return _builtin()
    if dataset_id.startswith("upload:"):
        path = os.path.join(UPLOAD_DIR, dataset_id.split(":", 1)[1] + ".parquet")
        if not os.path.exists(path):
            raise DataError("Unknown upload %s" % dataset_id)
        return pd.read_parquet(path)
    raise DataError("Unknown dataset %s" % dataset_id)


def resolve_asset(name: str, dataset_id: str = "builtin:top10_daily") -> str:
    """Map loose names (btc, BTCUSDT, bitcoin) onto a dataset asset id."""
    assets = list_assets(dataset_id)
    key = name.strip().upper().replace("-", "/").replace("_", "/")
    nick = {"BITCOIN": "BTC", "ETHER": "ETH", "ETHEREUM": "ETH", "SOLANA": "SOL", "RIPPLE": "XRP", "DOGECOIN": "DOGE", "CARDANO": "ADA", "AVALANCHE": "AVAX", "CHAINLINK": "LINK", "TONCOIN": "TON", "BINANCE COIN": "BNB"}
    key = nick.get(key, key)
    for a in assets:
        if key == a.upper():
            return a
    for a in assets:
        base = a.split("/")[0].upper()
        if key == base or key == base + "/USDT" or key == base + "USDT" or key == base + "USD" or key.startswith(base + "/"):
            return a
    raise DataError("Asset %r not in dataset. Available: %s" % (name, ", ".join(assets)))


def load(asset: str, dataset_id: str = "builtin:top10_daily", start: str | None = None, end: str | None = None) -> pd.DataFrame:
    df = _frame_for(dataset_id)
    asset = resolve_asset(asset, dataset_id)
    out = df[df["asset"] == asset].copy()
    if start:
        out = out[out["ts"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        out = out[out["ts"] <= pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)]
    out = out.reset_index(drop=True)
    if len(out) < 50:
        raise DataError("Only %d rows for %s in the chosen window" % (len(out), asset))
    return out


def save_upload(content: bytes, upload_id: str, asset_label: str | None = None) -> DatasetInfo:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    text = content.decode("utf-8-sig", errors="replace")
    raw = pd.read_csv(io.StringIO(text))
    df = _normalise(raw, asset=asset_label)
    df.to_parquet(os.path.join(UPLOAD_DIR, upload_id + ".parquet"))
    tf = _guess_timeframe(df)
    return DatasetInfo(
        id="upload:" + upload_id,
        label="Uploaded CSV (%s)" % (asset_label or ", ".join(sorted(df["asset"].unique())[:3])),
        timeframe=tf,
        source="user upload",
        license="user",
        assets=sorted(df["asset"].unique().tolist()),
        rows=len(df),
        start=str(df["ts"].min().date()),
        end=str(df["ts"].max().date()),
    )


def _guess_timeframe(df: pd.DataFrame) -> str:
    one = df[df["asset"] == df["asset"].iloc[0]]
    if len(one) < 3:
        return "1d"
    delta = one["ts"].diff().dropna().median()
    hours = delta.total_seconds() / 3600
    if hours >= 20:
        return "1d"
    if hours >= 3.5:
        return "4h"
    if hours >= 0.9:
        return "1h"
    if hours >= 0.2:
        return "15m"
    return "1m"


def bars_per_year(timeframe: str) -> float:
    return {"1d": 365.0, "4h": 365 * 6.0, "1h": 365 * 24.0, "15m": 365 * 96.0, "1m": 365 * 1440.0}.get(timeframe, 365.0)
