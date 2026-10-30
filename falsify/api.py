"""FastAPI app: JSON API + Server-Sent Events progress stream + static UI."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import uuid
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, ledger, pipeline
from .engine import data
from .engine.dsl import grammar_text
from .llm.client import LLM, LLMError, resolve_provider

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")

app = FastAPI(title="Falsify", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
MAX_CONCURRENT = int(os.environ.get("FALSIFY_MAX_CONCURRENT", "2"))
_sem = threading.Semaphore(MAX_CONCURRENT)


class ClaimIn(BaseModel):
    belief: str = Field(..., min_length=3, max_length=2000)
    dataset_id: str = data.DEFAULT_DATASET
    asset_hint: Optional[str] = Field(None, max_length=40)
    searcher: Optional[str] = Field(None, max_length=64, description="anonymous per-browser id used for the multiple-testing correction")


@app.on_event("startup")
def _startup():
    n = ledger.seed_if_empty()
    if n:
        print("ledger seeded with %d claims" % n)


def _provider_info() -> dict:
    p = resolve_provider()
    try:
        llm = LLM(p)
        return {"provider": p, "label": llm.label, "model": llm.model, "open_weights": llm.open_weights, "offline": p == "offline"}
    except LLMError as e:
        return {"provider": p, "label": "Offline stub — model not configured yet (%s)" % e, "model": None, "open_weights": False, "offline": True}


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__, "provider": resolve_provider()}


@app.get("/api/meta")
def meta():
    info = data.builtin_info()
    return {"version": __version__, "provider": _provider_info(), "dataset": info.__dict__, "datasets": [d.__dict__ for d in data.list_builtins()], "grammar": grammar_text(), "scoreboard": ledger.scoreboard(), "chain": ledger.verify_chain()}


@app.get("/api/assets")
def assets(dataset_id: str = data.DEFAULT_DATASET):
    try:
        return {"assets": data.list_assets(dataset_id)}
    except data.DataError as e:
        raise HTTPException(404, str(e))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), asset: Optional[str] = Form(None)):
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, "CSV larger than 25 MB")
    uid = uuid.uuid4().hex[:12]
    try:
        info = data.save_upload(content, uid, asset_label=(asset or "").strip() or None)
    except data.DataError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, "Could not read CSV: %s" % str(e)[:200])
    return info.__dict__


@app.post("/api/claims")
def create_claim(body: ClaimIn):
    claim_id = ledger.new_id()
    q: "queue.Queue[dict]" = queue.Queue()
    job = {"id": claim_id, "queue": q, "events": [], "done": False, "result": None, "created": time.time()}
    with _jobs_lock:
        # drop finished jobs older than an hour
        for k in [k for k, v in _jobs.items() if v["done"] and time.time() - v["created"] > 3600]:
            _jobs.pop(k, None)
        _jobs[claim_id] = job

    def emit(kind: str, payload: dict):
        ev = {"kind": kind, "payload": payload, "t": time.time()}
        job["events"].append(ev)
        q.put(ev)

    def work():
        with _sem:
            try:
                res = pipeline.run_claim(body.belief, dataset_id=body.dataset_id, asset_hint=body.asset_hint, emit=emit, claim_id=claim_id, searcher=(body.searcher or "")[:64] or None)
                job["result"] = res
            except Exception as e:  # pragma: no cover
                job["result"] = {"id": claim_id, "error": {"kind": "crash", "message": str(e)[:400]}}
                emit("error", {"message": str(e)[:400]})
            finally:
                job["done"] = True
                q.put({"kind": "__end__", "payload": {}, "t": time.time()})

    threading.Thread(target=work, daemon=True).start()
    return {"id": claim_id, "events_url": "/api/claims/%s/events" % claim_id, "result_url": "/api/claims/%s" % claim_id}


@app.get("/api/claims/{claim_id}/events")
async def claim_events(claim_id: str):
    job = _jobs.get(claim_id)
    if job is None:
        res = ledger.load_result(claim_id)
        if res is None:
            raise HTTPException(404, "unknown claim")

        async def replay():
            yield _sse("result", res)
            yield _sse("__end__", {})

        return StreamingResponse(replay(), media_type="text/event-stream")

    async def gen():
        sent = 0
        # replay anything already emitted, then stream
        while True:
            events = job["events"]
            while sent < len(events):
                ev = events[sent]
                sent += 1
                yield _sse(ev["kind"], ev["payload"])
            if job["done"] and sent >= len(job["events"]):
                yield _sse("result", job["result"] or {})
                yield _sse("__end__", {})
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(kind: str, payload: dict) -> str:
    return "event: %s\ndata: %s\n\n" % (kind, json.dumps(payload, ensure_ascii=False, default=str))


@app.get("/api/claims/{claim_id}")
def claim_result(claim_id: str):
    job = _jobs.get(claim_id)
    if job and job["result"] is not None:
        return job["result"]
    res = ledger.load_result(claim_id)
    if res is None:
        if job:
            return JSONResponse({"id": claim_id, "pending": True}, status_code=202)
        raise HTTPException(404, "unknown claim")
    return res


@app.get("/api/ledger")
def get_ledger(limit: int = 100):
    es = ledger.entries()
    es = [{k: v for k, v in e.items() if k not in ("canonical", "searcher")} for e in es]
    return {"entries": list(reversed(es))[:limit], "scoreboard": ledger.scoreboard(), "chain": ledger.verify_chain()}


@app.get("/api/ledger/{claim_id}/canonical")
def ledger_canonical(claim_id: str):
    for e in ledger.entries():
        if e["id"] == claim_id:
            return {"id": claim_id, "spec_hash": e["spec_hash"], "prev_hash": e["prev_hash"], "sealed_at": e["sealed_at"], "canonical": json.loads(e["canonical"])}
    raise HTTPException(404, "unknown claim")


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


@app.get("/claim/{claim_id}")
def claim_page(claim_id: str):
    return FileResponse(os.path.join(WEB, "index.html"))


@app.get("/ledger")
def ledger_page():
    return FileResponse(os.path.join(WEB, "index.html"))


app.mount("/static", StaticFiles(directory=WEB), name="static")
