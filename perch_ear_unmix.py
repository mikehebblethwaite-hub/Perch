#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_ear_unmix: overlapping sounds pulled apart by what the ear already knows.
# Every half second of sound becomes a FINGERPRINT: its total power in 64 bands (each FFT bin in exactly one band) spaced evenly by pitch (log) from 60 Hz to 7.9 kHz,
# from Perch's own FFT (murmur); numbers only, never the sound. A known sound (named by a person) keeps the mean fingerprint of
# its example. Sounds that overlap ADD their power, so a moment's fingerprint is explained as a mix: amounts (never below zero) of
# each known fingerprint, found by non-negative least squares, plus the LEFTOVER: the power nothing known accounts for. A big
# leftover is something new (a bird over the kettle); its own fingerprint is the leftover itself.
#   python3 perch_ear_unmix.py prints FILE.wav                      -> one fingerprint per half second (JSON lines)
#   python3 perch_ear_unmix.py learn  NAME FILE.wav [FROM TO] [KNOWN.json]
#   python3 perch_ear_unmix.py unmix  FILE.wav [KNOWN.json]          -> per half second: name share ... leftover share
import json, math, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perch_murmur as mm

RATE, WIN, STEP = 16000, 1024, 0.5
NB, LO, HI = 64, 60.0, 7900.0
EDGES = [LO * (HI / LO) ** (i / NB) for i in range(NB + 1)]
def _bins():                                           # each FFT bin in exactly ONE band (the lowest bands are narrower than a bin)
    out, nxt = [], 0
    for i in range(NB):
        a_ = max(int(EDGES[i] / (RATE / WIN)), nxt); b_ = max(a_ + 1, int(EDGES[i + 1] / (RATE / WIN))); out.append([a_, b_]); nxt = b_
    return out
BIN = _bins()


def samples(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                         capture_output=True, timeout=600).stdout
    if len(raw) < 2 * WIN: return None
    return mm.frombuffer(raw[: len(raw) // 2 * 2], "<i2").astype(mm.float32) / 32768.0


def prints(path):
    """[(seconds into the file, [64 band powers])], one per half second (each the mean of its 1024-sample windows: Welch)"""
    x = samples(path)
    if x is None: return []
    step = int(STEP * RATE); hop = WIN // 2; out = []
    han = mm.hanning(WIN)
    for k in range(0, len(x) - step + 1, step):
        seg = x[k: k + step]
        nw = (len(seg) - WIN) // hop + 1
        if nw < 1: continue
        pw = mm.fft.power(mm.as_strided(seg, (nw, WIN), (seg.strides[0] * hop, seg.strides[0])) * han).mean(axis=0).tolist()
        out.append((k / RATE, [sum(pw[a:b]) for a, b in BIN]))          # TOTAL power in the band: shares are shares of the sound
    return out


def mean_print(ps):
    if not ps: return None
    return [sum(p[i] for _, p in ps) / len(ps) for i in range(NB)]


def stages(ps, k=4, rounds=25):
    """a sound's STAGES (a kettle hisses, roars, then rumbles): its half-second fingerprints grouped into k by shape (k-means on
    each fingerprint's share per band, started from evenly spread moments: the same answer every time), each stage the mean of its
    members' power. Returns up to k fingerprints."""
    ps = [p for p in ps if sum(p) > 0]
    if not ps: return []
    if k <= 1: return [mean_print([(0, p) for p in ps])]          # one stage: the mean (k - 1 would divide by nothing)
    if len(ps) <= k: return [list(p) for p in ps]
    norm = [[v / sum(p) for v in p] for p in ps]
    cent = [list(norm[int(i * (len(norm) - 1) / (k - 1))]) for i in range(k)]
    for _ in range(rounds):
        groups = [[] for _ in range(k)]
        for j, q in enumerate(norm):
            groups[min(range(k), key=lambda c: sum((q[i] - cent[c][i]) ** 2 for i in range(NB)))].append(j)
        cent = [[sum(norm[j][i] for j in g) / len(g) for i in range(NB)] if g else cent[c] for c, g in enumerate(groups)]
    return [[sum(ps[j][i] for j in g) / len(g) for i in range(NB)] for g in groups if g]


def unmix_named(p, known, floor=0.0):
    """known: {name: [fingerprint, ...]} (a sound's stages). Below `floor` total power: quiet (nothing shared out). Returns
    ({name: share}, leftover) with each name's stages added together."""
    if sum(p) <= floor: return {}, 0.0
    flat = {f"{n}\x00{i}": fp for n, fps in known.items() for i, fp in enumerate(fps if isinstance(fps[0], list) else [fps])}
    sh, left = unmix_one(p, flat)
    out = {}
    for k_, v in sh.items(): out[k_.split("\x00")[0]] = round(out.get(k_.split("\x00")[0], 0) + v, 3)
    return out, left


def unmix_one(p, known):
    """amounts a_k >= 0 with sum_k a_k * known_k closest to p (coordinate descent on the least squares, kept non-negative);
    returns ({name: share of the power}, leftover share)"""
    names = list(known); W = [known[n] for n in names]
    total = sum(p) or 1e-30
    a = [0.0] * len(W)
    norms = [sum(v * v for v in w) or 1e-30 for w in W]
    r = list(p)                                        # the residual, kept up to date as each amount moves (not recomputed)
    for _ in range(300):
        moved = 0.0
        for j, w in enumerate(W):
            new = max(0.0, a[j] + sum(w[i] * r[i] for i in range(NB)) / norms[j])
            d = new - a[j]
            if d:
                for i in range(NB): r[i] -= d * w[i]
                moved += abs(d) * (sum(w) or 0); a[j] = new
        if moved < 1e-6 * total: break
    # each band: the known sounds explain AT MOST the power that is there (an average fingerprint overshoots some bands and falls
    # short in others); the rest of the band is leftover. So the shares and the leftover always add up to exactly 1.
    shares = [0.0] * len(W); left = 0.0
    for i in range(NB):
        parts = [a[k] * W[k][i] for k in range(len(W))]; fit = sum(parts)
        scale = min(1.0, p[i] / fit) if fit > 0 else 0.0
        for k in range(len(W)): shares[k] += parts[k] * scale
        left += max(0.0, p[i] - fit)
    return {n: round(shares[j] / total, 3) for j, n in enumerate(names)}, round(left / total, 3)


def load(path):
    return json.load(open(path)) if os.path.exists(path) else {}


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["prints"]:
        for t, p in prints(a[1]): print(json.dumps({"t": t, "p": [round(v, 9) for v in p]}))
    elif a[:1] == ["learn"] and len(a) >= 3:
        frm, to = (float(a[3]), float(a[4])) if len(a) >= 5 else (0, 1e9)
        kp = a[5] if len(a) > 5 else "known-prints.json"
        m = mean_print([x for x in prints(a[2]) if frm <= x[0] < to])
        if not m: sys.exit("no sound in that stretch")
        K = load(kp); K[a[1]] = m; json.dump(K, open(kp, "w")); print(f"learned {a[1]!r} ({len(K)} known)")
    elif a[:1] == ["unmix"] and len(a) >= 2:
        K = load(a[2] if len(a) > 2 else "known-prints.json")
        for t, p in prints(a[1]):
            s, left = unmix_one(p, K)
            print(f"{t:5.1f} " + " ".join(f"{n} {v:.2f}" for n, v in sorted(s.items(), key=lambda kv: -kv[1]) if v >= 0.05) + f" | leftover {left:.2f}")
    else:
        sys.exit("prints FILE | learn NAME FILE [FROM TO] [KNOWN] | unmix FILE [KNOWN]")
