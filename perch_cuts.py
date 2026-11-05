# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_cuts: where a video cuts to a new scene, from its 64x36 grey copy (numbers only, never shown).
# Each video is measured against itself: a cut is a sudden change far above that video's own normal change around it (a drawn
# cartoon changes a lot every frame, a lecture hardly at all). The change is taken after the best small shift, so a pan or a
# character walking does not count. A big movement inside one shot is told from a cut by what comes next: after a cut the new
# picture holds, or the frames after it go with the new picture and the frames before it with the old one.
import numpy as np


def shifted(a, c, r=3):
    """the change between two frames after the best small shift"""
    H, W = a.shape
    best = np.inf
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            best = min(best, float(np.abs(a[max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)] -
                                          c[max(0, -dy):H + min(0, -dy), max(0, -dx):W + min(0, -dx)]).mean()))
    return best


class Stream:
    """the same rule as the video plays: push each grey frame as it comes; a frame is decided once enough frames after it are in
    (about 2.5 s at 4 a second) -> push() and flush() return [(frame number, is it a cut)] in order"""
    def __init__(self, fps):
        self.near = max(2, round(2 * fps))      # the video's own normal change: the 2 s before and after
        self.gap = max(1, round(0.6 * fps))     # two cuts closer than this are one
        self.look = max(1, round(0.75 * fps))   # how far to look either side to see which picture the frames go with
        self.A, self.D, self.cuts, self.done = {}, [], [], 1
        self.n = 0

    def push(self, g):
        self.A[self.n] = g
        self.D.append(shifted(g, self.A[self.n - 1]) if self.n else 0.0)
        self.n += 1
        out = []
        while self.done < self.n - (self.near + 2):
            out.append(self._decide(self.done)); self.done += 1
        for old in [k for k in self.A if k < self.done - self.near - self.look - 2]: del self.A[old]   # keep only what is still looked at
        return out

    def flush(self):
        out = []
        while self.done < self.n:
            out.append(self._decide(self.done)); self.done += 1
        return out

    def _decide(self, i):
        A, D, N = self.A, self.D, self.n
        nb = D[max(1, i - self.near):i] + D[i + 2:i + 2 + self.near]
        base = float(np.median(nb)) if nb else 0.0
        nxt = D[i + 1] if i + 1 < N else 0.0
        cut = False
        if D[i] > max(22.0, 2.0 * base) and D[i] >= D[i - 1] and D[i] >= nxt and not (self.cuts and i - self.cuts[-1] <= self.gap):
            settles = i + 2 < N and nxt < 0.6 * D[i] and shifted(A[i + 2], A[i]) < 0.7 * D[i]
            sides = i - self.look >= 0 and i + self.look < N and \
                shifted(A[i + self.look], A[i - 1]) > 1.4 * shifted(A[i + self.look], A[i]) and \
                shifted(A[i - self.look], A[i]) > 1.4 * shifted(A[i - self.look], A[i - 1])
            cut = settles or sides
        if cut: self.cuts.append(i)
        return i, cut


def find(A, fps):
    """A: frames as an int16 array (N, H, W), taken fps times a second -> the frame numbers where a new scene starts"""
    s = Stream(fps)
    for g in A: s.push(g)
    s.flush()
    return s.cuts
