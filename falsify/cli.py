"""Command line: run one belief through the whole pipeline and print the verdict.

    python -m falsify.cli "When bitcoin's RSI goes under 30 it bounces within a week" [--asset BTC] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import pipeline
from .llm.client import LLM


def main(argv=None):
    ap = argparse.ArgumentParser(prog="falsify")
    ap.add_argument("belief", help="the belief to test, any language")
    ap.add_argument("--asset", default=None, help="asset hint, e.g. BTC or SOL/USDT")
    ap.add_argument("--dataset", default=None, help="dataset id (default: 10 coins daily since 2017)")
    ap.add_argument("--json", default=None, help="write the full result JSON here")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    llm = LLM()
    print("provider: %s" % llm.label, file=sys.stderr)
    t0 = time.time()

    def emit(kind, payload):
        if a.quiet:
            return
        if kind.endswith(".start"):
            print("  » %s" % payload.get("message", kind), file=sys.stderr)
        elif kind == "attack.finding":
            f = payload["finding"]
            print("    [%-4s] %s: %s" % (f["status"], f["name"], f["headline"]), file=sys.stderr)
        elif kind == "advocate.variant":
            v = payload["variant"]
            r = v.get("result") or {}
            print("    ~ %s → mean %s%% (n=%s)" % (v["label"], None if r.get("mean_net") is None else round(r["mean_net"], 2), r.get("n_trades")), file=sys.stderr)
        elif kind.startswith("formalize."):
            print("    agent: %s #%s %s" % (kind.split(".")[1], payload.get("n", ""), payload.get("reason", "")), file=sys.stderr)

    res = pipeline.run_claim(a.belief, dataset_id=a.dataset or pipeline.data.DEFAULT_DATASET, asset_hint=a.asset, emit=emit, llm=llm)
    if "error" in res:
        print("ERROR: %s" % res["error"], file=sys.stderr)
        return 2
    sp = res["spec"]
    print("\nRULE   %s | %s | %s | hold %d bars" % (sp["asset"], sp["direction"], sp["entry"], sp["exit"]["bars"]))
    print("SEAL   %s @ %s" % (res["seal"]["spec_hash"][:16], res["seal"]["sealed_at"]))
    bt = res["backtest"]
    print("TEST   trades=%s mean=%.2f%% win=%.0f%% total=%.1f%% (hold %.1f%%)" % (bt["n_trades"], bt["mean_net"] or 0, 100 * (bt["win_rate"] or 0), bt["total_net"] or 0, bt["buy_and_hold_total"] or 0))
    print("VERDICT %s — %s" % (res["verdict"]["label"], "; ".join(res["verdict"]["reasons"])))
    print("\n" + res["report"])
    print("\nmodel usage: %s | elapsed %.1fs" % (json.dumps(res["provider"]), time.time() - t0), file=sys.stderr)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
