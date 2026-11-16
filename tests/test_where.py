#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_where: where we are by elimination of what was seen (never GPS). Honest: a tree and a squirrel rule out desert and beach;
red + grey squirrel leave Britain, Ireland and Italy; a red squirrel alone keeps Europe and northern Asia, not America. Tricks: one
shaky sighting (a 'kangaroo?' at sure 3) cannot throw out what a sure one says; sightings that cannot be true together are said so;
a thing nothing is known about says nothing (it never narrows the world); a doubted branch's report is not used."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import perch_where as W

fails, n = [], 0
def check(name, got, want):
    global n; n += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

def ans(*seen):
    w = W.Where()
    for what, sure in seen: w.add(what, sure)
    return w.answer()

a = ans(("tree", 9), ("squirrel", 7))
check("a thing nothing is known about ('squirrel', no colour) says nothing", [s["what"] for s in a["seen"]], ["tree"])
a = ans(("tree", 9), ("red squirrel", 7))
check("tree + red squirrel: not a desert, not a beach", {"desert", "beach"} <= set(a["habitat_out"]), True)
check("...Britain fits, the United States does not", ("GB" in a["fits"], "US" in a["fits"]), (True, False))
a = ans(("red squirrel", 7), ("grey squirrel", 7))
check("red + grey squirrel: Britain, Ireland, Italy", a["fits"], ["GB", "IE", "IT"])
check("...all sightings agree", a["all_agree"], True)
a = ans(("red squirrel", 8), ("kangaroo", 3))
check("trick: a shaky kangaroo (3) does not throw out the sure red squirrel's Britain", ("GB" in a["fits"], "AU" in a["fits"]), (True, False))
check("...and it is said that one sighting is probably wrong", ("probably wrong" in a["said"], a["all_agree"]), (True, False))
a = ans(("red squirrel", 7), ("kangaroo", 7))
check("equally sure and impossible together: both kept as the best fit", ("GB" in a["fits"], "AU" in a["fits"], a["all_agree"]), (True, True, False))
check("nothing seen: nothing said", ans()["fits"], [])
check("the countries are the place list's own (252)", len(W.Where().countries) >= 240, True)

# through the tree: a branch (or a twig's agent) reports what a thing IS; the trunk narrows where
import tempfile, shutil, numpy as np, subprocess
import perch_loop as L
def sees_red(p): return [{"is": "red squirrel", "sure": 7}], []
def sees_grey(p): return [{"is": "grey squirrel", "sure": 7}], []
def look_two(p): return [{"summary": "a park"}], [{"kind": "animal", "why": "an animal", "score": 5}, {"kind": "animal2", "why": "another", "score": 5}]
d = tempfile.mkdtemp(prefix="perch-where-")
try:
    v = os.path.join(d, "one.mkv")
    p = subprocess.Popen(["ffmpeg", "-v", "quiet", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "160x120", "-r", "25", "-i", "-", "-c:v", "ffv1", v], stdin=subprocess.PIPE)
    p.stdin.write(np.full((120, 160, 3), 100, np.uint8).tobytes() * 2); p.stdin.close(); p.wait()
    rows = L.Trunk(v, os.path.join(d, "b1"), examiners={"look": look_two, "animal": sees_red, "animal2": sees_grey}).run()
    wh = [r for r in rows if r["type"] == "where"]
    check("tree: two branches report red and grey squirrels; the trunk's last word is Britain, Ireland, Italy", wh[-1]["fits"] if wh else None, ["GB", "IE", "IT"])
    rows = L.Trunk(v, os.path.join(d, "b2"), examiners={"look": look_two}).run()
    check("...the animals are twigs when no agent can name them yet", sum(1 for r in rows if r["type"] == "twig" and r["kind"].startswith("animal")), 2)
    L.grow(os.path.join(d, "b2"), {"animal": sees_red, "animal2": sees_grey})
    wh = [r for r in L.Board(os.path.join(d, "b2")).read() if r["type"] == "where"]
    check("grow: the twigs' agents report later, and where is narrowed then", wh[-1]["fits"] if wh else None, ["GB", "IE", "IT"])
finally:
    shutil.rmtree(d)

print(f"{n - len(fails)}/{n} pass")
sys.exit(1 if fails else 0)
