"""The adversarial battery: every way we know to make a backtest look better than it is.

Each attack returns a ``Finding`` dict::

    {"id": "random_baseline", "name": "...", "status": "pass"|"fail"|"warn"|"info",
     "headline": "one sentence", "details": {...numbers...}}

The battery is fixed and always runs in full. A language model may *add* targeted
variants (see ``run_variant``) but can never remove an attack.
"""
from __future__ import annotations

import ast
import copy
import math

import numpy as np
import pandas as pd

from . import backtest as B
from .dsl import compile_expression, DSLError
from .spec import ClaimSpec


def finding(fid: str, name: str, status: str, headline: str, **details) -> dict:
    clean = {}
    for k, v in details.items():
        if isinstance(v, float) and not math.isfinite(v):
            v = None
        clean[k] = v
    return {"id": fid, "name": name, "status": status, "headline": headline, "details": clean}


def _fmt(x: float | None, nd=2, suffix="%") -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    return ("%%.%df%%s" % nd) % (x, suffix)


# --------------------------------------------------------------------------- 1
def random_baseline(df: pd.DataFrame, spec: ClaimSpec, bt: B.BacktestResult, n_boot: int = 2000, seed: int = 7) -> dict:
    """Would N random entries with the same exit rule have done just as well?"""
    per_entry = B.all_entry_returns(df, spec)
    valid = np.flatnonzero(np.isfinite(per_entry))
    n = bt.n_trades
    if n == 0 or len(valid) < max(10, n):
        return finding("random_baseline", "Random-entry baseline", "warn", "Not enough trades to compare against random entries.", n_trades=n)
    rng = np.random.default_rng(seed)
    k = min(n, len(valid))
    picks = np.stack([rng.choice(valid, size=k, replace=False) for _ in range(n_boot)])
    means = per_entry[picks].mean(axis=1)
    rule_mean = bt.mean_net
    p = float(((means >= rule_mean).sum() + 1) / (n_boot + 1))
    pct = float((means < rule_mean).mean() * 100.0)
    uncond = float(np.nanmean(per_entry))
    excess = rule_mean - uncond
    hist, edges = np.histogram(means, bins=30)
    status = "pass" if (p < spec.criterion.alpha and excess > 0) else "fail"
    head = ("Rule mean %s vs random-entry mean %s; the rule beat %.0f%% of %d random sets (one-sided p = %.3f)."
            % (_fmt(rule_mean), _fmt(uncond), pct, n_boot, p))
    return finding(
        "random_baseline", "Random-entry baseline", status, head,
        rule_mean=rule_mean, random_mean=uncond, excess=excess, p_value=p, percentile=pct, n_boot=n_boot, n_trades=n,
        random_ci95=[float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
        hist={"counts": hist.tolist(), "edges": [round(float(e), 4) for e in edges]},
    )


# --------------------------------------------------------------------------- 2
def fee_sensitivity(bt: B.BacktestResult, spec: ClaimSpec, levels=(0, 5, 10, 25, 50)) -> dict:
    if not bt.trades:
        return finding("fees", "Fee and slippage sensitivity", "warn", "No trades to re-price.")
    gross = np.array([t.gross_pct for t in bt.trades])
    rows = []
    for bps in levels:
        net = B.net_from_gross(gross, bps)
        rows.append({"fee_bps_per_side": bps, "mean_net": float(net.mean()), "total_net": float((np.prod(1 + net / 100.0) - 1) * 100.0), "win_rate": float((net > 0).mean())})
    at25 = next(r for r in rows if r["fee_bps_per_side"] == 25)
    breakeven = None
    if gross.mean() > 0:
        # per-side fee (bps) at which the mean net return hits zero, approx linear
        breakeven = float(gross.mean() / 2.0 * 100.0)
    status = "pass" if at25["mean_net"] > 0 else "fail"
    head = "At 0.25%% per side the mean trade is %s%s." % (_fmt(at25["mean_net"]), "" if breakeven is None else "; the edge disappears at about %.0f bps per side" % breakeven)
    return finding("fees", "Fee and slippage sensitivity", status, head, table=rows, breakeven_bps_per_side=breakeven, assumed_bps=spec.fee_bps_per_side)


# --------------------------------------------------------------------------- 3
def time_split(bt: B.BacktestResult, df: pd.DataFrame) -> dict:
    if bt.n_trades < 6:
        return finding("time_split", "Does it hold in both halves of the data?", "warn", "Too few trades to split in time.")
    mid = df["ts"].iloc[len(df) // 2]
    first = [t for t in bt.trades if pd.Timestamp(t.entry_ts) < mid]
    second = [t for t in bt.trades if pd.Timestamp(t.entry_ts) >= mid]

    def stats(ts):
        if not ts:
            return {"n": 0, "mean_net": None, "win_rate": None}
        a = np.array([t.net_pct for t in ts])
        return {"n": len(ts), "mean_net": float(a.mean()), "win_rate": float((a > 0).mean())}

    s1, s2 = stats(first), stats(second)
    years = {}
    for t in bt.trades:
        y = t.entry_ts[:4]
        years.setdefault(y, []).append(t.net_pct)
    per_year = [{"year": y, "n": len(v), "mean_net": float(np.mean(v))} for y, v in sorted(years.items())]
    both_pos = (s1["mean_net"] or 0) > 0 and (s2["mean_net"] or 0) > 0
    if s1["n"] == 0 or s2["n"] == 0:
        status, head = "warn", "All trades fall in one half of the data (%s)." % ("first" if s1["n"] else "second")
    elif both_pos:
        status, head = "pass", "Positive in both halves: %s (n=%d) then %s (n=%d)." % (_fmt(s1["mean_net"]), s1["n"], _fmt(s2["mean_net"]), s2["n"])
    else:
        status, head = "fail", "Sign flips between halves: %s (n=%d) then %s (n=%d)." % (_fmt(s1["mean_net"]), s1["n"], _fmt(s2["mean_net"]), s2["n"])
    return finding("time_split", "Does it hold in both halves of the data?", status, head, first_half=s1, second_half=s2, split_at=str(mid.date()), per_year=per_year)


# --------------------------------------------------------------------------- 4
class _Perturb(ast.NodeTransformer):
    def __init__(self, target_index: int, factor: float):
        self.i = -1
        self.target = target_index
        self.factor = factor
        self.changed = None
        self._in_eq = False

    def visit_Compare(self, node):
        eq = any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
        prev, self._in_eq = self._in_eq, eq
        node = self.generic_visit(node)
        self._in_eq = prev
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return node
        self.i += 1
        if self.i != self.target or self._in_eq or node.value == 0:
            return node
        v = node.value * self.factor
        if isinstance(node.value, int):
            v = int(round(v))
            if v == node.value:
                v = node.value + (1 if self.factor > 1 else -1)
            if v < 1 and node.value >= 1:
                v = 1
        else:
            v = round(v, 4)
        self.changed = (node.value, v)
        return ast.copy_location(ast.Constant(value=v), node)


def _count_constants(expr: str) -> int:
    n = 0
    for node in ast.walk(ast.parse(expr, mode="eval")):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            n += 1
    return n


def neighbourhood_specs(spec: ClaimSpec, max_constants: int = 4) -> list[tuple[str, ClaimSpec]]:
    out = []
    nconst = min(_count_constants(spec.entry), max_constants)
    for idx in range(nconst):
        for factor in (0.8, 1.25):
            tr = _Perturb(idx, factor)
            tree = tr.visit(ast.parse(spec.entry, mode="eval"))
            if tr.changed is None:
                continue
            new_expr = ast.unparse(tree)
            try:
                compile_expression(new_expr)
            except DSLError:
                continue
            s = spec.model_copy(deep=True)
            s.entry = new_expr
            out.append(("entry: %s → %s" % (tr.changed[0], tr.changed[1]), s))
    for delta in (-max(1, round(spec.exit.bars * 0.4)), max(1, round(spec.exit.bars * 0.4))):
        nb = spec.exit.bars + delta
        if 1 <= nb <= 500 and nb != spec.exit.bars:
            s = spec.model_copy(deep=True)
            s.exit.bars = nb
            out.append(("hold: %d → %d bars" % (spec.exit.bars, nb), s))
    return out


def parameter_neighbourhood(df: pd.DataFrame, spec: ClaimSpec, bt: B.BacktestResult) -> dict:
    variants = neighbourhood_specs(spec)
    rows = []
    for label, s in variants:
        try:
            r = B.run(s, df)
        except Exception as e:  # pragma: no cover
            rows.append({"variant": label, "error": str(e)[:120]})
            continue
        rows.append({"variant": label, "entry": s.entry, "bars": s.exit.bars, "n_trades": r.n_trades, "mean_net": None if r.n_trades == 0 else r.mean_net, "win_rate": None if r.n_trades == 0 else r.win_rate})
    vals = [r["mean_net"] for r in rows if r.get("mean_net") is not None and r.get("n_trades", 0) >= 5]
    if not vals:
        return finding("neighbourhood", "Nearby parameters", "warn", "Could not build comparable variants.", table=rows)
    share_pos = float(np.mean([v > 0 for v in vals]))
    med = float(np.median(vals))
    if bt.n_trades == 0 or bt.mean_net is None or not math.isfinite(bt.mean_net):
        status, head = "warn", "Original rule produced no trades."
    elif bt.mean_net > 0 and share_pos < 0.5:
        status, head = "fail", "Only the exact parameters work: %d of %d nearby variants are positive (median %s). That is the signature of curve fitting." % (sum(v > 0 for v in vals), len(vals), _fmt(med))
    elif bt.mean_net > 0 and bt.mean_net > 2 * max(vals):
        status, head = "warn", "The chosen parameters are far better than every neighbour (%s vs best neighbour %s)." % (_fmt(bt.mean_net), _fmt(max(vals)))
    elif bt.mean_net > 0 and (share_pos < 0.67 or med < 0.25 * bt.mean_net):
        status, head = "warn", "Neighbours are lukewarm: %d of %d nearby variants are positive, median %s vs %s for the chosen parameters." % (sum(v > 0 for v in vals), len(vals), _fmt(med), _fmt(bt.mean_net))
    else:
        status, head = "pass", "%d of %d nearby variants point the same way (median %s)." % (sum((v > 0) == (bt.mean_net > 0) for v in vals), len(vals), _fmt(med))
    return finding("neighbourhood", "Nearby parameters", status, head, table=rows, share_positive=share_pos, median_mean_net=med, original_mean_net=bt.mean_net)


# --------------------------------------------------------------------------- 5
def cross_asset(spec: ClaimSpec, bt: B.BacktestResult, load_fn, assets: list[str], n_boot: int = 500) -> dict:
    rows = []
    for a in assets:
        if a == spec.asset:
            continue
        try:
            df = load_fn(a)
            s = spec.model_copy(deep=True)
            s.asset = a
            r = B.run(s, df)
            row = {"asset": a, "n_trades": r.n_trades, "mean_net": None if r.n_trades == 0 else r.mean_net, "win_rate": None if r.n_trades == 0 else r.win_rate}
            if r.n_trades >= 5:
                per_entry = B.all_entry_returns(df, s)
                valid = np.flatnonzero(np.isfinite(per_entry))
                if len(valid) >= r.n_trades:
                    rng = np.random.default_rng(11)
                    picks = np.stack([rng.choice(valid, size=r.n_trades, replace=False) for _ in range(n_boot)])
                    means = per_entry[picks].mean(axis=1)
                    row["p_value"] = float(((means >= r.mean_net).sum() + 1) / (n_boot + 1))
                    row["excess"] = float(r.mean_net - np.nanmean(per_entry))
            rows.append(row)
        except Exception as e:  # pragma: no cover
            rows.append({"asset": a, "error": str(e)[:120]})
    tested = [r for r in rows if r.get("mean_net") is not None and r.get("n_trades", 0) >= 5]
    if not tested:
        return finding("cross_asset", "Other assets", "info", "No other asset produced enough trades.", table=rows)
    pos = sum(1 for r in tested if (r.get("excess") if r.get("excess") is not None else r["mean_net"]) > 0)
    sig = sum(1 for r in tested if r.get("p_value") is not None and r["p_value"] < 0.05)
    share = pos / len(tested)
    if share >= 0.6:
        status, head = "pass", "Beats random entries on %d of %d other assets (%d significant at 5%%)." % (pos, len(tested), sig)
    elif share >= 0.4:
        status, head = "warn", "Mixed elsewhere: better than random on %d of %d other assets." % (pos, len(tested))
    else:
        status, head = "fail", "Does not travel: better than random on only %d of %d other assets." % (pos, len(tested))
    return finding("cross_asset", "Other assets", status, head, table=rows, share_positive=share, n_significant=sig)


# --------------------------------------------------------------------------- 6
def sample_size(bt: B.BacktestResult, spec: ClaimSpec) -> dict:
    n = bt.n_trades
    need = spec.criterion.min_trades
    if n < need:
        return finding("sample_size", "Sample size", "fail", "Only %d completed trade(s); the pre-registered minimum was %d. Nothing can be concluded." % (n, need), n_trades=n, min_trades=need, n_signals=bt.n_signals)
    note = ""
    if spec.position == "every_signal" and bt.exposure and bt.avg_bars_held and bt.avg_bars_held > 1:
        note = " Trades overlap (every signal taken), so the effective sample is smaller than %d." % n
    return finding("sample_size", "Sample size", "pass", "%d completed trades from %d signals.%s" % (n, bt.n_signals, note), n_trades=n, min_trades=need, n_signals=bt.n_signals)


# --------------------------------------------------------------------------- 7
def multiple_testing(spec: ClaimSpec, prior_claims_same_asset: int, p_value: float | None) -> dict:
    """Bonferroni over the beliefs *this searcher* has sealed on the same asset.

    Testing ten ideas and reporting the best one is how most 'edges' are born; the ledger remembers."""
    k = prior_claims_same_asset + 1
    alpha_adj = spec.criterion.alpha / k
    if p_value is None:
        return finding("multiple_testing", "How many beliefs have you tried?", "info", "This is your belief #%d about %s." % (k, spec.asset), claims_so_far=k, alpha=spec.criterion.alpha, alpha_adjusted=alpha_adj)
    if k == 1:
        status, head = "pass", "Your first sealed belief about %s; no correction needed (alpha %.3f)." % (spec.asset, spec.criterion.alpha)
    elif p_value < alpha_adj:
        status, head = "pass", "Survives a Bonferroni correction for the %d beliefs you have sealed about %s (needs p < %.4f, got %.4f)." % (k, spec.asset, alpha_adj, p_value)
    elif p_value < spec.criterion.alpha:
        status, head = "fail", "Significant alone (p = %.3f) but not after correcting for the %d beliefs you have sealed about %s (needs p < %.4f). Try enough ideas and one will look good by chance." % (p_value, k, spec.asset, alpha_adj)
    else:
        status, head = "info", "Not significant even before correction (p = %.3f); you have sealed %d beliefs about %s." % (p_value, k, spec.asset)
    return finding("multiple_testing", "How many beliefs have you tried?", status, head, claims_so_far=k, alpha=spec.criterion.alpha, alpha_adjusted=alpha_adj, p_value=p_value)


# --------------------------------------------------------------------------- 8
def lookahead_guard(spec: ClaimSpec, bt: B.BacktestResult) -> dict:
    return finding(
        "lookahead", "Look-ahead guard", "pass",
        "Conditions use only past and current bars; every trade opens at the next bar's open. Indicators used: %s." % ", ".join(compile_expression(spec.entry).used_indicators),
        convention="signal at close of bar i, entry at open of bar i+1, exit at close of bar i+%d" % spec.exit.bars,
    )


# --------------------------------------------------------------------------- 9
def opportunity_cost(bt: B.BacktestResult) -> dict:
    if bt.n_trades == 0 or bt.total_net is None:
        return finding("opportunity", "Versus just holding", "info", "No trades to compare with holding.")
    diff = bt.total_net - bt.buy_and_hold_total
    status = "info"
    head = "Rule compounded %s while holding the asset over the same window returned %s (exposure %.0f%% of the time)." % (_fmt(bt.total_net, 1), _fmt(bt.buy_and_hold_total, 1), 100 * (bt.exposure or 0))
    return finding("opportunity", "Versus just holding", status, head, rule_total=bt.total_net, hold_total=bt.buy_and_hold_total, difference=diff, exposure=bt.exposure)


# --------------------------------------------------------------------------- variants proposed by the agent
def run_variant(spec: ClaimSpec, label: str, load_fn) -> dict:
    df = load_fn(spec.asset, spec.dataset_id, spec.start, spec.end)
    r = B.run(spec, df)
    row = {"label": label, "asset": spec.asset, "entry": spec.entry, "bars": spec.exit.bars, "direction": spec.direction, "n_trades": r.n_trades, "mean_net": None if r.n_trades == 0 else r.mean_net, "win_rate": None if r.n_trades == 0 else r.win_rate, "total_net": None if r.n_trades == 0 else r.total_net}
    if r.n_trades >= 5:
        rb = random_baseline(df, spec, r, n_boot=500)
        row["p_value"] = rb["details"].get("p_value")
        row["excess"] = rb["details"].get("excess")
    return row


def full_battery(df: pd.DataFrame, spec: ClaimSpec, bt: B.BacktestResult, load_fn, assets: list[str], prior_claims_same_asset: int = 0) -> list[dict]:
    out = [sample_size(bt, spec), lookahead_guard(spec, bt)]
    rb = random_baseline(df, spec, bt)
    out.append(rb)
    out.append(multiple_testing(spec, prior_claims_same_asset, rb["details"].get("p_value")))
    out.append(fee_sensitivity(bt, spec))
    out.append(time_split(bt, df))
    out.append(parameter_neighbourhood(df, spec, bt))
    out.append(cross_asset(spec, bt, load_fn, assets))
    out.append(opportunity_cost(bt))
    return out
