"""One OpenAI-compatible client for all Meta model routes.

Provider resolution (first match wins):

* ``FALSIFY_LLM_PROVIDER=meta``    Meta Model API        base https://api.meta.ai/v1, key META_API_KEY, model muse-spark-1.3
* ``FALSIFY_LLM_PROVIDER=hf``      open weights via Hugging Face Inference Providers
                                   base https://router.huggingface.co/v1, key HF_TOKEN, model meta-models/Muse-Glimmer-30B
* ``FALSIFY_LLM_PROVIDER=custom``  any OpenAI-compatible endpoint (vLLM / llama.cpp serving Muse Spark Open or Muse Glimmer)
                                   LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
* ``FALSIFY_LLM_PROVIDER=offline`` deterministic stub, no network (development and tests only)

If the variable is unset the client picks ``meta`` when META_API_KEY exists, else ``hf`` when
HF_TOKEN exists, else ``offline``.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

DEFAULTS = {
    "meta": {"base_url": "https://api.meta.ai/v1", "key_env": "META_API_KEY", "model": "muse-spark-1.3", "label": "Meta Model API · Muse Spark 1.3", "open_weights": False},
    "hf": {"base_url": "https://router.huggingface.co/v1", "key_env": "HF_TOKEN", "model": "meta-models/Muse-Glimmer-30B", "label": "Open weights · Muse Glimmer 30B via Hugging Face Inference Providers", "open_weights": True},
    "custom": {"base_url": os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1"), "key_env": "LLM_API_KEY", "model": os.environ.get("LLM_MODEL", "meta-models/Muse-Glimmer-30B"), "label": "Open weights · self-hosted (%s)" % os.environ.get("LLM_MODEL", "Muse Glimmer 30B"), "open_weights": True},
}


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    log: list[dict] = field(default_factory=list)


def resolve_provider() -> str:
    p = os.environ.get("FALSIFY_LLM_PROVIDER", "").strip().lower()
    if p in ("meta", "hf", "custom", "offline"):
        return p
    if os.environ.get("META_API_KEY"):
        return "meta"
    if os.environ.get("HF_TOKEN"):
        return "hf"
    return "offline"


class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None, temperature: float = 0.2):
        self.provider = provider or resolve_provider()
        self.temperature = temperature
        self.usage = Usage()
        if self.provider == "offline":
            self.model = "offline-stub"
            self.label = "Offline stub (no model) — set HF_TOKEN or META_API_KEY for real inference"
            self.open_weights = False
            self._client = None
            return
        cfg = DEFAULTS[self.provider]
        self.model = model or os.environ.get("FALSIFY_LLM_MODEL") or cfg["model"]
        self.label = cfg["label"] if model is None and not os.environ.get("FALSIFY_LLM_MODEL") else "%s · %s" % (cfg["label"].split(" · ")[0], self.model)
        self.open_weights = cfg["open_weights"]
        key = os.environ.get(cfg["key_env"], "")
        if not key and self.provider != "custom":
            raise LLMError("%s is not set for provider %s" % (cfg["key_env"], self.provider))
        from openai import OpenAI  # imported lazily so the offline stub needs no network stack

        # Self-hosted reasoning models (Muse Glimmer on Ollama) think before answering: give them low effort,
        # extra token headroom and a longer timeout. All three are overridable by env.
        local = self.provider == "custom"
        self.reasoning_effort = os.environ.get("FALSIFY_LLM_REASONING_EFFORT", "low" if local else "").strip().lower() or None
        self.extra_tokens = int(os.environ.get("FALSIFY_LLM_EXTRA_TOKENS", "1500" if local else "0"))
        timeout = float(os.environ.get("FALSIFY_LLM_TIMEOUT", "300" if local else "120"))
        self._client = OpenAI(base_url=os.environ.get("FALSIFY_LLM_BASE_URL", cfg["base_url"]), api_key=key or "none", timeout=timeout, max_retries=0)

    # ------------------------------------------------------------------ core
    def chat(self, messages: list[dict], *, json_mode: bool = False, max_tokens: int = 1500, temperature: float | None = None, tools: list[dict] | None = None, retries: int = 2) -> dict:
        """Return {"content": str, "tool_calls": [...]} — never raises on JSON issues, only on transport failure."""
        if self._client is None:
            raise LLMError("offline provider has no chat(); use falsify.llm.stub")
        last = None
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                kwargs = dict(model=self.model, messages=messages, max_tokens=max_tokens + self.extra_tokens, temperature=self.temperature if temperature is None else temperature)
                if self.reasoning_effort:
                    kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if tools:
                    kwargs["tools"] = tools
                resp = self._client.chat.completions.create(**kwargs)
                dt = time.time() - t0
                choice = resp.choices[0]
                content = choice.message.content or ""
                calls = []
                for tc in (choice.message.tool_calls or []):
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {"_raw": tc.function.arguments}
                    calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
                u = getattr(resp, "usage", None)
                self.usage.calls += 1
                self.usage.seconds += dt
                if u:
                    self.usage.prompt_tokens += int(getattr(u, "prompt_tokens", 0) or 0)
                    self.usage.completion_tokens += int(getattr(u, "completion_tokens", 0) or 0)
                self.usage.log.append({"model": resp.model if hasattr(resp, "model") else self.model, "seconds": round(dt, 2), "prompt_tokens": getattr(u, "prompt_tokens", None) if u else None, "completion_tokens": getattr(u, "completion_tokens", None) if u else None})
                if not content.strip() and not calls and choice.finish_reason == "length" and attempt < retries:
                    # the model spent the whole budget thinking: retry once with double headroom
                    max_tokens *= 2
                    last = LLMError("empty reply (finish=length)")
                    continue
                return {"content": content, "tool_calls": calls, "finish_reason": choice.finish_reason}
            except Exception as e:  # transport / provider errors
                last = e
                msg = str(e)
                # json_object unsupported by some providers -> retry without it
                if "reasoning_effort" in msg and self.reasoning_effort:
                    self.reasoning_effort = None  # server does not know the parameter
                    continue
                if json_mode and ("response_format" in msg or "json_object" in msg or "400" in msg):
                    json_mode = False
                    continue
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise LLMError("model call failed after %d attempts: %s" % (retries + 1, str(last)[:300]))

    def usage_dict(self) -> dict:
        d = {"provider": self.provider, "model": self.model, "label": self.label, "open_weights": self.open_weights, "calls": self.usage.calls, "prompt_tokens": self.usage.prompt_tokens, "completion_tokens": self.usage.completion_tokens, "seconds": round(self.usage.seconds, 2)}
        if getattr(self, "degraded_from", None):
            d["degraded_from"] = self.degraded_from
        return d


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles fences and chatter)."""
    if not text:
        raise ValueError("empty reply")
    t = text.strip()
    # strip reasoning blocks some models emit
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start : i + 1])
    raise ValueError("unterminated JSON object in reply")
