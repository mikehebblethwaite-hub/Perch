# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_sky: day and time from natural instant clues, read the way a person reads them: the light, the sky, the sun, the moon,
# the stars. Step 1 is the file's own data (the camera's clock, its exposure); step 2 is Perch's reading of the picture.
# GPS is never used for the reading (Mike, 16 Nov 2026: "I do not rely on gps and bots later also should not"); it is only the
# REFEREE: the GPS place and time give the true sun and moon (our own maths below), which mark Perch's reading.
#   python3 perch_sky.py read PICTURE...                 Perch's reading (+ the file's own clock and light)
#   python3 perch_sky.py truth UTC LAT LON               the sun and moon at a moment and place (ISO time, Z)
#   python3 perch_sky.py score FOLDER [OUT.jsonl]        read every photo; mark each against its GPS referee where it has one
# Every clue says how sure it is (1-11, never 0) and what it rests on. A clue the picture cannot give is said as unknown.
# Not yet: shadow direction (needs the scene's ground plane), star patterns (north, latitude, time: needs a star list).
import datetime as dt, json, math, os, sys
import perch_murmur as mm                    # Perch's own number grids (C), not numpy
from PIL import Image, ImageOps

R = math.radians
D = math.degrees


# ---- the sky's clockwork (the referee's side): our own maths, from Meeus / NOAA, good to a fraction of a degree ----

def _jd(t):
    return t.timestamp() / 86400.0 + 2440587.5


def _obliquity(T):
    om = 125.04 - 1934.136 * T
    return 23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60 + 0.00256 * math.cos(R(om))


def _sun_ecliptic(T):
    L0 = (280.46646 + T * (36000.76983 + T * 0.0003032)) % 360
    M = 357.52911 + T * (35999.05029 - 0.0001537 * T)
    C = (math.sin(R(M)) * (1.914602 - T * (0.004817 + 0.000014 * T)) + math.sin(R(2 * M)) * (0.019993 - 0.000101 * T)
         + math.sin(R(3 * M)) * 0.000289)
    om = 125.04 - 1934.136 * T
    return (L0 + C - 0.00569 - 0.00478 * math.sin(R(om))) % 360


def _to_horizon(ra, dec, jd, lat, lon):
    T = (jd - 2451545.0) / 36525
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * T * T - T ** 3 / 38710000) % 360
    H = R((gmst + lon - ra) % 360)
    la, de = R(lat), R(dec)
    el = D(math.asin(max(-1, min(1, math.sin(la) * math.sin(de) + math.cos(la) * math.cos(de) * math.cos(H)))))
    az = (D(math.atan2(math.sin(H), math.cos(H) * math.sin(la) - math.tan(de) * math.cos(la))) + 180) % 360
    return az, el


def _ecl_to_eq(lon, lat, eps):
    lo, la, e = R(lon), R(lat), R(eps)
    ra = D(math.atan2(math.sin(lo) * math.cos(e) - math.tan(la) * math.sin(e), math.cos(lo))) % 360
    dec = D(math.asin(math.sin(la) * math.cos(e) + math.cos(la) * math.sin(e) * math.sin(lo)))
    return ra, dec


def sun(t, lat, lon):
    """The sun's compass bearing and height (degrees) at UTC time t, seen from lat/lon."""
    jd = _jd(t); T = (jd - 2451545.0) / 36525
    ra, dec = _ecl_to_eq(_sun_ecliptic(T), 0.0, _obliquity(T))
    az, el = _to_horizon(ra, dec, jd, lat, lon)
    return {"azimuth": round(az, 2), "elevation": round(el, 2)}


def moon(t, lat, lon_obs):
    """The moon's bearing, height (from the ground, parallax taken off), how much of it is lit, waxing or waning, its age."""
    jd = _jd(t); T = (jd - 2451545.0) / 36525
    Dm = R(297.8501921 + 445267.1114034 * T); M = R(357.5291092 + 35999.0502909 * T)
    Mp = R(134.9633964 + 477198.8675055 * T); F = R(93.2720950 + 483202.0175233 * T)
    Lp = 218.3164477 + 481267.88123421 * T
    s = math.sin
    mlon = Lp + (6.288774 * s(Mp) + 1.274027 * s(2 * Dm - Mp) + 0.658314 * s(2 * Dm) + 0.213618 * s(2 * Mp) - 0.185116 * s(M)
                 - 0.114332 * s(2 * F) + 0.058793 * s(2 * Dm - 2 * Mp) + 0.057066 * s(2 * Dm - M - Mp) + 0.053322 * s(2 * Dm + Mp)
                 + 0.045758 * s(2 * Dm - M) - 0.040923 * s(M - Mp) - 0.034720 * s(Dm) - 0.030383 * s(M + Mp))
    mlat = (5.128122 * s(F) + 0.280602 * s(Mp + F) + 0.277693 * s(Mp - F) + 0.173237 * s(2 * Dm - F)
            + 0.055413 * s(2 * Dm - F + Mp) + 0.046271 * s(2 * Dm - F - Mp))
    dist = (385000.56 - 20905.355 * math.cos(Mp) - 3699.111 * math.cos(2 * Dm - Mp) - 2955.968 * math.cos(2 * Dm)
            - 569.925 * math.cos(2 * Mp))
    eps = _obliquity(T)
    ra, dec = _ecl_to_eq(mlon % 360, mlat, eps)
    az, el = _to_horizon(ra, dec, jd, lat, lon_obs)
    el -= D(math.asin(6378.14 / dist * math.cos(R(el))))          # seen from the ground, not the earth's centre
    elong = (mlon - _sun_ecliptic(T)) % 360
    psi = D(math.acos(math.cos(R(mlat)) * math.cos(R(elong))))
    lit = (1 + math.cos(R(180 - psi))) / 2
    return {"azimuth": round(az, 2), "elevation": round(el, 2), "lit": round(lit, 3),
            "waxing": elong < 180, "age_days": round(elong / 360 * 29.530589, 1)}


def light_of(sun_el):
    """The sun's height as a person says it."""
    if sun_el > 20: return "sun high"
    if sun_el > 0: return "sun low"
    if sun_el > -12: return "twilight"
    return "night"


def truth(t, lat, lon):
    s, m = sun(t, lat, lon), moon(t, lat, lon)
    return {"utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "sun": s, "moon": m, "light": light_of(s["elevation"]),
            "moon_up": m["elevation"] > 0}


# ---- step 1: the file's own data (the camera's clock and its light meter) ----

def file_data(path):
    """The camera's clock (local, as written) and, from its exposure, how bright the scene was (EV at ISO 100)."""
    out = {}
    try:
        with Image.open(path) as im:
            ex = im.getexif()
    except Exception:
        return out
    x = ex.get_ifd(0x8769)
    t = x.get(0x9003) or ex.get(0x0132)
    if isinstance(t, str) and len(t) >= 19 and t[:4].isdigit() and t[:4] != "0000":
        out["camera_clock"] = t[:19].replace(":", "-", 2).replace(" ", "T")
        off = x.get(0x9011)
        if isinstance(off, str) and len(off) == 6: out["camera_offset"] = off
    try:
        et, fn, iso = float(x[0x829A]), float(x[0x829D]), float(x[0x8827] if not isinstance(x[0x8827], tuple) else x[0x8827][0])
        if et > 0 and fn > 0 and iso > 0:
            out["ev100"] = round(math.log2(fn * fn / et) - math.log2(iso / 100), 1)
            out["exposure"] = {"seconds": round(et, 5), "f": round(fn, 1), "iso": int(iso)}
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return out


# ---- step 2: Perch's reading of the picture itself ----

def _load(path, size=800):
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size))
        return mm.asarray(im, dtype=mm.float32)


def _blobs(mask):
    """Joined bright patches (4-neighbour), as lists of (y, x), found in reading order. murmur's C does the walk (no cap on size:
    the old Python walk stopped at 20,000 pixels, a made-up limit)."""
    return mm.patches(mask)


def sky_band(a):
    """The top of the picture, where the sky is when there is one: brightness, colour, how even it is."""
    top = a[: max(4, a.shape[0] // 4)]
    lum = top @ mm.array([0.2126, 0.7152, 0.0722], dtype=mm.float32)
    r, g, b = (float(top[..., i].mean()) for i in range(3))
    gy = mm.abs(mm.diff(lum, axis=0)).mean() + mm.abs(mm.diff(lum, axis=1)).mean()
    out = {"brightness": round(float(lum.mean()), 1), "blue": round((b - r) / 255, 3), "warm": round((r - b) / 255, 3),
           "even": round(float(gy), 2)}
    # the sky's own pixels: the upper half's bright, smooth pixels (stones and trees in the band are left out)
    half = a[: max(4, a.shape[0] // 2)]
    hl = half @ mm.array([0.2126, 0.7152, 0.0722], dtype=mm.float32)
    smooth = mm.zeros_like(hl, dtype=bool)
    smooth[1:-1, 1:-1] = (mm.abs(hl[2:, 1:-1] - hl[:-2, 1:-1]) + mm.abs(hl[1:-1, 2:] - hl[1:-1, :-2])) < 12
    sky = smooth & (hl > 130)
    out["sky_share"] = round(float(sky.mean()), 3)
    if sky.sum() > 50:
        px = half[sky]
        out["sky_brightness"] = round(float(hl[sky].mean()), 1)
        out["sky_blue"] = round(float((px[:, 2] - px[:, 0]).mean()) / 255, 3)
        out["sky_grey"] = round(float(mm.abs(px - px.mean(1, keepdims=True)).mean()) / 255, 3)
    return out


def find_discs(a, night, ev=None):
    """A sun or moon disc: a bright, round, solid patch standing clear of a darker sky. Returns the best, or None."""
    lum = a @ mm.array([0.2126, 0.7152, 0.0722], dtype=mm.float32)
    h, w = lum.shape
    thr = 245 if not night else max(120.0, float(mm.percentile(lum, 99.5)))
    best = None
    for pts in _blobs(lum >= thr):
        n = len(pts)
        if n < 12 or n > h * w * 0.05: continue
        ys = mm.array([p[0] for p in pts]); xs = mm.array([p[1] for p in pts])
        span = max(ys.max() - ys.min(), xs.max() - xs.min()) + 1
        fill = n / (math.pi * (span / 2) ** 2)                  # a full disc fills its circle; a crescent less
        if fill < 0.15 or fill > 1.3: continue
        r = span / 2                                            # the whole moon's circle: a crescent's tips span its diameter
        cy = (ys.max() + ys.min()) / 2 if ys.max() - ys.min() + 1 >= span else ys.mean()
        cx = (xs.max() + xs.min()) / 2
        if xs.max() - xs.min() + 1 < span * 0.9:                # narrower than tall: lit on one side; the circle sits on its outer edge
            inside = lambda c: float(mm.mean((xs - c) ** 2 + (ys - cy) ** 2 <= (r * 1.05) ** 2))
            right, left = xs.max() - r, xs.min() + r
            cx = right if inside(right) >= inside(left) else left
        fill = n / (math.pi * r * r)
        yy, xx = mm.ogrid[:h, :w]
        ring = ((yy - cy) ** 2 + (xx - cx) ** 2 >= (r * 1.6) ** 2) & ((yy - cy) ** 2 + (xx - cx) ** 2 <= (r * 2.6) ** 2)
        around = float(lum[ring].mean()) if ring.any() else 255.0
        if around > thr * 0.75: continue                         # a white wall or a lamp in a bright room, not a sky disc
        if night and (around > 40 or (ev is not None and ev > 6)): continue   # a moon hangs in a dark sky, outdoors at night
        d = {"x": round(float(cx / w), 3), "y": round(float(cy / h), 3), "size": round(float(span / max(h, w)), 4),
             "fill": round(min(fill, 1.0), 2), "around": round(around, 1), "pixels": n}
        offx = float(xs.mean() - cx) / r                        # which side of the circle the light is on
        d["lit_side"] = "right" if offx > 0.12 else "left" if offx < -0.12 else "even"
        if night and not (d["fill"] > 0.8 or d["lit_side"] != "even"): continue   # neither near full nor lit on one side
        if best is None or n > best["pixels"]: best = d
    return best


def find_stars(a, ev=None):
    """Stars: small sharp points in a dark, even sky. Noise at a high ISO is single pixels, so a star must be a small group
    brighter than the dark around it, and there must be several."""
    lum = a @ mm.array([0.2126, 0.7152, 0.0722], dtype=mm.float32)
    top = lum[: lum.shape[0] * 2 // 3]
    bg = float(mm.median(top))
    if bg > 30: return {"count": 0, "why": "sky not dark"}
    if ev is not None and ev > 4: return {"count": 0, "why": f"the camera's light meter says a lit scene (EV {ev})"}
    if float(mm.percentile(top, 75)) > 45: return {"count": 0, "why": "dark but not an even sky"}
    pts = []
    for p in _blobs(top >= bg + 45):
        if 2 <= len(p) <= 40:
            ys = [q[0] for q in p]; xs = [q[1] for q in p]
            if max(ys) - min(ys) <= 7 and max(xs) - min(xs) <= 7:
                pts.append((round(sum(xs) / len(xs) / top.shape[1], 3), round(sum(ys) / len(ys) / top.shape[0], 3)))
    return {"count": len(pts), "dark_sky": round(bg, 1), "points": pts[:200]}


def find_trails(a, ev=None):
    """Thin streaks in a dark sky: stars drawn into lines by a long exposure. Counted only; where they circle (the pole, so north)
    needs the full-size picture: at thumbnail size their pieces agree on a centre no better than random lines do (16 Nov)."""
    from PIL import ImageFilter
    if ev is not None and ev > 4: return 0
    lum = a @ mm.array([0.2126, 0.7152, 0.0722], dtype=mm.float32)
    top = lum[: lum.shape[0] * 2 // 3]
    if float(mm.median(top)) > 30: return 0
    blur = mm.asarray(Image.fromarray(mm.clip(top, 0, 255).astype(mm.uint8, how="trunc")).filter(ImageFilter.BoxBlur(2)), dtype=mm.float32)
    n = 0
    for pts in _blobs((top - blur > 10) & (top > 30)):
        if len(pts) < 4: continue
        P = mm.array(pts, float); Q = P - P.mean(0)
        ev_, _ = mm.linalg.eigh(Q.T @ Q / len(P))
        if math.sqrt(ev_[1] / max(ev_[0], 0.05)) >= 2.5 and 4 * math.sqrt(ev_[1]) >= 5: n += 1
    return n


def find_horizon(a):
    """Where land meets sky: the row each column turns from sky (bright, not green) to land (green or dark), the median across
    columns (a stone or tree spoils a few columns, not the median). As a share of the height, or None."""
    h, w, _ = a.shape
    lum = a @ mm.array([0.2126, 0.7152, 0.0722], dtype=mm.float32)
    land = ((a[..., 1] > a[..., 2] + 4) & (a[..., 1] >= a[..., 0] - 4)) | (lum < 70)
    rows = []
    for x in range(0, w, max(1, w // 120)):
        col = land[:, x]
        for y in range(h // 8, h - 3):
            if col[y] and col[y + 1] and col[y + 2] and col[y + 3 if y + 3 < h else y]:
                rows.append(y); break
    if len(rows) < 10: return None
    return float(mm.median(rows)) / h


def find_shadows(a, horizon, sun_x=None):
    """Shadows on the ground: dark patches below the horizon, much darker than the grass around them, longer than wide. On flat
    ground they are parallel, so in the picture their lines meet at one point on the horizon: straight below the sun (or its
    opposite point). Returns their pieces and where their lines meet the horizon."""
    h, w, _ = a.shape
    hy = int(horizon * h)
    lum = a @ mm.array([0.2126, 0.7152, 0.0722], dtype=mm.float32)
    ground = lum[hy + 2:]
    if ground.size == 0: return None
    grass = float(mm.median(ground))
    g = a[hy + 2:]
    dark = (ground < grass * 0.55) & (g[..., 1] > g[..., 2] + 2) & (g[..., 1] >= g[..., 0])   # dark GREEN: grass in shade, not a grey stone
    pieces = []
    for pts in _blobs(dark):
        if len(pts) < max(12, h * w // 4000): continue
        P = mm.array(pts, float); P[:, 0] += hy + 2; c = P.mean(0); Q = P - c
        ev, evec = mm.linalg.eigh(Q.T @ Q / len(P))
        if ev[1] < 4 * max(ev[0], 0.05): continue           # not long: a dark stone base, a hole, a person
        d = evec[:, 1]                                       # (dy, dx) along the shadow
        if abs(d[0]) < 1e-3: continue                        # level: meets the horizon nowhere
        t = (hy - c[0]) / d[0]
        xh = c[1] + t * d[1]                                 # where its line meets the horizon
        far = P[mm.argmin(P[:, 0])]; near = P[mm.argmax(P[:, 0])]
        pieces.append({"x": round(c[1] / w, 3), "y": round(c[0] / h, 3), "pixels": len(pts), "meets_horizon_x": round(xh / w, 3),
                       "length": round(4 * math.sqrt(ev[1]) / h, 3), "towards_camera": bool(near[0] - far[0] > 2)})
    if not pieces: return {"pieces": [], "grass": round(grass, 1)}
    wts = mm.array([p["pixels"] for p in pieces], float)
    xs = mm.array([p["meets_horizon_x"] for p in pieces])
    meet = float(mm.average(xs, weights=wts))
    spread = float(mm.sqrt(mm.average((xs - meet) ** 2, weights=wts)))
    return {"pieces": pieces, "meet_x": round(meet, 3), "spread": round(spread, 3), "grass": round(grass, 1)}


def shadow_reading(path, hfov=(60, 110)):
    """The shadow layer: is the sun ahead or behind, do the shadows belong to the sun in the picture, and how high the sun is.
    The height needs the lens's field of view (unknown in a cropped picture; exact in a 360 original), so it is a range."""
    a = _load(path)
    h, w, _ = a.shape
    hz = find_horizon(a)
    if hz is None: return {"error": "no horizon found"}
    lum = a @ mm.array([0.2126, 0.7152, 0.0722], dtype=mm.float32)
    disc = find_discs(a, False)
    sun_xy = (disc["x"], disc["y"]) if disc and disc["y"] < hz else None
    sh = find_shadows(a, hz)
    out = {"horizon": round(hz, 3), "sun_in_picture": sun_xy, "shadows": sh}
    if not sh or not sh["pieces"]:
        out["said"] = "no shadows on the ground: overcast, or the sun is out of sight"
        return out
    toward = sum(p["pixels"] for p in sh["pieces"] if p["towards_camera"]) > sum(p["pixels"] for p in sh["pieces"]) / 2
    out["sun_side"] = "ahead (shadows come towards the camera)" if toward else "behind (shadows run away from the camera)"
    lines = [f"shadows {'come towards' if toward else 'run away from'} the camera, so the sun is {'ahead' if toward else 'behind'}"]
    if sun_xy:
        if sh["spread"] > 0.1 or len(sh["pieces"]) < 2:      # the lines must agree among themselves before they can confirm anything
            out["shadows_meet_below_sun"] = None
            lines.append(f"their lines do not agree on one meeting point (spread {sh['spread']:.2f} of the width): "
                         "too ragged at this size to check against the sun")
        else:
            miss = abs(sh["meet_x"] - sun_xy[0])
            out["shadows_meet_below_sun"] = miss < 0.08
            lines.append(f"their lines meet the horizon at {sh['meet_x']:.2f} across, the sun is at {sun_xy[0]:.2f}: "
                         + ("they are this sun's shadows" if out["shadows_meet_below_sun"] else "they do not fit this sun"))
        up = (hz - sun_xy[1]) * h
        rng = []
        for fov in hfov:
            f = (w / 2) / math.tan(math.radians(fov / 2))
            rng.append(math.degrees(math.atan(up / f)))
        out["sun_height_deg"] = [round(min(rng), 1), round(max(rng), 1)]
        lines.append(f"the sun stands {out['sun_height_deg'][0]}-{out['sun_height_deg'][1]} deg above the horizon "
                     f"(a level camera with a {hfov[0]}-{hfov[1]} deg lens; the lens is not known)")
        off = (sun_xy[0] - 0.5) * hfov[0], (sun_xy[0] - 0.5) * hfov[1]
        out["sun_off_centre_deg"] = [round(min(off), 1), round(max(off), 1)]
    elif sh["spread"] > 0.1 or len(sh["pieces"]) < 2:
        lines.append(f"their lines do not agree on one meeting point (spread {sh['spread']:.2f} of the width): no bearing from them")
    else:
        lines.append(f"their lines meet the horizon at {sh['meet_x']:.2f} across: the sun's bearing (or its opposite) is there")
    out["said"] = "; ".join(lines)
    return out


def times_for_height(date, lat, lon, lo, hi, tz=None):
    """When on this date the sun at this place stands between lo and hi degrees: morning and afternoon windows (UTC or local)."""
    t = dt.datetime(date.year, date.month, date.day, tzinfo=dt.timezone.utc) - dt.timedelta(hours=2)
    win, cur = [], None
    for i in range(0, 28 * 60, 2):
        u = t + dt.timedelta(minutes=i)
        e = sun(u, lat, lon)
        inside = lo <= e["elevation"] <= hi
        if inside and cur is None: cur = [u, u, e["azimuth"], e["azimuth"]]
        elif inside: cur[1], cur[3] = u, e["azimuth"]
        elif cur is not None: win.append(cur); cur = None
    if cur: win.append(cur)
    if tz:
        from zoneinfo import ZoneInfo
        z = ZoneInfo(tz); win = [[a.astimezone(z), b.astimezone(z), c, d] for a, b, c, d in win]
    return [{"from": a.strftime("%H:%M"), "to": b.strftime("%H:%M"), "sun_bearing": f"{c:.0f}-{d:.0f}"} for a, b, c, d in win]


def sun_everywhere(clock):
    """Is the sun up (or down) everywhere people live at this local clock time and date? Our own sun maths over 55 S to 60 N, with
    the local clock allowed to be up to 1.5 hours off the sun's own time (time zones, summer time). "up", "down" or None (it depends)."""
    try:
        local = dt.datetime.fromisoformat(clock[:19])
    except ValueError:
        return None
    els = []
    for lat in range(-55, 61, 5):
        for off in (-1.5, -0.75, 0.0, 0.75, 1.5):
            t = (local - dt.timedelta(hours=off)).replace(tzinfo=dt.timezone.utc)   # at longitude 0 the sun's time is UTC
            els.append(sun(t, lat, 0.0)["elevation"])
    if min(els) > 0: return "up"
    if max(els) < -6: return "down"
    return None


def read(path):
    """Perch's own reading of day and time from the picture (and the file's own clock and light meter)."""
    f = file_data(path)
    try:
        a = _load(path)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "file": f}
    sky = sky_band(a)
    dark = sky["brightness"] < 50
    disc = find_discs(a, dark, f.get("ev100"))
    stars = find_stars(a, f.get("ev100"))
    clues, light, sure = [], None, 0

    def clue(what, says, s):
        clues.append({"clue": what, "says": says, "sure": s})

    ev = f.get("ev100")
    if ev is not None:
        if ev >= 13: clue(f"bright scene (EV {ev})", "sun high", 7)
        elif ev >= 10: clue(f"daylight-bright scene (EV {ev})", "day", 5)
        elif ev <= 4: clue(f"dark scene (EV {ev})", "night or indoors", 4)
    if stars["count"] >= 5:
        clue(f"{stars['count']} stars in a dark sky", "night", 9)
    if disc and not dark and disc["fill"] > 0.6 and disc["around"] > 120:
        clue("the sun's disc in the sky", "day", 8)
    elif disc and not dark and disc["around"] > 170 and disc["y"] < 0.5:
        clue("the sun's glare in a bright sky", "day", 7)            # glare is not a clean disc, but a blown-out patch in a bright sky
    if sky.get("sky_share", 0) > 0.15 and sky.get("sky_brightness", 0) > 165 and sky.get("sky_grey", 1) < 0.04 and abs(sky.get("sky_blue", 0)) < 0.06:
        clue("bright, grey sky", "day", 5)                           # overcast daylight
    elif sky.get("sky_share", 0) > 0.15 and sky.get("sky_blue", 0) > 0.12:
        clue("blue sky", "day", 6)
    trails = find_trails(a, f.get("ev100"))
    if trails >= 15:
        clue(f"{trails} streaks of light in a dark sky (star trails: a long night exposure)", "night", 6)
    if disc and dark:
        clue(f"a disc in a dark sky (lit {disc['fill']:.0%}, light on the {disc['lit_side']})", "night (moon)", 7)
        if disc["lit_side"] != "even":
            clue("moon lit on the " + disc["lit_side"], ("waxing" if disc["lit_side"] == "right" else "waning") +
                 " in the north half of the world (the other way round in the south)", 5)
    if sky["even"] < 6 and not dark:
        # "warm, even sky -> sun low" was dropped: on Mike's 705 GPS-refereed photos it was right 0 times, wrong 8 (warm indoor light
        # and white-balance tints, not a low sun). Low sun needs a better clue (shadow length, the sun's height in the picture).
        if sky["blue"] > 0.08 and sky["brightness"] > 140: clue("blue, even sky", "day", 6)
    if dark and sky["even"] < 4 and (ev is None or ev < 6): clue("dark, even sky", "night or twilight", 5)

    order = {"sun high": 0, "day": 1, "sun low": 2, "night or twilight": 3, "night (moon)": 4, "night": 5, "night or indoors": 6}
    votes = {}
    for c in clues:
        k = c["says"].split(" in the north")[0]
        if k.startswith(("waxing", "waning")): continue
        votes[k] = votes.get(k, 0) + c["sure"]
    if votes:
        light, sure = max(votes.items(), key=lambda kv: (kv[1], -order.get(kv[0], 9)))
        if light == "day":                                   # "day" agrees with "sun high" / "sun low": say the specific one
            exact = [k for k in ("sun high", "sun low") if k in votes]
            if exact:
                light = max(exact, key=lambda k: votes[k]); sure = votes["day"] + votes[light]
        sure = min(sure, max(c["sure"] for c in clues) + 1)  # agreeing clues add, but never past the best one by more than 1
    clock = None
    if light and f.get("camera_clock"):
        everywhere = sun_everywhere(f["camera_clock"])
        if light.startswith("night") and everywhere == "up":
            clock = (f"the camera's clock says {f['camera_clock'][:16].replace('T', ' ')}, when the sun is up everywhere people live: "
                     "it does not fit a night sky, so the clock is probably wrong")
        elif light in ("sun high", "day", "sun low") and everywhere == "down":
            clock = (f"the camera's clock says {f['camera_clock'][:16].replace('T', ' ')}, when it is dark everywhere people live: "
                     "it does not fit a daylit sky, so the clock is probably wrong")
    return {"light": light or "can't tell", "sure": max(1, min(11, sure)) if light else 1, "clues": clues, "clock_check": clock,
            "sky": sky, "disc": disc, "stars": {k: v for k, v in stars.items() if k != "points"}, "file": f,
            "said": said(light, clues, f) + (f"; {clock}" if clock else "")}


def said(light, clues, f):
    if not light: return "can't tell the time of day from this picture" + (f" (the camera's clock says {f['camera_clock']})" if f.get("camera_clock") else "")
    why = "; ".join(c["clue"] for c in clues[:3])
    line = f"I think {light} ({why})"
    if f.get("camera_clock"): line += f"; the camera's clock says {f['camera_clock'].replace('T', ' ')}"
    return line


# ---- the referee: the GPS place and the time give the true sky; GPS is never part of the reading ----

def referee_time(path, where):
    """The true moment (UTC): the GPS's own time if it wrote one, else the camera's clock read in the place's time zone."""
    try:
        with Image.open(path) as im:
            ex = im.getexif()
        g = ex.get_ifd(0x8825)
        if 7 in g and 29 in g:
            h, m, s = (float(v) for v in g[7])
            d = dt.datetime.strptime(str(g[29]).strip(), "%Y:%m:%d")
            return d.replace(tzinfo=dt.timezone.utc) + dt.timedelta(hours=h, minutes=m, seconds=s), "GPS time"
    except Exception:
        pass
    f = file_data(path)
    if not f.get("camera_clock"): return None, None
    local = dt.datetime.fromisoformat(f["camera_clock"])
    if f.get("camera_offset"):
        return local.replace(tzinfo=dt.datetime.strptime(f["camera_offset"], "%z").tzinfo).astimezone(dt.timezone.utc), "camera clock + offset"
    if where and where.get("tz"):
        from zoneinfo import ZoneInfo
        return local.replace(tzinfo=ZoneInfo(where["tz"])).astimezone(dt.timezone.utc), "camera clock in " + where["tz"]
    return None, None


MATCH = {"sun high": {"sun high"}, "day": {"sun high", "sun low"}, "sun low": {"sun low"},
         "night or twilight": {"twilight", "night"}, "night (moon)": {"night", "twilight"}, "night": {"night"},
         "night or indoors": {"night", "twilight", "sun high", "sun low"}}


def mark(reading, true):
    """Perch's reading against the true sky: right, wrong, or nothing said."""
    if reading["light"] == "can't tell": return "said can't tell"
    if reading["light"] == "night or indoors": return "hedged"
    return "right" if true["light"] in MATCH.get(reading["light"], set()) else "wrong"


def score(folder, out=None):
    import perch_geo
    rows, tally = [], {}
    for n in sorted(os.listdir(folder)):
        p = os.path.join(folder, n)
        if not n.lower().endswith((".jpg", ".jpeg", ".png", ".webp")): continue
        r = read(p)
        row = {"name": n, "reading": {k: r.get(k) for k in ("light", "sure", "said", "clues", "file")}}
        ref = perch_geo.referee_of(p)
        if ref:
            t, how = referee_time(p, ref)
            if t:
                tr = truth(t, ref["lat"], ref["lon"])
                tr["time_from"] = how
                row["referee"] = tr
                row["mark"] = mark(r, tr)
                k = (r["light"], tr["light"])
                tally[k] = tally.get(k, 0) + 1
        rows.append(row)
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows, tally


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["read"]:
        for p in a[1:]:
            r = read(p); print(p, "->", r["said"], f"(sure {r['sure']})")
    elif a[:1] == ["truth"] and len(a) == 4:
        t = dt.datetime.fromisoformat(a[1].replace("Z", "+00:00"))
        print(json.dumps(truth(t, float(a[2]), float(a[3])), indent=1))
    elif a[:1] == ["score"] and len(a) >= 2:
        rows, tally = score(a[1], a[2] if len(a) > 2 else None)
        marked = [r for r in rows if "mark" in r]
        from collections import Counter
        print(f"{len(rows)} read, {len(marked)} with a referee:", dict(Counter(r["mark"] for r in marked)))
        for (said_, true_), c in sorted(tally.items(), key=lambda kv: -kv[1]): print(f"  Perch {said_!r:22} truth {true_!r:10} {c}")
    else:
        sys.exit("perch_sky.py read PICTURE... | truth UTC LAT LON | score FOLDER [OUT.jsonl]")
