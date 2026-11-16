#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_loop: the trunk (main loop) marks points zoomed out and never waits; each point grows a branch (its own process) that runs
beside it; branches branch again; everything on one board on one clock. Honest: a green thing moving in green grass is seen (each
colour layer at full depth), tracked as ONE movement, its way said; branches run in parallel on the cores; a branch's new points
are examined; the real sky and shadow examiners on Mike's Stonehenge picture. Tricks: camera noise alone and a light change over
the whole picture are not movements; a crashing branch does not stop the trunk; a kind with no examiner becomes a twig, and a later examiner can be sent down it (grow)."""
import json, os, shutil, subprocess, sys, tempfile, time
from multiprocessing import Process
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import perch_loop as L

fails, n = [], 0
def check(name, got, want):
    global n; n += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

# test examiners (module level, so branch processes can run them)
def look_in(p): return [{"summary": "people in a living room"}], [{"kind": "indoors", "why": "indoor", "score": 9}, {"kind": "people", "why": "3 people", "score": 6}]
def look_out(p): return [{"summary": "a field under a blue sky"}], []
def sky_day(p): return [{"light": "day", "said": "day"}], []
def sky_cant(p): return [{"light": "can't tell", "said": "can't tell"}], []
def slow3(p): time.sleep(3); return [{"said": "slow done"}], []
def slow2(p): time.sleep(2); return [{"said": "two seconds"}], []
def fan(p): return [{"said": "fanned"}], [{"kind": "slow2", "why": f"sub {i}", "score": 3} for i in range(6)]
def boom(p): raise RuntimeError("examiner broke")
def quick(p): return [{"said": "quick"}], []
def mover(p):
    f, more = L.ex_movement({**p, "look": False}); return f, more

def video(path, frames, fps=25):
    h, w, _ = frames[0].shape
    p = subprocess.Popen(["ffmpeg", "-v", "quiet", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
                          "-c:v", "ffv1", path], stdin=subprocess.PIPE)
    for f in frames: p.stdin.write(f.astype(np.uint8).tobytes())
    p.stdin.close(); p.wait()
    return path

def grass(rng, h=240, w=320):
    return np.clip(np.stack([np.full((h, w), 60.0), np.full((h, w), 150.0), np.full((h, w), 50.0)], -1) + rng.normal(0, 2, (h, w, 3)), 0, 255)

d = tempfile.mkdtemp(prefix="perch-loop-")
try:
    # the board: many processes writing at once, every line whole
    b = L.Board(os.path.join(d, "b0"))
    def writer(k):
        bb = L.Board(b.dir)
        for i in range(300): bb.put({"type": "found", "t": i, "who": k, "pad": "x" * 300})
    ps = [Process(target=writer, args=(k,)) for k in range(8)]
    [p.start() for p in ps]; [p.join() for p in ps]
    rows = b.read()
    check("board: 8 processes x 300 lines, all whole", (len(rows), all(r["pad"] == "x" * 300 for r in rows)), (2400, True))

    rng = np.random.default_rng(5)
    # a green thing moving in green grass, left to right, 3 s
    fr = []
    for i in range(75):
        g = grass(rng); x = 20 + i * 3
        g[100:140, x:x + 40] = np.clip(np.array([70.0, 165.0, 55.0]) + rng.normal(0, 2, (40, 40, 3)), 0, 255)
        fr.append(g)
    v = video(os.path.join(d, "green.mkv"), fr)
    rows = L.Trunk(v, os.path.join(d, "b1"), examiners={"sky": quick, "shadows": quick, "movement": mover}, look=False).run()
    marks = [r for r in rows if r["type"] == "mark" and r["kind"] == "movement"]
    check("honest: a green thing moving in green grass is seen", len(marks) >= 1, True)
    check("...as ONE movement, not one per frame", len(marks), 1)
    drop = [r for r in rows if r["type"] == "drop"]
    check("...its way said: left to right", bool(drop) and drop[0]["said"].startswith("moved left to right"), True)
    f = [r for r in rows if r["type"] == "found" and r["kind"] == "movement"]
    check("...zoomed in, it differs most in green", bool(f) and "green" in f[0].get("note", ""), True)

    # two things far apart stay two
    fr = []
    for i in range(50):
        g = grass(rng); g[30:50, 20 + i * 2:40 + i * 2] = 230; g[190:210, 280 - i * 2:300 - i * 2] = 20; fr.append(g)
    rows = L.Trunk(video(os.path.join(d, "two.mkv"), fr), os.path.join(d, "b1b"), examiners={"sky": quick, "shadows": quick, "movement": quick}, look=False).run()
    drops = sorted(r["said"].split(",")[0] for r in rows if r["type"] == "drop")
    check("honest: two things far apart are two movements, each its own way", drops, ["moved left to right", "moved right to left"])

    # trick: camera noise alone
    v = video(os.path.join(d, "noise.mkv"), [grass(rng) for _ in range(40)])
    rows = L.Trunk(v, os.path.join(d, "b2"), examiners={"sky": quick, "shadows": quick, "movement": mover}, look=False).run()
    check("trick: camera noise alone is no movement", sum(1 for r in rows if r["type"] == "mark" and r["kind"] == "movement"), 0)
    # trick: the light changes over the whole picture at once
    fr = [grass(rng) for _ in range(20)] + [np.clip(grass(rng) * 1.4, 0, 255) for _ in range(20)]
    rows = L.Trunk(video(os.path.join(d, "light.mkv"), fr), os.path.join(d, "b3"), examiners={"sky": quick, "shadows": quick, "movement": mover}, look=False).run()
    check("trick: a light change over the whole picture is a new scene, not a movement",
          (sum(1 for r in rows if r["type"] == "mark" and r["kind"] == "movement"), sum(1 for r in rows if r["type"] == "mark" and r["kind"] == "sky")), (0, 2))

    # the trunk never waits: a 3 s branch on the sky, while the green thing moves
    fr = []
    for i in range(75):
        g = grass(rng); x = 20 + i * 3; g[100:140, x:x + 40] = 230; fr.append(g)
    v = video(os.path.join(d, "move.mkv"), fr)
    rows = L.Trunk(v, os.path.join(d, "b4"), examiners={"sky": slow3, "movement": quick}, look=False).run()
    sky_start = next(r["wall"] for r in rows if r["type"] == "branch" and r["kind"] == "sky")
    sky_end = next(r["wall"] for r in rows if r["type"] == "found" and r["kind"] == "sky")
    mv = next(r["wall"] for r in rows if r["type"] == "mark" and r["kind"] == "movement")
    check("honest: the trunk marked the movement while the 3 s sky branch was still running", sky_start <= mv < sky_end, True)
    check("...and the slow branch still finished", any(r.get("said") == "slow done" for r in rows), True)

    # branches branch again, and run in parallel on the cores
    one = video(os.path.join(d, "one.mkv"), [grass(rng) for _ in range(2)])
    t0 = time.time()
    rows = L.Trunk(one, os.path.join(d, "b5"), examiners={"sky": fan, "slow2": slow2}, look=False).run()
    subs = [r for r in rows if r["type"] == "mark" and r["kind"] == "slow2"]
    check("branch again: the sky branch's 6 new points are marked, with their parent", (len(subs), all(r.get("parent") == 1 for r in subs)), (6, True))
    check("...and all 6 examined", sum(1 for r in rows if r["type"] == "found" and r["kind"] == "slow2"), 6)
    if (os.cpu_count() or 1) >= 6:
        wall = time.time() - t0
        check(f"...side by side: six 2 s branches took {wall:.1f} s, not 12", wall < 6, True)

    # the sound branch beside the pictures: a bang marks a moment; the moment is zoomed into (read from the video) and looked at
    import wave
    rate = 16000; x = (rng.normal(0, 0.002, rate * 4)).astype(np.float32)
    x[int(2.5 * rate):int(2.5 * rate) + 400] += 0.8 * np.sin(np.arange(400) / 3.0).astype(np.float32)
    wav = os.path.join(d, "bang.wav")
    with wave.open(wav, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate); wf.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    silent = video(os.path.join(d, "pics.mkv"), [grass(rng) for _ in range(100)])
    av = os.path.join(d, "av.mkv")
    subprocess.run(["ffmpeg", "-v", "quiet", "-y", "-i", silent, "-i", wav, "-c:v", "copy", "-c:a", "pcm_s16le", av], check=True)
    rows = L.Trunk(av, os.path.join(d, "b5b"), examiners={"sky": quick, "shadows": quick, "movement": quick, "sound": L.ex_sound, "look": quick}, look=False).run()
    mom = [r for r in rows if r["type"] == "mark" and r["kind"] == "moment"]
    check("honest: a bang in the sound marks a moment, from the sound branch", len(mom) == 1 and abs(mom[0]["t"] - 2.5) < 0.2, True)
    check("...the moment is zoomed into and looked at", any(r["type"] == "found" and r["kind"] == "look" and r["id"] == mom[0]["id"] for r in rows) if mom else False, True)

    # branches inform each other: an indoor look puts doubt on that scene's sky and shadows, never another scene's
    rows = L.Trunk(one, os.path.join(d, "b8"), examiners={"sky": quick, "shadows": quick, "look": look_in}).run()
    check("honest: the look sees indoors: the sky reading is doubted", any(r["type"] == "doubt" and r["kind"] == "sky" for r in rows), True)
    check("...people the look saw are kept for a people examiner (a twig)", any(r["type"] == "twig" and r["kind"] == "people" for r in rows), True)
    rows = L.Trunk(one, os.path.join(d, "b8b"), examiners={"sky": quick, "shadows": quick, "look": look_in, "movement": look_in}).run()
    T = L.Trunk(one, os.path.join(d, "b8c"), examiners={"sky": quick, "look": look_in})
    T.look = True
    T.mark("look", 0.0, "a second look at the same scene", 4, picture=None)   # two looks, both saying indoors
    rows = T.run()
    sky_doubts = [r for r in rows if r["type"] == "doubt" and r["kind"] == "sky"]
    check("two indoor looks at one scene: the sky is doubted ONCE", len(sky_doubts), 1)
    rows = L.Trunk(one, os.path.join(d, "b9"), examiners={"sky": quick, "shadows": quick, "look": look_out}).run()
    check("trick: an outdoor look puts no doubt on the sky", any(r["type"] == "doubt" for r in rows), False)
    fr = [grass(rng) for _ in range(10)] + [np.clip(grass(rng) * 1.5, 0, 255) for _ in range(10)]
    T = L.Trunk(video(os.path.join(d, "cut.mkv"), fr), os.path.join(d, "b10"), examiners={"sky": quick, "shadows": quick, "look": look_in})
    rows = T.run()
    doubted = {r["id"] for r in rows if r["type"] == "doubt"}
    skies = [r for r in rows if r["type"] == "mark" and r["kind"] == "sky"]
    check("each scene's own look doubts only its own sky (2 scenes, 2 skies, each doubted once)",
          (len(skies), sorted(doubted & {s_["id"] for s_ in skies}) == sorted(s_["id"] for s_ in skies)), (2, True))
    check("the indoor words: 'living room' yes, 'outdoors' and 'mushroom field' no",
          (bool(L.INDOORS.search("a living room")), bool(L.INDOORS.search("outdoors in a park")), bool(L.INDOORS.search("a mushroom field"))), (True, False, False))

    # expectations: a confirmed sky leads to clouds (examined), weather (is a twig for a later examiner), birds (the trunk watches)
    from PIL import Image as _I, ImageDraw as _D
    def outdoor(cloud_share):
        g = grass(rng); h, w, _ = g.shape
        g[: h // 2] = [90, 150, 225]
        if cloud_share: g[: h // 2, : int(w * cloud_share)] = [215, 215, 218]
        return g
    for share, want in ((0.0, 0), (0.5, 4), (1.0, 8)):
        pth = os.path.join(d, f"sky{share}.png"); _I.fromarray(outdoor(share).astype(np.uint8)).save(pth)
        got = L.ex_clouds({"picture": pth})[0][0].get("oktas")
        check(f"clouds: {share:.0%} of the sky white -> {want}/8 (oktas)", got is not None and abs(got - want) <= 1, True)
    pth = os.path.join(d, "wall.png"); _I.fromarray(np.full((240, 320, 3), 70, np.uint8)).save(pth)
    check("trick: no open sky -> can't tell, not 'clear'", "can't tell" in L.ex_clouds({"picture": pth})[0][0]["said"], True)

    fr = []
    for i in range(50):
        g = outdoor(0.0); g[40:43, 20 + i * 5:23 + i * 5] = 20; fr.append(g)          # a small dark thing crossing the sky
    v = video(os.path.join(d, "bird.mkv"), fr)
    rows = L.Trunk(v, os.path.join(d, "b11"), examiners={"sky": sky_day, "clouds": L.ex_clouds, "movement": quick}, look=False).run()
    exp = sorted(r["kind"] for r in rows if r["type"] == "mark" and str(r.get("why", "")).startswith("expected"))
    check("honest: a confirmed sky -> clouds, weather, birds expected", exp, ["birds", "clouds", "weather"])
    check("...clouds examined (0/8 on a clear sky)", any(r["type"] == "found" and r["kind"] == "clouds" and r.get("oktas") == 0 for r in rows), True)
    check("...weather is a twig for a later examiner", any(r["type"] == "twig" and r["kind"] == "weather" for r in rows), True)
    check("...a small thing crossing the sky meets the birds watch, said as 'a bird?'", any(r["type"] == "met" and r["kind"] == "birds" and "bird?" in r["said"] for r in rows), True)
    fr = []
    for i in range(50):
        g = outdoor(0.0); g[200:203, 20 + i * 5:23 + i * 5] = 20; fr.append(g)        # the same small thing, crossing the grass
    rows = L.Trunk(video(os.path.join(d, "beetle.mkv"), fr), os.path.join(d, "b12"), examiners={"sky": sky_day, "clouds": L.ex_clouds, "movement": quick}, look=False).run()
    check("trick: a small thing crossing the GRASS is not a bird", any(r["type"] == "met" for r in rows), False)
    rows = L.Trunk(v, os.path.join(d, "b13"), examiners={"sky": sky_cant, "clouds": L.ex_clouds, "movement": quick}, look=False).run()
    check("trick: a sky that can't be told expects nothing", any(str(r.get("why", "")).startswith("expected") for r in rows), False)
    def look_in_slow(p): time.sleep(1.5); return look_in(p)
    rows = L.Trunk(v, os.path.join(d, "b14"), examiners={"sky": sky_day, "clouds": L.ex_clouds, "movement": quick, "look": look_in}).run()
    dk = {r["kind"] for r in rows if r["type"] == "doubt"}
    check("an indoor look doubts the sky AND what it led to expect", {"sky", "clouds", "weather", "birds"} <= dk or ("sky" in dk and not any(str(r.get("why", "")).startswith("expected") for r in rows)), True)

    # twigs: a later examiner is sent down them, on the same board
    def weather_now(p): return [{"said": "weather examiner grown later", "has_picture": bool(p.get("picture")) and os.path.exists(p["picture"])}], []
    bd = os.path.join(d, "b11")
    tw = [r for r in L.Board(bd).read() if r["type"] == "twig" and r["kind"] == "weather"]
    check("a twig keeps its picture, why, parent and scene", bool(tw) and all(tw[0].get(k) is not None for k in ("picture", "why", "parent", "scene")), True)
    grown = L.grow(bd, {"weather": weather_now})
    rows = L.Board(bd).read()
    check("grow: the weather twig gets its examiner later, with its picture", (len(grown), any(r["type"] == "found" and r["kind"] == "weather" and r.get("has_picture") for r in rows)), (1, True))
    check("...and is not grown twice", L.grow(bd, {"weather": weather_now}), [])

    # a crashing branch does not stop the trunk; a kind with no examiner becomes a twig
    rows = L.Trunk(one, os.path.join(d, "b6"), examiners={"sky": boom, "shadows": quick}, look=True).run()
    check("trick: a crashing branch is put on the board as an error", any(r["type"] == "error" and "examiner broke" in r["error"] for r in rows), True)
    check("...the trunk ran to the end", rows[-1]["type"], "end")
    check("a kind with no examiner yet is kept: a twig", any(r["type"] == "twig" and r["kind"] == "look" for r in rows), True)

    # real examiners on Mike's Stonehenge picture (sky + shadows; no model)
    pic = "/home/mike/Desktop/images/stonehenge/stonehenge day 2 360.jpg"
    if os.path.exists(pic):
        rows = L.Trunk(pic, os.path.join(d, "b7"), look=False).run()
        sky = next((r for r in rows if r["type"] == "found" and r["kind"] == "sky"), {})
        sh = next((r for r in rows if r["type"] == "found" and r["kind"] == "shadows"), {})
        check("real: Stonehenge picture: the sky branch says day", sky.get("light"), "day")
        check("real: ...the shadow branch says the sun is ahead", (sh.get("sun_side") or "").startswith("ahead"), True)
finally:
    shutil.rmtree(d)

print(f"{n - len(fails)}/{n} pass")
sys.exit(1 if fails else 0)
