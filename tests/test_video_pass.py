#!/usr/bin/python3 -I
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# tests/test_video_pass.py [VIDEO]: perch-video's one-decode pass, checked on a real video (the four things that broke it once):
#   1. a short run (--to) finishes, not hanging on the decoder        2. the grey copy is exactly what plain ffmpeg gives
#   3. the first frame's words are read (at 0:00.0)                   4. a run from the middle (--from/--to) stays inside its stretch
# VIDEO: any video with words on its first frame; default Steamboat Willie (public domain in the US), skipped if not here.
import os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PV = os.path.join(HERE, "perch-video")
src = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.cache/perch-models/test_video/steamboat/steamboat-willie-1928.webm")
if not os.path.isfile(src):
    print(f"skip: no test video at {src}"); sys.exit(0)
bad = []


def run(*args, limit=600):
    t = time.time()
    try:
        r = subprocess.run([PV, "--no-sound", *args, src], capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired:
        return None, time.time() - t
    return r.stdout.splitlines(), time.time() - t


# 1 + 3: a 20 s run finishes in good time and reads the first frame
out, took = run("--to", "20", limit=300)
if out is None: bad.append(f"1: a 20 s run did not finish in {took:.0f} s (the decoder was waited on)")
else:
    print(f"1 ok: 20 s run finished in {took:.1f} s")
    if any(l.startswith("text 0:00.0 ") for l in out): print("3 ok: words read at 0:00.0")
    else: bad.append("3: no words read on the first frame (0:00.0)")
# 2: the grey copy in the one-decode pass is byte for byte what a plain grey decode gives
pix = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", src],
                     capture_output=True, text=True).stdout.strip() or "yuv420p"
plain = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-t", "20", "-i", src, "-an", "-vf", "fps=4,scale=64:36,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True).stdout
split = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-t", "20", "-i", src, "-an", "-filter_complex",
                        f"[0:v]fps=4,format={pix},split=2[a][b];[a]scale=64:36,format=gray[g];[b]format=rgb24,nullsink",
                        "-map", "[g]", "-f", "rawvideo", "-"], capture_output=True).stdout
if plain and plain == split: print(f"2 ok: grey copy identical ({len(plain) // 2304} frames)")
else: bad.append(f"2: the grey copy differs from a plain decode ({len(plain)} against {len(split)} bytes)")
# 4: a stretch from the middle stays inside it
out, took = run("--from", "60", "--to", "75", limit=300)
if out is None: bad.append(f"4: a 15 s stretch did not finish in {took:.0f} s")
else:
    times = [m.group(1) for l in out if (m := re.match(r"(?:cut|text|moves) (\d+:\d+\.\d)", l))]
    def secs(v): return sum(float(x) * 60 ** i for i, x in enumerate(reversed(v.split(":"))))
    outside = [x for x in times if not 59.9 <= secs(x) <= 75.1]
    if outside: bad.append(f"4: lines outside 1:00-1:15: {outside[:5]}")
    else: print(f"4 ok: {len(times)} lines, all inside 1:00-1:15 ({took:.1f} s)")
print("FAIL\n  " + "\n  ".join(bad) if bad else "all 4 passed")
sys.exit(1 if bad else 0)
