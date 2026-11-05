# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_cuts: where a video cuts to a new scene, from its 64x36 grey copy (numbers only, never shown).
# Each video is measured against itself: a cut is a sudden change far above that video's own normal change around it (a drawn
# cartoon changes a lot every frame, a lecture hardly at all). The change is taken after the best small shift, so a pan or a
# character walking does not count. A big movement inside one shot is told from a cut by what comes next: after a cut the new
# picture holds, or the frames after it go with the new picture and the frames before it with the old one.
import numpy as np


def shifted_all(A, r=3):
    """the change from each frame to the one before, after the best shift of up to r cells each way -> array (D[0] = 0)"""
    N, H, W = A.shape
    best = np.full(N - 1, np.inf, dtype=np.float32)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            a = A[1:, max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)]
            c = A[:-1, max(0, -dy):H + min(0, -dy), max(0, -dx):W + min(0, -dx)]
            best = np.minimum(best, np.abs(a - c).mean(axis=(1, 2)))
    return np.concatenate([[0.0], best])


def shifted(a, c, r=3):
    """the change between two frames after the best small shift"""
    H, W = a.shape
    best = np.inf
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            best = min(best, float(np.abs(a[max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)] -
                                          c[max(0, -dy):H + min(0, -dy), max(0, -dx):W + min(0, -dx)]).mean()))
    return best


def find(A, fps):
    """A: frames as an int16 array (N, H, W), taken fps times a second -> the frame numbers where a new scene starts"""
    N = len(A)
    if N < 3: return []
    D = shifted_all(A)
    near = max(2, round(2 * fps))            # the video's own normal change: the 2 s before and after
    gap = max(1, round(0.6 * fps))           # two cuts closer than this are one
    look = max(1, round(0.75 * fps))         # how far to look either side to see which picture the frames go with
    cuts = []
    for i in range(1, N):
        nb = np.concatenate([D[max(1, i - near):i], D[i + 2:i + 2 + near]])
        base = float(np.median(nb)) if len(nb) else 0.0
        nxt = D[i + 1] if i + 1 < N else 0.0
        if not (D[i] > max(22.0, 2.0 * base) and D[i] >= D[i - 1] and D[i] >= nxt):
            continue
        if cuts and i - cuts[-1] <= gap:
            continue
        settles = i + 2 < N and nxt < 0.6 * D[i] and shifted(A[i + 2], A[i]) < 0.7 * D[i]
        sides = i - look >= 0 and i + look < N and \
            shifted(A[i + look], A[i - 1]) > 1.4 * shifted(A[i + look], A[i]) and \
            shifted(A[i - look], A[i]) > 1.4 * shifted(A[i - look], A[i - 1])
        if settles or sides:
            cuts.append(i)
    return cuts
