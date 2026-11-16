#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_sky: day and time from natural instant clues. The maths is checked against almanac values (midsummer noon at Greenwich;
the January 2024 new, first-quarter, full and last-quarter moons). The reading: tricks (a dark indoor texture, high-ISO noise,
a lamp in a dark room, a phone screen's brightness) must not be stars, moons or suns; honest cases (a starry sky, a waxing and a
waning crescent, a full moon, a midday sun in a blue sky) must be read. The referee's clock: GPS time, the camera's offset, the
place's time zone (summer time included)."""
import datetime as dt, os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image, ImageDraw
import perch_sky as s

fails, n = [], 0
def check(name, got, want):
    global n; n += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok: fails.append(name)
U = lambda x: dt.datetime.fromisoformat(x.replace("Z", "+00:00"))

# ---- the clockwork, against almanac values ----
sn = s.sun(U("2024-06-21T12:02:00Z"), 51.4769, 0.0)
check("midsummer noon, Greenwich: sun 61.96 deg high (90 - 51.48 + 23.44)", abs(sn["elevation"] - 61.96) < 0.15, True)
check("...and due south", abs(sn["azimuth"] - 180) < 1.0, True)
check("midnight in London, midwinter: night", s.light_of(s.sun(U("2024-12-21T00:00:00Z"), 51.5, -0.1)["elevation"]), "night")
check("sunrise side: 08:30 UTC, 21 Dec, London sun low in the SE",
      (s.light_of(s.sun(U("2024-12-21T08:30:00Z"), 51.5, -0.1)["elevation"]), 120 < s.sun(U("2024-12-21T08:30:00Z"), 51.5, -0.1)["azimuth"] < 150), ("sun low", True))
for when, lit, wax, name in [("2024-01-11T11:57:00Z", 0.0, None, "new moon"), ("2024-01-18T03:52:00Z", 0.5, True, "first quarter"),
                             ("2024-01-25T17:54:00Z", 1.0, None, "full moon"), ("2024-02-02T23:18:00Z", 0.5, False, "last quarter")]:
    m = s.moon(U(when), 51.5, 0.0)
    check(f"{name} {when[:10]}: lit {lit}", abs(m["lit"] - lit) < 0.03, True)
    if wax is not None: check(f"{name}: {'waxing' if wax else 'waning'}", m["waxing"], wax)

d = tempfile.mkdtemp(prefix="perch-sky-")
try:
    def save(name, arr_or_im, exif=None):
        im = arr_or_im if isinstance(arr_or_im, Image.Image) else Image.fromarray(np.clip(arr_or_im, 0, 255).astype(np.uint8))
        p = os.path.join(d, name)
        if exif:
            ex = Image.Exif()
            for k, v in exif.items(): ex[k] = v
            im.save(p, exif=ex, quality=95)
        else:
            im.save(p, quality=95)
        return p
    night = {0x8769: {0x829A: 15.0, 0x829D: 1.8, 0x8827: 1600, 0x9003: "2023:08:12 23:40:00"}}      # EV about -2: night
    room = {0x8769: {0x829A: 1 / 30, 0x829D: 1.9, 0x8827: 160, 0x9003: "2017:01:31 11:13:14"}}       # EV about 6: a lit room
    rng = np.random.default_rng(7)

    # a starry sky: dark, even, small soft points; dark land at the bottom
    a = rng.normal(10, 2, (600, 800, 3))
    for _ in range(60):
        y, x = rng.integers(5, 380), rng.integers(5, 795)
        a[y - 1:y + 2, x - 1:x + 2] += 120; a[y, x] += 100
    a[450:] = 5
    r = s.read(save("stars.jpg", a, night))
    check("honest: a starry sky reads as night, sure 9", (r["light"], r["sure"]), ("night", 9))
    check("...about 60 stars counted", 50 <= r["stars"]["count"] <= 62, True)

    # trick: the same sky photographed with a lit-room exposure (the light meter says it was not night)
    check("trick: dark speckle with a lit-room exposure is not stars", s.read(save("speckle.jpg", a, room))["stars"]["count"], 0)
    # trick: high-ISO noise only (single pixels): no stars
    b = rng.normal(10, 2, (600, 800, 3)); m = rng.random((600, 800)) < 0.002; b[m] += 150
    check("trick: high-ISO noise (single hot pixels) is not stars", s.read(save("noise.jpg", b, night))["stars"]["count"] < 5, True)
    # trick: a dark indoor texture (uneven, mid-dark): no stars
    c = rng.normal(55, 25, (600, 800, 3))
    check("trick: a dark indoor texture is not stars", s.read(save("texture.jpg", c))["stars"]["count"], 0)

    def moon_pic(name, lit_right, full=False):
        im = Image.new("RGB", (800, 600), (8, 8, 12)); dr = ImageDraw.Draw(im)
        dr.ellipse((370, 170, 430, 230), fill=(235, 235, 225))
        if not full:                                     # a crescent: a dark disc over most of it, leaving one side lit
            off = -22 if lit_right else 22
            dr.ellipse((370 + off, 168, 430 + off, 232), fill=(8, 8, 12))
        return save(name, im, night)
    r = s.read(moon_pic("wax.jpg", True))
    check("honest: crescent lit on the right: a moon, waxing (north)", (r["light"], r["disc"]["lit_side"], any("waxing" in c["says"] for c in r["clues"])), ("night (moon)", "right", True))
    r = s.read(moon_pic("wane.jpg", False))
    check("honest: crescent lit on the left: waning (north)", (r["disc"]["lit_side"], any("waning" in c["says"] for c in r["clues"])), ("left", True))
    r = s.read(moon_pic("full.jpg", True, full=True))
    check("honest: full moon in a dark sky", (r["light"], r["disc"]["fill"] > 0.8), ("night (moon)", True))

    # trick: a lamp shade in a dark room, lit room exposure: not a moon
    im = Image.new("RGB", (800, 600), (30, 25, 20)); dr = ImageDraw.Draw(im)
    dr.rectangle((0, 0, 800, 300), fill=(20, 18, 15)); dr.ellipse((370, 170, 430, 230), fill=(250, 240, 200))
    dr.ellipse((330, 130, 470, 270), outline=(140, 120, 90), width=20)
    r = s.read(save("lamp.jpg", im, room))
    check("trick: a lamp in a dark room is not the moon", r["disc"], None)

    # a midday sun: blue, even sky, a white disc, bright exposure
    im = Image.new("RGB", (800, 600), (90, 150, 225)); dr = ImageDraw.Draw(im)
    dr.ellipse((600, 40, 640, 80), fill=(255, 255, 255)); dr.rectangle((0, 420, 800, 600), fill=(60, 110, 40))
    day = {0x8769: {0x829A: 1 / 2000, 0x829D: 8.0, 0x8827: 100}}                                     # EV about 17
    r = s.read(save("noon.jpg", im, day))
    check("honest: sun in a blue sky, bright exposure: sun high", r["light"], "sun high")
    check("...the sun's disc found", any("sun's disc" in c["clue"] for c in r["clues"]), True)
    # nothing to go on: a mid-grey picture, no EXIF
    check("can't tell from a plain grey picture", s.read(save("grey.jpg", np.full((300, 300, 3), 128.0)))["light"], "can't tell")
    check("trick: a broken file does not crash", "error" in s.read(save("x.jpg", np.zeros((4, 4, 3))) and (open(os.path.join(d, "bad.jpg"), "wb").write(b"no") and os.path.join(d, "bad.jpg"))), True)

    # the referee's clock
    gp = save("gps.jpg", np.zeros((8, 8, 3)), {0x8769: {0x9003: "2017:07:01 14:00:00"}, 0x8825: {7: (13.0, 0.0, 0.0), 29: "2017:07:01"}})
    t, how = s.referee_time(gp, {"tz": "Europe/London"})
    check("referee time: the GPS's own time first", (t.strftime("%H:%M"), how), ("13:00", "GPS time"))
    bst = save("bst.jpg", np.zeros((8, 8, 3)), {0x8769: {0x9003: "2017:07:01 14:00:00"}})
    check("referee time: camera clock in summer time (BST) -> 13:00 UTC", s.referee_time(bst, {"tz": "Europe/London"})[0].strftime("%H:%M"), "13:00")
    gmt = save("gmt.jpg", np.zeros((8, 8, 3)), {0x8769: {0x9003: "2017:01:01 14:00:00"}})
    check("referee time: camera clock in winter (GMT) -> 14:00 UTC", s.referee_time(gmt, {"tz": "Europe/London"})[0].strftime("%H:%M"), "14:00")
    off = save("off.jpg", np.zeros((8, 8, 3)), {0x8769: {0x9003: "2020:01:01 14:00:00", 0x9011: "+09:00"}})
    check("referee time: the camera's own offset", s.referee_time(off, {"tz": "Europe/London"})[0].strftime("%H:%M"), "05:00")
    check("referee time: no clock, no time", s.referee_time(save("none.jpg", np.zeros((8, 8, 3))), {"tz": "Europe/London"}), (None, None))

    # overcast: a bright grey sky over dark land
    im = Image.new("RGB", (800, 600), (205, 207, 210)); ImageDraw.Draw(im).rectangle((0, 380, 800, 600), fill=(50, 80, 40))
    check("honest: a bright grey sky reads as day (overcast)", any(c["clue"] == "bright, grey sky" for c in s.read(save("grey_sky.jpg", im))["clues"]), True)
    # star trails: arcs round one pole in a dark sky at a night exposure; the same picture with a lit-room exposure is not
    im = Image.new("RGB", (800, 600), (10, 10, 25)); dr = ImageDraw.Draw(im)
    for _ in range(60):
        r_ = rng.uniform(120, 700); a0 = rng.uniform(0, 360)
        dr.arc((300 - r_, -100 - r_, 300 + r_, -100 + r_), a0, a0 + 6, fill=(220, 220, 255), width=1)
    check("honest: star trails at a night exposure read as night", s.read(save("trails.jpg", im, night))["light"], "night")
    check("trick: the same streaks with a lit-room exposure are not star trails", s.find_trails(s._load(save("trails_room.jpg", im, room)), 6.2), 0)
    # the clock against the sun everywhere people live
    check("clock check: midsummer noon is day everywhere", s.sun_everywhere("2020-06-21T12:00:00"), "up")
    check("clock check: new year 00:30 is dark everywhere", s.sun_everywhere("2020-01-01T00:30:00"), "down")
    check("honest: 30 May 15:52 depends on the place (far south: near sunset)", s.sun_everywhere("2009-05-30T15:52:42"), None)
    noon_night = {0x8769: {0x829A: 15.0, 0x829D: 1.8, 0x8827: 1600, 0x9003: "2020:06:21 12:00:00"}}
    r = s.read(save("stars_noon.jpg", a, noon_night))
    check("trick: a starry sky stamped midsummer noon: the clock is called wrong", (r["light"], bool(r["clock_check"])), ("night", True))

    tr = {"light": "night"}
    check("mark: night read at night is right", s.mark({"light": "night"}, tr), "right")
    check("mark: sun high read at night is wrong", s.mark({"light": "sun high"}, tr), "wrong")
    check("mark: can't tell is not counted right", s.mark({"light": "can't tell"}, tr), "said can't tell")
finally:
    shutil.rmtree(d)

print(f"{n - len(fails)}/{n} pass")
sys.exit(1 if fails else 0)
