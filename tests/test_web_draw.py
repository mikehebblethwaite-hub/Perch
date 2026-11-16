#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch-web: what a screenshot shows, read at the source (16 Nov 2026). Shapes drawn on a canvas (bars, lines, dashes,
shaded lines), faint words flagged by contrast (and plain words never flagged), and the page's dark look on request."""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(HERE, "perch-web")
fails = []


def check(name, ok, got=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"\n  got: {got[:400]}"))
    if not ok: fails.append(name)


def look(name, path, *flags):
    try:
        return subprocess.run([WEB, name, "--mode", "words", *flags, "go", path], capture_output=True, text=True, timeout=90).stdout
    finally:
        subprocess.run([WEB, name, "stop"], capture_output=True, timeout=30)


d = tempfile.mkdtemp(prefix="perch-draw-test-")
page = os.path.join(d, "draw.html")
open(page, "w").write("""<!doctype html><html><head><style>
body{background:#fff;color:#111} .faint{color:#bbb} .big{color:#888;font-size:30px} .onbox{background:#1A2429;color:#E3E9E3}
@media (prefers-color-scheme: dark){body{background:#151d21;color:#e3e9e3} .dk{color:#2a3a40}}
</style></head><body>
<p class=faint>faint words</p><p>plain words</p><p class=big>large grey words</p><p class=onbox>light on a dark box</p><p class=dk>dark only faint</p>
<canvas id=c width=300 height=100></canvas><canvas id=s width=200 height=40></canvas>
<script>
document.body.insertAdjacentHTML('beforeend', '<p>' + (matchMedia('(prefers-color-scheme: dark)').matches ? 'look is dark' : 'look is light') + '</p>');
var g = document.getElementById('c').getContext('2d'); g.fillStyle = '#ffffff'; g.fillRect(0, 0, 300, 100);
g.fillStyle = '#2e7a3a'; [10, 40, 25, 70].forEach(function (h, i) { g.fillRect(10 + i * 30, 100 - h, 20, h); });
g.setLineDash([4, 4]); g.strokeStyle = '#000'; g.beginPath(); g.moveTo(0, 50); g.lineTo(300, 50); g.stroke(); g.setLineDash([]);
g.beginPath(); g.moveTo(0, 90); g.lineTo(100, 60); g.lineTo(200, 80); g.lineTo(300, 20); g.strokeStyle = '#e5b421'; g.stroke();
var s = document.getElementById('s').getContext('2d'), v = [5, 20, 10, 30];
s.beginPath(); v.forEach(function (y, i) { s.lineTo(i * 60, 40 - y); }); s.lineTo(180, 40); s.lineTo(0, 40); s.closePath(); s.globalAlpha = .2; s.fillStyle = '#1d5fc4'; s.fill();
s.globalAlpha = 1; s.beginPath(); v.forEach(function (y, i) { i ? s.lineTo(i * 60, 40 - y) : s.moveTo(0, 40 - y); }); s.strokeStyle = '#1d5fc4'; s.stroke();
</script></body></html>""")

out = look("drawtest", page)
lines = out.splitlines()
t = lambda w: next((l for l in lines if l.startswith("t ") and f"'{w}'" in l), "")
check("bars: four, heights as drawn, base at the bottom", "bars #2e7a3a 4: base 0, x 10-100, heights 10 40 25 70" in out, out)
check("the white background fill is not a bar", "bars #ffffff" not in out, out)
check("dashed line said as dashed, level", "line #000000 dashed 2 pts: 0,50 to 300,50" in out, out)
check("line points counted up from the bottom", "line #e5b421 4 pts: x 0-300, y 10 40 20 80" in out, out)
check("shaded line said once, as shaded", out.count("#1d5fc4") == 1 and "line #1d5fc4 shaded 4 pts" in out, out)
check("faint words flagged", "!contrast 1.9" in t("faint words"), t("faint words"))
check("plain words not flagged", t("plain words") != "" and "contrast" not in t("plain words"), t("plain words"))
check("large grey words: the large-word rule (3), not flagged", t("large grey words") != "" and "contrast" not in t("large grey words"), t("large grey words"))
check("light words on their own dark box: not flagged", t("light on a dark box") != "" and "contrast" not in t("light on a dark box"), t("light on a dark box"))
check("the page's light look by default", "'look is light'" in out and "contrast" not in t("dark only faint"), out)

out = look("drawtest", page, "--scheme", "dark")
lines = out.splitlines()
check("--scheme dark: the page's dark look", "'look is dark'" in out, out)
check("--scheme dark: words faint only in dark are flagged", "!contrast" in t("dark only faint"), t("dark only faint"))
check("--scheme dark: plain words still fine", "contrast" not in t("plain words"), t("plain words"))

bad = subprocess.run([WEB, "drawtest", "--scheme", "purple", "go", page], capture_output=True, text=True, timeout=60).stdout
subprocess.run([WEB, "drawtest", "stop"], capture_output=True, timeout=30)
check("a wrong --scheme is refused, naming the choices", "scheme: light | dark" in bad, bad)

print("ALL PASS" if not fails else f"{len(fails)} FAILED: {', '.join(fails)}")
sys.exit(1 if fails else 0)
