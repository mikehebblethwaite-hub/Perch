# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_play: a video session a bot drives while it plays (perch-video NAME COMMAND ...). Every video is different and its sound
# varies a lot, so the bot (and the player itself) can adjust until it is clear, replay a bit, and go on with the new settings.
#   perch-video NAME play FILE|URL [--fast]  start: the video plays at its own pace (--fast: as fast as the box can go)
#   perch-video NAME read                    the lines up to where the video is now, only those not read yet; then "@ 1:02.3/2:29.1"
#   perch-video NAME back [N]                the last N seconds (default 30) again with the settings now: their lines, new
#   perch-video NAME set lang en|auto        the speech's language from here on (and for back)
#   perch-video NAME set quality base|small  the speech model (small: better on poor sound, slower)
#   perch-video NAME set speech on|off|auto  words from speech always / never / when there are no subtitles (default)
#   perch-video NAME lessons                 what this video taught the player so far (also kept for the next time it plays)
#   perch-video NAME seek T | pause | go | status | stop
# The player works ahead in 30-second pieces with perch-video itself (so its own adjusting, "! adjusted ...", applies in each);
# a change of settings drops the pieces it made ahead and makes them again. Nothing is kept on disk but the pieces in memory.
import json, os, re, socket, subprocess, sys, threading, time

PIECE, AHEAD = 30.0, 60.0


def secs(v):
    return sum(float(x) * 60 ** i for i, x in enumerate(reversed(str(v).split(":"))))


def ts(t):
    m, s = divmod(round(max(0.0, t), 1), 60)
    return f"{int(m)}:{s:04.1f}"


def run_dir(name):
    d = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "perch-play-" + name)
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def client(here, name, args):
    sock = os.path.join(run_dir(name), "sock")
    if args[0] == "play":
        if os.path.exists(sock):
            try: c = socket.socket(socket.AF_UNIX); c.connect(sock); c.sendall(b'["stop"]\n'); c.recv(100); c.close(); time.sleep(0.3)
            except OSError: pass
        log = open(os.path.join(run_dir(name), "log"), "ab")
        subprocess.Popen([sys.executable, "-I", os.path.join(here, "perch-video"), name, "--serve"] + args[1:], stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(100):
            if os.path.exists(sock): break
            time.sleep(0.1)
        args = ["status"]
    try:
        c = socket.socket(socket.AF_UNIX); c.connect(sock)
    except OSError:
        sys.exit(f"err no video session '{name}' (start one: perch-video {name} play FILE)")
    c.sendall((json.dumps(args) + "\n").encode())
    out = b""
    while True:
        d = c.recv(65536)
        if not d: break
        out += d
    sys.stdout.write(out.decode("utf-8", "replace"))
    sys.exit(1 if out.startswith(b"err") else 0)


class Player:
    def __init__(self, here, src, fast):
        self.here, self.src, self.fast = here, src, fast
        self.set = {"lang": "auto", "quality": "base", "speech": "auto"}
        self.hint = None                      # a language the player itself switched to: the next pieces try it first
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src], capture_output=True, text=True)
        self.dur = float(p.stdout.strip() or 0)
        self.head = None
        self.lines = []                       # (t, text) made so far, in time order
        self.made_to = 0.0                    # pieces made up to here
        self.told = 0.0                       # lines up to here have been read
        self.pos0, self.t0, self.playing = 0.0, time.time(), True
        self.lock = threading.Lock()
        self.gen = 0                          # bumped by a change: pieces being made for an older setting are thrown away
        self.used = time.time()
        threading.Thread(target=self.worker, daemon=True).start()

    def pos(self):
        if self.fast: return self.made_to
        return min(self.dur, self.pos0 + ((time.time() - self.t0) if self.playing else 0))

    def piece(self, a, b, extra=()):
        cmd = [os.path.join(self.here, "perch-video"), "--from", str(a), "--to", str(b), "--lang", self.set["lang"], "--quality", self.set["quality"]]
        if self.set["lang"] == "auto" and self.hint: cmd += ["--hint", self.hint]   # what an earlier piece needed (the player in charge)
        if self.set["speech"] == "on": cmd.append("--speech")
        if self.set["speech"] == "off": cmd.append("--no-sound")
        r = subprocess.run(cmd + list(extra) + [self.src], capture_output=True, text=True, timeout=3600)
        out = r.stdout.splitlines()
        head, rest = (out[0], out[1:]) if out and out[0].startswith("V ") else (None, out)
        got = []
        for l in rest:
            m = re.match(r"\S+ (\d+:\d+(?::\d+)?\.\d)", l)
            got.append((secs(m.group(1)) if m else a, l))
        if not out: got.append((a, f"! not read {ts(a)}-{ts(b)}: " + (r.stderr.strip()[:200] or "no answer")))
        return head, got

    def worker(self):
        while True:
            with self.lock:
                a, gen = self.made_to, self.gen
                ahead = a - self.pos()
            if a >= self.dur - 0.05 or (not self.fast and ahead > AHEAD):
                time.sleep(0.5); continue
            b = min(self.dur, a + PIECE)
            head, got = self.piece(a, b)
            with self.lock:
                if gen != self.gen: continue          # the settings changed while this piece was made: made again
                if head and not self.head: self.head = re.sub(r" speech:\S+", "", head)
                langs = re.search(r"speech:(\S+)", head or "")
                if langs: got.append((a, f"lang {ts(a)} {langs.group(1)}"))
                for _, l in got:
                    m = re.match(r"! adjusted \S+ lang ([a-z]{2,3})", l)
                    if m: self.hint = m.group(1)
                self.lines = [e for e in self.lines if e[0] < a] + sorted(got, key=lambda e: e[0])
                self.made_to = b

    def answer(self, args):
        self.used = time.time()
        c = args[0] if args else ""
        with self.lock:
            if c == "status":
                return (f"{self.head or 'V (being read)'}\n@ {ts(self.pos())}/{ts(self.dur)} " + ("playing" if self.playing else "paused") +
                        f" · lang {self.set['lang']} · quality {self.set['quality']} · speech {self.set['speech']} · made to {ts(self.made_to)}\n")
            if c == "lessons":
                got = [l for _, l in self.lines if l.startswith(("lesson", "! adjusted", "! from lessons"))]
                return "\n".join(got or ["! no lessons yet (they come as the pieces are made)"]) + "\n"
            if c == "read":
                now = self.pos()
                new = [l for t, l in self.lines if self.told - 1e-6 <= t <= now and not (t < self.told)]
                self.told = max(self.told, min(now, self.made_to)) + 1e-6
                more = "" if self.made_to >= now - 0.05 else f" (still making {ts(self.made_to)}-{ts(now)}: read again)"
                return "\n".join(new + [f"@ {ts(now)}/{ts(self.dur)}{more}"]) + "\n"
            if c == "set" and len(args) == 3:
                k, v = args[1], args[2]
                ok = {"lang": re.fullmatch(r"auto|[a-z]{2,3}", v), "quality": v in ("tiny", "base", "small", "medium", "large-v3"),
                      "speech": v in ("on", "off", "auto")}.get(k)
                if not ok: return "err set lang auto|en|cy|... · set quality base|small · set speech on|off|auto\n"
                self.set[k] = v; self.gen += 1
                keep = min(self.made_to, self.told)                # what was read stays; what was made ahead is made again
                self.lines = [e for e in self.lines if e[0] < keep]; self.made_to = keep
                return f"! {k} {v} from {ts(keep)} (back N: the last N seconds again with it)\n"
            if c == "back":
                n = float(args[1]) if len(args) > 1 and re.fullmatch(r"\d+(\.\d+)?", args[1]) else PIECE
                b = min(self.pos(), self.made_to) if not self.fast else self.made_to
                a = max(0.0, b - n)
                if b - a < 0.5: return f"! nothing to go back over yet (made to {ts(self.made_to)}): read first\n"
            elif c == "seek" and len(args) == 2 and re.fullmatch(r"\d+(:\d+){0,2}(\.\d+)?", args[1]):
                t = min(self.dur, secs(args[1])); self.gen += 1
                self.pos0, self.t0 = t, time.time(); self.made_to = self.told = t; self.lines = [e for e in self.lines if e[0] < t]
                return f"@ {ts(t)}/{ts(self.dur)}\n"
            elif c in ("pause", "go"):
                if c == "pause" and self.playing: self.pos0 = self.pos(); self.playing = False
                if c == "go" and not self.playing: self.t0 = time.time(); self.playing = True
                return f"@ {ts(self.pos())}/{ts(self.dur)} " + ("playing" if self.playing else "paused") + "\n"
            elif c == "stop":
                threading.Timer(0.2, lambda: os._exit(0)).start(); return "ok stopped\n"
            else:
                return "err say: read · back [N] · set lang|quality|speech V · seek T · pause · go · status · lessons · stop\n"
        # back: made again now, outside the lock (it takes a while); its lines replace the old ones for that stretch
        head, got = self.piece(a, b)
        with self.lock:
            self.lines = [e for e in self.lines if not (a <= e[0] < b)] + got; self.lines.sort(key=lambda e: e[0])
        return "\n".join([f"~ {ts(a)}-{ts(b)} again (lang {self.set['lang']}, quality {self.set['quality']})"] + [l for _, l in sorted(got, key=lambda e: e[0])]) + "\n"


def serve(here, name, args):
    fast = "--fast" in args
    src = next((x for x in args if not x.startswith("--")), None)
    if not src or not (re.match(r"^https?://\S+$", src) or os.path.isfile(src)): sys.exit("err no such video")
    P = Player(here, os.path.abspath(src) if os.path.isfile(src) else src, fast)
    sock = os.path.join(run_dir(name), "sock")
    try: os.unlink(sock)
    except OSError: pass
    s = socket.socket(socket.AF_UNIX); old = os.umask(0o077); s.bind(sock); os.umask(old); s.listen(4)
    def idle():
        while time.time() - P.used < 1800: time.sleep(30)
        os._exit(0)
    threading.Thread(target=idle, daemon=True).start()
    while True:
        c, _ = s.accept()
        def one(c=c):
            try:
                l = c.makefile().readline()
                args = json.loads(l or "[]")
                if not isinstance(args, list) or not all(isinstance(x, str) for x in args): raise ValueError
                c.sendall(P.answer(args[:6]).encode())
            except Exception as e:
                try: c.sendall(f"err {type(e).__name__}\n".encode())
                except OSError: pass
            finally:
                c.close()
        threading.Thread(target=one, daemon=True).start()
