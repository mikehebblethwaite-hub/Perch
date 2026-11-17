#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_ear_unmix on MADE-UP sound (written here, nothing recorded): a low rumble A and a 2 kHz tone B are learned; their mix at
# 70/30 power comes back near 70/30 with little leftover; a NEW 4.5 kHz tone C added on top shows up as leftover, not as A or B.
import math, os, random, struct, sys, tempfile, wave
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import perch_ear_unmix as U
ok = n = 0
def check(name, cond):
    global ok, n
    n += 1; ok += bool(cond); print(("PASS " if cond else "FAIL ") + name)
R, S = 16000, 4
r = random.Random(11)
rumble_f = [(f, r.random() * 6.28) for f in range(200, 600, 7)]
def rumble(i): return sum(math.sin(2 * math.pi * f * i / R + ph) for f, ph in rumble_f) / math.sqrt(len(rumble_f) / 2)   # power 1, as the tone
def tone(f): return lambda i: math.sqrt(2) * math.sin(2 * math.pi * f * i / R)
def write(path, parts):                          # parts: [(power, fn)]: amplitudes from powers, so powers add
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(R)
        w.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(6000 * sum(math.sqrt(p) * fn(i) for p, fn in parts))))) for i in range(R * S)))
d = tempfile.mkdtemp(); P = lambda n: os.path.join(d, n)
write(P("a.wav"), [(1, rumble)]); write(P("b.wav"), [(1, tone(2000))])
write(P("mix.wav"), [(0.7, rumble), (0.3, tone(2000))]); write(P("mix3.wav"), [(0.5, rumble), (0.2, tone(2000)), (0.3, tone(4500))])
K = {"A": U.mean_print(U.prints(P("a.wav"))), "B": U.mean_print(U.prints(P("b.wav")))}
check("64-band fingerprints are made every half second", len(U.prints(P("a.wav"))) == 8 and len(K["A"]) == 64)
sh = [U.unmix_one(p, K) for _, p in U.prints(P("mix.wav"))]
a = sum(s["A"] for s, _ in sh) / len(sh); b = sum(s["B"] for s, _ in sh) / len(sh); left = sum(l for _, l in sh) / len(sh)
check(f"a 70/30 mix comes back near 70/30 (A {a:.2f}, B {b:.2f}) with little leftover ({left:.2f})", abs(a - 0.7) < 0.1 and abs(b - 0.3) < 0.1 and left < 0.1)
sh3 = [U.unmix_one(p, K) for _, p in U.prints(P("mix3.wav"))]
a3 = sum(s["A"] for s, _ in sh3) / len(sh3); b3 = sum(s["B"] for s, _ in sh3) / len(sh3); l3 = sum(l for _, l in sh3) / len(sh3)
check(f"a NEW tone on top shows as leftover near its 30% (leftover {l3:.2f}; A {a3:.2f}, B {b3:.2f})", 0.2 < l3 < 0.4 and abs(a3 - 0.5) < 0.12 and abs(b3 - 0.2) < 0.1)
sil = os.path.join(d, "quiet.wav"); write(sil, [])
qs = [U.unmix_one(p, K) for _, p in U.prints(sil)]
check(f"silence: nothing claimed (fingerprints {len(qs)}; shares {[round(sum(s.values()), 3) for s, _ in qs][:3]})", all(sum(s.values()) < 0.05 for s, _ in qs))
# a sound with STAGES (first a 1 kHz tone, then a 3 kHz tone): one average fingerprint explains each stage only half; its stages
# explain each moment whole; and below the quiet floor nothing is shared out
write(P("staged.wav"), [])
with wave.open(P("staged.wav"), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(R)
    w.writeframes(b"".join(struct.pack("<h", int(6000 * (tone(1000) if i < R * 2 else tone(3000))(i))) for i in range(R * S)))
SP = U.prints(P("staged.wav"))
one = {"S": [U.mean_print(SP)]}; staged = {"S": U.stages([p for _, p in SP], 2)}
early = [p for t, p in SP if t < 1.5]
m1 = sum(U.unmix_named(p, one)[0].get("S", 0) for p in early) / len(early)
m2 = sum(U.unmix_named(p, staged)[0].get("S", 0) for p in early) / len(early)
check(f"one average fingerprint explains a stage only partly ({m1:.2f}); its stages explain it whole ({m2:.2f})", m1 < 0.7 and m2 > 0.9)
check("below the quiet floor nothing is shared out", U.unmix_named([1e-9] * 64, staged, floor=1.0) == ({}, 0.0))
check("one stage asked for gives the mean, not a crash (a short sound: a cough)", len(U.stages([p for _, p in SP], 1)) == 1 and U.stages([], 3) == [])
print(f"{ok}/{n} pass"); sys.exit(0 if ok == n else 1)
