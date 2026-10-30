"""The agent loop: formalize -> validate -> repair, then devil's-advocate variants, then the report.

The loop may repair its own mistakes (syntax, unknown asset, impossible units) but is
forbidden from tuning the rule toward a better result. Every attempt is recorded so the
UI can show the trace.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from pydantic import ValidationError

from ..engine import backtest as B
from ..engine.dsl import DSLError
from ..engine.spec import ClaimSpec
from . import prompts, stub
from .client import LLM, LLMError, extract_json

MAX_ATTEMPTS = 4


def detect_language(text: str) -> str:
    return "ko" if re.search(r"[가-힣]", text) else "en"


def _spec_from_json(obj: dict, belief: str, dataset_id: str, assets: list[str], resolve_asset: Callable[[str], str], timeframe: str) -> ClaimSpec:
    if obj.get("untestable"):
        raise UntestableBelief(str(obj.get("reason") or "The belief cannot be tested with price and volume data."))
    data = dict(obj)
    data.pop("assumptions", None)
    data.pop("untestable", None)
    data.pop("reason", None)
    data["belief_text"] = belief
    data["dataset_id"] = dataset_id
    data["timeframe"] = timeframe
    if not data.get("asset"):
        raise ValueError("asset missing")
    data["asset"] = resolve_asset(str(data["asset"]))
    if not data.get("language"):
        data["language"] = detect_language(belief)
    if not data.get("title"):
        data["title"] = belief[:60]
    return ClaimSpec(**data)


class UntestableBelief(Exception):
    pass


def formalize(llm: LLM, belief: str, *, dataset_id: str, assets: list[str], dataset_desc: str, timeframe: str, resolve_asset: Callable[[str], str], load_df: Callable[[ClaimSpec], object], emit: Callable[[str, dict], None] | None = None) -> tuple[ClaimSpec, dict]:
    """Returns (spec, trace). trace = {"attempts": [...], "assumptions": [...], "provider": ...}."""
    emit = emit or (lambda *_: None)
    trace: dict = {"attempts": [], "assumptions": [], "provider": llm.label}
    if llm.provider == "offline":
        obj = stub.formalize(belief, assets)
        spec = _spec_from_json(obj, belief, dataset_id, assets, resolve_asset, timeframe)
        trace["attempts"].append({"n": 1, "ok": True, "entry": spec.entry, "note": "offline stub"})
        trace["assumptions"] = obj.get("assumptions", [])
        return spec, trace

    user = ("Dataset: %s\nAssets available: %s\nBar size: %s\n\nBelief:\n%s" % (dataset_desc, ", ".join(assets), timeframe, belief.strip()))
    messages = [{"role": "system", "content": prompts.FORMALIZER_SYSTEM}]
    for ex_in, ex_out in prompts.FORMALIZER_EXAMPLES:
        messages.append({"role": "user", "content": "Dataset: example\nAssets available: %s\nBar size: 1d\n\nBelief:\n%s" % (", ".join(assets), ex_in)})
        messages.append({"role": "assistant", "content": json.dumps(ex_out, ensure_ascii=False)})
    messages.append({"role": "user", "content": user})

    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        emit("formalize.attempt", {"n": attempt})
        reply = llm.chat(messages, json_mode=True, max_tokens=900)
        text = reply["content"]
        rec = {"n": attempt, "ok": False, "raw": text[:1500]}
        try:
            obj = extract_json(text)
            spec = _spec_from_json(obj, belief, dataset_id, assets, resolve_asset, timeframe)
            # preview: does the rule fire at all?
            df = load_df(spec)
            bt = B.run(spec, df)
            rec.update(ok=True, entry=spec.entry, n_signals=bt.n_signals, n_trades=bt.n_trades)
            trace["attempts"].append(rec)
            trace["assumptions"] = [str(a) for a in (obj.get("assumptions") or [])]
            if bt.n_signals == 0 and attempt < MAX_ATTEMPTS and not rec.get("zero_retry"):
                # one repair round for a condition that can never fire (usually a units mistake)
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": "That condition never fired on %s (%d bars). Check units and semantics (percent indicators are in percent, rsi is 0-100, dow is 0-6). If the belief genuinely implies a rule this strict, return the same JSON again; otherwise fix the mistake WITHOUT making the rule looser than the belief." % (spec.asset, len(df))})
                emit("formalize.repair", {"n": attempt, "reason": "zero signals"})
                reply2 = llm.chat(messages, json_mode=True, max_tokens=900)
                try:
                    obj2 = extract_json(reply2["content"])
                    spec2 = _spec_from_json(obj2, belief, dataset_id, assets, resolve_asset, timeframe)
                    bt2 = B.run(spec2, load_df(spec2))
                    trace["attempts"].append({"n": attempt + 1, "ok": True, "entry": spec2.entry, "n_signals": bt2.n_signals, "n_trades": bt2.n_trades, "note": "after zero-signal check", "raw": reply2["content"][:1500]})
                    trace["assumptions"] = [str(a) for a in (obj2.get("assumptions") or trace["assumptions"])]
                    return spec2, trace
                except Exception as e2:  # keep the first valid spec
                    trace["attempts"].append({"n": attempt + 1, "ok": False, "error": str(e2)[:300]})
            hint = _weekday_hint(belief, spec.entry)
            if hint and attempt < MAX_ATTEMPTS:
                # one-shot timing check: "buy on Monday" must fire at Sunday's close
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": hint})
                emit("formalize.repair", {"n": attempt, "reason": "weekday timing check"})
                try:
                    reply2 = llm.chat(messages, json_mode=True, max_tokens=900)
                    obj2 = extract_json(reply2["content"])
                    spec2 = _spec_from_json(obj2, belief, dataset_id, assets, resolve_asset, timeframe)
                    bt2 = B.run(spec2, load_df(spec2))
                    if bt2.n_signals > 0:
                        trace["attempts"].append({"n": attempt + 1, "ok": True, "entry": spec2.entry, "n_signals": bt2.n_signals, "n_trades": bt2.n_trades, "note": "after weekday timing check", "raw": reply2["content"][:1500]})
                        trace["assumptions"] = [str(a) for a in (obj2.get("assumptions") or trace["assumptions"])]
                        return spec2, trace
                except Exception as e2:
                    trace["attempts"].append({"n": attempt + 1, "ok": False, "error": str(e2)[:300]})
            return spec, trace
        except UntestableBelief:
            trace["attempts"].append(rec)
            raise
        except (ValueError, ValidationError, DSLError) as e:
            last_err = _friendly_error(e)
            rec["error"] = last_err
            trace["attempts"].append(rec)
            emit("formalize.repair", {"n": attempt, "reason": last_err[:200]})
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": "Your JSON was rejected: %s\nReturn the corrected JSON object only." % last_err})
    raise LLMError("Could not turn the belief into a valid rule after %d attempts: %s" % (MAX_ATTEMPTS, last_err))


_DAYS = [("monday", "월요일"), ("tuesday", "화요일"), ("wednesday", "수요일"), ("thursday", "목요일"), ("friday", "금요일"), ("saturday", "토요일"), ("sunday", "일요일")]


def _weekday_hint(belief: str, entry: str) -> str | None:
    """If the rule fires on the very weekday the belief names, the trade actually opens the day AFTER.
    Ask the model once to confirm which day the person means to enter."""
    m = re.search(r"dow\(\)\s*==\s*(\d)", entry)
    if not m:
        return None
    k = int(m.group(1))
    if k > 6:
        return None
    b = belief.lower()
    en, ko = _DAYS[k]
    if en not in b and en[:3] + " " not in b and ko not in b:
        return None
    prev = (k - 1) % 7
    return ("Timing check: dow() == %d fires at the CLOSE of %s, so the position opens at the next bar's open, i.e. on %s. "
            "If the person means to ENTER on %s (e.g. 'buy on %s'), the condition must be dow() == %d. "
            "If they really mean a signal at %s's close, keep it. Return the final JSON object only."
            % (k, en.title(), _DAYS[(k + 1) % 7][0].title(), en.title(), en.title(), prev, en.title()))


def _friendly_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        parts = []
        for err in e.errors()[:4]:
            parts.append("%s: %s" % (".".join(str(x) for x in err.get("loc", [])), err.get("msg")))
        return "; ".join(parts)
    return str(e)[:400]


def propose_variants(llm: LLM, spec: ClaimSpec, assets: list[str], resolve_asset: Callable[[str], str], max_variants: int = 3) -> list[dict]:
    """Devil's advocate: extra counter-tests as full specs. Returns [{"label","why","spec": ClaimSpec}]."""
    base = spec.model_dump(mode="json")
    if llm.provider == "offline":
        raw = stub.variants(base, assets)
    else:
        msg = [
            {"role": "system", "content": prompts.ADVOCATE_SYSTEM},
            {"role": "user", "content": "Assets available: %s\n\nPre-registered rule (JSON):\n%s\n\nOriginal belief:\n%s" % (", ".join(assets), json.dumps(base, ensure_ascii=False), spec.belief_text)},
        ]
        try:
            reply = llm.chat(msg, json_mode=True, max_tokens=2500)
            raw = extract_json(reply["content"])
        except (LLMError, ValueError) as e:
            return [{"label": "advocate unavailable", "why": str(e)[:200], "spec": None}]
    out = []
    for v in (raw.get("variants") or [])[:max_variants]:
        try:
            merged = dict(base)
            patch = v.get("spec") or {}
            for k, val in patch.items():
                if k in ("exit", "criterion") and isinstance(val, dict):
                    merged[k] = dict(base.get(k, {}), **val)
                elif k not in ("belief_text", "dataset_id", "timeframe", "assumptions"):
                    merged[k] = val
            merged["asset"] = resolve_asset(str(merged.get("asset", spec.asset)))
            merged["title"] = str(v.get("label") or merged.get("title") or "variant")[:120]
            vs = ClaimSpec(**merged)
            if vs.entry == spec.entry and vs.asset == spec.asset and vs.direction == spec.direction and vs.exit == spec.exit:
                continue  # identical to the original, nothing to learn
            out.append({"label": str(v.get("label", "variant"))[:80], "why": str(v.get("why", ""))[:300], "spec": vs})
        except Exception as e:
            out.append({"label": str(v.get("label", "variant"))[:80], "why": "rejected: %s" % _friendly_error(e)[:200], "spec": None})
    return out


def write_report(llm: LLM, spec: ClaimSpec, bt: dict, findings: list[dict], verdict: dict, variants: list[dict]) -> str:
    sd = spec.model_dump(mode="json")
    if llm.provider == "offline":
        return stub.report(sd, bt, findings, verdict, variants)
    compact_bt = {k: bt.get(k) for k in ("asset", "window_start", "window_end", "n_signals", "n_trades", "mean_net", "median_net", "win_rate", "total_net", "max_drawdown", "buy_and_hold_total", "exposure", "avg_bars_held", "t_stat")}
    compact_f = [{"name": f["name"], "status": f["status"], "headline": f["headline"]} for f in findings]
    compact_v = [{"label": v.get("label"), "why": v.get("why"), "result": v.get("result")} for v in variants]
    payload = {"language": spec.language, "rule": {"title": spec.title, "plain_language": spec.plain_language, "entry": spec.entry, "exit": sd["exit"], "direction": spec.direction, "asset": spec.asset, "criterion": sd["criterion"]}, "backtest": compact_bt, "findings": compact_f, "verdict": verdict, "agent_counter_tests": compact_v, "original_belief": spec.belief_text}
    msg = [{"role": "system", "content": prompts.REPORTER_SYSTEM}, {"role": "user", "content": "Language: %s\n\n%s" % (spec.language, json.dumps(payload, ensure_ascii=False, default=str))}]
    try:
        reply = llm.chat(msg, max_tokens=1800, temperature=0.3)
        text = re.sub(r"<think>.*?</think>", "", reply["content"], flags=re.S).strip()
        if not text:
            raise LLMError("empty report")
        return text
    except LLMError as e:
        return stub.report(sd, bt, findings, verdict, variants, model_failed=True) + "\n\n(Model report unavailable: %s)" % str(e)[:160]
