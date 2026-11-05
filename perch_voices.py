# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_voices: who is speaking, as voice 1, voice 2 ... within one video (never who a real person is). Our own voice prints,
# numbers only: for each stretch of speech, the shape of the voice (MFCCs: their mean and spread over the voiced moments) and its
# pitch (median and spread). Stretches are grouped by likeness; how many voices there are is worked out from the video itself.
import numpy as np

SR = 16000


def _mel_bank(n_fft=512, n_mels=40, lo=60.0, hi=7600.0):
    mel = lambda f: 2595 * np.log10(1 + f / 700.0)
    inv = lambda m: 700 * (10 ** (m / 2595.0) - 1)
    pts = inv(np.linspace(mel(lo), mel(hi), n_mels + 2))
    bins = np.floor((n_fft + 1) * pts / SR).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for i in range(n_mels):
        a, b, c = bins[i], bins[i + 1], bins[i + 2]
        fb[i, a:b] = (np.arange(a, b) - a) / max(1, b - a)
        fb[i, b:c] = (c - np.arange(b, c)) / max(1, c - b)
    return fb


FB = _mel_bank()
DCT = np.cos(np.pi / 40 * (np.arange(40)[None, :] + 0.5) * np.arange(20)[:, None])      # DCT-II, 20 coefficients


def voice_print(x, with_tone=False):
    """x: float32 samples of one stretch (16 kHz) -> a vector, or None if too little voice in it"""
    if len(x) < SR * 0.6: return None
    win, hop = 400, 160
    n = 1 + (len(x) - win) // hop
    fr = np.lib.stride_tricks.as_strided(x, (n, win), (x.strides[0] * hop, x.strides[0])) * np.hamming(win)
    pw = np.abs(np.fft.rfft(fr, 512)) ** 2
    en = np.log(pw.sum(1) + 1e-9)
    voiced = en > np.percentile(en, 40)                          # the louder 60%: voice, not the gaps between words
    if voiced.sum() < 30: return None
    mf = (DCT @ np.log(FB @ pw[voiced].T + 1e-9)).T[:, 1:]       # 19 MFCCs per voiced moment
    # pitch: autocorrelation over 40 ms, 70-320 Hz, on the voiced moments
    f0, hnr = [], []
    w2 = 640
    for i in np.nonzero(voiced)[0][::3]:
        s = x[i * hop:i * hop + w2]
        if len(s) < w2: break
        s = s - s.mean(); ac = np.fft.irfft(np.abs(np.fft.rfft(s, 2 * w2)) ** 2)[:w2]
        if ac[0] <= 0: continue
        lo, hi = SR // 320, SR // 70
        k = lo + int(np.argmax(ac[lo:hi]));
        if ac[k] / ac[0] > 0.45:
            f0.append(SR / k); r_ = min(0.999, ac[k] / ac[0]); hnr.append(10 * np.log10(r_ / (1 - r_)))
    p = np.log(f0) if len(f0) >= 8 else None
    pitch = [np.median(p), np.subtract(*np.percentile(p, [75, 25]))] if p is not None else [np.nan, np.nan]
    # the MFCCs already are the tone of a voice as the ear hears it (the mel scale copies hearing) and pitch is the rest; the
    # classic extra measures (formants, clearness, brightness, jitter) added nothing on a room recording (tested 5 Nov 2026:
    # host questions 86% -> 83%, and twice the time), so they are only for a closer listen on clean vowels
    return np.concatenate([mf.mean(0), mf.std(0)] + ([tone(x, fr, pw, voiced, f0, hnr)] if with_tone else []) + [pitch])


def _lpc(f, order=12):                                          # linear prediction (Levinson-Durbin): the vocal tract as a filter
    r = np.correlate(f, f, "full")[len(f) - 1:len(f) + order]
    if r[0] <= 0: return None
    a = np.zeros(order + 1); a[0] = 1.0; e = r[0]
    for i in range(1, order + 1):
        k = -(r[i] + a[1:i] @ r[i - 1:0:-1]) / e
        a[1:i + 1] = a[1:i + 1] + k * np.concatenate([a[i - 1:0:-1], [1.0]]); e *= 1 - k * k
        if e <= 0: return None
    return a


def tone(x, fr, pw, voiced, f0, hnr):
    """the tone of a voice, as people hear it: formants F1-F3 and their spacing (the size and shape of throat and mouth),
    clear or breathy (harmonics to noise), bright or dark (spectral tilt), steady or wobbly (jitter)"""
    F = []
    for i in np.nonzero(voiced)[0][::4]:
        f = np.append(fr[i][0], fr[i][1:] - 0.97 * fr[i][:-1])  # pre-emphasis, as speech analysis does
        a = _lpc(f)
        if a is None: continue
        rts = [r for r in np.roots(a) if np.imag(r) > 0.01]
        fq = sorted(np.angle(rts) * SR / (2 * np.pi))
        fq = [v for v in fq if 200 < v < 4000]
        if len(fq) >= 3: F.append(fq[:3])
    F = np.median(np.array(F), 0) if len(F) >= 5 else np.array([np.nan] * 3)
    disp = (F[2] - F[0]) / 2 if not np.isnan(F).any() else np.nan
    lf = np.log(np.arange(1, pw.shape[1]) * SR / 1024.0)        # brightness: slope of loudness against frequency
    tilt = np.median([np.polyfit(lf, np.log(pw[i, 1:] + 1e-9), 1)[0] for i in np.nonzero(voiced)[0][::6]])
    jit = float(np.median(np.abs(np.diff(f0)) / np.array(f0[1:]))) if len(f0) >= 8 else np.nan
    h = float(np.median(hnr)) if len(hnr) >= 8 else np.nan
    return np.array([np.log(F[0]) if F[0] > 0 else np.nan, np.log(F[1]), np.log(F[2]), np.log(disp) if disp > 0 else np.nan, tilt, h, jit])


def _kmeans(X, k, rng, iters=40):
    c = [X[rng.integers(len(X))]]
    for _ in range(1, k):                                        # k-means++ start
        d = np.min([((X - ci) ** 2).sum(1) for ci in c], axis=0)
        c.append(X[rng.choice(len(X), p=d / d.sum())])
    c = np.array(c)
    for _ in range(iters):
        lab = np.argmin(((X[:, None, :] - c[None]) ** 2).sum(2), 1)
        new = np.array([X[lab == j].mean(0) if (lab == j).any() else c[j] for j in range(k)])
        if np.allclose(new, c): break
        c = new
    return lab, c


def _silhouette(X, lab, rng, n=900):
    idx = rng.choice(len(X), min(n, len(X)), replace=False); Xs, ls = X[idx], lab[idx]
    D = np.sqrt(((Xs[:, None, :] - Xs[None]) ** 2).sum(2)); s = []
    for i in range(len(Xs)):
        same = ls == ls[i]; same[i] = False
        if not same.any(): continue
        a = D[i, same].mean(); b = min(D[i, ls == j].mean() for j in set(ls) if j != ls[i])
        s.append((b - a) / max(a, b))
    return float(np.mean(s)) if s else -1.0


def _wccn(Z, lab, k, reg=0.1):
    """a rescaling that makes the variation inside each voice count less (what changes with the words, not the person)"""
    Sw = sum(np.cov(Z[lab == j].T) * (lab == j).sum() for j in range(k)) / len(Z)
    Sw = Sw + reg * np.eye(Z.shape[1]) * np.trace(Sw) / Z.shape[1]
    w, V = np.linalg.eigh(Sw)
    return V @ np.diag(w ** -0.5) @ V.T


def voices(wav, segs, max_voices=6, seed=7, smooth=1.0, rounds=3):
    """wav: float32 16 kHz mono; segs: [(start s, end s)] of speech -> [(voice number from 1 or 0 = unknown, sure 1-11)] per seg,
    and the number of voices found. Voices are numbered in the order they first speak."""
    rng = np.random.default_rng(seed)
    P = [voice_print(np.asarray(wav[int(a * SR):int(b * SR)], dtype=np.float32) / (32768.0 if wav.dtype == np.int16 else 1.0)) for a, b in segs]
    ok = [i for i, p in enumerate(P) if p is not None and not np.isnan(p).any()]
    if len(ok) < 6: return [(0, 1)] * len(segs), 0, None
    X = np.array([P[i] for i in ok]); mu, sd = X.mean(0), X.std(0) + 1e-6; Z = (X - mu) / sd
    Z[:, -2:] *= 2.0                                             # pitch counts for more than any one spectral number
    best = (1, None, None, -1.0)
    for k in range(2, max_voices + 1):
        lab, c = _kmeans(Z, k, rng)
        if min(np.bincount(lab, minlength=k)) < max(3, len(Z) * 0.01): continue   # a "voice" with almost no speech is noise
        s = _silhouette(Z, lab, rng)
        within = np.mean([np.sqrt(((Z[lab == j] - c[j]) ** 2).sum(1)).mean() for j in range(k)])
        apart = min(np.sqrt(((c[i] - c[j]) ** 2).sum()) for i in range(k) for j in range(i + 1, k)) / within
        if s > best[3] and apart >= 0.8: best = (k, lab, c, s)
    # careful: one person's voice rises and falls (a lecturer: 0.11 apart, a 0.75 gap), a few people sit further apart
    # (an interview: 0.19, 0.89). More than one voice only when the groups are clearly apart; the zoomed-in listen decides the rest.
    if best[1] is None or best[3] < 0.15:
        return [(1, 6) if p is not None else (0, 1) for p in P], 1, None
    k, lab, c, _ = best
    T = np.eye(Z.shape[1])
    for _ in range(rounds):                                     # self-training: group, learn what tells these voices apart, group again
        T = _wccn(Z, lab, k); lab, c = _kmeans(Z @ T, k, np.random.default_rng(seed))
    Z = Z @ T
    d = np.sqrt(((Z[:, None, :] - c[None]) ** 2).sum(2)); srt = np.sort(d, 1)
    margin = (srt[:, 1] - srt[:, 0]) / (srt[:, 1] + 1e-9)        # how much nearer its own voice than the next: the sure number
    if smooth and len(Z) > 2:                                   # people speak in turns: a lone flip mid-turn costs a switch penalty
        dur = np.array([max(0.5, min(10.0, segs[i][1] - segs[i][0])) for i in ok])
        cost = d * np.sqrt(dur)[:, None]
        pen = smooth * float(np.median(np.abs(d[:, :1] - d[:, 1:2]))) * 2
        acc = cost[0].copy(); back = []
        for n in range(1, len(Z)):
            stay = acc[:, None] + np.where(np.eye(k, dtype=bool), 0.0, pen)
            b = np.argmin(stay, 0); back.append(b); acc = stay[b, np.arange(k)] + cost[n]
        path = [int(np.argmin(acc))]
        for b in reversed(back): path.append(int(b[path[-1]]))
        lab = np.array(path[::-1])
        own = d[np.arange(len(Z)), lab]; other = np.where(np.eye(k, dtype=bool)[lab], np.inf, d).min(1)
        margin = np.clip((other - own) / (other + 1e-9), 0, 1)
    order = {}
    for j in lab:
        order.setdefault(int(j), len(order) + 1)
    out = [(0, 1)] * len(segs)
    for n, i in enumerate(ok):
        out[i] = (order[int(lab[n])], int(1 + round(10 * min(1.0, margin[n] * 2.5))))
    model = {"mu": mu.tolist(), "sd": sd.tolist(), "T": T.tolist(), "centres": [c[j].tolist() for j in sorted(order, key=order.get)]}
    return out, k, model


def match(wav, segs, model):
    """voice numbers for new stretches from a model kept earlier (a focused listen keeps the first pass's numbering)"""
    mu, sd, C = np.array(model["mu"]), np.array(model["sd"]), np.array(model["centres"])
    out = []
    for a, b in segs:
        p = voice_print(np.asarray(wav[int(a * SR):int(b * SR)], dtype=np.float32) / (32768.0 if wav.dtype == np.int16 else 1.0))
        if p is None or np.isnan(p).any(): out.append((0, 1)); continue
        z = (p - mu) / sd; z[-2:] *= 2.0
        if "T" in model: z = z @ np.array(model["T"])
        d = np.sqrt(((z[None] - C) ** 2).sum(1)); o = np.argsort(d)
        m = (d[o[1]] - d[o[0]]) / (d[o[1]] + 1e-9) if len(d) > 1 else 1.0
        out.append((int(o[0]) + 1, int(1 + round(10 * min(1.0, m * 2.5)))))
    return out


def voices_learned(E, max_voices=6, seed=7):
    """the same, from learned voice prints (perch_speaker.py: one unit row per stretch, zeros where none) -> (out, k, model)"""
    rng = np.random.default_rng(seed)
    ok = [i for i in range(len(E)) if np.linalg.norm(E[i]) > 0.5]
    if len(ok) < 6: return [(0, 1)] * len(E), 0, None
    mu = E[ok].mean(0); Z = E[ok] - mu
    best = (1, None, None, -1.0)
    for k in range(2, max_voices + 1):
        lab, c = _kmeans(Z, k, rng)
        if min(np.bincount(lab, minlength=k)) < max(3, len(Z) * 0.01): continue
        s = _silhouette(Z, lab, rng)
        within = np.mean([np.sqrt(((Z[lab == j] - c[j]) ** 2).sum(1)).mean() for j in range(k)])
        apart = min(np.sqrt(((c[i] - c[j]) ** 2).sum()) for i in range(k) for j in range(i + 1, k)) / within
        if s > best[3] and apart >= 0.8: best = (k, lab, c, s)
    # the same careful count as our own prints (tested 5 Nov 2026 with TitaNet: lecture 0.10 apart, gap 0.53 -> one voice;
    # interview 0.19, 0.91 -> two): one voice unless clearly apart
    if best[1] is None or best[3] < 0.15:
        c1 = Z.mean(0, keepdims=True)
        out = [(0, 1)] * len(E)
        for i in ok: out[i] = (1, 6)
        return out, 1, {"kind": "learned", "mu": mu.tolist(), "centres": c1.tolist()}
    k, lab, c, _ = best
    d = np.sqrt(((Z[:, None, :] - c[None]) ** 2).sum(2)); srt = np.sort(d, 1)
    margin = (srt[:, 1] - srt[:, 0]) / (srt[:, 1] + 1e-9)
    order = {}
    for j in lab: order.setdefault(int(j), len(order) + 1)
    out = [(0, 1)] * len(E)
    for n, i in enumerate(ok): out[i] = (order[int(lab[n])], int(1 + round(10 * min(1.0, margin[n] * 2.5))))
    return out, k, {"kind": "learned", "mu": mu.tolist(), "centres": [c[j].tolist() for j in sorted(order, key=order.get)]}


def match_learned(E, model):
    """voice numbers for new learned prints from a kept model (the zoom in keeps the first run's numbers)"""
    mu, C = np.array(model["mu"]), np.array(model["centres"]); out = []
    for e in E:
        if np.linalg.norm(e) < 0.5: out.append((0, 1)); continue
        d = np.sqrt((((e - mu)[None] - C) ** 2).sum(1)); o = np.argsort(d)
        m = (d[o[1]] - d[o[0]]) / (d[o[1]] + 1e-9) if len(d) > 1 else 1.0
        out.append((int(o[0]) + 1, int(1 + round(10 * min(1.0, m * 2.5)))))
    return out
