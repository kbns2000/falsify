"""Deterministic rule evaluation. No model is involved here on purpose:
the numbers that decide a verdict must not be something a language model can fudge.

Conventions
-----------
* The entry condition is evaluated at the close of bar *i*; the trade opens at the
  open of bar *i+1*. Nothing from the future leaks into the signal.
* ``exit.bars = N`` holds the position for N bars and closes at the close of the
  N-th bar (entry bar included).
* Optional stop/target are checked against each bar's low/high; if both could have
  been hit in the same bar, the stop is assumed first (conservative).
* Fees are charged per side as basis points of notional.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from .dsl import compile_expression
from .spec import ClaimSpec


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    entry_ts: str
    exit_ts: str
    entry_px: float
    exit_px: float
    gross_pct: float
    net_pct: float
    bars_held: int
    reason: str


@dataclass
class BacktestResult:
    asset: str
    n_bars: int
    window_start: str
    window_end: str
    n_signals: int
    n_trades: int
    trades: list[Trade] = field(default_factory=list)
    mean_net: float = float("nan")
    median_net: float = float("nan")
    std_net: float = float("nan")
    win_rate: float = float("nan")
    t_stat: float = float("nan")
    total_net: float = float("nan")  # compounded, percent
    profit_factor: float = float("nan")
    max_drawdown: float = float("nan")  # percent, of the compounded trade equity
    avg_bars_held: float = float("nan")
    exposure: float = float("nan")  # fraction of bars in a position
    buy_and_hold_total: float = float("nan")  # percent over the same window
    years: float = float("nan")
    equity_curve: dict = field(default_factory=dict)  # {ts: [...], rule: [...], hold: [...]}
    signal_ts: list[str] = field(default_factory=list)

    def to_dict(self, max_trades: int = 400) -> dict:
        d = asdict(self)
        d["trades"] = [asdict(t) for t in self.trades[:max_trades]]
        d["trades_truncated"] = len(self.trades) > max_trades
        d["signal_ts"] = self.signal_ts[:1000]
        for k, v in list(d.items()):
            if isinstance(v, float) and not np.isfinite(v):
                d[k] = None
        return d


def _fee_mult(fee_bps: float) -> float:
    return (1.0 - fee_bps / 10_000.0) ** 2


def net_from_gross(gross_pct: np.ndarray, fee_bps: float) -> np.ndarray:
    return ((1.0 + gross_pct / 100.0) * _fee_mult(fee_bps) - 1.0) * 100.0


def simulate_entry(df_np: dict, e: int, spec: ClaimSpec, exit_mask: np.ndarray | None) -> tuple[int, float, float, str] | None:
    """Simulate one trade entered at the open of bar ``e``.

    Returns (exit_idx, entry_px, exit_px, reason) or None if the entry is not possible.
    """
    o, h, l, c = df_np["open"], df_np["high"], df_np["low"], df_np["close"]
    n = len(c)
    if e >= n:
        return None
    bars = spec.exit.bars
    last = min(e + bars - 1, n - 1)
    entry_px = o[e]
    if not np.isfinite(entry_px) or entry_px <= 0:
        return None
    is_long = spec.direction == "long"
    stop = target = None
    if spec.exit.stop_pct:
        stop = entry_px * (1 - spec.exit.stop_pct / 100.0) if is_long else entry_px * (1 + spec.exit.stop_pct / 100.0)
    if spec.exit.target_pct:
        target = entry_px * (1 + spec.exit.target_pct / 100.0) if is_long else entry_px * (1 - spec.exit.target_pct / 100.0)
    for j in range(e, last + 1):
        if stop is not None:
            hit = (l[j] <= stop) if is_long else (h[j] >= stop)
            if hit:
                px = stop
                # gap through the stop: fill at the open
                if is_long and o[j] < stop:
                    px = o[j]
                if (not is_long) and o[j] > stop:
                    px = o[j]
                return j, entry_px, px, "stop"
        if target is not None:
            hit = (h[j] >= target) if is_long else (l[j] <= target)
            if hit:
                px = target
                if is_long and o[j] > target:
                    px = o[j]
                if (not is_long) and o[j] < target:
                    px = o[j]
                return j, entry_px, px, "target"
        if exit_mask is not None and j < last and exit_mask[j] and j + 1 < n:
            return j + 1, entry_px, o[j + 1], "exit_rule"
    return last, entry_px, c[last], "time"


def gross_pct(entry_px: float, exit_px: float, direction: str) -> float:
    r = exit_px / entry_px - 1.0
    if direction == "short":
        r = -r
    return r * 100.0


def _frame_np(df: pd.DataFrame) -> dict:
    return {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")}


def all_entry_returns(df: pd.DataFrame, spec: ClaimSpec) -> np.ndarray:
    """Net return (percent) of entering at the open of every bar, under the spec's exit rule.

    NaN where an entry is impossible (not enough bars left). Used by the random-entry baseline.
    """
    arr = _frame_np(df)
    n = len(df)
    out = np.full(n, np.nan)
    exit_mask = None
    if spec.exit.exit_condition:
        exit_mask = compile_expression(spec.exit.exit_condition).evaluate(df).to_numpy()
    for e in range(1, n):
        if e + spec.exit.bars - 1 > n - 1:
            break
        sim = simulate_entry(arr, e, spec, exit_mask)
        if sim is None:
            continue
        _, ep, xp, _ = sim
        out[e] = gross_pct(ep, xp, spec.direction)
    return net_from_gross(out, spec.fee_bps_per_side)


def run(spec: ClaimSpec, df: pd.DataFrame) -> BacktestResult:
    df = df.reset_index(drop=True)
    arr = _frame_np(df)
    n = len(df)
    signal = compile_expression(spec.entry).evaluate(df).to_numpy()
    exit_mask = None
    if spec.exit.exit_condition:
        exit_mask = compile_expression(spec.exit.exit_condition).evaluate(df).to_numpy()
    ts = df["ts"]
    trades: list[Trade] = []
    in_pos_until = -1
    sig_idx = np.flatnonzero(signal)
    for i in sig_idx:
        e = int(i) + 1
        if e > n - 1:
            continue
        if e + spec.exit.bars - 1 > n - 1:
            continue  # not enough bars to complete the trade -> not counted
        if spec.position == "one_at_a_time" and e <= in_pos_until:
            continue
        sim = simulate_entry(arr, e, spec, exit_mask)
        if sim is None:
            continue
        x, ep, xp, reason = sim
        g = gross_pct(ep, xp, spec.direction)
        net = float(net_from_gross(np.array([g]), spec.fee_bps_per_side)[0])
        trades.append(Trade(e, x, ts.iloc[e].isoformat(), ts.iloc[x].isoformat(), float(ep), float(xp), float(g), net, x - e + 1, reason))
        in_pos_until = x
    res = BacktestResult(
        asset=spec.asset,
        n_bars=n,
        window_start=ts.iloc[0].isoformat(),
        window_end=ts.iloc[-1].isoformat(),
        n_signals=int(signal.sum()),
        n_trades=len(trades),
        trades=trades,
        signal_ts=[ts.iloc[int(i)].isoformat() for i in sig_idx[:1000]],
    )
    close = arr["close"]
    years = max((ts.iloc[-1] - ts.iloc[0]).total_seconds() / (365.25 * 86400), 1e-9)
    res.years = years
    first_valid = int(np.flatnonzero(np.isfinite(close))[0])
    res.buy_and_hold_total = float((close[-1] / close[first_valid] - 1.0) * 100.0)
    if trades:
        net = np.array([t.net_pct for t in trades])
        res.mean_net = float(net.mean())
        res.median_net = float(np.median(net))
        res.std_net = float(net.std(ddof=1)) if len(net) > 1 else 0.0
        res.win_rate = float((net > 0).mean())
        res.t_stat = float(net.mean() / (net.std(ddof=1) / np.sqrt(len(net)))) if len(net) > 1 and net.std(ddof=1) > 0 else 0.0
        eq = np.cumprod(1.0 + net / 100.0)
        res.total_net = float((eq[-1] - 1.0) * 100.0)
        gains, losses = net[net > 0].sum(), -net[net < 0].sum()
        res.profit_factor = float(gains / losses) if losses > 0 else float("inf") if gains > 0 else 0.0
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
        dd = (np.concatenate([[1.0], eq]) / peak - 1.0) * 100.0
        res.max_drawdown = float(dd.min())
        res.avg_bars_held = float(np.mean([t.bars_held for t in trades]))
        held = np.zeros(n, dtype=bool)
        for t in trades:
            held[t.entry_idx : t.exit_idx + 1] = True
        res.exposure = float(held.mean())
        res.equity_curve = _equity_curve(df, trades, spec)
    else:
        res.equity_curve = _equity_curve(df, [], spec)
    return res


def _equity_curve(df: pd.DataFrame, trades: list[Trade], spec: ClaimSpec, max_points: int = 600) -> dict:
    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    rule = np.ones(n)
    fee = spec.fee_bps_per_side / 10_000.0
    # mark-to-market: while in a trade, equity follows close relative to entry
    level = 1.0
    ti = 0
    trades_sorted = sorted(trades, key=lambda t: t.entry_idx)
    cur = None
    for i in range(n):
        if cur is None and ti < len(trades_sorted) and trades_sorted[ti].entry_idx == i:
            cur = trades_sorted[ti]
            ti += 1
        if cur is not None:
            if i < cur.exit_idx:
                r = close[i] / cur.entry_px - 1.0
                if spec.direction == "short":
                    r = -r
                rule[i] = level * (1.0 + r) * (1.0 - fee)
            else:
                level = level * (1.0 + cur.net_pct / 100.0)
                rule[i] = level
                cur = None
        else:
            rule[i] = level
    first_valid = int(np.flatnonzero(np.isfinite(close))[0])
    hold = close / close[first_valid]
    idx = np.arange(n)
    if n > max_points:
        idx = np.unique(np.concatenate([np.linspace(0, n - 1, max_points).astype(int), [n - 1]]))
    ts = df["ts"].dt.strftime("%Y-%m-%d").to_numpy() if spec.timeframe == "1d" else df["ts"].dt.strftime("%Y-%m-%d %H:%M").to_numpy()
    return {
        "ts": [str(ts[i]) for i in idx],
        "rule": [round(float(rule[i]), 5) for i in idx],
        "hold": [round(float(hold[i]), 5) for i in idx],
    }
