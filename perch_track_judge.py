# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_track_judge: what a TRACK was, judged by the bot itself from how it moved (and, when given, how it sounded) over time.

No pictures and no one asked to label: a leaf and a bird are told apart the way a person standing at the window does it. A bird moves
by itself while the rest of the view is still, stops and goes, pecks; a flying bird crosses fast and straight with a wing beat. A
leaf moves when everything moves (the wind), tumbles (its size flickers without a beat) and drifts down or with the gust. Foliage
sways back and forth and goes nowhere. Rain streaks fall fast and short. Something that never moves itself was left behind (or is
where a thing stood). Each judgement says its kind, a sure 1..11 (never 0: 6 is the middle) and the reasons, in words.

    python3 perch_track_judge.py tracks.jsonl [--sound ear-lines.txt]     one line per track: id kind sure reasons
    from perch_track_judge import judge;  judge(track_dict, sounds=[...]) -> {"kind", "sure", "why"}
"""
import json, sys

def judge(t, sounds=None):
    s, why = {}, {}
    def vote(kind, w, reason):
        s[kind] = s.get(kind, 0) + w; why.setdefault(kind, []).append(reason)
    secs, still, stopgo = t["secs"], t["still"], t["stopgo"]
    speed, straight, scene = t["speed"], t["straight"], t["scene"]
    flut, fhz, acv, down, area = t["flutter"], t["flutter_hz"], t["area_cv"], t["down"], t["area"]
    path, net = t["path_px"], t["net_px"]
    windy, calm = scene > 0.02, scene < 0.005
    # never moved itself
    if path < 4 and still > 0.9:
        vote("still thing", 3, "it never moved itself (a thing put down, or where something stood)")
    # foliage: in the restless zone (a bush that keeps moving), or back and forth going nowhere while the view moves
    zone = t.get("zone", 0)
    if zone >= 0.5: vote("foliage", 5, f"it lived in a restless patch ({zone:.0%} of it: a bush that keeps moving)")
    if acv > 0.4 and secs < 4 and zone >= 0.3: vote("foliage", 1, "its shape kept changing, briefly")
    # foliage: back and forth, going nowhere, while the view moves
    if path > 20 and straight < 0.2:
        vote("foliage", 1, "back and forth, going nowhere")
        if windy: vote("foliage", 2, "while the whole view was moving (wind)")
    # a leaf: with the wind, tumbling, drifting
    if windy and still < 0.5: vote("leaf", 2, "moved while everything moved (wind)")
    if acv > 0.35 and flut < 0.3: vote("leaf", 1, "its size flickered without a beat (tumbling)")
    if down > 0.6 and straight > 0.5: vote("leaf", 1, "drifted down")
    if down > 0.6 and still < 0.1: vote("leaf", 2, "kept falling, never stopping")
    if flut >= 0.5 and 0 < fhz < 1.5: vote("leaf", 2, f"tumbled over slowly ({fhz:.1f} a second), no wing beat")
    if still < 0.2 and secs < 6 and straight > 0.6 and not (flut >= 0.3 and 2 <= fhz <= 8): vote("leaf", 1, "carried along without stopping")
    # a bird on the ground: by itself, stop and go
    if calm and zone < 0.3: vote("bird on the ground", 1, "moved by itself while the rest of the view was still")
    if 0.25 <= still <= 0.95 and stopgo >= 0.4 and zone < 0.3 and acv < 0.4: vote("bird on the ground", 2, f"stopped and went ({stopgo:.1f} changes a second)")
    if secs >= 4 and net < 0.6 * path and path > 10 and still >= 0.2: vote("bird on the ground", 1, "stayed about one place, moving about it")
    if flut >= 0.3 and fhz >= 1.5 and still > 0.4: vote("bird on the ground", 1, f"a beat in its shape at {fhz:.1f} a second while standing (pecking?)")
    # a bird flying: fast, straight, wing beat
    if speed > 80 and straight > 0.7: vote("bird flying", 2, f"crossed fast and straight ({speed:.0f} px a second)")
    if flut >= 0.3 and 2 <= fhz <= 8 and still < 0.3: vote("bird flying", 2, f"a wing beat: its shape repeated {fhz:.1f} times a second")
    # rain
    if speed > 150 and secs < 0.8 and down > 0.7 and area < 30: vote("rain", 3, "small, fast, falling, gone in a moment")
    # big
    if area > 600: vote("something big (person, cat, fox)", 2, f"big ({area:.0f} px on the 640 view)")
    # the ear on the same clock
    for l in sounds or []:
        w = l.lower()
        if any(k in w for k in ("bird", "chirp", "caw", "coo", "tweet")): vote("bird on the ground", 1, "a bird sound at the same time"); vote("bird flying", 1, "a bird sound at the same time")
        if any(k in w for k in ("wind", "gust", "rustle")): vote("leaf", 1, "wind heard at the same time"); vote("foliage", 1, "wind heard at the same time")
        if "rain" in w: vote("rain", 1, "rain heard at the same time")
    if not s: return {"kind": "not sure what", "sure": 1, "why": ["no clear sign either way"]}
    best = sorted(s.items(), key=lambda kv: -kv[1])
    k, v = best[0]; second = best[1][1] if len(best) > 1 else 0
    sure = max(1, min(11, 3 + 2 * v - 2 * second))           # agreeing signs raise it; signs for something else pull it down
    return {"kind": k, "sure": sure, "why": why[k] + ([f"but also like {best[1][0]}"] if second else [])}

def ear_between(path, a, b):
    out = []
    try:
        for l in open(path):
            p = l.split(None, 1)
            try:
                if a <= float(p[0]) <= b: out.append(p[1].strip())
            except (ValueError, IndexError): pass
    except OSError: pass
    return out

if __name__ == "__main__":
    args = sys.argv[1:]
    ear = args[args.index("--sound") + 1] if "--sound" in args else None
    for l in open(args[0]):
        t = json.loads(l)
        j = judge(t, ear_between(ear, t["from"], t["to"]) if ear else None)
        print(t["id"], j["kind"], f"sure {j['sure']}", "; ".join(j["why"]))
