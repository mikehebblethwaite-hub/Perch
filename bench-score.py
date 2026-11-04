# Copyright (c) 2026 Mike J Hebblethwaite
# Licensed under the GNU AGPL-3.0 or a commercial licence - see LICENSE.md
"""Score a perch-bench run: python3 bench-score.py tape/bench-HHMMSS"""
import sys, os
d = sys.argv[1]
HZ = os.sysconf("SC_CLK_TCK")
img_tokens = lambda w, h: max(1, round(w * h / 750))        # Claude's image cost: w*h/750 tokens
truth = [l.split() for l in open(f"{d}/truth.txt")]
t_start = float(truth[0][0]); t_end = float(truth[-1][0])
events = [(float(t), int(x), int(y), int(w), int(h), n) for t, x, y, w, h, n in truth if n not in ("start", "end")]
lines = []
for l in open(f"{d}/perch.txt"):
    p = l.split()
    if len(p) < 3 or p[2] != "S": continue
    areas = []
    rest = l.split("|", 1)[1].split()
    for i in range(0, len(rest), 2):
        x, y = map(int, rest[i].split(",")); w, h = map(int, rest[i + 1].split("x")); areas.append((x, y, w, h))
    text = l.split(" ", 2)[2].strip()
    lines.append((float(p[0]), float(p[1]), areas, text))
shots = [(float(a), int(b), int(c), int(e)) for a, b, c, e in (l.split() for l in open(f"{d}/shots.txt"))]
shots = [s for s in shots if t_start - 0.5 <= s[0] <= t_end]
hit = lambda a, e: a[0] < e[1] + e[3] and e[1] < a[0] + a[2] and a[1] < e[2] + e[4] and e[2] < a[1] + a[3]
rows = []; used = set()
for e in events:
    pl = next(((i, L) for i, L in enumerate(lines) if L[0] >= e[0] - 0.05 and L[0] <= e[0] + 3 and any(hit(a, e) for a in L[2])), None)
    if pl: used.add(pl[0])
    over = lambda f: f[1] < e[1] + e[3] and e[1] < f[1] + f[3] and f[2] < e[2] + e[4] and e[2] < f[2] + f[4]
    gone = next((f[0] for f in events if f[0] > e[0] and over(f)), float("inf"))   # the next change to the same spot undoes it
    sh = next((s for s in shots if e[0] <= s[0] < gone), None)                       # a shot only sees it while it is there
    rows.append((e[5], (pl[1][1] - e[0]) if pl else None, (sh[0] - e[0]) if sh and sh[0] - e[0] < 3 else None))
false = [L for i, L in enumerate(lines) if i not in used and not any(abs(L[0] - e[0]) < 3 and any(hit(a, e) for a in L[2]) for e in events)]
def cpu(f):
    u, s = open(f).read().split()[:2]; return float(u) + float(s)
e0, e1, t0, t1 = map(int, open(f"{d}/perch.cpu").read().split())
perch_cpu = (e1 - e0 + t1 - t0) / HZ + cpu(f"{d}/wait.cpu")
shot_cpu = cpu(f"{d}/shot.cpu")
secs = t_end - t_start
tok_lines = sum(max(1, round(len(L[3]) / 4)) for L in lines)
tok_crops = sum(img_tokens(a[2], a[3]) for L in lines for a in L[2])
tok_shots = sum(img_tokens(s[2], s[3]) for s in shots)
print(f"# bench {os.path.basename(d)}: {secs:.0f} s scripted screen, {len(events)} events")
print("event          perch_notice_s  screenshot_notice_s")
for n, p, s in rows: print(f"{n:<14} {('%.2f' % p) if p is not None else 'MISSED':>14}  {('%.2f' % s) if s is not None else 'MISSED':>19}")
caught_p = sum(r[1] is not None for r in rows); caught_s = sum(r[2] is not None for r in rows)
print(f"caught         {caught_p}/{len(rows):<12}  {caught_s}/{len(rows)}   (screenshot: a shot taken while it was there; the model must still spot it)")
print(f"false alarms   {len(false)}")
print(f"tokens         perch lines {tok_lines}, lines+crops {tok_lines + tok_crops}   screenshots {tok_shots} ({len(shots)} shots)")
print(f"cpu seconds    perch {perch_cpu:.2f} (eye+tap+wait)   screenshots {shot_cpu:.2f}   ({100*perch_cpu/secs:.2f}% vs {100*shot_cpu/secs:.2f}% of a core)")
print(f"bytes to bot   perch {sum(len(L[3]) for L in lines)} text   screenshots {sum(s[1] for s in shots)} PNG")
