#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_murmur against numpy as the REFEREE: the same random grids through both, every answer compared (numpy is only the referee
here, never in Perch's running code). Then numpy's known faults, which murmur must refuse in plain words (silent wraparound, silent
float cut, the silent outer grid, writing to a copy, the ambiguous yes/no, the mean of nothing), and json taking murmur's numbers.
Last, a speed table on picture-sized grids."""
import json, math, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import perch_murmur as mm

fails, n = [], 0
def check(name, ok, detail=""):
    global n; n += 1
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok: fails.append(name)

def same(name, got, want, tol=1e-5):
    if isinstance(got, list) and isinstance(want, list) and len(got) == len(want) and \
            any(isinstance(v, (list, mm.Grid)) for v in got):           # a list of parts of different lengths: each part in turn
        bad = [i for i, (gv, wv) in enumerate(zip(got, want)) if not _close(gv, wv, tol)]
        check(name, not bad, f"parts {bad} differ"); return
    g = np.asarray(got.tolist() if isinstance(got, mm.Grid) else got, dtype=float)
    w = np.asarray(want, dtype=float)
    ok = g.shape == w.shape and np.allclose(g, w, rtol=tol, atol=tol * 10, equal_nan=True)
    check(name, ok, f"shape {g.shape} vs {w.shape}" if g.shape != w.shape else f"max diff {np.nanmax(np.abs(g - w)) if g.size else 0}")

def _close(got, want, tol):
    g = np.asarray(got.tolist() if isinstance(got, mm.Grid) else got, dtype=float); w = np.asarray(want, dtype=float)
    return g.shape == w.shape and np.allclose(g, w, rtol=tol, atol=tol * 10, equal_nan=True)

def refused(name, fn, words):
    try:
        fn(); check(name, False, "not refused")
    except mm.MurmurError as e:
        check(name, words in str(e), f"refused, but said: {e}")

rng = np.random.default_rng(11)
A = rng.uniform(0, 255, (37, 53, 3)).astype(np.float32)
B = rng.uniform(-3, 3, (37, 53)).astype(np.float64)
U = rng.integers(0, 256, (29, 31, 3)).astype(np.uint8)
M = rng.random((37, 53)) > 0.6
a, b, u, m = mm.asarray(A.tolist(), mm.float32), mm.asarray(B.tolist()), mm.asarray(U.tolist(), mm.uint8), mm.asarray(M.tolist())

# making and shapes
same("array from lists", a, A); same("uint8 grid", u, U)
same("reshape(-1, 3)", a.reshape(-1, 3), A.reshape(-1, 3)); same("transpose", a.transpose(2, 0, 1), A.transpose(2, 0, 1))
same("slices with steps", a[3:30:2, ::-3, 1], A[3:30:2, ::-3, 1]); same("ellipsis", a[..., 2], A[..., 2])
same("negative index", a[-1, -2], A[-1, -2]); same("None (new axis)", b[:, None, :], B[:, None, :])
same("zeros/full/arange/linspace", mm.full((2, 3), 7.5) + mm.arange(3) + mm.linspace(0, 1, 3),
     np.full((2, 3), 7.5) + np.arange(3) + np.linspace(0, 1, 3))
same("ogrid outer (meant)", (lambda y, x: (y - 3) ** 2 + (x - 4) ** 2)(*mm.ogrid[:7, :9]), (lambda y, x: (y - 3) ** 2 + (x - 4) ** 2)(*np.ogrid[:7, :9]))

# maths and broadcasting
same("+ - * / with scalars", (a * 2 - 1) / 3 + 0.5, (A * 2 - 1) / 3 + 0.5)
same("grid ** 2", b ** 2, B ** 2); same("abs, sqrt, log10, exp", mm.exp(mm.log10(mm.sqrt(mm.abs(b)) + 1)), np.exp(np.log10(np.sqrt(np.abs(B)) + 1)))
same("(H,W,3) minus (3,)", a - mm.array([1.0, 2.0, 3.0]), A - np.array([1.0, 2.0, 3.0]))
same("keepdims broadcast", a - a.mean(2, keepdims=True), A - A.mean(2, keepdims=True), 1e-4)
same("@ colour weights (luminance)", a @ mm.array([0.2126, 0.7152, 0.0722], mm.float32), A @ np.array([0.2126, 0.7152, 0.0722], np.float32), 1e-4)
P = rng.normal(0, 1, (40, 2)); p = mm.asarray(P.tolist())
same("Q.T @ Q (2x2)", (p - p.mean(0)).T @ (p - p.mean(0)), (P - P.mean(0)).T @ (P - P.mean(0)))
same("maximum, minimum, clip", mm.clip(mm.maximum(b, -1.0) * mm.minimum(b, 1.0), -0.5, 0.5), np.clip(np.maximum(B, -1.0) * np.minimum(B, 1.0), -0.5, 0.5))
same("comparisons and & | ~", ((b > 0) & ~(b > 2)) | (b < -2.5), ((B > 0) & ~(B > 2)) | (B < -2.5))
same("where", mm.where(b > 0, b, 0.0), np.where(B > 0, B, 0.0))
same("diff along each axis", mm.abs(mm.diff(b, axis=0)).mean() + mm.abs(mm.diff(b, axis=1)).mean(), np.abs(np.diff(B, axis=0)).mean() + np.abs(np.diff(B, axis=1)).mean())

# along an axis
for f in ("sum", "mean", "max", "min"):
    same(f"{f}() all", getattr(a, f)(), getattr(A, f)(), 1e-4)
    for ax in (0, 1, 2):
        same(f"{f}(axis={ax})", getattr(a, f)(ax), getattr(A, f)(ax), 1e-4)
same("argmax/argmin", [a.argmax(), a.argmin(), mm.argmax(b[3]), mm.argmin(b[:, 4])], [A.argmax(), A.argmin(), np.argmax(B[3]), np.argmin(B[:, 4])])
same("any by axis", m.any(0), M.any(0)); same("all by axis", m.all(1), M.all(1))
same("median all", mm.median(a), np.median(A)); same("median axis 0 of rows", mm.median(a.reshape(-1, 3), axis=0), np.median(A.reshape(-1, 3), axis=0))
for q in (0, 12.5, 50, 75, 99.5, 100): same(f"percentile {q}", mm.percentile(b, q), np.percentile(B, q))
same("median of an even count", mm.median(mm.array([4.0, 1.0, 3.0, 2.0])), 2.5)
same("std and var", [b.std(), b.var()], [B.std(), B.var()]); same("std by axis", b.std(0), B.std(0))
w = rng.uniform(0, 1, 50); x = rng.normal(0, 1, 50)
same("weighted average", mm.average(mm.asarray(x.tolist()), weights=mm.asarray(w.tolist())), np.average(x, weights=w))
same("long float32 sum stays exact (Kahan, doubles)", mm.full((1_000_000,), 0.1, mm.float32).sum(), 1_000_000 * float(np.float32(0.1)), 1e-9)

# masks, picking, nonzero, patches
same("mask picks", b[m], B[M]); same("mask on leading axes", a[m], A[M])
c = b.copy(); C = B.copy(); c[m] = 0.0; C[M] = 0.0; same("mask write", c, C)
c[2:5, 1:4] = 9.0; C[2:5, 1:4] = 9.0; same("slice write", c, C)
c[:, 0] += 1.5; C[:, 0] += 1.5; same("slice += (view writes through)", c, C)
ys, xs = mm.nonzero(m); Ys, Xs = np.nonzero(M); same("nonzero", [ys.tolist(), xs.tolist()], [Ys.tolist(), Xs.tolist()])
same("index list", b[mm.array([3, 0, -1])], B[np.array([3, 0, -1])])
same("rows by a mask (as pw[voiced])", a.reshape(-1, 3)[mm.asarray((A.reshape(-1, 3)[:, 0] > 128).tolist())], A.reshape(-1, 3)[A.reshape(-1, 3)[:, 0] > 128])
At = A.transpose(1, 0, 2); at = a.transpose(1, 0, 2); Mt = M.T; mt = m.T
same("trick: a mask over TWO leading axes of a transposed (non-side-by-side) grid", at[mt], At[Mt])
same("rows by an index list of a strided view", a[::3, 1:][[2, 0, 5]], A[::3, 1:][[2, 0, 5]])
def py_patches(mask):
    h, w = mask.shape; seen = np.zeros_like(mask); out = []
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]: continue
            st, pts = [(y, x)], []; seen[y, x] = True
            while st:
                cy, cx = st.pop(); pts.append((cy, cx))
                for yy, xx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                    if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]: seen[yy, xx] = True; st.append((yy, xx))
            out.append(sorted(pts))
    return out
check("patches (C) = the old Python flood fill, same patches, same order", [sorted(q) for q in mm.patches(m)] == py_patches(M))
ev, evec = mm.linalg.eigh((p - p.mean(0)).T @ (p - p.mean(0)))
EV, EVEC = np.linalg.eigh((P - P.mean(0)).T @ (P - P.mean(0)))
same("eigh values", ev, EV); same("eigh vectors (up to sign)", mm.abs(evec), np.abs(EVEC))
H = rng.normal(0, 1, (6, 6)); H = H + H.T
same("eigh 6x6", mm.linalg.eigh(mm.asarray(H.tolist()))[0], np.linalg.eigh(H)[0])

# pictures in and out (PIL)
from PIL import Image
im = Image.fromarray(U); g = mm.asarray(im)
same("PIL picture in", g, U); check("PIL picture out (fromarray)", np.array_equal(np.asarray(Image.fromarray(g)), U))
check("PIL out from a strided view", np.array_equal(np.asarray(Image.fromarray(g[::2, 1::3])), U[::2, 1::3]))

# numpy's faults: refused in plain words
refused("no silent wraparound: 255 + 1 put back into uint8", lambda: (mm.array([255], mm.uint8) + 1).astype(mm.uint8), "do not fit uint8")
check("255 + 1 is 256 (small whole numbers widen)", (mm.array([255], mm.uint8) + 1).tolist() == [256])
check("200 - 250 is -50, not 206", (mm.array([200], mm.uint8) - mm.array([250], mm.uint8)).tolist() == [-50])
refused("no silent float cut: 3.9 into uint8 asks how", lambda: mm.array([3.9]).astype(mm.uint8), "how=")
check("...how='trunc' / 'round' do as told", (mm.array([3.9]).astype(mm.uint8, how="trunc").tolist(), mm.array([3.5, 2.5]).astype(mm.uint8, how="round").tolist()) == ([3], [4, 2]))
refused("no silent outer grid: (3,) against (3,1)", lambda: mm.array([1.0, 2, 3]) - mm.array([[1.0], [2], [3]]), "stretching the bigger one")
refused("writing to a mask's copy is an error", lambda: b[m].__setitem__(0, 1.0), "COPY")
refused("a grid is not one yes/no", lambda: bool(b > 0), "use .any()")
refused("the mean of nothing", lambda: mm.zeros((0,)).mean(), "of nothing")
refused("& on numbers (Python's order trap)", lambda: b & 1, "mind Python's order")
refused("shapes that do not fit say which axis", lambda: a + mm.zeros((37, 52, 3)), "axis 1 is 53 against 52")
refused("a value that does not fit a slice write", lambda: u.__setitem__((0, 0, 0), 300), "do not fit this uint8")
check("one value is a plain Python number: json takes it", json.dumps({"mean": a.mean(), "max": u.max(), "any": m.any()}) is not None)

# the FFT, against numpy: any length, batches, padding and cutting, the inverse, the parts
for L in (1, 2, 3, 8, 400, 512, 800, 997, 8000):
    x = rng.normal(0, 1, L)
    X = mm.fft.rfft(mm.asarray(x.tolist())); NX = np.fft.rfft(x)
    same(f"rfft length {L} (real, imag)", [X.real.tolist(), X.imag.tolist()], [NX.real.tolist(), NX.imag.tolist()], 1e-9)
    same(f"irfft(rfft) length {L} gives it back", mm.fft.irfft(X, L), x, 1e-9)
fr = rng.normal(0, 1, (37, 400))
same("batch of 37 frames, padded to 512, power", mm.fft.power(mm.asarray(fr.tolist()), 512), np.abs(np.fft.rfft(fr, 512)) ** 2, 1e-9)
same("cut to 256 (n shorter than the row)", mm.abs(mm.fft.rfft(mm.asarray(fr.tolist()), 256)), np.abs(np.fft.rfft(fr, 256)), 1e-9)
sv = rng.normal(0, 1, 300); w2 = 300
same("pitch autocorrelation irfft(|rfft(s, 2w)|^2)[:w]", mm.fft.irfft(mm.fft.power(mm.asarray(sv.tolist()), 2 * w2))[:w2],
     np.fft.irfft(np.abs(np.fft.rfft(sv, 2 * w2)) ** 2)[:w2], 1e-9)
same("irfft of an odd length", mm.fft.irfft(mm.fft.rfft(mm.asarray(sv[:7].tolist())), 7), sv[:7], 1e-9)
z = mm.fft.rfft(mm.asarray(sv[:64].tolist())); Z = np.fft.rfft(sv[:64])
same("angle and conj", [mm.angle(z).tolist(), mm.conj(z).imag.tolist()], [np.angle(Z).tolist(), np.conj(Z).imag.tolist()], 1e-9)
same("rfftfreq", mm.fft.rfftfreq(800, 1 / 16000), np.fft.rfftfreq(800, 1 / 16000))
same("hanning and hamming", [mm.hanning(9).tolist(), mm.hamming(9).tolist(), mm.hanning(1).tolist()], [np.hanning(9).tolist(), np.hamming(9).tolist(), np.hanning(1).tolist()])
same("pad on the left", mm.pad(mm.array([1.0, 2.0]), (3, 0)), np.pad(np.array([1.0, 2.0]), (3, 0)))
raw = (rng.integers(-32768, 32767, 1000)).astype("<i2").tobytes()
same("sound bytes as int16", mm.frombuffer(raw, "<i2").astype(mm.float32) / 32768.0, np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0)
same("FFT of silence is silence", mm.fft.power(mm.zeros(800)), np.zeros(401))
refused("complex grids refuse what murmur does not do on them", lambda: z * 2, "take abs(x)")
refused("rfft of a complex grid", lambda: mm.fft.rfft(z), "takes real numbers")
refused("rfft of length 0", lambda: mm.fft.rfft(mm.zeros(4), 0), "length of 1 or more")
refused("int16 never wraps: 40000 refused", lambda: mm.array([40000]).astype(mm.int16), "-32768..32767")

# stage 3 (voices and cuts), against numpy
A2 = rng.normal(0, 1, (40, 257)); B2 = rng.normal(0, 1, (257, 300))
same("matmul (40x257) @ (257x300)", mm.asarray(A2.tolist()) @ mm.asarray(B2.tolist()), A2 @ B2, 1e-9)
same("matmul with a transposed view", mm.asarray(B2.tolist()).T @ mm.asarray(A2.T.tolist()), B2.T @ A2.T, 1e-9)
same("1-D @ 2-D", mm.asarray(B2[:, 0].tolist()) @ mm.asarray(B2.tolist()), B2[:, 0] @ B2, 1e-9)
S3 = rng.normal(0, 1, (7, 9)); S3[2, 3] = np.nan; S3[4, :3] = 1.5
same("sort along rows (NaN last, ties)", mm.sort(mm.asarray(S3.tolist()), 1), np.sort(S3, 1))
same("sort along columns", mm.sort(mm.asarray(S3.tolist()), 0), np.sort(S3, 0))
same("argsort stable on ties", mm.argsort(mm.asarray([3.0, 1.0, 3.0, 1.0, 2.0])), np.argsort(np.array([3.0, 1.0, 3.0, 1.0, 2.0]), kind="stable"))
fa, fv = rng.normal(0, 1, 40), rng.normal(0, 1, 7)
for md in ("full", "same", "valid"):
    same(f"correlate {md}", mm.correlate(mm.asarray(fa.tolist()), mm.asarray(fv.tolist()), md), np.correlate(fa, fv, md), 1e-9)
def rsort(z): return sorted([complex(v) for v in z], key=lambda c: (round(c.real, 6), round(c.imag, 6)))
for deg in (1, 2, 5, 12):
    P = rng.normal(0, 1, deg + 1)
    got, want = rsort(mm.roots(mm.asarray(P.tolist())).tolist()), rsort(np.roots(P))
    check(f"roots degree {deg} (complex pairs too)", len(got) == len(want) and all(abs(g - w) < 1e-7 * max(1, abs(w)) for g, w in zip(got, want)), f"{got} vs {want}")
_g, _w = rsort(mm.roots(mm.array([0.0, 1.0, -3.0, 2.0, 0.0])).tolist()), rsort(np.roots([0.0, 1.0, -3.0, 2.0, 0.0]))
check("roots: leading zeros dropped, trailing zeros are roots at 0", len(_g) == len(_w) == 3 and all(abs(a - b) < 1e-9 for a, b in zip(_g, _w)), f"{_g} vs {_w}")
xs_ = rng.normal(0, 1, 60); ys_ = 2 * xs_ ** 3 - xs_ + 0.5 + rng.normal(0, 0.05, 60)
for deg in (1, 2, 3): same(f"polyfit degree {deg}", mm.polyfit(mm.asarray(xs_.tolist()), mm.asarray(ys_.tolist()), deg), np.polyfit(xs_, ys_, deg), 1e-7)
CV = rng.normal(0, 1, (5, 80))
same("cov (rows are variables)", mm.cov(mm.asarray(CV.tolist())), np.cov(CV), 1e-9)
same("eye, diag, trace", [mm.eye(4).tolist(), mm.diag(mm.array([1.0, 2.0, 3.0])).tolist(), mm.diag(mm.asarray(CV[:, :5].tolist())).tolist(), [mm.trace(mm.asarray(CV[:, :5].tolist()))]],
     [np.eye(4).tolist(), np.diag([1.0, 2.0, 3.0]).tolist(), np.diag(CV[:, :5]).tolist(), [np.trace(CV[:, :5])]])
same("bincount with minlength", mm.bincount(mm.array([0, 2, 2, 5]), minlength=8), np.bincount([0, 2, 2, 5], minlength=8))
check("allclose", (mm.allclose(mm.array([1.0, 2.0]), mm.array([1.0, 2.0 + 1e-9])), mm.allclose(mm.array([1.0]), mm.array([1.1]))) == (True, False))
same("append", mm.append(1.5, mm.array([2.0, 3.0])), np.append(1.5, [2.0, 3.0]))
same("percentiles of a list", mm.percentile(mm.array([1.0, 5, 2, 8, 3]), [75, 25]), np.percentile([1.0, 5, 2, 8, 3], [75, 25]))
snd_ = rng.normal(0, 1, 3000).astype(np.float32); g_ = mm.asarray(snd_.tolist(), mm.float32)
nfr = 1 + (3000 - 400) // 160
same("as_strided frames (overlapping, as voice prints take them)", mm.as_strided(g_, (nfr, 400), (4 * 160, 4)),
     np.lib.stride_tricks.as_strided(snd_, (nfr, 400), (4 * 160, 4)))
refused("as_strided reaching outside the grid is refused", lambda: mm.as_strided(g_, (nfr + 5, 400), (4 * 160, 4)), "outside the grid")
refused("...and the frames are read-only, said why (they overlap)", lambda: mm.as_strided(g_, (2, 4), (16, 4)).__setitem__((0, 0), 1.0), "numbers overlap")
import tempfile
tmp = tempfile.mkdtemp()
np.save(os.path.join(tmp, "e.npy"), rng.normal(0, 1, (6, 192)).astype(np.float32))
same(".npy read without numpy", mm.load(os.path.join(tmp, "e.npy")), np.load(os.path.join(tmp, "e.npy")))
pcm_ = rng.integers(-30000, 30000, 4000).astype(np.int16); pcm_.tofile(os.path.join(tmp, "s.pcm"))
wm = mm.memmap(os.path.join(tmp, "s.pcm"), mm.int16)
same("memmap of a sound file, sliced", wm[1000:1500], np.memmap(os.path.join(tmp, "s.pcm"), dtype=np.int16, mode="r")[1000:1500])
refused("memmap is read-only, said why (the file is never changed)", lambda: wm.__setitem__(0, 5), "file itself is never changed")
refused("a kept window is shared: changing it is refused, said why", lambda: mm.hanning(16).__setitem__(0, 9.0), "shared and read-only")
r1, r2 = mm.random.default_rng(7), mm.random.default_rng(7)
check("random: the same seed gives the same numbers", [r1.integers(1000) for _ in range(20)] == [r2.integers(1000) for _ in range(20)])
r3 = mm.random.default_rng(3); us = r3.random(20000).tolist()
check("random: uniform 0..1, mean near 0.5", 0 <= min(us) and max(us) < 1 and abs(sum(us) / len(us) - 0.5) < 0.01)
ch = r3.choice(50, 20, replace=False).tolist(); check("choice without replacement: all different", len(set(ch)) == 20 and all(0 <= v < 50 for v in ch))
wp = [r3.choice(3, p=[0.1, 0.2, 0.7]) for _ in range(20000)]
check("choice with weights 0.1/0.2/0.7 comes out near them", all(abs(wp.count(i) / 20000 - q) < 0.015 for i, q in enumerate([0.1, 0.2, 0.7])))
def np_shifted(a, c, r=3):
    H, W = a.shape; best = np.inf
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            best = min(best, float(np.abs(a[max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)] - c[max(0, -dy):H + min(0, -dy), max(0, -dx):W + min(0, -dx)]).mean()))
    return best
F1 = rng.integers(0, 256, (36, 64)).astype(np.int16); F2 = np.roll(F1, (2, -1), (0, 1)); F2[:5] = 0
check("shift_match = perch_cuts' numpy rule exactly", mm.shift_match(mm.asarray(F1.tolist(), mm.int16), mm.asarray(F2.tolist(), mm.int16)) == np_shifted(F1, F2))

# cumsum and normal random numbers
CS = rng.normal(0, 1, (6, 7, 5))
for ax in (None, 0, 1, 2, -1): same(f"cumsum axis {ax}", mm.cumsum(mm.asarray(CS.tolist()), ax), np.cumsum(CS, ax), 1e-9)
same("cumsum of uint8 (no wrap: 200+100+...)", mm.cumsum(mm.array([200, 100, 250, 5], mm.uint8)), np.cumsum(np.array([200, 100, 250, 5], np.uint8), dtype=np.int64))
same("cumsum of yes/no counts them", mm.cumsum(mm.array([True, False, True, True])), np.cumsum([True, False, True, True]))
same("grid.cumsum(1)", mm.asarray(CS.tolist()).cumsum(1), CS.cumsum(1), 1e-9)
check("cumsum of an empty row is empty", mm.cumsum(mm.zeros((0,))).shape == (0,))
L32 = mm.full((2_000_000,), 0.1, mm.float32)
exact = 2_000_000 * float(np.float32(0.1))
mine, theirs = float(mm.cumsum(L32)[-1]), float(np.cumsum(np.full(2_000_000, 0.1, np.float32))[-1])
check(f"long float32 run: murmur's last total {mine:.1f} near exact {exact:.1f} (numpy's float32 drifts to {theirs:.1f})", abs(mine - exact) / exact < 1e-6)
g1, g2 = mm.random.default_rng(42), mm.random.default_rng(42)
check("normal: the same seed gives the same numbers", g1.normal(size=50).tolist() == g2.normal(size=50).tolist())
g3 = mm.random.default_rng(9); Nm = g3.normal(3.0, 2.0, 200_000)
check("normal: the shape asked for", g3.normal(size=(3, 4)).shape == (3, 4))
same("normal(3, 2): mean 3, spread 2", [Nm.mean(), Nm.std()], [3.0, 2.0], 5e-3)
zs = sorted(((v - 3.0) / 2.0) for v in Nm.tolist()); nn = len(zs)
ks = max(max(abs((i + 1) / nn - 0.5 * (1 + math.erf(z / math.sqrt(2)))), abs(i / nn - 0.5 * (1 + math.erf(z / math.sqrt(2))))) for i, z in enumerate(zs))
check(f"normal: Kolmogorov-Smirnov against the true bell curve D={ks:.4f} (< 1.63/sqrt(n) = {1.63 / math.sqrt(nn):.4f}, 99%)", ks < 1.63 / math.sqrt(nn))
tail = sum(1 for z in zs if abs(z) > 3) / nn
check(f"normal: tails beyond 3 spreads {tail:.5f} (true 0.0027)", abs(tail - 0.0027) < 0.0006)
refused("normal: a negative spread", lambda: g3.normal(0, -1, 5), "cannot be negative")
gp, gc = mm.random.default_rng(3), mm.random.default_rng(3)
check("uniform draws from C and from Python are one stream", gc.random(4).tolist() + [gc.random()] == [gp.random() for _ in range(5)])

# speed on picture-sized grids (the same work through both)
big = rng.uniform(0, 255, (360, 640, 3)).astype(np.float32); gb = mm.asarray(Image.fromarray(big.astype(np.uint8))).astype(mm.float32)
def t(f, k=5):
    t0 = time.perf_counter()
    for _ in range(k): f()
    return (time.perf_counter() - t0) / k * 1000
W3 = np.array([0.2126, 0.7152, 0.0722], np.float32); w3 = mm.array([0.2126, 0.7152, 0.0722], mm.float32)
SND = rng.normal(0, 1, 8000) * np.hanning(8000); snd = mm.asarray(SND.tolist())
FR = rng.normal(0, 1, (2000, 400)); frs = mm.asarray(FR.tolist())
FBn = rng.uniform(0, 1, (40, 257)); PWn = rng.uniform(0, 1, (257, 2000)); FBm = mm.asarray(FBn.tolist()); PWm = mm.asarray(PWn.tolist())
f1m, f2m = mm.asarray(F1.tolist(), mm.int16), mm.asarray(F2.tolist(), mm.int16)
BIGC = rng.uniform(0, 1, (1080, 5760)).astype(np.float32); bigc = mm.frombuffer(BIGC.tobytes(), mm.float32).reshape(1080, 5760)
NRNG, MRNG = np.random.default_rng(1), mm.random.default_rng(1)
rows = [("luminance (360x640x3 @ 3)", lambda: big @ W3, lambda: gb @ w3),
        ("median of 230,400", lambda: np.median(big[..., 1]), lambda: mm.median(gb[..., 1])),
        ("abs diff + compare", lambda: np.abs(big - big[::-1]) > 20, lambda: mm.abs(gb - gb[::-1]) > 20),
        ("mean by axis", lambda: big.mean(0), lambda: gb.mean(0)),
        ("patches of a 160x90 mask", lambda: py_patches(M), lambda: mm.patches(m)),
        ("sound band power, 8000 window", lambda: np.abs(np.fft.rfft(SND)) ** 2, lambda: mm.fft.power(snd)),
        ("voice frames 2000x400 -> 512", lambda: np.abs(np.fft.rfft(FR, 512)) ** 2, lambda: mm.fft.power(frs, 512)),
        ("mel filters (40x257) @ (257x2000)", lambda: FBn @ PWn, lambda: FBm @ PWm),
        ("cuts: 49-shift match 64x36", lambda: np_shifted(F1, F2), lambda: mm.shift_match(f1m, f2m)),
        ("cumsum of 6.2 million (rows of 5760)", lambda: np.cumsum(BIGC, 1), lambda: mm.cumsum(bigc, 1)),
        ("1 million normal numbers", lambda: NRNG.normal(size=1_000_000), lambda: MRNG.normal(size=1_000_000))]
print("speed (ms, average of 5)          numpy    murmur")
for name, f_np, f_mm in rows:
    print(f"  {name:32} {t(f_np):7.2f}  {t(f_mm):7.2f}")

print(f"{n - len(fails)}/{n} pass")
sys.exit(1 if fails else 0)
