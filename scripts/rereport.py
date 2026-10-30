"""Re-write only the model report of seed results whose report fell back to the numbers-only summary
(e.g. a timeout). Verdicts, numbers and ledger hashes are untouched; the report is not part of the seal.

    FALSIFY_LLM_PROVIDER=custom LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=muse-glimmer:30b \
        FALSIFY_LLM_TIMEOUT=900 python scripts/rereport.py [--all]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from falsify.engine.spec import ClaimSpec  # noqa: E402
from falsify.ledger import SEED_DIR  # noqa: E402
from falsify.llm import agent  # noqa: E402
from falsify.llm.client import LLM  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="rewrite every report, not only the failed ones")
    a = ap.parse_args()
    llm = LLM()
    print("provider:", llm.label)
    if llm.provider == "offline":
        sys.exit("refusing to rewrite seed reports with the offline stub; configure a real model")
    d = os.path.join(SEED_DIR, "results")
    for fn in sorted(os.listdir(d)):
        path = os.path.join(d, fn)
        r = json.load(open(path, encoding="utf-8"))
        if not a.all and "Model report unavailable" not in (r.get("report") or ""):
            continue
        spec = ClaimSpec(**r["spec"])
        t0 = time.time()
        rep = agent.write_report(llm, spec, r["backtest"], r["findings"], r["verdict"], r["variants"])
        ok = "Model report unavailable" not in rep
        print("%s %-9s %5.0fs %s" % (fn, r["verdict"]["label"], time.time() - t0, "ok" if ok else "still failed"))
        if not ok:
            continue
        r["report"] = rep
        r["report_rewritten_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for st in r.get("stages", []):
            if st.get("stage") == "report" and "report" in st:
                st["report"] = rep
        json.dump(r, open(path, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
