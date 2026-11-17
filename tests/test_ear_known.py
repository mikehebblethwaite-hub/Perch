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
# the unnamed sounds it keeps hearing: three repeats of a hum and two of a chirp, plus the known kettle, give two groups to ask about
hum = [3, 6, 5, 2, 1]; chirp = [1, 2, 4, 9, 11]
L2 = []
for i, base in enumerate((5000, 5300, 5600)): L2 += stretch(base, 30, hum, "noise", 3)
for base in (6000, 6300): L2 += stretch(base, 30, chirp, "tone", 6)
L2 += stretch(7000, 60, rush, "music?", 4)
lines2 = os.path.join(d, "ear2.txt"); open(lines2, "w").write("\n".join(L2) + "\n")
G = K.group_unknown(K.read_lines(lines2), K.known(kn))
big = [g for g in G if len(g["times"]) >= 2]
check(f"repeated unnamed sounds form groups to ask about: 2 groups (got {len(big)}: {[len(g['times']) for g in big]})", len(big) == 2)
check("the biggest group (the hum, 3 times) is 'sound A'", big and big[0]["label"] == "A" and len(big[0]["times"]) >= 3)
check("the known kettle is NOT asked about", all(abs(g["shape"]["bands"][0] - rush[0]) > 2 or abs(g["shape"]["bands"][4] - rush[4]) > 2 for g in big))
print(f"{ok}/{n} pass"); sys.exit(0 if ok == n else 1)
