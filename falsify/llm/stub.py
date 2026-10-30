"""Deterministic offline stand-in for the model. Used in tests and when no key is configured.
It understands a handful of common belief shapes; everything else becomes an RSI dip rule so
the pipeline still runs end to end. The UI shows a clear 'offline' badge in this mode."""
from __future__ import annotations

import re

ASSET_WORDS = {
    "btc": "BTC/USDT", "bitcoin": "BTC/USDT", "비트": "BTC/USDT", "비트코인": "BTC/USDT",
    "eth": "ETH/USDT", "ethereum": "ETH/USDT", "이더": "ETH/USDT", "이더리움": "ETH/USDT",
    "sol": "SOL/USDT", "solana": "SOL/USDT", "솔라나": "SOL/USDT",
    "xrp": "XRP/USDT", "ripple": "XRP/USDT", "리플": "XRP/USDT",
    "doge": "DOGE/USDT", "dogecoin": "DOGE/USDT", "도지": "DOGE/USDT",
    "ada": "ADA/USDT", "cardano": "ADA/USDT", "에이다": "ADA/USDT",
    "bnb": "BNB/USDT", "avax": "AVAX/USDT", "link": "LINK/USDT", "ton": "TON/USDT",
}


def _lang(text: str) -> str:
    return "ko" if re.search(r"[가-힣]", text) else "en"


def _num(pattern: str, text: str, default):
    m = re.search(pattern, text, re.I)
    return type(default)(m.group(1)) if m else default


def formalize(belief: str, assets: list[str]) -> dict:
    t = belief.lower()
    asset = next((a for w, a in ASSET_WORDS.items() if re.search(r"\b%s\b" % re.escape(w), t) or (w in t and not w.isascii())), "BTC/USDT")
    if asset not in assets:
        asset = assets[0]
    direction = "short" if re.search(r"\b(short|sell|shorting)\b|공매도|숏", t) and not re.search(r"\bbuy\b|산다|매수", t) else "long"
    bars = _num(r"(\d+)\s*(?:day|days|bars|일|거래일)", t, 5)
    if re.search(r"week|주일", t) and not re.search(r"\d+\s*(day|일)", t):
        bars = 10 if re.search(r"two|2\s*week|2주", t) else 5
    if re.search(r"next day|다음\s*날|이튼날|하루", t):
        bars = 1
    assumptions = []
    if re.search(r"rsi", t):
        n = _num(r"rsi\s*\(?(\d+)\)?", t, 14)
        thr = _num(r"(?:under|below|<|아래|이하|밑)\D{0,10}(\d+)|(\d+)\s*(?:아래|이하|밑)", t, 30) if not re.search(r"above|over|>|위|넘", t) else None
        if thr is None:
            thr = _num(r"(?:above|over|>|위|넘)\D{0,10}(\d+)", t, 70)
            entry = "rsi(%d) > %d" % (n, thr)
        else:
            entry = "rsi(%d) < %d" % (n, thr)
        title = "RSI rule"
    elif re.search(r"golden|cross", t):
        entry = "cross_above(sma(20), sma(50))"
        title = "Golden cross"
        assumptions.append("golden cross read as SMA20 crossing above SMA50")
    elif re.search(r"breakout|new high|고점|돌파|highest", t):
        n = _num(r"(\d+)[- ]?(?:day|일)", t, 20)
        entry = "close > shift(high_n(%d), 1)" % n
        title = "%d-bar breakout" % n
        if re.search(r"volume|거래량", t):
            entry += " and vol_ratio(20) > 1.5"
    elif re.search(r"consecutive|in a row|연속|straight", t) or re.search(r"(\d+)\s*(?:red|down)\s*(?:days|candles)", t):
        n = _num(r"(\d+)\s*(?:consecutive|in a row|연속|straight|red|down)", t, 3)
        n = _num(r"(\d+)\s*(?:일|day)?\s*연속", t, n)
        entry = "streak_down() >= %d" % n if not re.search(r"up|green|상승|오르", t) else "streak_up() >= %d" % n
        title = "%d-bar streak" % n
    elif re.search(r"monday|월요일", t):
        entry = "dow() == 6"
        title = "Monday effect"
        assumptions.append("'Monday' means the signal fires at Sunday's close so the trade opens Monday")
        bars = bars if bars != 5 else 1
    elif re.search(r"friday|금요일", t):
        entry = "dow() == 3"
        title = "Friday effect"
        bars = 1
    elif re.search(r"dip|drop|fall|떨어|급락|하락", t):
        pct = _num(r"(\d+(?:\.\d+)?)\s*%", t, 5.0)
        entry = "ret(1) < -%s" % pct
        title = "Buy the %g%% dip" % pct
    elif re.search(r"volume|거래량", t):
        entry = "vol_ratio(20) > 2"
        title = "Volume spike"
    elif re.search(r"bollinger|band|밴드", t):
        entry = "close < bb_lower(20, 2)"
        title = "Lower Bollinger touch"
    elif re.search(r"200|moving average|이동평균|이평", t):
        entry = "cross_above(close, sma(200))"
        title = "Cross above 200-day"
    else:
        entry = "rsi(14) < 30"
        title = "Oversold dip"
        assumptions.append("belief shape not recognised offline; mapped to an RSI<30 dip rule")
    if asset == "BTC/USDT" and not any(w in t for w in ("btc", "bitcoin", "비트")):
        assumptions.append("no asset named: BTC/USDT")
    lang = _lang(belief)
    plain = ("%s가 「%s」 조건을 종가에 만족하면 다음 날 시가에 %s, %d개 봉 뒤 종가에 청산한다." % (asset, entry, "산다" if direction == "long" else "공매도한다", bars)) if lang == "ko" else ("When %s closes with %s, go %s at the next open and exit at the close %d bar(s) later." % (asset, entry, direction, bars))
    return {
        "title": title, "asset": asset, "direction": direction, "entry": entry,
        "exit": {"bars": int(bars), "stop_pct": None, "target_pct": None, "exit_condition": None},
        "position": "one_at_a_time", "fee_bps_per_side": 10,
        "criterion": {"metric": "mean_net_return", "beats": "random_entries", "alpha": 0.05, "min_trades": 20},
        "plain_language": plain, "language": lang, "assumptions": assumptions, "notes": "offline stub",
    }


def variants(spec: dict, assets: list[str]) -> dict:
    out = []
    mirror = dict(spec)
    mirror["direction"] = "short" if spec["direction"] == "long" else "long"
    out.append({"label": "Mirror image", "why": "If the opposite trade also looks good, the result is drift, not signal.", "spec": mirror})
    longer = dict(spec)
    longer["exit"] = dict(spec["exit"], bars=max(1, spec["exit"]["bars"] * 2))
    out.append({"label": "Double the holding period", "why": "Checks whether the edge is timing or just exposure.", "spec": longer})
    other = next((a for a in assets if a != spec["asset"]), None)
    if other:
        o = dict(spec)
        o["asset"] = other
        out.append({"label": "Same rule on %s" % other, "why": "A pattern that only exists on one asset is probably noise.", "spec": o})
    return {"variants": out}


def report(spec: dict, bt: dict, findings: list[dict], verdict: dict, variants: list[dict], model_failed: bool = False) -> str:
    lang = spec.get("language", "en")
    label = verdict["label"]
    fails = [f for f in findings if f["status"] == "fail"]
    warns = [f for f in findings if f["status"] == "warn"]
    passes = [f for f in findings if f["status"] == "pass"]
    n = bt.get("n_trades", 0)
    mean = bt.get("mean_net")
    if lang == "ko":
        head = {"FALSIFIED": "반증됨 — 이 믿음은 과거 데이터에서 사전등록한 기준을 넘지 못했습니다.", "FRAGILE": "취약함 — 본 검정은 넘었지만 반박 하나 이상이 결과를 깨뜨렸습니다.", "SURVIVED": "생존 — 준비한 반박을 모두 견뎠습니다. 미래 예측은 아닙니다.", "INCONCLUSIVE": "판정 불가 — 거래 수가 너무 적어 아무것도 말할 수 없습니다."}[label]
        lines = [head, "", "**무엇을 검정했나**", spec.get("plain_language", ""), "", "**가장 강한 반대 근거**"]
        lines += (["- " + f["headline"] for f in fails] + ["- (약함) " + f["headline"] for f in warns]) or ["- 없음"]
        lines += ["", "**버틴 것**"] + (["- " + f["headline"] for f in passes[:3]] or ["- 없음"])
        lines += ["", "**판정을 바꿀 수 있는 것**", "- 같은 규칙을 더 긴 기간·다른 자산에서 사전등록해 다시 검정", "", "**주의**", "- 과거 자료만 봤습니다. 매수·매도 조언이 아닙니다. " + ("(모델 보고서가 실패해 숫자만으로 만든 요약입니다)" if model_failed else "(오프라인 모드: 모델 없이 생성된 보고서)")]
        if n:
            lines.insert(4, "거래 %d건, 거래당 평균 순수익 %.2f%%." % (n, mean or 0))
        return "\n".join(lines)
    head = {"FALSIFIED": "FALSIFIED — on past data this belief did not beat its pre-registered benchmark.", "FRAGILE": "FRAGILE — it passed the main test but at least one attack broke it.", "SURVIVED": "SURVIVED — it withstood every attack we ran. That is a statement about the past, not a forecast.", "INCONCLUSIVE": "INCONCLUSIVE — too few trades to say anything either way."}[label]
    lines = [head, "", "**What was tested**", spec.get("plain_language", "")]
    if n:
        lines.append("%d trades, mean net return %.2f%% per trade." % (n, mean or 0))
    lines += ["", "**Strongest evidence against**"] + ((["- " + f["headline"] for f in fails] + ["- (weak) " + f["headline"] for f in warns]) or ["- none"])
    lines += ["", "**What held up**"] + (["- " + f["headline"] for f in passes[:3]] or ["- nothing"])
    lines += ["", "**What would change the verdict**", "- Pre-register the same rule on a longer window or a different asset and test again.", "", "**Caveats**", "- Past data only. Not investment advice. " + ("(The model report failed; this summary was built from the numbers alone.)" if model_failed else "(Offline mode: this report was generated without a model.)")]
    return "\n".join(lines)
