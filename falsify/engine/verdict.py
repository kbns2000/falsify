"""Turn the backtest and the attack battery into a verdict. Deterministic by design."""
from __future__ import annotations

import math

from . import backtest as B
from .spec import ClaimSpec

LABELS = {
    "FALSIFIED": "The belief did not beat its pre-registered benchmark on past data.",
    "FRAGILE": "It passed the main test but at least one attack broke it.",
    "SURVIVED": "It survived every attack we ran. That is not a prediction.",
    "INCONCLUSIVE": "Too few trades to say anything either way.",
}


def _get(findings: list[dict], fid: str) -> dict | None:
    return next((f for f in findings if f["id"] == fid), None)


def decide(spec: ClaimSpec, bt: B.BacktestResult, findings: list[dict]) -> dict:
    crit = spec.criterion
    reasons: list[str] = []
    rb = _get(findings, "random_baseline") or {"details": {}, "status": "warn"}
    mt = _get(findings, "multiple_testing") or {"details": {}, "status": "info"}
    p = rb["details"].get("p_value")
    alpha_adj = mt["details"].get("alpha_adjusted", crit.alpha)

    if bt.n_trades < crit.min_trades:
        reasons.append("Only %d completed trades (minimum %d)." % (bt.n_trades, crit.min_trades))
        return _pack("INCONCLUSIVE", spec, bt, findings, reasons, primary=None, p=p)

    metric = {"mean_net_return": bt.mean_net, "win_rate": (bt.win_rate or 0) * 100.0, "total_net_return": bt.total_net}[crit.metric]
    primary_pass = False
    if crit.beats == "random_entries":
        excess = rb["details"].get("excess")
        primary_pass = p is not None and p < alpha_adj and (excess or 0) > 0 and (bt.mean_net or 0) > 0
        if p is None:
            reasons.append("Random-entry comparison unavailable.")
        elif not primary_pass:
            if (excess or 0) <= 0:
                reasons.append("Random entries with the same holding period did as well or better (rule %.2f%% vs random %.2f%% per trade)." % (bt.mean_net, rb["details"].get("random_mean", float("nan"))))
            else:
                reasons.append("Edge over random entries is not significant (p = %.3f, needed < %.3f%s)." % (p, alpha_adj, "" if alpha_adj == crit.alpha else " after correcting for %d sealed claims" % mt["details"].get("claims_so_far", 1)))
    elif crit.beats == "zero":
        from math import erf, sqrt
        t = bt.t_stat or 0.0
        p_t = 0.5 * (1 - erf(t / sqrt(2)))  # one-sided normal approx
        primary_pass = metric is not None and metric > 0 and p_t < alpha_adj
        if not primary_pass:
            reasons.append("Mean trade %.2f%% is not significantly above zero (t = %.2f, p ≈ %.3f)." % (bt.mean_net, t, p_t))
        p = p_t
    else:  # buy_and_hold
        primary_pass = (bt.total_net or 0) > (bt.buy_and_hold_total or 0)
        if not primary_pass:
            reasons.append("Rule total %.1f%% did not beat holding (%.1f%%)." % (bt.total_net, bt.buy_and_hold_total))

    if not primary_pass:
        return _pack("FALSIFIED", spec, bt, findings, reasons, primary=False, p=p)

    kills = []
    for fid, text in (("fees", "a realistic 0.25% fee per side wipes out the mean trade"),
                      ("time_split", "the sign flips between the first and second half of the data"),
                      ("neighbourhood", "only the exact parameters work; neighbours do not"),
                      ("multiple_testing", "it stops being significant once the number of sealed claims is accounted for")):
        f = _get(findings, fid)
        if f and f["status"] == "fail":
            kills.append(text)
    ca = _get(findings, "cross_asset")
    warns = []
    if ca and ca["status"] == "fail":
        warns.append("it does not transfer to other assets")
    for fid in ("neighbourhood", "time_split"):
        f = _get(findings, fid)
        if f and f["status"] == "warn":
            warns.append(f["headline"])
    if kills:
        reasons.append("Passed the main test, but: " + "; ".join(kills) + ".")
        return _pack("FRAGILE", spec, bt, findings, reasons, primary=True, p=p, warnings=warns)
    reasons.append("Beat %d random-entry sets at p = %.3f, survived fee, time-split and parameter attacks." % (rb["details"].get("n_boot", 0), p if p is not None else float("nan")))
    return _pack("SURVIVED", spec, bt, findings, reasons, primary=True, p=p, warnings=warns)


def _pack(label, spec, bt, findings, reasons, primary, p, warnings=None):
    fails = [f["id"] for f in findings if f["status"] == "fail"]
    passes = [f["id"] for f in findings if f["status"] == "pass"]
    return {
        "label": label,
        "subtitle": LABELS[label],
        "primary_passed": primary,
        "p_value": None if p is None or (isinstance(p, float) and not math.isfinite(p)) else float(p),
        "reasons": reasons,
        "warnings": warnings or [],
        "attacks_failed": fails,
        "attacks_passed": passes,
        "n_attacks": len(findings),
        "disclaimer": "Falsify tests beliefs about the past. It is not investment advice and makes no prediction.",
    }
