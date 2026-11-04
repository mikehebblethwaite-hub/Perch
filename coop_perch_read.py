# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# coop_perch_read: reading words from pixels, for the parts of a screen that give no words (games, canvas drawing, remote
# desktops, old or custom-drawn apps). Here on the machine itself (Tesseract), no tokens. Used by coop-perch-vmlook (VMs) and
# coop-perch-look (a desktop display). Each text line comes back with its own box:
#   R x,y wxh 'text'   read from pixels: may hold mistakes
import io, re, shutil, subprocess


def available():
    return shutil.which("tesseract") is not None


def _png(im):
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def read(im, boxes, conf=55):
    """boxes: (x, y, w, h) or (x, y, w, h, ox, oy, ow, oh) where o is the part that matters (only lines touching it are kept).
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
        g = ImageOps.grayscale(im.crop((x, y, x + w, y + h))).resize((w * 2, h * 2), Image.LANCZOS)
        if sum(g.resize((1, 1)).getdata()) < 128:          # light words on a dark ground: turned round for the reader
            g = ImageOps.invert(g)
        g = ImageOps.expand(g, 16, fill=255)
        tsv = subprocess.run(["tesseract", "-", "-", "--psm", "11" if w * h > 120000 else "6", "-l", "eng", "tsv"], input=_png(g),
                             capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
        lines = {}
        for row in tsv.splitlines()[1:]:
            c = row.split("\t")
            if len(c) < 12 or not c[11].strip() or float(c[10]) < conf:
                continue
            k = (c[2], c[3], c[4])                              # block, paragraph, line
            bx, by, bw, bh = (int(c[6]) - 16) // 2 + x, (int(c[7]) - 16) // 2 + y, int(c[8]) // 2, int(c[9]) // 2
            l = lines.setdefault(k, [[], [bx, by, bx + bw, by + bh]])
            l[0].append(c[11].strip()); r = l[1]
            r[0], r[1], r[2], r[3] = min(r[0], bx), min(r[1], by), max(r[2], bx + bw), max(r[3], by + bh)
        for words, r in sorted(lines.values(), key=lambda v: (v[1][1], v[1][0])):
            if r[2] < ox or r[0] > ox + ow or r[3] < oy or r[1] > oy + oh:
                continue                                        # a line near the part that matters, not in it
            t = " ".join(words)
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
