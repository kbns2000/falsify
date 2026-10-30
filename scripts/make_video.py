"""Assemble the Falsify demo video from scripts/record_demo.py output.

    python scripts/make_video.py --in demo_out --out Falsify_demo_3min.mp4 [--voice en-US-GuyNeural] [--no-tts] [--no-burn]

Steps: cut each scene out of raw.webm (dead model waits are trimmed to 2 s), synthesise the narration
per scene with edge-tts (free, no key), freeze the last frame when the narration is longer than the
scene, mux, concatenate, write an .srt and burn it in, and squeeze uniformly if the total exceeds
--max-seconds (default 178). Needs ffmpeg on PATH (or `pip install imageio-ffmpeg`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

NARRATION = {
    "home": "Everyone who trades carries beliefs. When R S I drops under thirty, bitcoin bounces. Three red days mean a green one. Nobody writes them down exactly, and nobody tests them against a fair baseline. Falsify is the devil's advocate.",
    "type": "Type a belief in any language. Here, in Korean: after three down days, buy the bounce.",
    "understand": "Meta's open-weight Muse Glimmer rewrites it as one exact rule in a tiny, safe language, picks the asset and the holding period, and writes down the success criterion before any result exists. It lists every assumption. If the rule does not compile, it repairs its own mistake. It is forbidden from tuning the rule.",
    "seal": "Then the rule is sealed: hashed and chained to a public ledger. Everything you are about to see was computed after this moment. No moving the goalposts.",
    "test": "A deterministic engine replays the rule. Signal at the close, entry at the next open, fees on both sides. The model never touches a number.",
    "attack": "Now the attacks. Two thousand random portfolios with the same number of trades and the same holding period: where does the rule land? What happens at realistic fees? Does it hold in both halves of nine years? Do nearby parameters work, or only the exact ones, the signature of curve fitting? Does it travel to other coins?",
    "advocate": "Then Muse Glimmer argues against the belief. It proposes counter-tests specific to this claim, the mirror-image trade, a longer horizon, the coin you actually trade, and the engine runs them under the same rules.",
    "verdict": "The verdict is decided by the numbers, never by the model. Falsified, fragile, survived for now, or inconclusive. The model explains it in the language you wrote in: what was tested, the strongest evidence against, what held up, and what would change the verdict.",
    "second": "A second belief, in English. Notice the multiple-testing attack: it counts how many beliefs you personally have sealed on this asset. Your twentieth idea is held to a stricter bar than a stranger's first. Try enough ideas and one will look good by chance. Falsify remembers.",
    "ledger": "Every claim ever sealed stays in an append-only, hash-chained ledger that anyone can recompute. The rule can never be edited after its verdict.",
    "upload": "Bring your own C S V to test beliefs about any market.",
    "outro": "Falsify never tells you what to buy. It tells you whether your belief survived an honest attempt to break it. Open weights, open ledger, open source.",
}
WAIT_KEEP = 2.0  # seconds of each model wait to keep (shows the live status)


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("ffmpeg not found: install it or `pip install imageio-ffmpeg`")


FF = None


def ff(*args, cwd=None):
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    subprocess.run(cmd, check=True, cwd=cwd)


def duration(path: Path) -> float:
    r = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", r.stderr)
    if not m:
        raise RuntimeError("cannot read duration of %s" % path)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def has_filter(name: str) -> bool:
    r = subprocess.run([FF, "-hide_banner", "-filters"], capture_output=True, text=True)
    return re.search(r"\s%s\s" % re.escape(name), r.stdout) is not None


def tts(text: str, voice: str, out: Path):
    import edge_tts  # type: ignore

    async def go():
        await edge_tts.Communicate(text, voice, rate="+4%").save(str(out))
    asyncio.run(go())


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def chunks(text: str, max_chars: int = 84) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars and cur:
            out.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out


def main():
    global FF
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="demo_out")
    ap.add_argument("--out", default="Falsify_demo_3min.mp4")
    ap.add_argument("--voice", default="en-US-GuyNeural")
    ap.add_argument("--no-tts", action="store_true", help="silent track (dry run without network)")
    ap.add_argument("--no-burn", action="store_true", help="do not burn subtitles, sidecar .srt only")
    ap.add_argument("--max-seconds", type=float, default=178.0)
    a = ap.parse_args()
    FF = ffmpeg_bin()
    inp = Path(a.inp)
    raw = inp / "raw.webm"
    scenes = json.load(open(inp / "scenes.json"))
    work = inp / "_work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    raw_len = duration(raw)

    segs = []  # dicts: name, kind, start, end (source), vlen (after padding), narration path
    for m in scenes["marks"]:
        start, end = m["start"], min(m["end"], raw_len)
        if end - start < 0.3:
            continue
        if m["kind"] == "wait":
            end = min(end, start + WAIT_KEEP)
        segs.append({"name": m["name"], "kind": m["kind"], "start": start, "end": end})

    # narration per scene (several segments may share a name, e.g. 'second'; narrate the last one)
    last_of = {}
    for i, s in enumerate(segs):
        if s["kind"] == "scene":
            last_of[s["name"]] = i
    for name, i in last_of.items():
        text = NARRATION.get(name)
        if text and not a.no_tts:
            p = work / ("narr_%02d.mp3" % i)
            tts(text, a.voice, p)
            segs[i]["narration"] = p
            segs[i]["text"] = text
        elif text:
            segs[i]["text"] = text

    timeline, t = [], 0.0
    parts = []
    for i, s in enumerate(segs):
        vlen = s["end"] - s["start"]
        pad = 0.0
        if s.get("narration"):
            nlen = duration(s["narration"]) + 0.35
            if nlen > vlen:
                pad = nlen - vlen
        total_len = vlen + pad
        v = work / ("v_%02d.mp4" % i)
        vf = "fps=30,scale=1280:800:flags=lanczos,format=yuv420p"
        if pad > 0:
            vf += ",tpad=stop_mode=clone:stop_duration=%.3f" % pad
        ff("-ss", "%.3f" % s["start"], "-to", "%.3f" % s["end"], "-i", raw, "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", v)
        aud = work / ("a_%02d.m4a" % i)
        if s.get("narration"):
            ff("-i", s["narration"], "-af", "apad", "-t", "%.3f" % total_len, "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "128k", aud)
        else:
            ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "%.3f" % total_len, "-c:a", "aac", "-b:a", "128k", aud)
        mux = work / ("m_%02d.mp4" % i)
        ff("-i", v, "-i", aud, "-c:v", "copy", "-c:a", "copy", "-shortest", mux)
        real = duration(mux)
        timeline.append({"name": s["name"], "kind": s["kind"], "start": t, "end": t + real, "text": s.get("text")})
        t += real
        parts.append(mux)

    lst = work / "concat.txt"
    lst.write_text("".join("file '%s'\n" % p.name for p in parts), encoding="utf-8")
    joined = work / "joined.mp4"
    ff("-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", joined.name, cwd=work)
    total = duration(joined)
    factor = 1.0
    if total > a.max_seconds:
        factor = total / a.max_seconds
        print("total %.1fs > %.0fs: speeding up by %.3fx" % (total, a.max_seconds, factor))
        sped = work / "sped.mp4"
        ff("-i", joined, "-filter_complex", "[0:v]setpts=PTS/%.4f[v];[0:a]atempo=%.4f[a]" % (factor, factor), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k", sped)
        joined = sped
        total = duration(joined)
        for e in timeline:
            e["start"] /= factor; e["end"] /= factor

    # subtitles
    srt_lines, n = [], 1
    for e in timeline:
        if not e.get("text"):
            continue
        cs = chunks(e["text"])
        span = e["end"] - e["start"]
        weights = [len(c) for c in cs]
        tot = float(sum(weights)) or 1.0
        cur = e["start"]
        for c, w in zip(cs, weights):
            d = span * w / tot
            srt_lines += [str(n), "%s --> %s" % (srt_time(cur), srt_time(min(cur + d, e["end"]) - 0.05)), c, ""]
            cur += d; n += 1
    out = Path(a.out).resolve()
    srt = out.with_suffix(".srt")
    srt.write_text("\n".join(srt_lines), encoding="utf-8")
    (work / "final.srt").write_text("\n".join(srt_lines), encoding="utf-8")

    if a.no_burn or not has_filter("subtitles"):
        shutil.copy(joined, out)
        if not a.no_burn:
            print("ffmpeg has no subtitles filter: wrote sidecar .srt only")
    else:
        ff("-i", joined.name, "-vf", "subtitles=final.srt:force_style='FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=1,Shadow=0,MarginV=28'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", "final_burn.mp4", cwd=work)
        shutil.copy(work / "final_burn.mp4", out)
    json.dump({"total": duration(out), "speed_factor": factor, "timeline": timeline}, open(out.with_suffix(".timeline.json"), "w"), indent=1, ensure_ascii=False)
    print("wrote %s  (%.1fs)  + %s" % (out, duration(out), srt.name))
    if duration(out) > 180:
        print("!! longer than 3:00 — lower --max-seconds or shorten DWELL/narration")


if __name__ == "__main__":
    main()
