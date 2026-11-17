#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_ear_known: the ear's KNOWN sounds, learned from examples a person names (a kettle, a boiler, a fridge, a bird).
# The ear (perch-sound) writes a line when the sound changes:  TIME ear L5 tone 1400Hz b 2-3-11-6-5   (loudness 1-11; a kind;
# five bands 1-11). A named EXAMPLE is a stretch of those lines; its SHAPE is kept, never the sound:
#   level   the loudness of its active lines (mean, spread)
#   bands   the mean five-band profile of its active lines
#   kinds   how often each kind (tone, voice?, music?, noise, bang) came
#   length  how long it stayed active
# Then any stretch of new lines can be compared with every known shape; the closest is said WITH how sure (1-11), and only if it is
# close enough: names only ever come from a person's examples, never guessed.
#   python3 perch_ear_known.py label LINES.txt NAME FROM TO [KNOWN.jsonl]    (FROM/TO: unix seconds or HH:MM:SS today)
#   python3 perch_ear_known.py scan  LINES.txt [KNOWN.jsonl] [WINDOW_S]       -> one line per window: I think NAME (sure n/11)
#   python3 perch_ear_known.py ask   LINES.txt [KNOWN.jsonl]                  -> the unnamed sounds it keeps hearing: "what is sound A?"
import datetime as dt, json, math, os, re, sys

KINDS = ("quiet", "tone", "voice?", "music?", "noise", "bang")
LINE = re.compile(r"^(\d+(?:\.\d+)?) ear L(\d+) (\S+)(?: (\d+)Hz)?(?: b ([\d-]+))?")


def read_lines(path):
    out = []
    for l in open(path, encoding="utf-8", errors="replace"):
        m = LINE.match(l.strip())
        if not m: continue
        t, L, kind, hz, b = m.groups()
        bands = [int(x) for x in b.split("-")] if b else None
        out.append({"t": float(t), "L": int(L), "kind": kind, "hz": int(hz) if hz else None, "bands": bands})
    return out


def shape(lines):
    """the shape of a stretch: only its ACTIVE lines (not quiet) count"""
    act = [x for x in lines if x["kind"] != "quiet" and x["bands"]]
    if len(act) < 2: return None
    lv = [x["L"] for x in act]; m = sum(lv) / len(lv)
    bands = [sum(x["bands"][i] for x in act) / len(act) for i in range(5)]
    kinds = {k: sum(1 for x in act if x["kind"] == k) / len(act) for k in KINDS if k != "quiet"}
    return {"level": round(m, 2), "level_spread": round(math.sqrt(sum((v - m) ** 2 for v in lv) / len(lv)), 2),
            "bands": [round(v, 2) for v in bands], "kinds": {k: round(v, 3) for k, v in kinds.items()},
            "length": round(act[-1]["t"] - act[0]["t"], 1), "n": len(act)}


def distance(a, b):
    """0 = the same shape; bigger = further apart. Bands count most (where the sound sits), then kinds, loudness, length."""
    na = math.sqrt(sum(v * v for v in a["bands"])) or 1; nb = math.sqrt(sum(v * v for v in b["bands"])) or 1
    cos = sum(x * y for x, y in zip(a["bands"], b["bands"])) / (na * nb)
    band_d = 1 - cos                                                    # 0..1 (profiles, not loudness)
    kind_d = sum(abs(a["kinds"].get(k, 0) - b["kinds"].get(k, 0)) for k in KINDS) / 2   # 0..1
    lvl_d = abs(a["level"] - b["level"]) / 10                          # 0..1
    len_d = abs(math.log((a["length"] + 1) / (b["length"] + 1))) / 3   # ~0..1
    return 3 * band_d + kind_d + 0.5 * lvl_d + 0.3 * len_d


def sure_of(d):
    """distance -> sure 1..11 (11 = the same shape); said only from 6 up"""
    return max(1, min(11, int(round(11 - d * 12))))


def group_unknown(lines, known_list, window=30.0, near=0.45):
    """the sounds it keeps hearing without a name: windows that match no known sound (sure < 6) are grouped by shape (a window
    joins the closest group if within `near`, else starts a new one). Returns groups [{label: 'A', shape, times: [...]}], the
    biggest first: the ear's questions for a person ("what is sound A?")."""
    groups = []
    if not lines: return groups
    t, end = lines[0]["t"], lines[-1]["t"]
    while t <= end:
        s = shape([x for x in lines if t <= x["t"] < t + window])
        if s and not (known_list and sure_of(min(distance(s, k["shape"]) for k in known_list)) >= 6):
            best = min(groups, key=lambda g: distance(s, g["shape"]), default=None)
            if best is not None and distance(s, best["shape"]) < near:
                best["times"].append(t)
                k = len(best["times"])                       # the group's shape follows its members (running mean)
                best["shape"]["bands"] = [round(b + (x - b) / k, 2) for b, x in zip(best["shape"]["bands"], s["bands"])]
                best["shape"]["level"] = round(best["shape"]["level"] + (s["level"] - best["shape"]["level"]) / k, 2)
            else:
                groups.append({"shape": s, "times": [t]})
        t += window
    groups.sort(key=lambda g: -len(g["times"]))
    for i, g in enumerate(groups): g["label"] = chr(65 + i) if i < 26 else f"Z{i}"
    return groups


def when(s):
    if re.fullmatch(r"\d+(\.\d+)?", s): return float(s)
    h, m, sec = (int(x) for x in s.split(":"))
    return dt.datetime.combine(dt.date.today(), dt.time(h, m, sec)).timestamp()


def label(lines_path, name, frm, to, known_path):
    lines = [x for x in read_lines(lines_path) if when(frm) <= x["t"] <= when(to)]
    s = shape(lines)
    if not s: sys.exit(f"nothing active between {frm} and {to}: no example taken")
    with open(known_path, "a") as f: f.write(json.dumps({"name": name, "from": when(frm), "to": when(to), "shape": s}) + "\n")
    print(f"learned an example of {name!r}: {s}")


def known(known_path):
    return [json.loads(l) for l in open(known_path)] if os.path.exists(known_path) else []


def scan(lines_path, known_path, window=30.0):
    K = known(known_path)
    if not K: sys.exit("no known sounds yet: label an example first")
    L = read_lines(lines_path)
    if not L: return
    t, end = L[0]["t"], L[-1]["t"]
    while t <= end:
        s = shape([x for x in L if t <= x["t"] < t + window])
        if s:
            best = min(K, key=lambda k: distance(s, k["shape"]))
            sure = sure_of(distance(s, best["shape"]))
            at = dt.datetime.fromtimestamp(t).strftime("%H:%M:%S")
            print(f"{at} I think {best['name']} (sure {sure}/11)" if sure >= 6 else f"{at} a sound I do not know (closest {best['name']}, sure {sure}/11)")
        t += window


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["label"] and len(a) >= 5: label(a[1], a[2], a[3], a[4], a[5] if len(a) > 5 else "known-sounds.jsonl")
    elif a[:1] == ["scan"] and len(a) >= 2: scan(a[1], a[2] if len(a) > 2 else "known-sounds.jsonl", float(a[3]) if len(a) > 3 else 30.0)
    elif a[:1] == ["ask"] and len(a) >= 2:
        for g in group_unknown(read_lines(a[1]), known(a[2] if len(a) > 2 else "known-sounds.jsonl"))[:8]:
            if len(g["times"]) < 2: continue
            ts = ", ".join(dt.datetime.fromtimestamp(t).strftime("%H:%M") for t in g["times"][:6])
            print(f"sound {g['label']}: heard {len(g['times'])} times ({ts}{' ...' if len(g['times']) > 6 else ''}): "
                  f"bands {'-'.join(str(round(b)) for b in g['shape']['bands'])}, loudness {g['shape']['level']}: what is it?")
    else: sys.exit(__doc__ or "label LINES NAME FROM TO [KNOWN] | scan LINES [KNOWN] [WINDOW] | ask LINES [KNOWN]")
