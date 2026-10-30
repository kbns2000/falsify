"""Record the Falsify demo walk-through with Playwright (one continuous screen capture).

    python scripts/record_demo.py --base http://localhost:8000 --out demo_out [--fast]

Writes  <out>/raw.webm              the full recording (dead waits included)
        <out>/scenes.json           scene / wait boundaries in seconds, for scripts/make_video.py
        <out>/shots/*.png           stills for the README

Run it against a server backed by a real model; the stub is fine for a dry run (--fast).
Set PW_CHROMIUM_PATH to use a system Chromium instead of Playwright's bundled one.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

# Dwell targets in seconds (the narration in make_video.py is written to fit these).
DWELL = {"home": 12, "type": 7, "understand": 18, "seal": 10, "test": 12, "attack": 20, "advocate": 16, "verdict": 20, "second": 20, "ledger": 13, "upload": 5, "outro": 8}
BELIEF1 = "3일 연속 떨어지면 다음날 반등하니까 그때 산다"
BELIEF2 = "When bitcoin's RSI goes under 30 it always bounces within a week"


class Rec:
    def __init__(self, page, out: Path, speed: float):
        self.page, self.out, self.speed = page, out, speed
        self.t0 = time.time()
        self.marks: list[dict] = []
        self._cur: dict | None = None
        (out / "shots").mkdir(parents=True, exist_ok=True)

    def now(self) -> float:
        return round(time.time() - self.t0, 3)

    def begin(self, name: str, kind: str = "scene"):
        self.end()
        self._cur = {"name": name, "kind": kind, "start": self.now()}
        print("%7.1fs  %s %s" % (self._cur["start"], kind, name), flush=True)

    def end(self):
        if self._cur:
            self._cur["end"] = self.now()
            self.marks.append(self._cur)
            self._cur = None

    def dwell_until(self, name: str):
        """Sleep until the current scene has lasted its DWELL target (scaled by --fast)."""
        target = DWELL[name] * self.speed
        while self._cur and self.now() - self._cur["start"] < target:
            time.sleep(0.1)

    def shot(self, name: str):
        self.page.screenshot(path=str(self.out / "shots" / (name + ".png")), full_page=False)

    def wait_stage(self, stage: str, timeout_s: float = 600):
        """Wait for a pipeline stage card to be marked done; recorded as a 'wait' segment."""
        sel = '.stage[data-stage="%s"].done' % stage
        if self.page.locator(sel).count():
            return
        self.begin("wait_" + stage, "wait")
        try:
            self.page.wait_for_selector(sel, timeout=timeout_s * 1000, state="attached")
        except PWTimeout:
            print("!! stage %s did not finish within %ss" % (stage, timeout_s), flush=True)
        self.end()

    def scroll_to(self, selector: str, block: str = "start", settle: float = 0.8):
        self.page.evaluate("([s, b]) => { const el = document.querySelector(s); if (el) el.scrollIntoView({behavior: 'smooth', block: b}); }", [selector, block])
        time.sleep(settle)

    def glide(self, px: int, seconds: float, steps: int = 40):
        """Smoothly scroll the window by px over `seconds`."""
        for _ in range(steps):
            self.page.mouse.wheel(0, px / steps)
            time.sleep(seconds / steps)


def run(args):
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    speed = 0.25 if args.fast else 1.0
    exe = args.chromium or os.environ.get("PW_CHROMIUM_PATH") or None
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--force-device-scale-factor=1"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, record_video_dir=str(out / "_video"), record_video_size={"width": 1280, "height": 800}, color_scheme="dark", locale="en-US")
        page = ctx.new_page()
        page.set_default_timeout(30000)
        r = Rec(page, out, speed)

        # 1. home
        r.begin("home")
        page.goto(args.base + "/", wait_until="networkidle")
        page.wait_for_selector("#examples .chip")
        time.sleep(1.5 * speed)
        r.shot("01_home")
        for chip in page.locator("#examples .chip").all()[:3]:
            chip.hover(); time.sleep(0.6 * speed)
        r.glide(420, 2.5 * speed); time.sleep(0.6 * speed); r.glide(-420, 1.5 * speed)
        r.dwell_until("home")

        # 2. type the first belief (Korean) and submit
        r.begin("type")
        page.click("#belief")
        page.type("#belief", args.belief1, delay=int(55 * speed) + 5)
        try:
            page.select_option("#asset-hint", args.asset1)
        except Exception:
            pass
        time.sleep(1.0 * speed)
        page.hover("#submit"); time.sleep(0.5 * speed)
        page.click("#submit")
        page.wait_for_selector("#view-run:not(.hidden), #view-run", state="visible")
        r.dwell_until("type")

        # 3. understand
        r.wait_stage("understand")
        r.begin("understand")
        r.scroll_to('.stage[data-stage="understand"]')
        time.sleep(1.2 * speed)
        det = page.locator('.stage[data-stage="understand"] details summary')
        if det.count():
            det.first.click(); time.sleep(0.8 * speed)
        r.shot("02_understand")
        r.dwell_until("understand")

        # 4. seal
        r.wait_stage("seal")
        r.begin("seal")
        r.scroll_to('.stage[data-stage="seal"]')
        r.shot("03_seal")
        r.dwell_until("seal")

        # 5. test
        r.wait_stage("test")
        r.begin("test")
        r.scroll_to('.stage[data-stage="test"]')
        r.shot("04_test")
        r.dwell_until("test")

        # 6. attack
        r.wait_stage("attack")
        r.begin("attack")
        r.scroll_to('.stage[data-stage="attack"]')
        time.sleep(1.5 * speed)
        r.shot("05_attack")
        r.glide(700, 6.0 * speed)
        dets = page.locator("#findings details summary")
        if dets.count():
            dets.first.click(); time.sleep(1.0 * speed)
        r.glide(500, 4.0 * speed)
        r.dwell_until("attack")

        # 7. devil's advocate
        r.wait_stage("advocate")
        r.begin("advocate")
        r.scroll_to('.stage[data-stage="advocate"]')
        r.shot("06_advocate")
        r.dwell_until("advocate")

        # 8. verdict + report
        r.wait_stage("verdict")
        r.begin("verdict")
        r.scroll_to('.stage[data-stage="verdict"]')
        time.sleep(1.5 * speed)
        r.shot("07_verdict")
        r.glide(500, 7.0 * speed)
        r.dwell_until("verdict")

        # 9. second belief (same asset, same searcher -> multiple-testing card)
        r.begin("second")
        page.click("text=New claim")
        page.wait_for_selector("#belief", state="visible")
        page.fill("#belief", "")
        page.type("#belief", args.belief2, delay=int(35 * speed) + 5)
        try:
            page.select_option("#asset-hint", args.asset1)
        except Exception:
            pass
        page.click("#submit")
        r.end()
        r.wait_stage("verdict", timeout_s=900)
        r.begin("second")
        r.scroll_to('.stage[data-stage="attack"]')
        mt = page.locator("#findings li:has-text('How many beliefs')")
        if mt.count():
            mt.first.scroll_into_view_if_needed(); time.sleep(0.5 * speed)
        r.shot("08_multiple_testing")
        time.sleep(4.0 * speed)
        r.scroll_to('.stage[data-stage="verdict"]')
        r.shot("09_verdict2")
        r.dwell_until("second")

        # 10. ledger
        r.begin("ledger")
        page.click("text=Ledger")
        page.wait_for_selector("#ledger-table tr", state="attached")
        time.sleep(1.5 * speed)
        r.shot("10_ledger")
        r.glide(300, 3.0 * speed)
        r.dwell_until("ledger")

        # 11. upload field
        r.begin("upload")
        page.click("text=New claim")
        page.wait_for_selector("#csv", state="attached")
        r.scroll_to("#csv", block="center")
        page.hover("#csv")
        r.dwell_until("upload")

        # 12. outro
        r.begin("outro")
        r.scroll_to("footer", block="end")
        r.shot("11_outro")
        r.dwell_until("outro")
        r.end()

        total = r.now()
        page.close()
        video_path = page.video.path()
        ctx.close(); browser.close()
    shutil.move(video_path, out / "raw.webm")
    shutil.rmtree(out / "_video", ignore_errors=True)
    json.dump({"base": args.base, "speed": speed, "total": total, "dwell": DWELL, "marks": r.marks}, open(out / "scenes.json", "w"), indent=1, ensure_ascii=False)
    print("recorded %.1fs -> %s" % (total, out / "raw.webm"))
    print("scenes: %s" % ", ".join("%s %.1f-%.1f" % (m["name"], m["start"], m["end"]) for m in r.marks))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--out", default="demo_out")
    ap.add_argument("--belief1", default=BELIEF1)
    ap.add_argument("--belief2", default=BELIEF2)
    ap.add_argument("--asset1", default="BTC/USDT")
    ap.add_argument("--chromium", default=None)
    ap.add_argument("--fast", action="store_true", help="quarter-length dwell times for a dry run")
    run(ap.parse_args())
