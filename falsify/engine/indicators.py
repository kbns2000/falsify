"""Indicator catalogue used by the claim DSL.

Every indicator is a function ``f(df, *params) -> pd.Series`` aligned with ``df``.
Only *past and current bar* information is used; forward-looking helpers are
deliberately absent so a claim can never peek at the future.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class IndicatorDef:
    name: str
    fn: Callable
    params: tuple[str, ...]
    defaults: tuple
    doc: str
    unit: str = ""


def _s(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].astype(float)


def sma(df, n=20):
    return _s(df, "close").rolling(int(n)).mean()


def ema(df, n=20):
    return _s(df, "close").ewm(span=int(n), adjust=False).mean()


def rsi(df, n=14):
    c = _s(df, "close")
    d = c.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    # Wilder smoothing
    au = up.ewm(alpha=1.0 / int(n), adjust=False, min_periods=int(n)).mean()
    ad = dn.ewm(alpha=1.0 / int(n), adjust=False, min_periods=int(n)).mean()
    rs = au / ad.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(ad != 0.0, 100.0)
    return out


def atr(df, n=14):
    h, l, c = _s(df, "high"), _s(df, "low"), _s(df, "close")
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / int(n), adjust=False, min_periods=int(n)).mean()


def atr_pct(df, n=14):
    return atr(df, n) / _s(df, "close") * 100.0


def ret(df, n=1):
    """Percent return over the last n bars (close/close[-n] - 1) * 100."""
    return _s(df, "close").pct_change(int(n)) * 100.0


def high_n(df, n=20):
    """Highest high over the last n bars, including the current bar."""
    return _s(df, "high").rolling(int(n)).max()


def low_n(df, n=20):
    return _s(df, "low").rolling(int(n)).min()


def close_high_n(df, n=20):
    return _s(df, "close").rolling(int(n)).max()


def close_low_n(df, n=20):
    return _s(df, "close").rolling(int(n)).min()


def bb_upper(df, n=20, k=2.0):
    c = _s(df, "close")
    return c.rolling(int(n)).mean() + float(k) * c.rolling(int(n)).std(ddof=0)


def bb_lower(df, n=20, k=2.0):
    c = _s(df, "close")
    return c.rolling(int(n)).mean() - float(k) * c.rolling(int(n)).std(ddof=0)


def bb_pct(df, n=20, k=2.0):
    """Position inside the Bollinger band: 0 = lower band, 1 = upper band."""
    up, lo = bb_upper(df, n, k), bb_lower(df, n, k)
    return (_s(df, "close") - lo) / (up - lo).replace(0.0, np.nan)


def macd(df, fast=12, slow=26):
    c = _s(df, "close")
    return c.ewm(span=int(fast), adjust=False).mean() - c.ewm(span=int(slow), adjust=False).mean()


def macd_signal(df, fast=12, slow=26, sig=9):
    return macd(df, fast, slow).ewm(span=int(sig), adjust=False).mean()


def macd_hist(df, fast=12, slow=26, sig=9):
    return macd(df, fast, slow) - macd_signal(df, fast, slow, sig)


def vol_sma(df, n=20):
    return _s(df, "volume").rolling(int(n)).mean()


def vol_ratio(df, n=20):
    """Volume divided by its n-bar average (1.0 = average volume)."""
    return _s(df, "volume") / vol_sma(df, n).replace(0.0, np.nan)


def drawdown(df, n=90):
    """Percent below the highest close of the last n bars (<= 0)."""
    c = _s(df, "close")
    return (c / c.rolling(int(n)).max() - 1.0) * 100.0


def runup(df, n=90):
    """Percent above the lowest close of the last n bars (>= 0)."""
    c = _s(df, "close")
    return (c / c.rolling(int(n)).min() - 1.0) * 100.0


def _streak(sign: pd.Series) -> pd.Series:
    out = np.zeros(len(sign), dtype=float)
    run = 0
    vals = sign.to_numpy()
    for i, v in enumerate(vals):
        run = run + 1 if v else 0
        out[i] = run
    return pd.Series(out, index=sign.index)


def streak_down(df):
    """Number of consecutive bars that closed lower than the previous close."""
    c = _s(df, "close")
    return _streak(c.diff() < 0)


def streak_up(df):
    c = _s(df, "close")
    return _streak(c.diff() > 0)


def dow(df):
    """Day of week of the bar (0 = Monday ... 6 = Sunday, UTC)."""
    return pd.Series(df["ts"].dt.day_of_week.astype(float).to_numpy(), index=df.index)


def month(df):
    return pd.Series(df["ts"].dt.month.astype(float).to_numpy(), index=df.index)


def hour(df):
    return pd.Series(df["ts"].dt.hour.astype(float).to_numpy(), index=df.index)


def day_of_month(df):
    return pd.Series(df["ts"].dt.day.astype(float).to_numpy(), index=df.index)


def body_pct(df):
    """Candle body: (close - open) / open * 100."""
    o = _s(df, "open")
    return (_s(df, "close") - o) / o.replace(0.0, np.nan) * 100.0


def range_pct(df):
    """Candle range: (high - low) / close * 100."""
    return (_s(df, "high") - _s(df, "low")) / _s(df, "close") * 100.0


def gap_pct(df):
    """Open versus previous close, in percent."""
    return (_s(df, "open") / _s(df, "close").shift(1) - 1.0) * 100.0


def zscore(df, n=20):
    c = _s(df, "close")
    m = c.rolling(int(n)).mean()
    s = c.rolling(int(n)).std(ddof=0).replace(0.0, np.nan)
    return (c - m) / s


def volatility(df, n=20):
    """Annualised volatility of bar returns in percent (daily bars assumed)."""
    r = _s(df, "close").pct_change()
    return r.rolling(int(n)).std(ddof=0) * np.sqrt(365.0) * 100.0


def stoch_k(df, n=14):
    h, l, c = _s(df, "high"), _s(df, "low"), _s(df, "close")
    hh, ll = h.rolling(int(n)).max(), l.rolling(int(n)).min()
    return (c - ll) / (hh - ll).replace(0.0, np.nan) * 100.0


def obv(df):
    c, v = _s(df, "close"), _s(df, "volume")
    return (np.sign(c.diff()).fillna(0.0) * v).cumsum()


def bars_since_high(df, n=90):
    """Bars elapsed since the highest close of the last n bars."""
    c = _s(df, "close").to_numpy()
    n = int(n)
    out = np.full(len(c), np.nan)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1 : i + 1]
        out[i] = n - 1 - int(np.argmax(w))
    return pd.Series(out, index=df.index)


CATALOGUE: dict[str, IndicatorDef] = {}


def _reg(name, fn, params, defaults, doc, unit=""):
    CATALOGUE[name] = IndicatorDef(name, fn, tuple(params), tuple(defaults), doc, unit)


_reg("sma", sma, ("n",), (20,), "Simple moving average of close over n bars", "price")
_reg("ema", ema, ("n",), (20,), "Exponential moving average of close", "price")
_reg("rsi", rsi, ("n",), (14,), "Relative Strength Index (Wilder), 0-100", "0-100")
_reg("atr", atr, ("n",), (14,), "Average true range in price units", "price")
_reg("atr_pct", atr_pct, ("n",), (14,), "ATR as percent of close", "%")
_reg("ret", ret, ("n",), (1,), "Percent change of close over the last n bars", "%")
_reg("high_n", high_n, ("n",), (20,), "Highest high of the last n bars (incl. current)", "price")
_reg("low_n", low_n, ("n",), (20,), "Lowest low of the last n bars (incl. current)", "price")
_reg("close_high_n", close_high_n, ("n",), (20,), "Highest close of the last n bars", "price")
_reg("close_low_n", close_low_n, ("n",), (20,), "Lowest close of the last n bars", "price")
_reg("bb_upper", bb_upper, ("n", "k"), (20, 2.0), "Bollinger upper band", "price")
_reg("bb_lower", bb_lower, ("n", "k"), (20, 2.0), "Bollinger lower band", "price")
_reg("bb_pct", bb_pct, ("n", "k"), (20, 2.0), "Position in Bollinger band, 0=lower 1=upper", "ratio")
_reg("macd", macd, ("fast", "slow"), (12, 26), "MACD line", "price")
_reg("macd_signal", macd_signal, ("fast", "slow", "sig"), (12, 26, 9), "MACD signal line", "price")
_reg("macd_hist", macd_hist, ("fast", "slow", "sig"), (12, 26, 9), "MACD histogram", "price")
_reg("vol_sma", vol_sma, ("n",), (20,), "Average volume over n bars", "volume")
_reg("vol_ratio", vol_ratio, ("n",), (20,), "Volume / its n-bar average (2 = double the usual volume)", "ratio")
_reg("drawdown", drawdown, ("n",), (90,), "Percent below the highest close of the last n bars (<=0)", "%")
_reg("runup", runup, ("n",), (90,), "Percent above the lowest close of the last n bars (>=0)", "%")
_reg("streak_down", streak_down, (), (), "Consecutive down closes so far", "bars")
_reg("streak_up", streak_up, (), (), "Consecutive up closes so far", "bars")
_reg("dow", dow, (), (), "Day of week, 0=Mon .. 6=Sun (UTC)", "0-6")
_reg("month", month, (), (), "Calendar month 1-12", "1-12")
_reg("hour", hour, (), (), "Hour of day (UTC), intraday data only", "0-23")
_reg("day_of_month", day_of_month, (), (), "Day of month 1-31", "1-31")
_reg("body_pct", body_pct, (), (), "Candle body (close-open)/open in percent", "%")
_reg("range_pct", range_pct, (), (), "Candle range (high-low)/close in percent", "%")
_reg("gap_pct", gap_pct, (), (), "Open vs previous close in percent", "%")
_reg("zscore", zscore, ("n",), (20,), "Z-score of close against its n-bar mean", "sd")
_reg("volatility", volatility, ("n",), (20,), "Annualised volatility of returns, percent", "%")
_reg("stoch_k", stoch_k, ("n",), (14,), "Stochastic %K, 0-100", "0-100")
_reg("obv", obv, (), (), "On-balance volume", "volume")
_reg("bars_since_high", bars_since_high, ("n",), (90,), "Bars since the highest close of the last n bars", "bars")

COLUMNS = ("open", "high", "low", "close", "volume")


def catalogue_text() -> str:
    """Human/LLM readable list of indicators for prompts."""
    lines = []
    for d in CATALOGUE.values():
        sig = d.name + "(" + ", ".join("%s=%s" % (p, v) for p, v in zip(d.params, d.defaults)) + ")"
        lines.append("- %s — %s [%s]" % (sig, d.doc, d.unit))
    return "\n".join(lines)
