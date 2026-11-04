# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_sound: sound as a few numbers a bot can read, not 16,000 samples a second (the thought: a ZX81 tape held a whole program
# in a few kilobytes of tones). Used by perch-video and perch-sound. Everything here on the machine; numbers, no model.
#   s 0:03.2 L7 tone 440Hz b 2-9-3-1-1      a line when the sound changes:
#     L1..L11   how loud (1 = silence, 11 = as loud as it goes; Mike's scales run 1 to 11, never 0)
#     kind      quiet | tone NHz (one steady pitch) | voice? (speech-like) | music? (tones that change) | noise (hiss, wind,
#               water, a crowd) | bang (a sudden loud start: a knock, a drop, a shot)
#     b a-b-c-d-e  how the sound sits in five bands (low < 250 Hz < 1 kHz < 3 kHz < 6 kHz < high), each 1..11
# A line repeats nothing: it comes only when the kind, the loudness (by 2 or more) or the bands change.
import subprocess

import numpy as np

RATE, HOP, WIN = 16000, 0.1, 0.5                 # 16 kHz mono; a look every 0.1 s over the last 0.5 s
EDGES = [0, 250, 1000, 3000, 6000, 8000]


def scale(x, lo, hi):
    """x between lo and hi -> 1..11"""
    return int(1 + round(10 * min(1.0, max(0.0, (x - lo) / (hi - lo)))))


def samples(src, start=0.0, end=None):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"] + (["-ss", str(start)] if start else []) + ["-i", src] + \
          (["-t", str(end - start)] if end else []) + ["-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=1800).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def describe(x, prev_db):
    """one window of samples -> (L, kind, bands, db)"""
    rms = float(np.sqrt(np.mean(x * x)) + 1e-9); db = 20 * np.log10(rms)
    L = scale(db, -60, 0)
    if L <= 2: return L, "quiet", None, db
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    e = np.array([spec[(f >= a) & (f < b)].sum() for a, b in zip(EDGES, EDGES[1:])]) + 1e-12
    edb = 10 * np.log10(e / e.max())
    bands = [scale(v, -40, 0) for v in edb]
    flat = float(np.exp(np.mean(np.log(spec[1:] + 1e-12))) / (np.mean(spec[1:]) + 1e-12))     # 1 = noise, near 0 = tones
    peak = float(f[1:][np.argmax(spec[1:])])
    # how the loudness moves inside the window: speech rises and falls 3-8 times a second
    seg = x[: len(x) // 400 * 400].reshape(-1, 400)                  # 25 ms pieces
    env = np.sqrt((seg ** 2).mean(axis=1)) + 1e-9
    wobble = float(np.std(20 * np.log10(env)))
    head = float(20 * np.log10(np.sqrt(np.mean(x[: int(RATE * HOP)] ** 2)) + 1e-9))
    if prev_db is not None and head - prev_db > 15: kind = "bang"
    elif flat > 0.3: kind = "noise"
    elif flat < 0.02 and wobble < 2: kind = f"tone {round(peak, -1):.0f}Hz"
    elif wobble > 6 and e[1:3].sum() / e.sum() > 0.6: kind = "voice?"
    else: kind = "music?"
    return L, kind, bands, db


def lines(src, start=0.0, end=None, ts=None):
    """the sound of src as (time, text) pairs, only when it changes"""
    x = samples(src, start, end)
    w, h = int(WIN * RATE), int(HOP * RATE)
    n = len(x) // h
    if n < 1: return []
    hop_db = 20 * np.log10(np.sqrt((x[: n * h].reshape(n, h) ** 2).mean(axis=1)) + 1e-9)   # each 0.1 s slice's loudness
    out, last, pending, onset, dated = [], None, None, None, False
    if hop_db[0] > -40: onset = start                                      # sound from the very first moment: it starts here
    for k in range(n):
        t = start + (k + 1) * HOP
        jump = k and hop_db[k] - hop_db[k - 1] > 15 and hop_db[k] > -40      # a sudden loud start against the slice before
        gone = jump and min(hop_db[k + 1:k + 4], default=-99) < hop_db[k] - 15  # ... and over within 0.3 s: a bang (a knock)
        if jump and not gone: onset, dated = t - HOP, False                 # the start of a sound that carries on
        if onset is not None and t - onset > WIN + HOP: onset = None        # the window holds only the new sound now
        if gone:
            if not (last and last[1] == "bang" and t - last[4] < 0.5):
                b = describe(x[k * h:(k + 1) * h], None)
                out.append((t - HOP, f"s {ts(t - HOP) if ts else round(t - HOP, 1)} L{b[0]} bang" + (f" b {'-'.join(map(str, b[2]))}" if b[2] else "")))
                last = ("bang", "bang", b[0], b[2], t)
            continue
        lo = max(0, (k + 1) * h - w)
        if onset is not None: lo = max(lo, int(round((onset - start) * RATE)))   # judge a new sound on itself, not the silence before
        win = x[lo:(k + 1) * h]
        if len(win) < w and onset is None: win = np.pad(win, (w - len(win), 0))   # never pad a fresh sound with silence
        L, kind, bands, db = describe(x[k * h:(k + 1) * h], None) if hop_db[k] < -48 else describe(win, None)
        if last and last[1] == "bang" and kind != "quiet" and t - last[4] < WIN + HOP: continue   # the bang's own tail
        changed = last is None or kind != last[1] or abs(L - last[2]) >= 2 or \
                  (bands and last[3] and max(abs(a - b) for a, b in zip(bands, last[3])) >= 3)
        if not changed: pending = None; continue
        # a new kind must hold for two slices (the edge between two sounds is neither); quiet counts at once
        if kind != "quiet" and (pending is None or pending[0] != kind):
            pending = (kind, t); continue
        t0 = pending[1] if pending and pending[0] == kind else t
        if onset is not None and not dated and t0 - onset <= 0.9: t0 = onset + HOP   # it started at the onset, not when the window filled
        pending = None; dated = True
        out.append((t0 - HOP, f"s {ts(t0 - HOP) if ts else round(t0 - HOP, 1)} L{L} {kind}" + (f" b {'-'.join(map(str, bands))}" if bands else "")))
        last = (kind.split()[0], kind, L, bands, t)
    return out
