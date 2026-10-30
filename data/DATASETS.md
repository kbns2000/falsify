# Bundled datasets

| file | source | license | coverage |
|---|---|---|---|
| `top10_daily.csv` | Hugging Face dataset `gokulalex/top10-crypto-daily-ohlcv-2024-2026` (Binance public spot daily candles, collected by the dataset author) | CC0-1.0 | 10 USDT pairs (ADA, AVAX, BNB, BTC, DOGE, ETH, LINK, SOL, XRP, TON), 2024-01-01 → 2026-05-29, daily |

Columns: `timestamp,asset,open,high,low,close,volume` (UTC days).

`scripts/fetch_public_candles.py` can refresh or extend the data from public exchange endpoints (Binance / Upbit, no API key). The deployed demo never needs an exchange account.

Falsify never gives investment advice. The data is here so the agent can test beliefs about the past, not predict the future.
