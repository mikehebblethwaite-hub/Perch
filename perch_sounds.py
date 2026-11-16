# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_sounds: fetch open sound recordings of birds, insects and frogs, by place, for Perch's ear to learn the background sound of
# a place (Mike, 16 Nov 2026: Australian day and night sound against Texas day and night sound is "an instant known"; "insect night
# noise is also important"). From GBIF's copy of iNaturalist (no account, no key), ONLY recordings their makers released as CC0
# (free) or CC BY (free, with credit): Perch is sold, so non-commercial (CC BY-NC) recordings are left out. Every file's credit
# (who recorded it, its licence, its record) is kept in index.jsonl beside it; CC BY asks for that credit wherever it is used.
# Day or night: the recording's own time and place through Perch's sun maths (perch_sky): the REFEREE's label, never the ear's input.
#   python3 perch_sounds.py OUTDIR [PLACE ...] [--groups birds,insects,frogs]   PLACE: GB, AU, US/Texas (default all three)
# Resumable (files already there are kept); OUTDIR/status.json says how far it is, live.
# The host's own rules, kept (16 Nov 2026: a first run pulled 7.1 GB in 48 minutes and iNaturalist refused our address; their
# recommended practices warn that over 5 GB of media an hour or 24 GB a day may bring a permanent block):
#   * a byte budget under theirs: at most 4 GB in any hour and 20 GB in any day (kept in OUTDIR/budget.jsonl across runs);
#   * every run first sends ONE request; refused (403/429) = it stops there, says so, exit 3;
#   * the first refusal during a run stops the whole run at once (no retrying into a block);
#   * every failure is written with its reason in OUTDIR/failures.jsonl.
# Four downloads at a time: a courtesy to the hosts.
import datetime as dt, json, os, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

GBIF = "https://api.gbif.org/v1/occurrence/search"
INATURALIST = "50c9509d-22c7-4a22-a47d-8c48425ef4a7"     # GBIF dataset: iNaturalist research-grade observations
TAXA = {"birds": 212, "insects": 216, "frogs": 131}      # GBIF keys: Aves, Insecta, Amphibia
OPEN = ("CC0_1_0", "CC_BY_4_0")
EXT = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/vnd.wave": "wav", "audio/wav": "wav",
       "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/flac": "flac"}
UA = {"User-Agent": "perch-sounds/0.01"}
HOUR_BYTES, DAY_BYTES = 4_000_000_000, 20_000_000_000   # under the host's 5 GB an hour / 24 GB a day
REFUSED = (403, 429)


class Refused(Exception):
    """The host said no (403 Forbidden / 429 Too Many Requests): the run stops."""


class Budget:
    """Bytes fetched in the last hour and the last day, kept on disk so a new run counts what the last one took."""
    def __init__(self, path):
        self.path, self.lock, self.log = path, threading.Lock(), []
        if os.path.exists(path):
            with open(path) as f:
                for l in f:
                    try: t, b = json.loads(l); self.log.append((t, b))
                    except ValueError: pass

    def _used(self, now, span): return sum(b for t, b in self.log if now - t < span)

    def wait(self, stop):
        """Before a download: wait while the last hour or day is full (or until the run is stopped)."""
        while not stop.is_set():
            with self.lock:
                now = time.time()
                self.log = [(t, b) for t, b in self.log if now - t < 86400]
                if self._used(now, 3600) < HOUR_BYTES and self._used(now, 86400) < DAY_BYTES: return
            stop.wait(60)

    def took(self, n):
        with self.lock:
            t = time.time(); self.log.append((t, n))
            with open(self.path, "a") as f: f.write(json.dumps([t, n]) + "\n")


def check(url):
    """ONE request to the host: True if it answers, False if it refuses (403/429). Other trouble (no network) raises."""
    try:
        req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read(1024); return True
    except urllib.error.HTTPError as e:
        if e.code in REFUSED: return False
        raise


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
    have, probe = set(), None
    if os.path.exists(idx_path):
        with open(idx_path, encoding="utf-8") as f:
            for l in f:
                if l.strip():
                    row = json.loads(l); have.add(row["file"]); probe = probe or row.get("source")
    status = {"started": time.time(), "jobs": {}, "now": None, "budget": {"hour": HOUR_BYTES, "day": DAY_BYTES}}
    stop = threading.Event()
    budget = Budget(os.path.join(out, "budget.jsonl"))

    def save_status():
        with open(os.path.join(out, "status.json.new"), "w") as f: json.dump(status, f)
        os.replace(os.path.join(out, "status.json.new"), os.path.join(out, "status.json"))

    def failed(rel, url, why):
        with open(os.path.join(out, "failures.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "file": rel, "url": url, "why": why}) + "\n")

    if probe:                                            # one request first: still refused = stop here, having asked once
        try:
            ok = check(probe)
        except Exception as e:
            status["stopped"] = f"could not reach the host: {type(e).__name__}: {e}"; save_status(); raise
        if not ok:
            status["stopped"] = "refused (403/429) on the first, single request: still blocked; nothing else asked"
            status["ended"] = time.time(); save_status()
            raise Refused(status["stopped"])

    def one(job, r, m, n):
        ext = EXT.get(m.get("format", ""), "audio")
        rel = os.path.join(job, f"{r['gbifID']}-{n}.{ext}")
        path = os.path.join(out, rel)
        if rel in have and os.path.exists(path) and os.path.getsize(path) > 0: return "had", 0
        if stop.is_set(): return "skipped", 0
        budget.wait(stop)
        if stop.is_set(): return "skipped", 0
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(m["identifier"], headers=UA), timeout=120) as resp:
                    data = resp.read()
                break
            except urllib.error.HTTPError as e:
                if e.code in REFUSED:                    # the host said no: everything stops now
                    failed(rel, m["identifier"], f"HTTP {e.code}: refused, run stopped"); stop.set()
                    status["stopped"] = f"refused (HTTP {e.code}) at {time.strftime('%Y-%m-%d %H:%M:%S')}: run stopped"
                    return "failed", 0
                if attempt == 3: failed(rel, m["identifier"], f"HTTP {e.code}"); return "failed", 0
            except Exception as e:
                if attempt == 3: failed(rel, m["identifier"], f"{type(e).__name__}: {e}"); return "failed", 0
            time.sleep(2 * (attempt + 1))
        budget.took(len(data))
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
                if stop.is_set(): break
                job = f"{place.replace(' ', '_')}/{group}"
                st = status["jobs"][job] = {"got": 0, "had": 0, "failed": 0, "skipped": 0, "bytes": 0, "seen": 0}
                status["now"] = job; save_status()
                futs = []
                for r in records(country, state or None, taxon):
                    if stop.is_set(): break
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
    if stop.is_set(): raise Refused(status.get("stopped", "refused"))
    return status


if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit("perch_sounds.py OUTDIR [PLACE ...]   e.g. GB AU US/Texas")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    args = sys.argv[2:]
    groups = None
    if "--groups" in args:
        i = args.index("--groups"); groups = args[i + 1].split(","); args = args[:i] + args[i + 2:]
    try:
        s = fetch(sys.argv[1], args or ["GB", "AU", "US/Texas"], groups)
    except Refused as e:
        print("stopped:", e); sys.exit(3)                # 3 = the host refused: waiting for the block to lift, not a fault
    for job, st in s["jobs"].items(): print(job, st)
