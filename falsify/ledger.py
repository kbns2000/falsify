"""Append-only, hash-chained ledger of sealed claims and their verdicts."""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_DIR = os.environ.get("FALSIFY_LEDGER_DIR", os.path.join(ROOT, "ledger"))
LEDGER_FILE = os.path.join(LEDGER_DIR, "claims.jsonl")
RESULTS_DIR = os.path.join(LEDGER_DIR, "results")
_lock = threading.Lock()


def _ensure():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    if not os.path.exists(LEDGER_FILE):
        open(LEDGER_FILE, "a", encoding="utf-8").close()


def entries() -> list[dict]:
    _ensure()
    out = []
    with open(LEDGER_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def last_hash() -> str:
    es = entries()
    return es[-1]["spec_hash"] if es else ""


def count_for_asset(asset: str) -> int:
    return sum(1 for e in entries() if e.get("asset") == asset and e.get("kind", "claim") == "claim")


def new_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8]


def append(entry: dict) -> None:
    _ensure()
    with _lock:
        with open(LEDGER_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def update(claim_id: str, **fields) -> None:
    """Rewrite the ledger with the verdict filled in (the sealed fields never change)."""
    _ensure()
    with _lock:
        es = entries()
        for e in es:
            if e["id"] == claim_id:
                for k, v in fields.items():
                    if k in ("spec_hash", "prev_hash", "sealed_at", "canonical", "id"):
                        continue
                    e[k] = v
        with open(LEDGER_FILE, "w", encoding="utf-8") as f:
            for e in es:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")


def save_result(claim_id: str, result: dict) -> None:
    _ensure()
    with open(os.path.join(RESULTS_DIR, claim_id + ".json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)


def load_result(claim_id: str) -> dict | None:
    p = os.path.join(RESULTS_DIR, claim_id + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def verify_chain() -> dict:
    """Recompute the chain to prove nobody edited a sealed claim after the fact."""
    import hashlib
    prev = ""
    ok = True
    broken = None
    es = entries()
    for e in es:
        body = e.get("canonical", "")
        h = hashlib.sha256((prev + "\n" + e["sealed_at"] + "\n" + body).encode("utf-8")).hexdigest()
        if h != e["spec_hash"] or e.get("prev_hash", "") != prev:
            ok = False
            broken = e["id"]
            break
        prev = e["spec_hash"]
    return {"ok": ok, "entries": len(es), "broken_at": broken, "head": prev}


def scoreboard() -> dict:
    es = [e for e in entries() if e.get("kind", "claim") == "claim"]
    labels = {}
    for e in es:
        lab = (e.get("verdict") or {}).get("label") or "PENDING"
        labels[lab] = labels.get(lab, 0) + 1
    return {"total": len(es), "by_label": labels}
