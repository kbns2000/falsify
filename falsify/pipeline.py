"""Orchestrates one claim from belief text to verdict, emitting progress events."""
from __future__ import annotations

import time
import traceback
from typing import Callable

from . import ledger
from .engine import attacks, backtest, data, verdict as V
from .engine.spec import ClaimSpec, seal
from .llm import agent
from .llm.client import LLM, LLMError

Emit = Callable[[str, dict], None]


def _load_for(spec: ClaimSpec):
    return data.load(spec.asset, spec.dataset_id, spec.start, spec.end)


def run_claim(belief: str, *, dataset_id: str = "builtin:top10_daily", asset_hint: str | None = None, emit: Emit | None = None, llm: LLM | None = None, claim_id: str | None = None) -> dict:
    emit = emit or (lambda *_: None)
    t0 = time.time()
    llm = llm or LLM()
    claim_id = claim_id or ledger.new_id()
    info = data.builtin_info() if dataset_id.startswith("builtin") else data.upload_info(dataset_id)
    assets = info.assets
    resolve = lambda name: data.resolve_asset(name, dataset_id)
    result: dict = {"id": claim_id, "belief": belief, "dataset": info.__dict__, "provider": llm.usage_dict(), "stages": []}

    def stage(name: str, status: str, **payload):
        rec = {"stage": name, "status": status, "t": round(time.time() - t0, 2), **payload}
        result["stages"].append(rec)
        emit(name + "." + status, rec)

    # 1 ---- understand
    stage("understand", "start", message="Turning the belief into one exact rule")
    belief_full = belief if not asset_hint else "%s\n\n(The person says this is about %s.)" % (belief, asset_hint)
    try:
        spec, trace = agent.formalize(llm, belief_full, dataset_id=dataset_id, assets=assets, dataset_desc="%s, %s to %s" % (info.label, info.start, info.end), timeframe=info.timeframe, resolve_asset=resolve, load_df=_load_for, emit=emit)
    except agent.UntestableBelief as e:
        result["error"] = {"kind": "untestable", "message": str(e)}
        stage("understand", "error", message=str(e))
        result["provider"] = llm.usage_dict()
        ledger.save_result(claim_id, result)
        return result
    except (LLMError, Exception) as e:
        result["error"] = {"kind": "formalize_failed", "message": str(e)[:500]}
        stage("understand", "error", message=str(e)[:300])
        result["provider"] = llm.usage_dict()
        ledger.save_result(claim_id, result)
        return result
    spec.belief_text = belief
    result["spec"] = spec.model_dump(mode="json")
    result["trace"] = trace
    stage("understand", "done", spec=result["spec"], trace=trace)

    # 2 ---- seal
    stage("seal", "start", message="Sealing the rule before looking at any result")
    sealed = seal(spec, ledger.last_hash())
    prior = ledger.count_for_asset(spec.asset)
    ledger.append({"id": claim_id, "kind": "claim", "title": spec.title, "asset": spec.asset, "entry": spec.entry, "bars": spec.exit.bars, "direction": spec.direction, "belief": belief, "language": spec.language, "spec_hash": sealed["spec_hash"], "prev_hash": sealed["prev_hash"], "sealed_at": sealed["sealed_at"], "canonical": sealed["canonical"], "verdict": None, "provider": llm.label})
    result["seal"] = {k: sealed[k] for k in ("spec_hash", "prev_hash", "sealed_at")}
    result["seal"]["prior_claims_same_asset"] = prior
    stage("seal", "done", **result["seal"])

    # 3 ---- test
    stage("test", "start", message="Replaying the rule on %s" % spec.asset)
    df = _load_for(spec)
    bt = backtest.run(spec, df)
    btd = bt.to_dict()
    result["backtest"] = btd
    stage("test", "done", summary={k: btd[k] for k in ("n_signals", "n_trades", "mean_net", "win_rate", "total_net", "buy_and_hold_total", "max_drawdown")}, equity_curve=btd["equity_curve"])

    # 4 ---- attack
    stage("attack", "start", message="Running the attack battery")
    load_fn = lambda a: data.load(a, dataset_id, spec.start, spec.end)
    findings: list[dict] = []
    steps = [
        lambda: attacks.sample_size(bt, spec),
        lambda: attacks.lookahead_guard(spec, bt),
        lambda: attacks.random_baseline(df, spec, bt),
        None,  # multiple testing needs p from random baseline
        lambda: attacks.fee_sensitivity(bt, spec),
        lambda: attacks.time_split(bt, df),
        lambda: attacks.parameter_neighbourhood(df, spec, bt),
        lambda: attacks.cross_asset(spec, bt, load_fn, assets),
        lambda: attacks.opportunity_cost(bt),
    ]
    for fn in steps:
        if fn is None:
            rb = next((f for f in findings if f["id"] == "random_baseline"), None)
            f = attacks.multiple_testing(spec, prior, (rb or {}).get("details", {}).get("p_value"))
        else:
            try:
                f = fn()
            except Exception as e:  # never let one attack kill the run
                f = attacks.finding("attack_error", "Attack failed", "warn", "An attack crashed: %s" % str(e)[:160])
        findings.append(f)
        emit("attack.finding", {"finding": f})
    result["findings"] = findings
    stage("attack", "done", n=len(findings), failed=[f["id"] for f in findings if f["status"] == "fail"])

    # 5 ---- devil's advocate
    stage("advocate", "start", message="Asking the model how this result could still be an illusion")
    variants = []
    try:
        proposals = agent.propose_variants(llm, spec, assets, resolve)
    except Exception as e:
        proposals = [{"label": "advocate unavailable", "why": str(e)[:200], "spec": None}]
    for p in proposals:
        row = {"label": p["label"], "why": p["why"]}
        if p.get("spec") is None:
            row["result"] = None
        else:
            try:
                row["spec"] = p["spec"].model_dump(mode="json")
                row["result"] = attacks.run_variant(p["spec"], p["label"], lambda a, ds=dataset_id, s=None, e=None: data.load(a, ds, s, e))
            except Exception as e:
                row["result"] = {"error": str(e)[:200]}
        variants.append(row)
        emit("advocate.variant", {"variant": row})
    result["variants"] = variants
    stage("advocate", "done", n=len(variants))

    # 6 ---- verdict + report
    stage("verdict", "start", message="Deciding")
    ver = V.decide(spec, bt, findings)
    result["verdict"] = ver
    stage("verdict", "done", verdict=ver)
    stage("report", "start", message="Writing the report")
    report = agent.write_report(llm, spec, btd, findings, ver, variants)
    result["report"] = report
    result["provider"] = llm.usage_dict()
    stage("report", "done", report=report, provider=result["provider"])

    # 7 ---- ledger
    ledger.update(claim_id, verdict={"label": ver["label"], "p_value": ver["p_value"], "n_trades": bt.n_trades, "mean_net": btd["mean_net"], "attacks_failed": ver["attacks_failed"]}, finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    result["elapsed"] = round(time.time() - t0, 2)
    ledger.save_result(claim_id, result)
    stage("done", "done", elapsed=result["elapsed"], label=ver["label"])
    return result
