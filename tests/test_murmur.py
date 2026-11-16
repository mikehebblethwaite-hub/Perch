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
    g = np.asarray(got.tolist() if isinstance(got, mm.Grid) else got, dtype=float)
    w = np.asarray(want, dtype=float)
    ok = g.shape == w.shape and np.allclose(g, w, rtol=tol, atol=tol * 10, equal_nan=True)
    check(name, ok, f"shape {g.shape} vs {w.shape}" if g.shape != w.shape else f"max diff {np.nanmax(np.abs(g - w)) if g.size else 0}")

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

# speed on picture-sized grids (the same work through both)
big = rng.uniform(0, 255, (360, 640, 3)).astype(np.float32); gb = mm.asarray(Image.fromarray(big.astype(np.uint8))).astype(mm.float32)
def t(f, k=5):
    t0 = time.perf_counter()
    for _ in range(k): f()
    return (time.perf_counter() - t0) / k * 1000
W3 = np.array([0.2126, 0.7152, 0.0722], np.float32); w3 = mm.array([0.2126, 0.7152, 0.0722], mm.float32)
rows = [("luminance (360x640x3 @ 3)", lambda: big @ W3, lambda: gb @ w3),
        ("median of 230,400", lambda: np.median(big[..., 1]), lambda: mm.median(gb[..., 1])),
        ("abs diff + compare", lambda: np.abs(big - big[::-1]) > 20, lambda: mm.abs(gb - gb[::-1]) > 20),
        ("mean by axis", lambda: big.mean(0), lambda: gb.mean(0)),
        ("patches of a 160x90 mask", lambda: py_patches(M), lambda: mm.patches(m))]
print("speed (ms, average of 5)          numpy    murmur")
for name, f_np, f_mm in rows:
    print(f"  {name:32} {t(f_np):7.2f}  {t(f_mm):7.2f}")

print(f"{n - len(fails)}/{n} pass")
sys.exit(1 if fails else 0)
