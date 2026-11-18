#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch-track-bench: a fair test for a tracking eye. It draws made-up scenes whose truth is KNOWN, frame by frame, with the
matching sound on the same clock, and scores the tracks an eye finds against that truth.

Things that must become tracks, each with its own way of moving:
  bird-hop    stop-go hops across the lawn, pecking between hops (small head bob), a chirp now and then
  bird-fly    straight fast flight across the top, wings beating (its height flutters 6 times a second: a real beat faster than half the frame rate aliases)
  leaf-fall   a leaf tumbling down: slow fall, side-to-side drift, its shape flickering as it turns over
  leaf-gust   a leaf blown sideways in a gust while the whole bush sways harder and the wind roars (moves WITH the scene)
  bird-cross  two birds whose paths cross (does the eye keep who is who?)
Things that must NOT become tracks (or must be marked as such):
  foliage     a bush swaying all the time (its own restless spot)
  rain        short streaks, each gone in 2-3 frames, with hiss
  cloud       the whole view darkening over a second
  turn        the camera turning: the whole view slides 40 px

  perch-track-bench.py make DIR [--fps 15] [--seconds 60] [--seed 1]
      writes DIR/frames.gray (raw 640x360 grey, 1 byte a pixel), DIR/sound.wav (16 kHz mono, same clock), DIR/truth.json
      (every thing: id, kind, should_track, and its box each frame) and DIR/scene.mp4 (to watch it; built from the same frames)
  perch-track-bench.py score DIR TRACKS.jsonl
      TRACKS.jsonl: one finished track a line with at least "path": [[t, x, y, ...], ...] (t in s from the first frame, x y its
      centre in the 640x360 view); any other fields (speed, flutter, stop-go...) are reported per matched truth so you can see
      whether they tell a leaf from a bird
"""
import json, math, os, subprocess, sys, wave
import numpy as np

W, H = 640, 360


def smooth_noise(rng, h, w, scale):
    small = rng.random((h // scale + 2, w // scale + 2))
    ys = np.linspace(0, small.shape[0] - 1.001, h); xs = np.linspace(0, small.shape[1] - 1.001, w)
    y0 = ys.astype(int); x0 = xs.astype(int); fy = (ys - y0)[:, None]; fx = (xs - x0)[None, :]
    a = small[y0][:, x0]; b = small[y0][:, x0 + 1]; c = small[y0 + 1][:, x0]; d = small[y0 + 1][:, x0 + 1]
    return a * (1 - fy) * (1 - fx) + b * (1 - fy) * fx + c * fy * (1 - fx) + d * fy * fx


def blob(img, cx, cy, w, h, value, soft=1.5):
    """an ellipse of brightness `value` with a soft edge, drawn over img"""
    x0, x1 = int(max(0, cx - w)), int(min(W, cx + w + 1)); y0, y1 = int(max(0, cy - h)), int(min(H, cy + h + 1))
    if x0 >= x1 or y0 >= y1: return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    r = np.sqrt(((xx - cx) / max(w / 2, 0.5)) ** 2 + ((yy - cy) / max(h / 2, 0.5)) ** 2)
    a = np.clip((1.0 - r) * (w / 2) / soft + 0.5, 0, 1)
    img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - a) + value * a


def make(out, fps=15, seconds=60, seed=1):
    rng = np.random.default_rng(seed); os.makedirs(out, exist_ok=True)
    n = int(fps * seconds); dt = 1.0 / fps
    # the garden: sky band, a wall, the lawn, a bush (foliage) on the left; a wide margin so a camera turn slides real scenery in
    M = 60
    base = np.zeros((H + 2 * M, W + 2 * M))
    base[:] = 120 + 40 * smooth_noise(rng, H + 2 * M, W + 2 * M, 24) - 20
    base[: M + 70] = 180 + 15 * smooth_noise(rng, M + 70, W + 2 * M, 60)                     # sky
    base[M + 70: M + 110] = 140 + 20 * smooth_noise(rng, 40, W + 2 * M, 6)                   # wall
    lawn = 90 + 30 * smooth_noise(rng, H + M - 110, W + 2 * M, 4)
    base[M + 110:] = lawn
    bush = 60 + 50 * smooth_noise(rng, 140, 160, 3)                                          # leafy texture
    things = {}                                                                              # id -> {kind, should_track, boxes}

    def note(tid, kind, should, t, cx, cy, w, h):
        th = things.setdefault(tid, {"id": tid, "kind": kind, "should_track": should, "boxes": []})
        th["boxes"].append([round(t, 3), round(cx, 1), round(cy, 1), round(w, 1), round(h, 1)])

    # sound on the same clock
    SR = 16000; snd = rng.normal(0, 0.004, int(SR * seconds))                                # quiet room tone

    def chirp(t, f0=3200, f1=4600, d=0.12, a=0.25):
        i = int(t * SR); k = np.arange(int(d * SR)); f = f0 + (f1 - f0) * k / len(k)
        s = a * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.hanning(len(k)); snd[i:i + len(s)] += s[: max(0, len(snd) - i)]

    # bird-hop: 3-13 s, hops of ~18 px every 0.5-0.9 s, each hop 0.15 s with a lift, pecks between
    hop_t, hop_x = [], 200.0; t = 3.0
    while t < 13:
        hop_t.append((t, hop_x)); t += rng.uniform(0.5, 0.9); hop_x += rng.uniform(12, 24)
    for t0, _ in hop_t[::3]: chirp(t0 + 0.05)
    # rain 30-40 s: hiss, streak births
    snd[int(30 * SR):int(40 * SR)] += rng.normal(0, 0.05, int(10 * SR))
    rain = [(rng.uniform(30, 40), rng.uniform(0, W), rng.uniform(0, H - 40)) for _ in range(400)]
    # gust 24-28 s: wind roar (low rumble)
    g = np.arange(int(4 * SR)); snd[int(24 * SR):int(28 * SR)] += 0.12 * np.sin(np.pi * g / len(g)) * np.convolve(rng.normal(0, 1, len(g)), np.ones(60) / 60, "same") * 6
    # bird-cross: two birds 50-58 s for chirps
    chirp(51.0, 2600, 3900); chirp(55.5, 2600, 3900)

    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                           "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", os.path.join(out, "scene-video.mp4")], stdin=subprocess.PIPE)
    raw = open(os.path.join(out, "frames.gray"), "wb")
    for i in range(n):
        t = i * dt
        # the camera turn 48-48.5 s: the view slides 40 px right and stays there
        pan = 40 * min(1, max(0, (t - 48) / 0.5))
        gust = 1 + 3 * max(0, math.sin(math.pi * (t - 24) / 4)) if 24 <= t <= 28 else 1
        img = base[M:M + H, M + int(pan):M + int(pan) + W].copy()
        # foliage: the bush's texture shifted by a sway that differs across it (top moves most)
        sway = 3 * gust * math.sin(2 * math.pi * 0.7 * t)
        bx0 = 30 - int(pan); c0 = max(0, bx0); c1 = bx0 + 160          # the bush's columns in this view (it slides off on a turn)
        for r in range(140):
            k = (140 - r) / 140; s = int(round(sway * k + 1.5 * k * math.sin(2 * math.pi * 1.9 * t + r * 0.15)))
            img[150 + r, c0:c1] = np.roll(bush[r], s)[c0 - bx0:]
        if i % 15 == 0: note("foliage", "foliage", False, t, 110 - pan, 220, 160, 140)
        # bird-hop
        if 3 <= t < 13:
            x = hop_t[0][1]; lift = 0
            for (t0, x0), (t1, x1) in zip(hop_t, hop_t[1:] + [(99, hop_t[-1][1])]):
                if t0 <= t < t1:
                    p = min(1, (t - t0) / 0.15); x = x0 + (x1 - x0) * p if t1 < 99 else x0; lift = 8 * math.sin(math.pi * p) if p < 1 else 0
                    peck = 2 * max(0, math.sin(2 * math.pi * 3 * (t - t0))) if p >= 1 else 0
                    break
            cx, cy = x - pan, 300 - lift + peck; blob(img, cx, cy, 14, 10, 25); blob(img, cx + 7, cy - 4 + peck, 6, 6, 25)
            note("bird-hop", "bird-hop", True, t, cx, cy, 14, 10)
        # bird-fly: 8-11 s, straight across the top at ~230 px/s, wing beat 8/s
        if 8 <= t < 11:
            cx = -20 + 230 * (t - 8) - pan; cy = 60 + 5 * (t - 8); hh = 5 + 4 * abs(math.sin(2 * math.pi * 3 * t))   # 6 beats a second: under half the 15 fps, so it can be seen (faster aliases)
            blob(img, cx, cy, 18, hh, 30); note("bird-fly", "bird-fly", True, t, cx, cy, 18, hh)
        # leaf-fall: 14-22 s, slow fall with side drift, its shape flickering as it turns (3/s)
        if 14 <= t < 22:
            cx = 420 + 25 * math.sin(2 * math.pi * 0.4 * (t - 14)) - pan; cy = 120 + 28 * (t - 14)
            ww = 4 + 7 * abs(math.cos(2 * math.pi * 1.5 * t)); blob(img, cx, cy, ww, 7, 70 + 30 * abs(math.sin(2 * math.pi * 1.5 * t)))
            note("leaf-fall", "leaf-fall", True, t, cx, cy, ww, 7)
        # leaf-gust: 24.5-27.5 s, blown sideways fast with the wind, tumbling
        if 24.5 <= t < 27.5:
            cx = 250 + 140 * (t - 24.5) - pan; cy = 200 + 10 * math.sin(2 * math.pi * 1.2 * t); ww = 4 + 6 * abs(math.cos(2 * math.pi * 2.5 * t))
            blob(img, cx, cy, ww, 6, 75); note("leaf-gust", "leaf-gust", True, t, cx, cy, ww, 6)
        # rain: streaks that live 2-3 frames
        for (t0, rx, ry) in rain:
            if t0 <= t < t0 + 0.18:
                yy = int(ry + (t - t0) * 400)
                if 0 <= yy < H - 12: img[yy:yy + 12, int(rx):int(rx) + 1] = np.minimum(255, img[yy:yy + 12, int(rx):int(rx) + 1] + 45)
        if 30 <= t < 40 and i % 15 == 0: note("rain", "rain", False, t, W / 2, H / 2, W, H)
        # cloud 42-43 s: the whole view dims by a third, then stays dim until 46, recovers by 47
        dim = 1 - 0.33 * min(1, max(0, (t - 42))) if t < 46 else 1 - 0.33 * max(0, 1 - (t - 46))
        if 42 <= t < 47 and i % 15 == 0: note("cloud", "cloud", False, t, W / 2, H / 2, W, H)
        if 48 <= t < 48.6 and i % 3 == 0: note("turn", "turn", False, t, W / 2, H / 2, W, H)
        # bird-cross: 50-58 s, two birds walking toward each other on the lawn, crossing at ~54 s
        if 50 <= t < 58:
            for tid, x0, v, y0 in (("bird-cross-a", 120, 50, 280), ("bird-cross-b", 520, -50, 290)):
                step = 6 * max(0, math.sin(2 * math.pi * 1.5 * t))
                cx, cy = x0 + v * (t - 50) - pan, y0 + (54 - t) * (1 if tid.endswith("a") else -1) * 1.2
                blob(img, cx + step * 0.2, cy, 13, 10, 20 if tid.endswith("a") else 40)
                note(tid, "bird-cross", True, t, cx, cy, 13, 10)
        img = img * dim + rng.normal(0, 2.0, img.shape)                                   # sensor grain
        f = np.clip(img, 0, 255).astype(np.uint8).tobytes(); raw.write(f); ff.stdin.write(f)
    raw.close(); ff.stdin.close(); ff.wait()
    s16 = (np.clip(snd, -1, 1) * 32000).astype(np.int16)
    with wave.open(os.path.join(out, "sound.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(s16.tobytes())
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(out, "scene-video.mp4"), "-i", os.path.join(out, "sound.wav"),
                    "-c:v", "copy", "-c:a", "aac", "-shortest", os.path.join(out, "scene.mp4")])
    os.remove(os.path.join(out, "scene-video.mp4"))
    json.dump({"fps": fps, "size": [W, H], "seconds": seconds, "seed": seed, "things": list(things.values())},
              open(os.path.join(out, "truth.json"), "w"), indent=0)
    print(f"made {n} frames {W}x{H} @ {fps} fps, {len(things)} things ({sum(t['should_track'] for t in things.values())} to track) -> {out}")


def score(out, tracks_file):
    truth = json.load(open(os.path.join(out, "truth.json")))
    tracks = [json.loads(l) for l in open(tracks_file) if l.strip()]
    real = [t for t in truth["things"] if t["should_track"]]

    def dist_at(track, box):
        t, x, y = box[:3]; best = None
        for p in track["path"]:
            if abs(p[0] - t) <= 0.1:
                d = math.hypot(p[1] - x, p[2] - y); best = d if best is None or d < best else best
        return best

    used, rows = set(), []
    for th in real:
        cover = {}                                       # track index -> boxes it was near (within 25 px)
        errs = {}
        for b in th["boxes"]:
            for k, tr in enumerate(tracks):
                d = dist_at(tr, b)
                if d is not None and d <= 25:
                    cover[k] = cover.get(k, 0) + 1; errs.setdefault(k, []).append(d)
        if not cover:
            rows.append((th["id"], "MISSED", 0, 0, None, {})); continue
        k = max(cover, key=cover.get); used.update(cover)
        share = cover[k] / len(th["boxes"]); switches = len([c for c in cover.values() if c >= 3]) - 1
        feats = {f: v for f, v in tracks[k].items() if f not in ("path", "id")}
        rows.append((th["id"], "found", round(share, 2), max(0, switches), round(sum(errs[k]) / len(errs[k]), 1), feats))
    false = [k for k in range(len(tracks)) if k not in used]
    # false tracks: say where they fall (inside the bush, in the rain, at the cloud or the turn) so the reason shows
    def when_where(tr):
        t0, t1 = tr["path"][0][0], tr["path"][-1][0]; x, y = tr["path"][0][1], tr["path"][0][2]
        why = ("rain" if 30 <= t0 < 40.2 else "cloud" if 42 <= t0 < 47.2 else "turn" if 48 <= t0 < 56.5 and not (20 <= x <= 200 and 150 <= y <= 290) else
               "foliage" if 20 <= x <= 200 and 150 <= y <= 290 else "other")
        return f"{t0:.1f}-{t1:.1f}s at {x:.0f},{y:.0f} ({why})"
    print(f"truth: {len(real)} things to track; eye: {len(tracks)} tracks")
    print("thing|result|share of its life tracked|id switches|mean path error px|the track's own fields")
    for r in rows: print("|".join(str(x) for x in r[:5]) + "|" + json.dumps(r[5]))
    found = sum(1 for r in rows if r[1] == "found")
    causes = {}
    for k in false:
        c = when_where(tracks[k]).rsplit("(", 1)[1].rstrip(")"); causes[c] = causes.get(c, 0) + 1
    print(f"found {found}/{len(real)}, false tracks {len(false)}" + (" by cause: " + ", ".join(f"{c} {n}" for c, n in sorted(causes.items(), key=lambda x: -x[1])) if false else ""))
    for k in false: print("false|" + when_where(tracks[k]))


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "make":
        kw = {}
        for flag, key, typ in (("--fps", "fps", int), ("--seconds", "seconds", int), ("--seed", "seed", int)):
            if flag in a: kw[key] = typ(a[a.index(flag) + 1])
        make(a[1], **kw)
    elif len(a) == 3 and a[0] == "score":
        score(a[1], a[2])
    else:
        sys.exit(__doc__)
