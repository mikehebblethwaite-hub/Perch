#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_sound (the ear) on real WAV files written here: an empty or unfinished file says nothing (no crash); silence reads quiet;
# a steady 440 Hz tone reads as a tone near 440.
import math, os, struct, sys, tempfile, wave
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import perch_sound as S
ok = 0; n = 0
def check(name, cond):
    global ok, n
    n += 1; ok += bool(cond); print(("PASS " if cond else "FAIL ") + name)
def wav(path, secs, f=None, amp=0.5, rate=16000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(32767 * amp * math.sin(2 * math.pi * f * i / rate)) if f else 0) for i in range(int(secs * rate))))
d = tempfile.mkdtemp()
empty = os.path.join(d, "empty.wav"); open(empty, "wb").close()
check("an empty file (a clip still being written) gives no lines and no crash", S.lines(empty) == [])
quiet = os.path.join(d, "quiet.wav"); wav(quiet, 3)
q = S.lines(quiet)
check("silence reads quiet", q and all("quiet" in l for _, l in q))
tone = os.path.join(d, "tone.wav"); wav(tone, 3, 440)
t = " ".join(l for _, l in S.lines(tone))
hz = [int(x) for x in __import__("re").findall(r"tone (\d+)Hz", t)]
check(f"a 440 Hz tone reads as a tone near 440 (read: {t[:60]!r})", hz and abs(hz[0] - 440) <= 20)
print(f"{ok}/{n} pass"); sys.exit(0 if ok == n else 1)
