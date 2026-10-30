"""Fetch public candles (no API key) and write Falsify-ready CSVs.

Binance spot daily since 2017 for several USDT pairs, Binance hourly for one pair,
and optionally Upbit KRW markets (the Korean exchanges the author actually trades).

Usage:
    python scripts/fetch_public_candles.py binance-daily  --out data/binance_daily.csv
    python scripts/fetch_public_candles.py binance-hourly --symbol BTCUSDT --out data/btcusdt_1h.csv
    python scripts/fetch_public_candles.py upbit-daily    --out data/upbit_daily.csv

Only public endpoints are used. This script never places orders.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone

import httpx

BINANCE = "https://api.binance.com/api/v3/klines"
UPBIT = "https://api.upbit.com/v1/candles/days"
DEFAULT_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT"]
UPBIT_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-DOGE", "KRW-ADA", "KRW-AVAX", "KRW-LINK"]


def binance_klines(symbol: str, interval: str, start_ms: int, client: httpx.Client):
    out = []
    cur = start_ms
    while True:
        r = client.get(BINANCE, params={"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1000}, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        for k in rows:
            out.append({"timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "asset": symbol[:-4] + "/USDT", "open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5]})
        if len(rows) < 1000:
            break
        cur = rows[-1][0] + 1
        time.sleep(0.25)
    # drop the still-open last candle
    return out[:-1] if out else out


def upbit_days(market: str, client: httpx.Client, max_rows: int = 4000):
    out = []
    to = None
    while len(out) < max_rows:
        params = {"market": market, "count": 200}
        if to:
            params["to"] = to
        r = client.get(UPBIT, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        for c in rows:
            out.append({"timestamp": c["candle_date_time_utc"].replace("T", " "), "asset": market, "open": c["opening_price"], "high": c["high_price"], "low": c["low_price"], "close": c["trade_price"], "volume": c["candle_acc_trade_volume"]})
        to = rows[-1]["candle_date_time_utc"] + "Z"
        if len(rows) < 200:
            break
        time.sleep(0.15)
    out.sort(key=lambda x: x["timestamp"])
    return out[:-1] if out else out


def write(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "asset", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows(rows)
    print("wrote %d rows -> %s" % (len(rows), path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["binance-daily", "binance-hourly", "upbit-daily"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    ap.add_argument("--since", default="2017-08-17")
    a = ap.parse_args()
    start_ms = int(datetime.strptime(a.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    rows = []
    with httpx.Client(headers={"User-Agent": "falsify-data-fetch/0.1"}) as client:
        if a.what == "binance-daily":
            for p in a.pairs.split(","):
                p = p.strip()
                got = binance_klines(p, "1d", start_ms, client)
                print(p, len(got), file=sys.stderr)
                rows += got
        elif a.what == "binance-hourly":
            rows = binance_klines(a.symbol, "1h", start_ms, client)
        else:
            for m in UPBIT_MARKETS:
                got = upbit_days(m, client)
                print(m, len(got), file=sys.stderr)
                rows += got
    write(rows, a.out)


if __name__ == "__main__":
    main()
