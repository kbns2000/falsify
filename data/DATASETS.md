# Bundled datasets

| id | file | coverage | source | license |
|---|---|---|---|---|
| `builtin:binance_daily` (default) | `binance_daily.csv` | BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX, LINK, LTC vs USDT · daily · 2017-08-17 → 2026-10-29 | Binance public klines, fetched with `scripts/fetch_public_candles.py binance-daily` (no API key) | exchange public market data |
| `builtin:btcusdt_1h` | `btcusdt_1h.csv` | BTC/USDT · hourly · 2020-01-01 → 2026-10-30 | same script, `binance-hourly` | exchange public market data |
| `builtin:top10_daily` | `top10_daily.csv` | 10 USDT pairs · daily · 2024-01-01 → 2026-05-29 | Hugging Face dataset `gokulalex/top10-crypto-daily-ohlcv-2024-2026` | CC0-1.0 |

Columns everywhere: `timestamp,asset,open,high,low,close,volume` (UTC).

Refresh or extend with `scripts/fetch_public_candles.py` (Binance / Upbit public endpoints). The deployed demo never needs an exchange account, and nothing here places orders.

Falsify never gives investment advice. The data is here so the agent can test beliefs about the past, not predict the future.
