import json
import os
import time

import pytest
from fastapi.testclient import TestClient

os.environ["FALSIFY_LLM_PROVIDER"] = "offline"
os.environ["FALSIFY_LEDGER_DIR"] = os.path.join(os.path.dirname(__file__), "_ledger_tmp")

from falsify import ledger  # noqa: E402
from falsify.api import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_ledger():
    import shutil
    shutil.rmtree(os.environ["FALSIFY_LEDGER_DIR"], ignore_errors=True)
    yield
    shutil.rmtree(os.environ["FALSIFY_LEDGER_DIR"], ignore_errors=True)


def test_meta_and_assets():
    c = TestClient(app)
    m = c.get("/api/meta").json()
    assert m["provider"]["offline"] is True
    assert "BTC/USDT" in m["dataset"]["assets"]
    assert c.get("/healthz").json()["ok"]


def test_claim_roundtrip_and_ledger_chain():
    c = TestClient(app)
    r = c.post("/api/claims", json={"belief": "When bitcoin's RSI goes under 30 it bounces within a week"}).json()
    cid = r["id"]
    for _ in range(100):
        res = c.get("/api/claims/%s" % cid)
        if res.status_code == 200 and "verdict" in res.json():
            break
        time.sleep(0.1)
    body = res.json()
    assert body["verdict"]["label"] in ("FALSIFIED", "FRAGILE", "SURVIVED", "INCONCLUSIVE")
    assert body["spec"]["entry"].startswith("rsi(")
    assert len(body["findings"]) >= 8
    led = c.get("/api/ledger").json()
    assert led["chain"]["ok"] and led["scoreboard"]["total"] == 1
    can = c.get("/api/ledger/%s/canonical" % cid).json()
    assert can["canonical"]["entry"] == body["spec"]["entry"]
    # SSE replay for a finished claim
    ev = c.get("/api/claims/%s/events" % cid)
    assert "event: result" in ev.text


def test_upload_csv_and_claim_on_it():
    c = TestClient(app)
    import io, csv
    import numpy as np
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
    rng = np.random.default_rng(0)
    px = 100.0
    import datetime as dt
    d0 = dt.date(2020, 1, 1)
    for i in range(500):
        o = px
        px = px * (1 + rng.normal(0, 0.02))
        w.writerow([(d0 + dt.timedelta(days=i)).isoformat(), o, max(o, px) * 1.01, min(o, px) * 0.99, px, 1000])
    up = c.post("/api/upload", files={"file": ("my.csv", buf.getvalue().encode(), "text/csv")}, data={"asset": "MYCOIN"}).json()
    assert up["id"].startswith("upload:") and up["assets"] == ["MYCOIN"]
    r = c.post("/api/claims", json={"belief": "buy after 3 red days in a row", "dataset_id": up["id"]}).json()
    for _ in range(100):
        res = c.get("/api/claims/%s" % r["id"])
        if res.status_code == 200 and "verdict" in res.json():
            break
        time.sleep(0.1)
    assert res.json()["spec"]["asset"] == "MYCOIN"
