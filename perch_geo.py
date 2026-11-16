# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_geo: where a photo or video was taken, read from the file itself (its GPS), named from a world place list kept on
# this machine. No guessing from the picture and nothing leaves the machine: the coordinates come from the camera, the names
# from GeoNames (CC BY 4.0, https://www.geonames.org), downloaded once.
#   python3 perch_geo.py build                    make the index from SRC (GeoNames dump files) into DIR
#     SRC needs allCountries.zip (or .txt), admin1CodesASCII.txt, admin2Codes.txt, countryInfo.txt, featureCodes_en.txt from
#     https://download.geonames.org/export/dump/
#   python3 perch_geo.py at LAT LON [FACING]      what is there
#   python3 perch_geo.py file PATH...             a photo's or video's own GPS, and what is there
#   python3 perch_geo.py fill OUTDIR FOLDER...    add "where" to OUTDIR/<name>.info.json files already written for FOLDER's files
# Data: PERCH_GEO (default ~/.cache/perch-geo): src/ holds the GeoNames files, the built index is places.tsv + cells.bin + kinds.json.
# Distances in miles (and metres for the camera's own error). The camera stands at the coordinates: what it pointed at may be
# far away (a zoom, a drone), so a landmark is "near where it was taken", never "in the picture". With a compass heading
# (iPhones write one) a landmark straight ahead is said as "ahead", still not as seen.
import bisect, json, math, mmap, os, re, struct, subprocess, sys, time

DIR = os.environ.get("PERCH_GEO", os.path.expanduser("~/.cache/perch-geo"))
SRC = os.path.join(DIR, "src")
CELL = 0.25                                             # index cell, degrees
REC = struct.Struct("<iqI")                             # cell key, byte offset, byte length
EARTH_MILES = 3958.8
NEAR_MILES = 10.0                                       # the nearest place of any size, this far at most
TOWN_MILES = 25.0                                       # the nearest town (5,000 people or a county/region seat)
LANDMARK_MILES = 1.0                                    # landmarks this close to where the camera stood are listed
SAY_MILES = 0.25                                        # ...and the nearest is said in the line only this close,
SIGHT_MILES = 0.5                                       # a big sight (castle, ancient site, tower...) this close, or one ahead
SIGHTS = {f"S.{c}" for c in "ANS CSTL MNMT HSTS RUIN PAL TOWR OBPT LTHSE PIER STDM AMTH PYR WALLA FT ARCH CTRA".split()}
BRITISH = {"railroad station": "railway station", "railroad yard": "railway yard", "railroad stop": "railway halt",
           "parking lot": "car park", "center": "centre", "theater": "theatre", "harbor": "harbour"}
TOWN_POP = 5000
SKIP_PLACES = {"PPLQ", "PPLH", "PPLW", "PPLCH"}         # abandoned, historical, destroyed: not where anyone stands now
LANDMARKS = {f"S.{c}" for c in "ANS CSTL MNMT HSTS RUIN MUS ZOO AMTH STDM TOWR OBPT PAL MSTY THTR LTHSE PIER OPRA CH BDG AIRP "
             "RSTN SQR GDN MKT UNIV WALLA PYR TMPL MSQE SHRN FT CAVE MAR CMTY RECG ATHF MALL CSNO ARCH OBS SPA HSP".split()} | \
            {"L.PRK", "L.RES", "T.BCH", "T.MT", "T.HLL", "T.PK", "T.CAPE", "T.ISL", "T.CLF", "T.GRGE",
             "H.LK", "H.BAY", "H.FLLS", "H.LGN", "H.RSV"}
POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def key(lat, lon):
    return int(math.floor((lat + 90) / CELL)) * 10000 + int(math.floor((lon + 180) / CELL)) % int(360 / CELL)


def build(src=SRC, out=DIR):
    """GeoNames allCountries.txt -> places.tsv (only places people stand at and landmarks), sorted by cell; cells.bin to find a cell."""
    kinds = {}
    with open(os.path.join(src, "featureCodes_en.txt"), encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) > 1: kinds[p[0]] = p[1]
    admin1, admin2, country = {}, {}, {}
    for name, d in (("admin1CodesASCII.txt", admin1), ("admin2Codes.txt", admin2)):
        with open(os.path.join(src, name), encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) > 1: d[p[0]] = p[1]
    with open(os.path.join(src, "countryInfo.txt"), encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                p = line.rstrip("\n").split("\t")
                if len(p) > 4: country[p[0]] = p[4]
    rows = []
    txt = os.path.join(src, "allCountries.txt")
    if os.path.exists(txt):
        f = open(txt, encoding="utf-8")
    else:                                               # straight from the download: no 1.6 GB unzipped copy kept
        import io, zipfile
        f = io.TextIOWrapper(zipfile.ZipFile(os.path.join(src, "allCountries.zip")).open("allCountries.txt"), encoding="utf-8")
    with f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 15: continue
            cls, code = p[6], p[7]
            fc = f"{cls}.{code}"
            if cls == "P" and code not in SKIP_PLACES: role = "P"
            elif fc in LANDMARKS: role = "L"
            else: continue
            try: lat, lon = float(p[4]), float(p[5])
            except ValueError: continue
            cc, a1, a2 = p[8], p[10], p[11]
            pop = int(p[14]) if p[14].isdigit() else 0
            town = role == "P" and (pop >= TOWN_POP or code in ("PPLC", "PPLA", "PPLA2"))
            rows.append((key(lat, lon), f"{lat:.5f}\t{lon:.5f}\t{role}{'T' if town else ''}\t{fc}\t{p[1]}\t{pop}\t"
                                        f"{admin2.get(f'{cc}.{a1}.{a2}', '')}\t{admin1.get(f'{cc}.{a1}', '')}\t{country.get(cc, cc)}\t{p[0]}\n"))
    rows.sort(key=lambda r: r[0])
    tmp = os.path.join(out, "places.tsv.new")
    idx = []
    with open(tmp, "wb") as f:
        cur, start = None, 0
        for k, line in rows:
            if k != cur:
                if cur is not None: idx.append((cur, start, f.tell() - start))
                cur, start = k, f.tell()
            f.write(line.encode("utf-8"))
        if cur is not None: idx.append((cur, start, f.tell() - start))
    with open(os.path.join(out, "cells.bin.new"), "wb") as f:
        for r in idx: f.write(REC.pack(*r))
    with open(os.path.join(out, "kinds.json.new"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in kinds.items() if k in LANDMARKS or k.startswith("P.")}, f, ensure_ascii=False)
    fetched = open(os.path.join(src, "FETCHED")).read().strip() if os.path.exists(os.path.join(src, "FETCHED")) else "?"
    with open(os.path.join(out, "about.json.new"), "w") as f:
        json.dump({"data": "GeoNames (CC BY 4.0, https://www.geonames.org)", "fetched": fetched, "places": len(rows),
                   "cells": len(idx), "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)
    for n in ("places.tsv", "cells.bin", "kinds.json", "about.json"):     # all four swap in together, readers never see half
        os.replace(os.path.join(out, n + ".new"), os.path.join(out, n))
    return len(rows), len(idx)


class Places:
    def __init__(self, d=DIR):
        self.f = open(os.path.join(d, "places.tsv"), "rb")
        self.m = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        raw = open(os.path.join(d, "cells.bin"), "rb").read()
        self.idx = [REC.unpack_from(raw, i) for i in range(0, len(raw), REC.size)]
        self.keys = [r[0] for r in self.idx]
        self.kinds = json.load(open(os.path.join(d, "kinds.json"), encoding="utf-8"))
        self.about = json.load(open(os.path.join(d, "about.json")))

    def cell(self, k):
        i = bisect.bisect_left(self.keys, k)
        if i == len(self.keys) or self.keys[i] != k: return
        _, off, n = self.idx[i]
        for line in self.m[off:off + n].decode("utf-8").splitlines():
            p = line.split("\t")
            yield {"lat": float(p[0]), "lon": float(p[1]), "role": p[2], "code": p[3], "name": p[4], "pop": int(p[5]),
                   "county": p[6], "region": p[7], "country": p[8], "geonameid": int(p[9])}

    def around(self, lat, lon, miles):
        dlat = miles / 69.0
        dlon = miles / max(69.17 * math.cos(math.radians(min(abs(lat), 89.0))), 0.5)
        lat0, lat1 = max(lat - dlat, -90), min(lat + dlat, 89.9999)
        seen = set()
        for a in range(int(math.floor((lat0 + 90) / CELL)), int(math.floor((lat1 + 90) / CELL)) + 1):
            for b in range(int(math.floor((lon - dlon + 180) / CELL)), int(math.floor((lon + dlon + 180) / CELL)) + 1):
                k = a * 10000 + b % int(360 / CELL)
                if k in seen: continue
                seen.add(k)
                yield from self.cell(k)


def miles_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_MILES * math.asin(min(1.0, math.sqrt(h)))


def bearing(lat1, lon1, lat2, lon2):
    """Compass degrees from point 1 to point 2."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return (math.degrees(math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) + 360) % 360


def point(deg):
    return POINTS[int((deg + 22.5) // 45) % 8]


def miles_said(m):
    return f"{m:.1f} miles" if m >= 0.1 else f"{round(m * 1760)} yards"


def valid(lat, lon):
    """A real fix: inside the world and not the 0,0 a camera writes when it had none."""
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 and not (abs(lat) < 1e-6 and abs(lon) < 1e-6)


_places = None


def places():
    global _places
    if _places is None: _places = Places()
    return _places


def lookup(lat, lon, facing=None, err_m=None, pl=None):
    """What is at a camera position. Returns the "where" for a file's info, or None for no real fix."""
    if not valid(lat, lon): return None
    pl = pl or places()
    near = town = None
    marks = []
    for r in pl.around(lat, lon, max(TOWN_MILES, LANDMARK_MILES)):
        d = miles_between(lat, lon, r["lat"], r["lon"])
        r["miles"] = d
        if r["role"].startswith("P"):
            if d <= NEAR_MILES and (near is None or d < near["miles"]): near = r
            if r["role"] == "PT" and d <= TOWN_MILES and (town is None or d < town["miles"]): town = r
        elif d <= LANDMARK_MILES:
            marks.append(r)
    marks.sort(key=lambda r: r["miles"])
    sure = 10 if err_m is None else (11 if err_m <= 10 else 10 if err_m <= 50 else 8 if err_m <= 200 else 6 if err_m <= 1000 else 3)
    out = {"from": "the file's own GPS", "lat": round(lat, 6), "lon": round(lon, 6), "sure": sure}
    if err_m is not None: out["gps_error_m"] = round(err_m, 1)
    if facing is not None:
        out["facing_deg"] = round(facing % 360, 1)
        out["facing"] = point(facing)

    def place(r):
        kind = pl.kinds.get(r["code"], r["code"])
        for us, gb in BRITISH.items(): kind = kind.replace(us, gb)
        o = {"name": r["name"], "kind": kind, "code": r["code"], "miles": round(r["miles"], 2),
             "camera_is": point(bearing(r["lat"], r["lon"], lat, lon)) + " of it" if r["miles"] >= 0.05 else "at it",
             "geonameid": r["geonameid"]}
        if r["pop"]: o["people"] = r["pop"]
        return o
    ref = near or town
    if ref:
        out["region"] = {"county": ref["county"] or None, "region": ref["region"] or None, "country": ref["country"]}
    if near: out["near"] = place(near)
    if town and town is not near: out["town"] = place(town)
    lm = []
    for r in marks[:12]:
        o = place(r)
        if facing is not None and r["miles"] >= 0.02:
            off = (bearing(lat, lon, r["lat"], r["lon"]) - facing + 540) % 360 - 180
            o["off_heading_deg"] = round(off)
            if abs(off) <= 30: o["ahead"] = True       # in front of the camera, roughly: not proof it is in the picture
        lm.append(o)
    if lm: out["landmarks"] = lm
    out["said"] = said(out)
    out["data"] = f"{pl.about['data']}, fetched {pl.about['fetched'][:10]}"
    return out


def said(w):
    """One plain line, British miles; the landmark is where it was taken, never claimed as what the picture shows."""
    bits = []
    lm = w.get("landmarks") or []
    sayable = lambda m: m["miles"] <= SAY_MILES or (m["miles"] <= SIGHT_MILES and m.get("code") in SIGHTS) or m.get("ahead")
    told = next((m for m in lm if m.get("code") in SIGHTS and sayable(m)), None) or next((m for m in lm if sayable(m)), None)
    if told:
        m = told
        bits.append(f"taken {'at' if m['camera_is'] == 'at it' else miles_said(m['miles']) + ' ' + m['camera_is'].replace(' of it', ' of')} {m['name']} ({m['kind']})")
    r = w.get("near") or w.get("town")
    if r and told and r["name"] == told["name"]:
        r = w.get("town") if w.get("town") and w["town"]["name"] != r["name"] else None
    if r:
        bits.append(("near " if bits else "taken near ") + r["name"] + (f" ({miles_said(r['miles'])})" if r["miles"] >= 0.5 else ""))
    reg = w.get("region") or {}
    tail = ", ".join(x for x in (reg.get("county"), reg.get("region"), reg.get("country")) if x and x != (r or {}).get("name"))
    line = ", ".join(bits) if bits else f"taken at {w['lat']}, {w['lon']} (no named place within {NEAR_MILES:.0f} miles)"
    if tail: line += ", " + tail
    if w.get("facing"): line += f"; camera facing {w['facing']}"
    if told and told.get("ahead"): line += f" (towards {told['name']})"
    return line


# ---- reading the file's own GPS ----

def _num(v):
    try:
        if isinstance(v, tuple) and len(v) == 2: return v[0] / v[1] if v[1] else None
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms(v, ref):
    try:
        if isinstance(v, (tuple, list)) and len(v) == 3:
            d, m, s = (_num(x) for x in v)
            if None in (d, m, s): return None
            x = d + m / 60 + s / 3600
        else:
            x = _num(v)
            if x is None: return None
    except (TypeError, ValueError):
        return None
    ref = ref.decode() if isinstance(ref, bytes) else (ref or "")
    return -x if ref.strip().upper() in ("S", "W") else x


def image_gps(path):
    """(lat, lon, facing, error metres, altitude metres) from a photo's EXIF, or None."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            g = im.getexif().get_ifd(0x8825)
    except Exception:
        return None
    if not g or 2 not in g or 4 not in g: return None
    lat, lon = _dms(g.get(2), g.get(1)), _dms(g.get(4), g.get(3))
    if not valid(lat, lon): return None
    facing = _num(g.get(17)) if 17 in g else None
    err = _num(g.get(31)) if 31 in g else None
    alt = _num(g.get(6)) if 6 in g else None
    if alt is not None and g.get(5) in (1, b"\x01"): alt = -alt
    return lat, lon, facing, err, alt


ISO6709 = re.compile(r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?")


def video_gps(path):
    """(lat, lon, None, None, altitude) from a video's location tag (ISO 6709, as phones write it), or None."""
    try:
        p = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_entries", "format_tags", path],
                           capture_output=True, text=True, timeout=60)
        tags = {k.lower(): v for k, v in json.loads(p.stdout or "{}").get("format", {}).get("tags", {}).items()}
    except Exception:
        return None
    for k in ("com.apple.quicktime.location.iso6709", "location", "location-eng"):
        m = ISO6709.match(tags.get(k, "").strip())
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if valid(lat, lon): return lat, lon, None, None, float(m.group(3)) if m.group(3) else None
    return None


VIDEO = (".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv")


def where_of(path, pl=None):
    """A file's "where" from its own GPS, or None (no GPS, or the 0,0 of no fix)."""
    g = video_gps(path) if path.lower().endswith(VIDEO) else image_gps(path)
    if not g: return None
    lat, lon, facing, err, alt = g
    w = lookup(lat, lon, facing, err, pl)
    if w and alt is not None: w["altitude_m"] = round(alt, 1)
    return w


def fill(outdir, folders):
    """Add "where" to info files already written (the describe job keeps going; each file is rewritten whole, then swapped in)."""
    added = none = 0
    for folder in folders:
        for n in sorted(os.listdir(folder)):
            dst = os.path.join(outdir, n + ".info.json")
            if not os.path.isfile(dst): continue
            w = where_of(os.path.join(folder, n))
            if not w:
                none += 1
                continue
            with open(dst, encoding="utf-8") as f:
                info = json.load(f)
            info["where"] = w
            tmp = dst + ".geo-new"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            os.chmod(tmp, os.stat(dst).st_mode & 0o777)
            os.replace(tmp, dst)
            added += 1
            print(n, "->", w["said"], flush=True)
    return added, none


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["build"]:
        t = time.time(); n, c = build()
        print(f"{n} places in {c} cells, {time.time() - t:.0f} s")
    elif a[:1] == ["at"] and len(a) >= 3:
        print(json.dumps(lookup(float(a[1]), float(a[2]), float(a[3]) if len(a) > 3 else None), ensure_ascii=False, indent=1))
    elif a[:1] == ["file"]:
        for p in a[1:]:
            w = where_of(p)
            print(p, "->", w["said"] if w else "no GPS in the file")
    elif a[:1] == ["fill"] and len(a) >= 3:
        print("added %d, no GPS %d" % fill(a[1], a[2:]))
    else:
        sys.exit(__doc__ or "perch_geo.py build | at LAT LON [FACING] | file PATH... | fill OUTDIR FOLDER...")
