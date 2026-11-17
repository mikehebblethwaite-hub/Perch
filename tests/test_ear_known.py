#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_ear_known on MADE-UP ear lines (no real recordings): a named example is learned by its shape; the same kind of sound later
# is named with a high sure; a different sound is NOT given that name (said as unknown); quiet alone gives no shape.
import os, random, sys, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import perch_ear_known as K
ok = n = 0
def check(name, cond):
    global ok, n
    n += 1; ok += bool(cond); print(("PASS " if cond else "FAIL ") + name)
r = random.Random(3)
def stretch(t0, secs, bands, kind, L):
    out = []
    for i in range(int(secs)):
        b = "-".join(str(max(1, min(11, v + r.randint(-1, 1)))) for v in bands)
        out.append(f"{t0 + i:.2f} ear L{max(1, min(11, L + r.randint(-1, 1)))} {kind} b {b}")
        out.append(f"{t0 + i + 0.5:.2f} ear L1 quiet")
    return out
d = tempfile.mkdtemp(); lines = os.path.join(d, "ear.txt"); kn = os.path.join(d, "known.jsonl")
rush = [9, 11, 10, 10, 6]; whistle = [2, 3, 11, 6, 5]
L = stretch(1000, 120, rush, "music?", 4) + stretch(2000, 20, whistle, "tone", 5) + stretch(3000, 90, rush, "music?", 4) + \
    [f"{4000 + i:.2f} ear L1 quiet" for i in range(60)]
open(lines, "w").write("\n".join(L) + "\n")
K.label(lines, "kettle", "1000", "1120", kn)
check("an example is learned and kept as a shape (no sound)", json.loads(open(kn).read().splitlines()[0])["shape"]["bands"])
same = K.shape([x for x in K.read_lines(lines) if 3000 <= x["t"] < 3090])
diff = K.shape([x for x in K.read_lines(lines) if 2000 <= x["t"] < 2020])
ex = K.known(kn)[0]["shape"]
check(f"the same kind of sound later is named, sure >= 8 (got {K.sure_of(K.distance(same, ex))})", K.sure_of(K.distance(same, ex)) >= 8)
check(f"a different sound (a whistle) is NOT called kettle: sure < 6 (got {K.sure_of(K.distance(diff, ex))})", K.sure_of(K.distance(diff, ex)) < 6)
check("quiet alone has no shape (nothing to name)", K.shape([x for x in K.read_lines(lines) if x["t"] >= 4000]) is None)
print(f"{ok}/{n} pass"); sys.exit(0 if ok == n else 1)
