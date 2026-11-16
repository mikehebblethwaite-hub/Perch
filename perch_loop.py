# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_loop: Perch's main loop, marker and dispatcher: a tree. The main loop is the trunk: it looks at the whole scene zoomed out,
# every tick, and never waits. What it marks (points of interest) grow branches: each point goes to the examiner that suits it
# (the sky, the shadows, a movement, a whole look, words, the sound...), as its own process running BESIDE the main loop, never
# after it. A branch may mark points of its own (it branches again). Everything lands on one board, on one clock, so the trunk
# always holds the whole scene and every branch's findings at once.
#   python3 perch_loop.py SOURCE [--board DIR] [--no-look]    SOURCE: a picture, a video, or a folder of pictures
#   python3 perch_loop.py watch DIR                           follow a board live
#   python3 perch_loop.py twigs DIR / grow DIR                list the twigs / send examiners down those that now have one
# (A twig, as in "I twigged it": the moment something is realised, kept until it can be looked into.)
# The board: DIR/board.jsonl, one JSON line per event: tick / mark / branch / found / drop / doubt / watch / met / twig / grown / where / error / end,
#   t     the scene's clock (a video's own time; 0 for a still picture)
#   wall  the machine's clock (so a branch running beside the trunk shows on the same line of time)
# Zoom: the trunk keeps the last seconds of full frames in memory; a branch gets its crop from there (no second decode).
# How many branches run at the same moment: the machine's cores (a physical bound, not a chosen cap); more wait in order of how
# much they matter. Kinds with no examiner yet become TWIGS: kept with all a later examiner needs, never dropped (Mike).
import json, math, os, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor
import perch_murmur as mm                    # Perch's own number grids (C), not numpy
from PIL import Image

LOOK_W = 160                                            # the zoomed-out look: this many pixels across
KEEP_S = 3.0                                            # full frames kept in memory for zooming in (seconds)
FRAME_W = 640                                           # full frames decoded this wide (the source's own width if smaller)
PICTURES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")


# ---- the board: one file, one clock; lines from many processes never mix (each event is ONE write to an O_APPEND file) ----

class Board:
    def __init__(self, d):
        self.dir = d
        os.makedirs(os.path.join(d, "crops"), exist_ok=True)
        self.path = os.path.join(d, "board.jsonl")
        self.t0 = time.time()

    def put(self, rec):
        rec.setdefault("wall", round(time.time(), 3))
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)

    def read(self):
        if not os.path.exists(self.path): return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]


# ---- the examiners (step 2): each takes a point and returns (findings, new points). They run in their own processes. ----

def ex_sky(p):
    import perch_sky
    r = perch_sky.read(p["picture"])
    return [{"light": r["light"], "sure": r["sure"], "said": r["said"], "clues": [c["clue"] for c in r["clues"]]}], []


def ex_shadows(p):
    import perch_sky
    r = perch_sky.shadow_reading(p["picture"])
    return [{"said": r.get("said") or r.get("error"), "sun_side": r.get("sun_side"), "sun_height_deg": r.get("sun_height_deg"),
             "shadows_meet_below_sun": r.get("shadows_meet_below_sun")}], []


def ex_look(p):
    """The whole look (the local vision model stands in until Perch's own describer): what it is; it marks what needs its own
    examination next: words to read, people, a technical second look (already done inside describe_one)."""
    import perch_describe
    info = perch_describe.describe_one(p["picture"])
    people = (info.get("people") or {}).get("count", 0)
    setting = str((info.get("scene") or {}).get("setting", "unknown"))
    found = [{"summary": info.get("summary"), "people": people, "setting": setting, "sure": info.get("sure")}]
    if info.get("kind"): found[0]["picture_kind"] = info["kind"]          # never "kind": that is the branch's own kind on the board
    if info.get("technical"): found[0]["technical"] = info["technical"]
    more = []
    if info.get("text_in_media"): more.append({"kind": "words", "where": p.get("where"), "why": "the look saw writing", "score": 5})
    if people > 0:
        more.append({"kind": "people", "where": p.get("where"), "why": f"the look saw {people} people", "score": 6})
    if INDOORS.search(setting + " " + (info.get("summary") or "")):
        more.append({"kind": "indoors", "why": f"the look sees an indoor scene ({setting})", "score": 9})
    return found, more


import re
INDOORS = re.compile(r"\b(indoors?|inside|room|living room|kitchen|bedroom|office|hall|corridor|ceiling|bathroom|classroom|shop|restaurant|pub|church interior)\b", re.I)


def ex_words(p):
    import perch_read
    if not perch_read.available(): return [{"said": "no reader on this machine (tesseract)"}], []
    im = Image.open(p["picture"]).convert("RGB")
    got = perch_read.read(im, [(0, 0, im.width, im.height)], page=True)
    return [{"words": [g[4] for g in got]}], []


def ex_movement(p):
    """A movement, zoomed in: the crop from the full frame. Its size, its colour at full depth in each layer against what was
    around it (so a green thing moving in green is still told apart), and a whole look at it when it is big enough."""
    im = mm.asarray(Image.open(p["picture"]).convert("RGB"), dtype=mm.float32)
    h, w, _ = im.shape
    mean = im.reshape(-1, 3).mean(0)
    found = {"size_px": [w, h], "colour": [round(float(v), 1) for v in mean]}
    if p.get("around"):
        diff = [round(float(a - b), 1) for a, b in zip(mean, p["around"])]
        found["against_around"] = diff
        if mm.argmax(mm.abs(diff)) == 1 and p["around"][1] >= max(p["around"][0], p["around"][2]):
            found["note"] = "it differs from its surroundings most in green: a thing moving in green"
    more = [{"kind": "look", "picture": p["picture"], "why": "a moving thing big enough to look at", "score": 4}] if min(w, h) >= 48 and p.get("look", True) else []
    return [found], more


def ex_sound(p):
    import perch_sound
    got = perch_sound.lines(p["source"])
    more = []
    for t, line in got:
        if " bang" in line:
            more.append({"kind": "moment", "t": t, "why": "a bang in the sound", "score": 6})
    return [{"lines": [l for _, l in got]}], more


def ex_clouds(p):
    """How much of the sky is cloud, in oktas (eighths, as British weather says it): the sky's own pixels above the horizon,
    blue (clear) against white or grey (cloud); stones, trees and roofs are left out. Measured, not guessed; the cloud's KIND
    (and so the weather and the place) needs a learned eye."""
    import perch_sky
    a = mm.asarray(Image.open(p["picture"]).convert("RGB"), dtype=mm.float32)
    small_a = small(a.astype(mm.uint8))
    hz = perch_sky.find_horizon(small_a) or 0.5
    top = small_a[: max(1, int(hz * small_a.shape[0]))].reshape(-1, 3)
    lum = top @ mm.array([0.2126, 0.7152, 0.0722], dtype=mm.float32)
    blue = (top[:, 2] - top[:, 0] > 25) & (top[:, 2] > 110)
    cloud = ((top.max(1) - top.min(1)) < 30) & (lum > 130) & ~blue
    seen = int(blue.sum() + cloud.sum())
    if seen < 0.05 * len(top):
        return [{"said": "can't tell: too little open sky in the picture"}], []
    okta = int(round(8 * cloud.sum() / seen))
    words = {0: "clear", 1: "a few clouds", 2: "a few clouds", 3: "scattered cloud", 4: "scattered cloud", 5: "broken cloud",
             6: "broken cloud", 7: "broken cloud", 8: "overcast"}[okta]
    return [{"oktas": okta, "said": f"{okta}/8 of the sky is cloud ({words})", "sky_seen": round(seen / len(top), 2)}], []


# what a confirmed thing leads us to expect, each to be looked for (Mike, 16 Nov 2026: "we see a sky we expect clouds weather
# birds, we identify those and we now have more clues where we are"). "watch" = the trunk looks for it itself as it goes.
EXPECT = {"sky": [("clouds", "a sky holds clouds (or none)", 5), ("weather", "a sky shows the weather", 3),
                  ("birds", "a sky has birds in it", 3)]}
TRUNK_WATCHES = {"birds"}
FROM_SKY = {"sky", "shadows", "clouds", "weather", "birds"}


EXAMINERS = {"sky": ex_sky, "clouds": ex_clouds, "shadows": ex_shadows, "look": ex_look, "words": ex_words, "movement": ex_movement,
             "sound": ex_sound}


def run_branch(board_dir, point, examiners=None):
    """A branch: one examiner on one point, in its own process. It puts its findings on the board itself."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    b = Board(board_dir)
    ex = (examiners or EXAMINERS)[point["kind"]]
    b.put({"type": "branch", "t": point["t"], "id": point["id"], "kind": point["kind"], "pid": os.getpid()})
    found, more = ex(point)
    for f in found:
        b.put({**f, "type": "found", "t": point["t"], "id": point["id"], "kind": point["kind"]})
    return {"found": found, "more": more}


# ---- the sources: frames on a clock ----

def frames_of(src):
    """(t, full frame) pairs. A picture: one frame at t=0. A video: decoded once, FRAME_W wide, at its own frame rate."""
    if src.lower().endswith(PICTURES):
        im = Image.open(src).convert("RGB")
        yield 0.0, mm.asarray(im, dtype=mm.uint8)
        return
    pr = json.loads(subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-select_streams", "v:0", src],
                                   capture_output=True, text=True).stdout or "{}")
    st = (pr.get("streams") or [{}])[0]
    W, H = int(st.get("width", 0)), int(st.get("height", 0))
    if not W: return
    num, den = (st.get("avg_frame_rate") or "25/1").split("/")
    fps = float(num) / float(den or 1) if float(num) else 25.0
    w = min(FRAME_W, W); w -= w % 2; h = int(round(H * w / W / 2)) * 2
    p = subprocess.Popen(["ffmpeg", "-v", "quiet", "-nostdin", "-i", src, "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    n, size = 0, w * h * 3
    while True:
        raw = p.stdout.read(size)
        if len(raw) < size: break
        yield n / fps, mm.frombuffer(raw, dtype=mm.uint8).reshape(h, w, 3)
        n += 1
    p.wait()


def has_sound(src):
    if src.lower().endswith(PICTURES): return False
    out = subprocess.run(["ffprobe", "-v", "quiet", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", src],
                         capture_output=True, text=True).stdout
    return bool(out.strip())


# ---- the trunk: the main loop (step 1, zoomed out) ----

def small(frame):
    h, w, _ = frame.shape
    if w <= LOOK_W: return frame.astype(mm.float32)
    return mm.asarray(Image.fromarray(frame).resize((LOOK_W, max(1, round(h * LOOK_W / w))), Image.BILINEAR), dtype=mm.float32)


def changed_regions(a, b):
    """Where the zoomed-out look changed, in each colour layer separately at full depth (a change only in green still counts).
    Returns (regions as [x0,y0,x1,y1] shares, share of the picture changed)."""
    d = mm.abs(a - b)                                     # per layer
    med = mm.median(d.reshape(-1, 3), axis=0)
    if float(med.max()) > 8: return [], 1.0               # most pixels changed: the whole picture (a cut, the light), not a movement
    noise = med + 1.0
    hit = (d > mm.maximum(6.0, 4 * noise)).any(axis=2)
    share = float(hit.mean())
    import perch_sky
    boxes = []
    h, w = hit.shape
    for pts in perch_sky._blobs(hit):
        if len(pts) < 3: continue
        ys = [q[0] for q in pts]; xs = [q[1] for q in pts]
        boxes.append([min(xs), min(ys), max(xs) + 1, max(ys) + 1])
    # one thing moving shows as its front and back edges (its middle, the same colour, does not change): join pieces whose gap
    # is no bigger than the pieces themselves
    joined = True
    while joined:
        joined = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                A, B = boxes[i], boxes[j]
                size = max(A[2] - A[0], A[3] - A[1], B[2] - B[0], B[3] - B[1])
                gx = max(0, max(A[0], B[0]) - min(A[2], B[2])); gy = max(0, max(A[1], B[1]) - min(A[3], B[3]))
                if gx <= size and gy <= size:
                    boxes[i] = [min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3])]
                    boxes.pop(j); joined = True; break
            if joined: break
    return [[x0 / w, y0 / h, x1 / w, y1 / h] for x0, y0, x1, y1 in boxes], share


def overlap(a, b, pad=0.04):
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


class Trunk:
    def __init__(self, source, board_dir, examiners=None, look=True, workers=None):
        self.src, self.board = source, Board(board_dir)
        self.ex = examiners or EXAMINERS
        self.look = look
        self.workers = workers or os.cpu_count() or 1        # the machine's cores: a physical bound
        self.pool = ProcessPoolExecutor(max_workers=self.workers)
        self.running, self.waiting = {}, []                  # future -> point; points not yet started, most important first
        self.next_id = 1
        self.tracks = {}                                     # movement id -> {"where", "path": [(t, cx, cy)], "quiet": ticks}
        self.memory = []                                     # (t, full frame): the last KEEP_S seconds, for zooming in
        self.ticks = 0
        self.scene = 0.0                                     # the clock time the current scene began
        self.marked = []                                     # every point marked so far (id, kind, t, scene)
        self.doubted = set()                                 # points already put in doubt
        self.indoor_scenes = set()                           # scenes a look said are indoors
        self.watches = {}                                    # scene -> {kind: point id} the trunk looks for itself
        self.horizon = None                                  # this scene's horizon (share of the height)
        self.where = None                                    # where we are, by elimination of what the branches found

    def mark(self, kind, t, why, score, where=None, parent=None, **extra):
        p = {"id": self.next_id, "kind": kind, "t": round(t, 3), "why": why, "score": score, "where": where, **extra}
        p.setdefault("scene", self.scene)
        if parent: p["parent"] = parent
        self.next_id += 1
        self.board.put({"type": "mark", **{k: v for k, v in p.items() if k not in ("picture", "around")}})
        if kind == "indoors":                                # branches inform each other: the look says this scene is indoors,
            self.indoor_scenes.add(p["scene"])               # so its "sky" was a ceiling or a wall, its "shadows" not the sun's,
            for q in self.marked:                            # and whatever that sky led us to expect goes with it
                if q["scene"] == p["scene"] and q["kind"] in FROM_SKY and q["id"] not in self.doubted:
                    self.doubted.add(q["id"])                # once: a second look saying the same adds nothing new
                    self.board.put({"type": "doubt", "t": q["t"], "id": q["id"], "kind": q["kind"], "by": p["id"],
                                    "said": f"the look (#{parent}) sees an indoor scene: this {q['kind']} reading is not of the sky"})
            for sc in [sc for sc, w in self.watches.items() if sc == p["scene"]]:
                self.watches.pop(sc, None)
            return p
        self.marked.append({"id": p["id"], "kind": kind, "t": p["t"], "scene": p["scene"]})
        if kind in TRUNK_WATCHES:                            # the trunk looks for it itself as it goes (birds: in its movements)
            self.watches.setdefault(p["scene"], {})[kind] = p["id"]
            self.board.put({"type": "watch", "t": p["t"], "id": p["id"], "kind": kind, "said": f"the trunk watches for {kind}"})
            return p
        if kind not in self.ex and not (kind == "moment" and "look" in self.ex) and kind not in TRUNK_WATCHES and kind != "indoors":
            self.twig(p)                                     # no examiner yet: a twig, kept for one to be sent down later
            return p
        self.waiting.append(p)
        self.waiting.sort(key=lambda q: -q["score"])
        return p

    def crop(self, t, where, name):
        """Zoom in: the full frame nearest t from memory, cut to where (with a margin), saved for the branch."""
        fr = None
        if self.memory:
            ft, fr = min(self.memory, key=lambda m: abs(m[0] - t))
            if abs(ft - t) > 0.1: fr = None                  # that moment has left memory: read it from the video itself
        if fr is None:
            if self.src.lower().endswith(PICTURES): return None
            raw = subprocess.run(["ffmpeg", "-v", "quiet", "-nostdin", "-ss", f"{t:.3f}", "-i", self.src, "-frames:v", "1",
                                  "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
            if not raw: return None
            import io
            fr = mm.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
        h, w, _ = fr.shape
        if where:
            x0, y0, x1, y1 = where
            mx, my = (x1 - x0) * 0.25 + 0.01, (y1 - y0) * 0.25 + 0.01
            x0, y0, x1, y1 = max(0, x0 - mx), max(0, y0 - my), min(1, x1 + mx), min(1, y1 + my)
            fr = fr[int(y0 * h):max(int(y0 * h) + 1, int(y1 * h)), int(x0 * w):max(int(x0 * w) + 1, int(x1 * w))]
        path = os.path.join(self.board.dir, "crops", name)
        Image.fromarray(fr).save(path)
        return path

    def twig(self, p):
        """A twig: a point no examiner can take yet (weather, people, animals ...). It keeps all a later examiner needs: when,
        where, why, its parent and scene, and its own picture (the crop, or the scene's picture). `grow` sends examiners down."""
        pic = p.get("picture")
        if not pic or not os.path.exists(pic):
            pic = self.crop(p["t"], p.get("where"), f"{p['id']}-twig.png") if (self.memory or not self.src.lower().endswith(PICTURES)) else None
            if pic is None and self.src.lower().endswith(PICTURES): pic = self.src
        self.board.put({"type": "twig", "t": p["t"], "id": p["id"], "kind": p["kind"], "where": p.get("where"), "why": p.get("why"),
                        "parent": p.get("parent"), "scene": p.get("scene"), "picture": pic, "source": self.src,
                        "said": f"a twig: {p['kind']} waits for its examiner"})

    def report(self, p, found):
        """A branch (or a twig's agent) reports what a thing IS; the trunk narrows where we are by elimination (perch_where)."""
        import perch_where
        new = False
        for f in found:
            if f.get("is") and p["id"] not in self.doubted:
                if self.where is None: self.where = perch_where.Where()
                new = self.where.add(f["is"], f.get("sure", 5), by=p["id"]) or new
        if new:
            self.board.put({"type": "where", "t": p["t"], "by": p["id"], **self.where.answer()})

    def expect(self, p, found):
        """A branch confirmed something: mark what it leads us to expect. Not from a doubted point, an indoor scene, or a reading
        that could not tell."""
        if p["kind"] not in EXPECT or p["id"] in self.doubted or p.get("scene") in self.indoor_scenes: return
        if not found or found[0].get("light") in (None, "can't tell", "night or indoors"): return
        for kind, why, score in EXPECT[p["kind"]]:
            extra = {"picture": p.get("picture")} if kind in self.ex else {}
            self.mark(kind, p["t"], f"expected: {why}", score, parent=p["id"], scene=p.get("scene", self.scene), **extra)

    def steer(self):
        """Never waits: collect the branches that are done (their new points are marked), start waiting ones on free cores."""
        for fut in [f for f in self.running if f.done()]:
            p = self.running.pop(fut)
            try:
                res = fut.result() or {}
                self.expect(p, res.get("found") or [])
                self.report(p, res.get("found") or [])
                for q in res.get("more") or []:
                    extra = {k: v for k, v in q.items() if k not in ("kind", "t", "why", "score", "where")}
                    t = q.get("t", p["t"])
                    if "picture" not in extra and q["kind"] in self.ex and q["kind"] != "moment":
                        extra["picture"] = p.get("picture")
                    extra.setdefault("scene", p.get("scene", self.scene))
                    self.mark(q["kind"], t, q.get("why", ""), q.get("score", 3), q.get("where"), parent=p["id"], **extra)
            except Exception as e:
                self.board.put({"type": "error", "t": p["t"], "id": p["id"], "kind": p["kind"], "error": f"{type(e).__name__}: {e}"})
        while self.waiting and len(self.running) < self.workers:
            p = self.waiting.pop(0)
            if p["kind"] == "moment":                       # a moment marked from the sound: zoom into that time in the video
                p["picture"] = self.crop(p["t"], None, f"{p['id']}-moment.png") or p.get("picture")
                p["kind"] = "look"
                if "look" not in self.ex: continue
            self.running[self.pool.submit(run_branch, self.board.dir, p, None if self.ex is EXAMINERS else self.ex)] = p

    def scene_marks(self, t, frame):
        """A new scene (the first frame, or a cut): the whole-scene points."""
        self.scene = round(t, 3)
        path = self.crop(t, None, f"scene-{t:.2f}.png")
        self.mark("sky", t, "a new scene: read the sky", 5, picture=path)
        import perch_sky
        a = small(frame)
        hz = perch_sky.find_horizon(a)
        self.horizon = hz
        sh = perch_sky.find_shadows(a, hz) if hz else None
        if sh and sh.get("pieces"):
            self.mark("shadows", t, f"{len(sh['pieces'])} shadow pieces on the ground", 5, picture=path)
        if self.look:
            self.mark("look", t, "a new scene: the whole look", 4, picture=path)

    def tick(self, t, frame, prev):
        self.ticks += 1
        self.memory.append((t, frame))
        while self.memory and t - self.memory[0][0] > KEEP_S: self.memory.pop(0)
        a = small(frame)
        if prev is None:
            self.scene_marks(t, frame)
        else:
            regs, share = changed_regions(a, prev)
            if share > 0.5:                                  # most of the picture changed at once: a cut or a light change
                self.board.put({"type": "tick", "t": round(t, 3), "said": f"{share:.0%} of the picture changed: a new scene"})
                self.tracks.clear()
                self.scene_marks(t, frame)
            else:
                seen = set()
                for r in regs:
                    tid = next((k for k, v in self.tracks.items() if overlap(v["where"], r)), None)
                    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
                    if tid is None:
                        around = a[max(0, int(r[1] * a.shape[0]) - 3):int(r[3] * a.shape[0]) + 3,
                                   max(0, int(r[0] * a.shape[1]) - 3):int(r[2] * a.shape[1]) + 3].reshape(-1, 3)
                        p = self.mark("movement", t, "something moved", 6, [round(v, 3) for v in r],
                                      picture=self.crop(t, r, f"{self.next_id}-move.png"),
                                      around=[round(float(v), 1) for v in prev.reshape(-1, 3).mean(0)] if around.size == 0 else
                                             [round(float(v), 1) for v in mm.median(prev.reshape(-1, 3), axis=0)], look=self.look)
                        tid = p["id"]
                        self.tracks[tid] = {"where": r, "path": [], "quiet": 0}
                    tr = self.tracks[tid]
                    tr["where"], tr["quiet"] = r, 0
                    tr["size"] = max(tr.get("size", 0), (r[2] - r[0]) * (r[3] - r[1]))
                    tr["path"].append((round(t, 3), round(cx, 3), round(cy, 3)))
                    seen.add(tid)
                for tid in [k for k in self.tracks if k not in seen]:
                    tr = self.tracks[tid]; tr["quiet"] += 1
                    if tr["quiet"] >= 3: self.drop(tid, t)
        if self.ticks % 25 == 0 or prev is None:
            self.board.put({"type": "tick", "t": round(t, 3), "ticks": self.ticks, "running": len(self.running), "waiting": len(self.waiting)})
        self.steer()
        return a

    def drop(self, tid, t):
        tr = self.tracks.pop(tid)
        path = tr["path"]
        said = "still"
        if len(path) >= 2:
            dx, dy = path[-1][1] - path[0][1], path[-1][2] - path[0][2]
            secs = max(1e-6, path[-1][0] - path[0][0])
            way = []
            if abs(dx) > 0.03: way.append("left to right" if dx > 0 else "right to left")
            if abs(dy) > 0.03: way.append("down the picture (towards)" if dy > 0 else "up the picture (away)")
            said = (" and ".join(way) or "on the spot") + f", {math.hypot(dx, dy) / secs:.2f} picture-widths a second"
        self.board.put({"type": "drop", "t": round(t, 3), "id": tid, "kind": "movement", "said": f"moved {said}", "path": path[:200]})
        w = self.watches.get(self.scene, {})
        if "birds" in w and self.horizon and len(path) >= 3 and tr.get("size", 1) < 0.01 \
                and all(cy < self.horizon - 0.02 for _, _, cy in path):
            self.board.put({"type": "met", "t": round(path[0][0], 3), "id": w["birds"], "kind": "birds", "by": tid,
                            "said": f"a small thing moved {said.split(',')[0]} across the sky: a bird? (or a plane, an insect near the lens)"})

    def run(self):
        self.board.put({"type": "start", "t": 0, "source": os.path.basename(self.src), "cores": self.workers})
        if has_sound(self.src):
            self.mark("sound", 0.0, "the sound, beside the pictures", 5, source=self.src)
        prev = None
        for t, frame in frames_of(self.src):
            prev = self.tick(t, frame, prev)
        end = self.memory[-1][0] if self.memory else 0.0
        for tid in list(self.tracks): self.drop(tid, end)
        while self.running or self.waiting:                  # the source ended: let the branches finish (a live source never ends)
            self.steer()
            time.sleep(0.05)
        self.pool.shutdown()
        self.board.put({"type": "end", "t": round(end, 3), "ticks": self.ticks})
        return self.board.read()


def grow(board_dir, examiners=None):
    """Send examiners down the twigs: every twig whose kind has an examiner now (and that is not grown or doubted yet) is
    examined as a branch; its findings go on the same board, under the twig's id."""
    ex = examiners or EXAMINERS
    b = Board(board_dir)
    rows = b.read()
    done = {r["id"] for r in rows if r["type"] in ("grown", "doubt")}
    out = []
    for r in rows:
        if r["type"] != "twig" or r["id"] in done or r["kind"] not in ex: continue
        res = run_branch(board_dir, {"id": r["id"], "kind": r["kind"], "t": r["t"], "where": r.get("where"), "picture": r.get("picture"),
                                     "source": r.get("source"), "scene": r.get("scene")}, None if ex is EXAMINERS else ex)
        b.put({"type": "grown", "t": r["t"], "id": r["id"], "kind": r["kind"], "new_points": len(res.get("more") or [])})
        out.append(r["id"])
    if out:                                              # what the twigs' agents found joins what the board already knew
        import perch_where
        w = perch_where.Where()
        doubted = {r["id"] for r in b.read() if r["type"] == "doubt"}
        for r in b.read():
            if r["type"] == "found" and r.get("is") and r["id"] not in doubted: w.add(r["is"], r.get("sure", 5), by=r["id"])
        if w.seen: b.put({"type": "where", "t": 0, "by": "grow", **w.answer()})
    return out


def line(r):
    """A board event as one short line (watch)."""
    k = r.get("type")
    head = f"{r.get('t', 0):7.2f} {k:6} " + (f"#{r['id']} {r.get('kind', '')}" if "id" in r else "")
    rest = {x: v for x, v in r.items() if x not in ("type", "t", "id", "kind", "wall", "pid")}
    if "said" in rest: return head + " " + str(rest["said"])[:200]
    return head + " " + json.dumps(rest, ensure_ascii=False)[:200]


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["watch"] and len(a) == 2:
        path, pos = os.path.join(a[1], "board.jsonl"), 0
        while True:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    f.seek(pos)
                    for l in f:
                        if l.endswith("\n"): print(line(json.loads(l)), flush=True)
                    pos = f.tell()
            time.sleep(0.3)
    elif a[:1] == ["twigs"] and len(a) == 2:
        rows = Board(a[1]).read(); done = {r["id"] for r in rows if r["type"] in ("grown", "doubt")}
        for r in rows:
            if r["type"] == "twig": print(("grown " if r["id"] in done else "open  ") + line(r))
    elif a[:1] == ["grow"] and len(a) == 2:
        print("grown:", grow(a[1]))
    elif a:
        src = a[0]
        board = a[a.index("--board") + 1] if "--board" in a else os.path.join("/dev/shm", "perch-board-" + str(os.getpid()))
        srcs = [os.path.join(src, n) for n in sorted(os.listdir(src)) if n.lower().endswith(PICTURES)] if os.path.isdir(src) else [src]
        for s in srcs:
            d = board if len(srcs) == 1 else os.path.join(board, os.path.basename(s))
            for r in Trunk(s, d, look="--no-look" not in a).run():
                print(line(r))
        print("board:", board)
    else:
        sys.exit("perch_loop.py SOURCE [--board DIR] [--no-look] | watch DIR")
