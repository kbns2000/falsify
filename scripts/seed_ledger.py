"""Run the example beliefs through the pipeline with the configured model and export the
ledger as the committed seed (ledger/seed). Run this where a real Meta model is reachable:

    FALSIFY_LLM_PROVIDER=custom LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=muse-glimmer:30b \
        python scripts/seed_ledger.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FALSIFY_LEDGER_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ledger", "_seed_build"))

from falsify import ledger, pipeline  # noqa: E402
from falsify.llm.client import LLM  # noqa: E402

BELIEFS = [
    "When bitcoin's RSI goes under 30 it always bounces within a week",
    "3일 연속 떨어지면 다음날 반등하니까 그때 산다",
    "Breakouts above the 20-day high on heavy volume keep running for two weeks on SOL",
    "Buying ETH every Monday open and selling at the close beats random days",
    "A golden cross (SMA20 over SMA50) on DOGE means a month of gains",
    "After a 10% drop in one day, XRP recovers over the next 3 days",
    "Bitcoin closes above its 200-day average: buying there and holding 30 days beats random",
    "If BTC falls more than 5% in a day, shorting the next day for 2 days works",
]


def main():
    llm = LLM()
    print("provider:", llm.label)
    for b in BELIEFS:
        r = pipeline.run_claim(b, llm=llm, searcher="seed")
        if "error" in r:
            print("ERR ", b, r["error"])
        else:
            print("%-12s %s | %s | n=%s mean=%s" % (r["verdict"]["label"], b[:50], r["spec"]["entry"], r["backtest"]["n_trades"], None if r["backtest"]["mean_net"] is None else round(r["backtest"]["mean_net"], 2)))
    n = ledger.export_seed()
    print("exported %d results to ledger/seed" % n, ledger.verify_chain())


if __name__ == "__main__":
    main()
