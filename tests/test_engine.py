import numpy as np
import pandas as pd
import pytest

from falsify.engine import attacks, backtest, data, dsl, indicators as I, verdict
from falsify.engine.spec import ClaimSpec, seal, canonical_json


def synthetic(n=600, seed=1, drift=0.0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.02, n)))
    ts = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    o = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"asset": "SYN", "ts": ts, "open": o, "high": np.maximum(o, close) * 1.01, "low": np.minimum(o, close) * 0.99, "close": close, "volume": rng.uniform(100, 200, n)})


def spec(entry="rsi(14) < 30", **kw):
    base = dict(title="test claim", belief_text="test belief", asset="BTC/USDT", entry=entry)
    base.update(kw)
    return ClaimSpec(**base)


def test_dsl_accepts_and_rejects():
    ok = dsl.compile_expression("rsi(14) < 30 and close > sma(200)")
    assert "rsi(14)" in ok.used_indicators and "sma(200)" in ok.used_indicators
    for bad in ["rsi(14)", "__import__('os')", "close.shift(1) > 0", "foo(1) > 0", "close > 'x'", "rsi(0) < 1", "(lambda: 1)() > 0"]:
        with pytest.raises(dsl.DSLError):
            dsl.compile_expression(bad)


def test_indicators_bounds():
    df = synthetic()
    r = I.rsi(df)
    assert r.dropna().between(0, 100).all()
    assert I.sma(df, 5).dropna().shape[0] == len(df) - 4
    assert (I.streak_down(df) >= 0).all()
    assert I.dow(df).between(0, 6).all()


def test_entry_next_open_and_hold():
    df = synthetic()
    s = spec("close > 0", exit={"bars": 3}, position="every_signal")
    r = backtest.run(s, df)
    t = r.trades[0]
    # signal at bar 0 -> entry at bar 1 open, exit at close of bar 3
    assert t.entry_idx == 1 and t.exit_idx == 3 and t.bars_held == 3
    assert abs(t.entry_px - df.open.iloc[1]) < 1e-9 and abs(t.exit_px - df.close.iloc[3]) < 1e-9
    assert r.n_trades == len(df) - 1 - 2  # last entries without 3 bars left are dropped


def test_one_at_a_time_blocks_overlap():
    df = synthetic()
    s = spec("close > 0", exit={"bars": 5})
    r = backtest.run(s, df)
    idx = [t.entry_idx for t in r.trades]
    assert all(b - a >= 5 for a, b in zip(idx, idx[1:]))


def test_stop_and_target():
    df = synthetic(seed=3)
    s = spec("close > 0", exit={"bars": 20, "stop_pct": 2, "target_pct": 2})
    r = backtest.run(s, df)
    reasons = {t.reason for t in r.trades}
    assert reasons <= {"stop", "target", "time"} and ("stop" in reasons or "target" in reasons)
    for t in r.trades:
        if t.reason == "stop":
            assert t.gross_pct <= -2 + 1e-6
        if t.reason == "target":
            assert t.gross_pct >= 2 - 1e-6


def test_fees_reduce_returns():
    g = np.array([1.0, -1.0, 0.0])
    net = backtest.net_from_gross(g, 10)
    assert (net < g).all()


def test_short_direction_flips_sign():
    df = synthetic(seed=5)
    long = backtest.run(spec("close > 0", exit={"bars": 2}, position="every_signal"), df)
    short = backtest.run(spec("close > 0", exit={"bars": 2}, position="every_signal", direction="short"), df)
    assert np.isclose(long.trades[0].gross_pct, -short.trades[0].gross_pct)


def test_random_baseline_p_in_range_and_random_rule_not_significant():
    df = synthetic(seed=11)
    s = spec("dow() == 2", exit={"bars": 1})
    r = backtest.run(s, df)
    f = attacks.random_baseline(df, s, r, n_boot=400)
    assert 0 < f["details"]["p_value"] <= 1
    assert f["details"]["n_trades"] == r.n_trades


def test_neighbourhood_variants_change_constants():
    s = spec("rsi(14) < 30 and close > sma(200)", exit={"bars": 5})
    labels = [l for l, _ in attacks.neighbourhood_specs(s)]
    assert any("14" in l for l in labels) and any("200" in l for l in labels) and any("hold" in l for l in labels)
    assert all(_.entry != s.entry or _.exit.bars != s.exit.bars for _, _ in [(x, x) for _, x in attacks.neighbourhood_specs(s)])


def test_seal_is_deterministic_for_same_spec():
    s = spec()
    assert canonical_json(s) == canonical_json(spec())
    a, b = seal(s, "x"), seal(s, "x")
    assert len(a["spec_hash"]) == 64
    assert a["canonical"] == b["canonical"]


def test_verdict_inconclusive_when_few_trades():
    df = synthetic()
    s = spec("rsi(14) < 5", exit={"bars": 5})
    r = backtest.run(s, df)
    f = attacks.full_battery(df, s, r, lambda a: df, ["BTC/USDT"], 0)
    v = verdict.decide(s, r, f)
    assert v["label"] == "INCONCLUSIVE"


def test_builtin_data_loads():
    df = data.load("btc")
    assert {"ts", "open", "high", "low", "close", "volume"} <= set(df.columns)
    assert data.resolve_asset("solana") == "SOL/USDT"
