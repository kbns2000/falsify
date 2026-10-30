# Falsify — the devil's advocate for market beliefs

> Say what you believe about the market. An AI will try to prove it wrong.

Falsify takes a belief in plain language — any language — turns it into **one exact, testable rule**,
**seals that rule in a public hash-chained ledger before any result exists**, replays it on historical
candles with a deterministic engine, and then runs a **battery of attacks** against it: random entries
with the same holding period, fee sensitivity, first half vs second half, nearby parameters, other assets,
multiple-testing correction, sample size, a look-ahead guard, and model-proposed counter-tests specific
to the belief. The verdict — **FALSIFIED / FRAGILE / SURVIVED (for now) / INCONCLUSIVE** — is decided by
the numbers and explained by Meta's open-weight **Muse Glimmer 30B** in the language the belief was written in.

It never tells you what to buy. It only tells you whether your belief survived an honest attempt to break it.

Built solo for the **Meta Global AI Developer Hackathon 2026** (Indie track, *Build for Your World*).

---

## Why

Retail traders — the author included — carry around beliefs like *"when RSI goes under 30 it always
bounces"* or *"three red days means a green one"*. They are almost never written down precisely, never
tested against a fair baseline, and quietly re-tuned after the fact until they look right. Backtesting
tools exist, but they are built to make a strategy *look good*; nothing in the stack is built to make a
belief *fail*.

Falsify is the missing adversary. Its three design rules:

1. **Seal before you see.** The rule is hashed and chained to the ledger *before* the first number is
   computed. No moving the goalposts after the result.
2. **The model never touches the numbers.** Muse Glimmer translates, argues and explains. A deterministic
   engine computes. The two cannot be mixed up because the engine only accepts a tiny, whitelisted rule
   language.
3. **A pass is not a forecast.** The best possible verdict is *SURVIVED (for now)*, and the report says so
   in its first line.

## How a claim is tried

| Stage | Who | What happens |
|---|---|---|
| 1. Understand | Muse Glimmer | Rewrites the belief as one rule in the Falsify rule language, names the asset, direction, holding period and the **pre-registered success criterion**, and lists every assumption it had to make. If the rule does not compile or produces zero signals, it is shown its own error and repairs it (max 4 attempts). It is forbidden from tuning the rule to improve results. |
| 2. Seal | engine | Canonical JSON of the rule is hashed with SHA-256 and chained to the previous ledger entry (`sha256(prev_hash + sealed_at + canonical)`). |
| 3. Test | engine | Signal evaluated at the close, entry at the **next open**, exit after N bars (or stop/target), fees charged on both sides. Equity curve, win rate, drawdown, buy-and-hold comparison. |
| 4. Attack | engine | The fixed battery below. Each attack returns PASS / WARN / FAIL with a one-line headline and details. |
| 5. Devil's advocate | Muse Glimmer | Proposes up to three counter-tests tailored to *this* belief — the mirror-image trade, a different horizon, the asset the user actually trades — which the engine runs under the same rules. |
| 6. Verdict + report | engine, then Muse Glimmer | The verdict is decided by fixed rules (below). The model then writes the report: what was tested, the strongest evidence against, what held up, what would change the verdict, caveats. |

### The attack battery

| Attack | Question it asks | Fails when |
|---|---|---|
| Sample size | Are there enough trades to say anything? | fewer than `min_trades` (default 20) |
| Look-ahead guard | Does the rule only use past and current bars? | any future-referencing construct (the grammar forbids it) |
| Random-entry baseline | Does the rule beat 2 000 random sets of the same number of trades with the same holding period? | one-sided bootstrap p ≥ α |
| Multiple testing | How many beliefs has *this searcher* already sealed on this asset? | Bonferroni-adjusted α is no longer met |
| Fee sensitivity | At what fee level does the edge vanish? | mean net return ≤ 0 at 25 bps per side |
| Time split | Does it hold in both halves of the data? | sign flips between halves |
| Parameter neighbourhood | Do nearby parameters (RSI 25/35, hold 3/8 …) also work? | fewer than half the neighbours positive; WARN under two thirds or when the chosen parameters beat every neighbour by a wide margin |
| Cross-asset | Does the same rule beat random on the other assets? | better than random on fewer than 40 % of them; WARN under 60 % |
| Opportunity cost | How does it compare with just holding the asset? | informational only |

### Verdict rules

* **INCONCLUSIVE** — fewer completed trades than the pre-registered minimum.
* **FALSIFIED** — the primary criterion (sealed in stage 2) is not met: the rule does not beat random
  entries at the (multiple-testing-adjusted) α, or its excess return is not positive.
* **FRAGILE** — the primary criterion is met, but fee sensitivity, time split, parameter neighbourhood or
  multiple testing fails.
* **SURVIVED (for now)** — everything above holds. The report still opens with "that is a statement
  about the past, not a forecast."

## Where Meta's models are used

Falsify uses a Meta model in three roles, all through one OpenAI-compatible client
(`falsify/llm/client.py`):

| Role | Prompt | Why a strong open model matters |
|---|---|---|
| Formalizer | `FORMALIZER_SYSTEM` + few-shot examples | Must map free text in any language to a strict grammar, pick sensible defaults, surface assumptions, and repair its own compile errors. |
| Devil's advocate | `ADVOCATE_SYSTEM` | Must reason about *why* a belief might be an artefact and design the counter-test that would expose it. |
| Reporter | `REPORTER_SYSTEM` | Must explain a statistical verdict honestly in the user's language without drifting into advice. |

Supported routes (first match wins when `FALSIFY_LLM_PROVIDER` is unset):

| `FALSIFY_LLM_PROVIDER` | Model | Needs |
|---|---|---|
| `hf` | **Muse Glimmer 30B** (open weights) via Hugging Face Inference Providers, `meta-models/Muse-Glimmer-30B` | `HF_TOKEN` |
| `custom` | Muse Glimmer 30B or Muse Spark Open on any OpenAI-compatible server (Ollama, vLLM, llama.cpp) | `LLM_BASE_URL`, `LLM_MODEL`, optional `LLM_API_KEY` |
| `meta` | Muse Spark 1.3 on the Meta Model API | `META_API_KEY` |
| `offline` | deterministic stub, no network | nothing — development and tests only |

If a configured route is unreachable, the pipeline falls back to the offline stub for that run and shows
a visible *degraded* badge on the result; the engine, ledger and attacks behave identically either way.

## Run it locally

```bash
git clone https://github.com/kbns2000/falsify && cd falsify
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**With Muse Glimmer on your own machine (Ollama ≥ 0.40):**

```bash
ollama pull muse-glimmer:30b
export FALSIFY_LLM_PROVIDER=custom LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=muse-glimmer:30b
uvicorn falsify.api:app --reload            # open http://localhost:8000
```

**With Hugging Face Inference Providers:**

```bash
export HF_TOKEN=hf_...                      # FALSIFY_LLM_PROVIDER=hf is implied
uvicorn falsify.api:app --reload
```

**Command line:**

```bash
python -m falsify.cli "When bitcoin's RSI goes under 30 it always bounces within a week"
python -m falsify.cli "3일 연속 떨어지면 다음날 반등하니까 그때 산다" --asset ETH/USDT --json
```

**Tests** (engine, attacks, ledger chain, API; no network):

```bash
python -m pytest -q
```

## Deploy

* **Render (free tier):** the repo ships a `render.yaml` Blueprint. Create a Blueprint from the repo and
  set `HF_TOKEN` in the dashboard. The service seeds its ledger from `ledger/seed/` on first start.
* **Docker:** `docker build -t falsify . && docker run -p 8000:8000 -e HF_TOKEN=... falsify`

Secrets are never read from the repository; `.env` and ledgers are git-ignored.

## Data

Built-in datasets need no account or API key:

| id | contents |
|---|---|
| `builtin:binance_daily` (default) | 10 majors (BTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, LINK, LTC) vs USDT, daily, 2017-08 → 2026-10, Binance public klines |
| `builtin:btcusdt_1h` | BTC/USDT hourly, 2020 → 2026 |
| `builtin:top10_daily` | CC0 daily set used for tests |

Regenerate or extend them with `scripts/fetch_public_candles.py`. Details in `data/DATASETS.md`.

**Upload your own CSV** from the home page: a timestamp column (`ts`, `time`, `date`, `timestamp`,
`open_time` …, ISO or epoch), `open high low close volume`, and optionally an `asset`/`symbol` column
for multi-asset files. The timeframe is inferred from the median bar spacing.

## The rule language

A deliberately tiny expression language, compiled through a Python AST whitelist — no attribute access,
no calls outside the catalogue, no loops, no future bars:

```
rsi(14) < 30
close > high_n(20) and vol_ratio(20) > 2
streak_down() >= 3
cross_above(sma(20), sma(50))
ret(1) < -10
dow() == 0 and hour() == 0
```

Columns `open high low close volume`; indicators `sma ema rsi atr atr_pct ret high_n low_n close_high_n
close_low_n bb_upper bb_lower bb_pct macd macd_signal macd_hist vol_sma vol_ratio drawdown runup
streak_down streak_up dow month hour day_of_month body_pct range_pct gap_pct zscore volatility stoch_k
obv bars_since_high`; helpers `shift cross_above cross_below pct_diff abs`. The exact grammar the model
sees is returned by `GET /api/meta` (`grammar`).

## The ledger

Append-only JSON lines, one entry per sealed claim, each carrying `spec_hash`, `prev_hash`, `sealed_at`
and the canonical rule. The verdict is written next to the hash only after the run completes; the rule
itself can never be edited. `GET /api/meta` reports whether the chain verifies, and
`GET /api/ledger/{id}/canonical` returns the exact bytes that were hashed so anyone can recompute it.

The multiple-testing attack reads this ledger per anonymous *searcher* (a random id kept in the
browser), so one person's twentieth variation on an idea is penalised while a stranger's first try is not.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness, provider name |
| GET | `/api/meta` | provider, datasets, grammar, scoreboard, chain status |
| GET | `/api/assets?dataset_id=` | assets in a dataset |
| POST | `/api/upload` | CSV upload → `upload:<id>` dataset |
| POST | `/api/claims` | `{belief, dataset_id?, asset_hint?, searcher?}` → claim id |
| GET | `/api/claims/{id}/events` | Server-Sent Events stream of every stage (replays on reconnect) |
| GET | `/api/claims/{id}` | full result JSON |
| GET | `/api/ledger` | all sealed claims |
| GET | `/api/ledger/{id}/canonical` | canonical bytes behind a hash |

## Layout

```
falsify/
  engine/      data.py (datasets, uploads) · indicators.py (catalogue) · dsl.py (safe compiler)
               spec.py (ClaimSpec + seal) · backtest.py · attacks.py · verdict.py
  llm/         client.py (routes) · prompts.py · agent.py (formalize / advocate / report loops) · stub.py
  ledger.py    hash chain, results, seed export
  pipeline.py  one claim end to end, emitting SSE events
  api.py       FastAPI app · cli.py
web/           single-page UI (vanilla JS, hand-drawn SVG charts)
scripts/       fetch_public_candles.py · seed_ledger.py
tests/         pytest (engine, attacks, chain, API)
```

## Limitations and non-goals

* **Past data only.** A verdict is a statement about history on the data provided. It is not a
  prediction and not investment advice.
* One rule, one asset, fixed holding period per claim. Portfolio effects, position sizing, slippage
  models beyond flat fees and funding costs are out of scope.
* Bootstrap p-values assume trades are roughly independent; overlapping holding periods make the
  baseline optimistic, which is one reason a pass is called *SURVIVED (for now)*.
* The built-in datasets are spot candles from one exchange; survivorship bias in asset selection is real
  (these ten coins are the ones that are still here).
* A language model wrote the rule and the report. Falsify shows its full trace and every assumption
  precisely because that step can be wrong.

## License

MIT — see `LICENSE`.
