# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_sounds: fetch open sound recordings of birds, insects and frogs, by place, for Perch's ear to learn the background sound of
# a place (Mike, 16 Nov 2026: Australian day and night sound against Texas day and night sound is "an instant known"; "insect night
# noise is also important"). From GBIF's copy of iNaturalist (no account, no key), ONLY recordings their makers released as CC0
# (free) or CC BY (free, with credit): Perch is sold, so non-commercial (CC BY-NC) recordings are left out. Every file's credit
# (who recorded it, its licence, its record) is kept in index.jsonl beside it; CC BY asks for that credit wherever it is used.
# Day or night: the recording's own time and place through Perch's sun maths (perch_sky): the REFEREE's label, never the ear's input.
#   python3 perch_sounds.py OUTDIR [PLACE ...] [--groups birds,insects,frogs]   PLACE: GB, AU, US/Texas (default all three)
# Resumable (files already there are kept); OUTDIR/status.json says how far it is, live. Four downloads at a time: a courtesy to
# the hosts, not a cap on the work.
import datetime as dt, json, os, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

GBIF = "https://api.gbif.org/v1/occurrence/search"
INATURALIST = "50c9509d-22c7-4a22-a47d-8c48425ef4a7"     # GBIF dataset: iNaturalist research-grade observations
TAXA = {"birds": 212, "insects": 216, "frogs": 131}      # GBIF keys: Aves, Insecta, Amphibia
OPEN = ("CC0_1_0", "CC_BY_4_0")
EXT = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/vnd.wave": "wav", "audio/wav": "wav",
       "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/flac": "flac"}
UA = {"User-Agent": "perch-sounds/0.01"}


def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)


def records(country, state=None, taxon=None):
    """Every open-licence sound record for a place and a group, page by page (GBIF serves up to 100,000 per search)."""
    q = {"datasetKey": INATURALIST, "country": country, "taxonKey": taxon, "mediaType": "Sound", "limit": 300}
    if state: q["stateProvince"] = state
    base = GBIF + "?" + urllib.parse.urlencode(q) + "".join(f"&license={l}" for l in OPEN)
    off = 0
    while off < 100000:
        d = _get(f"{base}&offset={off}")
        for r in d.get("results", []): yield r
        if d.get("endOfRecords") or not d.get("results"): break
        off += 300


def open_licence(url):
    """CC0 (public domain) or exactly CC BY: free to use in what Perch sells. Not BY-NC, not BY-ND, not BY-SA (share-alike would bind
    what is made from it), not 'all rights reserved'."""
    u = (url or "").lower()
    return "publicdomain/zero/" in u or "/licenses/by/" in u


def light_at(when, lat, lon):
    """The referee's day/night for a recording: its own time and place through the sun maths, or None without a clock time."""
    if not when or "T" not in when or lat is None or lon is None: return None
    try:
        t = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:                                  # local time with no zone: the zone of the place itself (the referee's own list)
        try:
            import perch_geo
            from zoneinfo import ZoneInfo
            tz = (perch_geo.lookup(lat, lon) or {}).get("tz")
            if not tz: return None
            t = t.replace(tzinfo=ZoneInfo(tz))
        except Exception:
            return None
    import perch_sky
    return perch_sky.light_of(perch_sky.sun(t.astimezone(dt.timezone.utc), lat, lon)["elevation"])


def fetch(out, places, groups=None):
    os.makedirs(out, exist_ok=True)
    idx_path = os.path.join(out, "index.jsonl")
    have = set()
    if os.path.exists(idx_path):
        with open(idx_path, encoding="utf-8") as f:
            have = {json.loads(l)["file"] for l in f if l.strip()}
    status = {"started": time.time(), "jobs": {}, "now": None}

    def save_status():
        with open(os.path.join(out, "status.json.new"), "w") as f: json.dump(status, f)
        os.replace(os.path.join(out, "status.json.new"), os.path.join(out, "status.json"))

    def one(job, r, m, n):
        ext = EXT.get(m.get("format", ""), "audio")
        rel = os.path.join(job, f"{r['gbifID']}-{n}.{ext}")
        path = os.path.join(out, rel)
        if rel in have and os.path.exists(path) and os.path.getsize(path) > 0: return "had", 0
        os.makedirs(os.path.dirname(path), exist_ok=True)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(m["identifier"], headers=UA), timeout=120) as resp:
                    data = resp.read()
                break
            except Exception:
                if attempt == 3: return "failed", 0
                time.sleep(2 * (attempt + 1))
        with open(path + ".part", "wb") as f: f.write(data)
        os.replace(path + ".part", path)
        lat, lon = r.get("decimalLatitude"), r.get("decimalLongitude")
        row = {"file": rel, "gbifID": r["gbifID"], "species": r.get("species"), "scientificName": r.get("scientificName"),
               "group": job.split("/")[-1], "country": r.get("countryCode"), "state": r.get("stateProvince"),
               "when": r.get("eventDate"), "lat": lat, "lon": lon, "light": light_at(r.get("eventDate"), lat, lon),
               "licence": m.get("license"), "by": m.get("rightsHolder") or r.get("recordedBy"), "source": m["identifier"],
               "record": f"https://www.gbif.org/occurrence/{r['gbifID']}", "bytes": len(data)}
        with open(idx_path, "a", encoding="utf-8") as f: f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return "got", len(data)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for place in places:
            country, _, state = place.partition("/")
            for group, taxon in TAXA.items():
                if groups and group not in groups: continue
                job = f"{place.replace(' ', '_')}/{group}"
                st = status["jobs"][job] = {"got": 0, "had": 0, "failed": 0, "bytes": 0, "seen": 0}
                status["now"] = job; save_status()
                futs = []
                for r in records(country, state or None, taxon):
                    # the search asked for open records; each sound's OWN licence is checked again: CC0 or exactly CC BY
                    sounds = [m for m in r.get("media", []) if m.get("type") == "Sound" and m.get("identifier") and open_licence(m.get("license"))]
                    for n, m in enumerate(sounds):
                        futs.append(pool.submit(one, job, r, m, n))
                    st["seen"] += 1
                    if len(futs) >= 64:
                        for f in futs:
                            how, b = f.result(); st[how] += 1; st["bytes"] += b
                        futs = []; save_status()
                for f in futs:
                    how, b = f.result(); st[how] += 1; st["bytes"] += b
                save_status()
    status["now"] = None; status["ended"] = time.time(); save_status()
    return status


if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit("perch_sounds.py OUTDIR [PLACE ...]   e.g. GB AU US/Texas")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    args = sys.argv[2:]
    groups = None
    if "--groups" in args:
        i = args.index("--groups"); groups = args[i + 1].split(","); args = args[:i] + args[i + 2:]
    s = fetch(sys.argv[1], args or ["GB", "AU", "US/Texas"], groups)
    for job, st in s["jobs"].items(): print(job, st)
