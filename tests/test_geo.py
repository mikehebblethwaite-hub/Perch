#!/usr/bin/env python3
# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
"""perch_geo: where a photo or video was taken, from the file's own GPS, named offline. Tricks: the 0,0 of no fix, numbers out
of the world, a zero-denominator EXIF, an abandoned village, a far landmark claimed as the place, a big GPS error all refused or
said as weaker. Honest: real photos with real EXIF (south and west signs), a video's ISO 6709 tag, a place across the 180th
meridian, a landmark ahead of the camera, filling info files without losing the look. With the real index built (~/.cache/perch-geo)
it also checks famous places."""
import json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import perch_geo as g
from PIL import Image

fails, n = [], 0
def check(name, got, want):
    global n; n += 1
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

ROWS = [  # id, name, lat, lon, class, code, country, admin1, admin2, population
    (1, "Stonehenge", 51.17889, -1.82622, "S", "ANS", "GB", "ENG", "Z8", 0),
    (2, "West Amesbury", 51.16700, -1.80600, "P", "PPL", "GB", "ENG", "Z8", 300),
    (3, "Amesbury", 51.17300, -1.77900, "P", "PPL", "GB", "ENG", "Z8", 10000),
    (4, "Lost Village", 51.17880, -1.83000, "P", "PPLQ", "GB", "ENG", "Z8", 0),
    (5, "Far Church", 51.19000, -1.84200, "S", "CH", "GB", "ENG", "Z8", 0),
    (6, "Fiji East", -16.50000, 179.99000, "P", "PPL", "FJ", "01", "", 2000),
    (7, "Hobart", -42.87936, 147.32941, "P", "PPLA", "AU", "06", "", 200000),
    (8, "Some Farm", 51.17000, -1.85000, "S", "FRM", "GB", "ENG", "Z8", 0),
]

d = tempfile.mkdtemp(prefix="perch-geo-")
try:
    src = os.path.join(d, "src"); os.makedirs(src)
    with open(os.path.join(src, "allCountries.txt"), "w") as f:
        for i, nm, la, lo, c, code, cc, a1, a2, pop in ROWS:
            f.write("\t".join(map(str, [i, nm, nm, "", la, lo, c, code, cc, "", a1, a2, "", "", pop, "", "", "", ""])) + "\n")
    open(os.path.join(src, "featureCodes_en.txt"), "w").write(
        "S.ANS\tarchaeological/prehistoric site\t\nP.PPL\tpopulated place\t\nP.PPLA\tseat of a first-order administrative division\t\n"
        "S.CH\tchurch\t\nS.FRM\tfarm\t\nP.PPLQ\tabandoned populated place\t\n")
    open(os.path.join(src, "admin1CodesASCII.txt"), "w").write("GB.ENG\tEngland\tEngland\t1\nAU.06\tTasmania\tTasmania\t2\n")
    open(os.path.join(src, "admin2Codes.txt"), "w").write("GB.ENG.Z8\tWiltshire\tWiltshire\t3\n")
    open(os.path.join(src, "countryInfo.txt"), "w").write("#c\nGB\tGBR\t826\tUK\tUnited Kingdom\nAU\tAUS\t36\tAS\tAustralia\nFJ\tFJI\t242\tFJ\tFiji\n")
    open(os.path.join(src, "FETCHED"), "w").write("2026-11-16T00:00:00Z\n")
    g.build(src, d)
    pl = g.Places(d)

    w = g.lookup(51.1780, -1.8320, None, None, pl)
    check("honest: Stonehenge said, 0.3 miles W", w["said"].startswith("taken 0.3 miles W of Stonehenge"), True)
    check("trick: an abandoned village is never the place", "Lost Village" in json.dumps(w), False)
    check("trick: a farm is not a landmark", "Some Farm" in json.dumps(w), False)
    check("near is the nearest live place", w["near"]["name"], "West Amesbury")
    check("town is the nearest town", w["town"]["name"], "Amesbury")
    check("county and country", (w["region"]["county"], w["region"]["country"]), ("Wiltshire", "United Kingdom"))
    check("no compass: sure 10", w["sure"], 10)
    check("a far church is listed but not said", ("Far Church" in [m["name"] for m in w["landmarks"]], "Far Church" in w["said"]), (True, False))

    w = g.lookup(51.1780, -1.8320, 60, None, pl)
    check("honest: facing NE towards Stonehenge", w["said"].endswith("camera facing NE (towards Stonehenge)"), True)
    w = g.lookup(51.1780, -1.8320, 240, None, pl)
    check("facing away: not said as towards it", "towards" in w["said"], False)
    check("trick: a big GPS error lowers sure", g.lookup(51.1780, -1.8320, None, 3000, pl)["sure"], 3)

    check("trick: 0,0 (no fix) refused", g.lookup(0, 0, None, None, pl), None)
    check("trick: out of the world refused", g.lookup(95, 10, None, None, pl), None)
    check("honest: across the 180th meridian", g.lookup(-16.5, -179.995, None, None, pl)["near"]["name"], "Fiji East")
    check("honest: south (Hobart)", g.lookup(-42.88, 147.33, None, None, pl)["near"]["name"], "Hobart")
    check("nowhere named: says the numbers", g.lookup(10.0, -40.0, None, None, pl)["said"].startswith("taken at 10.0, -40.0"), True)

    # real JPEGs with real EXIF GPS (degrees, minutes, seconds as rationals; S and W refs)
    def jpeg(path, gps):
        ex = Image.Exif(); ex[0x8825] = gps; Image.new("RGB", (8, 8)).save(path, exif=ex)
    pics = os.path.join(d, "pics"); out = os.path.join(d, "out"); os.makedirs(pics); os.makedirs(out)
    jpeg(os.path.join(pics, "stones.jpg"), {1: "N", 2: (51.0, 10.0, 40.8), 3: "W", 4: (1.0, 49.0, 55.2), 16: "T", 17: 60.0, 31: 5.0})
    jpeg(os.path.join(pics, "hobart.jpg"), {1: "S", 2: (42.0, 52.0, 48.0), 3: "E", 4: (147.0, 19.0, 48.0)})
    jpeg(os.path.join(pics, "nofix.jpg"), {1: "N", 2: (0.0, 0.0, 0.0), 3: "E", 4: (0.0, 0.0, 0.0)})
    Image.new("RGB", (8, 8)).save(os.path.join(pics, "plain.jpg"))
    open(os.path.join(pics, "broken.jpg"), "wb").write(b"not a picture")
    check("photo: N/W EXIF read", [round(x, 4) for x in g.image_gps(os.path.join(pics, "stones.jpg"))[:2]], [51.178, -1.832])
    check("photo: S/E EXIF read", round(g.image_gps(os.path.join(pics, "hobart.jpg"))[0], 2), -42.88)
    ws = g.referee_of(os.path.join(pics, "stones.jpg"), pl)
    check("photo: compass + 5 m error -> sure 11, towards", (ws["sure"], ws["facing"], "towards Stonehenge" in ws["said"]), (11, "NE", True))
    check("trick: photo with 0,0 has no referee", g.referee_of(os.path.join(pics, "nofix.jpg"), pl), None)
    check("photo without GPS has no referee", g.referee_of(os.path.join(pics, "plain.jpg"), pl), None)
    check("trick: a broken file does not crash", g.referee_of(os.path.join(pics, "broken.jpg"), pl), None)
    check("trick: zero-denominator EXIF number", g._dms(((51, 1), (0, 0), (1, 1)), "N"), None)

    # a real video with the ISO 6709 tag phones write (skipped when ffmpeg is not here)
    if shutil.which("ffmpeg"):
        v = os.path.join(pics, "clip.mp4")
        subprocess.run(["ffmpeg", "-v", "quiet", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.2", "-metadata",
                        "location=+51.1780-001.8320+080.0/", "-movflags", "use_metadata_tags", v], check=True)
        g._places = pl
        wv = g.referee_of(v)
        check("video: ISO 6709 tag read", (wv["lat"], wv["lon"], wv["altitude_m"]), (51.178, -1.832, 80.0))
        v0 = os.path.join(pics, "zero.mp4")
        subprocess.run(["ffmpeg", "-v", "quiet", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.2", "-metadata",
                        "location=+00.0000+000.0000/", "-movflags", "use_metadata_tags", v0], check=True)
        check("trick: video with 0,0 has no referee", g.referee_of(v0), None)

    # fill: adds where to info files already written, keeps the look, leaves the rest alone
    g._places = pl
    json.dump({"summary": "stones on a plain", "sure": 9}, open(os.path.join(out, "stones.jpg.info.json"), "w"))
    json.dump({"summary": "grey square", "sure": 9}, open(os.path.join(out, "plain.jpg.info.json"), "w"))
    added, none = g.fill(out, [pics])
    s = json.load(open(os.path.join(out, "stones.jpg.info.json")))
    check("fill: gps_referee added, look kept", ("gps_referee" in s, s["summary"]), (True, "stones on a plain"))
    check("fill: no GPS, file untouched", json.load(open(os.path.join(out, "plain.jpg.info.json"))), {"summary": "grey square", "sure": 9})
    check("fill: counts", (added, none), (1, 1))
    g._places = None
finally:
    shutil.rmtree(d)

if os.path.exists(os.path.join(g.DIR, "places.tsv")):
    for la, lo, name in [(51.1780, -1.8320, "Stonehenge"), (51.5081, -0.0759, "Tower of London"), (53.8159, -3.0545, "Blackpool Tower"),
                         (40.6892, -74.0445, "Statue of Liberty")]:
        check(f"real index: {name}", name in g.lookup(la, lo)["said"], True)

print(f"{n - len(fails)}/{n} pass")
sys.exit(1 if fails else 0)
