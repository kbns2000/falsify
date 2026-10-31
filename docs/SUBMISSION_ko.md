# Meta Global AI Developer Hackathon — Falsify 제출 문안 (초안 2026-10-30 12:55 KST, 10-31 01:20 데모 문구 추가)

제출 페이지: https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit
마감: 2026-11-04 21:00 ET = **2026-11-05 11:00 KST**
저장소: https://github.com/kbns2000/falsify (제출 직전 public 전환 — 주인님 승인 뒤)
영상: (YouTube 주소, 촬영 뒤 기입)

아래 「넣을 것」은 PC가 양식에 그대로 붙여 넣습니다. 주인님은 마지막 Submit 단추만.

| 화면 칸 | 넣을 것 |
|---|---|
| Team Name* | Falsify |
| Team Members | (계정 이름 그대로, 1인) |
| Track* | Indie |
| Problem statement* | Build for Your World |
| X post link | (선택) 아래 X 글을 주인님이 올리면 그 주소. 안 올리면 비움 |
| Project Description* | 아래 ① |
| Public GitHub Repository* | https://github.com/kbns2000/falsify |
| 3-Minute Demo Video* | (YouTube 주소) |
| Describe your use of Meta products* | 아래 ② |
| Why are Meta's models important* | 아래 ③ |
| Feedback on partner technologies | 아래 ④ |
| Likelihood to build with Meta Model API* (1~5) | 4 (추천. 이유: 오픈 가중치는 실제로 썼고, API는 카드 없이 크레딧을 못 받아 못 써봄 → ④에 적음) |
| Overall opinion of Meta Model API* (1~5) | 4 (추천. 같은 이유) |
| 동의 체크 | 체크 → Submit (주인님 손) |

---

## ① Project Description

**Falsify — the devil's advocate for market beliefs.**

Every retail trader carries beliefs: "when bitcoin's RSI drops under 30 it always bounces", "three red days means a green one", "a golden cross on DOGE means a month of gains". They are almost never written down precisely, never tested against a fair baseline, and quietly re-tuned after the fact until they look right. Backtesting tools are built to make a strategy look good. Nothing in the stack is built to make a belief *fail*.

Falsify is that missing adversary. You type a belief in any language. Meta's open-weight **Muse Glimmer 30B** rewrites it as one exact rule in a tiny, safe rule language, names the asset, direction, holding period and — crucially — the **pre-registered success criterion**, and lists every assumption it had to make. The rule is then **hashed and chained to a public ledger before a single number is computed**. A deterministic engine replays it on nine years of public candles (signal at the close, entry at the next open, fees on both sides) and runs a fixed battery of attacks: 2,000 random-entry portfolios with the same holding period, fee sensitivity, first half vs second half, nearby parameters (the curve-fitting test), the same rule on other assets, a Bonferroni correction for how many beliefs *you* have already sealed, sample size and a look-ahead guard. Muse Glimmer then plays devil's advocate and proposes counter-tests specific to that belief — the mirror-image trade, a different horizon, the asset you actually trade — which the engine runs under the same rules.

The verdict — FALSIFIED, FRAGILE, SURVIVED (for now) or INCONCLUSIVE — is decided by the numbers, never by the model. The model writes the report in the user's language: what was tested, the strongest evidence against, what held up, what would change the verdict. Every sealed claim and its verdict stay in an append-only, hash-chained ledger that anyone can recompute.

Three design rules make it honest: seal before you see; the model never touches the numbers; a pass is not a forecast. Falsify never tells you what to buy. It tells you whether your belief survived an honest attempt to break it — which is the one thing a trader cannot do for themselves.

Built solo in one week: FastAPI + a deterministic pandas engine, a whitelisted expression compiler, SSE streaming of every stage, hand-drawn SVG charts, 15 tests, one-click Render deploy. Bring your own CSV to test beliefs about any market.

Live demo: https://falsify-baxc.onrender.com — the eight seeded claims and the demo video were produced with Muse Glimmer 30B running locally via Ollama. Without paid inference credits, new beliefs on the hosted demo are formalized by a clearly labelled rule-based fallback; the engine, attacks and ledger are identical. Run it locally with `ollama pull muse-glimmer:30b` for the full model loop.

## ② Describe your use of Meta products

Falsify runs on **Muse Glimmer 30B (open weights)** through one OpenAI-compatible client with three routes: Hugging Face Inference Providers (`meta-models/Muse-Glimmer-30B`) for the public demo, a self-hosted Ollama/vLLM endpoint (`muse-glimmer:30b`) for local use and for building the seed ledger, and the Meta Model API (Muse Spark) as a drop-in alternative. The model is used in three agentic roles:

1. **Formalizer** — maps free text in any language to a strict rule grammar, picks sensible defaults, surfaces assumptions, and repairs its own compile or zero-signal errors in a loop (max 4 attempts). It is explicitly forbidden from tuning the rule to improve results.
2. **Devil's advocate** — reasons about *why* a belief might be an artefact and designs up to three counter-tests the deterministic engine then runs.
3. **Reporter** — explains a statistical verdict honestly in the user's language, with the strongest evidence against listed first and no advice.

Prompts, the repair loop and the full agent trace are in `falsify/llm/`; the trace is shown on every result page so judges can see exactly what the model did. The engine, ledger and attacks are deterministic Python; the model never sees or edits a number.

## ③ Why are Meta's models important

Falsify only works if the model is strong at three things at once: multilingual instruction following into a strict grammar, self-repair from tool errors, and honest explanation of statistics without drifting into advice. Muse Glimmer 30B does all three well enough to be trusted with the *translation* step of a tool whose whole point is that nothing can be tuned after the fact.

Open weights matter here specifically. A tool that audits beliefs has to be auditable itself: anyone can pull `muse-glimmer:30b`, run the same prompts locally, and reproduce the same sealed rule from the same belief. The ledger stores the model label next to each hash. With a closed API that reproducibility promise would be weaker, and for a retail-trader audience the ability to run it on their own machine for free is the difference between a demo and a tool they will keep using.

## ④ Feedback on partner technologies

- **Hugging Face Inference Providers**: the OpenAI-compatible router made the open-weight route a one-line change. Free-tier monthly credits are tight for a public demo, so Falsify degrades to a visible "offline stub" badge when a route fails instead of silently faking a model.
- **Ollama**: `muse-glimmer:30b` needed Ollama ≥ 0.40; older installs fail the pull with HTTP 412. A clearer error message would save newcomers time.
- **Meta Model API credits**: the credit application required a credit card. For an Indie builder without one, that meant the API could not be tried at all; a card-free sandbox tier would have let me compare Muse Spark against the open weights in the same pipeline.

## (선택) X 글 — Most Viral $20K 응모용 (주인님이 올릴 때만)

Everyone who trades carries beliefs nobody has tried to break. I built Falsify for the @Meta Global AI Developer Hackathon: say what you believe about the market, Muse Glimmer turns it into one sealed rule, and a battery of attacks tries to kill it. Verdicts, not advice. (주소) #MetaAIHackathon

## 비고
- 양식 칸은 10-30 PC 1차 확인 기준 14칸. 제출 당일 PC가 다시 한 번 칸을 확인하고 바뀐 곳이 있으면 이 파일을 고침.
- 「Team Members」는 Cerebral Valley 계정 이름이 자동으로 들어가면 그대로 둠.
