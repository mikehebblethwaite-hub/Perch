# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_read: reading words from pixels, for the parts of a screen that give no words (games, canvas drawing, remote
# desktops, old or custom-drawn apps). Here on the machine itself (Tesseract), no tokens. Used by perch-vmlook (VMs) and
# perch-look (a desktop display). Each text line comes back with its own box:
#   R x,y wxh 'text'   read from pixels: may hold mistakes
import io, re, shutil, subprocess


def available():
    return shutil.which("tesseract") is not None


def _png(im):
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def read(im, boxes, conf=55, memory=None, page=False):
    """boxes: (x, y, w, h) or (x, y, w, h, ox, oy, ow, oh) where o is the part that matters (only lines touching it are kept).
    page: a whole screen, read as lines of one column (Tesseract psm 4; psm 3 split an editor into 167 bits), not as scattered words.
    Returns [(x, y, w, h, text)] in screen pixels, top to bottom."""
    if not boxes or not available():
        return []
    from PIL import Image, ImageOps
    out = []
    for b in boxes:
        x, y, w, h = b[:4]
        ox, oy, ow, oh = b[4:] if len(b) == 8 else b
        w, h = w + min(0, x), h + min(0, y); x, y = max(0, x), max(0, y)
        w, h = min(w, im.width - x), min(h, im.height - y)
        if w < 8 or h < 8:
            continue
        # trim the box to its ink: long, mostly empty boxes (a title bar) defeat the reader; the marks alone do not
        g0 = ImageOps.grayscale(im.crop((x, y, x + w, y + h)))
        hist = g0.histogram(); bg = hist.index(max(hist))
        ink = g0.point(lambda v: 255 if abs(v - bg) > 48 else 0).getbbox()
        if not ink:
            continue                                            # nothing but background
        ix0, iy0, ix1, iy1 = max(0, ink[0] - 6), max(0, ink[1] - 4), min(w, ink[2] + 6), min(h, ink[3] + 4)
        x, y, w, h = x + ix0, y + iy0, ix1 - ix0, iy1 - iy0
        k = 3 if h < 24 else 2                                  # small screen text is read larger
        g = g0.crop((ix0, iy0, ix1, iy1)).resize((w * k, h * k), Image.LANCZOS)
        if sum(g.resize((1, 1)).getdata()) < 128:          # light words on a dark ground: turned round for the reader
            g = ImageOps.invert(g)
        g = ImageOps.expand(g, 16, fill=255)
        tsv = subprocess.run(["tesseract", "-", "-", "--psm", "4" if page else ("11" if w * h > 120000 else "6"), "-l", "eng", "tsv"], input=_png(g),
                             capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
        rows = [c for c in (r.split("\t") for r in tsv.splitlines()[1:]) if len(c) >= 12 and c[11].strip()]
        bits = " ".join(c[11].strip() for c in rows)
        if memory is not None and w <= 80 and h <= 80 and (not re.search(r"[A-Za-z0-9]{2}", bits) or len(bits.replace(" ", "")) <= 3):
            name = memory.recognise(im.crop((ox, oy, ox + ow, oy + oh)))     # no letters here: a picture seen before?
            if name:
                out.append((ox, oy, ow, oh, "[" + name + "]"))
                continue
        lines = {}
        for c in rows:
            if float(c[10]) < conf:
                continue
            key = (c[2], c[3], c[4])                            # block, paragraph, line
            bx, by, bw, bh = (int(c[6]) - 16) // k + x, (int(c[7]) - 16) // k + y, int(c[8]) // k, int(c[9]) // k
            l = lines.setdefault(key, [[], [bx, by, bx + bw, by + bh]])
            l[0].append(c[11].strip()); r = l[1]
            r[0], r[1], r[2], r[3] = min(r[0], bx), min(r[1], by), max(r[2], bx + bw), max(r[3], by + bh)
        for words, r in sorted(lines.values(), key=lambda v: (v[1][1], v[1][0])):
            if r[2] < ox or r[0] > ox + ow or r[3] < oy or r[1] > oy + oh:
                continue                                        # a line near the part that matters, not in it
            t = " ".join(words)
            if memory is not None:
                t = memory.fix(t)                               # what this screen taught the reader about its own mistakes
            if len(t) >= 2 and re.search(r"[A-Za-z0-9]{2}", t):
                out.append((r[0], r[1], r[2] - r[0], r[3] - r[1], t))
    return out


def quote(t):
    return "'" + t.replace("\\", "\\\\").replace("'", "\\'") + "'"


def not_told(found, words):
    """drops what the apps already told as words: a found line with a word box over at least half of it"""
    keep = []
    for x, y, w, h, t in found:
        cov = 0
        for wx, wy, ww, wh in words:
            ix, iy = min(x + w, wx + ww) - max(x, wx), min(y + h, wy + wh) - max(y, wy)
            if ix > 0 and iy > 0:
                cov += ix * iy
        if cov < 0.5 * w * h:
            keep.append((x, y, w, h, t))
    return keep


# ---------------- the learning error filter ----------------
# Most of a screen comes with the apps' own words (T lines): those are known to be right. Now and then their places are read
# from pixels as well and the reading is compared with the truth, so the reader learns its own mistakes on that screen's fonts:
#   junk at the ends of a line (a window's border, an icon), and words it reads wrong the same way again and again.
# R lines then get the learned fixes. One memory per screen (fonts differ), here: ~/.cache/perch/learn-SCREEN.json.
import difflib, json, os, time

LEARN_DIR = os.environ.get("PERCH_LEARN_DIR", os.path.expanduser("~/.cache/perch"))
try:                     # what any screen reader gets wrong, known before it has learned anything (in the repo)
    SEED = json.load(open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "perch_read_seed.json")))
except (OSError, ValueError):
    SEED = {"junk": [], "in_numbers": {}, "in_words": {}}
MIN_SEEN = 2            # a fix is used once it was seen at least this often...
MIN_SHARE = 0.7         # ...and was this word's answer at least this share of the times


class Memory:
    def __init__(self, screen):
        self.path = os.path.join(LEARN_DIR, "learn-" + "".join(c if c.isalnum() else "_" for c in screen) + ".json")
        try:
            d = json.load(open(self.path))
        except (OSError, ValueError):
            d = {}
        self.junk = d.get("junk", {})            # token -> times seen as junk at a line's end
        self.subs = d.get("subs", {})            # read word -> {true word: times} (a word read right counts too)
        self.icons = d.get("icons", {})          # a picture's fingerprint -> {its name: times} (named by the apps)
        self.stats = d.get("stats", {"lines": 0, "words": 0, "right_raw": 0, "right_fixed": 0, "since": time.strftime("%Y-%m-%d")})

    def save(self):
        os.makedirs(LEARN_DIR, mode=0o700, exist_ok=True)
        if len(self.subs) > 5000:                # keep it small: the words seen least go first
            for k in sorted(self.subs, key=lambda k: sum(self.subs[k].values()))[:1000]:
                del self.subs[k]
        tmp = self.path + ".tmp"
        json.dump({"junk": self.junk, "subs": self.subs, "icons": self.icons, "stats": self.stats}, open(tmp, "w"))
        os.replace(tmp, self.path)

    def fix(self, text):
        """the learned fixes on one read line"""
        t = text.split()
        while t and self._is_junk(t[0]):
            t.pop(0)
        while t and self._is_junk(t[-1]):
            t.pop()
        out = []
        for w in t:
            glued = {k for k, n in self.junk.items() if n >= MIN_SEEN} | set(SEED["junk"])
            for j in sorted((k for k in glued if len(k) <= 3 and not any(c.isalnum() for c in k)), key=len, reverse=True):
                if w.startswith(j) and len(w) > len(j) and w[len(j)].isalnum():
                    w = w[len(j):]              # junk stuck to the front of a word: "_|Invoice"
                if w.endswith(j) and len(w) > len(j) and w[-len(j) - 1].isalnum() and j not in "'\"":
                    w = w[:-len(j)]             # ...or to its end: "130*"
            s = self.subs.get(w)
            if s:
                best, n = max(s.items(), key=lambda kv: kv[1])
                if n >= MIN_SEEN and n >= MIN_SHARE * sum(s.values()):
                    w = best
            elif w not in self.subs:
                w = _seed_fix(w)                    # nothing learned about this word: the seed lessons
            out.append(w)
        return " ".join(out)

    def _is_junk(self, w):
        if w in SEED["junk"] and self.junk.get(w, 0) >= 0 and not self.subs.get(w, {}).get(w):
            return True                              # seed junk, unless this screen showed it is a real word here
        return self.junk.get(w, 0) >= MIN_SEEN and not any(c.isalnum() for c in w) or (self.junk.get(w, 0) >= 2 * MIN_SEEN and len(w) <= 2)

    def learn(self, read_text, true_text):
        """one place read from pixels against what the app said is there"""
        r, t = read_text.split(), true_text.split()
        if not r or not t:
            return
        fixed = self.fix(read_text).split()
        self.stats["lines"] += 1
        self.stats["words"] += len(t)
        self.stats["right_raw"] += _same(r, t)
        self.stats["right_fixed"] += _same(fixed, t)
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, r, t, autojunk=False).get_opcodes():
            if op == "equal":                        # read right: counted too, so a fix only wins where it is usually right
                for w in r[i1:i2]:
                    if w in self.subs or not w.isalpha():
                        self.subs.setdefault(w, {})[w] = self.subs.get(w, {}).get(w, 0) + 1
            if op == "delete" and (i1 == 0 or i2 == len(r)):
                for w in r[i1:i2]:
                    self.junk[w] = self.junk.get(w, 0) + 1
            elif op == "replace" and i2 - i1 == j2 - j1:
                for a, b in zip(r[i1:i2], t[j1:j2]):
                    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.5:
                        self.subs.setdefault(a, {})[b] = self.subs.get(a, {}).get(b, 0) + 1
            elif op == "replace" and i1 == 0 and i2 - i1 > j2 - j1:
                for w in r[i1:i1 + (i2 - i1) - (j2 - j1)]:   # extra read words at the start: junk
                    self.junk[w] = self.junk.get(w, 0) + 1
        # junk glued to the first word ("_|Invoice" for "Invoice")
        if r[0] != t[0] and r[0].endswith(t[0]) and len(r[0]) - len(t[0]) <= 3:
            j = r[0][:len(r[0]) - len(t[0])]
            if not any(c.isalnum() for c in j):
                self.junk[j] = self.junk.get(j, 0) + 1

    def learn_picture(self, crop, name):
        """the app named a place that holds no letters (an icon, a button's picture): its look is stored with the name"""
        fp = fingerprint(crop)
        if fp:
            new = fp not in self.icons
            self.icons.setdefault(fp, {})[name] = self.icons.get(fp, {}).get(name, 0) + 1
            if new and getattr(self, "_idx", None) is not None:
                for k in _pieces(fp):
                    self._idx.setdefault(k, set()).add(fp)

    # Pictures are found by an index, not by looking at every one stored: a fingerprint is cut into PIECES pieces and filed under
    # each piece. Two looks NEAR bits apart or less share at least one whole piece (pigeonhole), so only the pictures filed
    # under this look's pieces are compared: a few, however many millions are stored.
    def _index(self):
        if getattr(self, "_idx", None) is None:
            self._idx = {}
            for fp in self.icons:
                for k in _pieces(fp):
                    self._idx.setdefault(k, set()).add(fp)
        return self._idx

    def recognise(self, crop):
        """a picture seen before: its name (the nearest stored look, NEAR bits apart or less), else None"""
        fp = fingerprint(crop)
        if not fp or not self.icons:
            return None
        idx, a = self._index(), int(fp, 16)
        cands = set()
        for k in _pieces(fp):
            cands |= idx.get(k, set())
        best, d = None, NEAR + 1
        for c in cands:
            dd = bin(a ^ int(c, 16)).count("1")
            if dd < d:
                best, d = c, dd
        if best is None:
            return None
        names = self.icons[best]
        return max(names, key=names.get)

    def report(self):
        s = self.stats
        raw = s["right_raw"] / s["words"] * 100 if s["words"] else 0
        fx = s["right_fixed"] / s["words"] * 100 if s["words"] else 0
        subs = sorted(((sum(v.values()), k, max(v, key=v.get)) for k, v in self.subs.items()), reverse=True)[:8]
        junk = sorted(((n, k) for k, n in self.junk.items() if n >= MIN_SEEN), reverse=True)[:8]
        return "\n".join([f"checked against the apps' own words since {s['since']}: {s['lines']} lines, {s['words']} words",
                          f"words read right: {raw:.1f}% as read, {fx:.1f}% with what was learned before each check",
                          "junk learned: " + (", ".join(f"{k!r} x{n}" for n, k in junk) or "none yet"),
                          "fixes learned: " + (", ".join(f"{k!r}->{b!r} x{n}" for n, k, b in subs if k != b) or "none yet"),
                          f"pictures learned: {len(self.icons)} (" + ", ".join(sorted({max(v, key=v.get) for v in self.icons.values()})[:12]) + ")"])


def is_picture(said, name):
    """a short name over a place whose reading is nothing like it (no letters, or 'ol' for 'Restore'): a picture"""
    if not re.search(r"[A-Za-z0-9]{2}", said):
        return True
    return difflib.SequenceMatcher(None, said.lower(), name.lower()).ratio() < 0.4


def fits(w, h, text):
    """could these words be on the screen in that box? (a hidden name longer than its box is not what the screen shows)"""
    lines = max(1, h // 14)
    return len(text) * 5 <= w * lines


def _seed_fix(w):
    """look-alike characters, only where the rest of the word says which it is: 'O' in '44O1' is 0, '0' in 'b0iler' is o"""
    core = w.strip(".,:;!?()[]")
    if len(core) < 2:
        return w
    digits = sum(c.isdigit() for c in core); letters = sum(c.isalpha() for c in core)
    if digits >= 2 and digits > letters and letters <= 2:
        return w.replace(core, "".join(SEED["in_numbers"].get(c, c) for c in core))
    if letters >= 3 and digits == 1 and letters > 2 * digits:
        return w.replace(core, "".join(SEED["in_words"].get(c, c) for c in core))
    return w


NEAR = 16               # two looks this many bits apart (of 256) or less are the same picture
PIECES = NEAR + 1       # cut into one more piece than that: a near look always shares one whole piece


def _pieces(fp):
    bits = bin(int(fp, 16))[2:].zfill(256)
    step = -(-256 // PIECES)
    return [f"{i}:{bits[i * step:(i + 1) * step]}" for i in range(PIECES) if bits[i * step:(i + 1) * step]]


def fingerprint(crop):
    """a small picture's look as 256 bits: 16x16 grey, each dot lighter or darker than the middle (the same look at another
    place on the screen, a little lighter or darker, gives nearly the same bits)"""
    try:
        from PIL import ImageOps
        g = ImageOps.grayscale(crop).resize((16, 16))
        px = list(g.getdata()); mid = sorted(px)[len(px) // 2]
        if max(px) - min(px) < 24:
            return None                              # a flat patch: nothing to recognise
        return "%064x" % int("".join("1" if p > mid else "0" for p in px), 2)
    except Exception:
        return None


def _same(a, b):
    return sum(i2 - i1 for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op == "equal")


def calibrate(im, truths, memory, budget=8):
    """reads up to budget places the apps told as words (x, y, w, h, text) and learns from the difference"""
    picked = [t for t in truths if 10 <= t[3] <= 80 and t[2] >= 12 and 2 <= len(t[4]) <= 120 and "…" not in t[4]
              and (fits(t[2], t[3], t[4]) or (t[2] <= 80 and len(t[4].split()) <= 3))][:budget]
    for x, y, w, h, text in picked:
        got = read(im, [(x - 4, y - 3, w + 8, h + 6)], conf=0)
        said = " ".join(g[4] for g in got)
        if w <= 80 and h <= 80 and len(text.split()) <= 3 and is_picture(said, text):
            memory.learn_picture(im.crop((x, y, x + w, y + h)), text)    # a name with no letters to read: a picture
        elif got:
            memory.learn(said, text)
    if picked:
        memory.save()
    return len(picked)
